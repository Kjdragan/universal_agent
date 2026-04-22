"""Replay and post-process Claude Code intelligence packets.

This module lets us backfill or re-run downstream processing against an existing
packet without deleting seen-state or duplicating Task Hub work. It also writes
a durable candidate ledger and materializes a first-pass external wiki vault.
"""

from __future__ import annotations

import html
import json
import os
import shutil
import sqlite3
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from universal_agent import task_hub
from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.services.claude_code_intel import (
    KB_SLUG,
    LANE_SLUG,
    SOURCE_KIND_DEMO_TASK,
    SOURCE_KIND_KB_UPDATE,
    queue_follow_up_tasks,
    register_packet_artifact,
)
from universal_agent.wiki.core import ensure_vault, wiki_ingest_external_source


@dataclass(frozen=True)
class ClaudeCodeIntelReplayConfig:
    packet_dir: Path
    queue_task_hub: bool = True
    write_vault: bool = True
    expand_sources: bool = True
    artifacts_root: Path | None = None
    work_product_dir: Path | None = None


def resolve_lane_root(artifacts_root: Path | None = None) -> Path:
    return (artifacts_root or resolve_artifacts_dir()) / "proactive" / LANE_SLUG


def resolve_external_vault_root(artifacts_root: Path | None = None) -> Path:
    return (artifacts_root or resolve_artifacts_dir()) / "knowledge-vaults" / KB_SLUG


def replay_packet(
    *,
    config: ClaudeCodeIntelReplayConfig,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    packet_dir = config.packet_dir.expanduser().resolve()
    payload = load_packet(packet_dir)
    actions = list(payload["actions"])
    posts = list(payload["new_posts"])
    handle = str(payload["manifest"].get("handle") or "ClaudeDevs")
    queued_task_count = 0
    packet_artifact_id = ""

    if conn is not None and conn.row_factory is None:
        conn.row_factory = sqlite3.Row
    if conn is not None:
        task_hub.ensure_schema(conn)

    if conn is not None:
        packet_artifact_id = register_packet_artifact(
            conn,
            packet_dir=packet_dir,
            handle=handle,
            actions=actions,
            new_posts=posts,
        )
        if config.queue_task_hub:
            queued_task_count = queue_follow_up_tasks(
                conn,
                handle=handle,
                packet_dir=packet_dir,
                actions=actions,
            )

    linked_sources_path = write_linked_sources(packet_dir=packet_dir, actions=actions)
    linked_source_entries = expand_linked_sources(
        packet_dir=packet_dir,
        actions=actions,
        enabled=config.expand_sources,
    )
    implementation_opportunities_path = write_implementation_opportunities(packet_dir=packet_dir, actions=actions)
    vault_result = ingest_packet_into_external_vault(
        packet_dir=packet_dir,
        handle=handle,
        posts=posts,
        actions=actions,
        linked_source_entries=linked_source_entries,
        artifacts_root=config.artifacts_root,
        work_product_dir=config.work_product_dir,
        enabled=config.write_vault,
    )
    ledger_entries = build_candidate_ledger(
        packet_dir=packet_dir,
        handle=handle,
        actions=actions,
        conn=conn,
        packet_artifact_id=packet_artifact_id,
        vault_result=vault_result,
        artifacts_root=config.artifacts_root,
    )

    result = {
        "ok": True,
        "packet_dir": str(packet_dir),
        "handle": handle,
        "new_post_count": len(posts),
        "action_count": len(actions),
        "queued_task_count": queued_task_count,
        "packet_artifact_id": packet_artifact_id,
        "linked_sources_path": str(linked_sources_path),
        "linked_source_count": len(linked_source_entries),
        "linked_source_fetched_count": sum(1 for entry in linked_source_entries if str(entry.get("fetch_status") or "") == "fetched"),
        "implementation_opportunities_path": str(implementation_opportunities_path),
        "candidate_ledger_path": ledger_entries["packet_ledger_path"],
        "lane_ledger_path": ledger_entries["lane_ledger_path"],
        "vault_path": vault_result.get("vault_path") or "",
        "wiki_pages": vault_result.get("pages") or [],
        "email_evidence_ids": vault_result.get("email_evidence_ids") or [],
    }
    write_replay_summary(packet_dir=packet_dir, payload=result)
    return result


def load_packet(packet_dir: Path) -> dict[str, Any]:
    manifest_path = packet_dir / "manifest.json"
    raw_posts_path = packet_dir / "raw_posts.json"
    actions_path = packet_dir / "actions.json"
    new_posts_path = packet_dir / "new_posts.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Packet manifest not found: {manifest_path}")
    manifest = _load_json(manifest_path)
    actions = _load_json(actions_path) if actions_path.exists() else []
    raw_posts = _load_json(raw_posts_path) if raw_posts_path.exists() else {}
    new_posts = _load_json(new_posts_path) if new_posts_path.exists() else []
    if not isinstance(manifest, dict):
        raise RuntimeError(f"Invalid packet manifest: {manifest_path}")
    if not isinstance(actions, list):
        actions = []
    if not isinstance(new_posts, list):
        new_posts = []
    if not isinstance(raw_posts, dict):
        raw_posts = {}
    post_map = {
        str(post.get("id") or "").strip(): dict(post)
        for post in new_posts
        if isinstance(post, dict) and str(post.get("id") or "").strip()
    }
    return {
        "manifest": manifest,
        "actions": actions,
        "raw_posts": raw_posts,
        "new_posts": new_posts,
        "post_map": post_map,
    }


def write_linked_sources(*, packet_dir: Path, actions: list[dict[str, Any]]) -> Path:
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action in actions:
        for link in action.get("links") or []:
            clean = str(link or "").strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            entries.append(
                {
                    "url": clean,
                    "post_id": str(action.get("post_id") or "").strip(),
                    "tier": int(action.get("tier") or 0),
                    "action_type": str(action.get("action_type") or "").strip(),
                    "fetch_status": "pending",
                    "source_path": "",
                    "analysis_path": "",
                }
            )
    path = packet_dir / "linked_sources.json"
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
    return path


def expand_linked_sources(*, packet_dir: Path, actions: list[dict[str, Any]], enabled: bool) -> list[dict[str, Any]]:
    linked_sources_path = packet_dir / "linked_sources.json"
    entries = _load_json(linked_sources_path) if linked_sources_path.exists() else []
    if not isinstance(entries, list):
        entries = []
    if not enabled:
        return entries

    max_fetch = max(1, min(int(_safe_env_int("UA_CLAUDE_CODE_INTEL_LINK_MAX_FETCH", 10)), 50))
    fetch_timeout = max(5.0, min(float(_safe_env_float("UA_CLAUDE_CODE_INTEL_LINK_TIMEOUT_SECONDS", 20.0)), 120.0))
    linked_root = packet_dir / "linked_sources"
    linked_root.mkdir(parents=True, exist_ok=True)

    fetched = 0
    with httpx.Client(timeout=fetch_timeout, follow_redirects=True, headers={"User-Agent": "universal-agent-claude-code-intel/1.0"}) as client:
        for entry in entries:
            if fetched >= max_fetch:
                break
            url = str(entry.get("url") or "").strip()
            if not url:
                continue
            source_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
            entry["source_hash"] = source_hash
            source_dir = linked_root / source_hash
            source_dir.mkdir(parents=True, exist_ok=True)
            entry["metadata_path"] = str(source_dir / "metadata.json")
            entry["source_path"] = str(source_dir / "source.md")
            entry["analysis_path"] = str(source_dir / "analysis.md")
            if _should_skip_link_fetch(url):
                entry["fetch_status"] = "skipped"
                entry["skip_reason"] = "unsupported_or_opaque_source"
                _write_linked_source_files(source_dir=source_dir, entry=entry, content="", analysis=_linked_source_analysis(entry=entry, content="", metadata={}))
                continue
            if str(entry.get("fetch_status") or "") == "fetched" and Path(entry["source_path"]).exists():
                continue
            fetched += 1
            _fetch_linked_source(client=client, url=url, entry=entry, source_dir=source_dir)

    linked_sources_path.write_text(json.dumps(entries, indent=2, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
    return entries


def write_implementation_opportunities(*, packet_dir: Path, actions: list[dict[str, Any]]) -> Path:
    lines = ["# Implementation Opportunities", ""]
    opportunities = [action for action in actions if int(action.get("tier") or 0) >= 3]
    if not opportunities:
        lines.append("- No Tier 3/4 opportunities in this packet.")
    for action in opportunities:
        lines.extend(
            [
                f"## {action.get('action_type')} - {action.get('post_id')}",
                "",
                f"- Tier: `{action.get('tier')}`",
                f"- Post: {action.get('url') or action.get('post_id')}",
                f"- Links: {len(action.get('links') or [])}",
                f"- Summary: {str(action.get('text') or '').strip()}",
                "",
            ]
        )
    path = packet_dir / "implementation_opportunities.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def ingest_packet_into_external_vault(
    *,
    packet_dir: Path,
    handle: str,
    posts: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    linked_source_entries: list[dict[str, Any]],
    artifacts_root: Path | None,
    work_product_dir: Path | None,
    enabled: bool,
) -> dict[str, Any]:
    if not enabled:
        return {"vault_path": "", "pages": [], "email_evidence_ids": []}

    vault_root = resolve_external_vault_root(artifacts_root)
    context = ensure_vault("external", KB_SLUG, title="Claude Code Intelligence", root_override=str(vault_root))

    # Preserve immutable packet snapshots under raw/ for forensic replay.
    raw_dir = context.path / "raw" / "packets" / packet_dir.name
    raw_dir.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.json", "raw_posts.json", "new_posts.json", "actions.json", "source_links.md", "triage.md", "digest.md"):
        src = packet_dir / name
        if src.exists():
            shutil.copy2(src, raw_dir / name)

    pages: list[str] = []
    post_pages_by_post_id: dict[str, list[str]] = {}
    linked_pages_by_post_id: dict[str, list[str]] = {}
    work_product_pages: list[str] = []
    action_map = {str(a.get("post_id") or "").strip(): dict(a) for a in actions if str(a.get("post_id") or "").strip()}

    for post in posts:
        post_id = str(post.get("id") or "").strip()
        if not post_id:
            continue
        action = action_map.get(post_id, {})
        content = _post_source_markdown(handle=handle, post=post, action=action)
        result = wiki_ingest_external_source(
            vault_slug=KB_SLUG,
            source_title=f"ClaudeDevs post {post_id}",
            source_content=content,
            source_id=f"x_post_{post_id}",
            root_override=str(vault_root),
        )
        if isinstance(result, dict) and result.get("path"):
            page_path = str(result["path"])
            pages.append(page_path)
            post_pages_by_post_id.setdefault(post_id, []).append(page_path)

    if work_product_dir and work_product_dir.exists():
        for path in sorted(work_product_dir.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".md", ".html", ".txt"}:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            result = wiki_ingest_external_source(
                vault_slug=KB_SLUG,
                source_title=f"Claude Code work product: {path.stem}",
                source_content=content,
                source_id=f"work_product_{path.stem}",
                root_override=str(vault_root),
            )
            if isinstance(result, dict) and result.get("path"):
                page_path = str(result["path"])
                pages.append(page_path)
                work_product_pages.append(page_path)

    seen_linked_targets: set[str] = set()
    linked_result_by_target: dict[str, str] = {}
    for entry in linked_source_entries:
        if str(entry.get("fetch_status") or "") != "fetched":
            continue
        source_path = Path(str(entry.get("source_path") or ""))
        if not source_path.exists():
            continue
        metadata_path = Path(str(entry.get("metadata_path") or ""))
        metadata = _load_json(metadata_path) if metadata_path.exists() else {}
        canonical_target = str(metadata.get("final_url") or entry.get("url") or "").strip()
        if canonical_target and canonical_target in linked_result_by_target:
            linked_pages_by_post_id.setdefault(str(entry.get("post_id") or "").strip(), []).append(linked_result_by_target[canonical_target])
            continue
        if canonical_target:
            seen_linked_targets.add(canonical_target)
        content = source_path.read_text(encoding="utf-8", errors="replace")
        source_id = f"linked_source_{hashlib.sha256(canonical_target.encode('utf-8')).hexdigest()[:16]}" if canonical_target else f"linked_source_{entry.get('source_hash')}"
        result = wiki_ingest_external_source(
            vault_slug=KB_SLUG,
            source_title=f"Claude Code linked source: {metadata.get('title') or entry.get('title') or entry.get('url')}",
            source_content=content,
            source_id=source_id,
            root_override=str(vault_root),
        )
        if isinstance(result, dict) and result.get("path"):
            page_path = str(result["path"])
            pages.append(page_path)
            linked_pages_by_post_id.setdefault(str(entry.get("post_id") or "").strip(), []).append(page_path)
            if canonical_target:
                linked_result_by_target[canonical_target] = page_path

    email_evidence_ids = _collect_email_evidence_ids(work_product_dir)
    return {
        "vault_path": str(context.path),
        "pages": pages,
        "post_pages_by_post_id": post_pages_by_post_id,
        "linked_pages_by_post_id": linked_pages_by_post_id,
        "work_product_pages": work_product_pages,
        "email_evidence_ids": email_evidence_ids,
    }


def build_candidate_ledger(
    *,
    packet_dir: Path,
    handle: str,
    actions: list[dict[str, Any]],
    conn: sqlite3.Connection | None,
    packet_artifact_id: str,
    vault_result: dict[str, Any],
    artifacts_root: Path | None,
) -> dict[str, str]:
    entries = []
    lane_root = resolve_lane_root(artifacts_root)
    lane_ledger_dir = lane_root / "ledger"
    lane_ledger_dir.mkdir(parents=True, exist_ok=True)
    packet_wiki_pages = sorted(set(vault_result.get("pages") or []))
    post_pages_by_post_id = {
        str(k): sorted(set(v))
        for k, v in dict(vault_result.get("post_pages_by_post_id") or {}).items()
    }
    linked_pages_by_post_id = {
        str(k): sorted(set(v))
        for k, v in dict(vault_result.get("linked_pages_by_post_id") or {}).items()
    }
    work_product_pages = sorted(set(vault_result.get("work_product_pages") or []))
    packet_email_evidence_ids = sorted(set(vault_result.get("email_evidence_ids") or []))
    for action in actions:
        post_id = str(action.get("post_id") or "").strip()
        tier = int(action.get("tier") or 0)
        task_identity = intended_task_identity(post_id=post_id, tier=tier)
        task_state = lookup_task_state(conn, task_identity["task_id"]) if conn and task_identity["task_id"] else {}
        task_assignments = lookup_task_assignments(conn, task_identity["task_id"]) if conn and task_identity["task_id"] else []
        outbound_delivery = _task_outbound_delivery(task_state)
        assignment_workspaces = [str(item.get("workspace_dir") or "") for item in task_assignments if str(item.get("workspace_dir") or "")]
        workspace_email_evidence_ids = _collect_assignment_workspace_email_evidence_ids(task_assignments)
        candidate_artifact_id = lookup_candidate_artifact_id(
            conn,
            source_kind=task_identity["source_kind"],
            source_ref=post_id,
        ) if conn and task_identity["source_kind"] and post_id else ""
        email_evidence_ids = sorted(
            {
                *packet_email_evidence_ids,
                *workspace_email_evidence_ids,
                *[
                    str(outbound_delivery.get(key) or "").strip()
                    for key in ("message_id", "draft_id")
                    if str(outbound_delivery.get(key) or "").strip()
                ],
            }
        )
        entry = {
            "packet_dir": str(packet_dir),
            "post_id": post_id,
            "post_url": str(action.get("url") or ""),
            "tier": tier,
            "action_type": str(action.get("action_type") or ""),
            "packet_artifact_id": packet_artifact_id,
            "candidate_artifact_id": candidate_artifact_id,
            "intended_source_kind": task_identity["source_kind"],
            "intended_task_id": task_identity["task_id"],
            "task_row_present": bool(task_state),
            "task_status": str(task_state.get("status") or ""),
            "task_id": str(task_state.get("task_id") or task_identity["task_id"] or ""),
            "assignment_ids": [str(item.get("assignment_id") or "") for item in task_assignments if str(item.get("assignment_id") or "")],
            "assignment_states": [str(item.get("state") or "") for item in task_assignments if str(item.get("state") or "")],
            "assignment_result_summaries": [str(item.get("result_summary") or "") for item in task_assignments if str(item.get("result_summary") or "")],
            "assignment_workspaces": assignment_workspaces,
            "task_outbound_delivery": outbound_delivery,
            "post_source_pages": post_pages_by_post_id.get(post_id, []),
            "linked_source_pages": linked_pages_by_post_id.get(post_id, []),
            "work_product_pages": work_product_pages,
            "wiki_pages": sorted(set(post_pages_by_post_id.get(post_id, []) + linked_pages_by_post_id.get(post_id, []) + work_product_pages)) or packet_wiki_pages,
            "email_evidence_ids": email_evidence_ids,
            "links": list(action.get("links") or []),
            "handle": handle,
        }
        entries.append(entry)

    packet_path = packet_dir / "candidate_ledger.json"
    packet_path.write_text(json.dumps(entries, indent=2, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
    lane_path = lane_ledger_dir / f"{packet_dir.parent.name}__{packet_dir.name}.json"
    lane_path.write_text(json.dumps(entries, indent=2, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
    return {"packet_ledger_path": str(packet_path), "lane_ledger_path": str(lane_path)}


def write_replay_summary(*, packet_dir: Path, payload: dict[str, Any]) -> Path:
    path = packet_dir / "replay_summary.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
    return path


def intended_task_identity(*, post_id: str, tier: int) -> dict[str, str]:
    clean_post_id = str(post_id or "").strip()
    if tier < 3 or not clean_post_id:
        return {"source_kind": "", "task_id": ""}
    source_kind = SOURCE_KIND_DEMO_TASK if tier == 3 else SOURCE_KIND_KB_UPDATE
    task_id = f"{source_kind}:{hashlib.sha256(clean_post_id.encode()).hexdigest()[:16]}"
    return {"source_kind": source_kind, "task_id": task_id}


def lookup_task_state(conn: sqlite3.Connection | None, task_id: str) -> dict[str, Any]:
    if conn is None or not task_id:
        return {}
    row = conn.execute(
        "SELECT task_id, source_kind, source_ref, status, priority, created_at, updated_at, metadata_json FROM task_hub_items WHERE task_id = ? LIMIT 1",
        (task_id,),
    ).fetchone()
    if not row:
        return {}
    data = dict(row)
    try:
        data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
    except Exception:
        data["metadata"] = {}
    return data


def lookup_task_assignments(conn: sqlite3.Connection | None, task_id: str) -> list[dict[str, Any]]:
    if conn is None or not task_id:
        return []
    rows = conn.execute(
        """
        SELECT assignment_id, task_id, agent_id, workflow_run_id, workflow_attempt_id,
               provider_session_id, state, started_at, ended_at, result_summary, workspace_dir
        FROM task_hub_assignments
        WHERE task_id = ?
        ORDER BY started_at DESC
        """,
        (task_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def lookup_candidate_artifact_id(conn: sqlite3.Connection | None, *, source_kind: str, source_ref: str) -> str:
    if conn is None or not source_kind or not source_ref:
        return ""
    try:
        row = conn.execute(
            """
            SELECT artifact_id
            FROM proactive_artifacts
            WHERE source_kind = ? AND source_ref = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (source_kind, source_ref),
        ).fetchone()
    except Exception:
        return ""
    return str(row["artifact_id"] or "").strip() if row else ""


def _task_outbound_delivery(task_state: dict[str, Any]) -> dict[str, Any]:
    metadata = task_state.get("metadata") if isinstance(task_state.get("metadata"), dict) else {}
    dispatch = metadata.get("dispatch") if isinstance(metadata.get("dispatch"), dict) else {}
    outbound = dispatch.get("outbound_delivery") if isinstance(dispatch.get("outbound_delivery"), dict) else {}
    return {
        "channel": str(outbound.get("channel") or "").strip(),
        "message_id": str(outbound.get("message_id") or "").strip(),
        "draft_id": str(outbound.get("draft_id") or "").strip(),
        "sent_at": str(outbound.get("sent_at") or "").strip(),
    }


def _collect_assignment_workspace_email_evidence_ids(assignments: list[dict[str, Any]]) -> list[str]:
    ids: set[str] = set()
    for assignment in assignments:
        workspace_dir = Path(str(assignment.get("workspace_dir") or "")).expanduser()
        if not workspace_dir.exists():
            continue
        verification_dir = workspace_dir / "work_products" / "email_verification"
        if not verification_dir.exists():
            continue
        for path in verification_dir.glob("*.json"):
            ids.add(path.name)
    return sorted(ids)


def _post_source_markdown(*, handle: str, post: dict[str, Any], action: dict[str, Any]) -> str:
    post_id = str(post.get("id") or "").strip()
    lines = [
        f"# @{handle} Post {post_id}",
        "",
        f"- URL: https://x.com/{handle}/status/{post_id}",
        f"- Created at: {post.get('created_at') or ''}",
        f"- Tier: {action.get('tier') or 0}",
        f"- Action type: {action.get('action_type') or ''}",
        "",
        "## Text",
        "",
        str(post.get("text") or "").strip(),
        "",
        "## Links",
        "",
    ]
    links = list(action.get("links") or [])
    if not links:
        lines.append("- None")
    else:
        for link in links:
            lines.append(f"- {link}")
    lines.extend(
        [
            "",
            "## Triage",
            "",
            f"- Reasons: {', '.join(action.get('reasons') or [])}",
            f"- Matched terms: {', '.join(action.get('matched_terms') or []) or '(none)'}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _collect_email_evidence_ids(work_product_dir: Path | None) -> list[str]:
    if work_product_dir is None:
        return []
    verification_dir = work_product_dir / "email_verification"
    if not verification_dir.exists():
        return []
    ids: list[str] = []
    for path in sorted(verification_dir.glob("*.json")):
        ids.append(path.name)
    return ids


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _fetch_linked_source(*, client: httpx.Client, url: str, entry: dict[str, Any], source_dir: Path) -> None:
    metadata: dict[str, Any] = {
        "url": url,
        "post_id": entry.get("post_id") or "",
        "tier": int(entry.get("tier") or 0),
        "action_type": entry.get("action_type") or "",
    }
    content = ""
    analysis = ""
    try:
        resp = client.get(url)
        metadata["http_status"] = resp.status_code
        metadata["final_url"] = str(resp.url)
        metadata["content_type"] = resp.headers.get("content-type", "")
        metadata.update(_classify_linked_source(url=str(resp.url), content_type=metadata["content_type"]))
        if resp.status_code >= 400:
            entry["fetch_status"] = "error"
            entry["error"] = f"HTTP {resp.status_code}"
        else:
            body = resp.text
            content = _normalize_web_content(body=body, content_type=metadata["content_type"], url=str(resp.url))
            title = _extract_title(body) or _title_from_url(str(resp.url))
            entry["fetch_status"] = "fetched"
            entry["title"] = title
            metadata["title"] = title
            metadata["summary_excerpt"] = _summary_excerpt(content)
            analysis = _linked_source_analysis(entry=entry, content=content, metadata=metadata)
    except Exception as exc:
        entry["fetch_status"] = "error"
        entry["error"] = f"{type(exc).__name__}: {exc}"
    _write_linked_source_files(source_dir=source_dir, entry=entry, content=content, analysis=analysis, metadata=metadata)


def _write_linked_source_files(
    *,
    source_dir: Path,
    entry: dict[str, Any],
    content: str,
    analysis: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)
    metadata_payload = dict(metadata or {})
    metadata_payload.update(
        {
            "url": entry.get("url") or "",
            "fetch_status": entry.get("fetch_status") or "",
            "title": entry.get("title") or "",
            "error": entry.get("error") or "",
            "post_id": entry.get("post_id") or "",
            "tier": int(entry.get("tier") or 0),
            "action_type": entry.get("action_type") or "",
            "final_url": metadata_payload.get("final_url", ""),
            "content_type": metadata_payload.get("content_type", ""),
            "http_status": metadata_payload.get("http_status", 0),
        }
    )
    (source_dir / "metadata.json").write_text(json.dumps(metadata_payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
    if content:
        (source_dir / "source.md").write_text(content.rstrip() + "\n", encoding="utf-8")
    if analysis:
        (source_dir / "analysis.md").write_text(analysis.rstrip() + "\n", encoding="utf-8")


def _normalize_web_content(*, body: str, content_type: str, url: str) -> str:
    lowered = str(content_type or "").lower()
    if "application/json" in lowered:
        try:
            parsed = json.loads(body)
            return "# Linked Source\n\n```json\n" + json.dumps(parsed, indent=2, ensure_ascii=True, sort_keys=True) + "\n```\n"
        except Exception:
            pass
    if "text/plain" in lowered or url.lower().endswith(".md"):
        return body.strip() + "\n"
    text = body
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return f"# Linked Source\n\n{text}\n" if text else "# Linked Source\n\n[No readable text extracted]\n"


def _extract_title(body: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", body)
    if not match:
        return ""
    return html.unescape(match.group(1)).strip()


def _title_from_url(url: str) -> str:
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    tail = re.sub(r"[-_]+", " ", tail)
    tail = re.sub(r"\.[A-Za-z0-9]+$", "", tail)
    return tail.strip() or url


def _linked_source_analysis(*, entry: dict[str, Any], content: str, metadata: dict[str, Any] | None = None) -> str:
    metadata = dict(metadata or {})
    source_type = str(metadata.get("source_type") or "").strip()
    domain = str(metadata.get("domain") or "").strip()
    repo_slug = str(metadata.get("github_repo") or "").strip()
    guidance = _source_type_guidance(source_type=source_type, domain=domain, repo_slug=repo_slug)
    lines = [
        "# Linked Source Analysis",
        "",
        f"- URL: {entry.get('url') or ''}",
        f"- Final URL: {metadata.get('final_url') or ''}",
        f"- Source type: {source_type or 'generic_web'}",
        f"- Domain: {domain or ''}",
        f"- Post ID: {entry.get('post_id') or ''}",
        f"- Tier: {entry.get('tier') or 0}",
        f"- Action type: {entry.get('action_type') or ''}",
        f"- Fetch status: {entry.get('fetch_status') or ''}",
        "",
    ]
    if str(entry.get("fetch_status") or "") != "fetched":
        lines.append(f"- Fetch failed or was skipped: {entry.get('error') or entry.get('skip_reason') or 'unknown'}")
        return "\n".join(lines).rstrip() + "\n"
    snippet = "\n".join(line.strip() for line in content.splitlines()[:12] if line.strip())
    lines.extend(
        [
            "## First-Pass Assessment",
            "",
            "- This source was discovered directly from a ClaudeDevs post and preserved for later deeper review.",
            "- It should be used to refine wiki pages, migration notes, and implementation opportunities.",
            "",
            "## Source-Specific Guidance",
            "",
            *[f"- {line}" for line in guidance],
            "",
            "## Content Snippet",
            "",
            snippet or "(empty)",
            "",
            "## Next Questions",
            "",
            "- Does this source describe a concrete Claude Code capability or migration?",
            "- Does it include code, repo references, package/version details, or operational advice?",
            "- Should this source produce a demo task, a KB page only, or a strategic remediation task?",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _should_skip_link_fetch(url: str) -> bool:
    lowered = url.lower()
    if lowered.startswith("https://x.com/") or lowered.startswith("http://x.com/"):
        return True
    if lowered.startswith("https://twitter.com/") or lowered.startswith("http://twitter.com/"):
        return True
    return False


def _classify_linked_source(*, url: str, content_type: str) -> dict[str, Any]:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    path = parsed.path or ""
    source_type = "generic_web"
    github_owner = ""
    github_repo = ""
    if domain in {"github.com", "www.github.com"}:
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2:
            github_owner = parts[0]
            github_repo = f"{parts[0]}/{parts[1]}"
            if len(parts) == 2:
                source_type = "github_repo"
            elif len(parts) >= 4 and parts[2] in {"blob", "tree"}:
                source_type = "github_file" if parts[2] == "blob" else "github_tree"
            else:
                source_type = "github_page"
    elif "docs." in domain or domain.startswith("docs."):
        source_type = "docs_page"
    elif any(token in domain for token in ("platform.claude.com", "anthropic.com")):
        source_type = "vendor_docs" if "docs" in path or "docs" in domain else "vendor_web"
    elif any(token in domain for token in ("event", "conference", "summit", "hackathon", "cerebralvalley.ai")):
        source_type = "event_page"
    elif domain in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"}:
        source_type = "x_page"
    elif "text/html" not in str(content_type or "").lower() and str(content_type or "").lower():
        source_type = "non_html"
    return {
        "domain": domain,
        "source_type": source_type,
        "github_owner": github_owner,
        "github_repo": github_repo,
    }


def _source_type_guidance(*, source_type: str, domain: str, repo_slug: str) -> list[str]:
    if source_type == "github_repo":
        return [
            f"This appears to be a GitHub repository page{f' for {repo_slug}' if repo_slug else ''}.",
            "Look for installation instructions, README examples, API surface, and whether the repo is directly reusable in a Claude Code demo.",
            "Prefer extracting implementation primitives, commands, and file layout over generic marketing summary.",
        ]
    if source_type in {"github_file", "github_tree", "github_page"}:
        return [
            "This appears to be a GitHub code/file page.",
            "Focus on concrete code behavior, file purpose, and whether this should become a demo task, migration note, or reference page.",
            "Capture exact repo/path context in the vault page title and provenance.",
        ]
    if source_type in {"docs_page", "vendor_docs"}:
        return [
            f"This looks like a documentation page from {domain}.",
            "Extract capability changes, version/migration notes, command syntax, and any operational warnings that affect Universal Agent.",
            "Treat this as higher-trust reference material than the X post itself.",
        ]
    if source_type == "event_page":
        return [
            "This appears to be an event/community page.",
            "Likely lower direct implementation value unless it contains schedules, demos, prize structures, or links to deeper technical material.",
            "Avoid over-classifying community/event pages as code-demo work without stronger technical evidence.",
        ]
    if source_type == "x_page":
        return [
            "This is another X page or media wrapper.",
            "Treat it as low-fidelity evidence. Preserve the link but prefer any deeper canonical sources linked from it.",
        ]
    if source_type == "non_html":
        return [
            "This is a non-HTML source type.",
            "Preserve content type and a clean snapshot. If the payload is machine-readable, prefer structure over prose summary.",
        ]
    return [
        "Generic web source.",
        "Extract what changed, how it affects Claude Code or UA usage, and whether it should become a KB page, strategic note, or demo task.",
    ]


def _summary_excerpt(content: str, *, max_chars: int = 240) -> str:
    text = " ".join(line.strip() for line in content.splitlines() if line.strip())
    return text[:max_chars].strip()


def _safe_env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, default)))
    except Exception:
        return default


def _safe_env_float(name: str, default: float) -> float:
    try:
        return float(str(os.environ.get(name, default)))
    except Exception:
        return default

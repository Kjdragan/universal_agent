"""Replay and post-process Claude Code intelligence packets.

This module lets us backfill or re-run downstream processing against an existing
packet without deleting seen-state or duplicating Task Hub work. It also writes
a durable candidate ledger and materializes a first-pass external wiki vault.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    implementation_opportunities_path = write_implementation_opportunities(packet_dir=packet_dir, actions=actions)
    vault_result = ingest_packet_into_external_vault(
        packet_dir=packet_dir,
        handle=handle,
        posts=posts,
        actions=actions,
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
        wiki_pages=vault_result.get("pages") or [],
        email_evidence_ids=vault_result.get("email_evidence_ids") or [],
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
            pages.append(str(result["path"]))

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
                pages.append(str(result["path"]))

    email_evidence_ids = _collect_email_evidence_ids(work_product_dir)
    return {"vault_path": str(context.path), "pages": pages, "email_evidence_ids": email_evidence_ids}


def build_candidate_ledger(
    *,
    packet_dir: Path,
    handle: str,
    actions: list[dict[str, Any]],
    conn: sqlite3.Connection | None,
    packet_artifact_id: str,
    wiki_pages: list[str],
    email_evidence_ids: list[str],
    artifacts_root: Path | None,
) -> dict[str, str]:
    entries = []
    lane_root = resolve_lane_root(artifacts_root)
    lane_ledger_dir = lane_root / "ledger"
    lane_ledger_dir.mkdir(parents=True, exist_ok=True)
    packet_wiki_pages = sorted(set(wiki_pages))
    for action in actions:
        post_id = str(action.get("post_id") or "").strip()
        tier = int(action.get("tier") or 0)
        task_identity = intended_task_identity(post_id=post_id, tier=tier)
        task_state = lookup_task_state(conn, task_identity["task_id"]) if conn and task_identity["task_id"] else {}
        task_assignments = lookup_task_assignments(conn, task_identity["task_id"]) if conn and task_identity["task_id"] else []
        entry = {
            "packet_dir": str(packet_dir),
            "post_id": post_id,
            "post_url": str(action.get("url") or ""),
            "tier": tier,
            "action_type": str(action.get("action_type") or ""),
            "packet_artifact_id": packet_artifact_id,
            "intended_source_kind": task_identity["source_kind"],
            "intended_task_id": task_identity["task_id"],
            "task_row_present": bool(task_state),
            "task_status": str(task_state.get("status") or ""),
            "task_id": str(task_state.get("task_id") or task_identity["task_id"] or ""),
            "assignment_ids": [str(item.get("assignment_id") or "") for item in task_assignments if str(item.get("assignment_id") or "")],
            "assignment_states": [str(item.get("state") or "") for item in task_assignments if str(item.get("state") or "")],
            "assignment_result_summaries": [str(item.get("result_summary") or "") for item in task_assignments if str(item.get("result_summary") or "")],
            "wiki_pages": packet_wiki_pages,
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

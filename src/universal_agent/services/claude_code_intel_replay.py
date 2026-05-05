"""Replay and post-process Claude Code intelligence packets.

This module lets us backfill or re-run downstream processing against an existing
packet without deleting seen-state or duplicating Task Hub work. It also writes
a durable candidate ledger and materializes a first-pass external wiki vault.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
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
    classify_post,
    queue_follow_up_tasks,
    register_packet_artifact,
)
from universal_agent.wiki.core import (
    ACTION_CREATE,
    ACTION_EXTEND,
    ensure_vault,
    memex_apply_action,
    memex_page_exists,
    wiki_ingest_external_source,
)


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
    actions = refine_actions_with_linked_sources(
        packet_dir=packet_dir,
        actions=actions,
        linked_source_entries=linked_source_entries,
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


def refine_actions_with_linked_sources(
    *,
    packet_dir: Path,
    actions: list[dict[str, Any]],
    linked_source_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not actions:
        return actions
    if not linked_source_entries:
        return actions
    original_path = packet_dir / "actions_original.json"
    refined_path = packet_dir / "actions_refined.json"
    if not original_path.exists():
        original_path.write_text(json.dumps(actions, indent=2, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")

    summaries_by_post: dict[str, list[str]] = {}
    for entry in linked_source_entries:
        post_id = str(entry.get("post_id") or "").strip()
        if not post_id:
            continue
        metadata_path = Path(str(entry.get("metadata_path") or ""))
        metadata = _load_json(metadata_path) if metadata_path.exists() else {}
        summary_parts = [
            f"source_type={metadata.get('source_type') or ''}",
            f"domain={metadata.get('domain') or ''}",
            f"title={metadata.get('title') or entry.get('title') or ''}",
            f"summary={metadata.get('summary_excerpt') or ''}",
            f"github_repo={metadata.get('github_repo') or ''}",
        ]
        summaries_by_post.setdefault(post_id, []).append(" | ".join(part for part in summary_parts if part and not part.endswith("=")))

    refined_actions: list[dict[str, Any]] = []
    for action in actions:
        post_id = str(action.get("post_id") or "").strip()
        linked_context = "\n".join(summaries_by_post.get(post_id, []))
        post = {
            "id": post_id,
            "text": str(action.get("text") or ""),
            "created_at": str(action.get("created_at") or ""),
            "entities": {"urls": [{"expanded_url": link} for link in action.get("links") or []]},
        }
        refined = classify_post(post, handle=str(action.get("url") or "").split("/")[3] if str(action.get("url") or "").startswith("https://x.com/") else "ClaudeDevs", linked_context=linked_context)
        # Preserve existing fetched links and timestamps from the packet action.
        refined["links"] = list(action.get("links") or [])
        refined["created_at"] = str(action.get("created_at") or refined.get("created_at") or "")
        refined_actions.append(refined)

    (packet_dir / "actions.json").write_text(json.dumps(refined_actions, indent=2, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
    refined_path.write_text(json.dumps(refined_actions, indent=2, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
    return refined_actions


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
    with httpx.Client(timeout=fetch_timeout, follow_redirects=True, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }) as client:
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


# ── Memex extraction (PR 15) ────────────────────────────────────────────────
#
# After every replay, decide which entity/concept pages to CREATE or EXTEND
# from each action. CREATE is the dominant case (~80% per design doc §4.2);
# EXTEND fires when the page already exists. REVISE (~5%) requires LLM
# judgment about supersedes and is intentionally NOT wired here — it can
# layer on as a follow-up PR once we have data on how often it actually
# matters.
#
# Source-of-name precedence (deterministic, no LLM call):
#   1. release_info.package — when the action is a release_announcement
#      (PR 6a), the package name itself is an entity.
#   2. CamelCase / snake_case feature terms in action.text — same heuristic
#      research_grounding uses.
#   3. Skipped: anything from linked source titles. Future enhancement.

# Lifted from research_grounding._TERM_PATTERN so we extract the same
# named-feature shapes consistently across the v2 pipeline.
_MEMEX_TERM_PATTERN = re.compile(
    r"\b([A-Z][a-zA-Z0-9_]{2,}|[a-z][a-zA-Z0-9_]+_[a-zA-Z0-9_]+)\b"
)
_MEMEX_TERM_STOPWORDS = frozenset(
    {
        "claude",
        "anthropic",
        "agents",
        "agent",
        "tool",
        "tools",
        "this",
        "that",
        "with",
        "from",
        "into",
        "what",
        "when",
        "have",
        "been",
        "more",
        "https",
        "http",
        "url",
        "post",
        "github",
        "discord",
        "twitter",
        "reddit",
        "demo",
        "demos",
        "build",
        "release",
        "released",
        "today",
        "support",
        "supports",
    }
)


def _memex_candidates_for_action(action: dict[str, Any]) -> list[tuple[str, str]]:
    """Return [(kind, name)] candidates for one action.

    Conservative: returns at most a handful per action so the Memex pass
    doesn't explode into dozens of pages per tweet.
    """
    candidates: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(kind: str, name: str) -> None:
        cleaned = str(name or "").strip()
        if not cleaned:
            return
        key = (kind, cleaned.lower())
        if key in seen:
            return
        seen.add(key)
        candidates.append((kind, cleaned))

    # 1. Release announcements: the package itself is an entity.
    release_info = action.get("release_info") if isinstance(action.get("release_info"), dict) else None
    if release_info and release_info.get("package"):
        _add("entity", str(release_info["package"]))

    # 2. CamelCase / snake_case terms in the action's text. Cap to first 5.
    text = str(action.get("text") or "")
    if text:
        terms_found = 0
        for raw in _MEMEX_TERM_PATTERN.findall(text):
            if len(raw) < 3:
                continue
            if raw.lower() in _MEMEX_TERM_STOPWORDS:
                continue
            _add("entity", raw)
            terms_found += 1
            if terms_found >= 5:
                break

    return candidates


def _memex_body_for_create(
    *,
    handle: str,
    action: dict[str, Any],
    linked_for_post: list[dict[str, Any]],
) -> str:
    """Synthesize a minimal entity-page body from action + linked sources."""
    title = str(action.get("post_id") or "").strip()
    text = str(action.get("text") or "").strip()
    classifier = action.get("classifier") if isinstance(action.get("classifier"), dict) else {}
    reasoning = str(classifier.get("reasoning") or "").strip()
    post_url = str(action.get("url") or "").strip()
    parts: list[str] = ["## Discovery context", ""]
    parts.append(f"- handle: `@{handle}`")
    if post_url:
        parts.append(f"- post: {post_url}")
    if title:
        parts.append(f"- post_id: `{title}`")
    parts.append("")
    if text:
        parts.extend(["### Tweet text", "", text, ""])
    if reasoning:
        parts.extend(["### Classifier rationale", "", reasoning, ""])
    if linked_for_post:
        parts.extend(["### Linked official sources", ""])
        for entry in linked_for_post[:6]:
            url = str(entry.get("url") or "").strip()
            t = str(entry.get("title") or "").strip() or url or "source"
            if url:
                parts.append(f"- [{t}]({url})")
            else:
                parts.append(f"- {t}")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _memex_body_for_extend(
    *,
    action: dict[str, Any],
    linked_for_post: list[dict[str, Any]],
) -> str:
    """Body for an EXTEND — short, dated section appended to existing page."""
    text = str(action.get("text") or "").strip()
    parts: list[str] = []
    if text:
        parts.append(text)
    if linked_for_post:
        parts.append("")
        parts.append("Newly linked sources:")
        for entry in linked_for_post[:6]:
            url = str(entry.get("url") or "").strip()
            t = str(entry.get("title") or "").strip() or url or "source"
            if url:
                parts.append(f"- [{t}]({url})")
            else:
                parts.append(f"- {t}")
    return "\n".join(parts).rstrip() + "\n"


def apply_memex_pass(
    *,
    vault_path: Path,
    handle: str,
    actions: list[dict[str, Any]],
    linked_source_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Walk every action; CREATE or EXTEND entity/concept pages as needed.

    Returns a list of action records suitable for inclusion in the candidate
    ledger. Never raises on per-action failure — surfaces the error in the
    record so the downstream pipeline can keep working.
    """
    results: list[dict[str, Any]] = []

    # Pre-index linked sources by post_id so each action gets only its own.
    linked_by_post: dict[str, list[dict[str, Any]]] = {}
    for entry in linked_source_entries:
        if str(entry.get("fetch_status") or "") != "fetched":
            continue
        post_id = str(entry.get("post_id") or "").strip()
        if post_id:
            linked_by_post.setdefault(post_id, []).append(entry)

    for action in actions:
        post_id = str(action.get("post_id") or "").strip()
        candidates = _memex_candidates_for_action(action)
        if not candidates:
            continue
        linked_for_post = linked_by_post.get(post_id, [])
        source_id = f"x_post_{post_id}" if post_id else "x_post"
        source_title = f"ClaudeDevs post {post_id}" if post_id else "ClaudeDevs post"

        for kind, name in candidates:
            try:
                if memex_page_exists(vault_path, kind, name):
                    body = _memex_body_for_extend(
                        action=action,
                        linked_for_post=linked_for_post,
                    )
                    result = memex_apply_action(
                        vault_path,
                        action=ACTION_EXTEND,
                        kind=kind,
                        name=name,
                        body=body,
                        source_id=source_id,
                        source_title=source_title,
                        section_label=f"Update from @{handle}: post {post_id}",
                    )
                else:
                    body = _memex_body_for_create(
                        handle=handle,
                        action=action,
                        linked_for_post=linked_for_post,
                    )
                    result = memex_apply_action(
                        vault_path,
                        action=ACTION_CREATE,
                        kind=kind,
                        name=name,
                        body=body,
                        source_id=source_id,
                        source_title=source_title,
                        tags=["claude-code", "claude-devs", str(action.get("action_type") or "")],
                    )
                result["post_id"] = post_id
                result["entity_name"] = name
                result["entity_kind"] = kind
                results.append(result)
            except Exception as exc:
                results.append(
                    {
                        "action": "ERROR",
                        "post_id": post_id,
                        "entity_name": name,
                        "entity_kind": kind,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                    }
                )
    return results


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

    # Memex pass (PR 15) — CREATE/EXTEND entity/concept pages from each
    # action. Runs AFTER the source-page writes so the immutable raw and
    # source layers are already in place when entity pages reference them.
    # Disabled via UA_CSI_MEMEX_WIRING_ENABLED=0 if it ever needs an
    # emergency off switch in production.
    memex_actions: list[dict[str, Any]] = []
    if str(os.getenv("UA_CSI_MEMEX_WIRING_ENABLED") or "1").strip().lower() not in {"0", "false", "no", "off"}:
        try:
            memex_actions = apply_memex_pass(
                vault_path=context.path,
                handle=handle,
                actions=actions,
                linked_source_entries=linked_source_entries,
            )
        except Exception:
            # Memex failure must never block the source-page writes that
            # already succeeded. Surface in the return; downstream callers
            # can log it.
            import logging

            logging.getLogger(__name__).exception("apply_memex_pass failed; vault sources still written")

    return {
        "vault_path": str(context.path),
        "pages": pages,
        "post_pages_by_post_id": post_pages_by_post_id,
        "linked_pages_by_post_id": linked_pages_by_post_id,
        "work_product_pages": work_product_pages,
        "email_evidence_ids": email_evidence_ids,
        "memex_actions": memex_actions,
    }
def reconcile_packet_candidate_ledger(
    *,
    packet_dir: Path,
    conn: sqlite3.Connection | None,
    artifacts_root: Path | None = None,
) -> dict[str, Any]:
    packet_dir = packet_dir.expanduser().resolve()
    payload = load_packet(packet_dir)
    actions = payload["actions"]
    handle = str(payload["manifest"].get("handle") or "ClaudeDevs")

    summary_path = packet_dir / "replay_summary.json"
    packet_artifact_id = ""
    vault_result: dict[str, Any] = {}

    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            packet_artifact_id = summary.get("packet_artifact_id") or ""
            vault_result = {
                "pages": summary.get("wiki_pages") or [],
                "email_evidence_ids": summary.get("email_evidence_ids") or [],
            }
        except Exception:
            pass

    ledger_entries = build_candidate_ledger(
        packet_dir=packet_dir,
        handle=handle,
        actions=actions,
        conn=conn,
        packet_artifact_id=packet_artifact_id,
        vault_result=vault_result,
        artifacts_root=artifacts_root,
    )
    return {
        "ok": True,
        "packet_ledger_path": ledger_entries["packet_ledger_path"],
        "lane_ledger_path": ledger_entries["lane_ledger_path"],
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
        email_task_mapping_evidence = _collect_email_task_mapping_evidence(conn, task_identity["task_id"]) if conn and task_identity["task_id"] else []
        proactive_artifact_email_evidence = _collect_proactive_artifact_email_evidence(
            conn,
            artifact_ids=[packet_artifact_id, candidate_artifact_id],
        ) if conn else []
        email_evidence_ids = sorted(
            {
                *packet_email_evidence_ids,
                *workspace_email_evidence_ids,
                *[
                    record["evidence_id"]
                    for record in email_task_mapping_evidence + proactive_artifact_email_evidence
                    if str(record.get("evidence_id") or "").strip()
                ],
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
            "email_evidence_records": email_task_mapping_evidence + proactive_artifact_email_evidence,
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


def _collect_email_task_mapping_evidence(conn: sqlite3.Connection | None, task_id: str) -> list[dict[str, str]]:
    if conn is None or not task_id:
        return []
    try:
        row = conn.execute(
            """
            SELECT thread_id, ack_message_id, ack_draft_id, final_message_id, final_draft_id
            FROM email_task_mappings
            WHERE task_id = ?
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
    except Exception:
        return []
    if not row:
        return []
    records: list[dict[str, str]] = []
    thread_id = str(row["thread_id"] or "").strip()
    for field, evidence_type in (
        ("ack_message_id", "ack_message"),
        ("ack_draft_id", "ack_draft"),
        ("final_message_id", "final_message"),
        ("final_draft_id", "final_draft"),
    ):
        value = str(row[field] or "").strip()
        if not value:
            continue
        records.append(
            {
                "source": "email_task_mapping",
                "thread_id": thread_id,
                "message_id": value if "message" in evidence_type else "",
                "draft_id": value if "draft" in evidence_type else "",
                "evidence_id": value,
                "evidence_type": evidence_type,
            }
        )
    return records


def _collect_proactive_artifact_email_evidence(conn: sqlite3.Connection | None, *, artifact_ids: list[str]) -> list[dict[str, str]]:
    clean_ids = [str(a or "").strip() for a in artifact_ids if str(a or "").strip()]
    if conn is None or not clean_ids:
        return []
    placeholders = ",".join("?" for _ in clean_ids)
    try:
        rows = conn.execute(
            f"""
            SELECT artifact_id, message_id, thread_id, delivery_state
            FROM proactive_artifact_emails
            WHERE artifact_id IN ({placeholders})
            ORDER BY sent_at DESC
            """,
            clean_ids,
        ).fetchall()
    except Exception:
        return []
    records: list[dict[str, str]] = []
    for row in rows:
        message_id = str(row["message_id"] or "").strip()
        thread_id = str(row["thread_id"] or "").strip()
        if not message_id and not thread_id:
            continue
        records.append(
            {
                "source": "proactive_artifact_emails",
                "artifact_id": str(row["artifact_id"] or "").strip(),
                "thread_id": thread_id,
                "message_id": message_id,
                "draft_id": "",
                "evidence_id": message_id or thread_id,
                "evidence_type": str(row["delivery_state"] or "").strip() or "emailed",
            }
        )
    return records


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
        body = resp.text
        title = _extract_title(body) or _title_from_url(str(resp.url))
        metadata["title"] = title
        entry["title"] = title
        if resp.status_code >= 400:
            entry["fetch_status"] = "error"
            entry["error"] = f"HTTP {resp.status_code}"
        elif skip_reason := _detect_unusable_link_capture(url=str(resp.url), body=body, metadata=metadata):
            entry["fetch_status"] = "skipped"
            entry["skip_reason"] = skip_reason
            analysis = _linked_source_analysis(entry=entry, content="", metadata=metadata)
        else:
            github_raw = _maybe_fetch_github_raw(client=client, metadata=metadata)
            if github_raw:
                body = github_raw
                metadata["content_type"] = "text/plain"
            content = _normalize_web_content(
                body=body,
                content_type=metadata["content_type"],
                url=str(resp.url),
                source_type=str(metadata.get("source_type") or ""),
                metadata=metadata,
            )
            entry["fetch_status"] = "fetched"
            metadata["summary_excerpt"] = _summary_excerpt(content)
            metadata["commands"] = _extract_commands(content)
            metadata["version_matches"] = _extract_versions(content)
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


def _normalize_web_content(*, body: str, content_type: str, url: str, source_type: str = "", metadata: dict[str, Any] | None = None) -> str:
    lowered = str(content_type or "").lower()
    if "application/json" in lowered:
        try:
            parsed = json.loads(body)
            return "# Linked Source\n\n```json\n" + json.dumps(parsed, indent=2, ensure_ascii=True, sort_keys=True) + "\n```\n"
        except Exception:
            pass
    if "text/plain" in lowered or url.lower().endswith(".md"):
        return body.strip() + "\n"
    html_features = _extract_html_features(body)
    if source_type in {"docs_page", "vendor_docs"}:
        lines = ["# Linked Source", ""]
        if html_features["title"]:
            lines.extend([f"## Title", "", html_features["title"], ""])
        if html_features["headings"]:
            lines.extend(["## Headings", ""])
            for heading in html_features["headings"][:12]:
                lines.append(f"- {heading}")
            lines.append("")
        if html_features["code_blocks"]:
            lines.extend(["## Code Blocks", ""])
            for block in html_features["code_blocks"][:6]:
                lines.extend(["```text", block, "```", ""])
        if html_features["list_items"]:
            lines.extend(["## Key Items", ""])
            for item in html_features["list_items"][:12]:
                lines.append(f"- {item}")
            lines.append("")
        if html_features["text"]:
            lines.extend(["## Excerpt", "", html_features["text"], ""])
        return "\n".join(lines).rstrip() + "\n"
    if source_type.startswith("github"):
        lines = ["# Linked Source", ""]
        if metadata:
            if metadata.get("github_repo"):
                lines.append(f"- Repo: `{metadata['github_repo']}`")
            if metadata.get("github_owner"):
                lines.append(f"- Owner: `{metadata['github_owner']}`")
            if metadata.get("github_path"):
                lines.append(f"- Path: `{metadata['github_path']}`")
            if metadata.get("github_ref"):
                lines.append(f"- Ref: `{metadata['github_ref']}`")
            if metadata.get("raw_url"):
                lines.append(f"- Raw URL: {metadata['raw_url']}")
            lines.append("")
        if "text/plain" in lowered:
            lines.extend(["## Raw Content", "", body.strip(), ""])
        else:
            if html_features["title"]:
                lines.extend(["## Title", "", html_features["title"], ""])
            if html_features["list_items"]:
                lines.extend(["## Key Items", ""])
                for item in html_features["list_items"][:12]:
                    lines.append(f"- {item}")
                lines.append("")
            if html_features["text"]:
                lines.extend(["## Excerpt", "", html_features["text"], ""])
        return "\n".join(lines).rstrip() + "\n"
    if source_type == "event_page":
        lines = ["# Linked Source", ""]
        if html_features["title"]:
            lines.extend(["## Event Title", "", html_features["title"], ""])
        if html_features["headings"]:
            lines.extend(["## Event Sections", ""])
            for heading in html_features["headings"][:10]:
                lines.append(f"- {heading}")
            lines.append("")
        if html_features["list_items"]:
            lines.extend(["## Event Details", ""])
            for item in html_features["list_items"][:10]:
                lines.append(f"- {item}")
            lines.append("")
        if html_features["text"]:
            lines.extend(["## Excerpt", "", html_features["text"], ""])
        return "\n".join(lines).rstrip() + "\n"

    text = html_features["text"]
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


def _detect_unusable_link_capture(*, url: str, body: str, metadata: dict[str, Any] | None = None) -> str:
    metadata = dict(metadata or {})
    source_type = str(metadata.get("source_type") or "").strip()
    if source_type == "x_page":
        return "browser_gated_x_page"

    text = _extract_html_features(body).get("text") or ""
    normalized = " ".join(str(text).split()).lower()
    blocker_fragments = (
        "javascript is not available",
        "we've detected that javascript is disabled",
        "we’ve detected that javascript is disabled",
        "please enable javascript or switch to a supported browser",
        "privacy related extensions may cause issues on x.com",
        "something went wrong, but don't fret",
        "something went wrong, but don’t fret",
    )
    if any(fragment in normalized for fragment in blocker_fragments):
        return "browser_gated_page"
    return ""


def _should_skip_link_fetch(url: str) -> bool:
    lowered = url.lower()
    if lowered.startswith("https://x.com/") or lowered.startswith("http://x.com/"):
        return True
    if lowered.startswith("https://twitter.com/") or lowered.startswith("http://twitter.com/"):
        return True
    # claude.ai is the product app, not a content source for intelligence
    if "claude.ai" in lowered:
        return True
    return False


def _classify_linked_source(*, url: str, content_type: str) -> dict[str, Any]:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    path = parsed.path or ""
    source_type = "generic_web"
    github_owner = ""
    github_repo = ""
    github_path = ""
    github_ref = ""
    if domain in {"github.com", "www.github.com"}:
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2:
            github_owner = parts[0]
            github_repo = f"{parts[0]}/{parts[1]}"
            if len(parts) == 2:
                source_type = "github_repo"
            elif len(parts) >= 4 and parts[2] in {"blob", "tree"}:
                github_ref = parts[3]
                github_path = "/".join(parts[4:])
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
        "github_ref": github_ref,
        "github_path": github_path,
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


def _extract_html_features(body: str) -> dict[str, Any]:
    cleaned = re.sub(r"(?is)<script.*?>.*?</script>", " ", body)
    cleaned = re.sub(r"(?is)<style.*?>.*?</style>", " ", cleaned)
    title = _extract_title(cleaned)
    headings = [
        html.unescape(match).strip()
        for match in re.findall(r"(?is)<h[1-3][^>]*>(.*?)</h[1-3]>", cleaned)
        if html.unescape(match).strip()
    ]
    code_blocks = [
        html.unescape(re.sub(r"(?is)<[^>]+>", "", match)).strip()
        for match in re.findall(r"(?is)<pre[^>]*>(.*?)</pre>", cleaned)
        if html.unescape(re.sub(r"(?is)<[^>]+>", "", match)).strip()
    ]
    list_items = [
        html.unescape(re.sub(r"(?is)<[^>]+>", "", match)).strip()
        for match in re.findall(r"(?is)<li[^>]*>(.*?)</li>", cleaned)
        if html.unescape(re.sub(r"(?is)<[^>]+>", "", match)).strip()
    ]
    text = cleaned
    text = re.sub(r"(?i)<br\\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    text = text[:4000]
    return {
        "title": title,
        "headings": headings,
        "code_blocks": code_blocks,
        "list_items": list_items,
        "text": text,
    }


def _maybe_fetch_github_raw(*, client: httpx.Client, metadata: dict[str, Any]) -> str:
    source_type = str(metadata.get("source_type") or "").strip()
    if source_type != "github_file":
        return ""
    owner = str(metadata.get("github_owner") or "").strip()
    repo = str(metadata.get("github_repo") or "").strip().split("/", 1)[-1]
    ref = str(metadata.get("github_ref") or "").strip()
    path = str(metadata.get("github_path") or "").strip()
    if not all((owner, repo, ref, path)):
        return ""
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
    metadata["raw_url"] = raw_url
    try:
        resp = client.get(raw_url)
    except Exception as exc:
        metadata["raw_fetch_error"] = f"{type(exc).__name__}: {exc}"
        return ""
    metadata["raw_http_status"] = resp.status_code
    if resp.status_code >= 400:
        return ""
    return resp.text


def _extract_commands(content: str) -> list[str]:
    commands: list[str] = []
    seen: set[str] = set()
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("$ ", "# ")) and len(stripped) > 2:
            candidate = stripped[2:].strip()
        else:
            candidate = stripped
        if re.match(r"^(npm|pnpm|yarn|pip|python|uv|claude|git|curl|bash|node)\b", candidate):
            if candidate not in seen:
                seen.add(candidate)
                commands.append(candidate[:240])
    return commands[:12]


def _extract_versions(content: str) -> list[str]:
    seen: set[str] = set()
    versions: list[str] = []
    for match in re.findall(r"\bv?\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9_.-]+)?\b", content):
        if match not in seen:
            seen.add(match)
            versions.append(match)
    return versions[:12]


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

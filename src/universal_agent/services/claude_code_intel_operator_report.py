from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
import urllib.parse

from universal_agent.artifacts import resolve_artifacts_dir


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _excerpt(value: str, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def artifact_file_url(
    path: Path | str,
    *,
    artifacts_root: Path | None = None,
    frontend_url: str | None = None,
) -> str:
    candidate = Path(path).expanduser().resolve()
    root = (artifacts_root or resolve_artifacts_dir()).resolve()
    try:
        rel_path = candidate.relative_to(root)
    except Exception:
        return ""
    app_url = str(frontend_url or os.getenv("FRONTEND_URL") or "https://app.clearspringcg.com").rstrip("/")
    quoted = urllib.parse.quote(rel_path.as_posix(), safe="/")
    return f"{app_url}/api/artifacts/files/{quoted}"


def build_operator_report(
    *,
    sync_payload: dict[str, Any],
    artifacts_root: Path | None = None,
    frontend_url: str | None = None,
) -> dict[str, Any]:
    packet_dir = Path(str(sync_payload.get("packet_dir") or "")).expanduser().resolve()
    post_process = dict(sync_payload.get("post_process") or {})
    root = (artifacts_root or resolve_artifacts_dir()).resolve()

    actions = _load_json(packet_dir / "actions.json", [])
    ledger = _load_json(packet_dir / "candidate_ledger.json", [])
    linked_sources = _load_json(packet_dir / "linked_sources.json", [])
    manifest = _load_json(packet_dir / "manifest.json", {})
    action_by_post_id = {
        str(item.get("post_id") or "").strip(): item
        for item in actions
        if str(item.get("post_id") or "").strip()
    }

    top_rows: list[dict[str, Any]] = []
    sorted_ledger = sorted(
        ledger,
        key=lambda row: (
            int(row.get("tier") or 0),
            len(row.get("assignment_ids") or []),
            str(row.get("post_id") or ""),
        ),
        reverse=True,
    )
    for row in sorted_ledger[:8]:
        post_id = str(row.get("post_id") or "").strip()
        action = action_by_post_id.get(post_id, {})
        top_rows.append(
            {
                "post_id": post_id,
                "tier": int(row.get("tier") or 0),
                "action_type": str(row.get("action_type") or ""),
                "task_id": str(row.get("task_id") or ""),
                "assignment_ids": [str(value) for value in (row.get("assignment_ids") or [])],
                "email_evidence_ids": [str(value) for value in (row.get("email_evidence_ids") or [])],
                "wiki_pages": [str(value) for value in (row.get("wiki_pages") or [])][:6],
                "post_url": str(row.get("post_url") or action.get("url") or ""),
                "text_excerpt": _excerpt(str(action.get("text") or "")),
            }
        )

    tier_counts: dict[str, int] = {}
    action_type_counts: dict[str, int] = {}
    for row in ledger:
        tier_key = str(int(row.get("tier") or 0))
        tier_counts[tier_key] = tier_counts.get(tier_key, 0) + 1
        action_key = str(row.get("action_type") or "").strip() or "unknown"
        action_type_counts[action_key] = action_type_counts.get(action_key, 0) + 1

    linked_source_fetched_count = 0
    for item in linked_sources:
        if str(item.get("fetch_status") or "").strip() == "fetched":
            linked_source_fetched_count += 1

    report_md_path = packet_dir / "operator_report.md"
    report_json_path = packet_dir / "operator_report.json"
    digest_path = packet_dir / "digest.md"
    candidate_ledger_path = packet_dir / "candidate_ledger.json"
    linked_sources_path = packet_dir / "linked_sources.json"
    implementation_opportunities_path = packet_dir / "implementation_opportunities.md"
    lane_ledger_path = Path(str(post_process.get("lane_ledger_path") or "")).expanduser() if post_process.get("lane_ledger_path") else None
    vault_root = Path(str(post_process.get("vault_path") or (root / "knowledge-vaults" / "claude-code-intelligence"))).expanduser().resolve()
    vault_index_path = vault_root / "index.md"

    links = {
        "operator_report": artifact_file_url(report_md_path, artifacts_root=root, frontend_url=frontend_url),
        "digest": artifact_file_url(digest_path, artifacts_root=root, frontend_url=frontend_url),
        "candidate_ledger": artifact_file_url(candidate_ledger_path, artifacts_root=root, frontend_url=frontend_url),
        "linked_sources": artifact_file_url(linked_sources_path, artifacts_root=root, frontend_url=frontend_url),
        "implementation_opportunities": artifact_file_url(
            implementation_opportunities_path,
            artifacts_root=root,
            frontend_url=frontend_url,
        ),
        "vault_index": artifact_file_url(vault_index_path, artifacts_root=root, frontend_url=frontend_url),
    }
    if lane_ledger_path:
        links["lane_ledger"] = artifact_file_url(lane_ledger_path, artifacts_root=root, frontend_url=frontend_url)

    wiki_page_urls: list[dict[str, str]] = []
    for relative_page in list(post_process.get("wiki_pages") or [])[:10]:
        rel = str(relative_page or "").strip()
        if not rel:
            continue
        absolute = (vault_root / rel).resolve()
        wiki_page_urls.append(
            {
                "path": rel,
                "url": artifact_file_url(absolute, artifacts_root=root, frontend_url=frontend_url),
            }
        )

    summary = {
        "ok": bool(sync_payload.get("ok")),
        "generated_at": str(sync_payload.get("generated_at") or manifest.get("generated_at") or ""),
        "handle": str(sync_payload.get("handle") or manifest.get("handle") or ""),
        "user_id": str(sync_payload.get("user_id") or ""),
        "packet_dir": str(packet_dir),
        "artifact_id": str(sync_payload.get("artifact_id") or ""),
        "packet_artifact_id": str(post_process.get("packet_artifact_id") or ""),
        "new_post_count": int(sync_payload.get("new_post_count") or 0),
        "seen_post_count": int(sync_payload.get("seen_post_count") or 0),
        "action_count": int(sync_payload.get("action_count") or 0),
        "queued_task_count": int(sync_payload.get("queued_task_count") or 0),
        "linked_source_count": len(linked_sources),
        "linked_source_fetched_count": linked_source_fetched_count,
        "tier_counts": tier_counts,
        "action_type_counts": action_type_counts,
        "links": links,
        "top_rows": top_rows,
        "wiki_page_urls": wiki_page_urls,
        "checkpoint_note": (
            "The lane keeps a durable last_seen_post_id checkpoint under "
            "artifacts/proactive/claude_code_intel/state.json. Re-running with no new posts "
            "produces a fresh packet with zero new posts/actions instead of replaying old work."
        ),
        "error": str(sync_payload.get("error") or ""),
    }

    lines = [
        "# ClaudeDevs X Intel Operator Report",
        "",
        "## Run Summary",
        "",
        f"- Handle: `@{summary['handle']}`",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Status: `{'ok' if summary['ok'] else 'error'}`",
        f"- New posts: `{summary['new_post_count']}`",
        f"- Actions: `{summary['action_count']}`",
        f"- Queued Task Hub items: `{summary['queued_task_count']}`",
        f"- Linked sources: `{summary['linked_source_count']}` (`{summary['linked_source_fetched_count']}` fetched)",
        f"- Packet artifact id: `{summary['packet_artifact_id'] or summary['artifact_id']}`",
        "",
        "## Artifact Links",
        "",
        f"- Operator report: {links['operator_report'] or report_md_path}",
        f"- Digest: {links['digest'] or digest_path}",
        f"- Candidate ledger: {links['candidate_ledger'] or candidate_ledger_path}",
        f"- Linked sources: {links['linked_sources'] or linked_sources_path}",
        f"- Implementation opportunities: {links['implementation_opportunities'] or implementation_opportunities_path}",
        f"- Vault index: {links['vault_index'] or vault_index_path}",
    ]
    if links.get("lane_ledger"):
        lines.append(f"- Lane ledger: {links['lane_ledger']}")

    lines.extend(
        [
            "",
            "## Classification Counts",
            "",
            f"- Tier counts: `{json.dumps(tier_counts, sort_keys=True)}`",
            f"- Action type counts: `{json.dumps(action_type_counts, sort_keys=True)}`",
            "",
            "## Top Packet Rows",
            "",
        ]
    )

    if top_rows:
        for row in top_rows:
            lines.extend(
                [
                    f"### Post `{row['post_id']}`",
                    f"- Tier / action: `{row['tier']}` / `{row['action_type']}`",
                    f"- Post URL: {row['post_url'] or '(none)'}",
                    f"- Task ID: `{row['task_id'] or ''}`",
                    f"- Assignments: `{json.dumps(row['assignment_ids'])}`",
                    f"- Email evidence ids: `{json.dumps(row['email_evidence_ids'])}`",
                    f"- Wiki pages: `{json.dumps(row['wiki_pages'])}`",
                ]
            )
            if row["text_excerpt"]:
                lines.append(f"- Excerpt: {row['text_excerpt']}")
            lines.append("")
    else:
        lines.extend(["No candidate ledger rows were generated for this run.", ""])

    lines.extend(["## Checkpoint Behavior", "", summary["checkpoint_note"], ""])

    if wiki_page_urls:
        lines.extend(["## Sample Wiki Pages", ""])
        for item in wiki_page_urls:
            lines.append(f"- `{item['path']}` -> {item['url'] or '(no url)'}")
        lines.append("")

    if summary["error"]:
        lines.extend(["## Error", "", f"`{summary['error']}`", ""])

    report_md_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    report_json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True, sort_keys=True), encoding="utf-8")

    summary["report_markdown_path"] = str(report_md_path)
    summary["report_json_path"] = str(report_json_path)
    summary["report_markdown_url"] = links["operator_report"]
    return summary


def build_operator_email(summary: dict[str, Any]) -> tuple[str, str, str]:
    subject = (
        f"[ClaudeDevs X Intel] @{summary.get('handle') or 'ClaudeDevs'} sync "
        f"({summary.get('new_post_count', 0)} new / {summary.get('action_count', 0)} actions)"
    )
    lines = [
        f"ClaudeDevs X sync finished at {summary.get('generated_at') or '(unknown time)'}.",
        "",
        f"Status: {'ok' if summary.get('ok') else 'error'}",
        f"New posts: {summary.get('new_post_count', 0)}",
        f"Actions: {summary.get('action_count', 0)}",
        f"Queued Task Hub items: {summary.get('queued_task_count', 0)}",
        f"Linked sources: {summary.get('linked_source_count', 0)} ({summary.get('linked_source_fetched_count', 0)} fetched)",
        "",
        "Artifacts:",
    ]
    for label, value in (
        ("Operator report", summary.get("report_markdown_url") or summary.get("report_markdown_path")),
        ("Digest", (summary.get("links") or {}).get("digest")),
        ("Candidate ledger", (summary.get("links") or {}).get("candidate_ledger")),
        ("Linked sources", (summary.get("links") or {}).get("linked_sources")),
        ("Implementation opportunities", (summary.get("links") or {}).get("implementation_opportunities")),
        ("Vault index", (summary.get("links") or {}).get("vault_index")),
    ):
        if value:
            lines.append(f"- {label}: {value}")

    top_rows = list(summary.get("top_rows") or [])
    if top_rows:
        lines.extend(["", "Top packet rows:"])
        for row in top_rows[:5]:
            lines.append(
                f"- post {row.get('post_id')} | tier {row.get('tier')} | "
                f"{row.get('action_type')} | {row.get('post_url') or ''}"
            )

    checkpoint_note = str(summary.get("checkpoint_note") or "").strip()
    if checkpoint_note:
        lines.extend(["", checkpoint_note])

    if summary.get("error"):
        lines.extend(["", f"Error: {summary['error']}"])

    text = "\n".join(lines).strip() + "\n"

    html_lines = [
        "<html><body>",
        "<p><strong>ClaudeDevs X sync finished.</strong></p>",
        "<ul>",
        f"<li>Status: {'ok' if summary.get('ok') else 'error'}</li>",
        f"<li>New posts: {summary.get('new_post_count', 0)}</li>",
        f"<li>Actions: {summary.get('action_count', 0)}</li>",
        f"<li>Queued Task Hub items: {summary.get('queued_task_count', 0)}</li>",
        f"<li>Linked sources: {summary.get('linked_source_count', 0)} ({summary.get('linked_source_fetched_count', 0)} fetched)</li>",
        "</ul>",
        "<p><strong>Artifacts</strong></p>",
        "<ul>",
    ]
    for label, value in (
        ("Operator report", summary.get("report_markdown_url") or summary.get("report_markdown_path")),
        ("Digest", (summary.get("links") or {}).get("digest")),
        ("Candidate ledger", (summary.get("links") or {}).get("candidate_ledger")),
        ("Linked sources", (summary.get("links") or {}).get("linked_sources")),
        ("Implementation opportunities", (summary.get("links") or {}).get("implementation_opportunities")),
        ("Vault index", (summary.get("links") or {}).get("vault_index")),
    ):
        if value:
            html_lines.append(f"<li><a href=\"{value}\">{label}</a></li>")
    html_lines.append("</ul>")
    if top_rows:
        html_lines.append("<p><strong>Top packet rows</strong></p><ul>")
        for row in top_rows[:5]:
            post_url = row.get("post_url") or ""
            label = f"post {row.get('post_id')} | tier {row.get('tier')} | {row.get('action_type')}"
            if post_url:
                html_lines.append(f"<li><a href=\"{post_url}\">{label}</a></li>")
            else:
                html_lines.append(f"<li>{label}</li>")
        html_lines.append("</ul>")
    if checkpoint_note:
        html_lines.append(f"<p>{checkpoint_note}</p>")
    if summary.get("error"):
        html_lines.append(f"<p><strong>Error:</strong> {summary['error']}</p>")
    html_lines.append("</body></html>")
    return subject, text, "".join(html_lines)

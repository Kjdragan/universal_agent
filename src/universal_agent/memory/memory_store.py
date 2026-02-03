from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .memory_models import MemoryEntry
from .memory_index import append_index_entry, recent_entries


@dataclass
class MemoryPaths:
    workspace_dir: str
    memory_dir: str
    memory_md: str
    index_path: str


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _ensure_memory_md(path: str) -> None:
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Agent Memory\n\nPersistent context for the agent.\n")


def ensure_memory_scaffold(workspace_dir: str) -> MemoryPaths:
    memory_dir = os.path.join(workspace_dir, "memory")
    memory_md = os.path.join(workspace_dir, "MEMORY.md")
    index_path = os.path.join(memory_dir, "index.json")
    _ensure_dir(memory_dir)
    _ensure_memory_md(memory_md)
    return MemoryPaths(
        workspace_dir=workspace_dir,
        memory_dir=memory_dir,
        memory_md=memory_md,
        index_path=index_path,
    )


def _parse_iso_date(timestamp: str) -> str:
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.utcnow()
    return dt.strftime("%Y-%m-%d")


def _format_entry(entry: MemoryEntry) -> str:
    tag_str = ", ".join(entry.tags) if entry.tags else "(none)"
    summary = entry.summary or ""
    lines = [
        f"## {entry.timestamp} — {entry.source}",
        f"- session: {entry.session_id or 'unknown'}",
        f"- tags: {tag_str}",
    ]
    if summary:
        lines.append(f"- summary: {summary}")
    lines.append("")
    lines.append(entry.content.strip())
    lines.append("")
    return "\n".join(lines)


def append_memory_entry(
    workspace_dir: str,
    entry: MemoryEntry,
    max_chars: int = 4000,
) -> MemoryPaths:
    paths = ensure_memory_scaffold(workspace_dir)

    content = entry.content.strip()
    if max_chars > 0 and len(content) > max_chars:
        content = content[-max_chars:]
        entry.content = content

    if not entry.summary:
        entry.summary = _summarize_content(content)

    date_str = _parse_iso_date(entry.timestamp)
    daily_path = os.path.join(paths.memory_dir, f"{date_str}.md")

    with open(daily_path, "a", encoding="utf-8") as f:
        f.write(_format_entry(entry))
        f.write("\n")

    preview = (entry.summary or entry.content)[:280]
    append_index_entry(paths.index_path, entry, daily_path, preview)

    update_recent_context_section(paths, max_entries=10)
    return paths


def _summarize_content(content: str, max_len: int = 240) -> str:
    cleaned = " ".join(content.split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3] + "..."


def _upsert_section(md_text: str, section_name: str, section_body: str) -> str:
    header = f"## [{section_name}]"
    if header in md_text:
        before, rest = md_text.split(header, 1)
        # Remove existing section body
        remainder = rest.split("\n## [", 1)
        if len(remainder) == 2:
            _, tail = remainder
            tail = "## [" + tail
        else:
            tail = ""
        return before.rstrip() + "\n\n" + header + "\n" + section_body.strip() + "\n\n" + tail.lstrip()

    return md_text.rstrip() + "\n\n" + header + "\n" + section_body.strip() + "\n"


def update_recent_context_section(
    paths: MemoryPaths,
    max_entries: int = 10,
) -> None:
    entries = recent_entries(paths.index_path, limit=max_entries)
    if not entries:
        return

    lines = ["Recent context snapshots (most recent first):", ""]
    for entry in entries:
        ts = entry.get("timestamp", "")
        summary = entry.get("summary") or entry.get("preview") or ""
        tags = ", ".join(entry.get("tags") or [])
        if tags:
            lines.append(f"- {ts}: {summary} (tags: {tags})")
        else:
            lines.append(f"- {ts}: {summary}")

    section_body = "\n".join(lines)
    with open(paths.memory_md, "r", encoding="utf-8") as f:
        md_text = f.read()

    updated = _upsert_section(md_text, "RECENT_CONTEXT", section_body)
    if updated != md_text:
        with open(paths.memory_md, "w", encoding="utf-8") as f:
            f.write(updated)

from __future__ import annotations

import os
from typing import List

from .memory_index import recent_entries
from .memory_store import ensure_memory_scaffold


def _estimate_tokens(text: str) -> int:
    # Rough heuristic: 1 token per ~4 chars
    return max(1, len(text) // 4)


def _trim_to_token_budget(lines: List[str], max_tokens: int) -> str:
    if max_tokens <= 0:
        return ""
    output: List[str] = []
    token_count = 0
    for line in lines:
        tokens = _estimate_tokens(line)
        if token_count + tokens > max_tokens:
            break
        output.append(line)
        token_count += tokens
    return "\n".join(output).strip()


def build_file_memory_context(
    workspace_dir: str,
    max_tokens: int = 800,
    index_mode: str = "json",
    recent_limit: int = 8,
) -> str:
    if not workspace_dir:
        return ""

    paths = ensure_memory_scaffold(workspace_dir)

    if index_mode == "off":
        # Fallback: read MEMORY.md tail
        try:
            with open(paths.memory_md, "r", encoding="utf-8") as f:
                md_text = f.read()
            if not md_text.strip():
                return ""
            tail = md_text.strip().splitlines()[-50:]
            context = "\n".join(["# 🧠 FILE MEMORY (Tail)", ""] + tail)
            return _trim_to_token_budget(context.splitlines(), max_tokens)
        except Exception:
            return ""

    entries = recent_entries(paths.index_path, limit=recent_limit)
    if not entries:
        return ""

    lines = ["# 🧠 FILE MEMORY (Recent)", ""]
    for entry in entries:
        ts = entry.get("timestamp", "")
        summary = entry.get("summary") or entry.get("preview") or ""
        tags = ", ".join(entry.get("tags") or [])
        if tags:
            lines.append(f"- {ts}: {summary} (tags: {tags})")
        else:
            lines.append(f"- {ts}: {summary}")

    return _trim_to_token_budget(lines, max_tokens)

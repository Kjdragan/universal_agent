"""
heartbeat_scope_filter.py — Filter HEARTBEAT.md sections by factory role scope.

Sections are delimited by HTML comment markers:
  <!-- scope:hq -->      Only kept when heartbeat_scope == "global" (HQ/VPS)
  <!-- scope:local -->    Only kept when heartbeat_scope == "local" (desktop worker)
  <!-- scope:all -->      Always kept (explicit marker, same as unmarked)

Unmarked content (before the first marker, or between markers) defaults to "all".
"""
from __future__ import annotations

import re
from typing import Optional

_SCOPE_MARKER_RE = re.compile(
    r"^\s*<!--\s*scope:(hq|local|all)\s*-->\s*$",
    re.MULTILINE,
)

# Map heartbeat_scope values to which scope markers to keep
_SCOPE_KEEP_MAP: dict[str, set[str]] = {
    "global": {"hq", "all"},   # HQ keeps hq + all
    "local":  {"local", "all"},  # Desktop keeps local + all
}


def filter_heartbeat_by_scope(
    content: str,
    heartbeat_scope: str = "global",
) -> str:
    """Filter HEARTBEAT.md content, keeping only sections matching the scope.

    Parameters
    ----------
    content : str
        Raw HEARTBEAT.md text with optional ``<!-- scope:X -->`` markers.
    heartbeat_scope : str
        ``"global"`` for HQ/VPS, ``"local"`` for desktop worker.

    Returns
    -------
    str
        Filtered content with irrelevant sections removed.
    """
    if not content or not content.strip():
        return content

    keep = _SCOPE_KEEP_MAP.get(heartbeat_scope, {"hq", "local", "all"})

    # Split into (scope, text) segments
    parts = _SCOPE_MARKER_RE.split(content)

    # parts alternates: [text_before_first_marker, scope1, text1, scope2, text2, ...]
    # If no markers found, return as-is
    if len(parts) <= 1:
        return content

    result_chunks: list[str] = []

    # First chunk (before any marker) is scope:all by default
    if parts[0].strip():
        result_chunks.append(parts[0])

    # Process marker/text pairs
    for i in range(1, len(parts), 2):
        scope_tag = parts[i]  # "hq", "local", or "all"
        text = parts[i + 1] if i + 1 < len(parts) else ""
        if scope_tag in keep and text.strip():
            result_chunks.append(text)

    return "".join(result_chunks).strip() + "\n"

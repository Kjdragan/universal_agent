"""
Transform manual webhook payloads into a normalized YouTube agent action.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import parse_qs, urlparse


def _extract_video_id(video_url: str | None) -> str | None:
    if not video_url:
        return None
    try:
        parsed = urlparse(video_url)
    except Exception:
        return None

    host = parsed.netloc.lower()
    path = parsed.path.strip("/")

    if "youtu.be" in host:
        return path.split("/", 1)[0] or None
    if "youtube.com" in host:
        if path == "watch":
            values = parse_qs(parsed.query).get("v", [])
            return values[0] if values else None
        if path.startswith("shorts/"):
            return path.split("/", 1)[1] or None
        if path.startswith("live/"):
            return path.split("/", 1)[1] or None
    return None


def _safe_segment(value: str | None, fallback: str) -> str:
    text = (value or "").strip()
    if not text:
        return fallback
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", text)
    safe = safe.strip("_")
    return safe or fallback


def transform(ctx: dict[str, Any]) -> dict[str, Any] | None:
    payload = ctx.get("payload", {}) if isinstance(ctx, dict) else {}
    if not isinstance(payload, dict):
        return None

    video_url = payload.get("video_url")
    if isinstance(video_url, str):
        video_url = video_url.strip()
    else:
        video_url = ""

    video_id = payload.get("video_id")
    if isinstance(video_id, str):
        video_id = video_id.strip()
    else:
        video_id = ""

    if not video_id and video_url:
        video_id = _extract_video_id(video_url) or ""
    if not video_url and video_id:
        video_url = f"https://www.youtube.com/watch?v={video_id}"

    if not video_url:
        return None

    mode = payload.get("mode")
    if not isinstance(mode, str) or not mode.strip():
        mode = "explainer_only"
    allow_degraded = bool(payload.get("allow_degraded_transcript_only", True))

    channel_id = payload.get("channel_id")
    if not isinstance(channel_id, str):
        channel_id = ""
    channel_seg = _safe_segment(channel_id, "manual")

    if video_id:
        video_seg = _safe_segment(video_id, "manual")
    else:
        seed = video_url.encode("utf-8", errors="replace")
        video_seg = hashlib.sha256(seed).hexdigest()[:12]

    session_key = f"yt_{channel_seg}_{video_seg}"

    lines = [
        "Manual YouTube URL ingestion event received.",
        f"video_url: {video_url}",
        f"video_id: {video_id}",
        f"channel_id: {channel_id}",
        f"title: {payload.get('title', '')}",
        f"mode: {mode}",
        f"allow_degraded_transcript_only: {str(allow_degraded).lower()}",
        "Use visual analysis when possible. Continue with transcript-only mode when visual processing is unavailable.",
    ]

    return {
        "kind": "agent",
        "name": "ManualYouTubeWebhook",
        "session_key": session_key,
        "message": "\n".join(lines),
    }

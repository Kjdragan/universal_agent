"""
Transform manual webhook payloads into a normalized YouTube agent action.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

YOUTUBE_LEARNING_SUBAGENT = "youtube-explainer-expert"
MODE_EXPLAINER_ONLY = "explainer_only"
MODE_EXPLAINER_PLUS_CODE = "explainer_plus_code"
LEARNING_MODE_CONCEPT_ONLY = "concept_only"
LEARNING_MODE_CONCEPT_PLUS_IMPLEMENTATION = "concept_plus_implementation"


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


def _normalize_mode(raw_mode: Any) -> str:
    if not isinstance(raw_mode, str):
        return MODE_EXPLAINER_ONLY
    mode = raw_mode.strip().lower()
    if not mode:
        return MODE_EXPLAINER_ONLY
    if mode in {MODE_EXPLAINER_ONLY, "explain", "explanation", "explainer"}:
        return MODE_EXPLAINER_ONLY
    if mode in {
        MODE_EXPLAINER_PLUS_CODE,
        "plus_code",
        "code",
        "with_code",
        "explainer_with_code",
        "explain_and_code",
    }:
        return MODE_EXPLAINER_PLUS_CODE
    return MODE_EXPLAINER_ONLY


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _learning_mode_from_mode(mode: str) -> str:
    if mode == MODE_EXPLAINER_PLUS_CODE:
        return LEARNING_MODE_CONCEPT_PLUS_IMPLEMENTATION
    return LEARNING_MODE_CONCEPT_ONLY


def _resolve_artifacts_root_hint() -> str:
    raw = (os.getenv("UA_ARTIFACTS_DIR") or "").strip()
    if raw:
        return raw
    try:
        from universal_agent.artifacts import resolve_artifacts_dir

        return str(resolve_artifacts_dir())
    except Exception:
        return "artifacts"


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

    mode = _normalize_mode(payload.get("mode") or MODE_EXPLAINER_PLUS_CODE)
    learning_mode = _learning_mode_from_mode(mode)
    allow_degraded = _coerce_bool(payload.get("allow_degraded_transcript_only"), default=True)
    artifacts_root = _resolve_artifacts_root_hint()

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
        "Route this run to the YouTube learning specialist.",
        f"target_subagent: {YOUTUBE_LEARNING_SUBAGENT}",
        "Use the youtube-tutorial-learning skill workflow.",
        "Produce durable learning artifacts in UA_ARTIFACTS_DIR.",
        f"resolved_artifacts_root: {artifacts_root}",
        "Path rule: do not use a literal UA_ARTIFACTS_DIR folder segment in file paths.",
        "Invalid paths: /opt/universal_agent/UA_ARTIFACTS_DIR/... and UA_ARTIFACTS_DIR/...",
        f"Use this absolute durable base path: {artifacts_root}/youtube-tutorial-learning/...",
        "Required artifacts: README.md, CONCEPT.md, IMPLEMENTATION.md, implementation/, manifest.json.",
        "Create required artifacts first and keep them even if extraction fails.",
        "On extraction failure, set manifest status to degraded_transcript_only or failed (never leave empty run dirs).",
        f"video_url: {video_url}",
        f"video_id: {video_id}",
        f"channel_id: {channel_id}",
        f"title: {payload.get('title', '')}",
        f"mode: {mode}",
        f"learning_mode: {learning_mode}",
        f"allow_degraded_transcript_only: {str(allow_degraded).lower()}",
        "If learning_mode is concept_plus_implementation, include runnable code in implementation/ and explain how to run it.",
        "Transcript path: use youtube-transcript-api instance API as the single transcript source (no yt-dlp transcript fallback).",
        "Video analysis path: use Gemini multimodal video understanding with the YouTube URL directly when available.",
        "Use visual analysis when possible. Continue with transcript-only mode when visual processing is unavailable.",
    ]

    return {
        "kind": "agent",
        "name": "ManualYouTubeWebhook",
        "session_key": session_key,
        "to": YOUTUBE_LEARNING_SUBAGENT,
        "message": "\n".join(lines),
    }

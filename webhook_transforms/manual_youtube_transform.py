"""
Transform manual webhook payloads into a normalized YouTube agent action.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

YOUTUBE_LEARNING_SUBAGENT = "youtube-expert"
MODE_EXPLAINER_ONLY = "explainer_only"
MODE_EXPLAINER_PLUS_CODE = "explainer_plus_code"
MODE_AUTO = "auto"
LEARNING_MODE_CONCEPT_ONLY = "concept_only"
LEARNING_MODE_CONCEPT_PLUS_IMPLEMENTATION = "concept_plus_implementation"
_CODE_HINT_KEYWORDS = {
    "code",
    "coding",
    "programming",
    "python",
    "javascript",
    "typescript",
    "react",
    "nextjs",
    "next.js",
    "mcp",
    "api",
    "sdk",
    "cli",
    "sql",
    "database",
    "docker",
    "kubernetes",
    "repo",
    "github",
    "automation",
    "agent",
}
_NON_CODE_HINT_KEYWORDS = {
    "recipe",
    "cooking",
    "cook",
    "food",
    "kitchen",
    "grill",
    "charcoal",
    "souvlaki",
    "baking",
    "travel",
    "vlog",
    "music",
    "song",
    "workout",
    "fitness",
}


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
    if mode in {MODE_AUTO, "detect", "auto_detect"}:
        return MODE_AUTO
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


def _is_probably_code_tutorial(*parts: Any) -> bool:
    tokens = " ".join(str(part or "") for part in parts).strip().lower()
    if not tokens:
        return False
    has_code = any(keyword in tokens for keyword in _CODE_HINT_KEYWORDS)
    has_non_code = any(keyword in tokens for keyword in _NON_CODE_HINT_KEYWORDS)
    if has_non_code and not has_code:
        return False
    return has_code


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

    channel_id = payload.get("channel_id")
    if not isinstance(channel_id, str):
        channel_id = ""

    description = payload.get("description")
    if not isinstance(description, str):
        description = ""

    explicit_mode = payload.get("mode")
    mode = _normalize_mode(explicit_mode or MODE_AUTO)
    if mode == MODE_AUTO:
        title_hint = payload.get("title")
        mode = (
            MODE_EXPLAINER_PLUS_CODE
            if _is_probably_code_tutorial(title_hint, channel_id, video_url, description)
            else MODE_EXPLAINER_ONLY
        )
    learning_mode = _learning_mode_from_mode(mode)
    allow_degraded = _coerce_bool(payload.get("allow_degraded_transcript_only"), default=True)
    artifacts_root = _resolve_artifacts_root_hint()

    channel_seg = _safe_segment(channel_id, "manual")

    if video_id:
        video_seg = _safe_segment(video_id, "manual")
    else:
        seed = video_url.encode("utf-8", errors="replace")
        video_seg = hashlib.sha256(seed).hexdigest()[:12]

    session_key = f"yt_{channel_seg}_{video_seg}"

    lines = [
        "Manual YouTube URL ingestion event received.",
        "Route this run to the YouTube specialist.",
        f"target_subagent: {YOUTUBE_LEARNING_SUBAGENT}",
        "Ingestion first: use youtube-transcript-metadata skill for transcript+metadata.",
        "Then use youtube-tutorial-creation for durable tutorial artifacts.",
        "Produce durable learning artifacts in UA_ARTIFACTS_DIR.",
        f"resolved_artifacts_root: {artifacts_root}",
        "Path rule: do not use a literal UA_ARTIFACTS_DIR folder segment in file paths.",
        "Invalid paths: /opt/universal_agent/UA_ARTIFACTS_DIR/... and UA_ARTIFACTS_DIR/...",
        f"Use this absolute durable base path: {artifacts_root}/youtube-tutorial-creation/...",
        "Required baseline artifacts: README.md, CONCEPT.md, manifest.json.",
        "If learning_mode is concept_plus_implementation, also create IMPLEMENTATION.md and implementation/ with runnable code.",
        "If learning_mode is concept_only, keep implementation procedural (no repo bootstrap scripts).",
        "Create required artifacts first and keep them even if extraction fails.",
        "On extraction failure, set manifest status to degraded_transcript_only or failed (never leave empty run dirs).",
        f"video_url: {video_url}",
        f"video_id: {video_id}",
        f"channel_id: {channel_id}",
        f"title: {payload.get('title', '')}",
        f"description_hint: {description[:500] if description else ''}",
        f"mode: {mode}",
        f"learning_mode: {learning_mode}",
        f"allow_degraded_transcript_only: {str(allow_degraded).lower()}",
        "Set implementation_required=true only when transcript+metadata confirm software/coding content.",
        "If learning_mode is concept_plus_implementation, include runnable code in implementation/ and explain how to run it.",
        "Transcript path: youtube-transcript-api is source of truth. yt-dlp is metadata-only.",
        "Video analysis path: for concept_plus_implementation runs only, use ZAI Vision video analysis when available.",
        "Skip optional video/vision analysis for concept_only runs. Continue with transcript-only mode when visual processing is unavailable.",
        "DESCRIPTION LINK ANALYSIS: After metadata ingestion (Step 3d), check metadata.description for useful links.",
        "Extract URLs from the video description. Classify each as: github_repo, kaggle_competition, documentation, dataset, or other.",
        "For high-value links (GitHub repos, Kaggle problems, technical docs): fetch their content using DIRECT connections (no residential proxy).",
        "For GitHub repos: fetch README and file tree. For Kaggle: fetch competition/dataset page. For docs: extract clean content.",
        "Save fetched resources under work_products/description_resources/ and use them to enrich CONCEPT.md and IMPLEMENTATION.md.",
        "Record all extracted links and their fetch status in manifest.json under description_links array.",
        "Do NOT route external link fetches through the Webshare residential proxy — only YouTube API calls use the proxy.",
    ]

    return {
        "kind": "agent",
        "name": "ManualYouTubeWebhook",
        "session_key": session_key,
        "to": YOUTUBE_LEARNING_SUBAGENT,
        "message": "\n".join(lines),
    }

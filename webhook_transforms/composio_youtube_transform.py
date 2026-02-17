"""
Transform Composio trigger webhooks into a normalized YouTube agent action.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

YOUTUBE_LEARNING_SUBAGENT = "youtube-explainer-expert"
MODE_EXPLAINER_ONLY = "explainer_only"
MODE_EXPLAINER_PLUS_CODE = "explainer_plus_code"
LEARNING_MODE_CONCEPT_ONLY = "concept_only"
LEARNING_MODE_CONCEPT_PLUS_IMPLEMENTATION = "concept_plus_implementation"


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
    return None


def _pick(data: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if key == "resourceId" and isinstance(value, dict):
            nested_video_id = value.get("videoId")
            if isinstance(nested_video_id, str) and nested_video_id.strip():
                return nested_video_id.strip()
    return None


def _flatten_event_payload(event_payload: dict[str, Any]) -> dict[str, Any]:
    """
    Flatten common Composio YouTube payload shapes so field pickers can stay simple.

    Known real-world shape:
      {"event_type":"new_playlist_item","item":{...}}
    """
    flat: dict[str, Any] = dict(event_payload)
    item = event_payload.get("item")
    if isinstance(item, dict):
        for key, value in item.items():
            if key not in flat:
                flat[key] = value

        snippet = item.get("snippet")
        if isinstance(snippet, dict):
            for key, value in snippet.items():
                if key not in flat:
                    flat[key] = value

            resource = snippet.get("resourceId")
            if isinstance(resource, dict):
                if "resourceId" not in flat:
                    flat["resourceId"] = resource
                video_id = resource.get("videoId")
                if isinstance(video_id, str) and video_id.strip() and "videoId" not in flat:
                    flat["videoId"] = video_id.strip()

        content_details = item.get("contentDetails")
        if isinstance(content_details, dict):
            for key, value in content_details.items():
                if key not in flat:
                    flat[key] = value

    return flat


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
            query = parse_qs(parsed.query)
            values = query.get("v", [])
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


def _session_key(channel_id: str | None, video_id: str | None, raw_payload: dict[str, Any]) -> str:
    channel = _safe_segment(channel_id, "unknown_channel")
    if video_id:
        video = _safe_segment(video_id, "unknown_video")
    else:
        seed = str(raw_payload).encode("utf-8", errors="replace")
        video = hashlib.sha256(seed).hexdigest()[:12]
    return f"yt_{channel}_{video}"


def _is_youtube_event(trigger_slug: str | None, toolkit_slug: str | None, event_payload: dict[str, Any]) -> bool:
    slug = (trigger_slug or "").lower()
    toolkit = (toolkit_slug or "").lower()
    if "youtube" in slug or "youtube" in toolkit:
        return True

    event_type = str(event_payload.get("event_type") or "").strip().lower()
    if event_type in {"new_playlist_item", "youtube_new_playlist_item_trigger"}:
        return True

    key_hints = {
        "video_id",
        "video_url",
        "youtube_video_id",
        "youtube_video_url",
        "channel_id",
        "youtube_channel_id",
    }
    return any(key in event_payload for key in key_hints)


def _looks_like_youtube_video_id(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate))


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

    body_payload: dict[str, Any] | None = None
    raw_body_payload = payload.get("body")
    if isinstance(raw_body_payload, dict):
        body_payload = raw_body_payload
    elif isinstance(raw_body_payload, str):
        try:
            parsed = json.loads(raw_body_payload)
            if isinstance(parsed, dict):
                body_payload = parsed
        except Exception:
            body_payload = None

    event_type = _first_non_empty(
        payload.get("type"),
        payload.get("event_type"),
        body_payload.get("type") if isinstance(body_payload, dict) else None,
        body_payload.get("event_type") if isinstance(body_payload, dict) else None,
    )
    if event_type and event_type.strip().lower() not in {
        "composio.trigger.message",
        "new_playlist_item",
        "youtube_new_playlist_item_trigger",
    }:
        return None

    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}
    if not data and isinstance(body_payload, dict):
        body_data = body_payload.get("data")
        if isinstance(body_data, dict):
            data = body_data

    event_payload: dict[str, Any] | Any = data.get("data")
    if not isinstance(event_payload, dict) or not event_payload:
        candidate = data.get("payload")
        if isinstance(candidate, dict) and candidate:
            event_payload = candidate
    if not isinstance(event_payload, dict) or not event_payload:
        if isinstance(body_payload, dict):
            body_candidate = body_payload.get("data")
            if isinstance(body_candidate, dict) and body_candidate:
                event_payload = body_candidate
            else:
                event_payload = dict(body_payload)
    if not isinstance(event_payload, dict) or not event_payload:
        # Fallback for webhook payloads where data itself is already the event object.
        if isinstance(data, dict) and data:
            event_payload = dict(data)
        else:
            # Final fallback for payloads with no "data" envelope.
            event_payload = dict(payload)
    event_payload = _flatten_event_payload(event_payload)

    trigger_slug = _first_non_empty(
        data.get("trigger_slug"),
        data.get("trigger_type"),
        data.get("slug"),
        payload.get("trigger_slug"),
        payload.get("trigger_type"),
        payload.get("trigger_name"),
        payload.get("slug"),
    )
    toolkit_slug = _first_non_empty(
        data.get("toolkit_slug"),
        data.get("app"),
        data.get("provider"),
        payload.get("toolkit_slug"),
        payload.get("app"),
        payload.get("provider"),
    )

    if not _is_youtube_event(trigger_slug, toolkit_slug, event_payload):
        return None

    explicit_video_id = _pick(
        event_payload,
        ["video_id", "youtube_video_id", "videoId", "resourceId"],
    )
    fallback_item_id = _pick(event_payload, ["id"])
    video_id = explicit_video_id or fallback_item_id
    if video_id and not _looks_like_youtube_video_id(video_id):
        if explicit_video_id is None:
            video_id = None

    video_url = _pick(
        event_payload,
        ["video_url", "youtube_video_url", "url", "link", "videoLink"],
    )

    inferred_video_id = _extract_video_id(video_url)
    if not video_id:
        video_id = inferred_video_id
    if not video_url and video_id:
        video_url = f"https://www.youtube.com/watch?v={video_id}"

    if not video_url:
        # No actionable video target.
        return None

    channel_id = _pick(
        event_payload,
        ["channel_id", "youtube_channel_id", "channelId", "channel_id_str", "channelId"],
    )
    channel_name = _pick(
        event_payload,
        ["channel_name", "channel_title", "author", "uploader", "channelTitle"],
    )
    title = _pick(event_payload, ["title", "video_title", "name"])
    published_at = _pick(event_payload, ["published_at", "publishedAt", "timestamp"])

    raw_mode = _first_non_empty(
        event_payload.get("mode"),
        payload.get("mode"),
        MODE_EXPLAINER_PLUS_CODE,
    )
    mode = _normalize_mode(raw_mode)
    learning_mode = _learning_mode_from_mode(mode)
    degraded_raw = event_payload.get("allow_degraded_transcript_only")
    if degraded_raw is None:
        degraded_raw = payload.get("allow_degraded_transcript_only", True)
    allow_degraded = _coerce_bool(degraded_raw, default=True)
    artifacts_root = _resolve_artifacts_root_hint()

    session_key = _session_key(channel_id, video_id, payload)
    name = "ComposioYouTubeTrigger"

    message_lines = [
        "YouTube trigger received via Composio.",
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
        f"video_id: {video_id or ''}",
        f"channel_id: {channel_id or ''}",
        f"channel_name: {channel_name or ''}",
        f"title: {title or ''}",
        f"published_at: {published_at or ''}",
        f"trigger_slug: {trigger_slug or ''}",
        f"event_type: {event_type or ''}",
        f"mode: {mode}",
        f"learning_mode: {learning_mode}",
        f"allow_degraded_transcript_only: {str(allow_degraded).lower()}",
        "If learning_mode is concept_plus_implementation, include runnable code in implementation/ and explain how to run it.",
        "Use visual analysis when available, but proceed transcript-only when visual extraction is not feasible.",
    ]

    return {
        "kind": "agent",
        "name": name,
        "session_key": session_key,
        "to": YOUTUBE_LEARNING_SUBAGENT,
        "message": "\n".join(message_lines),
    }

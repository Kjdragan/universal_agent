"""Telegram notification sink for the YouTube tutorial pipeline.

Sends structured messages to a dedicated Telegram chat at each stage of
the tutorial processing lifecycle.

Per-video Telegram behaviour:
  - A single Telegram post is maintained per video when possible.
  - The post is created when the run is admitted (`youtube_tutorial_progress`)
    and then replaced by terminal outcome (`ready` / `failed` / `interrupted`).
  - Detection-only and retryable dispatch-not-admitted events are intentionally
    suppressed from Telegram to avoid notification floods.

System-health alerts (youtube_ingest_proxy_alert, hook_dispatch_queue_overflow)
are global notices — NOT per-video.  They are rate-limited to at most one
Telegram message per kind per HEALTH_ALERT_COOLDOWN_SECONDS (default 1 hour)
to avoid notification floods when many videos process during an outage.

Env vars:
    TELEGRAM_BOT_TOKEN              — bot token (shared with other notifiers)
    YOUTUBE_TUTORIAL_TELEGRAM_CHAT_ID — target chat / channel ID
                                       If unset, all sends are silently skipped.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

import json

from universal_agent.ops_config import resolve_ops_config_path

logger = logging.getLogger(__name__)

# ---- event kind classification ------------------------------------------

_RELEVANT_KINDS = {
    "youtube_playlist_new_video",
    "youtube_playlist_dispatch_failed",
    "youtube_tutorial_started",
    "youtube_tutorial_progress",
    "youtube_tutorial_interrupted",
    "youtube_tutorial_ready",
    "youtube_tutorial_failed",
    "youtube_ingest_failed",
    "hook_dispatch_queue_overflow",
    "youtube_hook_recovery_queued",
    "youtube_ingest_proxy_alert",
}

# These intermediate lifecycle events are suppressed — the user receives:
#   msg 1: youtube_playlist_new_video (video detected + pipeline kicked off)
#   msg 2: ready / failed / interrupted (final outcome)
_SUPPRESSED_KINDS = {
    "youtube_playlist_new_video",
    "youtube_playlist_dispatch_failed",
    "youtube_tutorial_started",
    "youtube_hook_recovery_queued",
}

# Global system-health alert kinds: not tied to a specific video.
# Rate-limited to one Telegram message per kind per cooldown window.
_HEALTH_ALERT_KINDS = {
    "youtube_ingest_proxy_alert",
    "hook_dispatch_queue_overflow",
}
_VIDEO_LIFECYCLE_KINDS = {
    "youtube_tutorial_progress",
    "youtube_tutorial_interrupted",
    "youtube_tutorial_ready",
    "youtube_tutorial_failed",
}
_VIDEO_KIND_PRIORITY: dict[str, int] = {
    "youtube_tutorial_progress": 10,
    "youtube_tutorial_interrupted": 30,
    "youtube_tutorial_failed": 40,
    "youtube_tutorial_ready": 50,
}
_VIDEO_MESSAGE_STATE_FILENAME = "tutorial_telegram_state.json"
_VIDEO_MESSAGE_STATE_VERSION = 1

# 1-hour cooldown for health alerts (seconds)
HEALTH_ALERT_COOLDOWN_SECONDS: float = float(
    os.getenv("UA_TUTORIAL_HEALTH_ALERT_COOLDOWN_SECONDS", "3600")
)

# In-process state: last Telegram send timestamp per kind for health alerts.
# Resets on restart which is intentional — a restart is a meaningful event.
_health_alert_last_sent: dict[str, float] = {}

# Per-video dedup: prevents duplicate Telegram messages for the same video
# within a cooldown window.  Keyed by (kind, video_id) → monotonic timestamp.
# Old entries are evicted on each maybe_send() call to prevent unbounded growth.
VIDEO_READY_DEDUP_SECONDS: float = float(
    os.getenv("UA_TUTORIAL_VIDEO_READY_DEDUP_SECONDS", "1800")
)
VIDEO_NEW_DEDUP_SECONDS: float = float(
    os.getenv("UA_TUTORIAL_VIDEO_NEW_DEDUP_SECONDS", "1800")
)
VIDEO_FAILED_DEDUP_SECONDS: float = float(
    os.getenv("UA_TUTORIAL_VIDEO_FAILED_DEDUP_SECONDS", "1800")
)

# Shared state for all per-video dedup — one dict per kind.
_video_ready_last_sent: dict[str, float] = {}
_video_new_last_sent: dict[str, float] = {}
_video_failed_last_sent: dict[str, float] = {}
_video_message_state_cache: dict[str, dict[str, Any]] | None = None

# Config table: kind → (dedup dict, cooldown attr name)
# We store the *attribute name* rather than the float value so that
# monkeypatch / runtime changes to the module-level cooldown variables
# are reflected immediately (the value is read via getattr at call time).
_PER_VIDEO_DEDUP_CONFIG: dict[str, tuple[dict[str, float], str]] = {}

# Cache a reference to this module for dynamic lookups.
import sys as _sys
_THIS_MODULE = _sys.modules[__name__]


def _init_per_video_dedup_config() -> None:
    """Populate the dedup config table.  Called once at module load."""
    _PER_VIDEO_DEDUP_CONFIG.update({
        "youtube_tutorial_ready": (_video_ready_last_sent, "VIDEO_READY_DEDUP_SECONDS"),
        "youtube_playlist_new_video": (_video_new_last_sent, "VIDEO_NEW_DEDUP_SECONDS"),
        "youtube_tutorial_failed": (_video_failed_last_sent, "VIDEO_FAILED_DEDUP_SECONDS"),
    })


_init_per_video_dedup_config()


def _get_ttl(attr_name: str) -> float:
    """Read the current cooldown value from the module attribute."""
    return float(getattr(_THIS_MODULE, attr_name, 1800.0))


def _evict_stale_dedup_entries() -> None:
    """Remove entries older than their cooldown window to prevent unbounded growth."""
    now = time.monotonic()
    for dedup_dict, ttl_attr in _PER_VIDEO_DEDUP_CONFIG.values():
        ttl = _get_ttl(ttl_attr)
        stale = [vid for vid, ts in dedup_dict.items() if (now - ts) >= ttl]
        for vid in stale:
            dedup_dict.pop(vid, None)


def _check_per_video_dedup(kind: str, video_id: str) -> bool:
    """Return True if this (kind, video_id) should be suppressed.

    Also records a successful check for future dedup.
    """
    config = _PER_VIDEO_DEDUP_CONFIG.get(kind)
    if config is None:
        return False
    dedup_dict, ttl_attr = config
    if not video_id:
        return False
    ttl = _get_ttl(ttl_attr)
    now_mono = time.monotonic()
    last = dedup_dict.get(video_id)
    if last is None:
        return False  # Never sent before — allow through.
    remaining = ttl - (now_mono - last)
    if remaining > 0:
        logger.debug(
            "tutorial_telegram_notifier: suppressed duplicate %s "
            "video_id=%s cooldown_remaining=%.0fs",
            kind, video_id, remaining,
        )
        return True
    return False


def _record_per_video_send(kind: str, video_id: str) -> None:
    """Record a successful send for per-video dedup."""
    config = _PER_VIDEO_DEDUP_CONFIG.get(kind)
    if config is None or not video_id:
        return
    dedup_dict, _ = config
    dedup_dict[video_id] = time.monotonic()

_KIND_EMOJI: dict[str, str] = {
    "youtube_playlist_new_video": "🎬",
    "youtube_playlist_dispatch_failed": "⚠️",
    "youtube_tutorial_started": "▶️",
    "youtube_tutorial_progress": "⏳",
    "youtube_tutorial_interrupted": "⚠️",
    "youtube_tutorial_ready": "✅",
    "youtube_tutorial_failed": "❌",
    "youtube_ingest_failed": "❌",
    "hook_dispatch_queue_overflow": "⚠️",
    "youtube_hook_recovery_queued": "🔁",
}


def _bot_token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def _chat_id() -> str:
    return os.getenv("YOUTUBE_TUTORIAL_TELEGRAM_CHAT_ID", "").strip()


def _is_configured() -> bool:
    return bool(_bot_token() and _chat_id())


def _build_message(kind: str, title: str, message: str, metadata: dict[str, Any]) -> str:
    emoji = _KIND_EMOJI.get(kind, "📌")
    lines = [f"{emoji} *{_escape(title)}*", _escape(message)]

    if kind == "youtube_playlist_new_video":
        url = str(metadata.get("video_url") or "")
        if url:
            lines.append(f"[Watch]({url})")

    elif kind == "youtube_tutorial_started":
        video_id = str(metadata.get("video_id") or "")
        if video_id:
            lines.append(f"`{video_id}`")

    elif kind == "youtube_tutorial_ready":
        video_id = str(metadata.get("video_id") or "")
        status = str(metadata.get("tutorial_status") or "full")
        run_path = str(metadata.get("tutorial_run_path") or "")
        if video_id:
            lines.append(f"Video ID: `{video_id}`")
        lines.append(f"Status: `{status}`")
        recovered_after_retry = bool(metadata.get("recovered_after_retry"))
        attempt_number = int(metadata.get("attempt_number") or 0)
        total_attempts_allowed = int(metadata.get("total_attempts_allowed") or 0)
        if recovered_after_retry and attempt_number > 0:
            if total_attempts_allowed > 0:
                lines.append(
                    f"Recovery: automatic retry attempt `{attempt_number}/{total_attempts_allowed}` succeeded"
                )
            else:
                lines.append(f"Recovery: automatic retry attempt `{attempt_number}` succeeded")
        elif bool(metadata.get("dispatch_issue_resolved")):
            lines.append("Recovery: output package validated after an earlier dispatch hiccup")
        if run_path:
            lines.append(f"Path: `{run_path}`")
        key_files = metadata.get("tutorial_key_files")
        if isinstance(key_files, list) and key_files:
            file_labels = [
                str(f.get("label") or f.get("name") or f.get("rel_path") or "")
                for f in key_files
                if isinstance(f, dict)
            ]
            visible = [x for x in file_labels if x][:5]
            if visible:
                lines.append("Files: " + ", ".join(f"`{x}`" for x in visible))

    elif kind == "youtube_tutorial_failed":
        video_id = str(metadata.get("video_id") or "")
        reason = str(metadata.get("error") or metadata.get("reason") or "")
        if video_id:
            lines.append(f"Video ID: `{video_id}`")
        if reason:
            lines.append(f"Reason: `{_escape(reason[:120])}`")

    elif kind == "youtube_tutorial_interrupted":
        video_id = str(metadata.get("video_id") or "")
        reason = str(metadata.get("reason") or "")
        if video_id:
            lines.append(f"Video ID: `{video_id}`")
        if reason:
            lines.append(f"Reason: `{_escape(reason[:120])}`")

    elif kind == "youtube_ingest_failed":
        video_key = str(metadata.get("video_key") or "")
        failure_class = str(metadata.get("failure_class") or "")
        if video_key:
            lines.append(f"Video: `{video_key}`")
        if failure_class:
            lines.append(f"Class: `{failure_class}`")

    elif kind == "hook_dispatch_queue_overflow":
        pending = metadata.get("pending")
        limit = metadata.get("limit")
        if pending and limit:
            lines.append(f"Queue: {pending}/{limit}")

    return "\n".join(lines)


def _escape(text: str) -> str:
    for ch in ["*", "_", "`", "["]:
        text = text.replace(ch, f"\\{ch}")
    return text


def _state_path() -> Path:
    ops_dir = Path(
        os.getenv("UA_OPS_DIR", "")
        or os.getenv("UA_OPS_CONFIG_PATH", str(resolve_ops_config_path()))
    )
    if ops_dir.suffix:
        ops_dir = ops_dir.parent
    ops_dir.mkdir(parents=True, exist_ok=True)
    return ops_dir / _VIDEO_MESSAGE_STATE_FILENAME


def _load_video_message_state() -> dict[str, dict[str, Any]]:
    global _video_message_state_cache
    if _video_message_state_cache is not None:
        return _video_message_state_cache
    path = _state_path()
    if not path.exists():
        _video_message_state_cache = {}
        return _video_message_state_cache
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _video_message_state_cache = {}
        return _video_message_state_cache
    raw_videos = payload.get("videos") if isinstance(payload, dict) else {}
    videos: dict[str, dict[str, Any]] = {}
    if isinstance(raw_videos, dict):
        for video_id, state in raw_videos.items():
            clean_video_id = str(video_id or "").strip()
            if not clean_video_id or not isinstance(state, dict):
                continue
            videos[clean_video_id] = {
                "message_id": state.get("message_id"),
                "kind": str(state.get("kind") or "").strip(),
                "text": str(state.get("text") or ""),
                "updated_at": str(state.get("updated_at") or ""),
            }
    _video_message_state_cache = videos
    return _video_message_state_cache


def _save_video_message_state(state: dict[str, dict[str, Any]]) -> None:
    global _video_message_state_cache
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _VIDEO_MESSAGE_STATE_VERSION,
        "videos": state,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    _video_message_state_cache = state


def _video_id_from_metadata(metadata: dict[str, Any]) -> str:
    return str(metadata.get("video_id") or "").strip()


def _kind_priority(kind: str) -> int:
    return int(_VIDEO_KIND_PRIORITY.get(kind, 0))


def _should_suppress_video_update(
    existing_kind: str,
    incoming_kind: str,
    *,
    existing_text: str,
    incoming_text: str,
) -> bool:
    if existing_kind == incoming_kind and existing_text == incoming_text:
        return True
    return _kind_priority(existing_kind) > _kind_priority(incoming_kind)


def _send_with_message_id(text: str) -> tuple[bool, int | None]:
    token = _bot_token()
    chat = _chat_id()
    if not token or not chat:
        return False, None
    from universal_agent.services.telegram_send import telegram_send_with_response_sync

    ok, payload, _err = telegram_send_with_response_sync(
        chat_id=chat,
        text=text,
        bot_token=token,
        parse_mode="Markdown",
        disable_preview=True,
    )
    if not ok:
        return False, None
    result = payload.get("result") if isinstance(payload, dict) else None
    try:
        message_id = int((result or {}).get("message_id"))
    except Exception:
        message_id = None
    return True, message_id


def _edit_message(message_id: int | str, text: str) -> bool:
    token = _bot_token()
    chat = _chat_id()
    if not token or not chat:
        return False
    from universal_agent.services.telegram_send import telegram_edit_sync

    ok, err = telegram_edit_sync(
        chat_id=chat,
        message_id=message_id,
        text=text,
        bot_token=token,
        parse_mode="Markdown",
        disable_preview=True,
    )
    if ok:
        return True
    detail = str(err or "").strip().lower()
    return "message is not modified" in detail


def _upsert_video_lifecycle_message(
    kind: str,
    text: str,
    metadata: dict[str, Any],
) -> bool:
    video_id = _video_id_from_metadata(metadata)
    if not video_id:
        sent, _message_id = _send_with_message_id(text)
        return sent
    state = _load_video_message_state()
    existing = state.get(video_id) or {}
    existing_kind = str(existing.get("kind") or "").strip()
    existing_text = str(existing.get("text") or "")
    if _should_suppress_video_update(
        existing_kind,
        kind,
        existing_text=existing_text,
        incoming_text=text,
    ):
        logger.debug(
            "tutorial_telegram_notifier: suppressed stale lifecycle update kind=%s existing_kind=%s video_id=%s",
            kind,
            existing_kind,
            video_id,
        )
        return False

    existing_message_id = existing.get("message_id")
    if existing_message_id not in {None, ""} and _edit_message(existing_message_id, text):
        state[video_id] = {
            "message_id": existing_message_id,
            "kind": kind,
            "text": text,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _save_video_message_state(state)
        return True

    sent, message_id = _send_with_message_id(text)
    if not sent:
        return False
    state[video_id] = {
        "message_id": message_id,
        "kind": kind,
        "text": text,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _save_video_message_state(state)
    return True


def _send(text: str) -> bool:
    token = _bot_token()
    chat = _chat_id()
    if not token or not chat:
        return False
    from universal_agent.services.telegram_send import telegram_send_sync

    ok, _err = telegram_send_sync(
        chat_id=chat,
        text=text,
        bot_token=token,
        parse_mode="Markdown",
        disable_preview=True,
    )
    return ok


def maybe_send(payload: dict[str, Any]) -> bool:
    """Send a Telegram notification if this payload is tutorial-relevant.

    Rules (in order):
    1. Payload must be a dict with a `kind` in _RELEVANT_KINDS.
    2. Telegram must be configured (bot token + chat id).
    3. Suppressed intermediate lifecycle events are silently dropped.
    4. System-health alert kinds are rate-limited (one per cooldown window).
    5. All other events are forwarded immediately.

    Returns True if a message was actually sent.
    """
    if not isinstance(payload, dict):
        return False
    kind = str(payload.get("kind") or "").strip()
    if kind not in _RELEVANT_KINDS:
        return False
    if not _is_configured():
        return False

    # --- suppressed intermediate events ---
    if kind in _SUPPRESSED_KINDS:
        logger.debug("tutorial_telegram_notifier: suppressed intermediate kind=%s", kind)
        return False

    # --- health alert rate-limiting (per-kind cooldown) ---
    if kind in _HEALTH_ALERT_KINDS:
        now = time.monotonic()
        last = _health_alert_last_sent.get(kind)
        if last is not None:
            remaining = HEALTH_ALERT_COOLDOWN_SECONDS - (now - last)
            if remaining > 0:
                logger.debug(
                    "tutorial_telegram_notifier: health alert throttled kind=%s cooldown_remaining=%.0fs",
                    kind,
                    remaining,
                )
                return False
        _health_alert_last_sent[kind] = now

    # --- per-video dedup (unified) ---
    _evict_stale_dedup_entries()
    video_id_for_dedup = str((payload.get("metadata") or {}).get("video_id") or "").strip()
    if _check_per_video_dedup(kind, video_id_for_dedup):
        return False

    title = str(payload.get("title") or "Tutorial Event")
    message = str(payload.get("message") or "")
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    try:
        text = _build_message(kind, title, message, metadata)
        if kind in _VIDEO_LIFECYCLE_KINDS:
            sent = _upsert_video_lifecycle_message(kind, text, metadata)
        else:
            sent = _send(text)
        # Record successful send for per-video dedup
        if sent:
            video_id = _video_id_from_metadata(metadata)
            _record_per_video_send(kind, video_id)
        return sent
    except Exception:
        logger.exception("tutorial_telegram_notifier.maybe_send failed kind=%s", kind)
        return False


def configured_status() -> dict[str, Any]:
    """Return configuration status dict for ops/health endpoints."""
    return {
        "bot_token_set": bool(_bot_token()),
        "chat_id_set": bool(_chat_id()),
        "configured": _is_configured(),
    }

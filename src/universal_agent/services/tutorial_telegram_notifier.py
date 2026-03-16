"""Telegram notification sink for the YouTube tutorial pipeline.

Sends structured messages to a dedicated Telegram chat at each stage of
the tutorial processing lifecycle.

Per-video Telegram behaviour:
  - 2 messages per video are sent:
      1. youtube_playlist_new_video   (video detected, pipeline kicked off)
      2. youtube_tutorial_ready | youtube_tutorial_failed | youtube_tutorial_interrupted
  - youtube_tutorial_started and youtube_tutorial_progress are suppressed
    (redundant given the detection + outcome pair).

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
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://api.telegram.org/bot{token}/sendMessage"

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
    "youtube_tutorial_started",
    "youtube_tutorial_progress",
}

# Global system-health alert kinds: not tied to a specific video.
# Rate-limited to one Telegram message per kind per cooldown window.
_HEALTH_ALERT_KINDS = {
    "youtube_ingest_proxy_alert",
    "hook_dispatch_queue_overflow",
}

# 1-hour cooldown for health alerts (seconds)
HEALTH_ALERT_COOLDOWN_SECONDS: float = float(
    os.getenv("UA_TUTORIAL_HEALTH_ALERT_COOLDOWN_SECONDS", "3600")
)

# In-process state: last Telegram send timestamp per kind for health alerts.
# Resets on restart which is intentional — a restart is a meaningful event.
_health_alert_last_sent: dict[str, float] = {}

# Per-video dedup for youtube_tutorial_ready: prevents the same video from
# generating multiple "artifacts ready" Telegram messages within a window.
# Key = video_id, value = monotonic timestamp of last send.
VIDEO_READY_DEDUP_SECONDS: float = float(
    os.getenv("UA_TUTORIAL_VIDEO_READY_DEDUP_SECONDS", "1800")
)
_video_ready_last_sent: dict[str, float] = {}

# Per-video dedup for youtube_playlist_new_video: prevents the same video
# from generating multiple "new video detected" Telegram messages within a
# window (common when multiple UA instances watch the same playlist).
VIDEO_NEW_DEDUP_SECONDS: float = float(
    os.getenv("UA_TUTORIAL_VIDEO_NEW_DEDUP_SECONDS", "1800")
)
_video_new_last_sent: dict[str, float] = {}

# Per-video dedup for youtube_tutorial_failed: prevents duplicate failure
# messages for the same video within a window.
VIDEO_FAILED_DEDUP_SECONDS: float = float(
    os.getenv("UA_TUTORIAL_VIDEO_FAILED_DEDUP_SECONDS", "1800")
)
_video_failed_last_sent: dict[str, float] = {}

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
        last = _health_alert_last_sent.get(kind, 0.0)
        remaining = HEALTH_ALERT_COOLDOWN_SECONDS - (now - last)
        if remaining > 0:
            logger.debug(
                "tutorial_telegram_notifier: health alert throttled kind=%s cooldown_remaining=%.0fs",
                kind,
                remaining,
            )
            return False
        _health_alert_last_sent[kind] = now

    # --- per-video dedup for youtube_playlist_new_video ---
    if kind == "youtube_playlist_new_video":
        video_id = str((payload.get("metadata") or {}).get("video_id") or "").strip()
        if video_id:
            now_mono = time.monotonic()
            last = _video_new_last_sent.get(video_id, 0.0)
            remaining = VIDEO_NEW_DEDUP_SECONDS - (now_mono - last)
            if remaining > 0:
                logger.debug(
                    "tutorial_telegram_notifier: suppressed duplicate "
                    "youtube_playlist_new_video video_id=%s cooldown_remaining=%.0fs",
                    video_id,
                    remaining,
                )
                return False

    # --- per-video dedup for youtube_tutorial_ready ---
    if kind == "youtube_tutorial_ready":
        video_id = str((payload.get("metadata") or {}).get("video_id") or "").strip()
        if video_id:
            now_mono = time.monotonic()
            last = _video_ready_last_sent.get(video_id, 0.0)
            remaining = VIDEO_READY_DEDUP_SECONDS - (now_mono - last)
            if remaining > 0:
                logger.debug(
                    "tutorial_telegram_notifier: suppressed duplicate "
                    "youtube_tutorial_ready video_id=%s cooldown_remaining=%.0fs",
                    video_id,
                    remaining,
                )
                return False

    # --- per-video dedup for youtube_tutorial_failed ---
    if kind == "youtube_tutorial_failed":
        video_id = str((payload.get("metadata") or {}).get("video_id") or "").strip()
        if video_id:
            now_mono = time.monotonic()
            last = _video_failed_last_sent.get(video_id, 0.0)
            remaining = VIDEO_FAILED_DEDUP_SECONDS - (now_mono - last)
            if remaining > 0:
                logger.debug(
                    "tutorial_telegram_notifier: suppressed duplicate "
                    "youtube_tutorial_failed video_id=%s cooldown_remaining=%.0fs",
                    video_id,
                    remaining,
                )
                return False

    title = str(payload.get("title") or "Tutorial Event")
    message = str(payload.get("message") or "")
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    try:
        text = _build_message(kind, title, message, metadata)
        sent = _send(text)
        # Record successful send for per-video dedup
        if sent:
            video_id = str(metadata.get("video_id") or "").strip()
            if video_id:
                if kind == "youtube_tutorial_ready":
                    _video_ready_last_sent[video_id] = time.monotonic()
                elif kind == "youtube_playlist_new_video":
                    _video_new_last_sent[video_id] = time.monotonic()
                elif kind == "youtube_tutorial_failed":
                    _video_failed_last_sent[video_id] = time.monotonic()
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

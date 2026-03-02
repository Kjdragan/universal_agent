"""Telegram notification sink for the YouTube tutorial pipeline.

Sends structured messages to a dedicated Telegram chat at each stage of
the tutorial processing lifecycle.

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

_RELEVANT_KINDS = {
    "youtube_playlist_new_video",
    "youtube_playlist_dispatch_failed",
    "youtube_tutorial_started",
    "youtube_tutorial_progress",
    "youtube_tutorial_ready",
    "youtube_tutorial_failed",
    "youtube_ingest_failed",
    "hook_dispatch_queue_overflow",
    "youtube_hook_recovery_queued",
}

_KIND_EMOJI: dict[str, str] = {
    "youtube_playlist_new_video": "🎬",
    "youtube_playlist_dispatch_failed": "⚠️",
    "youtube_tutorial_started": "▶️",
    "youtube_tutorial_progress": "⏳",
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
        status = str(metadata.get("tutorial_status") or "full")
        run_path = str(metadata.get("tutorial_run_path") or "")
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
        reason = str(metadata.get("error") or metadata.get("reason") or "")
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


_SEND_MAX_RETRIES = 3
_SEND_RETRY_BACKOFF = (1.0, 3.0, 5.0)


def _send(text: str) -> bool:
    token = _bot_token()
    chat = _chat_id()
    if not token or not chat:
        return False
    url = _BASE.format(token=token)
    last_exc: Optional[Exception] = None
    for attempt in range(_SEND_MAX_RETRIES):
        try:
            resp = httpx.post(
                url,
                json={
                    "chat_id": chat,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=10,
            )
            if resp.is_success:
                return True
            # 4xx errors are not retryable (bad request, chat not found, etc.)
            if 400 <= resp.status_code < 500:
                logger.warning(
                    "Telegram tutorial notify failed status=%d body=%s",
                    resp.status_code,
                    resp.text[:200],
                )
                return False
            # 5xx — retry
            logger.warning(
                "Telegram tutorial notify server error status=%d attempt=%d/%d",
                resp.status_code,
                attempt + 1,
                _SEND_MAX_RETRIES,
            )
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Telegram tutorial notify error attempt=%d/%d: %s",
                attempt + 1,
                _SEND_MAX_RETRIES,
                exc,
            )
        if attempt < _SEND_MAX_RETRIES - 1:
            time.sleep(_SEND_RETRY_BACKOFF[min(attempt, len(_SEND_RETRY_BACKOFF) - 1)])
    logger.warning("Telegram tutorial notify exhausted %d retries (last: %s)", _SEND_MAX_RETRIES, last_exc)
    return False


def maybe_send(payload: dict[str, Any]) -> bool:
    """Send a Telegram notification if this payload is tutorial-relevant.

    Silently returns False when Telegram is not configured or kind is not
    in the tutorial notification set — never raises.
    """
    if not isinstance(payload, dict):
        return False
    kind = str(payload.get("kind") or "").strip()
    if kind not in _RELEVANT_KINDS:
        return False
    if not _is_configured():
        return False
    title = str(payload.get("title") or "Tutorial Event")
    message = str(payload.get("message") or "")
    metadata = payload.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    try:
        text = _build_message(kind, title, message, metadata)
        return _send(text)
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

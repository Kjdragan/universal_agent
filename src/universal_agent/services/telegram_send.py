"""Shared Telegram send utility.

Provides both async and sync variants with unified retry policy, rate-limit
awareness, and structured logging.  All Telegram message sending in the UA
codebase should use this module instead of ad-hoc implementations.

Usage (async — preferred for gateway/bot context)::

    from universal_agent.services.telegram_send import telegram_send_async
    await telegram_send_async(chat_id=12345, text="Hello")

Usage (sync — for scripts, systemd timers, CSI digest jobs)::

    from universal_agent.services.telegram_send import telegram_send_sync
    ok, err = telegram_send_sync(chat_id=12345, text="Hello")

Env vars:
    TELEGRAM_BOT_TOKEN — default bot token (can be overridden per call)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/{method}"

_DEFAULT_RETRIES = 3
_DEFAULT_BASE_DELAY = 1.0
_DEFAULT_TIMEOUT = 15.0


def _resolve_token(bot_token: Optional[str] = None) -> str:
    token = (bot_token or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise ValueError(
            "No Telegram bot token provided. Set TELEGRAM_BOT_TOKEN or pass bot_token=."
        )
    return token


def _api_url(token: str, method: str) -> str:
    return _API_BASE.format(token=token, method=method)


def _build_payload(
    chat_id: int | str,
    text: str,
    *,
    parse_mode: Optional[str] = None,
    thread_id: Optional[int] = None,
    disable_preview: bool = True,
) -> dict:
    body: dict = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_preview,
    }
    if parse_mode:
        body["parse_mode"] = parse_mode
    if thread_id is not None:
        body["message_thread_id"] = thread_id
    return body


def _telegram_request_sync(
    *,
    method: str,
    body: dict[str, Any],
    bot_token: Optional[str] = None,
    retries: int = _DEFAULT_RETRIES,
    base_delay: float = _DEFAULT_BASE_DELAY,
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[bool, Optional[dict[str, Any]], str]:
    token = _resolve_token(bot_token)
    url = _api_url(token, method)

    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            resp = httpx.post(url, json=body, timeout=timeout)
            if resp.is_success:
                try:
                    payload = resp.json()
                except Exception:
                    payload = None
                if isinstance(payload, dict):
                    return True, payload, "ok"
                return True, None, "ok"
            if 400 <= resp.status_code < 500:
                last_error = f"telegram_http_{resp.status_code} body={resp.text[:300]}"
                logger.warning(
                    "telegram_%s_sync failed chat_id=%s status=%d (not retryable)",
                    method,
                    body.get("chat_id"),
                    resp.status_code,
                )
                return False, None, last_error
            last_error = f"telegram_http_{resp.status_code}"
            logger.warning(
                "telegram_%s_sync server error chat_id=%s status=%d attempt=%d/%d",
                method,
                body.get("chat_id"),
                resp.status_code,
                attempt,
                retries,
            )
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "telegram_%s_sync error chat_id=%s attempt=%d/%d: %s",
                method,
                body.get("chat_id"),
                attempt,
                retries,
                exc,
            )

        if attempt < retries:
            time.sleep(base_delay * attempt)

    logger.error(
        "telegram_%s_sync exhausted chat_id=%s retries=%d last_error=%s",
        method,
        body.get("chat_id"),
        retries,
        last_error,
    )
    return False, None, last_error


# ---------------------------------------------------------------------------
# Sync variant (for scripts, timers, CSI digest jobs)
# ---------------------------------------------------------------------------

def telegram_send_sync(
    chat_id: int | str,
    text: str,
    *,
    bot_token: Optional[str] = None,
    parse_mode: Optional[str] = None,
    thread_id: Optional[int] = None,
    disable_preview: bool = True,
    retries: int = _DEFAULT_RETRIES,
    base_delay: float = _DEFAULT_BASE_DELAY,
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[bool, str]:
    """Send a Telegram message synchronously with retry.

    Returns:
        (True, "ok") on success.
        (False, error_description) on failure.
    """
    ok, _payload, err = telegram_send_with_response_sync(
        chat_id, text,
        bot_token=bot_token,
        parse_mode=parse_mode,
        thread_id=thread_id,
        disable_preview=disable_preview,
        retries=retries,
        base_delay=base_delay,
        timeout=timeout,
    )
    return ok, err


def telegram_send_with_response_sync(
    chat_id: int | str,
    text: str,
    *,
    bot_token: Optional[str] = None,
    parse_mode: Optional[str] = None,
    thread_id: Optional[int] = None,
    disable_preview: bool = True,
    retries: int = _DEFAULT_RETRIES,
    base_delay: float = _DEFAULT_BASE_DELAY,
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[bool, Optional[dict[str, Any]], str]:
    """Send a Telegram message synchronously and return the parsed API payload."""
    body = _build_payload(
        chat_id,
        text,
        parse_mode=parse_mode,
        thread_id=thread_id,
        disable_preview=disable_preview,
    )
    return _telegram_request_sync(
        method="sendMessage",
        body=body,
        bot_token=bot_token,
        retries=retries,
        base_delay=base_delay,
        timeout=timeout,
    )


def telegram_edit_sync(
    chat_id: int | str,
    message_id: int | str,
    text: str,
    *,
    bot_token: Optional[str] = None,
    parse_mode: Optional[str] = None,
    disable_preview: bool = True,
    retries: int = _DEFAULT_RETRIES,
    base_delay: float = _DEFAULT_BASE_DELAY,
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[bool, str]:
    """Edit a Telegram message synchronously with retry."""
    body: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": int(message_id),
        "text": text,
        "disable_web_page_preview": disable_preview,
    }
    if parse_mode:
        body["parse_mode"] = parse_mode
    ok, _payload, err = _telegram_request_sync(
        method="editMessageText",
        body=body,
        bot_token=bot_token,
        retries=retries,
        base_delay=base_delay,
        timeout=timeout,
    )
    return ok, err


# ---------------------------------------------------------------------------
# Async variant (for gateway, bot, services)
# ---------------------------------------------------------------------------

async def telegram_send_async(
    chat_id: int | str,
    text: str,
    *,
    bot_token: Optional[str] = None,
    parse_mode: Optional[str] = None,
    thread_id: Optional[int] = None,
    disable_preview: bool = True,
    retries: int = _DEFAULT_RETRIES,
    base_delay: float = _DEFAULT_BASE_DELAY,
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[bool, str]:
    """Send a Telegram message asynchronously with retry.

    Returns:
        (True, "ok") on success.
        (False, error_description) on failure.
    """
    token = _resolve_token(bot_token)
    url = _api_url(token, "sendMessage")
    body = _build_payload(
        chat_id, text,
        parse_mode=parse_mode,
        thread_id=thread_id,
        disable_preview=disable_preview,
    )

    last_error = ""
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(1, retries + 1):
            try:
                resp = await client.post(url, json=body)
                if resp.is_success:
                    return True, "ok"
                if 400 <= resp.status_code < 500:
                    last_error = f"telegram_http_{resp.status_code} body={resp.text[:300]}"
                    logger.warning(
                        "telegram_send_async failed chat_id=%s status=%d (not retryable)",
                        chat_id, resp.status_code,
                    )
                    return False, last_error
                last_error = f"telegram_http_{resp.status_code}"
                logger.warning(
                    "telegram_send_async server error chat_id=%s status=%d attempt=%d/%d",
                    chat_id, resp.status_code, attempt, retries,
                )
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "telegram_send_async error chat_id=%s attempt=%d/%d: %s",
                    chat_id, attempt, retries, exc,
                )

            if attempt < retries:
                await asyncio.sleep(base_delay * attempt)

    logger.error(
        "telegram_send_async exhausted chat_id=%s retries=%d last_error=%s",
        chat_id, retries, last_error,
    )
    return False, last_error

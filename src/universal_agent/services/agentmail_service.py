"""AgentMail service for Universal Agent.

Gives Simone her own email inbox via AgentMail. Provides:
- Inbox management (idempotent creation, address resolution)
- Send/reply (direct or draft-first for human approval)
- Inbound email listening via WebSocket (dispatched through hooks pipeline)
- Ops status endpoint data

Env vars:
    UA_AGENTMAIL_ENABLED           — master toggle (default 0)
    AGENTMAIL_API_KEY              — API key from agentmail.to
    UA_AGENTMAIL_INBOX_ADDRESS     — pre-existing inbox address (optional)
    UA_AGENTMAIL_INBOX_USERNAME    — username for new inbox (default: simone)
    UA_AGENTMAIL_AUTO_SEND         — 1 = send directly, 0 = create drafts (default)
    UA_AGENTMAIL_WS_ENABLED        — 1 = start WebSocket listener for inbound email
    UA_AGENTMAIL_WS_RECONNECT_BASE_DELAY — base backoff seconds (default 2)
    UA_AGENTMAIL_WS_RECONNECT_MAX_DELAY  — max backoff seconds (default 120)
    UA_AGENTMAIL_WS_FAIL_OPEN_STATUS_CODES — comma-delimited HTTP statuses that disable WS and rely on polling
    UA_AGENTMAIL_WS_FAIL_OPEN_AFTER_ATTEMPTS — reconnect attempts before fail-open (default 3)
"""

from __future__ import annotations

import asyncio
from collections import deque
from email.utils import parseaddr
import json
import logging
import os
import random
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional

from universal_agent.durable.db import connect_runtime_db, get_activity_db_path

logger = logging.getLogger(__name__)


def _strip_html_quotes(html_body: str) -> str:
    """Strip quoted reply blocks from HTML emails.

    Handles Gmail (div.gmail_quote), Outlook (OLK_SRC_BODY_SECTION, #divRplyFwdMsg),
    Apple Mail / Thunderbird (blockquote[type=cite]), and generic blockquotes.
    Returns plain text content with quotes removed.
    """
    if not html_body or not html_body.strip():
        return ""
    try:
        import re as _re
        # Remove Gmail quote divs
        clean = _re.sub(
            r'<div[^>]*class\s*=\s*["\']gmail_quote["\'][^>]*>.*$',
            '', html_body, flags=_re.DOTALL | _re.IGNORECASE,
        )
        # Remove Outlook quote sections
        clean = _re.sub(
            r'<div[^>]*id\s*=\s*["\']divRplyFwdMsg["\'][^>]*>.*$',
            '', clean, flags=_re.DOTALL | _re.IGNORECASE,
        )
        clean = _re.sub(
            r'<span[^>]*id\s*=\s*["\']OLK_SRC_BODY_SECTION["\'][^>]*>.*$',
            '', clean, flags=_re.DOTALL | _re.IGNORECASE,
        )
        # Remove Thunderbird forward containers
        clean = _re.sub(
            r'<div[^>]*class\s*=\s*["\']moz-forward-container["\'][^>]*>.*$',
            '', clean, flags=_re.DOTALL | _re.IGNORECASE,
        )
        # Remove blockquote[type=cite] (Apple Mail / generic)
        clean = _re.sub(
            r'<blockquote[^>]*type\s*=\s*["\']cite["\'][^>]*>.*?</blockquote>',
            '', clean, flags=_re.DOTALL | _re.IGNORECASE,
        )
        # Strip remaining HTML tags → plain text
        text = _re.sub(r'<br\s*/?>|<br>', '\n', clean, flags=_re.IGNORECASE)
        text = _re.sub(r'<[^>]+>', '', text)
        # Collapse whitespace
        text = _re.sub(r'\n{3,}', '\n\n', text).strip()
        import html as _html
        text = _html.unescape(text)
        return text
    except Exception:
        logger.debug("HTML quote stripping failed", exc_info=True)
        return ""


def _extract_reply_text(text_body: str, html_body: str = "") -> str:
    """Extract only the new reply content, stripping quoted thread history.

    Uses HTML-aware quote stripping for HTML emails (handles Gmail, Outlook,
    Thunderbird, Apple Mail patterns), then falls back to email-reply-parser
    for plain text. Returns the original text if extraction fails.
    """
    if not text_body and not html_body:
        return text_body or ""
    # Try HTML extraction first (more accurate for rich emails)
    if html_body and html_body.strip():
        html_result = _strip_html_quotes(html_body)
        if html_result and html_result.strip() and len(html_result.strip()) > 5:
            return html_result.strip()
    # Fallback to plain text extraction
    if not text_body or not text_body.strip():
        return text_body or ""
    try:
        from email_reply_parser import EmailReplyParser

        reply = EmailReplyParser.parse_reply(text_body)
        # If extraction returned nothing useful, fall back to full body
        if reply and reply.strip():
            return reply.strip()
        return text_body
    except Exception:
        logger.debug("Email reply extraction failed, using full body", exc_info=True)
        return text_body

# Type aliases matching the patterns in youtube_playlist_watcher.py
DispatchFn = Callable[[dict[str, Any]], Coroutine[Any, Any, tuple[bool, str]]]
DispatchAdmissionFn = Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]
NotifyFn = Callable[[dict[str, Any]], None]
TrustedIngressFn = Callable[[dict[str, Any]], Any]

# Idempotency key for Simone's primary inbox
_INBOX_CLIENT_ID = "ua-simone-primary"
_DEFAULT_TRUSTED_SENDERS = (
    "kevin.dragan@outlook.com",
    "kevinjdragan@gmail.com",
    "kevin@clearspringcg.com",
)
_QUEUE_STATUS_QUEUED = "queued"
_QUEUE_STATUS_DISPATCHING = "dispatching"
_QUEUE_STATUS_BUSY_RETRY = "busy_retry"
_QUEUE_STATUS_COMPLETED = "completed"
_QUEUE_STATUS_FAILED = "failed"
_QUEUE_STATUS_CANCELLED = "cancelled"


def _is_enabled() -> bool:
    val = os.getenv("UA_AGENTMAIL_ENABLED", "0").strip().lower()
    return val in {"1", "true", "yes", "on"}


def _ws_enabled() -> bool:
    val = os.getenv("UA_AGENTMAIL_WS_ENABLED", "0").strip().lower()
    return val in {"1", "true", "yes", "on"}


def _auto_send() -> bool:
    val = os.getenv("UA_AGENTMAIL_AUTO_SEND", "0").strip().lower()
    return val in {"1", "true", "yes", "on"}


def _ws_reconnect_base() -> float:
    try:
        return max(1.0, float(os.getenv("UA_AGENTMAIL_WS_RECONNECT_BASE_DELAY", "2")))
    except Exception:
        return 2.0


def _ws_reconnect_max() -> float:
    try:
        return max(10.0, float(os.getenv("UA_AGENTMAIL_WS_RECONNECT_MAX_DELAY", "120")))
    except Exception:
        return 120.0


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_json_loads(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            loaded = json.loads(text)
        except Exception:
            return {}
        return dict(loaded) if isinstance(loaded, dict) else {}
    return {}


def _safe_json_dumps(raw: Any) -> str:
    return json.dumps(raw, ensure_ascii=True, separators=(",", ":"))


def _normalize_sender_email(value: str) -> str:
    _, address = parseaddr(value or "")
    return (address or value or "").strip().lower()


def _trusted_sender_addresses() -> tuple[str, ...]:
    configured = os.getenv("UA_AGENTMAIL_TRUSTED_SENDERS", "").strip()
    if configured:
        raw_items = configured.split(",")
    else:
        raw_items = list(_DEFAULT_TRUSTED_SENDERS)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        address = _normalize_sender_email(str(item))
        if not address or address in seen:
            continue
        seen.add(address)
        normalized.append(address)
    return tuple(normalized)


# ── Automated / bounce sender detection ──────────────────────────────────

_AUTOMATED_SENDER_PREFIXES = (
    "mailer-daemon@",
    "postmaster@",
    "noreply@",
    "no-reply@",
    "notifications@",
    "bounce@",
    "auto-reply@",
    "autoreply@",
    "do-not-reply@",
    "donotreply@",
)

_DSN_SUBJECT_PATTERNS = (
    "delivery status notification",
    "undeliverable",
    "mail delivery failed",
    "returned mail",
    "failure notice",
    "delivery failure",
    "non-delivery report",
)


def _is_automated_sender(sender_email: str, subject: str = "") -> bool:
    """Return True if the sender looks like an automated / bounce / DSN email.

    Checks both the sender address (mailer-daemon, postmaster, noreply, etc.)
    and the subject line (DSN patterns like 'Delivery Status Notification').
    """
    email_lower = (sender_email or "").strip().lower()
    if any(email_lower.startswith(prefix) for prefix in _AUTOMATED_SENDER_PREFIXES):
        return True
    subject_lower = (subject or "").strip().lower()
    if any(pattern in subject_lower for pattern in _DSN_SUBJECT_PATTERNS):
        return True
    return False


class AgentMailService:
    """Async AgentMail service for Simone's email inbox."""

    def __init__(
        self,
        *,
        dispatch_fn: Optional[DispatchFn] = None,
        dispatch_with_admission_fn: Optional[DispatchAdmissionFn] = None,
        notification_sink: Optional[NotifyFn] = None,
        trusted_ingress_fn: Optional[TrustedIngressFn] = None,
        priority_dispatch_fn: Optional[Any] = None,
    ) -> None:
        self._dispatch_fn = dispatch_fn
        self._dispatch_with_admission_fn = dispatch_with_admission_fn
        self._notification_sink = notification_sink
        self._trusted_ingress_fn = trusted_ingress_fn
        self._priority_dispatch_fn = priority_dispatch_fn

        # Email-to-task bridge (lazy init, created when first needed)
        self._email_task_bridge: Any = None
        self._email_task_bridge_initialized = False

        # SDK client (lazy init)
        self._client: Any = None
        self._inbox_id: str = ""
        self._inbox_ids: list[str] = []
        self._inbox_address: str = ""

        # WebSocket listener
        self._ws_task: Optional[asyncio.Task] = None
        self._queue_task: Optional[asyncio.Task] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._ws_stop_event = asyncio.Event()
        self._queue_wakeup = asyncio.Event()

        # Runtime status
        self._enabled = _is_enabled()
        self._started = False
        self._started_at: Optional[str] = None
        self._ws_connected = False
        self._ws_reconnect_count = 0
        self._ws_last_status_code: Optional[int] = None
        self._ws_fail_opened = False
        self._last_error: str = ""
        self._messages_sent: int = 0
        self._messages_received: int = 0
        self._drafts_created: int = 0
        ws_fail_open_statuses_raw = (
            os.getenv("UA_AGENTMAIL_WS_FAIL_OPEN_STATUS_CODES", "401,403,429")
            .strip()
        )
        ws_fail_open_statuses: set[int] = set()
        for token in ws_fail_open_statuses_raw.split(","):
            clean = str(token or "").strip()
            if not clean:
                continue
            try:
                ws_fail_open_statuses.add(int(clean))
            except ValueError:
                continue
        self._ws_fail_open_status_codes = (
            ws_fail_open_statuses if ws_fail_open_statuses else {401, 403, 429}
        )
        self._ws_fail_open_after_attempts = max(
            1,
            int(os.getenv("UA_AGENTMAIL_WS_FAIL_OPEN_AFTER_ATTEMPTS", "3") or 3),
        )
        self._inbound_poll_enabled = (
            os.getenv("UA_AGENTMAIL_INBOUND_POLL_ENABLED", "1").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        self._inbound_poll_interval_seconds = max(
            15.0,
            float(os.getenv("UA_AGENTMAIL_INBOUND_POLL_INTERVAL_SECONDS", "60") or 60),
        )
        self._seen_message_ids: deque[str] = deque(maxlen=2000)
        self._seen_message_id_set: set[str] = set()
        self._trusted_senders: tuple[str, ...] = _trusted_sender_addresses()
        self._last_trusted_inbound_at: Optional[str] = None
        self._trusted_queue_retry_base_seconds = max(
            5.0, float(os.getenv("UA_AGENTMAIL_INBOX_RETRY_BASE_SECONDS", "10") or 10)
        )
        self._trusted_queue_retry_max_seconds = max(
            self._trusted_queue_retry_base_seconds,
            float(os.getenv("UA_AGENTMAIL_INBOX_RETRY_MAX_SECONDS", "900") or 900),
        )
        self._trusted_queue_retry_jitter_ratio = min(
            1.0,
            max(0.0, float(os.getenv("UA_AGENTMAIL_INBOX_RETRY_JITTER_RATIO", "0.30") or 0.30)),
        )
        self._trusted_queue_poll_seconds = max(
            1.0, float(os.getenv("UA_AGENTMAIL_INBOX_POLL_SECONDS", "5") or 5)
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        if not self._enabled:
            logger.info("📧 AgentMail service DISABLED (UA_AGENTMAIL_ENABLED=0)")
            return

        api_key = os.getenv("AGENTMAIL_API_KEY", "").strip()
        if not api_key:
            logger.warning("📧 AgentMail service not started: AGENTMAIL_API_KEY not set")
            self._enabled = False
            self._last_error = "missing_api_key"
            return

        try:
            from agentmail import AsyncAgentMail
            self._client = AsyncAgentMail(api_key=api_key)
        except Exception as exc:
            logger.error("📧 Failed to initialize AgentMail client: %s", exc)
            self._enabled = False
            self._last_error = f"init_failed: {exc}"
            return

        # Resolve or create inbox
        try:
            await self._ensure_inbox()
        except Exception as exc:
            logger.error("📧 Failed to ensure inbox: %s", exc)
            self._last_error = f"inbox_setup_failed: {exc}"
            # Don't disable — client is valid, inbox can be retried
            return

        self._started = True
        self._started_at = _iso_now()
        self._ensure_queue_schema()
        logger.info(
            "📧 AgentMail service started inbox=%s auto_send=%s ws=%s",
            self._inbox_address,
            _auto_send(),
            _ws_enabled(),
        )

        if self._dispatch_with_admission_fn:
            self._queue_task = asyncio.create_task(self._trusted_inbox_queue_loop())
            self._queue_wakeup.set()

        if self._inbound_poll_enabled and self._dispatch_fn:
            self._poll_task = asyncio.create_task(self._inbox_poll_loop())

        # Start WebSocket listener if enabled
        if _ws_enabled() and self._inbox_id:
            self._ws_task = asyncio.create_task(self._ws_loop())

    async def shutdown(self) -> None:
        self._ws_stop_event.set()
        self._queue_wakeup.set()
        ws_task = self._ws_task
        if ws_task is not None:
            try:
                await asyncio.wait_for(ws_task, timeout=10)
            except Exception:
                ws_task.cancel()
            self._ws_task = None
        queue_task = self._queue_task
        if queue_task is not None:
            try:
                await asyncio.wait_for(queue_task, timeout=10)
            except Exception:
                queue_task.cancel()
            self._queue_task = None
        poll_task = self._poll_task
        if poll_task is not None:
            try:
                await asyncio.wait_for(poll_task, timeout=10)
            except Exception:
                poll_task.cancel()
            self._poll_task = None
        self._started = False
        logger.info("📧 AgentMail service stopped")

    # ------------------------------------------------------------------
    # Inbox Management
    # ------------------------------------------------------------------

    async def _ensure_inbox(self) -> None:
        """Resolve existing inboxes or create a new one idempotently."""
        configured_address = os.getenv("UA_AGENTMAIL_INBOX_ADDRESS", "").strip()
        configured_addresses_raw = os.getenv("UA_AGENTMAIL_INBOX_ADDRESSES", "").strip()
        
        self._inbox_ids = []
        
        if configured_addresses_raw:
            for addr in configured_addresses_raw.split(","):
                addr = addr.strip()
                if addr:
                    try:
                        await self._client.inboxes.get(inbox_id=addr)
                        if addr not in self._inbox_ids:
                            self._inbox_ids.append(addr)
                    except Exception as exc:
                        logger.warning("📧 Configured inbox %s not found: %s", addr, exc)
        elif configured_address:
            try:
                await self._client.inboxes.get(inbox_id=configured_address)
                self._inbox_ids.append(configured_address)
            except Exception as exc:
                logger.warning("📧 Configured inbox %s not found: %s", configured_address, exc)

        if self._inbox_ids:
            self._inbox_id = self._inbox_ids[0]
            self._inbox_address = self._inbox_id
            logger.info("📧 Resolved existing inboxes: %s (Primary: %s)", self._inbox_ids, self._inbox_address)
            return

        # Create inbox idempotently
        username = os.getenv("UA_AGENTMAIL_INBOX_USERNAME", "simone").strip() or "simone"
        try:
            inbox = await self._client.inboxes.create(
                username=username,
                client_id=_INBOX_CLIENT_ID,
            )
            self._inbox_id = inbox.inbox_id
            self._inbox_address = inbox.inbox_id
            logger.info("📧 Created inbox: %s", self._inbox_address)
        except Exception as exc:
            # If creation fails (e.g. username taken), try without username
            logger.warning("📧 Inbox creation with username=%s failed: %s, trying auto", username, exc)
            inbox = await self._client.inboxes.create(client_id=_INBOX_CLIENT_ID)
            self._inbox_id = inbox.inbox_id
            self._inbox_ids = [self._inbox_id]
            self._inbox_address = inbox.inbox_id
            logger.info("📧 Created auto-named inbox: %s", self._inbox_address)

    def get_inbox_address(self) -> str:
        """Return the current inbox email address."""
        return self._inbox_address

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send_email(
        self,
        *,
        to: str,
        subject: str,
        text: str,
        html: Optional[str] = None,
        attachments: Optional[list[dict[str, Any]]] = None,
        labels: Optional[list[str]] = None,
        force_send: bool = False,
    ) -> dict[str, Any]:
        """Send an email or create a draft depending on policy.

        Args:
            to: Recipient email address.
            subject: Email subject.
            text: Plain text body.
            html: Optional HTML body (recommended for deliverability).
            attachments: Optional list of attachment dicts with content/filename/content_type.
            labels: Optional labels to apply.
            force_send: Override draft policy and send directly.

        Returns:
            Dict with message_id/draft_id and status.
        """
        self._assert_ready()

        if _auto_send() or force_send:
            return await self._send_direct(
                to=to, subject=subject, text=text, html=html,
                attachments=attachments, labels=labels,
            )
        else:
            return await self._create_draft(
                to=to, subject=subject, text=text, html=html,
                attachments=attachments,
            )

    async def _send_direct(
        self, *, to: str, subject: str, text: str,
        html: Optional[str], attachments: Optional[list[dict]], labels: Optional[list[str]],
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "inbox_id": self._inbox_id,
            "to": to,
            "subject": subject,
            "text": text,
        }
        if html:
            kwargs["html"] = html
        if attachments:
            kwargs["attachments"] = attachments
        if labels:
            kwargs["labels"] = labels

        msg = await self._client.inboxes.messages.send(**kwargs)
        self._messages_sent += 1
        logger.info("📧 Sent email to=%s subject=%r message_id=%s", to, subject, msg.message_id)
        self._emit_notification(
            kind="agentmail_sent",
            title="Simone Sent Email",
            message=f"To: {to} | Subject: {subject}",
            severity="info",
            metadata={
                "message_id": msg.message_id,
                "to": to,
                "subject": subject,
                "inbox": self._inbox_address,
            },
        )
        return {"status": "sent", "message_id": msg.message_id, "inbox": self._inbox_address}

    async def _create_draft(
        self, *, to: str, subject: str, text: str,
        html: Optional[str], attachments: Optional[list[dict]],
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "inbox_id": self._inbox_id,
            "to": to,
            "subject": subject,
            "text": text,
        }
        if html:
            kwargs["html"] = html
        if attachments:
            kwargs["attachments"] = attachments

        draft = await self._client.inboxes.drafts.create(**kwargs)
        self._drafts_created += 1
        logger.info(
            "📧 Draft created to=%s subject=%r draft_id=%s (awaiting approval)",
            to, subject, draft.draft_id,
        )
        self._emit_notification(
            kind="agentmail_draft_created",
            title="Email Draft Created",
            message=f"To: {to} | Subject: {subject} — approve or discard",
            severity="info",
            metadata={
                "draft_id": draft.draft_id,
                "to": to,
                "subject": subject,
                "inbox": self._inbox_address,
            },
        )
        return {"status": "draft", "draft_id": draft.draft_id, "inbox": self._inbox_address}

    async def send_draft(self, draft_id: str) -> dict[str, Any]:
        """Approve and send a previously created draft."""
        self._assert_ready()
        msg = await self._client.inboxes.drafts.send(
            inbox_id=self._inbox_id,
            draft_id=draft_id,
        )
        self._messages_sent += 1
        logger.info("📧 Draft sent draft_id=%s", draft_id)
        return {"status": "sent", "draft_id": draft_id}

    async def list_drafts(
        self,
        *,
        inbox_id: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List drafts across one or all inboxes."""
        self._assert_ready()
        target_ids = [inbox_id] if inbox_id else (self._inbox_ids or [self._inbox_id])
        results: list[dict[str, Any]] = []
        for iid in target_ids:
            if not iid:
                continue
            try:
                drafts = await self._client.inboxes.drafts.list(inbox_id=iid)
                draft_list: list[Any] = list(
                    drafts.drafts if hasattr(drafts, "drafts") else drafts
                )
                for d in draft_list:
                    results.append({
                        "draft_id": getattr(d, "draft_id", ""),
                        "inbox_id": iid,
                        "to": getattr(d, "to", ""),
                        "subject": getattr(d, "subject", ""),
                        "text_preview": (getattr(d, "text", "") or "")[:200],
                        "send_status": getattr(d, "send_status", None),
                        "send_at": str(getattr(d, "send_at", "") or ""),
                        "created_at": str(getattr(d, "created_at", "")),
                    })
            except Exception as exc:
                logger.warning("📧 Failed to list drafts for inbox %s: %s", iid, exc)
        results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return results[:limit]

    async def reply(
        self,
        *,
        message_id: str,
        text: str,
        html: Optional[str] = None,
    ) -> dict[str, Any]:
        """Reply to a message in-thread."""
        self._assert_ready()
        kwargs: dict[str, Any] = {
            "inbox_id": self._inbox_id,
            "message_id": message_id,
            "text": text,
        }
        if html:
            kwargs["html"] = html

        msg = await self._client.inboxes.messages.reply(**kwargs)
        self._messages_sent += 1
        logger.info("📧 Replied to message_id=%s", message_id)
        return {"status": "sent", "message_id": msg.message_id}

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    async def list_messages(
        self,
        *,
        inbox_id: Optional[str] = None,
        label: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List recent messages in the inbox."""
        self._assert_ready()
        kwargs: dict[str, Any] = {"inbox_id": inbox_id or self._inbox_id}
        if label:
            kwargs["labels"] = [label]

        messages = await self._client.inboxes.messages.list(**kwargs)
        msg_list: list[Any] = list(messages.messages if hasattr(messages, "messages") else messages)
        results = []
        for msg in msg_list[:limit]:
            results.append({
                "message_id": getattr(msg, "message_id", ""),
                "thread_id": getattr(msg, "thread_id", ""),
                "from": getattr(msg, "from_", ""),
                "to": getattr(msg, "to", ""),
                "subject": getattr(msg, "subject", ""),
                "text": (getattr(msg, "text", "") or "")[:500],
                "labels": getattr(msg, "labels", []),
                "created_at": str(getattr(msg, "created_at", "")),
            })
        return results

    async def get_message(self, message_id: str) -> dict[str, Any]:
        """Get a specific message by ID."""
        self._assert_ready()
        msg = await self._client.inboxes.messages.get(
            inbox_id=self._inbox_id,
            message_id=message_id,
        )
        return {
            "message_id": getattr(msg, "message_id", ""),
            "thread_id": getattr(msg, "thread_id", ""),
            "from": getattr(msg, "from_", ""),
            "to": getattr(msg, "to", ""),
            "subject": getattr(msg, "subject", ""),
            "text": getattr(msg, "text", ""),
            "html": getattr(msg, "html", ""),
            "labels": getattr(msg, "labels", []),
            "attachments": getattr(msg, "attachments", []),
            "created_at": str(getattr(msg, "created_at", "")),
        }

    async def list_threads(
        self,
        *,
        inbox_id: Optional[str] = None,
        label: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List threads in a specific inbox (or primary inbox)."""
        self._assert_ready()
        kwargs: dict[str, Any] = {"inbox_id": inbox_id or self._inbox_id}
        if label:
            kwargs["labels"] = [label]

        threads = await self._client.inboxes.threads.list(**kwargs)
        thd_list: list[Any] = list(threads.threads if hasattr(threads, "threads") else threads)
        results = []
        for thd in thd_list[:limit]:
            results.append({
                "thread_id": getattr(thd, "thread_id", ""),
                "inbox_id": inbox_id or self._inbox_id,
                "subject": getattr(thd, "subject", ""),
                "preview": (getattr(thd, "preview", "") or "")[:200],
                "labels": getattr(thd, "labels", []),
                "message_count": getattr(thd, "message_count", 0),
                "created_at": str(getattr(thd, "created_at", "")),
                "updated_at": str(getattr(thd, "updated_at", "") or ""),
            })
        return results

    async def list_all_threads(
        self,
        *,
        label: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List threads across all configured inboxes, merged and sorted by recency."""
        self._assert_ready()
        target_ids = self._inbox_ids or [self._inbox_id]
        all_threads: list[dict[str, Any]] = []
        for iid in target_ids:
            if not iid:
                continue
            try:
                threads = await self.list_threads(inbox_id=iid, label=label, limit=limit)
                all_threads.extend(threads)
            except Exception as exc:
                logger.warning("📧 Failed to list threads for inbox %s: %s", iid, exc)
        # Sort by updated_at (most recent first), fallback to created_at
        all_threads.sort(
            key=lambda t: t.get("updated_at") or t.get("created_at", ""),
            reverse=True,
        )
        return all_threads[:limit]

    async def get_thread(self, thread_id: str) -> dict[str, Any]:
        """Fetch a specific thread by ID, including all its messages.

        Uses the top-level threads.get() API which returns a Thread object
        with an embedded ``messages`` list — much more reliable than listing
        recent messages globally and filtering.
        """
        self._assert_ready()
        thd = await self._client.threads.get(thread_id=thread_id)
        messages: list[dict[str, Any]] = []
        for msg in getattr(thd, "messages", []) or []:
            messages.append({
                "message_id": getattr(msg, "message_id", ""),
                "thread_id": getattr(msg, "thread_id", ""),
                "from": getattr(msg, "from_", ""),
                "to": getattr(msg, "to", ""),
                "subject": getattr(msg, "subject", ""),
                "text": (getattr(msg, "text", "") or "")[:2000],
                "labels": getattr(msg, "labels", []),
                "created_at": str(getattr(msg, "created_at", "")),
            })
        return {
            "thread_id": getattr(thd, "thread_id", ""),
            "inbox_id": getattr(thd, "inbox_id", ""),
            "subject": getattr(thd, "subject", ""),
            "preview": (getattr(thd, "preview", "") or "")[:200],
            "labels": getattr(thd, "labels", []),
            "message_count": getattr(thd, "message_count", 0),
            "created_at": str(getattr(thd, "created_at", "")),
            "updated_at": str(getattr(thd, "updated_at", "") or ""),
            "messages": messages,
        }

    async def delete_thread(self, *, inbox_id: str, thread_id: str) -> dict[str, Any]:
        """Delete a thread from an inbox via AgentMail API."""
        self._assert_ready()
        await self._client.inboxes.threads.delete(inbox_id=inbox_id, thread_id=thread_id)
        logger.info("📧 Deleted thread thread_id=%s inbox_id=%s", thread_id, inbox_id)
        return {"status": "deleted", "thread_id": thread_id, "inbox_id": inbox_id}

    def get_inbox_ids(self) -> list[str]:
        """Return all configured inbox IDs."""
        return list(self._inbox_ids) if self._inbox_ids else ([self._inbox_id] if self._inbox_id else [])

    # ------------------------------------------------------------------
    # WebSocket Listener (inbound email → hook dispatch)
    # ------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        """Persistent WebSocket listener with exponential backoff reconnect."""
        delay = _ws_reconnect_base()
        max_delay = _ws_reconnect_max()

        while not self._ws_stop_event.is_set():
            try:
                await self._ws_connect_and_listen()
                # Clean disconnect — reset backoff
                delay = _ws_reconnect_base()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._ws_connected = False
                self._ws_reconnect_count += 1
                status_code = self._extract_ws_status_code(exc)
                self._ws_last_status_code = status_code
                if (
                    status_code in self._ws_fail_open_status_codes
                    and self._ws_reconnect_count >= self._ws_fail_open_after_attempts
                ):
                    self._ws_fail_opened = True
                    self._last_error = f"ws_fail_open_status_{status_code}"
                    logger.warning(
                        "📧 WebSocket fail-open activated after status=%s reconnect attempts=%d; "
                        "stopping WS listener and relying on inbound polling.",
                        status_code,
                        self._ws_reconnect_count,
                    )
                    return
                if status_code is not None:
                    self._last_error = f"ws_status_{status_code}"
                    logger.warning(
                        "📧 WebSocket disconnected status=%s, reconnecting in %.0fs (attempt #%d)",
                        status_code,
                        delay,
                        self._ws_reconnect_count,
                    )
                else:
                    self._last_error = f"ws_disconnect:{type(exc).__name__}"
                    logger.warning(
                        "📧 WebSocket disconnected (%s), reconnecting in %.0fs (attempt #%d)",
                        type(exc).__name__,
                        delay,
                        self._ws_reconnect_count,
                    )
                # Exponential backoff with jitter
                jitter = random.uniform(0, delay * 0.3)
                try:
                    await asyncio.wait_for(
                        self._ws_stop_event.wait(),
                        timeout=delay + jitter,
                    )
                    return  # stop event was set
                except asyncio.TimeoutError:
                    pass
                delay = min(delay * 2, max_delay)

    async def _ws_connect_and_listen(self) -> None:
        """Single WebSocket connection lifecycle."""
        from agentmail import Subscribe, MessageReceivedEvent

        inbox_targets = self._inbox_ids if self._inbox_ids else [self._inbox_id]
        logger.info("📧 WebSocket connecting to AgentMail for inboxes=%s", inbox_targets)
        async with self._client.websockets.connect() as socket:
            await socket.send_subscribe(
                Subscribe(inbox_ids=inbox_targets)
            )
            self._ws_connected = True
            self._last_error = ""
            logger.info("📧 WebSocket connected and subscribed to inboxes=%s", inbox_targets)

            async for event in socket:
                if self._ws_stop_event.is_set():
                    break

                if isinstance(event, MessageReceivedEvent):
                    self._messages_received += 1
                    await self._handle_inbound_email(event)

    async def _handle_inbound_email(self, event: Any) -> None:
        """Process an inbound email event and dispatch through hooks."""
        message_id = ""
        claimed_message = False
        try:
            msg = event.message
            receiving_inbox = getattr(event, "inbox_id", getattr(msg, "inbox_id", self._inbox_address))
            sender = getattr(msg, "from_", "unknown")
            sender_email = _normalize_sender_email(sender)
            sender_role = (
                "trusted_operator"
                if sender_email and sender_email in self._trusted_senders
                else "external"
            )
            sender_trusted = sender_role == "trusted_operator"
            subject = getattr(msg, "subject", "(no subject)")
            thread_id = getattr(msg, "thread_id", "")
            message_id = str(getattr(msg, "message_id", "") or "").strip()
            text_body = getattr(msg, "text", "") or ""
            html_body = getattr(msg, "html", "") or ""

            # ── Filter automated / bounce / DSN emails ──
            # Drop mailer-daemon, noreply, postmaster, DSN subjects, etc.
            # before they create noisy dashboard notifications or trigger
            # dispatch / task materialization.
            if _is_automated_sender(sender_email, subject):
                logger.debug(
                    "📧 Suppressing automated/bounce email from=%s subject=%r message_id=%s",
                    sender_email, subject, message_id,
                )
                if message_id:
                    self._claim_seen_message_id(message_id)
                return

            if message_id:
                claimed_message = self._claim_seen_message_id(message_id)
                if not claimed_message:
                    logger.debug("📧 Skipping duplicate inbound message_id=%s", message_id)
                    return

            # Extract clean reply content (strips quoted thread history)
            # Uses HTML-aware extraction for richer accuracy
            reply_text = _extract_reply_text(text_body, html_body)
            reply_is_extracted = reply_text != text_body

            logger.info(
                "📧 Inbound email from=%s subject=%r thread=%s reply_extracted=%s trusted=%s",
                sender, subject, thread_id, reply_is_extracted, sender_trusted,
            )

            session_key = f"agentmail_{thread_id or message_id}"
            action_payload = {
                "kind": "agent",
                "name": "AgentMailInbound",
                "session_key": session_key,
                "to": "email-handler",
                "deliver": True,
                "receiving_inbox": receiving_inbox,
                "message": self._build_inbound_message(
                    sender=sender,
                    sender_email=sender_email,
                    sender_role=sender_role,
                    sender_trusted=sender_trusted,
                    subject=subject,
                    thread_id=thread_id,
                    message_id=message_id,
                    receiving_inbox=receiving_inbox,
                    reply_text=reply_text,
                    reply_is_extracted=reply_is_extracted,
                    text_body=text_body,
                    attachments=getattr(msg, "attachments", None),
                ),
            }

            if sender_trusted and self._trusted_ingress_fn:
                try:
                    self._trusted_ingress_fn(dict(action_payload))
                except Exception as exc:
                    logger.warning(
                        "📧 Trusted inbound pre-dispatch hook failed message_id=%s: %s",
                        message_id,
                        exc,
                    )

            # Emit notification
            self._emit_notification(
                kind="agentmail_received",
                title="New Email Received",
                message=f"From: {sender} | Subject: {subject}",
                severity="info",
                metadata={
                    "message_id": message_id,
                    "thread_id": thread_id,
                    "from": sender,
                    "sender_email": sender_email,
                    "sender_role": sender_role,
                    "sender_trusted": sender_trusted,
                    "subject": subject,
                    "inbox": self._inbox_address,
                },
            )

            if sender_trusted and message_id and self._dispatch_with_admission_fn:
                self._last_trusted_inbound_at = _iso_now()
                queue_id, created = self._queue_insert_trusted_inbound(
                    message_id=message_id,
                    thread_id=thread_id,
                    sender=sender,
                    sender_email=sender_email,
                    subject=subject,
                    reply_text=reply_text,
                    text_body=text_body,
                    session_key=session_key,
                    action_payload=action_payload,
                )

                # ── Email-to-Task Bridge: materialize as trackable task ──
                bridge_result = self._materialize_email_task(
                    thread_id=thread_id,
                    message_id=message_id,
                    sender_email=sender_email,
                    subject=subject,
                    reply_text=reply_text,
                    session_key=session_key,
                )

                # ── Priority-aware dispatch ──
                # Classify the email and dispatch immediately for P0/P1
                self._try_priority_dispatch(
                    sender_trusted=True,
                    is_reply=bool(reply_text),
                    thread_message_count=int(
                        bridge_result.get("message_count", 1) if bridge_result else 1
                    ),
                    subject=subject,
                    body_snippet=(reply_text or text_body or "")[:200],
                    task_id=bridge_result.get("task_id", "") if bridge_result else "",
                    thread_id=thread_id,
                    sender_email=sender_email,
                )

                # No automatic ack reply — Kevin trusts Simone is working
                # on the email and only expects a meaningful reply when done.
                self._emit_notification(
                    kind="agentmail_trusted_queued",
                    title="Trusted Email Queued",
                    message=f"Queued trusted inbound email from {sender_email}",
                    severity="info",
                    metadata={
                        "message_id": message_id,
                        "thread_id": thread_id,
                        "queue_id": queue_id,
                        "sender_email": sender_email,
                        "subject": subject,
                        "email_task_id": bridge_result.get("task_id", "") if bridge_result else "",
                    },
                )
                self._queue_wakeup.set()
                return

            # No automatic ack reply for trusted senders — only
            # the meaningful post-triage response is sent.

            # Dispatch through hooks pipeline if dispatch_fn available
            if self._dispatch_fn:
                try:
                    ok, reason = await self._dispatch_fn(action_payload)
                    if ok:
                        logger.info(
                            "📧 Dispatched inbound email handler message_id=%s",
                            message_id,
                        )
                    else:
                        logger.warning(
                            "📧 Inbound dispatch rejected message_id=%s reason=%s",
                            message_id, reason,
                        )
                except Exception as exc:
                    logger.exception(
                        "📧 Inbound dispatch error message_id=%s: %s",
                        message_id, exc,
                    )
        except Exception as exc:
            if message_id and claimed_message:
                self._release_seen_message_id(message_id)
            logger.exception("📧 Error handling inbound email: %s", exc)

    # ------------------------------------------------------------------
    # Email-to-Task Bridge
    # ------------------------------------------------------------------

    def _get_email_task_bridge(self) -> Any:
        """Lazily initialize the EmailTaskBridge."""
        if self._email_task_bridge_initialized:
            return self._email_task_bridge
        self._email_task_bridge_initialized = True
        try:
            from universal_agent.services.email_task_bridge import EmailTaskBridge

            conn = connect_runtime_db(get_activity_db_path())
            conn.row_factory = sqlite3.Row

            # Try to get the Todoist service (optional — best-effort)
            todoist_svc = None
            try:
                from universal_agent.services.todoist_service import TodoService
                todoist_svc = TodoService()  # Uses env var from infisical run wrapper
            except Exception as t_exc:
                logger.debug("📧→📋 Todoist service unavailable for email tasks: %s", t_exc)

            self._email_task_bridge = EmailTaskBridge(
                db_conn=conn,
                todoist_service=todoist_svc,
            )
            logger.info("📧→📋 EmailTaskBridge initialized successfully")
        except Exception as exc:
            logger.warning("📧→📋 EmailTaskBridge initialization failed: %s", exc)
            self._email_task_bridge = None
        return self._email_task_bridge

    def _materialize_email_task(
        self,
        *,
        thread_id: str,
        message_id: str,
        sender_email: str,
        subject: str,
        reply_text: str,
        session_key: str,
    ) -> Optional[dict[str, Any]]:
        """Materialize an inbound trusted email as a tracked task.

        This creates/updates a Task Hub entry, Todoist task, and HEARTBEAT.md
        entry so the email conversation becomes visible on the To-Do List
        dashboard and gets picked up by the heartbeat scheduler.
        Returns the bridge result dict, or None if the bridge is unavailable.
        """
        bridge = self._get_email_task_bridge()
        if bridge is None:
            return None
        try:
            return bridge.materialize(
                thread_id=thread_id,
                message_id=message_id,
                sender_email=sender_email,
                subject=subject,
                reply_text=reply_text,
                session_key=session_key,
            )
        except Exception as exc:
            logger.warning(
                "📧→📋 Email task materialization failed thread=%s: %s",
                thread_id, exc,
            )
            return None

    def _try_priority_dispatch(
        self,
        *,
        sender_trusted: bool,
        is_reply: bool,
        thread_message_count: int,
        subject: str,
        body_snippet: str,
        task_id: str,
        thread_id: str,
        sender_email: str,
    ) -> None:
        """Classify email priority and dispatch immediately for P0/P1.

        For P0 (immediate) and P1 (soon) priorities, triggers a heartbeat
        wake so Simone picks up the task without waiting for the next
        scheduled heartbeat cycle.
        """
        try:
            from universal_agent.services.priority_classifier import (
                classify_email_priority,
                TaskPriority,
            )

            decision = classify_email_priority(
                sender_trusted=sender_trusted,
                is_reply=is_reply,
                thread_message_count=thread_message_count,
                subject=subject,
                body_snippet=body_snippet,
            )

            logger.info(
                "📧⚡ Priority classified: %s (%s) strategy=%s subject='%s'",
                decision.priority.value,
                decision.reason,
                decision.strategy,
                subject[:60],
            )

            # P0/P1: trigger immediate dispatch via callback
            if decision.priority in (TaskPriority.P0_IMMEDIATE, TaskPriority.P1_SOON):
                if self._priority_dispatch_fn:
                    dispatch_result = self._priority_dispatch_fn(
                        task_id=task_id,
                        thread_id=thread_id,
                        sender_email=sender_email,
                        priority=decision.priority.value,
                        reason=decision.reason,
                    )
                    logger.info(
                        "📧⚡ Immediate dispatch result: %s",
                        dispatch_result,
                    )
                else:
                    logger.debug(
                        "📧⚡ Priority dispatch fn not wired — "
                        "falling back to heartbeat for %s task",
                        decision.priority.value,
                    )
            # P2/P3: no immediate dispatch, heartbeat will pick it up

        except Exception as exc:
            # Never fail the email flow due to priority classification
            logger.debug("📧⚡ Priority dispatch failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Trusted inbox queue
    # ------------------------------------------------------------------

    def _queue_connect(self) -> sqlite3.Connection:
        conn = connect_runtime_db(get_activity_db_path())
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_queue_schema(self) -> None:
        with self._queue_connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agentmail_inbox_queue (
                    queue_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL UNIQUE,
                    thread_id TEXT,
                    sender TEXT NOT NULL,
                    sender_email TEXT NOT NULL,
                    sender_role TEXT NOT NULL DEFAULT 'external',
                    subject TEXT NOT NULL DEFAULT '',
                    session_key TEXT NOT NULL,
                    action_payload_json TEXT NOT NULL DEFAULT '{}',
                    reply_text TEXT NOT NULL DEFAULT '',
                    full_body TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    last_attempt_at TEXT,
                    last_error TEXT NOT NULL DEFAULT '',
                    ack_status TEXT NOT NULL DEFAULT 'not_sent',
                    ack_message_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agentmail_inbox_queue_status_next
                    ON agentmail_inbox_queue(status, next_attempt_at, created_at);
                CREATE INDEX IF NOT EXISTS idx_agentmail_inbox_queue_sender
                    ON agentmail_inbox_queue(sender_email, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_agentmail_inbox_queue_thread
                    ON agentmail_inbox_queue(thread_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS agentmail_seen_messages (
                    message_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                );
                """
            )
            # Migration-safe column additions for post-triage lifecycle tracking
            _migration_columns = [
                ("completed_at", "TEXT DEFAULT ''"),
                ("session_exit_status", "TEXT DEFAULT ''"),
                ("reply_sent", "INTEGER DEFAULT 0"),
                ("classification", "TEXT DEFAULT ''"),
            ]
            for col_name, col_type in _migration_columns:
                try:
                    conn.execute(
                        f"ALTER TABLE agentmail_inbox_queue ADD COLUMN {col_name} {col_type}"
                    )
                except sqlite3.OperationalError:
                    pass  # Column already exists — safe to ignore

    def _queue_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = {
            "queue_id": str(row["queue_id"]),
            "message_id": str(row["message_id"]),
            "thread_id": str(row["thread_id"] or ""),
            "sender": str(row["sender"] or ""),
            "sender_email": str(row["sender_email"] or ""),
            "sender_role": str(row["sender_role"] or "external"),
            "subject": str(row["subject"] or ""),
            "session_key": str(row["session_key"] or ""),
            "reply_text": str(row["reply_text"] or ""),
            "full_body": str(row["full_body"] or ""),
            "status": str(row["status"] or _QUEUE_STATUS_QUEUED),
            "attempt_count": int(row["attempt_count"] or 0),
            "next_attempt_at": str(row["next_attempt_at"] or ""),
            "last_attempt_at": str(row["last_attempt_at"] or ""),
            "last_error": str(row["last_error"] or ""),
            "ack_status": str(row["ack_status"] or "not_sent"),
            "ack_message_id": str(row["ack_message_id"] or ""),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
            "action_payload": _safe_json_loads(row["action_payload_json"]),
        }
        # Include migration columns if present
        for col in ("completed_at", "session_exit_status", "classification"):
            try:
                d[col] = str(row[col] or "")
            except (IndexError, KeyError):
                d[col] = ""
        try:
            d["reply_sent"] = int(row["reply_sent"] or 0)
        except (IndexError, KeyError):
            d["reply_sent"] = 0
        return d

    def _trusted_queue_overview(self) -> dict[str, Any]:
        self._ensure_queue_schema()
        with self._queue_connect() as conn:
            counts = conn.execute(
                """
                SELECT status, COUNT(*) AS total
                FROM agentmail_inbox_queue
                GROUP BY status
                """
            ).fetchall()
            queue_depth = conn.execute(
                """
                SELECT COUNT(*)
                FROM agentmail_inbox_queue
                WHERE status IN (?, ?, ?)
                """,
                (_QUEUE_STATUS_QUEUED, _QUEUE_STATUS_BUSY_RETRY, _QUEUE_STATUS_DISPATCHING),
            ).fetchone()[0]
            oldest_pending = conn.execute(
                """
                SELECT created_at
                FROM agentmail_inbox_queue
                WHERE status IN (?, ?, ?)
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (_QUEUE_STATUS_QUEUED, _QUEUE_STATUS_BUSY_RETRY, _QUEUE_STATUS_DISPATCHING),
            ).fetchone()
        by_status = {str(row["status"]): int(row["total"] or 0) for row in counts}
        return {
            "queue_depth": int(queue_depth or 0),
            "oldest_pending_request_at": str(oldest_pending["created_at"]) if oldest_pending else "",
            "busy_retry_total": int(by_status.get(_QUEUE_STATUS_BUSY_RETRY, 0)),
            "completed_total": int(by_status.get(_QUEUE_STATUS_COMPLETED, 0)),
            "failed_total": int(by_status.get(_QUEUE_STATUS_FAILED, 0)),
        }

    def list_inbox_queue(
        self,
        *,
        limit: int = 50,
        status: str | None = None,
        sender: str | None = None,
        trusted_only: bool = False,
    ) -> list[dict[str, Any]]:
        self._ensure_queue_schema()
        clauses: list[str] = []
        params: list[Any] = []
        normalized_status = str(status or "").strip().lower()
        if normalized_status:
            clauses.append("status = ?")
            params.append(normalized_status)
        normalized_sender = _normalize_sender_email(sender or "")
        if normalized_sender:
            clauses.append("sender_email = ?")
            params.append(normalized_sender)
        if trusted_only:
            clauses.append("sender_role = 'trusted_operator'")
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT *
            FROM agentmail_inbox_queue
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
        """
        params.append(max(1, min(int(limit or 50), 200)))
        with self._queue_connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._queue_row_to_dict(row) for row in rows]

    def get_inbox_queue_item(self, queue_id: str) -> Optional[dict[str, Any]]:
        self._ensure_queue_schema()
        with self._queue_connect() as conn:
            row = conn.execute(
                "SELECT * FROM agentmail_inbox_queue WHERE queue_id = ? LIMIT 1",
                (str(queue_id or "").strip(),),
            ).fetchone()
        return self._queue_row_to_dict(row) if row else None

    def retry_inbox_queue_item(self, queue_id: str) -> Optional[dict[str, Any]]:
        queue_id = str(queue_id or "").strip()
        if not queue_id:
            return None
        now = _iso_now()
        self._ensure_queue_schema()
        with self._queue_connect() as conn:
            conn.execute(
                """
                UPDATE agentmail_inbox_queue
                SET status = ?, next_attempt_at = ?, updated_at = ?, last_error = ''
                WHERE queue_id = ? AND status NOT IN (?, ?)
                """,
                (
                    _QUEUE_STATUS_QUEUED,
                    now,
                    now,
                    queue_id,
                    _QUEUE_STATUS_COMPLETED,
                    _QUEUE_STATUS_CANCELLED,
                ),
            )
        self._queue_wakeup.set()
        return self.get_inbox_queue_item(queue_id)

    def cancel_inbox_queue_item(self, queue_id: str) -> Optional[dict[str, Any]]:
        queue_id = str(queue_id or "").strip()
        if not queue_id:
            return None
        now = _iso_now()
        self._ensure_queue_schema()
        with self._queue_connect() as conn:
            conn.execute(
                """
                UPDATE agentmail_inbox_queue
                SET status = ?, updated_at = ?
                WHERE queue_id = ? AND status NOT IN (?, ?)
                """,
                (
                    _QUEUE_STATUS_CANCELLED,
                    now,
                    queue_id,
                    _QUEUE_STATUS_COMPLETED,
                    _QUEUE_STATUS_CANCELLED,
                ),
            )
        return self.get_inbox_queue_item(queue_id)

    def _queue_insert_trusted_inbound(
        self,
        *,
        message_id: str,
        thread_id: str,
        sender: str,
        sender_email: str,
        subject: str,
        reply_text: str,
        text_body: str,
        session_key: str,
        action_payload: dict[str, Any],
    ) -> tuple[str, bool]:
        self._ensure_queue_schema()
        existing = self._find_queue_item_by_message_id(message_id)
        if existing:
            return str(existing.get("queue_id") or ""), False
        queue_id = f"amq_{uuid.uuid4().hex}"
        now = _iso_now()
        with self._queue_connect() as conn:
            conn.execute(
                """
                INSERT INTO agentmail_inbox_queue (
                    queue_id, message_id, thread_id, sender, sender_email, sender_role,
                    subject, session_key, action_payload_json, reply_text, full_body,
                    status, attempt_count, next_attempt_at, last_attempt_at, last_error,
                    ack_status, ack_message_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'trusted_operator', ?, ?, ?, ?, ?, ?, 0, ?, '', '', 'not_sent', '', ?, ?)
                """,
                (
                    queue_id,
                    message_id,
                    thread_id,
                    sender,
                    sender_email,
                    subject,
                    session_key,
                    _safe_json_dumps(action_payload),
                    reply_text,
                    text_body,
                    _QUEUE_STATUS_QUEUED,
                    now,
                    now,
                    now,
                ),
            )
        return queue_id, True

    def _find_queue_item_by_message_id(self, message_id: str) -> Optional[dict[str, Any]]:
        with self._queue_connect() as conn:
            row = conn.execute(
                "SELECT * FROM agentmail_inbox_queue WHERE message_id = ? LIMIT 1",
                (str(message_id or "").strip(),),
            ).fetchone()
        return self._queue_row_to_dict(row) if row else None

    def _mark_queue_ack_status(
        self,
        *,
        queue_id: str,
        ack_status: str,
        ack_message_id: str = "",
        last_error: str = "",
    ) -> None:
        now = _iso_now()
        with self._queue_connect() as conn:
            conn.execute(
                """
                UPDATE agentmail_inbox_queue
                SET ack_status = ?, ack_message_id = ?, last_error = ?, updated_at = ?
                WHERE queue_id = ?
                """,
                (ack_status, ack_message_id, last_error, now, queue_id),
            )

    def _claim_queue_item(self, *, queue_id: str, expected_status: str) -> bool:
        now = _iso_now()
        with self._queue_connect() as conn:
            cur = conn.execute(
                """
                UPDATE agentmail_inbox_queue
                SET status = ?, updated_at = ?, last_attempt_at = ?
                WHERE queue_id = ? AND status = ?
                """,
                (_QUEUE_STATUS_DISPATCHING, now, now, queue_id, expected_status),
            )
            return int(cur.rowcount or 0) > 0

    def _complete_queue_item(self, queue_id: str, *, attempts: int) -> None:
        now = _iso_now()
        with self._queue_connect() as conn:
            conn.execute(
                """
                UPDATE agentmail_inbox_queue
                SET status = ?, updated_at = ?, next_attempt_at = NULL, last_error = '', attempt_count = ?
                WHERE queue_id = ?
                """,
                (_QUEUE_STATUS_COMPLETED, now, int(attempts), queue_id),
            )

    def _fail_queue_item(self, queue_id: str, *, error: str, attempts: int) -> None:
        now = _iso_now()
        with self._queue_connect() as conn:
            conn.execute(
                """
                UPDATE agentmail_inbox_queue
                SET status = ?, updated_at = ?, next_attempt_at = NULL, attempt_count = ?, last_error = ?
                WHERE queue_id = ?
                """,
                (_QUEUE_STATUS_FAILED, now, int(attempts), str(error or ""), queue_id),
            )

    def _retry_queue_item(self, queue_id: str, *, error: str, attempts: int) -> None:
        now_ts = time.time()
        base_delay = self._trusted_queue_retry_base_seconds * (2 ** max(0, attempts - 1))
        capped_delay = min(self._trusted_queue_retry_max_seconds, base_delay)
        jitter = random.uniform(0.0, capped_delay * self._trusted_queue_retry_jitter_ratio)
        next_attempt = datetime.fromtimestamp(now_ts + capped_delay + jitter, tz=timezone.utc)
        with self._queue_connect() as conn:
            conn.execute(
                """
                UPDATE agentmail_inbox_queue
                SET status = ?, updated_at = ?, next_attempt_at = ?, attempt_count = ?, last_error = ?
                WHERE queue_id = ?
                """,
                (
                    _QUEUE_STATUS_BUSY_RETRY,
                    _iso_now(),
                    next_attempt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    int(attempts),
                    str(error or ""),
                    queue_id,
                ),
            )

    async def _trusted_inbox_queue_loop(self) -> None:
        while not self._ws_stop_event.is_set():
            try:
                processed = await self._process_due_queue_items(limit=5)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.exception("📧 Trusted inbox queue loop error: %s", exc)
                processed = 0
            if processed > 0:
                continue
            self._queue_wakeup.clear()
            stop_wait = asyncio.create_task(self._ws_stop_event.wait())
            wake_wait = asyncio.create_task(self._queue_wakeup.wait())
            try:
                done, pending = await asyncio.wait(
                    {stop_wait, wake_wait},
                    timeout=self._trusted_queue_poll_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if stop_wait in done and stop_wait.result():
                    return
            finally:
                for task in (stop_wait, wake_wait):
                    if not task.done():
                        task.cancel()

    async def _process_due_queue_items(self, *, limit: int) -> int:
        if not self._dispatch_with_admission_fn:
            return 0
        now = _iso_now()
        with self._queue_connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM agentmail_inbox_queue
                WHERE status IN (?, ?)
                  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (_QUEUE_STATUS_QUEUED, _QUEUE_STATUS_BUSY_RETRY, now, max(1, int(limit))),
            ).fetchall()
        processed = 0
        for row in rows:
            payload = self._queue_row_to_dict(row)
            if not self._claim_queue_item(
                queue_id=str(payload["queue_id"]),
                expected_status=str(payload["status"]),
            ):
                continue
            processed += 1
            attempts = int(payload.get("attempt_count") or 0) + 1
            try:
                result = await self._dispatch_with_admission_fn(dict(payload.get("action_payload") or {}))
            except Exception as exc:
                self._fail_queue_item(str(payload["queue_id"]), error=str(exc), attempts=attempts)
                logger.exception(
                    "📧 Trusted inbox dispatch failed queue_id=%s: %s",
                    payload["queue_id"],
                    exc,
                )
                continue

            decision = str(result.get("decision") or "").strip().lower()
            if decision == "accepted":
                subject = str(payload.get("subject") or "").strip() or "(no subject)"
                sender_email = str(payload.get("sender_email") or "").strip()
                session_id = str(result.get("session_id") or result.get("session_key") or "").strip()
                self._emit_notification(
                    kind="simone_session_started",
                    title="Simone Started Work",
                    message=f"Subject: {subject}" + (f" | From: {sender_email}" if sender_email else ""),
                    severity="info",
                    metadata={
                        "queue_id": str(payload.get("queue_id") or ""),
                        "message_id": str(payload.get("message_id") or ""),
                        "subject": subject,
                        "sender_email": sender_email,
                        "session_id": session_id,
                        "trigger": "trusted_email",
                    },
                )
                self._complete_queue_item(str(payload["queue_id"]), attempts=attempts)
                continue
            if decision in {"busy", "duplicate_in_progress"}:
                reason = str(result.get("reason") or decision)
                self._retry_queue_item(
                    str(payload["queue_id"]),
                    error=reason,
                    attempts=attempts,
                )
                continue
            self._fail_queue_item(
                str(payload["queue_id"]),
                error=str(result.get("error") or result.get("reason") or decision or "dispatch_failed"),
                attempts=attempts,
            )
        return processed

    async def _inbox_poll_loop(self) -> None:
        while not self._ws_stop_event.is_set():
            try:
                await self._poll_inbox_once(limit=25)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("📧 Inbound poll loop failed: %s", exc)
            try:
                await asyncio.wait_for(
                    self._ws_stop_event.wait(),
                    timeout=self._inbound_poll_interval_seconds,
                )
                return
            except asyncio.TimeoutError:
                continue

    async def _poll_inbox_once(self, *, limit: int = 25) -> None:
        self._assert_ready()
        messages = await self._client.inboxes.messages.list(inbox_id=self._inbox_id)
        rows: list[Any] = list(messages.messages if hasattr(messages, "messages") else messages)
        if not rows:
            return

        # Oldest-first processing keeps thread state stable.
        rows = rows[: max(1, int(limit))]
        rows.reverse()
        inbox_sender = _normalize_sender_email(self._inbox_address)

        for msg in rows:
            message_id = str(getattr(msg, "message_id", "") or "").strip()
            if not message_id or self._seen_message_id(message_id):
                continue
            sender_email = _normalize_sender_email(str(getattr(msg, "from_", "") or ""))
            # Ignore self-authored outbound copies in inbox listing.
            if sender_email and inbox_sender and sender_email == inbox_sender:
                self._claim_seen_message_id(message_id)
                continue
            # Filter automated / bounce / DSN emails in the polling path too.
            subject = str(getattr(msg, "subject", "") or "")
            if _is_automated_sender(sender_email, subject):
                logger.debug(
                    "📧 Poll: suppressing automated/bounce email from=%s subject=%r",
                    sender_email, subject,
                )
                self._claim_seen_message_id(message_id)
                continue
            hydrated_msg = msg
            try:
                hydrated_msg = await self._client.inboxes.messages.get(
                    inbox_id=self._inbox_id,
                    message_id=message_id,
                )
            except Exception as exc:
                logger.debug(
                    "📧 Poll hydration failed message_id=%s; using list preview payload (%s)",
                    message_id,
                    exc,
                )
            await self._handle_inbound_email(_InboundMessageEvent(message=hydrated_msg))

    # ------------------------------------------------------------------
    # Ops status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return service status for ops endpoint."""
        queue = self._trusted_queue_overview()
        return {
            "enabled": self._enabled,
            "started": self._started,
            "started_at": self._started_at,
            "inbox_address": self._inbox_address,
            "auto_send": _auto_send(),
            "ws_enabled": _ws_enabled(),
            "ws_connected": self._ws_connected,
            "ws_reconnect_count": self._ws_reconnect_count,
            "ws_last_status_code": self._ws_last_status_code,
            "ws_fail_opened": self._ws_fail_opened,
            "ws_fail_open_after_attempts": self._ws_fail_open_after_attempts,
            "ws_fail_open_status_codes": sorted(self._ws_fail_open_status_codes),
            "inbound_poll_enabled": self._inbound_poll_enabled,
            "inbound_poll_interval_seconds": self._inbound_poll_interval_seconds,
            "messages_sent": self._messages_sent,
            "messages_received": self._messages_received,
            "drafts_created": self._drafts_created,
            "trusted_sender_count": len(self._trusted_senders),
            "trusted_senders": list(self._trusted_senders),
            "trusted_requests_queued_total": queue["queue_depth"],
            "trusted_requests_busy_retry_total": queue["busy_retry_total"],
            "trusted_requests_completed_total": queue["completed_total"],
            "trusted_requests_failed_total": queue["failed_total"],
            "oldest_pending_request_at": queue["oldest_pending_request_at"],
            "last_inbound_trusted_at": self._last_trusted_inbound_at,
            "last_error": self._last_error,
        }

    def _build_inbound_message(
        self,
        *,
        sender: str,
        sender_email: str,
        sender_role: str,
        sender_trusted: bool,
        subject: str,
        thread_id: str,
        message_id: str,
        receiving_inbox: str,
        reply_text: str,
        reply_is_extracted: bool,
        text_body: str,
        attachments: Any = None,
    ) -> str:
        """Build a structured inbound email message for the email-handler agent.

        Matches the format used by the AgentMail webhook transform so the
        email-handler receives a consistent payload regardless of ingest path.
        """
        lines = [
            "Inbound email received in Simone's AgentMail inbox.",
            f"from: {sender}",
            f"sender_email: {sender_email}",
            f"sender_role: {sender_role}",
            f"sender_trusted: {sender_trusted}",
            f"subject: {subject}",
            f"thread_id: {thread_id}",
            f"message_id: {message_id}",
            f"receiving_inbox: {receiving_inbox}",
            f"reply_extracted: {reply_is_extracted}",
            "",
            "--- Reply (new content) ---",
            reply_text[:4000],  # type: ignore[misc]
        ]
        if reply_is_extracted:
            lines.append("")
            lines.append("--- Full Email Body (for reference) ---")
            lines.append(text_body[:4000])  # type: ignore[misc]

        if attachments and isinstance(attachments, (list, tuple)):
            lines.append("")
            lines.append(f"--- Attachments ({len(attachments)}) ---")
            att_list: list[Any] = list(attachments)  # type: ignore[arg-type]
            for att in att_list[:10]:
                if isinstance(att, dict):
                    fname = att.get("filename", "unnamed")
                    fsize = att.get("size", "?")
                    ftype = att.get("content_type", "unknown")
                    lines.append(f"- {fname} ({ftype}, {fsize} bytes)")
                elif hasattr(att, "filename"):
                    lines.append(f"- {getattr(att, 'filename', 'unnamed')} ({getattr(att, 'content_type', 'unknown')}, {getattr(att, 'size', '?')} bytes)")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Post-Triage Lifecycle
    # ------------------------------------------------------------------

    def mark_queue_completed(
        self,
        queue_id: str,
        *,
        session_exit_status: str = "ok",
        classification: str = "",
        reply_sent: bool = False,
    ) -> None:
        """Mark a queue item as completed with lifecycle metadata."""
        try:
            self._ensure_queue_schema()
            now = _iso_now()
            with self._queue_connect() as conn:
                conn.execute(
                    """
                    UPDATE agentmail_inbox_queue
                    SET status = 'completed',
                        completed_at = ?,
                        session_exit_status = ?,
                        classification = ?,
                        reply_sent = ?,
                        updated_at = ?
                    WHERE queue_id = ?
                    """,
                    (now, session_exit_status, classification, int(reply_sent), now, queue_id),
                )
        except Exception as exc:
            logger.warning("📧 mark_queue_completed failed queue_id=%s: %s", queue_id, exc)

    def mark_queue_failed(
        self,
        queue_id: str,
        *,
        error: str = "",
        session_exit_status: str = "crashed",
    ) -> None:
        """Mark a queue item as failed due to session crash or error."""
        try:
            self._ensure_queue_schema()
            now = _iso_now()
            with self._queue_connect() as conn:
                conn.execute(
                    """
                    UPDATE agentmail_inbox_queue
                    SET status = 'failed',
                        completed_at = ?,
                        session_exit_status = ?,
                        last_error = ?,
                        updated_at = ?
                    WHERE queue_id = ?
                    """,
                    (now, session_exit_status, error, now, queue_id),
                )
            self._emit_notification(
                kind="agentmail_processing_failed",
                title="Email Processing Failed",
                message=f"Email handler session crashed for queue_id={queue_id}: {error[:200]}",
                severity="warning",
                metadata={"queue_id": queue_id, "error": error[:500]},
            )
        except Exception as exc:
            logger.warning("📧 mark_queue_failed failed queue_id=%s: %s", queue_id, exc)

    async def check_reply_sent_in_thread(
        self,
        thread_id: str,
        *,
        since_message_id: str = "",
    ) -> bool:
        """Check if an outbound reply exists in a thread since a given message.

        Used for the mandatory reply verification — after Simone processes
        a triage brief, we check if she actually sent a reply to Kevin.
        """
        if not self._client or not self._inbox_id or not thread_id:
            return False
        try:
            messages_resp = await self._client.inboxes.messages.list(
                inbox_id=self._inbox_id,
            )
            all_msgs = getattr(messages_resp, "messages", []) or []
            thread_msgs = [
                m for m in all_msgs
                if getattr(m, "thread_id", "") == thread_id
            ]
            # Check for any outbound message from Simone's inbox in the thread
            inbox_addr = self._inbox_address.lower()
            for msg in thread_msgs:
                from_addr = str(getattr(msg, "from_", "") or "").lower()
                msg_id = str(getattr(msg, "message_id", "") or "")
                # Skip the original ack reply and the inbound message itself
                if msg_id == since_message_id:
                    continue
                if inbox_addr in from_addr and msg_id != since_message_id:
                    return True
            return False
        except Exception as exc:
            logger.debug("📧 check_reply_sent_in_thread failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _assert_ready(self) -> None:
        if not self._enabled or not self._client:
            raise RuntimeError("AgentMail service is not enabled or initialized")
        if not self._inbox_id:
            raise RuntimeError("AgentMail inbox not configured")

    def _emit_notification(
        self,
        *,
        kind: str,
        title: str,
        message: str,
        severity: str = "info",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        if not self._notification_sink:
            return
        try:
            self._notification_sink({
                "kind": kind,
                "title": title,
                "message": message,
                "severity": severity,
                "metadata": metadata or {},
            })
        except Exception:
            logger.exception("📧 Failed emitting notification kind=%s", kind)

    @staticmethod
    def _extract_ws_status_code(exc: Exception) -> Optional[int]:
        match = re.search(r"status_code:\s*(\d+)", str(exc or ""))
        if not match:
            return None
        try:
            return int(match.group(1))
        except Exception:
            return None

    def _seen_message_id(self, message_id: str) -> bool:
        clean_id = str(message_id or "").strip()
        if not clean_id:
            return False
        if clean_id in self._seen_message_id_set:
            return True
        try:
            self._ensure_queue_schema()
            with self._queue_connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM agentmail_seen_messages WHERE message_id = ? LIMIT 1",
                    (clean_id,)
                ).fetchone()
                if row:
                    self._seen_message_id_set.add(clean_id)
                    self._seen_message_ids.append(clean_id)
                    return True
        except Exception as exc:
            logger.warning("📧 SQLite seen_messages check failed: %s", exc)
        return False

    def _claim_seen_message_id(self, message_id: str) -> bool:
        clean_id = str(message_id or "").strip()
        if not clean_id:
            return False
        if self._seen_message_id(clean_id):
            return False
        self._seen_message_ids.append(clean_id)
        self._seen_message_id_set.add(clean_id)
        maxlen = self._seen_message_ids.maxlen
        if maxlen is not None and len(self._seen_message_id_set) > maxlen:
            self._seen_message_id_set = set(self._seen_message_ids)
        try:
            self._ensure_queue_schema()
            with self._queue_connect() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO agentmail_seen_messages (message_id, created_at) VALUES (?, ?)",
                    (clean_id, _iso_now())
                )
        except Exception as exc:
            logger.warning("📧 SQLite seen_messages insert failed: %s", exc)
        return True

    def _release_seen_message_id(self, message_id: str) -> None:
        clean_id = str(message_id or "").strip()
        if not clean_id:
            return
        if clean_id in self._seen_message_id_set:
            self._seen_message_id_set.discard(clean_id)
            self._seen_message_ids = deque(
                (mid for mid in self._seen_message_ids if mid != clean_id),
                maxlen=self._seen_message_ids.maxlen,
            )
        try:
            self._ensure_queue_schema()
            with self._queue_connect() as conn:
                conn.execute(
                    "DELETE FROM agentmail_seen_messages WHERE message_id = ?",
                    (clean_id,)
                )
        except Exception as exc:
            logger.warning("📧 SQLite seen_messages delete failed: %s", exc)


class _InboundMessageEvent:
    def __init__(self, *, message: Any) -> None:
        self.message = message

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
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)


def _extract_reply_text(text_body: str) -> str:
    """Extract only the new reply content, stripping quoted thread history.

    Uses email-reply-parser to identify and remove quoted blocks so the
    email-handler agent only sees the actionable new content.
    Returns the original text if extraction fails or yields nothing.
    """
    if not text_body or not text_body.strip():
        return text_body
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
NotifyFn = Callable[[dict[str, Any]], None]

# Idempotency key for Simone's primary inbox
_INBOX_CLIENT_ID = "ua-simone-primary"


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


class AgentMailService:
    """Async AgentMail service for Simone's email inbox."""

    def __init__(
        self,
        *,
        dispatch_fn: Optional[DispatchFn] = None,
        notification_sink: Optional[NotifyFn] = None,
    ) -> None:
        self._dispatch_fn = dispatch_fn
        self._notification_sink = notification_sink

        # SDK client (lazy init)
        self._client: Any = None
        self._inbox_id: str = ""
        self._inbox_address: str = ""

        # WebSocket listener
        self._ws_task: Optional[asyncio.Task] = None
        self._ws_stop_event = asyncio.Event()

        # Runtime status
        self._enabled = _is_enabled()
        self._started = False
        self._started_at: Optional[str] = None
        self._ws_connected = False
        self._ws_reconnect_count = 0
        self._last_error: str = ""
        self._messages_sent: int = 0
        self._messages_received: int = 0
        self._drafts_created: int = 0

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
        logger.info(
            "📧 AgentMail service started inbox=%s auto_send=%s ws=%s",
            self._inbox_address,
            _auto_send(),
            _ws_enabled(),
        )

        # Start WebSocket listener if enabled
        if _ws_enabled() and self._inbox_id:
            self._ws_task = asyncio.create_task(self._ws_loop())

    async def shutdown(self) -> None:
        self._ws_stop_event.set()
        if self._ws_task:
            try:
                await asyncio.wait_for(self._ws_task, timeout=10)
            except Exception:
                self._ws_task.cancel()
            self._ws_task = None
        self._started = False
        logger.info("📧 AgentMail service stopped")

    # ------------------------------------------------------------------
    # Inbox Management
    # ------------------------------------------------------------------

    async def _ensure_inbox(self) -> None:
        """Resolve existing inbox or create a new one idempotently."""
        configured_address = os.getenv("UA_AGENTMAIL_INBOX_ADDRESS", "").strip()

        if configured_address:
            # Use the configured address directly
            try:
                inbox = await self._client.inboxes.get(inbox_id=configured_address)
                self._inbox_id = configured_address
                self._inbox_address = configured_address
                logger.info("📧 Resolved existing inbox: %s", self._inbox_address)
                return
            except Exception as exc:
                logger.warning(
                    "📧 Configured inbox %s not found (%s), will create new",
                    configured_address,
                    exc,
                )

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
        label: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List recent messages in the inbox."""
        self._assert_ready()
        kwargs: dict[str, Any] = {"inbox_id": self._inbox_id}
        if label:
            kwargs["labels"] = [label]

        messages = await self._client.inboxes.messages.list(**kwargs)
        results = []
        for msg in (messages.messages if hasattr(messages, "messages") else messages)[:limit]:
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
        label: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List threads in the inbox."""
        self._assert_ready()
        kwargs: dict[str, Any] = {"inbox_id": self._inbox_id}
        if label:
            kwargs["labels"] = [label]

        threads = await self._client.inboxes.threads.list(**kwargs)
        results = []
        for thd in (threads.threads if hasattr(threads, "threads") else threads)[:limit]:
            results.append({
                "thread_id": getattr(thd, "thread_id", ""),
                "subject": getattr(thd, "subject", ""),
                "labels": getattr(thd, "labels", []),
                "message_count": getattr(thd, "message_count", 0),
                "created_at": str(getattr(thd, "created_at", "")),
            })
        return results

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
                self._last_error = f"ws_error: {type(exc).__name__}: {exc}"
                logger.warning(
                    "📧 WebSocket disconnected (%s), reconnecting in %.0fs (attempt #%d)",
                    exc, delay, self._ws_reconnect_count,
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

        logger.info("📧 WebSocket connecting to AgentMail for inbox=%s", self._inbox_id)
        async with self._client.websockets.connect() as socket:
            await socket.send_subscribe(
                Subscribe(inbox_ids=[self._inbox_id])
            )
            self._ws_connected = True
            self._last_error = ""
            logger.info("📧 WebSocket connected and subscribed to inbox=%s", self._inbox_id)

            async for event in socket:
                if self._ws_stop_event.is_set():
                    break

                if isinstance(event, MessageReceivedEvent):
                    self._messages_received += 1
                    await self._handle_inbound_email(event)

    async def _handle_inbound_email(self, event: Any) -> None:
        """Process an inbound email event and dispatch through hooks."""
        try:
            msg = event.message
            sender = getattr(msg, "from_", "unknown")
            subject = getattr(msg, "subject", "(no subject)")
            thread_id = getattr(msg, "thread_id", "")
            message_id = getattr(msg, "message_id", "")
            text_body = getattr(msg, "text", "") or ""
            html_body = getattr(msg, "html", "") or ""

            # Extract clean reply content (strips quoted thread history)
            reply_text = _extract_reply_text(text_body)
            reply_is_extracted = reply_text != text_body

            logger.info(
                "📧 Inbound email from=%s subject=%r thread=%s reply_extracted=%s",
                sender, subject, thread_id, reply_is_extracted,
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
                    "subject": subject,
                    "inbox": self._inbox_address,
                },
            )

            # Dispatch through hooks pipeline if dispatch_fn available
            if self._dispatch_fn:
                action_payload = {
                    "kind": "agent",
                    "name": "AgentMailInbound",
                    "session_key": f"agentmail_{thread_id or message_id}",
                    "to": "email-handler",
                    "deliver": True,
                    "message": self._build_inbound_message(
                        sender=sender,
                        subject=subject,
                        thread_id=thread_id,
                        message_id=message_id,
                        reply_text=reply_text,
                        reply_is_extracted=reply_is_extracted,
                        text_body=text_body,
                        attachments=getattr(msg, "attachments", None),
                    ),
                }
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
            logger.exception("📧 Error handling inbound email: %s", exc)

    # ------------------------------------------------------------------
    # Ops status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return service status for ops endpoint."""
        return {
            "enabled": self._enabled,
            "started": self._started,
            "started_at": self._started_at,
            "inbox_address": self._inbox_address,
            "auto_send": _auto_send(),
            "ws_enabled": _ws_enabled(),
            "ws_connected": self._ws_connected,
            "ws_reconnect_count": self._ws_reconnect_count,
            "messages_sent": self._messages_sent,
            "messages_received": self._messages_received,
            "drafts_created": self._drafts_created,
            "last_error": self._last_error,
        }

    def _build_inbound_message(
        self,
        *,
        sender: str,
        subject: str,
        thread_id: str,
        message_id: str,
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
            f"subject: {subject}",
            f"thread_id: {thread_id}",
            f"message_id: {message_id}",
            f"inbox: {self._inbox_address}",
            f"reply_extracted: {reply_is_extracted}",
            "",
            "--- Reply (new content) ---",
            reply_text[:4000],
        ]
        if reply_is_extracted:
            lines.append("")
            lines.append("--- Full Email Body (for reference) ---")
            lines.append(text_body[:4000])

        if attachments and isinstance(attachments, (list, tuple)):
            lines.append("")
            lines.append(f"--- Attachments ({len(attachments)}) ---")
            for att in attachments[:10]:
                if isinstance(att, dict):
                    fname = att.get("filename", "unnamed")
                    fsize = att.get("size", "?")
                    ftype = att.get("content_type", "unknown")
                    lines.append(f"- {fname} ({ftype}, {fsize} bytes)")
                elif hasattr(att, "filename"):
                    lines.append(f"- {getattr(att, 'filename', 'unnamed')} ({getattr(att, 'content_type', 'unknown')}, {getattr(att, 'size', '?')} bytes)")

        return "\n".join(lines)

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

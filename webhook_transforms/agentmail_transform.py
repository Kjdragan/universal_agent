"""AgentMail webhook transform for UA hooks pipeline.

Transforms inbound AgentMail webhook payloads into HookAction dicts
that the hooks_service can dispatch to the email-handler agent.

AgentMail webhook payload structure:
{
  "type": "event",
  "event_type": "message.received",
  "event_id": "evt_...",
  "message": {
    "inbox_id": "...",
    "thread_id": "thd_...",
    "message_id": "msg_...",
    "from": [{"name": "...", "email": "..."}],
    "to": [{"name": "...", "email": "..."}],
    "subject": "...",
    "text": "...",
    "html": "...",
    "labels": [...],
    "attachments": [...],
    "created_at": "..."
  }
}
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from universal_agent.services.agentmail_service import _extract_reply_text

logger = logging.getLogger(__name__)

# Only process these event types (ignore sent/delivered/bounced for now)
_ACTIONABLE_EVENTS = {"message.received"}


def transform(context: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Transform AgentMail webhook payload into a HookAction dict.

    Returns:
        dict — merged into base HookAction
        None — skip this event (not actionable)
    """
    payload = context.get("payload", {})
    if not isinstance(payload, dict):
        return None

    event_type = str(payload.get("event_type", "")).strip()
    if event_type not in _ACTIONABLE_EVENTS:
        logger.info("AgentMail webhook skipped event_type=%s", event_type)
        return None

    message = payload.get("message", {})
    if not isinstance(message, dict):
        return None

    # Extract sender info
    from_list = message.get("from", [])
    if isinstance(from_list, list) and from_list:
        sender_entry = from_list[0]
        sender_email = str(sender_entry.get("email", "")).strip() if isinstance(sender_entry, dict) else str(sender_entry)
        sender_name = str(sender_entry.get("name", "")).strip() if isinstance(sender_entry, dict) else ""
    else:
        sender_email = str(from_list) if from_list else "unknown"
        sender_name = ""

    sender_display = f"{sender_name} <{sender_email}>" if sender_name else sender_email

    inbox_id = str(message.get("inbox_id", "")).strip()
    thread_id = str(message.get("thread_id", "")).strip()
    message_id = str(message.get("message_id", "")).strip()
    subject = str(message.get("subject", "(no subject)")).strip()
    text_body = str(message.get("text", "")).strip()
    reply_text = _extract_reply_text(text_body)
    reply_is_extracted = reply_text != text_body
    event_id = str(payload.get("event_id", "")).strip()

    # Build session key from thread for continuity
    session_key = f"agentmail_{thread_id}" if thread_id else f"agentmail_{message_id}"

    # Build the agent message
    message_lines = [
        "Inbound email received in Simone's AgentMail inbox (via webhook).",
        f"from: {sender_display}",
        f"subject: {subject}",
        f"thread_id: {thread_id}",
        f"message_id: {message_id}",
        f"inbox: {inbox_id}",
        f"event_id: {event_id}",
        f"reply_extracted: {reply_is_extracted}",
        "",
        "--- Reply (new content) ---",
        reply_text[:4000],
    ]

    # Include full body when reply extraction stripped quoted content
    if reply_is_extracted:
        message_lines.append("")
        message_lines.append("--- Full Email Body (for reference) ---")
        message_lines.append(text_body[:4000])

    # Note attachments if present
    attachments = message.get("attachments", [])
    if attachments and isinstance(attachments, list):
        message_lines.append("")
        message_lines.append(f"--- Attachments ({len(attachments)}) ---")
        for att in attachments[:10]:
            if isinstance(att, dict):
                fname = att.get("filename", "unnamed")
                fsize = att.get("size", "?")
                ftype = att.get("content_type", "unknown")
                message_lines.append(f"- {fname} ({ftype}, {fsize} bytes)")

    return {
        "kind": "agent",
        "name": "AgentMailWebhook",
        "session_key": session_key,
        "to": "email-handler",
        "deliver": True,
        "message": "\n".join(message_lines),
    }

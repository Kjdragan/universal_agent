"""Standalone "send an email from Simone" helper for desktop/cron scripts.

A fresh process cannot reach the gateway's initialized ``AgentMailService``
(its client is built in ``startup()``), so this drives the ``AsyncAgentMail``
SDK directly — the same approach as the proven ``_SubprocessMailService`` shim
in ``scripts/cron_artifact_reminders_sweep.py``. Sender identity is the AgentMail
``simone`` inbox (resolved from ``UA_AGENTMAIL_INBOX_ADDRESS`` or the first inbox).

Best-effort: returns a result dict and never raises. Requires
``AGENTMAIL_API_KEY`` (and the secret bootstrap that provides it).
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

# Kevin's priority address. The general UA_*_EMAIL chain currently resolves to a
# bounced outlook address, so the triage/skill-gap mailers default here.
DEFAULT_RECIPIENT = os.getenv("UA_SIMONE_MAIL_RECIPIENT", "kevinjdragan@gmail.com")


def resolve_recipient(override: str = "") -> str:
    return (override or DEFAULT_RECIPIENT).strip() or "kevinjdragan@gmail.com"


async def _send_async(*, to: str, subject: str, text: str, source: str) -> dict[str, Any]:
    try:
        from agentmail import AsyncAgentMail  # type: ignore[import]
    except Exception as exc:  # noqa: BLE001
        return {"status": "skipped", "reason": f"agentmail SDK import failed: {exc}"}
    api_key = (os.getenv("AGENTMAIL_API_KEY") or "").strip()
    if not api_key:
        return {"status": "skipped", "reason": "AGENTMAIL_API_KEY not set"}
    client = AsyncAgentMail(api_key=api_key, timeout=30)
    configured = (
        os.getenv("UA_AGENTMAIL_INBOX_ADDRESS")
        or os.getenv("UA_AGENTMAIL_INBOX_USERNAME")
        or ""
    ).strip()
    try:
        if configured:
            inbox = await client.inboxes.get(inbox_id=configured)
        else:
            listing = await client.inboxes.list()
            inboxes = getattr(listing, "inboxes", None) or getattr(listing, "data", None) or []
            inbox = inboxes[0] if inboxes else None
        if inbox is None:
            return {"status": "skipped", "reason": "no AgentMail inbox"}
        inbox_id = str(getattr(inbox, "inbox_id", None) or getattr(inbox, "id", "") or "")
    except Exception as exc:  # noqa: BLE001
        return {"status": "skipped", "reason": f"inbox resolution failed: {exc}"}

    # Decorative [FYI/PROACTIVE] tagging to match house-style Simone emails.
    subj, body = subject, text
    try:
        from universal_agent.services.email_tags import (
            ActionTag,
            KindTag,
            format_body_header,
            format_tagged_subject,
        )

        subj = format_tagged_subject(ActionTag.FYI, KindTag.PROACTIVE, subject)
        _html_banner, text_banner = format_body_header(
            ActionTag.FYI, KindTag.PROACTIVE, source, related=None,
        )
        body = text_banner + text
    except Exception:  # noqa: BLE001 — tagging is decorative
        subj, body = subject, text

    try:
        msg = await client.inboxes.messages.send(
            inbox_id=inbox_id, to=to, subject=subj, text=body,
        )
        return {
            "status": "sent",
            "message_id": str(getattr(msg, "message_id", "") or ""),
            "thread_id": str(getattr(msg, "thread_id", "") or ""),
            "inbox": inbox_id,
            "to": to,
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "reason": str(exc), "to": to}


def send_simone_email(*, to: str = "", subject: str, text: str, source: str = "ua") -> dict[str, Any]:
    """Synchronous wrapper. Sends from the Simone inbox; never raises."""
    recipient = resolve_recipient(to)
    try:
        return asyncio.run(_send_async(to=recipient, subject=subject, text=text, source=source))
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "reason": str(exc), "to": recipient}

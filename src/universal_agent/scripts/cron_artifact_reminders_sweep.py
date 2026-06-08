"""Cron-driven entry point for the artifact reminder sweep.

Invoked by the gateway-registered ``cron_artifact_reminders_sweep``
system cron (default every 30 minutes during 6 AM – 10 PM Houston).
Calls ``cron_artifact_reminders.sweep_pending_artifact_reminders``
and exits 0 on success.

Design notes
------------

The original (2026-05-24 / PR #446) version tried to share the
gateway's ``_agentmail_service`` singleton via
``getattr(gateway_server, "_agentmail_service", None)``. That always
returned ``None`` because:

  1. The gateway runs as ``python -m universal_agent.gateway_server``,
     so its live module is ``sys.modules['__main__']``.
  2. The cron-service ``!script`` runner spawns this file as a FRESH
     Python subprocess. The subprocess's import path loads the gateway
     under the qualified name ``universal_agent.gateway_server`` — a
     SEPARATE module copy whose ``_agentmail_service`` is the pristine
     ``None`` declaration.

So we have to use a send path that does not depend on the parent
gateway's in-memory state. This module wraps ``AsyncAgentMail``
directly to give the reminder sweep an interface-compatible mail
service shim it can call.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Send-only mail service shim ────────────────────────────────────────


class _SubprocessMailService:
    """Minimal interface matching ``AgentMailService.send_email`` that the
    reminder sweep depends on.

    Initialised lazily; resolves the inbox on first send by hitting the
    AgentMail control plane. Reuses the same ``AsyncAgentMail`` SDK
    client the parent gateway uses, so deliverability behaviour matches.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._inbox_id: Optional[str] = None
        self._inbox_address: Optional[str] = None

    async def _ensure_ready(self) -> bool:
        if self._client is not None and self._inbox_id:
            return True
        try:
            from agentmail import AsyncAgentMail  # type: ignore[import]
        except ImportError:
            logger.warning(
                "cron_artifact_reminders_sweep: agentmail SDK not installed; "
                "cannot send reminder emails."
            )
            return False

        api_key = (os.getenv("AGENTMAIL_API_KEY") or "").strip()
        if not api_key:
            logger.warning(
                "cron_artifact_reminders_sweep: AGENTMAIL_API_KEY not set; "
                "cannot send reminder emails."
            )
            return False

        self._client = AsyncAgentMail(api_key=api_key, timeout=30)
        configured = (os.getenv("UA_AGENTMAIL_INBOX_ADDRESS") or "").strip()
        try:
            if configured:
                inbox = await self._client.inboxes.get(inbox_id=configured)
            else:
                # No address pinned — resolve via the list endpoint and
                # pick the first inbox. Operator typically has a single
                # inbox (Simone's), so this is unambiguous in practice.
                listing = await self._client.inboxes.list()
                inboxes = getattr(listing, "inboxes", None) or getattr(listing, "data", None) or []
                if not inboxes:
                    logger.warning(
                        "cron_artifact_reminders_sweep: AgentMail has no inboxes; "
                        "cannot send reminder emails."
                    )
                    return False
                inbox = inboxes[0]
            self._inbox_id = str(getattr(inbox, "inbox_id", None) or getattr(inbox, "id", "") or "")
            self._inbox_address = str(getattr(inbox, "address", "") or self._inbox_id)
            return bool(self._inbox_id)
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning(
                "cron_artifact_reminders_sweep: inbox resolution failed: %s", exc
            )
            return False

    async def send_email(
        self,
        *,
        to: str,
        subject: str,
        text: str,
        html: Optional[str] = None,
        force_send: bool = True,
        require_approval: bool = False,
        action: Any = None,
        kind: Any = None,
        source: Optional[str] = None,
        related: Any = None,
        attachments: Optional[list[dict[str, Any]]] = None,
        labels: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Send the email via the AgentMail SDK and return a result dict
        with ``status``, ``message_id``, ``thread_id`` to match the
        gateway's ``AgentMailService.send_email`` shape (reminder code
        reads ``message_id`` / ``thread_id`` from this dict)."""
        if not await self._ensure_ready():
            return {"status": "skipped", "message_id": "", "thread_id": ""}

        # Apply [ACTION/KIND] subject prefix + banner if both tags are
        # provided, mirroring AgentMailService.send_email so categorised
        # reminders look identical to those from the parent gateway.
        try:
            if action is not None and kind is not None:
                from universal_agent.services.email_tags import (
                    format_body_header,
                    format_tagged_subject,
                )

                subject = format_tagged_subject(action, kind, subject)
                html_banner, text_banner = format_body_header(
                    action, kind, source or "", related=related,
                )
                text = text_banner + (text or "")
                if html:
                    html = html_banner + html
        except Exception:  # noqa: BLE001 — formatting is decorative
            pass

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

        try:
            msg = await self._client.inboxes.messages.send(**kwargs)
            message_id = str(getattr(msg, "message_id", "") or "")
            thread_id = str(getattr(msg, "thread_id", "") or "")
            logger.info(
                "cron_artifact_reminders_sweep: sent email to=%s subject=%r message_id=%s",
                to, subject, message_id,
            )
            return {
                "status": "sent",
                "message_id": message_id,
                "thread_id": thread_id,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "cron_artifact_reminders_sweep: send_email failed: %s", exc
            )
            return {"status": "failed", "message_id": "", "thread_id": "", "error": str(exc)}


# ── Cron entry point ───────────────────────────────────────────────────


async def _async_main() -> int:
    # Lazy imports so a partial install doesn't crash before logging is configured.
    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
    from universal_agent.services.cron_artifact_reminders import (
        sweep_pending_artifact_reminders,
    )

    # Bootstrap Infisical secrets for standalone systemd execution (in-process path already has env).
    try:
        from universal_agent.infisical_loader import initialize_runtime_secrets

        initialize_runtime_secrets()
    except Exception as exc:  # noqa: BLE001 — best-effort; in-process path already has env
        logger.warning("cron_artifact_reminders_sweep: secrets bootstrap skipped: %s", exc)

    mail_service = _SubprocessMailService()

    # Recipient resolver (replicates gateway_server._proactive_review_recipient).
    recipient = (
        os.getenv("UA_PROACTIVE_REVIEW_EMAIL", "").strip()
        or os.getenv("UA_MORNING_REPORT_EMAIL", "").strip()
        or os.getenv("UA_PRIMARY_EMAIL", "").strip()
        or os.getenv("UA_NOTIFICATION_EMAIL", "").strip()
        or "kevinjdragan@gmail.com"
    )
    dashboard_base_url = (
        os.getenv("FRONTEND_URL", "")
        or os.getenv("UA_PUBLIC_BASE_URL", "")
        or "https://app.clearspringcg.com"
    )

    conn = connect_runtime_db(get_activity_db_path())
    try:
        report = await sweep_pending_artifact_reminders(
            conn=conn,
            mail_service=mail_service,
            recipient=recipient,
            dashboard_base_url=dashboard_base_url,
        )
    finally:
        conn.close()

    logger.info("cron_artifact_reminders_sweep report: %s", report)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return asyncio.run(_async_main())


if __name__ == "__main__":
    sys.exit(main())

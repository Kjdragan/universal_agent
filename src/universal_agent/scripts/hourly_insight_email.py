"""Cron entrypoint for the hourly insight-delivery email.

Replaces Simone's per-brief email loop. Runs at the top of every hour during
the active window (6 AM – 10 PM Houston). When briefs were generated in the
preceding hour, scores them via :mod:`services.hourly_insight_email`, emits a
single ``[Hourly Intel]`` email from Simone's inbox to the operator, and
stamps ``delivered_at`` on the surfaced artifacts so they aren't re-delivered.

Kill switch: ``UA_INSIGHT_HOURLY_EMAIL_ENABLED=0`` causes briefs to still be
generated but no email goes out. This is the structural lever that replaces
"email Simone to pause" — flipping the env var (or letting the cron registration
notice it) is the canonical way to silence hourly delivery without affecting
upstream convergence detection.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.services import proactive_artifacts as _pa
from universal_agent.services.email_tags import ActionTag, KindTag
from universal_agent.services.hourly_insight_email import compose_hourly_email

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.getenv("UA_INSIGHT_HOURLY_EMAIL_ENABLED", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _recipient() -> str:
    # Default matches the operator's preferred outbound (per MEMORY.md the
    # outlook address is quarantine-prone; gmail is the reliable channel).
    return (os.getenv("UA_INSIGHT_HOURLY_EMAIL_RECIPIENT") or "kevinjdragan@gmail.com").strip()


async def main() -> None:
    """Compose and send the hourly insight email, then stamp delivered_at."""
    logging.basicConfig(level=logging.INFO)
    initialize_runtime_secrets(profile="local_workstation")

    conn = connect_runtime_db(get_activity_db_path())
    try:
        _pa.ensure_schema(conn)
        payload = compose_hourly_email(conn, hour_window_hours=1)
        if payload is None:
            logger.info("hourly_insight_email: no candidates this hour — skipping send.")
            return

        if not _enabled():
            logger.info(
                "hourly_insight_email: UA_INSIGHT_HOURLY_EMAIL_ENABLED=0 — "
                "composed payload but not sending. %d candidate(s) considered.",
                len(payload.get("considered") or []),
            )
            return

        from universal_agent.services.agentmail_service import AgentMailService

        mail = AgentMailService()
        await mail.startup()
        try:
            if not getattr(mail, "_started", False):
                logger.error(
                    "hourly_insight_email: AgentMail failed to start — falling back to log dump."
                )
                logger.info("Subject: %s", payload["subject"])
                logger.info("Text:\n%s", payload["text"])
                return
            result = await mail.send_email(
                to=_recipient(),
                subject=payload["subject"],
                text=payload["text"],
                html=payload["html"],
                force_send=True,
                require_approval=False,
                action=ActionTag.FYI,
                kind=KindTag.PROACTIVE,
                source="hourly_insight_email cron",
            )
            logger.info("hourly_insight_email: sent (%s)", (result or {}).get("status") or "ok")
        finally:
            try:
                await mail.shutdown()
            except Exception:  # noqa: BLE001
                pass

        # Mark surfaced artifacts as delivered so they aren't re-considered next
        # hour. We only stamp the briefs that actually went out — the
        # sub-threshold filler does count, since it was sent.
        for slot_key in ("insight_1", "insight_2"):
            pick = payload.get(slot_key)
            if not pick:
                continue
            artifact_id = str(pick["artifact"].get("artifact_id") or "").strip()
            if not artifact_id:
                continue
            try:
                _pa.mark_artifact_delivered(conn, artifact_id=artifact_id)
            except KeyError:
                logger.warning(
                    "hourly_insight_email: artifact %s vanished between score and stamp",
                    artifact_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "hourly_insight_email: failed to stamp delivered_at on %s: %s",
                    artifact_id,
                    exc,
                )
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        logger.exception("hourly_insight_email failed: %s", exc)
        sys.exit(1)

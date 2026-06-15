"""Deterministic cron entrypoint for the hourly intel digest.

This is the LLM-independent delivery path for the convergence-brief digest.
It does exactly what Simone's ``/hourly-intel-digest`` skill does — compose the
payload, send one collated email from the Simone inbox, stamp the briefs
delivered — but as a plain Python cron so delivery no longer depends on the
heartbeat LLM actually choosing to invoke the skill (which silently stopped
after 2026-05-30; see project memory). The heartbeat directive can stay as a
harmless backup: ``compose_send_payload`` is idempotent per clock hour
(``is_throttled`` + ``delivered_at IS NULL``), so whichever path fires first
sends and the other sees ``throttled``.

Schedule: ``0 6-21 * * *`` America/Chicago (top of every active-window hour —
content-generation work, respects the dormancy default).

Kill switch: ``UA_INTEL_DIGEST_CRON_ENABLED=0`` composes the payload (so
near-duplicate supersede bookkeeping still runs) but sends nothing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.services import proactive_artifacts as _pa
from universal_agent.services.agentmail_service import AgentMailService
from universal_agent.services.dormancy import should_run
from universal_agent.services.email_tags import ActionTag, KindTag
from universal_agent.services.hourly_intel_digest import (
    compose_send_payload,
    mark_all_delivered,
)
from universal_agent.services.proactive_artifacts import record_email_delivery

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.getenv("UA_INTEL_DIGEST_CRON_ENABLED", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


async def run_once(conn) -> str:  # noqa: ANN001 — sqlite3.Connection
    """Compose, (maybe) send the digest, and stamp delivered. Returns a status.

    Returns one of: ``paused`` / ``throttled`` / ``no_candidates`` (nothing to
    send), ``disabled`` (kill-switch off), ``sent`` (emailed + stamped), or
    ``send_failed`` (compose was ready but AgentMail raised — artifacts stay
    eligible for the next hour).
    """
    payload = compose_send_payload(conn)
    status = str((payload or {}).get("status") or "no_candidates")
    if status != "ready":
        logger.info("hourly_intel_digest_cron: nothing to send (status=%s)", status)
        return status

    artifact_ids = list(payload.get("artifact_ids") or [])
    if not _enabled():
        logger.info(
            "hourly_intel_digest_cron: UA_INTEL_DIGEST_CRON_ENABLED=0 — composed "
            "%d brief(s) but not sending.",
            len(artifact_ids),
        )
        return "disabled"

    mail = AgentMailService()
    await mail.startup()
    try:
        if not getattr(mail, "_started", False):
            logger.error(
                "hourly_intel_digest_cron: AgentMail failed to start — not sending. "
                "Subject: %s",
                payload.get("subject"),
            )
            return "send_failed"
        result = await mail.send_email(
            to=payload["recipient"],
            subject=payload["subject"],
            text=payload["text"],
            html=payload.get("html"),
            force_send=True,
            require_approval=False,
            action=ActionTag.FYI,
            kind=KindTag.PROACTIVE,
            source="hourly_intel_digest cron",
        )
    except Exception as exc:  # noqa: BLE001 — never crash the cron; stay eligible
        logger.exception("hourly_intel_digest_cron: AgentMail send failed: %s", exc)
        return "send_failed"
    finally:
        try:
            await mail.shutdown()
        except Exception:  # noqa: BLE001
            pass

    message_id = str((result or {}).get("message_id") or "")
    # Stamp delivered FIRST (the throttle/dedup gate), then best-effort per-artifact
    # email-delivery records for the dashboard history.
    mark_all_delivered(conn, artifact_ids)
    for artifact_id in artifact_ids:
        try:
            record_email_delivery(
                conn,
                artifact_id=artifact_id,
                message_id=message_id,
                subject=payload.get("subject", ""),
                recipient=payload.get("recipient", ""),
                metadata={"channel": "hourly_digest", "source": "hourly_intel_digest cron"},
            )
        except KeyError:
            logger.warning(
                "hourly_intel_digest_cron: artifact %s vanished before email-record",
                artifact_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "hourly_intel_digest_cron: failed to record email delivery for %s: %s",
                artifact_id,
                exc,
            )

    logger.info(
        "hourly_intel_digest_cron: sent digest (%d brief(s), message_id=%s)",
        len(artifact_ids),
        message_id or "n/a",
    )
    return "sent"


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # Runtime dormancy gate. This is an interval (hourly) job: the systemd timer
    # fires every hour (00..23) so this gate decides per-run. Default (env unset)
    # stays windowed — set UA_INTEL_DIGEST_24_7=true in Infisical to run 24/7.
    # Gate BEFORE initialize_runtime_secrets() so the overnight skip costs no
    # Infisical round-trip.
    run_24_7 = str(os.environ.get("UA_INTEL_DIGEST_24_7", "")).strip().lower() in {
        "1", "true", "yes", "on",
    }
    if not should_run(mode="always" if run_24_7 else "dormancy_aware"):
        logger.info(
            "hourly_intel_digest_cron: dormant window, skipping "
            "(set UA_INTEL_DIGEST_24_7=true to run 24/7)"
        )
        return
    # One-shot subprocess: make sure the Infisical-backed secrets (AgentMail API
    # key, LLM keys, etc.) are present before we stand up the mailer. Use NO
    # hardcoded profile so UA_DEPLOYMENT_PROFILE is honored: under the systemd
    # unit it is `vps` -> strict Infisical production load (a hardcoded
    # profile="local_workstation" would override that backstop and silently run
    # keyless under systemd). Dev leaves the var unset -> local_workstation, so
    # dev behavior is unchanged.
    initialize_runtime_secrets()
    conn = connect_runtime_db(get_activity_db_path())
    try:
        _pa.ensure_schema(conn)
        await run_once(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001
        logger.exception("hourly_intel_digest_cron failed: %s", exc)
        sys.exit(1)

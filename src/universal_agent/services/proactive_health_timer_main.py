"""Standalone entrypoint for the deploy-independent proactive_health watchdog.

S5 Phase C (ADR ``project_docs/06_platform/08_scheduling_substrate_adr.md``,
Decision 3). The ~19 proactive_health invariant probes used to be computed
*inside the heartbeat tick* (``heartbeat_service._run_heartbeat`` →
``run_pre_flight_check``). The heartbeat skips on lock / retry-not-due /
not-scheduled / dormancy / no-targets / empty-directive — and on any skipped
tick health was neither computed nor delivered. So no health report reliably
reached the operator.

This module moves the compute to a deterministic, LLM-free systemd oneshot
(``universal-agent-proactive-health.service``, fired by
``universal-agent-proactive-health.timer`` every 10 min with ``OnCalendar`` +
``Persistent=true`` so it rides through the frequent deploy restarts and
replays a slot missed inside a deploy window). It:

1. Bootstraps Infisical runtime secrets FIRST — the mailer keys, Infisical
   creds and DB env all come from this; a fresh subprocess inherits none. Then
   uses function-local imports so any import-time env reads in the work modules
   see the loaded secrets.
2. Computes the proactive_health payload (same builder + call shape as the live
   ``GET /api/v1/ops/proactive_health`` endpoint).
3. Writes the DURABLE cross-process snapshot (a singleton row in
   ``activity_state.db`` + a fixed-path JSON mirror) — replacing the ephemeral
   per-heartbeat-workspace sidecar the timer could never share. The heartbeat
   now READS this snapshot for Simone's prompt instead of recomputing.
4. If there are critical findings, sends ONE digest email — 6h cooldown keyed
   on the durable snapshot's finding-set fingerprint (a fresh oneshot has no
   in-memory ``_notifications`` cache, so the cooldown MUST be durable).
5. Exits 0 (a oneshot must terminate cleanly for systemd).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)


async def _run() -> int:
    # (1) Secrets FIRST. The mailer (AgentMail/gws), Infisical and DB env all
    # come from this call; without it the digest is silently undeliverable and
    # key-dependent invariants misread. Function-local import keeps this the
    # very first thing that touches universal_agent runtime config.
    try:
        from universal_agent.infisical_loader import initialize_runtime_secrets

        initialize_runtime_secrets()
        logger.info("Infisical runtime secrets loaded for proactive_health timer")
    except Exception as exc:  # noqa: BLE001 — never block the run on bootstrap
        logger.warning("Infisical secret bootstrap skipped: %s", exc)

    # (2) Function-local imports AFTER the secret bootstrap.
    from datetime import datetime, timezone
    from pathlib import Path

    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
    from universal_agent.services import (
        proactive_health_notifier as notifier,
        proactive_health_snapshot as snap,
    )
    from universal_agent.services.proactive_health import build_proactive_health_payload

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    activity_path = get_activity_db_path()
    workspaces_dir = Path(activity_path).parent
    # The fresh oneshot has no in-memory CronService, so Layer-1 cron staleness
    # is read from the persistence file (cron_jobs.json sits in the
    # AGENT_RUN_WORKSPACES root alongside the activity DB). Same fallback the
    # heartbeat pre-flight used. CSI path mirrors gateway_server._csi_default_db_path.
    cron_persistence_path = workspaces_dir / "cron_jobs.json"
    csi_db_path = Path(os.getenv("CSI_DB_PATH", "/var/lib/universal-agent/csi/csi.db"))

    sent = False
    conn = connect_runtime_db(activity_path)
    try:
        try:
            payload = build_proactive_health_payload(
                activity_conn=conn,
                cron_jobs=None,
                csi_db_path=csi_db_path,
                cron_persistence_path=cron_persistence_path,
            )
        except Exception as exc:  # noqa: BLE001 — degrade, never crash the timer
            logger.warning(
                "proactive_health timer: payload build failed", exc_info=True
            )
            payload = {
                "overall_status": "warn",
                "generated_at_utc": now_iso,
                "crons": [],
                "stale_tasks": {"count": 0, "samples": []},
                "parked_tasks": {"count": 0, "samples": []},
                "invariants": [],
                "error": f"payload_build_failed: {type(exc).__name__}: {exc}",
            }

        invariants = payload.get("invariants") or []
        criticals = [
            f for f in invariants if str(f.get("severity") or "").lower() == "critical"
        ]
        fingerprint = snap.compute_finding_fingerprint(criticals)

        # Cooldown decision against the DURABLE snapshot (a oneshot has no
        # in-memory notifications cache). Re-fire only when the finding-SET
        # changes or the 6h window has elapsed.
        prev = snap.read_latest_snapshot(conn)
        cooldown = notifier._cooldown_seconds()
        should_send = bool(criticals) and notifier._email_enabled()
        if should_send and prev is not None:
            last_fp = prev.get("last_digest_fingerprint")
            last_sent = notifier._parse_iso_timestamp(
                prev.get("last_digest_sent_at_utc")
            )
            if (
                last_fp == fingerprint
                and last_sent is not None
                and (now.timestamp() - last_sent) < cooldown
            ):
                should_send = False
                logger.info(
                    "proactive_health timer: digest suppressed (same finding-set "
                    "within %ds cooldown)",
                    cooldown,
                )

        if should_send:
            result = await notifier.send_critical_digest(
                criticals=criticals,
                generated_at=str(payload.get("generated_at_utc") or now_iso),
            )
            sent = bool(result.get("sent"))
            if not sent:
                logger.warning(
                    "proactive_health timer: digest NOT sent (%s)",
                    result.get("reason"),
                )

        # (3) Durable snapshot — written EVERY run. Stamp the digest cooldown
        # fields ONLY when we actually sent; otherwise write_snapshot preserves
        # the prior cooldown state via COALESCE so the 6h window keeps ticking.
        try:
            snap.write_snapshot(
                conn,
                payload=payload,
                updated_at_utc=now_iso,
                digest_fingerprint=fingerprint if sent else None,
                digest_sent_at_utc=now_iso if sent else None,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "proactive_health timer: snapshot write failed", exc_info=True
            )
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    # (4) Fixed-path JSON mirror for the dashboard / quick eyeballing.
    # Best-effort; the DB row is the canonical cross-process source.
    try:
        mirror_dir = workspaces_dir / "proactive_health"
        mirror_dir.mkdir(parents=True, exist_ok=True)
        (mirror_dir / "latest.json").write_text(
            json.dumps(payload, indent=2, default=str)
        )
    except Exception:  # noqa: BLE001
        logger.debug("proactive_health timer: JSON mirror write failed", exc_info=True)

    crit_n, warn_n = snap.count_by_severity(payload)
    logger.info(
        "proactive_health timer: run complete (overall=%s, criticals=%d, "
        "warns=%d, digest_sent=%s)",
        payload.get("overall_status"),
        crit_n,
        warn_n,
        sent,
    )
    return 0


def main() -> int:
    logging.basicConfig(
        level=os.getenv("UA_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        return asyncio.run(_run())
    except Exception:  # noqa: BLE001 — a oneshot must exit cleanly for systemd
        logger.error("proactive_health timer: fatal error", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

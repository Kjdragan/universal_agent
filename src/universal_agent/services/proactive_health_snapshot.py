"""Durable, cross-process snapshot store for proactive_health.

S5 Phase C (ADR ``project_docs/06_platform/08_scheduling_substrate_adr.md``,
Decision 3). The proactive_health invariants used to be computed *inside the
heartbeat tick* and written to a per-run sidecar
(``run_daemon_simone_heartbeat_<ts>/work_products/proactive_health_latest.json``).
That sidecar is ephemeral — it lives in the heartbeat's workspace, which is
deleted on restart and is a *different process* than the deploy-independent
systemd timer (``universal-agent-proactive-health.service``) that now owns the
compute. So the timer and the heartbeat need a fixed, shared location both
resolve identically.

This module provides that store: a singleton ``proactive_health_snapshots`` row
(``id = 1``, last-write-wins) in ``activity_state.db`` (resolved via
``durable.db.get_activity_db_path`` — the same root the gateway / heartbeat
already use). The timer ``write_snapshot``s every run; the heartbeat
``read_latest_snapshot``s a cheap copy for Simone's prompt without recomputing.

The row also carries the digest-email cooldown state
(``last_digest_fingerprint`` / ``last_digest_sent_at_utc``) so the timer's 6h
"don't re-spam the same finding-set" rule survives a process restart — the
in-memory ``_notifications`` cache the in-process notifier used for cooldown
does not exist in a fresh oneshot subprocess.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

# Singleton primary key — there is exactly one "latest" snapshot row.
LATEST_ROW_ID = 1

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS proactive_health_snapshots (
    id                      INTEGER PRIMARY KEY,
    generated_at_utc        TEXT,
    updated_at_utc          TEXT NOT NULL,
    overall_status          TEXT,
    critical_count          INTEGER NOT NULL DEFAULT 0,
    warn_count              INTEGER NOT NULL DEFAULT 0,
    payload_json            TEXT NOT NULL,
    last_digest_fingerprint TEXT,
    last_digest_sent_at_utc TEXT
)
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the snapshot table if it doesn't exist. Idempotent.

    Owned by the writer (the timer entrypoint). Readers tolerate a missing
    table (return None) so they never DDL on a hot path.
    """
    conn.execute(_SCHEMA_DDL)


def count_by_severity(payload: Dict[str, Any]) -> tuple[int, int]:
    """Return (critical_count, warn_count) over the payload's invariants."""
    crit = warn = 0
    for finding in payload.get("invariants") or ():
        sev = str(finding.get("severity") or "").lower()
        if sev == "critical":
            crit += 1
        elif sev == "warn":
            warn += 1
    return crit, warn


def compute_finding_fingerprint(criticals: Iterable[Dict[str, Any]]) -> str:
    """Stable fingerprint of a critical finding-SET (order-independent).

    Keyed on ``finding_id`` (falling back to ``metric_key``) so the digest
    cooldown re-fires only when the *set* of criticals changes, matching the
    per-finding dedup key shape used by the in-process notifier.
    """
    ids = sorted(
        str(f.get("finding_id") or f.get("metric_key") or "unknown") for f in criticals
    )
    return "|".join(ids)


def write_snapshot(
    conn: sqlite3.Connection,
    *,
    payload: Dict[str, Any],
    updated_at_utc: str,
    digest_fingerprint: Optional[str] = None,
    digest_sent_at_utc: Optional[str] = None,
) -> None:
    """Upsert the singleton latest-snapshot row.

    The digest columns are only overwritten when a value is provided; passing
    ``None`` (the common "didn't send a digest this run" case) PRESERVES the
    prior cooldown state via ``COALESCE`` so the 6h window keeps ticking from
    the original send.
    """
    ensure_schema(conn)
    crit, warn = count_by_severity(payload)
    conn.execute(
        """
        INSERT INTO proactive_health_snapshots
            (id, generated_at_utc, updated_at_utc, overall_status,
             critical_count, warn_count, payload_json,
             last_digest_fingerprint, last_digest_sent_at_utc)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            generated_at_utc        = excluded.generated_at_utc,
            updated_at_utc          = excluded.updated_at_utc,
            overall_status          = excluded.overall_status,
            critical_count          = excluded.critical_count,
            warn_count              = excluded.warn_count,
            payload_json            = excluded.payload_json,
            last_digest_fingerprint = COALESCE(
                excluded.last_digest_fingerprint,
                proactive_health_snapshots.last_digest_fingerprint),
            last_digest_sent_at_utc = COALESCE(
                excluded.last_digest_sent_at_utc,
                proactive_health_snapshots.last_digest_sent_at_utc)
        """,
        (
            LATEST_ROW_ID,
            str(payload.get("generated_at_utc") or ""),
            updated_at_utc,
            str(payload.get("overall_status") or ""),
            crit,
            warn,
            json.dumps(payload, default=str),
            digest_fingerprint,
            digest_sent_at_utc,
        ),
    )


def read_latest_snapshot(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    """Read the singleton latest-snapshot row, or None if none / table absent.

    Reader-safe: a missing table (timer hasn't run yet) returns None rather
    than raising, and the ``payload`` field is the parsed dict.
    """
    try:
        cur = conn.execute(
            """
            SELECT generated_at_utc, updated_at_utc, overall_status,
                   critical_count, warn_count, payload_json,
                   last_digest_fingerprint, last_digest_sent_at_utc
            FROM proactive_health_snapshots
            WHERE id = ?
            """,
            (LATEST_ROW_ID,),
        )
        row = cur.fetchone()
    except sqlite3.OperationalError:
        # No such table — timer has never written. Treat as "no snapshot".
        return None
    if row is None:
        return None
    # Support both sqlite3.Row and plain-tuple connections.
    try:
        keys = row.keys()  # type: ignore[attr-defined]
        data = {k: row[k] for k in keys}
    except AttributeError:
        (
            generated_at_utc,
            updated_at_utc,
            overall_status,
            critical_count,
            warn_count,
            payload_json,
            last_digest_fingerprint,
            last_digest_sent_at_utc,
        ) = row
        data = {
            "generated_at_utc": generated_at_utc,
            "updated_at_utc": updated_at_utc,
            "overall_status": overall_status,
            "critical_count": critical_count,
            "warn_count": warn_count,
            "payload_json": payload_json,
            "last_digest_fingerprint": last_digest_fingerprint,
            "last_digest_sent_at_utc": last_digest_sent_at_utc,
        }
    try:
        data["payload"] = json.loads(data.get("payload_json") or "{}")
    except (ValueError, TypeError):
        data["payload"] = {}
    return data

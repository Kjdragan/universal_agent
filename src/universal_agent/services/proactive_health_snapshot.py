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

This module also owns the operator-acknowledgement table
(``proactive_health_acks`` — suppress-until-recovered with hysteresis; see the
"Acknowledgements" section below), which lives in the same ``activity_state.db``.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import sqlite3
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

# Singleton primary key — there is exactly one "latest" snapshot row.
LATEST_ROW_ID = 1

# Acknowledgement hysteresis: an acked finding must stay GREEN this long
# (continuously — any red touch resets the clock via last_red_at_utc) before
# the ack flips to 'recovered' and re-arms alerting. Criticals flap on a
# minutes scale, so a brief green dip must NOT count as recovery.
DEFAULT_ACK_RECOVERY_SECONDS = 21600  # 6h
# Max-lifetime backstop: an ack can never outlive this, even while still red,
# so a forgotten click can't mute a finding forever.
ACK_MAX_LIFETIME_DAYS = 30

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

    Keyed on ``finding_id`` (falling back to ``metric_key``).

    NB: retained for payload introspection / back-compat. It is NO LONGER the
    digest dedup key — keying the cooldown on exact-set equality let a *flapping*
    invariant (one that enters/leaves the critical set faster than the cooldown)
    re-fire the digest on every membership change. ``decide_digest`` is the
    dedup key now; see its docstring.
    """
    ids = sorted(
        str(f.get("finding_id") or f.get("metric_key") or "unknown") for f in criticals
    )
    return "|".join(ids)


def _split_ids(fingerprint: Optional[str]) -> set:
    """Parse a pipe-joined finding-id fingerprint into a set of ids."""
    return {part for part in (fingerprint or "").split("|") if part}


def _join_ids(ids: Iterable[str]) -> str:
    """Render finding-ids as a stable, order-independent fingerprint string."""
    return "|".join(sorted(set(ids)))


def decide_digest(
    *,
    current_finding_ids: Iterable[str],
    prev_fingerprint: Optional[str],
    last_sent_ts: Optional[float],
    now_ts: float,
    cooldown_seconds: float,
    excluded_ids: Iterable[str] = (),
) -> tuple[bool, str]:
    """Decide whether to send a critical digest, and the alerted-set to persist.

    The dedup key is the *cumulative set of critical finding-ids already alerted
    within the active cooldown window* — NOT the exact set of currently-critical
    ids. Email iff a currently-critical id has not yet been alerted this window.

    This makes the gate robust to a *flapping* invariant (one entering/leaving
    the critical set faster than the cooldown):

    - a critical *disappearing* shrinks the live set, still a subset of the
      alerted set → no email;
    - a critical *re-appearing* is an id already alerted this window → no email;
    - a *genuinely-new* critical (an id not in the alerted set) → email on the
      very next tick, never waiting out the window;
    - a still-firing critical after the window lapses → re-nudge at most once per
      cooldown.

    Window semantics (sliding anchor): ``last_sent_ts`` anchors the active
    window. While it is within ``cooldown_seconds`` of ``now_ts`` the
    previously-alerted set is carried forward; once it lapses the alerted set
    resets. On a real send the caller stamps ``last_digest_sent_at_utc = now``,
    sliding the window forward from the last thing the operator was actually
    told.

    ``excluded_ids`` (the operator-ACKNOWLEDGED finding-ids) are stripped from
    the carried-forward alerted set so an acked id never lingers in (or
    re-enters) the fingerprint: when its ack later recovers and the finding
    re-reds, it counts as a genuinely-new id and alerts immediately even if a
    pre-ack send stamped it into ``last_digest_fingerprint`` within a
    still-live window. Callers also keep acked ids OUT of
    ``current_finding_ids`` (the timer filters them upstream).

    Returns ``(should_send, next_alerted_fingerprint)``. The fingerprint is the
    union of the carried-forward alerted set and the current criticals, to be
    stamped into ``last_digest_fingerprint`` — meaningful only when
    ``should_send`` is True (on a no-send the caller passes ``None`` and the
    COALESCE upsert preserves the prior alerted set).
    """
    current = {str(i) for i in current_finding_ids}
    if not current:
        return False, ""
    window_live = (
        last_sent_ts is not None and (now_ts - last_sent_ts) < cooldown_seconds
    )
    already = _split_ids(prev_fingerprint) if window_live else set()
    already -= {str(i) for i in excluded_ids or ()}
    new_ids = current - already
    if not new_ids:
        return False, _join_ids(already)
    return True, _join_ids(already | current)


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


# ─── Acknowledgements (suppress-until-recovered, with hysteresis) ─────────────
# Operator-facing "Acknowledge" links in the critical-digest email land here
# (gateway ``GET /api/v1/proactive_health/ack`` → ``record_ack``). An *active*
# ack mutes a finding-id out of the digest until the finding RECOVERS — stays
# green for ``UA_PROACTIVE_HEALTH_ACK_RECOVERY_SECONDS`` (criticals flap on a
# minutes scale, so a brief green dip must not re-arm). The timer is the single
# reconciler (``reconcile_acks`` every tick); everything else reads via
# ``get_active_acks``. Rows are never deleted — recovery flips ``status`` to
# 'recovered' so the table doubles as an audit trail.

_ACK_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS proactive_health_acks (
    id               INTEGER PRIMARY KEY,
    finding_id       TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active',
    acked_at_utc     TEXT NOT NULL,
    ack_source       TEXT,
    last_red_at_utc  TEXT,
    recovered_at_utc TEXT,
    note             TEXT
)
"""

# Partial unique index: at most ONE active ack per finding-id; recovered rows
# accumulate freely (audit trail across repeated ack/recover cycles).
_ACK_ACTIVE_INDEX_DDL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_proactive_health_acks_active
ON proactive_health_acks (finding_id) WHERE status = 'active'
"""


def ensure_ack_schema(conn: sqlite3.Connection) -> None:
    """Create the acks table + active-uniqueness index. Idempotent."""
    conn.execute(_ACK_SCHEMA_DDL)
    conn.execute(_ACK_ACTIVE_INDEX_DDL)


def ack_recovery_seconds() -> int:
    """Green-streak length required before an active ack flips to recovered."""
    raw = os.getenv("UA_PROACTIVE_HEALTH_ACK_RECOVERY_SECONDS")
    if not raw:
        return DEFAULT_ACK_RECOVERY_SECONDS
    try:
        return max(60, int(raw))
    except ValueError:
        return DEFAULT_ACK_RECOVERY_SECONDS


def _parse_ack_ts(value: Any) -> Optional[float]:
    """ISO-8601 (Z-tolerant) → epoch seconds, or None on garbage."""
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _ack_row_to_dict(row: Any) -> Dict[str, Any]:
    try:
        return {k: row[k] for k in row.keys()}  # type: ignore[attr-defined]
    except AttributeError:
        keys = (
            "id",
            "finding_id",
            "status",
            "acked_at_utc",
            "ack_source",
            "last_red_at_utc",
            "recovered_at_utc",
            "note",
        )
        return dict(zip(keys, row))


def record_ack(
    conn: sqlite3.Connection,
    *,
    finding_id: str,
    ack_source: Optional[str] = None,
    note: Optional[str] = None,
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Record an operator acknowledgement for a finding-id. Idempotent.

    A second ack while one is already active is a no-op that returns the
    existing row with ``created=False`` (the gateway renders an "already
    acknowledged" page off that flag). A fresh ack after a prior recovery
    inserts a NEW row — the recovered row stays behind as audit trail.
    """
    ensure_ack_schema(conn)
    fid = str(finding_id or "").strip()
    if not fid:
        raise ValueError("finding_id must be non-empty")
    now = now_iso or datetime.now(timezone.utc).isoformat()
    existing = conn.execute(
        "SELECT * FROM proactive_health_acks WHERE finding_id = ? AND status = 'active'",
        (fid,),
    ).fetchone()
    if existing is not None:
        out = _ack_row_to_dict(existing)
        out["created"] = False
        return out
    cur = conn.execute(
        """
        INSERT INTO proactive_health_acks
            (finding_id, status, acked_at_utc, ack_source, last_red_at_utc, note)
        VALUES (?, 'active', ?, ?, ?, ?)
        """,
        (fid, now, ack_source, now, note),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM proactive_health_acks WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    out = _ack_row_to_dict(row)
    out["created"] = True
    logger.info(
        "proactive_health ack recorded: finding_id=%s source=%s", fid, ack_source
    )
    return out


def get_active_acks(conn: sqlite3.Connection) -> Dict[str, Dict[str, Any]]:
    """Return ``{finding_id: ack_row}`` for all active acks.

    Reader-safe: a missing table (nothing ever acked) returns {} rather than
    raising, so read paths never DDL.
    """
    try:
        rows = conn.execute(
            "SELECT * FROM proactive_health_acks WHERE status = 'active'"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {str(_ack_row_to_dict(r)["finding_id"]): _ack_row_to_dict(r) for r in rows}


def reconcile_acks(
    conn: sqlite3.Connection,
    *,
    current_critical_ids: Iterable[str],
    now_iso: str,
    recovery_seconds: Optional[int] = None,
) -> Dict[str, list]:
    """Advance ack lifecycle against the current critical set. Timer-only.

    For every ACTIVE ack:

    - finding still critical → touch ``last_red_at_utc`` (resets the green
      streak; this is the hysteresis that keeps a flapping critical muted);
    - finding green for longer than ``recovery_seconds`` (measured from the
      last red touch, falling back to the ack time) → flip to 'recovered',
      re-arming alerting for the next NEW red;
    - ack older than ``ACK_MAX_LIFETIME_DAYS`` → force-recover regardless of
      redness (backstop so a click can't mute a finding forever).

    Returns ``{"active": [...], "recovered": [...]}`` finding-id lists.
    """
    ensure_ack_schema(conn)
    window = recovery_seconds if recovery_seconds is not None else ack_recovery_seconds()
    current = {str(i) for i in current_critical_ids}
    now_ts = _parse_ack_ts(now_iso)
    if now_ts is None:
        now_ts = datetime.now(timezone.utc).timestamp()
    max_lifetime_seconds = ACK_MAX_LIFETIME_DAYS * 86400
    active: list[str] = []
    recovered: list[str] = []
    for fid, ack in get_active_acks(conn).items():
        acked_ts = _parse_ack_ts(ack.get("acked_at_utc"))
        expired = acked_ts is not None and (now_ts - acked_ts) > max_lifetime_seconds
        if fid in current and not expired:
            conn.execute(
                "UPDATE proactive_health_acks SET last_red_at_utc = ? WHERE id = ?",
                (now_iso, ack["id"]),
            )
            active.append(fid)
            continue
        last_red_ts = _parse_ack_ts(ack.get("last_red_at_utc")) or acked_ts
        green_long_enough = (
            last_red_ts is None or (now_ts - last_red_ts) > window
        )
        if expired or (fid not in current and green_long_enough):
            conn.execute(
                "UPDATE proactive_health_acks "
                "SET status = 'recovered', recovered_at_utc = ? WHERE id = ?",
                (now_iso, ack["id"]),
            )
            recovered.append(fid)
        else:
            # Green, but not green long enough — keep the ack active.
            active.append(fid)
    conn.commit()
    if recovered:
        logger.info(
            "proactive_health acks recovered (re-armed): %s", ", ".join(sorted(recovered))
        )
    return {"active": active, "recovered": recovered}

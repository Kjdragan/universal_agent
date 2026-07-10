"""TTL-guarded reaper for VP missions orphaned as ``queued`` + ``cancel_requested=1``.

A VP mission lands at ``status='queued'``, ``cancel_requested=1`` when an
operator or pipeline requests cancellation of a queued mission that has not
yet been leased by a worker. The dispatcher honors ``cancel_requested=1`` by
never leasing the mission — so, with no worker ever picking it up, it can
never reach the terminal ``cancelled`` state on its own. It sits queued
forever and is:

  * invisible to :func:`flush_vp_mission_backlog._list_queued` (which filters
    ``cancel_requested=0`` — that tool's job is flushing the *fresh* backlog);
  * invisible to :func:`db_health_monitor.check_stuck_vp_missions` (which
    scopes to ``status IN ('dispatched', 'running')`` — never ``'queued'``);
    and
  * counted permanently in the queued backlog snapshot
    (:func:`vp_mission_backlog.compute_backlog_snapshot` filters
    ``cancel_requested=0``, so it does not even see them).

:func:`reap_stale_cancel_requested_queued_vp_missions` finalizes them to
``cancelled`` once they are older than a TTL grace window — the race-guard
that protects a cancel requested in the same seconds (the dispatcher loop may
be mid-flight honoring it). Past the TTL the mission is definitively
orphaned: it will never lease, so it can never self-finalize. A
``vp_events`` audit row (``event_type='vp.mission.cancelled'``) is written per
mission so the lifecycle trail records why the cancellation completed.

The reaper is strictly bookkeeping — no ``vp_failure`` / rescue card is
surfaced (the mission never ran; there is no outcome worth re-surfacing). It
is idempotent via the ``WHERE status='queued' AND cancel_requested=1`` guard
on the UPDATE, so a second pass finds nothing.

Two callers:

  * :func:`db_health_monitor.check_stuck_vp_missions` — recurring self-heal
    on every health-check heartbeat, default TTL.
  * :func:`flush_vp_mission_backlog.main` (``--reap-cancel-requested``) — the
    sanctioned operator one-shot backfill, explicit min-age.

Mirrors the design of
:func:`stuck_run_reaper.finalize_stale_youtube_hook_runs` (the
run/run_attempt active/active orphan sweep): a TTL-guarded, idempotent,
audit-emitting finalize pass wired into the health loop that detects it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import sqlite3
import uuid

logger = logging.getLogger(__name__)


# Grace window before a queued + cancel_requested=1 mission is considered
# definitively orphaned. A cancel requested within this window MIGHT still be
# mid-processing by the dispatcher loop, so we leave it alone; past it the
# mission will never lease (cancel_requested=1 blocks leasing) and is stuck.
# 60m mirrors services.stuck_run_reaper.DEFAULT_FALLBACK_TTL_MINUTES so a
# stuck VP mission gets at least as much grace as any run_kind.
DEFAULT_STALE_CANCEL_REQUESTED_TTL_MINUTES = 60


@dataclass(frozen=True)
class ReapedCancelRequestedMissionInfo:
    """Structured result for each mission finalized by the orphan sweep.

    Distinct from a plain dict so callers (health findings, audit logs) get a
    typed contract and a stable ``to_dict`` for JSON serialization.
    """

    mission_id: str
    vp_id: str
    stale_minutes: float
    ttl_minutes: int
    terminal_reason: str

    def to_dict(self) -> dict:
        """Return a JSON-serializable summary of the finalized mission."""
        return {
            "mission_id": self.mission_id,
            "vp_id": self.vp_id,
            "stale_minutes": round(self.stale_minutes, 1),
            "ttl_minutes": self.ttl_minutes,
            "terminal_reason": self.terminal_reason,
        }


def _age_minutes(updated_at_str: str | None, now: datetime) -> float:
    """Minutes between ``now`` and a vp_missions.updated_at timestamp.

    Tolerates the ``Z`` suffix and naive (assumed-UTC) timestamps the way the
    run reapers do; returns 0.0 on any parse failure so the info struct stays
    numeric instead of crashing the sweep.
    """
    if not updated_at_str:
        return 0.0
    try:
        ts = str(updated_at_str).strip()
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        updated = datetime.fromisoformat(ts)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 0.0
    return max(0.0, (now - updated).total_seconds()) / 60.0


def reap_stale_cancel_requested_queued_vp_missions(
    conn: sqlite3.Connection,
    *,
    ttl_minutes: int = DEFAULT_STALE_CANCEL_REQUESTED_TTL_MINUTES,
    vp_id: str | None = None,
    dry_run: bool = False,
    reason: str = "stale queued+cancel_requested reaper",
    now: datetime | None = None,
) -> list[ReapedCancelRequestedMissionInfo]:
    """Finalize ``queued`` + ``cancel_requested=1`` VP missions past a TTL.

    Selects every vp_missions row that is ``status='queued'`` AND
    ``cancel_requested=1`` AND whose ``updated_at`` is older than
    ``ttl_minutes`` (the race-guard — vp_missions has no
    ``cancel_requested_at``, so ``updated_at`` is the closest "when was
    cancel requested" signal). For each: flips to ``cancelled`` (idempotent
    via the same ``WHERE`` guard on the UPDATE) and writes a ``vp_events``
    audit row.

    Args:
        conn: ``vp_state.db`` connection (``vp_missions`` + ``vp_events``).
        ttl_minutes: only finalize missions whose ``updated_at`` is older than
            this many minutes. Default
            :data:`DEFAULT_STALE_CANCEL_REQUESTED_TTL_MINUTES` (60). The
            operator backfill passes a small value (e.g. 1) to clear existing
            buildup; the recurring health-loop self-heal uses the default.
        vp_id: restrict to a single ``vp_id`` (e.g. ``vp.coder.primary``).
            ``None`` = all VPs.
        dry_run: when True, return the candidate list WITHOUT mutating — for
            operator preview (``--dry-run``).
        reason: cancellation reason recorded in the ``vp_events`` audit payload.
        now: override "now" for deterministic testing.

    Returns:
        One :class:`ReapedCancelRequestedMissionInfo` per mission finalized
        (or, under ``dry_run``, per candidate) this pass.
    """
    now_dt = now or datetime.now(timezone.utc)
    ttl = max(1, int(ttl_minutes))
    cutoff_iso = (now_dt - timedelta(minutes=ttl)).isoformat()
    now_iso = now_dt.isoformat()

    sql = (
        "SELECT mission_id, vp_id, updated_at "
        "FROM vp_missions "
        "WHERE status = 'queued' AND cancel_requested = 1 "
        "  AND updated_at < ?"
    )
    params: tuple = (cutoff_iso,)
    if vp_id:
        sql += " AND vp_id = ?"
        params = (cutoff_iso, vp_id)
    sql += " ORDER BY updated_at ASC"
    rows = conn.execute(sql, params).fetchall()

    candidates: list[ReapedCancelRequestedMissionInfo] = []
    for row in rows:
        is_row = isinstance(row, sqlite3.Row)
        mission_id = row["mission_id"] if is_row else row[0]
        row_vp_id = (row["vp_id"] if is_row else row[1]) or ""
        updated_at_str = row["updated_at"] if is_row else row[2]
        stale = _age_minutes(updated_at_str, now_dt)
        terminal_reason = (
            f"reaper:cancel_requested_queued_stale:"
            f"{int(stale)}m_stale(ttl={ttl}m)"
        )
        candidates.append(
            ReapedCancelRequestedMissionInfo(
                mission_id=str(mission_id),
                vp_id=str(row_vp_id),
                stale_minutes=stale,
                ttl_minutes=ttl,
                terminal_reason=terminal_reason,
            )
        )

    if dry_run or not candidates:
        return candidates

    # Idempotent finalize: the WHERE guard means a concurrent finalize (or a
    # re-run) touches zero rows for anything already terminal.
    mission_ids = [c.mission_id for c in candidates]
    placeholders = ",".join("?" * len(mission_ids))
    cursor = conn.execute(
        f"""
        UPDATE vp_missions
        SET status = 'cancelled',
            cancel_requested = 1,
            updated_at = ?,
            completed_at = COALESCE(completed_at, ?)
        WHERE mission_id IN ({placeholders})
          AND status = 'queued'
          AND cancel_requested = 1
        """,
        (now_iso, now_iso, *mission_ids),
    )
    affected = int(cursor.rowcount or 0)

    # Audit trail — one vp_events row per mission, matching the event shape
    # flush_vp_mission_backlog._cancel writes. Best-effort: a missing
    # vp_events table (old/partial schema) must not unwind the cancellation.
    audit_payload = json.dumps({"reason": reason, "source": "vp_mission_reaper"})
    audited = 0
    for cand in candidates:
        try:
            conn.execute(
                """
                INSERT INTO vp_events
                  (event_id, mission_id, vp_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, 'vp.mission.cancelled', ?, ?)
                """,
                (
                    f"vp-event-{uuid.uuid4().hex}",
                    cand.mission_id,
                    cand.vp_id,
                    audit_payload,
                    now_iso,
                ),
            )
            audited += 1
        except sqlite3.OperationalError as exc:
            logger.debug(
                "vp_events audit insert skipped for %s (non-fatal): %s",
                cand.mission_id,
                exc,
            )

    conn.commit()
    logger.warning(
        "\U0001fa93 vp_mission_reaper: finalized %d queued+cancel_requested=1 "
        "mission(s) past %dm TTL (%d audit rows written): %s",
        affected,
        ttl,
        audited,
        ", ".join(c.mission_id for c in candidates),
    )
    return candidates

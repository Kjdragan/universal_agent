"""Hermes Phase C — Atlas-direct-dispatch service.

Bypasses the Simone-heartbeat throttle for tasks pre-tagged with
``metadata.preferred_vp = "vp.general.primary"``. Runs every 60s via a
cron-registered ``!script`` entrypoint (``scripts/atlas_direct_dispatch``).

Architectural context:
  * Today, only Simone calls ``dispatch_vp_mission`` for Atlas (via her
    heartbeat). When her heartbeat is busy or stalled, Atlas-eligible
    work sits in ``task_hub_items`` even though Atlas has free slots.
  * Phase C adds an independent caller that does the same dispatch
    without waiting for Simone. The signal-curator pattern at
    ``heartbeat_service.py:2225-2253`` is the precedent for this
    bypass; Phase C generalizes it.

v3 JSON-path: candidates are filtered on top-level
``metadata.preferred_vp`` (the path used by ``proactive_convergence.py:562,
646`` producers), NOT ``metadata.dispatch.preferred_vp``. The Atlas-direct
TRACKING fields we ADD go under ``metadata.dispatch.atlas_direct_*`` for
namespace consistency with retry counters.

Safety:
  * Default OFF — ``UA_ATLAS_DIRECT_DISPATCH_ENABLED=0`` (operator opts in
    after dry-run). The cron registration also goes through
    ``_proactive_cron_enabled``.
  * Respects ``UA_MAX_CONCURRENT_VP_GENERAL`` slot cap (default 2).
  * Idempotency at two layers: (1) atomic SQL claim via
    ``json_set`` UPDATE on ``metadata.dispatch.atlas_direct_dispatched_at``
    only matches NULL rows; (2) ``dispatch_vp_mission`` carries its own
    ``idempotency_key=f"atlas-direct-{task_id}"`` so re-dispatch after
    crash is caught downstream too.
  * Filters out ``source_kind = "vp_mission"`` rows defensively
    (the VP mirror rows from ``vp_orchestration.py:296-305``).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from universal_agent import task_hub

logger = logging.getLogger(__name__)


# ── Configuration knobs ────────────────────────────────────────────────────


def _env_positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


def _is_enabled() -> bool:
    return str(os.getenv("UA_ATLAS_DIRECT_DISPATCH_ENABLED", "0")).strip() in {
        "1",
        "true",
        "True",
        "yes",
    }


# ── Pure functions (easy to unit-test) ─────────────────────────────────────


def find_atlas_direct_candidates(
    conn: sqlite3.Connection,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Return task_hub_items rows eligible for Atlas-direct dispatch.

    Eligibility:
        * status = 'open'
        * agent_ready = 1
        * source_kind != 'vp_mission' (defensive: don't claim VP mirror rows)
        * metadata.preferred_vp = 'vp.general.primary' (v3 top-level path)
        * metadata.dispatch.atlas_direct_dispatched_at IS NULL (not claimed)

    Returns up to ``limit`` rows, oldest first (FIFO).
    """
    if limit <= 0:
        return []
    rows = conn.execute(
        """
        SELECT task_id, title, description, metadata_json, project_key, priority
        FROM task_hub_items
        WHERE status = ?
          AND agent_ready = 1
          AND COALESCE(source_kind, '') != 'vp_mission'
          AND json_extract(metadata_json, '$.preferred_vp') = ?
          AND json_extract(metadata_json, '$.dispatch.atlas_direct_dispatched_at') IS NULL
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (task_hub.TASK_STATUS_OPEN, "vp.general.primary", int(limit)),
    ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        candidates.append(
            {
                "task_id": str(row["task_id"]),
                "title": row["title"],
                "description": row["description"],
                "metadata_json": row["metadata_json"],
                "project_key": row["project_key"],
                "priority": row["priority"],
            }
        )
    return candidates


def try_claim_atlas_direct(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    now_iso: str | None = None,
    assignee: str = "vp.general.primary",
    objective_preview: str = "",
) -> bool:
    """Atomically mark a task as claimed for Atlas-direct dispatch.

    Returns True if this caller successfully claimed (rowcount=1); False
    if another sweep got there first (rowcount=0). This is the only
    safe concurrency primitive in the dispatch path.
    """
    ts = now_iso or datetime.now(timezone.utc).isoformat()
    preview = (objective_preview or "")[:120]
    cursor = conn.execute(
        """
        UPDATE task_hub_items
        SET metadata_json = json_set(
                json_set(
                    json_set(
                        json_set(
                            COALESCE(metadata_json, '{}'),
                            '$.dispatch',
                            COALESCE(json_extract(metadata_json, '$.dispatch'), json('{}'))
                        ),
                        '$.dispatch.atlas_direct_dispatched_at', ?
                    ),
                    '$.dispatch.atlas_direct_lane', ?
                ),
                '$.dispatch.atlas_direct_assignee', ?
            ),
            updated_at = ?
        WHERE task_id = ?
          AND json_extract(metadata_json, '$.dispatch.atlas_direct_dispatched_at') IS NULL
        """,
        (ts, "atlas_direct", assignee, ts, task_id),
    )
    claimed = cursor.rowcount == 1
    if claimed and preview:
        conn.execute(
            "UPDATE task_hub_items SET metadata_json = json_set(metadata_json, "
            "'$.dispatch.atlas_direct_objective_preview', ?) WHERE task_id = ?",
            (preview, task_id),
        )
    return claimed


def count_active_general_slots(conn: sqlite3.Connection) -> int:
    """Count active general-VP assignments via the canonical task_hub helper.

    Mirrors ``todo_dispatch_service._vp_active_counts`` but keeps the
    full call self-contained (the helper there takes a list; we pass
    the list pulled fresh from ``get_agent_activity``).
    """
    from universal_agent.services.todo_dispatch_service import _vp_active_counts

    activity = task_hub.get_agent_activity(conn)
    active_assignments = (
        activity.get("active_assignments") if isinstance(activity, dict) else []
    )
    _coder, general = _vp_active_counts(active_assignments)
    return int(general)


# ── Orchestrator (async, callable from script or tests) ────────────────────


async def dispatch_atlas_candidates_once(
    conn: sqlite3.Connection,
    *,
    max_general_slots: int | None = None,
    dispatch_fn: Any = None,
) -> dict[str, Any]:
    """One pass of the Atlas-direct dispatch sweep.

    Args:
        conn: Open SQLite connection to the runtime DB.
        max_general_slots: Override for ``UA_MAX_CONCURRENT_VP_GENERAL``.
            Defaults to the env value (or 2 if unset).
        dispatch_fn: Injection seam for the dispatch coroutine. Defaults
            to ``tools.vp_orchestration.dispatch_vp_mission``. Tests pass
            a stub.

    Returns: ``{"dispatched": N, "skipped": M, "remaining_slots_at_start":
        K, "reason": <string when no dispatch>}``.
    """
    if dispatch_fn is None:
        from universal_agent.tools.vp_orchestration import dispatch_vp_mission

        dispatch_fn = dispatch_vp_mission

    max_general = (
        max_general_slots
        if max_general_slots is not None
        else _env_positive_int("UA_MAX_CONCURRENT_VP_GENERAL", 2)
    )
    active_general = count_active_general_slots(conn)
    remaining = max_general - active_general
    if remaining <= 0:
        return {
            "dispatched": 0,
            "skipped": 0,
            "remaining_slots_at_start": 0,
            "reason": "atlas_at_capacity",
        }

    candidates = find_atlas_direct_candidates(conn, limit=remaining)
    if not candidates:
        return {
            "dispatched": 0,
            "skipped": 0,
            "remaining_slots_at_start": remaining,
            "reason": "no_candidates",
        }

    dispatched = 0
    skipped = 0
    for candidate in candidates:
        task_id = candidate["task_id"]
        description = str(candidate.get("description") or candidate.get("title") or "")
        objective_preview = description[:120]
        claimed = try_claim_atlas_direct(
            conn,
            task_id=task_id,
            assignee="vp.general.primary",
            objective_preview=objective_preview,
        )
        conn.commit()
        if not claimed:
            skipped += 1
            continue
        try:
            await dispatch_fn(
                vp_id="vp.general.primary",
                objective=description,
                mission_type="proactive_general",
                idempotency_key=f"atlas-direct-{task_id}",
                source_session_id="atlas_direct_dispatch",
            )
            dispatched += 1
            logger.info(
                "atlas_direct: dispatched task_id=%s objective_preview=%r",
                task_id,
                objective_preview[:60],
            )
        except Exception as exc:
            # Record the failure on the task so the next sweep doesn't retry
            # blindly. The atlas_direct_dispatched_at field stays set (the
            # claim was real), so a second pass won't re-dispatch — operator
            # must rehydrate via the Phase B.1 verbs to retry.
            logger.warning(
                "atlas_direct: dispatch_vp_mission failed for task_id=%s: %s",
                task_id,
                exc,
            )
            try:
                conn.execute(
                    "UPDATE task_hub_items SET metadata_json = json_set("
                    "metadata_json, '$.dispatch.atlas_direct_dispatch_error', ?) "
                    "WHERE task_id = ?",
                    (str(exc)[:240], task_id),
                )
                conn.commit()
            except Exception:
                logger.exception(
                    "atlas_direct: also failed to record dispatch error on task"
                )

    return {
        "dispatched": dispatched,
        "skipped": skipped,
        "remaining_slots_at_start": remaining,
        "reason": "ok" if dispatched else "all_claims_lost",
    }


def list_recent_atlas_direct_dispatches(
    conn: sqlite3.Connection,
    *,
    within_minutes: int = 15,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """For Simone's briefing assembly (C.2).

    Returns recently Atlas-direct-dispatched task summaries — task_id,
    objective_preview, dispatched_at — for tasks still in flight.
    """
    rows = conn.execute(
        """
        SELECT task_id,
               json_extract(metadata_json, '$.dispatch.atlas_direct_dispatched_at') AS dispatched_at,
               json_extract(metadata_json, '$.dispatch.atlas_direct_objective_preview') AS objective_preview,
               status
        FROM task_hub_items
        WHERE json_extract(metadata_json, '$.dispatch.atlas_direct_dispatched_at') IS NOT NULL
          AND json_extract(metadata_json, '$.dispatch.atlas_direct_dispatched_at') >
              datetime('now', ?)
        ORDER BY json_extract(metadata_json, '$.dispatch.atlas_direct_dispatched_at') DESC
        LIMIT ?
        """,
        (f"-{int(within_minutes)} minutes", int(limit)),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "task_id": str(row["task_id"]),
                "dispatched_at": row["dispatched_at"],
                "objective_preview": row["objective_preview"] or "",
                "status": row["status"],
            }
        )
    return out

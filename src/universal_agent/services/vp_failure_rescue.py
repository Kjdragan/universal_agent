"""VP failure-rescue surfacing — route every VP mission failure to Simone.

Per PRD ``docs/01_Architecture/12_VP_Goal_Integration_And_Failure_Rescue_PRD.md``:
every VP failure (all modes per A1) creates an informational task hub item
with ``source_kind="vp_mission_failure"`` and ``failure_count`` payload (B3).
Simone reads it on her next heartbeat and chooses one of four actions:
retry / redispatch-fresh / escalate / ignore (the latter via plain
``task_hub_task_action(complete)``).

This service is called from ``durable/state.py:finalize_vp_mission`` whenever
a mission terminates with ``status in {failed, cancelled}`` AND the cancel
was not operator-initiated.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Source-kind constant for the informational task hub items this service
# creates. Routed to Simone via the existing route_all_to_simone router.
SOURCE_KIND_VP_FAILURE = "vp_mission_failure"

# Cap transcript-tail bytes that we copy into task_hub_item.metadata to
# avoid bloating the DB. The original transcript stays in the workspace.
_TRANSCRIPT_TAIL_MAX_BYTES = 2000

# How many bytes of original_objective to embed verbatim (long objectives
# get truncated; full text lives in vp_missions row).
_OBJECTIVE_PREVIEW_MAX_BYTES = 600


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _workspace_path_from_result_ref(result_ref: Optional[str]) -> Optional[str]:
    """Decode a vp_missions.result_ref ("workspace://<path>") to a path."""
    if not result_ref or not isinstance(result_ref, str):
        return None
    raw = result_ref.strip()
    if raw.startswith("workspace://"):
        return raw[len("workspace://") :].strip() or None
    return raw or None


def _mission_payload_metadata(payload_json: Any) -> dict[str, Any]:
    """Extract metadata dict from a vp_missions.payload_json blob."""
    if not payload_json:
        return {}
    try:
        if isinstance(payload_json, str):
            payload = json.loads(payload_json)
        elif isinstance(payload_json, dict):
            payload = payload_json
        else:
            return {}
    except Exception:
        return {}
    meta = payload.get("metadata") if isinstance(payload, dict) else None
    return meta if isinstance(meta, dict) else {}


def _count_chain_failures(
    activity_conn: sqlite3.Connection,
    *,
    rescue_chain_id: Optional[str],
    original_task_id: Optional[str],
) -> int:
    """Count prior vp_mission_failure task_hub_items for the same rescue chain.

    Counts both chain-id-matched and original-task-id-matched rows so the
    function works even when a mission wasn't dispatched as part of a
    rescue chain (failure_count starts at 1 for the first failure).
    """
    if not rescue_chain_id and not original_task_id:
        return 0

    clauses: list[str] = []
    params: list[Any] = []
    # NOTE: task_hub_items stores metadata in the ``metadata_json`` TEXT
    # column (see task_hub.py:271). json_extract works on TEXT columns
    # containing JSON, so we read directly from that column.
    if rescue_chain_id:
        clauses.append("json_extract(metadata_json, '$.rescue_chain_id') = ?")
        params.append(rescue_chain_id)
    if original_task_id:
        clauses.append("json_extract(metadata_json, '$.original_task_id') = ?")
        params.append(original_task_id)

    where = " OR ".join(clauses)
    sql = f"""
        SELECT COUNT(*) FROM task_hub_items
        WHERE source_kind = ?
          AND ({where})
    """
    params_full = [SOURCE_KIND_VP_FAILURE, *params]
    try:
        row = activity_conn.execute(sql, params_full).fetchone()
        return int(row[0] if row else 0)
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("_count_chain_failures failed: %s", exc)
        return 0


def surface_failure_to_simone(
    *,
    mission_id: str,
    failure_mode: str,
    transcript_tail: Optional[str] = None,
    result_ref: Optional[str] = None,
    activity_db_path: Optional[str] = None,
    vp_db_path: Optional[str] = None,
) -> Optional[str]:
    """Create a vp_mission_failure task hub item routed to Simone.

    Returns the task_id of the created item on success, or None if surfacing
    was skipped (e.g. operator_cancel) or failed silently. Never raises —
    callers in the VP finalize path must not be blocked by rescue plumbing.
    """
    if not mission_id:
        return None
    if failure_mode == "operator_cancel":
        # Operator-initiated cancel: don't notify Simone, this was deliberate.
        return None

    # Lazy imports to avoid circular-import on module load.
    from universal_agent import task_hub
    from universal_agent.durable.db import (
        connect_runtime_db,
        get_activity_db_path,
        get_vp_db_path,
    )
    from universal_agent.durable.migrations import ensure_schema as ensure_vp_schema
    from universal_agent.durable.state import get_vp_mission

    vp_path = vp_db_path or get_vp_db_path()
    activity_path = activity_db_path or get_activity_db_path()

    # Load the mission so metadata payload (rescue_chain_id, original_task_id,
    # original_objective, cody_mode, etc.) is available to Simone.
    mission_row: Optional[sqlite3.Row] = None
    try:
        with connect_runtime_db(vp_path) as vp_conn:
            try:
                ensure_vp_schema(vp_conn)
            except Exception:
                pass  # schema may already be in place; reading is what matters
            mission_row = get_vp_mission(vp_conn, mission_id)
    except Exception as exc:
        logger.warning(
            "surface_failure_to_simone: could not read mission %s: %s",
            mission_id, exc,
        )

    if mission_row is None:
        # Best-effort fallback: build a minimal payload from what we know.
        mission_dict: dict[str, Any] = {
            "mission_id": mission_id,
            "vp_id": "",
            "objective": "",
            "payload_json": None,
            "result_ref": result_ref,
        }
    else:
        mission_dict = dict(mission_row)

    vp_id = str(mission_dict.get("vp_id") or "")
    original_objective_full = str(mission_dict.get("objective") or "")
    original_objective = original_objective_full[:_OBJECTIVE_PREVIEW_MAX_BYTES]
    if len(original_objective_full) > _OBJECTIVE_PREVIEW_MAX_BYTES:
        original_objective += "…"

    payload_meta = _mission_payload_metadata(mission_dict.get("payload_json"))
    rescue_chain_id = str(payload_meta.get("rescue_chain_id") or "").strip() or mission_id
    original_task_id = str(payload_meta.get("original_task_id") or "").strip() or None
    cody_mode = str(payload_meta.get("cody_mode") or "").strip() or None

    workspace_path = (
        _workspace_path_from_result_ref(result_ref)
        or _workspace_path_from_result_ref(mission_dict.get("result_ref"))
    )
    brief_path: Optional[str] = None
    if workspace_path:
        candidate = Path(workspace_path) / "BRIEF.md"
        # Don't check existence (best-effort); Simone reads it lazily.
        brief_path = str(candidate)

    transcript_clip: Optional[str] = None
    if transcript_tail:
        text = str(transcript_tail)
        transcript_clip = text[-_TRANSCRIPT_TAIL_MAX_BYTES:]

    # Count prior failures in the same rescue chain (or matching original task).
    failure_count = 1
    try:
        with connect_runtime_db(activity_path) as activity_conn:
            try:
                task_hub.ensure_schema(activity_conn)
            except Exception:
                pass
            prior = _count_chain_failures(
                activity_conn,
                rescue_chain_id=rescue_chain_id,
                original_task_id=original_task_id,
            )
            failure_count = prior + 1
    except Exception as exc:
        logger.debug("surface_failure_to_simone: failure_count lookup failed: %s", exc)

    metadata: dict[str, Any] = {
        "mission_id": mission_id,
        "vp_id": vp_id,
        "failure_mode": failure_mode,
        "failure_count": failure_count,
        "rescue_chain_id": rescue_chain_id,
        "brief_path": brief_path,
        "transcript_tail": transcript_clip,
        "workspace_path": workspace_path,
        "original_objective": original_objective,
        "surfaced_at": _now_iso(),
    }
    if original_task_id:
        metadata["original_task_id"] = original_task_id
    if cody_mode:
        metadata["cody_mode"] = cody_mode

    # task_id is per-failure (unique mission_id ensures one row per failure
    # of one mission; a retry creates a new mission_id → new failure row).
    task_id = f"vp_failure:{mission_id}"
    title_parts = [
        f"VP failure — {vp_id or 'unknown'}",
        f"({failure_mode})" if failure_mode else "",
        f"[#{failure_count}]" if failure_count > 1 else "",
    ]
    title = " ".join(part for part in title_parts if part).strip()

    try:
        with connect_runtime_db(activity_path) as activity_conn:
            try:
                task_hub.ensure_schema(activity_conn)
            except Exception:
                pass
            task_hub.upsert_item(
                activity_conn,
                {
                    "task_id": task_id,
                    "source_kind": SOURCE_KIND_VP_FAILURE,
                    "status": task_hub.TASK_STATUS_OPEN,
                    "agent_ready": True,
                    "trigger_type": "immediate",
                    "title": title,
                    "metadata": metadata,
                },
            )
        logger.info(
            "vp_failure surfaced to Simone: mission_id=%s vp_id=%s mode=%s count=%d",
            mission_id, vp_id, failure_mode, failure_count,
        )
        return task_id
    except Exception as exc:
        logger.exception(
            "surface_failure_to_simone: upsert failed for mission %s: %s",
            mission_id, exc,
        )
        return None


__all__ = ["SOURCE_KIND_VP_FAILURE", "surface_failure_to_simone"]

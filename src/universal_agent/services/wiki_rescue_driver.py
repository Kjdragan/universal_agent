"""Deterministic rescue driver for failed proactive_wiki missions.

Executes the pure ``decide_wiki_rescue`` policy. Called from the VP worker-loop
failure path immediately after ``finalize_vp_mission`` so a failed nightly wiki
is retried / handed to Cody / escalated **now**, instead of rotting in Simone's
rescue queue (which, empirically, never acted on 0/152 production failures).

Flag-gated (``UA_WIKI_RESCUE_ENABLED``, default OFF) so it ships inert and is
enabled after a synthetic-failure smoke. Bounded by the policy's attempt budget,
fires at most once per failure (the finalize ``rowcount==1`` guard), and is
best-effort — it never raises into the worker loop.

Reuses existing primitives: ``vp_failure_rescue`` already surfaced the
authoritative ``failure_count`` onto the ``vp_failure:<mission_id>`` task (and
skips ``operator_cancel`` → no task → nothing to rescue), and the rescue is
executed via the existing ``_vp_dispatch_mission_redispatch_fresh_impl`` /
``_escalate_vp_failure_to_operator_impl`` (with a new ``override_vp_id`` for the
Cody handoff).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from typing import Any, Optional

from universal_agent.durable.db import (
    connect_runtime_db,
    get_activity_db_path,
    get_vp_db_path,
)
from universal_agent.feature_flags import coder_vp_id
from universal_agent.services.wiki_rescue_policy import (
    ACTION_ESCALATE,
    ACTION_HANDOFF_CODY,
    ACTION_RETRY_ATLAS,
    ACTION_SKIP,
    RESCUABLE_MISSION_TYPES,
    decide_wiki_rescue,
)

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


def _enabled() -> bool:
    return str(os.getenv("UA_WIKI_RESCUE_ENABLED", "")).strip().lower() in _TRUTHY


def _dry_run() -> bool:
    """When set, compute + log the rescue decision but dispatch NOTHING.

    Lets us smoke the live worker-loop -> driver -> decision path against real DB
    state (a synthetic failed mission) and confirm the verdict before allowing
    the driver to spawn real rescue missions unattended.
    """
    return str(os.getenv("UA_WIKI_RESCUE_DRY_RUN", "")).strip().lower() in _TRUTHY


def _load_failure_meta(mission_id: str) -> Optional[dict[str, Any]]:
    """Return the metadata dict of the ``vp_failure:<mission_id>`` task, or None.

    None means vp_failure_rescue did NOT surface a task — i.e. the failure was an
    ``operator_cancel`` (deliberate) and there is nothing to rescue.
    """
    try:
        with connect_runtime_db(get_activity_db_path()) as conn:
            row = conn.execute(
                "SELECT metadata_json FROM task_hub_items WHERE task_id = ?",
                (f"vp_failure:{mission_id}",),
            ).fetchone()
    except sqlite3.Error as exc:
        logger.debug("wiki-rescue: failure-task read failed for %s: %s", mission_id, exc)
        return None
    if not row:
        return None
    try:
        return json.loads(row[0] or "{}")
    except (TypeError, ValueError):
        return {}


def _cody_available() -> bool:
    """True when Cody has no running mission.

    Conservative: on any uncertainty we return False so a structural/exhausted
    failure falls back to an ATLAS attempt rather than piling onto the single Cody.
    """
    try:
        with connect_runtime_db(get_vp_db_path()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM vp_missions WHERE vp_id = ? AND status = 'running'",
                (coder_vp_id(),),
            ).fetchone()
        return int(row[0] if row else 1) == 0
    except sqlite3.Error as exc:
        logger.debug("wiki-rescue: cody availability check failed: %s", exc)
        return False


def _guidance(action: str, mode: str) -> str:
    if action == ACTION_HANDOFF_CODY:
        return (
            f"This nightly `proactive_wiki` mission failed (mode='{mode}') and is handed to you (Cody) "
            "to DIAGNOSE AND FIX. Work out why the card->wiki pipeline failed — NLM auth/tooling, the "
            "`wiki_ingest_external_source` step, the NLM research/studio/download steps, or a code/config "
            "issue — then EITHER complete the wiki end-to-end OR fix the underlying cause so the next run "
            "succeeds. Report the root cause and the fix."
        )
    return (
        f"Automatic deterministic retry after a transient failure (mode='{mode}'). Re-run the nightly "
        "`proactive_wiki` pipeline from a fresh workspace."
    )


async def maybe_rescue_failed_wiki_mission(
    *,
    mission_id: str,
    mission_type: str,
    failure_mode: str,
    status: str,
) -> Optional[dict[str, Any]]:
    """Decide + execute a deterministic rescue for a failed wiki mission.

    Returns a small dict describing the action taken (for logging/tests) or None
    when nothing was done (disabled, out of scope, or no failure task). Never
    raises into the caller.
    """
    if not _enabled():
        return None
    if str(status or "") not in {"failed", "cancelled"}:
        return None
    if str(mission_type or "") not in RESCUABLE_MISSION_TYPES:
        return None

    meta = _load_failure_meta(mission_id)
    if meta is None:
        # No surfaced failure task → operator_cancel or surfacing skipped. Skip.
        return None
    failure_count = int(meta.get("failure_count") or 1)
    mode = str(meta.get("failure_mode") or failure_mode or "")

    decision = decide_wiki_rescue(
        mission_type=mission_type,
        failure_mode=mode,
        failure_count=failure_count,
        cody_available=_cody_available(),
    )
    logger.info(
        "wiki-rescue: mission=%s mode=%s count=%d -> action=%s vp=%s (%s)",
        mission_id, mode, failure_count, decision.action, decision.target_vp, decision.reason,
    )

    if _dry_run():
        # Smoke mode: report the verdict, dispatch nothing.
        logger.info("wiki-rescue: DRY_RUN — not executing %s for %s", decision.action, mission_id)
        return {
            "action": decision.action,
            "target_vp": decision.target_vp,
            "reason": decision.reason,
            "failure_count": failure_count,
            "dry_run": True,
        }

    if decision.action == ACTION_SKIP:
        return {"action": ACTION_SKIP, "reason": decision.reason}

    # Lazy import: vp_orchestration pulls in the claude_agent_sdk @tool stack —
    # keep it out of this module's import-time path (and off any cycle).
    from universal_agent.tools.vp_orchestration import (
        _escalate_vp_failure_to_operator_impl,
        _vp_dispatch_mission_redispatch_fresh_impl,
    )

    try:
        if decision.action in (ACTION_RETRY_ATLAS, ACTION_HANDOFF_CODY):
            args: dict[str, Any] = {
                "mission_id": mission_id,
                "additional_context": _guidance(decision.action, mode),
                "rescue_action": decision.action,
            }
            if decision.action == ACTION_HANDOFF_CODY:
                args["override_vp_id"] = coder_vp_id()
            result = await _vp_dispatch_mission_redispatch_fresh_impl(args)
            return {"action": decision.action, "target_vp": decision.target_vp, "result": result}

        if decision.action == ACTION_ESCALATE:
            result = await _escalate_vp_failure_to_operator_impl({
                "mission_id": mission_id,
                "summary": (
                    f"Nightly wiki mission failed {failure_count}x (mode='{mode}'); the deterministic "
                    "retry-then-Cody rescue budget is exhausted."
                ),
                "why_escalating": (
                    "Automated bounded rescue (ATLAS retries + Cody handoff) did not produce a wiki."
                ),
                "recommended_action": (
                    "Inspect the universal-agent-nightly-wiki.service + VP mission logs; the card->wiki "
                    "loop needs a human look."
                ),
            })
            return {"action": ACTION_ESCALATE, "result": result}
    except Exception as exc:  # noqa: BLE001 — never raise into the worker loop
        logger.warning("wiki-rescue: executing %s for %s failed: %s", decision.action, mission_id, exc)
        return {"action": decision.action, "error": str(exc)}

    return None


def _log_rescue_task_result(task: "asyncio.Task") -> None:
    try:
        task.result()
    except Exception as exc:  # noqa: BLE001 — observability only
        logger.warning("wiki-rescue: scheduled rescue task failed: %s", exc)


def schedule_wiki_rescue(
    *,
    mission_id: str,
    mission_type: str,
    failure_mode: str,
    status: str,
) -> None:
    """Sync-safe entry point: schedule the rescue from non-async code.

    The stale reconciler (`gateway_server::_reconcile_stale_vp_missions_once`) is
    a sync function executing inside the gateway's running event loop — there we
    ``create_task`` on that loop (the task runs once the sync sweep yields back).
    For genuinely loop-less callers (scripts, tests) we fall back to
    ``asyncio.run``. Best-effort: never raises into the caller.
    """
    coro = maybe_rescue_failed_wiki_mission(
        mission_id=mission_id,
        mission_type=mission_type,
        failure_mode=failure_mode,
        status=status,
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(coro)
        except Exception as exc:  # noqa: BLE001
            logger.warning("wiki-rescue: sync-run failed for %s: %s", mission_id, exc)
        return
    task = loop.create_task(coro)
    task.add_done_callback(_log_rescue_task_result)

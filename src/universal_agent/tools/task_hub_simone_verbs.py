"""Hermes Phase D — Simone-callable unstick verbs.

These three tools let Simone (and other LLM principals) act on her own
judgment of completed/wedged Cody work without operator intervention.
They wrap Phase B.1's `perform_task_action` verbs (which previously were
operator-only via the dashboard).

This is the missing piece that closes the Simone-directs-Cody autonomy
loop: Simone reads `task_hub_runs` history via the failure-context
endpoint / her briefing, judges Cody's last attempt, and uses one of
these tools to either retry-with-context, redirect to a different VP,
or request a revision with feedback.

Tools registered:
    * `task_re_evaluate(task_id, reason)` — retry with prior_runs
      attached to re_evaluation_context. NO retry-budget bump (per
      operator decision 2026-05-11). Use when Cody's output looks
      incomplete or off-target and a fresh attempt with evidence might
      do better.
    * `task_redirect_to(task_id, target_vp, reason)` — rehydrate the
      task and set `metadata.preferred_vp` so Atlas-direct-dispatch
      (Phase C) or the next-claim path routes it to a different VP.
      Use when the original VP is clearly the wrong fit.
    * `task_request_revision(task_id, feedback, max_extra_retries=1)`
      — rehydrate + attach feedback comment + bump revision_round +
      bump max_retries by max_extra_retries. Use when the work is
      close but needs operator-style refinement.

All three are operator-equivalent to the existing dashboard buttons
shipped in Phase B.2; this module just exposes them as LLM tools.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict

from claude_agent_sdk import tool

from universal_agent import task_hub
from universal_agent.durable.db import connect_runtime_db, get_activity_db_path


def _ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=True, default=str)}]}


def _err(message: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": f"error: {message}"}]}


# ── task_re_evaluate ────────────────────────────────────────────────────────


@tool(
    name="task_re_evaluate",
    description=(
        "Retry a wedged or completed task with prior-run evidence attached "
        "for the next agent's prompt. Use when Cody's output looks "
        "incomplete, off-target, or you suspect the agent missed key "
        "context that's visible in the run history. Resets the task to "
        "`open` status and attaches `re_evaluation_context` (last error, "
        "retry counts, side-effect evidence, prior_assignments_summary, "
        "and prior_runs from task_hub_runs) which the next claimer's "
        "prompt assembler will surface as an addendum. "
        "Does NOT bump the retry budget — task remains subject to "
        "existing max_retries / heartbeat_retry_limit. Use "
        "`task_request_revision` instead if you need to extend retry "
        "budget alongside attaching feedback."
    ),
    input_schema={
        "task_id": str,
        "reason": str,
    },
)
async def task_re_evaluate_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    task_id = str(args.get("task_id", "") or "").strip()
    if not task_id:
        return _err("task_id is required")

    reason = str(args.get("reason", "") or "").strip()
    conn = connect_runtime_db(get_activity_db_path())
    conn.row_factory = sqlite3.Row
    try:
        item = task_hub.get_item(conn, task_id)
        if not item:
            return _err(f"No task found with ID: {task_id}")
        updated = task_hub.perform_task_action(
            conn,
            task_id=task_id,
            action="re_evaluate",
            reason=reason or "simone_re_evaluate",
            agent_id="simone",
        )
    except ValueError as exc:
        return _err(str(exc))
    finally:
        conn.close()

    return _ok(
        {
            "success": True,
            "task_id": task_id,
            "action": "re_evaluate",
            "reason": reason or "simone_re_evaluate",
            "item": updated,
        }
    )


# ── task_redirect_to ────────────────────────────────────────────────────────


@tool(
    name="task_redirect_to",
    description=(
        "Redirect a wedged or unfit task to a different VP/agent. "
        "Resets the task to `open`, rehydrates the dispatch metadata, "
        "and sets `metadata.preferred_vp` to the target so Phase C's "
        "Atlas-direct-dispatch sweep (or the next routing decision) "
        "picks it up for the new VP. Use when the original VP is "
        "clearly the wrong fit (e.g. coding mission landed with a "
        "non-coding VP, or Cody is stuck on something Atlas would "
        "handle better). "
        "`target_vp` examples: 'vp.general.primary' (Atlas), "
        "'vp.coder.primary' (Cody). `reason` is logged in the "
        "evaluation history."
    ),
    input_schema={
        "task_id": str,
        "target_vp": str,
        "reason": str,
    },
)
async def task_redirect_to_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    task_id = str(args.get("task_id", "") or "").strip()
    if not task_id:
        return _err("task_id is required")
    target_vp = str(args.get("target_vp", "") or "").strip()
    if not target_vp:
        return _err("target_vp is required (e.g. 'vp.general.primary', 'vp.coder.primary')")
    reason = str(args.get("reason", "") or "").strip()

    # perform_task_action's redirect_to verb reads the target from `reason`
    # OR `note` — pass it via reason so it's the canonical field.
    conn = connect_runtime_db(get_activity_db_path())
    conn.row_factory = sqlite3.Row
    try:
        item = task_hub.get_item(conn, task_id)
        if not item:
            return _err(f"No task found with ID: {task_id}")
        updated = task_hub.perform_task_action(
            conn,
            task_id=task_id,
            action="redirect_to",
            reason=target_vp,
            note=reason or "simone_redirect_to",
            agent_id="simone",
        )
    except ValueError as exc:
        return _err(str(exc))
    finally:
        conn.close()

    return _ok(
        {
            "success": True,
            "task_id": task_id,
            "action": "redirect_to",
            "target_vp": target_vp,
            "reason": reason or "simone_redirect_to",
            "item": updated,
        }
    )


# ── task_request_revision ───────────────────────────────────────────────────


@tool(
    name="task_request_revision",
    description=(
        "Request a revision of a Cody (or any VP) work product with "
        "specific feedback attached. Rehydrates the task, appends "
        "the feedback as an operator-style comment, bumps "
        "`revision_round` so the next agent knows this is a revision, "
        "and bumps `max_retries` by max_extra_retries (default 1) "
        "so the retry budget doesn't immediately re-park the task. "
        "Use when the work product is close but needs refinement: "
        "missing edge cases, doc updates, additional tests, etc. "
        "`feedback` should be specific and actionable — Cody reads it "
        "verbatim as guidance."
    ),
    input_schema={
        "task_id": str,
        "feedback": str,
        "max_extra_retries": int,
    },
)
async def task_request_revision_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    task_id = str(args.get("task_id", "") or "").strip()
    if not task_id:
        return _err("task_id is required")
    feedback = str(args.get("feedback", "") or "").strip()
    if not feedback:
        return _err("feedback is required (operator-style specific guidance for the revision)")

    raw_extra = args.get("max_extra_retries", 1)
    try:
        extra_retries = max(0, int(raw_extra))
    except (TypeError, ValueError):
        extra_retries = 1

    conn = connect_runtime_db(get_activity_db_path())
    conn.row_factory = sqlite3.Row
    try:
        item = task_hub.get_item(conn, task_id)
        if not item:
            return _err(f"No task found with ID: {task_id}")
        # perform_task_action's request_revision verb reads the feedback
        # text from `note` (matches the dashboard prompt's "Reason" field).
        updated = task_hub.perform_task_action(
            conn,
            task_id=task_id,
            action="request_revision",
            note=feedback,
            reason=f"max_extra_retries={extra_retries}",
            agent_id="simone",
        )
    except ValueError as exc:
        return _err(str(exc))
    finally:
        conn.close()

    return _ok(
        {
            "success": True,
            "task_id": task_id,
            "action": "request_revision",
            "feedback": feedback,
            "max_extra_retries": extra_retries,
            "item": updated,
        }
    )


__all__ = [
    "task_re_evaluate_wrapper",
    "task_redirect_to_wrapper",
    "task_request_revision_wrapper",
]

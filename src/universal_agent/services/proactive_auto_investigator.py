"""Proactive Auto-Investigator — diagnostic analysis for failed proactive tasks.

When a proactive task reaches a failure terminal state (block/review), this
module generates a concise diagnostic artifact by gathering context from the
task's assignment and evaluation history, then running a Gemini Flash analysis
(with a deterministic fallback).

Results are stored as proactive artifacts and optionally written to long-term
memory.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Optional
import uuid

logger = logging.getLogger(__name__)

# ── LLM Prompts ──────────────────────────────────────────────────────────

_DIAGNOSTIC_SYSTEM = """\
You are a diagnostic analyst for an autonomous agent system. A proactive task \
has failed or been blocked. Analyze the available context and produce a concise \
post-mortem with:

1. ROOT CAUSE: What most likely caused the failure (1-2 sentences)
2. CONTRIBUTING FACTORS: Environmental or systemic issues (bullet list)
3. RECOMMENDATION: What should change to prevent recurrence (1-2 sentences)
4. DISPATCH QUALITY: Was this task worth attempting? (yes/no with reason)

Keep the total response under 200 words. Be specific and actionable.
"""


# ── Core entry point ─────────────────────────────────────────────────────

def investigate_proactive_failure(
    conn: sqlite3.Connection,
    *,
    task: dict[str, Any],
    outcome: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Generate a diagnostic artifact for a failed proactive task.

    Returns a dict with ``artifact_id``, ``summary``, and ``diagnostic`` text,
    or ``None`` if investigation could not be completed.
    """
    task_id = str(task.get("task_id") or "").strip()
    if not task_id:
        return None

    try:
        context = _build_investigation_context(conn, task=task, outcome=outcome)
        diagnostic_text = _generate_diagnostic(context)
        artifact = _store_diagnostic_artifact(conn, task=task, outcome=outcome, diagnostic_text=diagnostic_text)

        logger.info(
            "Auto-investigation complete for task %s: artifact=%s",
            task_id, artifact.get("artifact_id"),
        )

        return {
            "artifact_id": artifact.get("artifact_id", ""),
            "summary": diagnostic_text[:300],
            "diagnostic": diagnostic_text,
            "context": context,
        }
    except Exception as exc:
        logger.warning("Auto-investigation failed for task %s: %s", task_id, exc)
        return None


# ── Context Gathering ────────────────────────────────────────────────────

def _build_investigation_context(
    conn: sqlite3.Connection,
    *,
    task: dict[str, Any],
    outcome: dict[str, Any],
) -> dict[str, Any]:
    """Gather all relevant context for failure investigation."""
    task_id = str(task.get("task_id") or "").strip()

    # Assignment history
    assignments = []
    try:
        rows = conn.execute(
            """
            SELECT assignment_id, agent_id, state, started_at, ended_at, result_summary
            FROM task_hub_assignments
            WHERE task_id = ?
            ORDER BY started_at DESC
            LIMIT 10
            """,
            (task_id,),
        ).fetchall()
        assignments = [dict(r) for r in rows]
    except Exception:
        pass

    # Evaluation history
    evaluations = []
    try:
        rows = conn.execute(
            """
            SELECT agent_id, decision, reason, score, evaluated_at
            FROM task_hub_evaluations
            WHERE task_id = ?
            ORDER BY evaluated_at DESC
            LIMIT 10
            """,
            (task_id,),
        ).fetchall()
        evaluations = [dict(r) for r in rows]
    except Exception:
        pass

    # Related comments
    comments = []
    try:
        rows = conn.execute(
            """
            SELECT author, content, created_at
            FROM task_hub_comments
            WHERE task_id = ?
            ORDER BY created_at DESC
            LIMIT 5
            """,
            (task_id,),
        ).fetchall()
        comments = [dict(r) for r in rows]
    except Exception:
        pass

    return {
        "task": {
            "task_id": task_id,
            "title": str(task.get("title") or ""),
            "description": str(task.get("description") or "")[:2000],
            "source_kind": str(task.get("source_kind") or ""),
            "project_key": str(task.get("project_key") or ""),
            "priority": task.get("priority"),
            "labels": task.get("labels") or [],
            "created_at": str(task.get("created_at") or ""),
        },
        "outcome": {
            "action": str(outcome.get("action") or ""),
            "reason": str(outcome.get("reason") or ""),
            "agent_id": str(outcome.get("agent_id") or ""),
            "assignment_count": outcome.get("assignment_count", 0),
            "duration_seconds": outcome.get("duration_seconds"),
        },
        "assignments": assignments,
        "evaluations": evaluations,
        "comments": comments,
    }


# ── Diagnostic Generation ────────────────────────────────────────────────

def _generate_diagnostic(context: dict[str, Any]) -> str:
    """Generate diagnostic text via LLM with deterministic fallback."""
    try:
        return _llm_diagnostic(context)
    except Exception as exc:
        logger.debug("LLM diagnostic unavailable, using fallback: %s", exc)
        return _deterministic_diagnostic(context)


def _llm_diagnostic(context: dict[str, Any]) -> str:
    """Call Gemini Flash for diagnostic analysis."""
    import google.genai as genai

    user_prompt = _format_context_for_llm(context)

    client = genai.Client()
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=user_prompt,
        config=genai.types.GenerateContentConfig(
            system_instruction=_DIAGNOSTIC_SYSTEM,
            max_output_tokens=500,
            temperature=0.3,
        ),
    )

    text = ""
    if response and response.text:
        text = response.text.strip()

    if not text:
        raise ValueError("Empty LLM response")

    return text


def _format_context_for_llm(context: dict[str, Any]) -> str:
    """Format investigation context into a structured prompt."""
    task = context.get("task", {})
    outcome = context.get("outcome", {})
    assignments = context.get("assignments", [])
    evaluations = context.get("evaluations", [])
    comments = context.get("comments", [])

    lines = [
        "FAILED PROACTIVE TASK INVESTIGATION",
        "",
        f"Task: {task.get('title', 'unknown')}",
        f"Source: {task.get('source_kind', 'unknown')}",
        f"Priority: {task.get('priority', 'unknown')}",
        f"Labels: {', '.join(task.get('labels', []))}",
        f"Created: {task.get('created_at', 'unknown')}",
        "",
        f"Terminal Action: {outcome.get('action', 'unknown')}",
        f"Reason: {outcome.get('reason', 'no reason given')}",
        f"Agent: {outcome.get('agent_id', 'unknown')}",
        f"Assignments: {outcome.get('assignment_count', 0)}",
        f"Duration: {outcome.get('duration_seconds', 'unknown')}s",
        "",
        f"Description (truncated): {task.get('description', '')[:500]}",
    ]

    if assignments:
        lines.append("")
        lines.append("ASSIGNMENT HISTORY:")
        for asg in assignments[:5]:
            lines.append(
                f"  - {asg.get('agent_id', '?')} | state={asg.get('state', '?')} | "
                f"started={asg.get('started_at', '?')} | result={asg.get('result_summary', '?')}"
            )

    if evaluations:
        lines.append("")
        lines.append("EVALUATION HISTORY:")
        for ev in evaluations[:5]:
            lines.append(
                f"  - {ev.get('agent_id', '?')} decided={ev.get('decision', '?')} "
                f"reason={ev.get('reason', '?')} score={ev.get('score', '?')}"
            )

    if comments:
        lines.append("")
        lines.append("COMMENTS:")
        for c in comments[:3]:
            lines.append(f"  - [{c.get('author', '?')}] {str(c.get('content', ''))[:200]}")

    return "\n".join(lines)


def _deterministic_diagnostic(context: dict[str, Any]) -> str:
    """Template-based diagnostic when LLM is unavailable."""
    task = context.get("task", {})
    outcome = context.get("outcome", {})
    assignments = context.get("assignments", [])

    task_title = task.get("title", "unknown task")
    action = outcome.get("action", "unknown")
    reason = outcome.get("reason", "no reason provided")
    agent_id = outcome.get("agent_id", "unknown")
    assignment_count = outcome.get("assignment_count", 0)
    duration = outcome.get("duration_seconds")

    lines = [
        f"DIAGNOSTIC: {task_title}",
        "",
        f"1. ROOT CAUSE: Task was terminated with action '{action}'. "
        f"Agent '{agent_id}' reported: {reason}.",
        "",
        "2. CONTRIBUTING FACTORS:",
    ]

    if assignment_count == 0:
        lines.append("  - Task was never assigned to an agent (possible eligibility issue)")
    elif assignment_count > 2:
        lines.append(f"  - Task was assigned {assignment_count} times (possible retry exhaustion)")

    if duration is not None and duration > 3600:
        lines.append(f"  - Long execution time ({duration:.0f}s) suggests resource contention or complexity")
    elif duration is not None and duration < 10:
        lines.append(f"  - Very short execution ({duration:.0f}s) suggests immediate rejection or classification error")

    if not reason or reason in ("unknown", ""):
        lines.append("  - No actionable reason provided by the agent")

    # Check for pattern in assignment results
    failed_results = [
        a for a in assignments
        if str(a.get("result_summary") or "").lower() in ("blocked", "failed", "error")
    ]
    if failed_results:
        lines.append(f"  - {len(failed_results)} assignment(s) ended in failure state")

    lines.extend([
        "",
        f"3. RECOMMENDATION: Review the task source ({task.get('source_kind', 'unknown')}) "
        f"pipeline for quality. Consider adjusting dispatch priority or adding guardrails "
        f"for this category of work.",
        "",
        f"4. DISPATCH QUALITY: Uncertain — insufficient data to determine if this task "
        f"was worth attempting. Monitor similar tasks for pattern.",
    ])

    return "\n".join(lines)


# ── Artifact Storage ─────────────────────────────────────────────────────

def _store_diagnostic_artifact(
    conn: sqlite3.Connection,
    *,
    task: dict[str, Any],
    outcome: dict[str, Any],
    diagnostic_text: str,
) -> dict[str, Any]:
    """Store the diagnostic as a proactive artifact."""
    try:
        from universal_agent.services.proactive_artifacts import (
            ARTIFACT_STATUS_CANDIDATE,
            make_artifact_id,
            upsert_artifact,
        )

        task_id = str(task.get("task_id") or "")
        artifact = upsert_artifact(
            conn,
            artifact_id=make_artifact_id(
                source_kind="proactive_outcome",
                source_ref=task_id,
                artifact_type="failure_diagnostic",
                title=str(task.get("title") or "diagnostic"),
            ),
            artifact_type="failure_diagnostic",
            source_kind="proactive_outcome",
            source_ref=task_id,
            title=f"Diagnostic: {task.get('title', 'unknown')}",
            summary=diagnostic_text[:500],
            status=ARTIFACT_STATUS_CANDIDATE,
            priority=2,
            topic_tags=["diagnostic", "proactive_outcome", str(task.get("source_kind") or "")],
            metadata={
                "task_id": task_id,
                "outcome_action": outcome.get("action"),
                "outcome_reason": outcome.get("reason"),
                "agent_id": outcome.get("agent_id"),
            },
        )
        return artifact
    except Exception as exc:
        logger.warning("Failed to store diagnostic artifact: %s", exc)
        # Return a minimal artifact dict so callers can still function
        return {
            "artifact_id": f"diag_{uuid.uuid4().hex[:12]}",
            "diagnostic": diagnostic_text,
        }

"""Durable recaps for proactive Task Hub work items.

The recap is intentionally stored separately from task metadata so the dashboard
can audit the evaluator output without mutating the original task record.  The
current evaluator is session-evidence based and keeps a raw payload that can be
replaced by an LLM response when model-backed evaluation is enabled.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any, Optional
import uuid

logger = logging.getLogger(__name__)

MAX_EXCERPT_CHARS = 6000
MAX_WORK_PRODUCTS = 25


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS proactive_work_recaps (
            recap_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL UNIQUE,
            assignment_id TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            workspace_dir TEXT NOT NULL DEFAULT '',
            source_kind TEXT NOT NULL DEFAULT '',
            evaluation_status TEXT NOT NULL DEFAULT '',
            idea TEXT NOT NULL DEFAULT '',
            implemented TEXT NOT NULL DEFAULT '',
            known_issues TEXT NOT NULL DEFAULT '',
            success_assessment TEXT NOT NULL DEFAULT '',
            recommended_next_action TEXT NOT NULL DEFAULT '',
            confidence REAL,
            raw_model_output_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_proactive_work_recaps_task
            ON proactive_work_recaps(task_id);
        CREATE INDEX IF NOT EXISTS idx_proactive_work_recaps_updated
            ON proactive_work_recaps(updated_at DESC);
        """
    )
    conn.commit()


def get_recap_for_task(conn: sqlite3.Connection, task_id: str) -> Optional[dict[str, Any]]:
    ensure_schema(conn)
    row = conn.execute(
        """
        SELECT *
        FROM proactive_work_recaps
        WHERE task_id = ?
        LIMIT 1
        """,
        (str(task_id or "").strip(),),
    ).fetchone()
    if not row:
        return None
    return _row_to_recap(row)


def upsert_recap_for_task(
    conn: sqlite3.Connection,
    *,
    task: dict[str, Any],
    action: str = "",
    reason: str = "",
) -> Optional[dict[str, Any]]:
    """Generate and store a recap for a proactive terminal task.

    This function is best-effort.  Callers should treat ``None`` as "no task id"
    rather than a terminal failure.
    """
    ensure_schema(conn)
    task_id = str(task.get("task_id") or "").strip()
    if not task_id:
        return None

    assignment = _latest_assignment(conn, task_id)
    workspace_dir = str(assignment.get("workspace_dir") or "").strip()
    session_id = str(
        assignment.get("provider_session_id")
        or assignment.get("workflow_run_id")
        or assignment.get("agent_id")
        or ""
    ).strip()
    now_iso = _now_iso()
    existing = get_recap_for_task(conn, task_id)
    recap_id = str((existing or {}).get("recap_id") or f"pwr_{uuid.uuid4().hex[:16]}")
    created_at = str((existing or {}).get("created_at") or now_iso)
    generated = _evaluate_from_session_evidence(
        task=task,
        assignment=assignment,
        action=action,
        reason=reason,
        workspace_dir=workspace_dir,
    )

    conn.execute(
        """
        INSERT INTO proactive_work_recaps (
            recap_id, task_id, assignment_id, session_id, workspace_dir,
            source_kind, evaluation_status, idea, implemented, known_issues,
            success_assessment, recommended_next_action, confidence,
            raw_model_output_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET
            assignment_id = excluded.assignment_id,
            session_id = excluded.session_id,
            workspace_dir = excluded.workspace_dir,
            source_kind = excluded.source_kind,
            evaluation_status = excluded.evaluation_status,
            idea = excluded.idea,
            implemented = excluded.implemented,
            known_issues = excluded.known_issues,
            success_assessment = excluded.success_assessment,
            recommended_next_action = excluded.recommended_next_action,
            confidence = excluded.confidence,
            raw_model_output_json = excluded.raw_model_output_json,
            updated_at = excluded.updated_at
        """,
        (
            recap_id,
            task_id,
            str(assignment.get("assignment_id") or ""),
            session_id,
            workspace_dir,
            str(task.get("source_kind") or ""),
            generated["evaluation_status"],
            generated["idea"],
            generated["implemented"],
            generated["known_issues"],
            generated["success_assessment"],
            generated["recommended_next_action"],
            generated["confidence"],
            json.dumps(generated["raw_model_output"], ensure_ascii=True, sort_keys=True),
            created_at,
            now_iso,
        ),
    )
    conn.commit()
    stored = get_recap_for_task(conn, task_id)
    logger.info("Stored proactive work recap for task=%s status=%s", task_id, generated["evaluation_status"])
    return stored


def _latest_assignment(conn: sqlite3.Connection, task_id: str) -> dict[str, Any]:
    try:
        row = conn.execute(
            """
            SELECT assignment_id, task_id, agent_id, workflow_run_id, workflow_attempt_id,
                   provider_session_id, workspace_dir, state, started_at, ended_at, result_summary
            FROM task_hub_assignments
            WHERE task_id = ?
            ORDER BY COALESCE(ended_at, started_at) DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
    except Exception:
        row = None
    if not row:
        return {}
    return {key: row[key] for key in row.keys()}


def _evaluate_from_session_evidence(
    *,
    task: dict[str, Any],
    assignment: dict[str, Any],
    action: str,
    reason: str,
    workspace_dir: str,
) -> dict[str, Any]:
    title = str(task.get("title") or "").strip()
    description = str(task.get("description") or "").strip()
    result_summary = str(assignment.get("result_summary") or "").strip()
    action_norm = str(action or "").strip().lower()
    reason_text = str(reason or "").strip()
    workspace = Path(workspace_dir).expanduser() if workspace_dir else None
    work_products = _list_work_products(workspace)
    transcript_tail = _read_tail(workspace / "transcript.md") if workspace else ""
    run_log_tail = _read_tail(workspace / "run.log") if workspace else ""

    idea = title or description or "Untitled proactive work item"
    implemented = _implemented_summary(
        result_summary=result_summary,
        reason=reason_text,
        work_products=work_products,
        transcript_tail=transcript_tail,
        run_log_tail=run_log_tail,
    )
    known_issues = _known_issues(
        action=action_norm,
        reason=reason_text,
        result_summary=result_summary,
        workspace=workspace,
        work_products=work_products,
    )
    success_assessment, confidence = _success_assessment(
        action=action_norm,
        implemented=implemented,
        known_issues=known_issues,
        work_products=work_products,
        transcript_tail=transcript_tail,
        run_log_tail=run_log_tail,
    )
    recommended = _recommended_next_action(
        action=action_norm,
        known_issues=known_issues,
        work_products=work_products,
    )
    raw_model_output = {
        "evaluator": "session_evidence_recap_v1",
        "llm_upgrade_ready": True,
        "task_id": str(task.get("task_id") or ""),
        "assignment_id": str(assignment.get("assignment_id") or ""),
        "action": action_norm,
        "reason": reason_text,
        "workspace_dir": workspace_dir,
        "work_products": work_products,
        "transcript_tail_chars": len(transcript_tail),
        "run_log_tail_chars": len(run_log_tail),
        "prompt_context": {
            "idea": idea,
            "description": description,
            "result_summary": result_summary,
        },
    }
    return {
        "evaluation_status": "session_evidence_evaluated",
        "idea": idea,
        "implemented": implemented,
        "known_issues": known_issues,
        "success_assessment": success_assessment,
        "recommended_next_action": recommended,
        "confidence": confidence,
        "raw_model_output": raw_model_output,
    }


def _implemented_summary(
    *,
    result_summary: str,
    reason: str,
    work_products: list[str],
    transcript_tail: str,
    run_log_tail: str,
) -> str:
    if result_summary:
        summary = result_summary
    elif reason:
        summary = reason
    elif work_products:
        summary = f"Produced {len(work_products)} work product(s): {', '.join(work_products[:5])}."
    else:
        excerpt = transcript_tail or run_log_tail
        summary = _first_meaningful_line(excerpt) or "No concrete implementation summary was captured."
    if work_products and "work product" not in summary.lower():
        summary = f"{summary} Work products: {', '.join(work_products[:5])}."
    return summary[:1200]


def _known_issues(
    *,
    action: str,
    reason: str,
    result_summary: str,
    workspace: Optional[Path],
    work_products: list[str],
) -> str:
    issues: list[str] = []
    if action in {"block", "review", "park"}:
        issues.append(reason or result_summary or f"Terminal action was {action}.")
    if workspace and not workspace.exists():
        issues.append("Recorded workspace directory is not available for audit.")
    if action in {"complete", "approve"} and not work_products and not result_summary:
        issues.append("No result summary or work product files were found.")
    return " ".join(issues)


def _success_assessment(
    *,
    action: str,
    implemented: str,
    known_issues: str,
    work_products: list[str],
    transcript_tail: str,
    run_log_tail: str,
) -> tuple[str, float]:
    has_evidence = bool(implemented.strip()) and (
        bool(work_products) or bool(transcript_tail) or bool(run_log_tail)
    )
    if action in {"complete", "approve"} and not known_issues and has_evidence:
        return "Successful based on terminal status plus available session evidence.", 0.78
    if action in {"complete", "approve"}:
        return "Completed, but success confidence is limited by sparse session evidence.", 0.52
    if action in {"block", "review"}:
        return "Not yet successful; the task requires follow-up before counting as completed proactive work.", 0.7
    if action == "park":
        return "Parked; useful context may exist, but the work is not complete.", 0.62
    return "Terminal status was recorded; evaluator confidence is limited.", 0.45


def _recommended_next_action(*, action: str, known_issues: str, work_products: list[str]) -> str:
    if action in {"block", "review"}:
        return "Create a fresh continuation session that reuses the recorded workspace and addresses the issue above."
    if action == "park":
        return "Re-score later or create a smaller continuation task if the opportunity remains valuable."
    if known_issues:
        return "Review the three-panel session and decide whether to spawn a continuation task."
    if work_products:
        return "Review linked artifacts and provide feedback if this should influence future proactive scoring."
    return "Review the three-panel session because no separate artifact was found."


def _list_work_products(workspace: Optional[Path]) -> list[str]:
    if not workspace:
        return []
    root = workspace / "work_products"
    if not root.exists() or not root.is_dir():
        return []
    out: list[str] = []
    try:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                out.append(str(path.relative_to(workspace)))
                if len(out) >= MAX_WORK_PRODUCTS:
                    break
    except Exception:
        return out
    return out


def _read_tail(path: Path) -> str:
    try:
        if not path.exists() or not path.is_file():
            return ""
        data = path.read_bytes()
    except Exception:
        return ""
    return data[-MAX_EXCERPT_CHARS:].decode("utf-8", errors="replace")


def _first_meaningful_line(text: str) -> str:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if len(line) >= 20 and not line.startswith(("{", "[", "TRACE", "DEBUG")):
            return line[:500]
    return ""


def _row_to_recap(row: sqlite3.Row) -> dict[str, Any]:
    raw_json = str(row["raw_model_output_json"] or "{}")
    try:
        raw = json.loads(raw_json)
    except Exception:
        raw = {}
    return {
        "recap_id": str(row["recap_id"] or ""),
        "task_id": str(row["task_id"] or ""),
        "assignment_id": str(row["assignment_id"] or ""),
        "session_id": str(row["session_id"] or ""),
        "workspace_dir": str(row["workspace_dir"] or ""),
        "source_kind": str(row["source_kind"] or ""),
        "evaluation_status": str(row["evaluation_status"] or ""),
        "status": str(row["evaluation_status"] or ""),
        "idea": str(row["idea"] or ""),
        "implemented": str(row["implemented"] or ""),
        "known_issues": str(row["known_issues"] or ""),
        "success_assessment": str(row["success_assessment"] or ""),
        "recommended_next_action": str(row["recommended_next_action"] or ""),
        "confidence": row["confidence"],
        "raw_model_output": raw,
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "generated_at": str(row["updated_at"] or ""),
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

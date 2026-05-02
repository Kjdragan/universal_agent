"""Durable recaps for proactive Task Hub work items.

The recap is intentionally stored separately from task metadata so the dashboard
can audit the evaluator output without mutating the original task record.  The
current evaluator uses a high-capability LLM when enabled and keeps a
session-evidence fallback for provider failures.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Optional
import uuid

from universal_agent.utils.model_resolution import resolve_opus

logger = logging.getLogger(__name__)

MAX_EXCERPT_CHARS = 6000
MAX_WORK_PRODUCTS = 25
LLM_MAX_CONTEXT_CHARS = 20_000
LLM_TIMEOUT_SECONDS = 20


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create proactive_work_recaps tables and indexes if they do not exist."""
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
    """Fetch the stored recap for a task, returning None if not found."""
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
    evidence = _collect_evidence_bundle(
        task=task,
        assignment=assignment,
        action=action,
        reason=reason,
        workspace_dir=workspace_dir,
    )
    generated = _evaluate_recap(
        task=task,
        assignment=assignment,
        action=action,
        reason=reason,
        evidence=evidence,
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
    """Return the most recent task_hub_assignments row for a task_id."""
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


def _collect_evidence_bundle(
    *,
    task: dict[str, Any],
    assignment: dict[str, Any],
    action: str,
    reason: str,
    workspace_dir: str,
) -> dict[str, Any]:
    """Gather workspace artifacts, transcript tail, and run log into an evidence dict.

    When the ephemeral workspace has been cleaned up (common for VP-delegated
    work), fall back to the persistent packet directory recorded in the task
    metadata.  The packet dir survives workspace cleanup and contains digest,
    knowledge-base updates, and implementation opportunity files that serve as
    concrete evidence of completed work.
    """
    workspace = Path(workspace_dir).expanduser() if workspace_dir else None
    workspace_exists = bool(workspace and workspace.exists())

    # Primary evidence: ephemeral workspace
    work_products = _list_work_products(workspace) if workspace_exists else []
    transcript_tail = _read_tail(workspace / "transcript.md") if workspace_exists else ""
    run_log_tail = _read_tail(workspace / "run.log") if workspace_exists else ""

    # Fallback evidence: persistent packet directory from task metadata
    packet_dir_str = str((task.get("metadata") or {}).get("packet_dir") or "").strip()
    packet_dir = Path(packet_dir_str) if packet_dir_str else None
    packet_exists = bool(packet_dir and packet_dir.exists())
    if not work_products and packet_exists:
        work_products = _list_packet_artifacts(packet_dir)
    if not transcript_tail and packet_exists:
        # The digest.md in packet dir is the closest analogue to a transcript
        transcript_tail = _read_tail(packet_dir / "digest.md")

    return {
        "action": str(action or "").strip().lower(),
        "reason": str(reason or "").strip(),
        "workspace_dir": workspace_dir,
        "work_products": work_products,
        "transcript_tail": transcript_tail,
        "run_log_tail": run_log_tail,
        "workspace_exists": workspace_exists,
        "packet_dir": packet_dir_str,
        "packet_exists": packet_exists,
    }


def _evaluate_recap(
    *,
    task: dict[str, Any],
    assignment: dict[str, Any],
    action: str,
    reason: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate a recap via LLM when available, falling back to session evidence heuristics."""
    heuristic = _evaluate_from_session_evidence(
        task=task,
        assignment=assignment,
        action=action,
        reason=reason,
        evidence=evidence,
    )
    if not _llm_recap_enabled():
        return heuristic
    try:
        generated = _call_llm_recap_evaluator(
            task=task,
            assignment=assignment,
            action=action,
            reason=reason,
            evidence=evidence,
        )
        return _normalize_llm_recap(generated, heuristic=heuristic)
    except Exception as exc:
        logger.warning("LLM proactive recap failed for task=%s: %s", task.get("task_id"), exc)
        fallback = dict(heuristic)
        fallback["evaluation_status"] = "llm_failed_fallback"
        raw = dict(fallback.get("raw_model_output") or {})
        raw["llm_error"] = str(exc)
        raw["fallback_evaluator"] = raw.get("evaluator") or "session_evidence_recap_v1"
        fallback["raw_model_output"] = raw
        return fallback


def _evaluate_from_session_evidence(
    *,
    task: dict[str, Any],
    assignment: dict[str, Any],
    action: str,
    reason: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Produce a heuristic recap from session evidence without calling an LLM."""
    title = str(task.get("title") or "").strip()
    description = str(task.get("description") or "").strip()
    result_summary = str(assignment.get("result_summary") or "").strip()
    action_norm = str(evidence.get("action") or action or "").strip().lower()
    reason_text = str(evidence.get("reason") or reason or "").strip()
    workspace_dir = str(evidence.get("workspace_dir") or "").strip()
    work_products = [str(item) for item in evidence.get("work_products") or []]
    transcript_tail = str(evidence.get("transcript_tail") or "")
    run_log_tail = str(evidence.get("run_log_tail") or "")
    workspace_exists = bool(evidence.get("workspace_exists"))

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
        workspace_exists=workspace_exists,
        workspace_dir=workspace_dir,
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


def _llm_recap_enabled() -> bool:
    """Return True when LLM recap evaluation should be attempted."""
    raw = (os.getenv("UA_PROACTIVE_RECAP_LLM_ENABLED") or "").strip().lower()
    if raw in {"1", "true", "yes", "on", "enabled"}:
        return True
    if raw in {"0", "false", "no", "off", "disabled"}:
        return False
    return bool(
        (os.getenv("ANTHROPIC_API_KEY") or "").strip()
        or (os.getenv("ANTHROPIC_AUTH_TOKEN") or "").strip()
        or (os.getenv("ZAI_API_KEY") or "").strip()
        or (os.getenv("OPENAI_API_KEY") or "").strip()
    )


def _call_llm_recap_evaluator(
    *,
    task: dict[str, Any],
    assignment: dict[str, Any],
    action: str,
    reason: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Call an LLM to evaluate the recap and return the parsed JSON response.

    Uses the Anthropic SDK (same pattern as llm_classifier.py) which correctly
    handles Z.AI model routing for glm-5.1 / glm-5-turbo model identifiers.
    The previous litellm.completion() call failed because litellm requires an
    explicit provider prefix (e.g., 'openai/glm-5.1') for non-standard models.
    """
    from anthropic import Anthropic

    model = (os.getenv("UA_PROACTIVE_RECAP_LLM_MODEL") or "").strip() or resolve_opus()
    prompt = _build_llm_recap_prompt(
        task=task,
        assignment=assignment,
        action=action,
        reason=reason,
        evidence=evidence,
    )

    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
        or ""
    ).strip()
    if not api_key:
        raise RuntimeError("No Anthropic/ZAI API key available for recap LLM")

    client_kwargs: dict[str, Any] = {"api_key": api_key, "timeout": float(LLM_TIMEOUT_SECONDS)}
    base_url = (os.getenv("ANTHROPIC_BASE_URL") or "").strip()
    if base_url:
        client_kwargs["base_url"] = base_url

    client = Anthropic(**client_kwargs)
    response = client.messages.create(
        model=model,
        max_tokens=900,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            raw_text += block.text
    raw_text = raw_text.strip()

    parsed = _loads_json_object(raw_text)
    parsed["raw_model_output"] = {
        "evaluator": "llm_recap_v1",
        "model": model,
        "raw_text": raw_text,
        "heuristic_context": {
            "work_products": evidence.get("work_products") or [],
            "transcript_tail_chars": len(str(evidence.get("transcript_tail") or "")),
            "run_log_tail_chars": len(str(evidence.get("run_log_tail") or "")),
        },
    }
    parsed["evaluation_status"] = "llm_evaluated"
    return parsed


def _build_llm_recap_prompt(
    *,
    task: dict[str, Any],
    assignment: dict[str, Any],
    action: str,
    reason: str,
    evidence: dict[str, Any],
) -> str:
    """Build the LLM prompt for recap evaluation from task and evidence context."""
    context = {
        "task": {
            "task_id": task.get("task_id"),
            "source_kind": task.get("source_kind"),
            "title": task.get("title"),
            "description": task.get("description"),
            "status": task.get("status"),
            "metadata": task.get("metadata") or {},
        },
        "terminal_action": action,
        "terminal_reason": reason,
        "latest_assignment": {
            "assignment_id": assignment.get("assignment_id"),
            "agent_id": assignment.get("agent_id"),
            "provider_session_id": assignment.get("provider_session_id"),
            "workspace_dir": assignment.get("workspace_dir"),
            "state": assignment.get("state"),
            "result_summary": assignment.get("result_summary"),
        },
        "evidence": {
            "workspace_dir": evidence.get("workspace_dir"),
            "workspace_exists": evidence.get("workspace_exists"),
            "work_products": evidence.get("work_products") or [],
            "transcript_tail": _truncate(str(evidence.get("transcript_tail") or ""), LLM_MAX_CONTEXT_CHARS // 2),
            "run_log_tail": _truncate(str(evidence.get("run_log_tail") or ""), LLM_MAX_CONTEXT_CHARS // 2),
        },
    }
    return (
        "You evaluate completed or terminal proactive autonomous work for Universal Agent.\n"
        "Return strict JSON only. Do not wrap it in markdown.\n\n"
        "Fields required:\n"
        "- idea: concise description of the original idea/opportunity\n"
        "- implemented: what concrete work was actually completed or produced\n"
        "- known_issues: defects, missing evidence, blockers, or empty string\n"
        "- success_assessment: whether this succeeded and why, grounded only in evidence\n"
        "- recommended_next_action: what Kevin/Simone should do next\n"
        "- confidence: number between 0 and 1\n\n"
        "Rules:\n"
        "- Do not treat ideation alone as completion.\n"
        "- Do not infer success from silence.\n"
        "- Mention missing artifacts/session evidence when relevant.\n"
        "- Recommend a fresh continuation session when follow-up work is needed.\n\n"
        f"Evidence bundle:\n{json.dumps(context, ensure_ascii=True, indent=2, default=str)}"
    )


def _normalize_llm_recap(raw: dict[str, Any], *, heuristic: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize LLM output, falling back to heuristic fields for missing values."""
    if not isinstance(raw, dict):
        raise ValueError("LLM recap was not an object")
    normalized = {
        "evaluation_status": "llm_evaluated",
        "idea": _clean_text(raw.get("idea")) or heuristic["idea"],
        "implemented": _clean_text(raw.get("implemented")) or heuristic["implemented"],
        "known_issues": _clean_text(raw.get("known_issues")),
        "success_assessment": _clean_text(raw.get("success_assessment")) or heuristic["success_assessment"],
        "recommended_next_action": _clean_text(raw.get("recommended_next_action")) or heuristic["recommended_next_action"],
        "confidence": _bounded_float(raw.get("confidence"), default=heuristic["confidence"]),
        "raw_model_output": raw.get("raw_model_output") if isinstance(raw.get("raw_model_output"), dict) else raw,
    }
    return normalized


def _loads_json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON object from raw text, tolerating markdown code fences."""
    text = str(raw or "").strip()
    if text.startswith("```json"):
        text = text[7:].strip()
    if text.startswith("```"):
        text = text[3:].strip()
    if text.endswith("```"):
        text = text[:-3].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM recap JSON was not an object")
    return parsed


def _clean_text(value: Any) -> str:
    """Strip and truncate a text value to 1600 characters."""
    return str(value or "").strip()[:1600]


def _bounded_float(value: Any, *, default: float) -> float:
    """Parse a float from value, clamped to [0.0, 1.0], falling back to default."""
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    return max(0.0, min(1.0, parsed))


def _truncate(value: str, limit: int) -> str:
    """Return the trailing limit characters of value when it exceeds the limit."""
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[-limit:]


def _implemented_summary(
    *,
    result_summary: str,
    reason: str,
    work_products: list[str],
    transcript_tail: str,
    run_log_tail: str,
) -> str:
    """Derive a human-readable summary of what was implemented."""
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
    workspace_exists: bool,
    workspace_dir: str,
    work_products: list[str],
) -> str:
    """Detect and describe known issues from terminal action, workspace, and artifact state."""
    issues: list[str] = []
    if action in {"block", "review", "park"}:
        issues.append(reason or result_summary or f"Terminal action was {action}.")
    if workspace_dir and not workspace_exists:
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
    """Return a qualitative success string and a 0-1 confidence score."""
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
    """Suggest a follow-up action based on terminal state and available evidence."""
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
    """List files under workspace/work_products/, capped at MAX_WORK_PRODUCTS."""
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


def _list_packet_artifacts(packet_dir: Optional[Path]) -> list[str]:
    """List evidence files in the persistent packet directory.

    Packet directories survive workspace cleanup and contain digests,
    knowledge-base updates, and implementation opportunity files that
    serve as concrete evidence of completed proactive work.
    """
    if not packet_dir or not packet_dir.exists():
        return []
    # Only count files that represent actual work output, not raw data dumps
    _EVIDENCE_SUFFIXES = {".md", ".py", ".txt", ".json", ".html", ".pdf"}
    _RAW_DATA_NAMES = {"raw_posts.json", "raw_user.json", "new_posts.json", "manifest.json"}
    out: list[str] = []
    try:
        for path in sorted(packet_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.name in _RAW_DATA_NAMES:
                continue
            if path.suffix.lower() in _EVIDENCE_SUFFIXES:
                out.append(f"packet:{path.relative_to(packet_dir)}")
                if len(out) >= MAX_WORK_PRODUCTS:
                    break
    except Exception:
        return out
    return out


def _read_tail(path: Path) -> str:
    """Return the last MAX_EXCERPT_CHARS bytes of a file, or empty string."""
    try:
        if not path.exists() or not path.is_file():
            return ""
        data = path.read_bytes()
    except Exception:
        return ""
    return data[-MAX_EXCERPT_CHARS:].decode("utf-8", errors="replace")


def _first_meaningful_line(text: str) -> str:
    """Return the first line of text that looks like human-readable prose (>=20 chars)."""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if len(line) >= 20 and not line.startswith(("{", "[", "TRACE", "DEBUG")):
            return line[:500]
    return ""


def _row_to_recap(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite3.Row into a normalized recap dict with parsed JSON fields."""
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
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()

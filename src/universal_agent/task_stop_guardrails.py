from __future__ import annotations

from dataclasses import dataclass, field
import re
import sqlite3
from typing import Any, Optional

_TASK_STOP_PLACEHOLDER_IDS = {
    "",
    "*",
    "all",
    "any",
    "every",
    "none",
    "null",
    "n/a",
    "na",
    "unknown",
    "task",
    "taskstop",
    "task-stop",
    "dummy",
    "dummy-stop",
    "placeholder",
    "example",
    "test",
    "all-tasks",
    "stop-all",
    "cancel-all",
}

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)
_OPAQUE_TASK_PREFIX_RE = re.compile(r"^(?:task|bg|background)_[0-9A-Z]{10,}$")
_TOOLU_RE = re.compile(r"^toolu_[A-Za-z0-9_]{16,}$")


@dataclass(frozen=True)
class TaskStopPolicyDecision:
    decision: str
    reason: Optional[str] = None
    system_message: str = ""
    policy_context: dict[str, Any] = field(default_factory=dict)


def extract_task_stop_id(tool_input: dict[str, Any]) -> str:
    for key in ("task_id", "id", "target_task_id"):
        value = tool_input.get(key)
        if value is None:
            continue
        return str(value).strip()
    return ""


def task_stop_rejection_reason(task_id: str) -> Optional[str]:
    clean_id = str(task_id or "").strip()
    if not clean_id:
        return "Missing `task_id`."

    lowered = clean_id.lower()
    if "," in clean_id:
        return "Multiple task IDs are not supported in a single call."
    if lowered in _TASK_STOP_PLACEHOLDER_IDS:
        return f"Invalid placeholder `task_id` ({clean_id!r})."
    if lowered.startswith(("session_", "run_")):
        return f"Invalid session/run identifier used as task_id ({clean_id!r})."

    if _UUID_RE.fullmatch(clean_id):
        return None
    if _OPAQUE_TASK_PREFIX_RE.fullmatch(clean_id):
        return None
    if _TOOLU_RE.fullmatch(clean_id):
        return None

    body = clean_id
    for prefix in ("task_", "bg_", "background_"):
        if lowered.startswith(prefix):
            body = clean_id[len(prefix) :]
            break

    if len(body) < 10:
        return (
            f"Untrusted `task_id` ({clean_id!r}): too short. "
            "Real SDK task IDs are opaque tokens."
        )

    if re.fullmatch(r"[a-z]+(?:_[a-z0-9]+)+", body.lower()):
        if re.search(r"[a-z]+_[a-z]+", body.lower()):
            return (
                f"Untrusted `task_id` ({clean_id!r}): human-readable, human-composed. "
                "Real SDK task IDs are opaque tokens, not word-based labels."
            )
        return (
            f"Untrusted `task_id` ({clean_id!r}): human-readable. "
            "Real SDK task IDs are opaque tokens, not descriptive names."
        )

    if re.search(r"[a-z]{3,}", body):
        return (
            f"Untrusted `task_id` ({clean_id!r}): human-readable. "
            "Real SDK task IDs are opaque tokens, not descriptive names."
        )

    return None


def normalize_task_stop_run_kind(run_kind: Any) -> str:
    return str(run_kind or "").strip().lower()


def resolve_task_stop_run_kind(
    runtime_db_conn: sqlite3.Connection | None,
    run_id: str | None,
    *,
    explicit_run_kind: Any = None,
) -> str:
    normalized = normalize_task_stop_run_kind(explicit_run_kind)
    if normalized:
        return normalized
    if not runtime_db_conn or not run_id:
        return ""
    try:
        from universal_agent.durable.state import get_run

        row = get_run(runtime_db_conn, str(run_id))
    except Exception:
        return ""
    if not row:
        return ""
    try:
        return normalize_task_stop_run_kind(row["run_kind"])
    except Exception:
        return normalize_task_stop_run_kind(getattr(row, "run_kind", ""))


def run_has_sdk_task_stop_evidence(
    runtime_db_conn: sqlite3.Connection | None,
    run_id: str | None,
) -> bool:
    if not runtime_db_conn or not run_id:
        return False
    try:
        row = runtime_db_conn.execute(
            """
            SELECT 1
            FROM tool_calls
            WHERE run_id = ?
              AND (
                (LOWER(COALESCE(tool_name, '')) = 'task' AND LOWER(COALESCE(tool_namespace, '')) = 'claude_code')
                OR LOWER(COALESCE(tool_name, '')) = 'agent'
                OR LOWER(COALESCE(raw_tool_name, '')) IN ('task', 'agent')
              )
            LIMIT 1
            """,
            (str(run_id),),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def _is_hard_blocked_run_kind(run_kind: str) -> bool:
    return run_kind in {"todo_execution", "email_triage"} or run_kind.startswith("heartbeat")


def _lane_guidance(run_kind: str) -> str:
    if run_kind == "todo_execution":
        return (
            "This run is already inside the canonical Task Hub execution lane.\n"
            "Do not use `TaskStop` here.\n"
            "Instead: continue execution or disposition the assigned Task Hub item via "
            "`task_hub_task_action` with `complete`, `review`, `block`, or `park`."
        )
    if run_kind == "email_triage":
        return (
            "This run is triage-only.\n"
            "Do not use `TaskStop` and do not attempt final delivery from this lane.\n"
            "Instead: finish triage, record metadata, and let the dedicated ToDo executor own execution."
        )
    if run_kind.startswith("heartbeat"):
        return (
            "This run is a heartbeat/proactive workflow.\n"
            "Do not use `TaskStop` here.\n"
            "Instead: continue the health/proactive flow and use the canonical workflow tools for the next action."
        )
    return (
        "No active SDK-managed task is known in this run.\n"
        "Instead: continue the workflow directly, relaunch the correct `Task(...)` delegate if you truly need a subagent, "
        "or use the relevant MCP/native tool directly."
    )


def _block_message(*, reason: str, run_kind: str, circuit_breaker: bool) -> str:
    if circuit_breaker:
        return (
            "⛔ Circuit-breaker: `TaskStop` blocked after repeated invalid attempts.\n\n"
            f"{reason}\n\n"
            f"{_lane_guidance(run_kind)}"
        )
    return (
        "⚠️ Invalid `TaskStop` request blocked.\n\n"
        f"{reason}\n\n"
        f"{_lane_guidance(run_kind)}"
    )


def evaluate_task_stop_policy(
    *,
    task_id: str,
    runtime_db_conn: sqlite3.Connection | None,
    run_id: str | None,
    explicit_run_kind: Any = None,
    already_stopped: bool = False,
    consecutive_failures: int = 0,
) -> TaskStopPolicyDecision:
    run_kind = resolve_task_stop_run_kind(
        runtime_db_conn,
        run_id,
        explicit_run_kind=explicit_run_kind,
    )
    has_sdk_task_evidence = run_has_sdk_task_stop_evidence(runtime_db_conn, run_id)
    reason_code = ""
    reason: Optional[str] = None

    if already_stopped:
        reason_code = "duplicate_taskstop"
        reason = f"Duplicate stop request for task_id {task_id!r}."
    else:
        reason = task_stop_rejection_reason(task_id)
        if reason:
            reason_code = "invalid_task_id"
        elif _is_hard_blocked_run_kind(run_kind):
            reason_code = "disallowed_run_kind"
            reason = (
                f"`TaskStop` is not valid in run_kind={run_kind!r}. "
                "This lane does not use SDK task-stop lifecycle control."
            )
        elif not has_sdk_task_evidence:
            reason_code = "missing_sdk_task_evidence"
            reason = "No active SDK-managed task is known in this run."

    policy_context = {
        "run_kind": run_kind or "unknown",
        "has_sdk_task_evidence": has_sdk_task_evidence,
        "already_stopped": already_stopped,
        "reason_code": reason_code,
        "task_id": task_id,
    }

    if reason is None:
        return TaskStopPolicyDecision(
            decision="allow",
            policy_context=policy_context,
        )

    circuit_breaker = consecutive_failures >= 2
    if circuit_breaker:
        policy_context["reason_code"] = "circuit_breaker"
        policy_context["base_reason_code"] = reason_code
    return TaskStopPolicyDecision(
        decision="block",
        reason=reason,
        system_message=_block_message(
            reason=reason,
            run_kind=run_kind,
            circuit_breaker=circuit_breaker,
        ),
        policy_context=policy_context,
    )

import json
from typing import Any, Iterable, Optional

from ..durable.db import connect_runtime_db
from ..durable.state import request_run_cancel


def _load_run_spec(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _extract_workspace_dir(run_spec_json: str) -> Optional[str]:
    run_spec = _load_run_spec(run_spec_json)
    workspace_dir = run_spec.get("workspace_dir")
    if isinstance(workspace_dir, str) and workspace_dir.strip():
        return workspace_dir
    return None


def list_runs(
    statuses: Optional[Iterable[str]] = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    conn = connect_runtime_db()
    where_clause = ""
    params: list[Any] = []
    status_list = [status for status in (statuses or []) if status]
    if status_list:
        placeholders = ", ".join("?" for _ in status_list)
        where_clause = f"WHERE status IN ({placeholders})"
        params.extend(status_list)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT run_id, created_at, updated_at, status, run_mode, job_path, run_spec_json
        FROM runs
        {where_clause}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    results = []
    for row in rows:
        results.append(
            {
                "run_id": row["run_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "status": row["status"],
                "run_mode": row["run_mode"],
                "job_path": row["job_path"],
                "workspace_dir": _extract_workspace_dir(row["run_spec_json"]),
            }
        )
    return results


def get_run(run_id: str) -> Optional[dict[str, Any]]:
    conn = connect_runtime_db()
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if not row:
        return None
    run_spec_json = row["run_spec_json"]
    return {
        **dict(row),
        "workspace_dir": _extract_workspace_dir(run_spec_json),
        "run_spec": _load_run_spec(run_spec_json),
    }


def get_last_checkpoint(run_id: str) -> Optional[dict[str, Any]]:
    conn = connect_runtime_db()
    row = conn.execute(
        """
        SELECT checkpoint_id, created_at, checkpoint_type, step_id
        FROM checkpoints
        WHERE run_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    return dict(row) if row else None


def list_tool_calls(run_id: str, limit: int = 10) -> list[dict[str, Any]]:
    conn = connect_runtime_db()
    rows = conn.execute(
        """
        SELECT tool_call_id, created_at, updated_at, raw_tool_name, tool_name, tool_namespace,
               status, replay_policy, idempotency_key
        FROM tool_calls
        WHERE run_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (run_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def request_cancel(run_id: str, reason: Optional[str]) -> bool:
    conn = connect_runtime_db()
    row = conn.execute("SELECT run_id FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if not row:
        return False
    request_run_cancel(conn, run_id, reason)
    return True


def tail_tool_calls(run_id: str, limit: int = 20) -> list[dict[str, Any]]:
    conn = connect_runtime_db()
    rows = conn.execute(
        """
        SELECT tool_call_id, created_at, raw_tool_name, tool_name, tool_namespace, status, replay_policy
        FROM tool_calls
        WHERE run_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (run_id, limit),
    ).fetchall()
    return [dict(row) for row in reversed(rows)]

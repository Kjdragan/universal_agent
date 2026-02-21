from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from claude_agent_sdk import tool

from universal_agent.durable.db import connect_runtime_db, get_vp_db_path
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import get_vp_mission, list_vp_events, list_vp_missions
from universal_agent.vp.dispatcher import (
    MissionDispatchRequest,
    cancel_mission,
    dispatch_mission_with_retry,
    is_sqlite_lock_error,
)

_TERMINAL_MISSION_STATUSES = {"completed", "failed", "cancelled"}
_TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".log",
    ".py",
    ".ts",
    ".js",
    ".tsx",
    ".jsx",
    ".toml",
    ".ini",
    ".cfg",
    ".html",
    ".css",
    ".xml",
    ".sql",
}


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, indent=2, sort_keys=True, default=str),
            }
        ]
    }


def _error_payload(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
        },
    }


def _connect_vp_db() -> sqlite3.Connection:
    conn = connect_runtime_db(get_vp_db_path())
    ensure_schema(conn)
    return conn


def _parse_json(raw: Any) -> Any:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _parse_iso_ts(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _mission_duration_seconds(started_at: Any, completed_at: Any) -> Optional[float]:
    started = _parse_iso_ts(started_at)
    completed = _parse_iso_ts(completed_at)
    if not started or not completed:
        return None
    return max(0.0, (completed - started).total_seconds())


def _mission_to_dict(row: Any) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    payload = {key: row[key] for key in row.keys()} if hasattr(row, "keys") else dict(row)
    budget = _parse_json(payload.get("budget_json"))
    if isinstance(budget, dict):
        payload["budget"] = budget
    mission_payload = _parse_json(payload.get("payload_json"))
    if isinstance(mission_payload, dict):
        payload["payload"] = mission_payload
    payload["duration_seconds"] = _mission_duration_seconds(
        payload.get("started_at"),
        payload.get("completed_at"),
    )
    return payload


def _event_to_dict(row: Any) -> dict[str, Any]:
    payload = {key: row[key] for key in row.keys()} if hasattr(row, "keys") else dict(row)
    payload["payload"] = _parse_json(payload.get("payload_json"))
    return payload


def _failure_detail(events: list[dict[str, Any]]) -> Optional[str]:
    for event in reversed(events):
        event_type = str(event.get("event_type") or "")
        if event_type not in {"vp.mission.failed", "vp.mission.cancelled"}:
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        for key in ("error", "message", "reason"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _artifact_candidates(workspace_root: Path) -> list[Path]:
    if not workspace_root.exists():
        return []
    files = [path for path in workspace_root.rglob("*") if path.is_file()]
    files.sort(key=lambda item: str(item))
    return files


def _is_probably_text(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_EXTENSIONS


def _safe_read_excerpt(path: Path, max_bytes: int) -> Optional[str]:
    if max_bytes <= 0 or not _is_probably_text(path):
        return None
    try:
        raw = path.read_bytes()[:max_bytes]
    except Exception:
        return None
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


@tool(
    name="vp_dispatch_mission",
    description="Dispatch an external VP mission through the internal VP ledger.",
    input_schema={
        "vp_id": str,
        "objective": str,
        "mission_type": str,
        "constraints": dict,
        "budget": dict,
        "idempotency_key": str,
        "priority": int,
        "reply_mode": str,
    },
)
async def vp_dispatch_mission_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    return await _vp_dispatch_mission_impl(args)


async def _vp_dispatch_mission_impl(args: dict[str, Any]) -> dict[str, Any]:
    vp_id = str(args.get("vp_id") or "").strip()
    objective = str(args.get("objective") or "").strip()
    if not vp_id:
        return _result(_error_payload("validation_error", "vp_id is required"))
    if not objective:
        return _result(_error_payload("validation_error", "objective is required"))

    mission_type = str(args.get("mission_type") or "task").strip() or "task"
    constraints = args.get("constraints") if isinstance(args.get("constraints"), dict) else {}
    budget = args.get("budget") if isinstance(args.get("budget"), dict) else {}
    reply_mode = str(args.get("reply_mode") or "async").strip() or "async"
    priority = int(args.get("priority") or 100)
    raw_idempotency = str(args.get("idempotency_key") or "").strip()
    idempotency_key = raw_idempotency or f"vp-tool-{uuid.uuid4().hex}"

    conn = _connect_vp_db()
    try:
        row = dispatch_mission_with_retry(
            conn=conn,
            request=MissionDispatchRequest(
                vp_id=vp_id,
                mission_type=mission_type,
                objective=objective,
                constraints=constraints,
                budget=budget,
                idempotency_key=idempotency_key,
                source_session_id=str(args.get("source_session_id") or "internal.vp_tool"),
                source_turn_id=str(args.get("source_turn_id") or uuid.uuid4().hex),
                reply_mode=reply_mode,
                priority=priority,
                run_id=str(args.get("run_id") or "").strip() or None,
            ),
        )
        mission = _mission_to_dict(row) or {}
        return _result(
            {
                "ok": True,
                "mission_id": mission.get("mission_id"),
                "status": mission.get("status"),
                "vp_id": mission.get("vp_id"),
                "queued_at": mission.get("created_at"),
                "mission": mission,
            }
        )
    except ValueError as exc:
        return _result(_error_payload("validation_error", str(exc)))
    except sqlite3.OperationalError as exc:
        if is_sqlite_lock_error(exc):
            return _result(_error_payload("vp_db_locked", str(exc), retryable=True))
        return _result(_error_payload("sqlite_error", str(exc)))
    except Exception as exc:
        return _result(_error_payload("dispatch_failed", str(exc)))
    finally:
        conn.close()


@tool(
    name="vp_get_mission",
    description="Get mission state and lifecycle details for one VP mission id.",
    input_schema={"mission_id": str},
)
async def vp_get_mission_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    return await _vp_get_mission_impl(args)


async def _vp_get_mission_impl(args: dict[str, Any]) -> dict[str, Any]:
    mission_id = str(args.get("mission_id") or "").strip()
    if not mission_id:
        return _result(_error_payload("validation_error", "mission_id is required"))

    conn = _connect_vp_db()
    try:
        row = get_vp_mission(conn, mission_id)
        if row is None:
            return _result(_error_payload("not_found", f"Mission not found: {mission_id}"))
        mission = _mission_to_dict(row) or {}
        events = [_event_to_dict(item) for item in list_vp_events(conn, mission_id=mission_id, limit=100)]
        return _result(
            {
                "ok": True,
                "mission": mission,
                "terminal": str(mission.get("status") or "").lower() in _TERMINAL_MISSION_STATUSES,
                "failure_detail": _failure_detail(events),
                "events": events,
            }
        )
    except Exception as exc:
        return _result(_error_payload("lookup_failed", str(exc)))
    finally:
        conn.close()


@tool(
    name="vp_list_missions",
    description="List VP missions by vp_id/status with deterministic ordering.",
    input_schema={"vp_id": str, "status": str, "limit": int},
)
async def vp_list_missions_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    return await _vp_list_missions_impl(args)


async def _vp_list_missions_impl(args: dict[str, Any]) -> dict[str, Any]:
    vp_id_raw = str(args.get("vp_id") or "").strip()
    vp_id = vp_id_raw or None
    status_raw = str(args.get("status") or "all").strip().lower()
    limit = max(1, min(int(args.get("limit") or 50), 500))

    statuses = None
    if status_raw and status_raw != "all":
        statuses = [item.strip() for item in status_raw.split(",") if item.strip()]

    conn = _connect_vp_db()
    try:
        rows = list_vp_missions(conn, vp_id=vp_id, statuses=statuses, limit=limit)
        missions = [_mission_to_dict(row) for row in rows]
        return _result(
            {
                "ok": True,
                "count": len(missions),
                "vp_id": vp_id,
                "status_filter": statuses or ["all"],
                "missions": missions,
            }
        )
    except Exception as exc:
        return _result(_error_payload("list_failed", str(exc)))
    finally:
        conn.close()


@tool(
    name="vp_wait_mission",
    description="Wait for mission terminal state with bounded timeout/polling.",
    input_schema={"mission_id": str, "timeout_seconds": int, "poll_seconds": int},
)
async def vp_wait_mission_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    return await _vp_wait_mission_impl(args)


async def _vp_wait_mission_impl(args: dict[str, Any]) -> dict[str, Any]:
    mission_id = str(args.get("mission_id") or "").strip()
    if not mission_id:
        return _result(_error_payload("validation_error", "mission_id is required"))

    timeout_seconds = max(1, min(int(args.get("timeout_seconds") or 300), 3600))
    poll_seconds = max(1, min(int(args.get("poll_seconds") or 3), 30))
    started = time.monotonic()

    while True:
        conn = _connect_vp_db()
        try:
            row = get_vp_mission(conn, mission_id)
            if row is None:
                return _result(_error_payload("not_found", f"Mission not found: {mission_id}"))
            mission = _mission_to_dict(row) or {}
            status = str(mission.get("status") or "").lower()
            if status in _TERMINAL_MISSION_STATUSES:
                return _result(
                    {
                        "ok": True,
                        "timed_out": False,
                        "mission": mission,
                    }
                )
        finally:
            conn.close()

        elapsed = time.monotonic() - started
        if elapsed >= timeout_seconds:
            return _result(
                {
                    "ok": True,
                    "timed_out": True,
                    "timeout_seconds": timeout_seconds,
                    "mission_id": mission_id,
                }
            )
        await asyncio.sleep(poll_seconds)


@tool(
    name="vp_cancel_mission",
    description="Request cancellation for a queued/running VP mission.",
    input_schema={"mission_id": str, "reason": str},
)
async def vp_cancel_mission_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    return await _vp_cancel_mission_impl(args)


async def _vp_cancel_mission_impl(args: dict[str, Any]) -> dict[str, Any]:
    mission_id = str(args.get("mission_id") or "").strip()
    if not mission_id:
        return _result(_error_payload("validation_error", "mission_id is required"))
    reason = str(args.get("reason") or "cancel_requested").strip() or "cancel_requested"

    conn = _connect_vp_db()
    try:
        cancelled = cancel_mission(conn, mission_id, reason=reason)
        if not cancelled:
            return _result(_error_payload("not_found", f"Mission not found or not cancellable: {mission_id}"))
        mission = _mission_to_dict(get_vp_mission(conn, mission_id)) or {}
        return _result(
            {
                "ok": True,
                "status": "cancel_requested",
                "mission": mission,
            }
        )
    except Exception as exc:
        return _result(_error_payload("cancel_failed", str(exc)))
    finally:
        conn.close()


@tool(
    name="vp_read_result_artifacts",
    description="Summarize mission output artifacts from workspace result_ref.",
    input_schema={"mission_id": str, "max_files": int, "max_bytes": int},
)
async def vp_read_result_artifacts_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    return await _vp_read_result_artifacts_impl(args)


async def _vp_read_result_artifacts_impl(args: dict[str, Any]) -> dict[str, Any]:
    mission_id = str(args.get("mission_id") or "").strip()
    if not mission_id:
        return _result(_error_payload("validation_error", "mission_id is required"))

    max_files = max(1, min(int(args.get("max_files") or 20), 200))
    max_bytes = max(256, min(int(args.get("max_bytes") or 200_000), 2_000_000))

    conn = _connect_vp_db()
    try:
        mission_row = get_vp_mission(conn, mission_id)
        if mission_row is None:
            return _result(_error_payload("not_found", f"Mission not found: {mission_id}"))
        mission = _mission_to_dict(mission_row) or {}
        result_ref = str(mission.get("result_ref") or "").strip()
        if not result_ref.startswith("workspace://"):
            return _result(
                _error_payload(
                    "artifact_location_unavailable",
                    f"Mission result_ref is not a workspace URI: {result_ref or '<empty>'}",
                )
            )

        workspace_root = Path(result_ref.replace("workspace://", "", 1)).expanduser().resolve()
        if not workspace_root.exists():
            return _result(
                _error_payload(
                    "artifact_workspace_missing",
                    f"Workspace path does not exist: {workspace_root}",
                )
            )

        files = _artifact_candidates(workspace_root)
        indexed_files: list[dict[str, Any]] = []
        consumed = 0
        for file_path in files:
            if len(indexed_files) >= max_files:
                break
            relpath = str(file_path.relative_to(workspace_root))
            size = int(file_path.stat().st_size)

            remaining = max(0, max_bytes - consumed)
            excerpt = _safe_read_excerpt(file_path, min(remaining, 12_000))
            if excerpt:
                consumed += len(excerpt.encode("utf-8", errors="ignore"))

            indexed_files.append(
                {
                    "path": relpath,
                    "bytes": size,
                    "excerpt": excerpt,
                    "excerpt_truncated": bool(excerpt and size > len(excerpt.encode("utf-8", errors="ignore"))),
                }
            )

        return _result(
            {
                "ok": True,
                "mission_id": mission_id,
                "result_ref": result_ref,
                "workspace_root": str(workspace_root),
                "files_indexed": len(indexed_files),
                "files_total": len(files),
                "max_files": max_files,
                "max_bytes": max_bytes,
                "artifacts": indexed_files,
            }
        )
    except Exception as exc:
        return _result(_error_payload("artifact_read_failed", str(exc)))
    finally:
        conn.close()

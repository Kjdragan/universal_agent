from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Optional
import uuid

from claude_agent_sdk import tool

from universal_agent.durable.db import (
    connect_runtime_db,
    get_activity_db_path,
    get_vp_db_path,
)
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import (
    get_vp_mission,
    list_vp_events,
    list_vp_missions,
)
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


def _error_payload(
    code: str, message: str, *, retryable: bool = False
) -> dict[str, Any]:
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
    payload = (
        {key: row[key] for key in row.keys()} if hasattr(row, "keys") else dict(row)
    )
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
    payload = (
        {key: row[key] for key in row.keys()} if hasattr(row, "keys") else dict(row)
    )
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
    description="Dispatch an external VP mission through the internal VP ledger. ALWAYS provide `idempotency_key` (e.g. `task-<task_id>`) to prevent duplicate dispatches if execution is interrupted.",
    input_schema={
        "vp_id": str,
        "objective": str,
        "mission_type": str,
        "constraints": dict,
        "budget": dict,
        "idempotency_key": str,
        "priority": int,
        "reply_mode": str,
        "execution_mode": str,
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
    constraints = (
        args.get("constraints") if isinstance(args.get("constraints"), dict) else {}
    )
    budget = args.get("budget") if isinstance(args.get("budget"), dict) else {}
    objective = _with_preference_context(
        vp_id=vp_id,
        objective=objective,
        mission_type=mission_type,
        constraints=constraints,
    )
    reply_mode = str(args.get("reply_mode") or "async").strip() or "async"
    priority = int(args.get("priority") or 100)
    raw_idempotency = str(args.get("idempotency_key") or "").strip()
    idempotency_key = raw_idempotency or f"vp-tool-{uuid.uuid4().hex}"

    # Priority tier resolution. Callers normally omit this — the queue
    # layer auto-resolves from mission_type via vp.mission_priority. We
    # only need to validate an explicit override here so a typo doesn't
    # land an unknown tier value in the DB.
    raw_priority_tier = str(args.get("priority_tier") or "").strip() or None
    if raw_priority_tier is not None:
        try:
            from universal_agent.vp.mission_priority import TIERS, is_valid_tier
            if not is_valid_tier(raw_priority_tier):
                return _result(
                    _error_payload(
                        "validation_error",
                        f"priority_tier must be one of {list(TIERS)}; got {raw_priority_tier!r}",
                    )
                )
        except Exception:
            # If the constants module ever fails to import, accept whatever
            # the caller gave us and let the DB default catch malformed
            # values via the NOT NULL constraint.
            pass

    source_session_id = str(args.get("source_session_id") or "").strip()
    run_id = str(args.get("run_id") or "").strip()
    
    try:
        from universal_agent.session_ctx import get_ctx
        ctx = get_ctx()
        if ctx:
            if not source_session_id:
                source_session_id = str(ctx.trace.get("provider_session_id") or ctx.run_id or "").strip()
            if not run_id:
                run_id = str(ctx.run_id or "").strip()
    except Exception:
        pass
        
    if not source_session_id:
        source_session_id = "internal.vp_tool"

    # Hermes Phase E — resolve cody_mode and plumb it into mission
    # metadata so vp_missions.payload_json carries the toggle for the
    # VP worker (CLI/SDK adapter) to honor. Resolution order:
    #   1. explicit args["cody_mode"]
    #   2. task row's cody_mode (if a linked task_id is supplied)
    #   3. DB setting `cody_default_mode` (operator UI toggle)
    #   4. UA_CODY_DEFAULT_MODE env
    #   5. "anthropic" hardcoded fallback (flipped 2026-05-11 PM)
    raw_metadata = (
        args.get("metadata") if isinstance(args.get("metadata"), dict) else {}
    )
    # ``linked_task`` is hoisted out of the cody_mode resolution branch so the
    # use_goal_loop inheritance block below can read it regardless of which
    # branch ran. ``linked_task_id`` is also hoisted so we can attempt to
    # load the linked task when an explicit cody_mode was passed (otherwise
    # we'd skip inheritance for any caller that supplies cody_mode directly).
    linked_task_id = str(args.get("task_id") or raw_metadata.get("task_id") or "").strip()
    linked_task: dict[str, Any] | None = None

    # PR #490c fallback — when the caller (Simone's LLM via the
    # vp_dispatch_mission tool) didn't include task_id, auto-discover it
    # from the orchestrator's currently-seized assignment. Without this
    # fallback every operator-dispatched Cody mission loses the
    # parent-task linkage: PR #490's record_cody_dispatch_metadata
    # silently no-ops, mission_receipt.task_id stays null, and the Task
    # Hub card never accumulates a Delegation Trace. Empirically
    # observed in the post-PR-#490 smoke test on production
    # (vp-mission-5cedd30dd387a10374b88359, 2026-05-27 04:30).
    #
    # The inner ``vp_dispatch_mission`` tool call gets
    # ``source_session_id="internal.vp_tool"`` because the SDK adapter
    # doesn't propagate session context through tool calls — so we use
    # the unfiltered query (latest seized assignment overall). At the
    # moment of a vp_dispatch_mission call there's typically exactly
    # one orchestrator-claimed task in flight, so this matches the right
    # parent.
    if not linked_task_id:
        try:
            from universal_agent import task_hub as _th
            from universal_agent.durable.db import (
                connect_runtime_db,
                get_activity_db_path,
            )

            with connect_runtime_db(get_activity_db_path()) as _th_conn:
                # If we have a non-trivial source_session_id, narrow the
                # search to that agent. Otherwise (internal.vp_tool /
                # empty) fall back to the latest seized assignment
                # overall — see helper docstring for the rationale.
                agent_slug = (
                    source_session_id
                    if source_session_id and source_session_id != "internal.vp_tool"
                    else ""
                )
                discovered = _th.find_recent_active_task_for_agent(
                    _th_conn, agent_slug=agent_slug
                )
                if discovered:
                    linked_task_id = discovered
                    import logging as _logging

                    _logging.getLogger(__name__).info(
                        "vp_dispatch_mission: auto-discovered linked_task_id=%s "
                        "(source_session_id=%s, caller did not include task_id in args)",
                        linked_task_id,
                        source_session_id or "<empty>",
                    )
        except Exception:
            # Best-effort — if the lookup fails for any reason, fall
            # through to the no-linkage path the rest of this function
            # already handles silently.
            pass

    explicit_cody_mode = str(args.get("cody_mode") or raw_metadata.get("cody_mode") or "").strip().lower()
    if explicit_cody_mode in {"zai", "anthropic"}:
        resolved_cody_mode: str = explicit_cody_mode
        # Still try to load the linked task for use_goal_loop inheritance.
        if linked_task_id:
            try:
                from universal_agent import task_hub as _th
                from universal_agent.durable.db import (
                    connect_runtime_db,
                    get_activity_db_path,
                )
                with connect_runtime_db(get_activity_db_path()) as _th_conn:
                    linked_task = _th.get_item(_th_conn, linked_task_id)
            except Exception:
                linked_task = None
    else:
        from universal_agent.services.cody_mode import resolve_cody_mode

        # Load the linked task row (if any) and pass the same conn into
        # the resolver so it can read the operator DB setting.
        th_conn = None
        try:
            from universal_agent import (
                task_hub,  # noqa: F401 (used implicitly via resolve_cody_mode)
            )
            from universal_agent.durable.db import (
                connect_runtime_db,
                get_activity_db_path,
            )

            th_conn = connect_runtime_db(get_activity_db_path())
            if linked_task_id:
                try:
                    from universal_agent import task_hub as _th
                    linked_task = _th.get_item(th_conn, linked_task_id)
                except Exception:
                    linked_task = None
            resolved_cody_mode = resolve_cody_mode(linked_task, conn=th_conn)
        except Exception:
            # Resolver couldn't reach the DB; fall through to env/hardcoded.
            resolved_cody_mode = resolve_cody_mode(linked_task)
        finally:
            if th_conn is not None:
                try:
                    th_conn.close()
                except Exception:
                    pass

    mission_metadata = dict(raw_metadata)
    mission_metadata["cody_mode"] = resolved_cody_mode

    # Propagate the originating Task Hub task_id into mission metadata so
    # the spawned worker (CLI or SDK) can attach its identifiers (CLI
    # session_id, workspace_dir, etc.) back to the parent task row. Without
    # this, the worker sees task_id="" and the entire Phase F.1 bookkeeping
    # plus the Workspace-button deep-link fix from PR #488 silently no-op.
    if linked_task_id:
        mission_metadata["linked_task_id"] = linked_task_id

    # Inherit use_goal_loop from the linked task hub item's metadata if not
    # already set on the mission. This is the wiring that makes the dashboard
    # "Dispatch Mission" UI automatically activate /goal for operator-dispatched
    # Cody work: the dashboard endpoint sets ``metadata.use_goal_loop=True`` on
    # the task hub item when target_agent=vp.coder.primary, and this block
    # propagates it onto the vp_missions row where
    # ``is_goal_eligible_mission`` reads it.
    # An explicit ``args["metadata"]["use_goal_loop"]`` value (True or False)
    # always wins over the linked-task default.
    if "use_goal_loop" not in raw_metadata and linked_task:
        linked_meta = linked_task.get("metadata") if isinstance(linked_task, dict) else None
        if isinstance(linked_meta, dict) and bool(linked_meta.get("use_goal_loop")):
            mission_metadata["use_goal_loop"] = True

    # Hermes Phase E.2 routing rule (refined 2026-05-26): when the
    # resolved cody_mode is "anthropic", FORCE execution_mode="cli" so
    # the spawned `claude` subprocess uses workspace-local OAuth
    # (Anthropic Max) instead of the gateway's ZAI-routed env. SDK
    # in-process mode cannot easily flip ANTHROPIC_* per-call without
    # races; CLI mode gives Cody true environmental autonomy
    # (own PID/env/workspace) — and is the ONLY mode where Anthropic
    # features like `/goal` actually function.
    #
    # Previously, an explicit ``args["execution_mode"]`` could override
    # this — the intent was operator-driven exceptions (e.g. "dag" for
    # deterministic flows). In practice it became a footgun: Simone
    # (running on ZAI) defaulted to ``execution_mode="autonomous"`` on
    # the 2026-05-26 /goal smoke test, which overrode the Anthropic
    # auto-route, dropped the mission onto the SDK in-process adapter
    # (ZAI-routed), and ran the mission on glm-5 instead of Claude. The
    # `/goal` loop was never possible because /goal is an Anthropic
    # Claude Code feature, and the COMPLETION-attestation guard
    # spuriously demoted the (successful) mission to failed.
    #
    # Fix: cody_mode is the source of truth for "use Anthropic." If the
    # operator wants the SDK in-process path, they should pass
    # ``cody_mode="zai"`` (which conveys the intent properly); explicit
    # ``execution_mode`` only overrides in non-anthropic mode.
    explicit_exec_mode = str(args.get("execution_mode") or "").strip().lower()
    if resolved_cody_mode == "anthropic":
        resolved_execution_mode = "cli"
        if explicit_exec_mode and explicit_exec_mode != "cli":
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "vp_dispatch_mission: ignoring explicit execution_mode=%r because "
                "cody_mode='anthropic' requires execution_mode='cli' (Anthropic "
                "endpoint + workspace OAuth). To use the SDK in-process path, set "
                "cody_mode='zai' instead.",
                explicit_exec_mode,
            )
    elif explicit_exec_mode:
        resolved_execution_mode = explicit_exec_mode
    else:
        resolved_execution_mode = "sdk"

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
                source_session_id=source_session_id,
                source_turn_id=str(args.get("source_turn_id") or uuid.uuid4().hex),
                reply_mode=reply_mode,
                priority=priority,
                priority_tier=raw_priority_tier,
                run_id=run_id or None,
                execution_mode=resolved_execution_mode,
                metadata=mission_metadata,
            ),
        )
        mission = _mission_to_dict(row) or {}

        # Register in Task Hub for Kanban board visibility
        try:
            from universal_agent import task_hub
            from universal_agent.durable.db import (
                connect_runtime_db,
                get_activity_db_path,
            )

            th_conn = connect_runtime_db(get_activity_db_path())
            task_hub.ensure_schema(th_conn)
            task_hub.upsert_item(
                th_conn,
                {
                    "task_id": mission.get("mission_id"),
                    "title": objective[:200],
                    "description": objective,
                    "status": task_hub.TASK_STATUS_DELEGATED,
                    "source_kind": "vp_mission",
                    "source_ref": vp_id,
                    "mirror_status": "external",
                    "trigger_type": "vp_dispatch",
                    # vp_mission rows are visibility mirrors only — VP workers
                    # claim by mission_id directly, NOT through the dispatch
                    # sweep. Setting agent_ready=False keeps the row out of
                    # the queue eligibility check (task_hub.py:1387) so a
                    # non-VP claimer (e.g. daemon_simone_todo) can't pick it
                    # up if reopen_stale_delegations flips status back to
                    # OPEN. Closes the recurrence path that produced the
                    # 2026-05-07 rogue-branch incident — see
                    # docs/operations/2026-05-07_open_followups.md Followup #3.
                    "agent_ready": False,
                    "metadata": {
                        "vp_id": vp_id,
                        "mission_type": mission_type,
                        "dispatch_channel": "agent_tool",
                    },
                },
            )
            th_conn.close()
        except Exception:
            pass  # Task Hub registration is best-effort

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


async def dispatch_vp_mission(
    *,
    objective: str,
    mission_type: str,
    idempotency_key: str,
    vp_id: str = "vp.general.primary",
    execution_mode: str = "sdk",
    source_session_id: str = "",
    **extra_args,
) -> dict[str, Any]:
    """Convenience wrapper that dispatches a VP mission and unwraps the result.

    Returns the inner payload dict (e.g. ``{"ok": True, "mission_id": ...}``).
    Raises ``RuntimeError`` on dispatch failure or unexpected result shape.
    """
    import logging

    logger = logging.getLogger(__name__)
    args: dict[str, Any] = {
        "vp_id": vp_id,
        "objective": objective,
        "mission_type": mission_type,
        "idempotency_key": idempotency_key,
        "execution_mode": execution_mode,
    }
    if source_session_id:
        args["source_session_id"] = source_session_id
    args.update(extra_args)

    result = await _vp_dispatch_mission_impl(args)
    text = result.get("content", [{}])[0].get("text")
    if not text:
        raise RuntimeError(f"Unexpected VP dispatch result format: {result}")
    payload = json.loads(text)
    if not payload.get("ok"):
        raise RuntimeError(f"VP dispatch failed: {payload}")
    logger.info(f"Dispatched {mission_type} mission: {payload.get('mission_id')}")
    return payload


def _with_preference_context(
    *,
    vp_id: str,
    objective: str,
    mission_type: str,
    constraints: dict[str, Any],
) -> str:
    if bool((constraints or {}).get("skip_preference_context")):
        return objective
    if vp_id not in {"vp.coder.primary", "vp.general.primary"}:
        return objective
    topic_tags = constraints.get("topic_tags")
    if not isinstance(topic_tags, list):
        topic_tags = constraints.get("tags")
    if not isinstance(topic_tags, list):
        topic_tags = []
    try:
        from universal_agent.services.proactive_preferences import (
            get_delegation_context,
        )

        with connect_runtime_db(get_activity_db_path()) as conn:
            context = get_delegation_context(
                conn,
                task_type=mission_type or vp_id,
                topic_tags=[str(tag) for tag in topic_tags],
            )
    except Exception:
        return objective
    if not context:
        return objective
    return f"{objective.rstrip()}\n\n---\nKEVIN'S PREFERENCE CONTEXT:\n{context}\n---"


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
            return _result(
                _error_payload("not_found", f"Mission not found: {mission_id}")
            )
        mission = _mission_to_dict(row) or {}
        events = [
            _event_to_dict(item)
            for item in list_vp_events(conn, mission_id=mission_id, limit=100)
        ]
        return _result(
            {
                "ok": True,
                "mission": mission,
                "terminal": str(mission.get("status") or "").lower()
                in _TERMINAL_MISSION_STATUSES,
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
    description="Wait for mission terminal state with bounded timeout/polling. For code_generation missions, use timeout_seconds=1200 or higher (default 1200, max 3600). Short timeouts risk missing completion of complex coding tasks.",
    input_schema={"mission_id": str, "timeout_seconds": int, "poll_seconds": int},
)
async def vp_wait_mission_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    return await _vp_wait_mission_impl(args)


async def _vp_wait_mission_impl(args: dict[str, Any]) -> dict[str, Any]:
    mission_id = str(args.get("mission_id") or "").strip()
    if not mission_id:
        return _result(_error_payload("validation_error", "mission_id is required"))

    timeout_seconds = max(1, min(int(args.get("timeout_seconds") or 1200), 3600))
    poll_seconds = max(1, min(int(args.get("poll_seconds") or 3), 30))
    started = time.monotonic()

    while True:
        conn = _connect_vp_db()
        try:
            row = get_vp_mission(conn, mission_id)
            if row is None:
                return _result(
                    _error_payload("not_found", f"Mission not found: {mission_id}")
                )
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
            return _result(
                _error_payload(
                    "not_found", f"Mission not found or not cancellable: {mission_id}"
                )
            )
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
            return _result(
                _error_payload("not_found", f"Mission not found: {mission_id}")
            )
        mission = _mission_to_dict(mission_row) or {}
        result_ref = str(mission.get("result_ref") or "").strip()
        if not result_ref.startswith("workspace://"):
            return _result(
                _error_payload(
                    "artifact_location_unavailable",
                    f"Mission result_ref is not a workspace URI: {result_ref or '<empty>'}",
                )
            )

        workspace_root = (
            Path(result_ref.replace("workspace://", "", 1)).expanduser().resolve()
        )
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
                    "excerpt_truncated": bool(
                        excerpt and size > len(excerpt.encode("utf-8", errors="ignore"))
                    ),
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


# ─────────────────────────────────────────────────────────────────────────
# VP failure-rescue tools (Step 2 of VP /goal + Failure-Rescue PRD)
#
# These three verbs are Simone's rescue toolset for handling
# vp_mission_failure task hub items surfaced by services/vp_failure_rescue.
# See docs/01_Architecture/12_VP_Goal_Integration_And_Failure_Rescue_PRD.md
# § 5.3 and HEARTBEAT.md "Handling vp_mission_failure items".
# ─────────────────────────────────────────────────────────────────────────


def _load_failed_mission(mission_id: str) -> Optional[dict[str, Any]]:
    """Load mission row from vp_state.db; returns dict or None."""
    if not mission_id:
        return None
    conn = _connect_vp_db()
    try:
        row = get_vp_mission(conn, mission_id)
        return _mission_to_dict(row) if row is not None else None
    finally:
        conn.close()


def _close_failure_task(activity_conn: sqlite3.Connection, mission_id: str, *, note: str, action: str) -> None:
    """Best-effort: mark the vp_failure:<mission_id> task hub item as completed.

    Simone may pull rescue actions back-to-back; we want each rescue to
    close out its corresponding informational notification so the queue
    doesn't carry stale failure rows.
    """
    if not mission_id:
        return
    try:
        from universal_agent import task_hub
        failure_task_id = f"vp_failure:{mission_id}"
        # Append a note to metadata for audit.
        item = task_hub.get_item(activity_conn, failure_task_id)
        if not item:
            return
        meta = dict(item.get("metadata") or {})
        rescue_log = list(meta.get("rescue_log") or [])
        rescue_log.append({
            "action": action,
            "note": note,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        meta["rescue_log"] = rescue_log
        task_hub.upsert_item(activity_conn, {
            "task_id": failure_task_id,
            "status": task_hub.TASK_STATUS_COMPLETED,
            "metadata": meta,
        })
    except Exception:
        pass  # never block the rescue action


def _next_chain_id(failed_mission: dict[str, Any]) -> str:
    """Derive the rescue_chain_id for a retry mission.

    If the failed mission already has metadata.rescue_chain_id, reuse it
    (extending an existing chain). Otherwise, the failed mission_id becomes
    the chain anchor.
    """
    try:
        payload = json.loads(failed_mission.get("payload_json") or "{}")
        meta = payload.get("metadata") or {}
        existing = str(meta.get("rescue_chain_id") or "").strip()
        if existing:
            return existing
    except Exception:
        pass
    return str(failed_mission.get("mission_id") or "")


def _build_retry_objective(failed_mission: dict[str, Any], additional_guidance: str) -> str:
    """Compose the new objective: original + Simone's rescue guidance."""
    original = str(failed_mission.get("objective") or "").rstrip()
    guidance = (additional_guidance or "").strip()
    if not guidance:
        return original
    return (
        f"{original}\n\n"
        f"--- SIMONE RESCUE GUIDANCE ({datetime.now(timezone.utc).isoformat()}) ---\n"
        f"This is a re-dispatch of a failed mission. Use the guidance below\n"
        f"to address the failure. The previous workspace (if any) is referenced\n"
        f"in your mission metadata.\n\n"
        f"{guidance}\n"
        f"--- END RESCUE GUIDANCE ---\n"
    )


@tool(
    name="vp_dispatch_mission_retry",
    description=(
        "Re-dispatch a failed VP mission with Simone's additional guidance, "
        "extending the same rescue chain. Use when the failure was self-reported "
        "or hit a /goal cap AND your guidance addresses the gap. Same VP, same "
        "rescue chain (so failure_count keeps growing). Closes the corresponding "
        "vp_failure:<mission_id> task hub item."
    ),
    input_schema={
        "mission_id": str,
        "additional_guidance": str,
        "max_additional_turns": int,
    },
)
async def vp_dispatch_mission_retry_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    return await _vp_dispatch_mission_retry_impl(args)


async def _vp_dispatch_mission_retry_impl(args: dict[str, Any]) -> dict[str, Any]:
    mission_id = str(args.get("mission_id") or "").strip()
    additional_guidance = str(args.get("additional_guidance") or "").strip()
    if not mission_id:
        return _result(_error_payload("validation_error", "mission_id is required"))
    if not additional_guidance:
        return _result(_error_payload(
            "validation_error",
            "additional_guidance is required — Simone must explain what to do differently",
        ))

    failed = _load_failed_mission(mission_id)
    if failed is None:
        return _result(_error_payload("not_found", f"Mission not found: {mission_id}"))

    chain_id = _next_chain_id(failed)
    new_objective = _build_retry_objective(failed, additional_guidance)

    # Preserve constraints, mission_type, vp_id from the failed mission.
    payload_meta: dict[str, Any] = {}
    try:
        payload = json.loads(failed.get("payload_json") or "{}")
        payload_meta = payload.get("metadata") or {}
    except Exception:
        pass

    dispatch_args: dict[str, Any] = {
        "vp_id": str(failed.get("vp_id") or ""),
        "objective": new_objective,
        "mission_type": str(failed.get("mission_type") or "task"),
        "idempotency_key": f"rescue-retry-{chain_id}-{uuid.uuid4().hex[:8]}",
        "metadata": {
            **payload_meta,
            "rescue_chain_id": chain_id,
            "rescue_prior_mission_id": mission_id,
            "rescue_action": "retry",
        },
    }
    max_additional_turns = args.get("max_additional_turns")
    if isinstance(max_additional_turns, int) and max_additional_turns > 0:
        dispatch_args["metadata"]["max_additional_turns"] = max_additional_turns

    try:
        result = await _vp_dispatch_mission_impl(dispatch_args)
        # Best-effort close the failure task.
        try:
            with connect_runtime_db(get_activity_db_path()) as activity_conn:
                _close_failure_task(
                    activity_conn, mission_id,
                    note=f"retried with guidance (chain={chain_id})",
                    action="retry",
                )
        except Exception:
            pass
        return result
    except Exception as exc:
        return _result(_error_payload("rescue_dispatch_failed", str(exc)))


@tool(
    name="vp_dispatch_mission_redispatch_fresh",
    description=(
        "Re-dispatch a failed VP mission in a FRESH workspace (no inheritance "
        "of partial outputs), copying only the original BRIEF and adding "
        "Simone's context. Use when the failure was a subprocess crash, env "
        "corruption, or workspace contamination — situations where the prior "
        "state may itself be the problem. Same VP, extends the rescue chain. "
        "Closes the corresponding vp_failure:<mission_id> task hub item."
    ),
    input_schema={
        "mission_id": str,
        "additional_context": str,
    },
)
async def vp_dispatch_mission_redispatch_fresh_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    return await _vp_dispatch_mission_redispatch_fresh_impl(args)


async def _vp_dispatch_mission_redispatch_fresh_impl(args: dict[str, Any]) -> dict[str, Any]:
    mission_id = str(args.get("mission_id") or "").strip()
    additional_context = str(args.get("additional_context") or "").strip()
    if not mission_id:
        return _result(_error_payload("validation_error", "mission_id is required"))

    failed = _load_failed_mission(mission_id)
    if failed is None:
        return _result(_error_payload("not_found", f"Mission not found: {mission_id}"))

    chain_id = _next_chain_id(failed)
    fresh_objective = str(failed.get("objective") or "").rstrip()
    if additional_context:
        fresh_objective = (
            f"{fresh_objective}\n\n"
            f"--- SIMONE FRESH-RESTART CONTEXT ---\n"
            f"The prior attempt failed (likely environmental). Starting fresh.\n\n"
            f"{additional_context}\n"
            f"--- END CONTEXT ---\n"
        )

    payload_meta: dict[str, Any] = {}
    try:
        payload = json.loads(failed.get("payload_json") or "{}")
        payload_meta = payload.get("metadata") or {}
    except Exception:
        pass

    dispatch_args: dict[str, Any] = {
        "vp_id": str(failed.get("vp_id") or ""),
        "objective": fresh_objective,
        "mission_type": str(failed.get("mission_type") or "task"),
        "idempotency_key": f"rescue-fresh-{chain_id}-{uuid.uuid4().hex[:8]}",
        "metadata": {
            **payload_meta,
            "rescue_chain_id": chain_id,
            "rescue_prior_mission_id": mission_id,
            "rescue_action": "redispatch_fresh",
        },
    }

    try:
        result = await _vp_dispatch_mission_impl(dispatch_args)
        try:
            with connect_runtime_db(get_activity_db_path()) as activity_conn:
                _close_failure_task(
                    activity_conn, mission_id,
                    note=f"redispatched fresh (chain={chain_id})",
                    action="redispatch_fresh",
                )
        except Exception:
            pass
        return result
    except Exception as exc:
        return _result(_error_payload("rescue_dispatch_failed", str(exc)))


@tool(
    name="escalate_vp_failure_to_operator",
    description=(
        "Escalate a VP mission failure to Kevin via a chat_panel task hub item. "
        "Use when the failure is auth/workspace-guard/config (Simone can't fix), "
        "when failure_count is high (rescue attempts already failed), or when "
        "you choose not to retry. Closes the corresponding vp_failure:<mission_id>."
    ),
    input_schema={
        "mission_id": str,
        "summary": str,
        "why_escalating": str,
        "recommended_action": str,
    },
)
async def escalate_vp_failure_to_operator_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    return await _escalate_vp_failure_to_operator_impl(args)


async def _escalate_vp_failure_to_operator_impl(args: dict[str, Any]) -> dict[str, Any]:
    mission_id = str(args.get("mission_id") or "").strip()
    summary = str(args.get("summary") or "").strip()
    why_escalating = str(args.get("why_escalating") or "").strip()
    recommended_action = str(args.get("recommended_action") or "").strip()

    if not mission_id:
        return _result(_error_payload("validation_error", "mission_id is required"))
    if not summary:
        return _result(_error_payload("validation_error", "summary is required"))
    if not why_escalating:
        return _result(_error_payload(
            "validation_error",
            "why_escalating is required — Simone must explain why she's escalating instead of retrying",
        ))

    failed = _load_failed_mission(mission_id)
    vp_id = str((failed or {}).get("vp_id") or "")
    original_objective = str((failed or {}).get("objective") or "")[:600]

    body_parts = [
        f"## VP Failure Escalation — {vp_id or 'unknown VP'}",
        "",
        f"**Mission:** `{mission_id}`",
        f"**Summary:** {summary}",
        f"**Why Simone escalated:** {why_escalating}",
    ]
    if recommended_action:
        body_parts.extend(["", f"**Simone's recommended action:** {recommended_action}"])
    body_parts.extend([
        "",
        "**Original objective (preview):**",
        f"> {original_objective[:400]}",
    ])
    body = "\n".join(body_parts)

    escalation_task_id = f"chat_panel:vp_escalation:{mission_id}"
    try:
        with connect_runtime_db(get_activity_db_path()) as activity_conn:
            from universal_agent import task_hub
            task_hub.ensure_schema(activity_conn)
            task_hub.upsert_item(
                activity_conn,
                {
                    "task_id": escalation_task_id,
                    "source_kind": "chat_panel",
                    "status": task_hub.TASK_STATUS_OPEN,
                    "agent_ready": True,
                    "trigger_type": "immediate",
                    "title": f"[VP Escalation] {vp_id}: {summary[:80]}",
                    "metadata": {
                        "intake_channel": "chat_panel",
                        "escalation_source": "vp_failure",
                        "vp_id": vp_id,
                        "mission_id": mission_id,
                        "summary": summary,
                        "why_escalating": why_escalating,
                        "recommended_action": recommended_action or None,
                        "body": body,
                    },
                },
            )
            _close_failure_task(
                activity_conn, mission_id,
                note=f"escalated to operator: {summary[:80]}",
                action="escalate",
            )
        return _result({
            "ok": True,
            "escalation_task_id": escalation_task_id,
            "mission_id": mission_id,
        })
    except Exception as exc:
        return _result(_error_payload("escalation_failed", str(exc)))

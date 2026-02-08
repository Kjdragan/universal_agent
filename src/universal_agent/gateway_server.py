"""
Universal Agent Gateway Server — External HTTP/WebSocket API.

Exposes the InProcessGateway as a standalone service for external clients.
Server runs on port 8002 by default (configurable via UA_GATEWAY_PORT env var).

Usage:
    python -m universal_agent.gateway_server
"""

import asyncio
import hashlib
import json
import logging
import os
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from dotenv import load_dotenv
import httpx

# Load .env early so SDK/CLI subprocesses inherit API keys and settings.
BASE_DIR = Path(__file__).parent.parent.parent
load_dotenv(BASE_DIR / ".env", override=False)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from universal_agent.gateway import (
    InProcessGateway,
    GatewaySession,
    GatewayRequest,
    GatewaySessionSummary,
)
from universal_agent.agent_core import AgentEvent, EventType
from universal_agent.feature_flags import heartbeat_enabled, memory_index_enabled, cron_enabled
from universal_agent.identity import resolve_user_id
from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
from universal_agent.durable.migrations import ensure_schema
from universal_agent.heartbeat_service import HeartbeatService
from universal_agent.cron_service import CronService
from universal_agent.ops_service import OpsService
from universal_agent.ops_config import (
    apply_merge_patch,
    load_ops_config,
    ops_config_hash,
    ops_config_schema,
    write_ops_config,
)
from universal_agent.approvals import list_approvals, update_approval, upsert_approval
from universal_agent.security_paths import (
    allow_external_workspaces_from_env,
    resolve_ops_log_path,
    resolve_workspace_dir,
    validate_session_id,
)
from universal_agent.session_policy import (
    evaluate_request_against_policy,
    load_session_policy,
    normalize_memory_policy,
    save_session_policy,
    update_session_policy,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Feature flags (placeholders, no runtime behavior changes yet)
HEARTBEAT_ENABLED = heartbeat_enabled()
CRON_ENABLED = cron_enabled()
MEMORY_INDEX_ENABLED = memory_index_enabled()

# 1. Configurable Workspaces Directory
# Default to AGENT_RUN_WORKSPACES in project root, but allow override via env var
_default_ws_dir = BASE_DIR / "AGENT_RUN_WORKSPACES"
env_ws_dir = os.getenv("UA_WORKSPACES_DIR")
if env_ws_dir:
    WORKSPACES_DIR = Path(env_ws_dir).resolve()
    logger.info(f"📁 Workspaces Directory Overridden: {WORKSPACES_DIR}")
else:
    WORKSPACES_DIR = _default_ws_dir

_DEPLOYMENT_PROFILE = (os.getenv("UA_DEPLOYMENT_PROFILE") or "local_workstation").strip().lower()
if _DEPLOYMENT_PROFILE not in {"local_workstation", "standalone_node", "vps"}:
    _DEPLOYMENT_PROFILE = "local_workstation"


def _deployment_profile_defaults() -> dict:
    if _DEPLOYMENT_PROFILE == "standalone_node":
        return {
            "profile": _DEPLOYMENT_PROFILE,
            "allowlist_required": False,
            "ops_token_required": True,
            "remote_access": "vpn_recommended",
            "notes": "Single-owner appliance posture with explicit ops token.",
        }
    if _DEPLOYMENT_PROFILE == "vps":
        return {
            "profile": _DEPLOYMENT_PROFILE,
            "allowlist_required": True,
            "ops_token_required": True,
            "remote_access": "vpn_or_strict_firewall",
            "notes": "Internet-exposed posture requires strict auth and network controls.",
        }
    return {
        "profile": _DEPLOYMENT_PROFILE,
        "allowlist_required": False,
        "ops_token_required": False,
        "remote_access": "local_only_default",
        "notes": "Development workstation defaults prioritize local iteration speed.",
    }

# 2. Allowlist Configuration
ALLOWED_USERS = set()
_allowed_users_str = os.getenv("UA_ALLOWED_USERS", "").strip()
if _allowed_users_str:
    ALLOWED_USERS = {u.strip() for u in _allowed_users_str.split(",") if u.strip()}
    logger.info(f"🔒 Authenticated Access Only. Allowed Users: {len(ALLOWED_USERS)}")
else:
    logger.info("🔓 Public Access Mode (No Allowlist configured)")

# Ops access token (optional hard gate for /api/v1/ops/* endpoints)
OPS_TOKEN = os.getenv("UA_OPS_TOKEN", "").strip()


# =============================================================================
# Pydantic Models
# =============================================================================


class CreateSessionRequest(BaseModel):
    user_id: Optional[str] = None
    workspace_dir: Optional[str] = None


class CreateSessionResponse(BaseModel):
    session_id: str
    user_id: str
    workspace_dir: str
    metadata: dict = {}


class SessionSummaryResponse(BaseModel):
    session_id: str
    workspace_dir: str
    status: str
    metadata: dict = {}


class ExecuteRequest(BaseModel):
    user_input: str
    force_complex: bool = False
    metadata: dict = {}


class GatewayEventWire(BaseModel):
    type: str
    data: dict
    timestamp: str


class HeartbeatWakeRequest(BaseModel):
    session_id: Optional[str] = None
    reason: Optional[str] = None
    mode: Optional[str] = None  # now | next


class CronJobCreateRequest(BaseModel):
    user_id: Optional[str] = None
    workspace_dir: Optional[str] = None
    command: str
    every: Optional[str] = None  # Simple interval (e.g., "30m", "1h")
    cron_expr: Optional[str] = None  # 5-field cron expression (e.g., "0 7 * * 1")
    timezone: str = "UTC"  # Timezone for cron expression
    run_at: Optional[str] = None  # One-shot: relative ("20m") or absolute ISO timestamp
    delete_after_run: bool = False  # One-shot: delete after successful run
    model: Optional[str] = None  # Model override for this job
    enabled: bool = True
    metadata: dict = {}


class CronJobUpdateRequest(BaseModel):
    command: Optional[str] = None
    every: Optional[str] = None
    cron_expr: Optional[str] = None
    timezone: Optional[str] = None
    run_at: Optional[str] = None
    delete_after_run: Optional[bool] = None
    model: Optional[str] = None
    enabled: Optional[bool] = None
    workspace_dir: Optional[str] = None
    user_id: Optional[str] = None
    metadata: Optional[dict] = None


class SystemEventRequest(BaseModel):
    session_id: Optional[str] = None
    event_type: Optional[str] = None
    payload: Optional[dict] = None
    wake_heartbeat: Optional[str] = None  # now | next | truthy
    wake_mode: Optional[str] = None


class SystemPresenceRequest(BaseModel):
    node_id: Optional[str] = None
    status: Optional[str] = None
    reason: Optional[str] = None
    metadata: Optional[dict] = None


class OpsConfigRequest(BaseModel):
    config: dict = {}
    base_hash: Optional[str] = None


class OpsConfigPatchRequest(BaseModel):
    patch: dict = {}
    base_hash: Optional[str] = None


class OpsSkillUpdateRequest(BaseModel):
    enabled: Optional[bool] = None


class OpsApprovalCreateRequest(BaseModel):
    approval_id: Optional[str] = None
    phase_id: Optional[str] = None
    status: Optional[str] = None
    summary: Optional[str] = None
    requested_by: Optional[str] = None
    metadata: dict = {}


class OpsApprovalUpdateRequest(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    metadata: Optional[dict] = None


class OpsSessionResetRequest(BaseModel):
    clear_logs: bool = True
    clear_memory: bool = True
    clear_work_products: bool = False


class OpsSessionCompactRequest(BaseModel):
    max_lines: int = 400
    max_bytes: int = 200_000


class OpsSessionArchiveRequest(BaseModel):
    clear_memory: bool = False
    clear_work_products: bool = False


class OpsSessionCancelRequest(BaseModel):
    reason: Optional[str] = None


class NotificationUpdateRequest(BaseModel):
    status: str
    note: Optional[str] = None
    snooze_minutes: Optional[int] = None


class NotificationBulkUpdateRequest(BaseModel):
    status: str
    note: Optional[str] = None
    kind: Optional[str] = None
    current_status: Optional[str] = None
    snooze_minutes: Optional[int] = None
    limit: int = 200


class SessionPolicyPatchRequest(BaseModel):
    patch: dict = {}


class ResumeRequest(BaseModel):
    approval_id: Optional[str] = None
    reason: Optional[str] = None


# =============================================================================
# Gateway Singleton
# =============================================================================

_gateway: Optional[InProcessGateway] = None
_sessions: dict[str, GatewaySession] = {}
_session_runtime: dict[str, dict[str, Any]] = {}
_heartbeat_service: Optional[HeartbeatService] = None
_cron_service: Optional[CronService] = None
_ops_service: Optional[OpsService] = None
_system_events: dict[str, list[dict]] = {}
_system_presence: dict[str, dict] = {}
_system_events_max = int(os.getenv("UA_SYSTEM_EVENTS_MAX", "100"))
_channel_probe_results: dict[str, dict] = {}
_notifications: list[dict] = []
_notifications_max = int(os.getenv("UA_NOTIFICATIONS_MAX", "500"))
_continuity_active_alerts: set[str] = set()
_pending_gated_requests: dict[str, dict] = {}
_session_turn_state: dict[str, dict[str, Any]] = {}
_session_turn_locks: dict[str, asyncio.Lock] = {}
_observability_metrics: dict[str, Any] = {
    "started_at": datetime.now().isoformat(),
    "sessions_created": 0,
    "ws_attach_attempts": 0,
    "ws_attach_successes": 0,
    "ws_attach_failures": 0,
    "resume_attempts": 0,
    "resume_successes": 0,
    "resume_failures": 0,
    "turn_busy_rejected": 0,
    "turn_duplicate_in_progress": 0,
    "turn_duplicate_completed": 0,
}

SESSION_STATE_IDLE = "idle"
SESSION_STATE_RUNNING = "running"
SESSION_STATE_TERMINAL = "terminal"
TURN_STATUS_RUNNING = "running"
TURN_STATUS_COMPLETED = "completed"
TURN_STATUS_FAILED = "failed"
TURN_HISTORY_LIMIT = int(os.getenv("UA_TURN_HISTORY_LIMIT", "200"))
TURN_FINGERPRINT_DEDUPE_WINDOW_SECONDS = int(os.getenv("UA_TURN_FINGERPRINT_DEDUPE_WINDOW_SECONDS", "120"))
CONTINUITY_RESUME_SUCCESS_MIN = float(os.getenv("UA_CONTINUITY_RESUME_SUCCESS_MIN", "0.90") or 0.90)
CONTINUITY_ATTACH_SUCCESS_MIN = float(os.getenv("UA_CONTINUITY_ATTACH_SUCCESS_MIN", "0.90") or 0.90)
CONTINUITY_FAILURE_WARN_THRESHOLD = int(os.getenv("UA_CONTINUITY_FAILURE_WARN_THRESHOLD", "3") or 3)
NOTIFICATION_SNOOZE_MINUTES_DEFAULT = int(os.getenv("UA_NOTIFICATION_SNOOZE_MINUTES_DEFAULT", "30") or 30)
NOTIFICATION_SNOOZE_MINUTES_MAX = int(os.getenv("UA_NOTIFICATION_SNOOZE_MINUTES_MAX", "1440") or 1440)


def _now_iso() -> str:
    return datetime.now().isoformat()


def _increment_metric(name: str, amount: int = 1) -> None:
    current = int(_observability_metrics.get(name, 0) or 0)
    _observability_metrics[name] = current + max(0, int(amount))
    _sync_continuity_notifications()


def _continuity_alerts_snapshot() -> dict[str, Any]:
    resume_attempts = int(_observability_metrics.get("resume_attempts", 0) or 0)
    resume_successes = int(_observability_metrics.get("resume_successes", 0) or 0)
    ws_attach_attempts = int(_observability_metrics.get("ws_attach_attempts", 0) or 0)
    ws_attach_successes = int(_observability_metrics.get("ws_attach_successes", 0) or 0)
    resume_rate = round(resume_successes / resume_attempts, 4) if resume_attempts > 0 else None
    attach_rate = round(ws_attach_successes / ws_attach_attempts, 4) if ws_attach_attempts > 0 else None
    resume_failures = int(_observability_metrics.get("resume_failures", 0) or 0)
    attach_failures = int(_observability_metrics.get("ws_attach_failures", 0) or 0)
    alerts: list[dict[str, Any]] = []
    if resume_rate is not None and resume_rate < CONTINUITY_RESUME_SUCCESS_MIN:
        alerts.append(
            {
                "code": "resume_success_rate_low",
                "severity": "warning",
                "message": "Resume success rate below threshold.",
                "actual": resume_rate,
                "threshold": CONTINUITY_RESUME_SUCCESS_MIN,
            }
        )
    if attach_rate is not None and attach_rate < CONTINUITY_ATTACH_SUCCESS_MIN:
        alerts.append(
            {
                "code": "attach_success_rate_low",
                "severity": "warning",
                "message": "Attach success rate below threshold.",
                "actual": attach_rate,
                "threshold": CONTINUITY_ATTACH_SUCCESS_MIN,
            }
        )
    if resume_failures >= CONTINUITY_FAILURE_WARN_THRESHOLD:
        alerts.append(
            {
                "code": "resume_failures_high",
                "severity": "warning",
                "message": "Resume failures exceeded warning threshold.",
                "actual": resume_failures,
                "threshold": CONTINUITY_FAILURE_WARN_THRESHOLD,
            }
        )
    if attach_failures >= CONTINUITY_FAILURE_WARN_THRESHOLD:
        alerts.append(
            {
                "code": "attach_failures_high",
                "severity": "warning",
                "message": "Attach failures exceeded warning threshold.",
                "actual": attach_failures,
                "threshold": CONTINUITY_FAILURE_WARN_THRESHOLD,
            }
        )
    return {
        "resume_success_rate": resume_rate,
        "attach_success_rate": attach_rate,
        "continuity_status": "degraded" if alerts else "ok",
        "alerts": alerts,
    }


def _sync_continuity_notifications() -> None:
    global _continuity_active_alerts
    # This function can be invoked early during app initialization in tests;
    # guard in case notification utilities are not yet fully available.
    if "_add_notification" not in globals():
        return
    snapshot = _continuity_alerts_snapshot()
    alerts = snapshot.get("alerts") or []
    if not isinstance(alerts, list):
        return
    by_code = {
        str(alert.get("code")): alert
        for alert in alerts
        if isinstance(alert, dict) and alert.get("code")
    }
    current_codes = set(by_code.keys())
    newly_active = current_codes - _continuity_active_alerts
    recovered = _continuity_active_alerts - current_codes

    for code in sorted(newly_active):
        alert = by_code.get(code, {})
        message = str(alert.get("message") or code)
        actual = alert.get("actual")
        threshold = alert.get("threshold")
        details = f" actual={actual}, threshold={threshold}" if actual is not None and threshold is not None else ""
        _add_notification(
            kind="continuity_alert",
            title="Session Continuity Alert",
            message=f"{message}.{details}",
            severity="warning",
            requires_action=False,
            metadata={"code": code, "alert": alert, "source": "session_continuity_metrics"},
        )

    for code in sorted(recovered):
        _add_notification(
            kind="continuity_recovered",
            title="Session Continuity Recovered",
            message=f"Continuity alert resolved: {code}.",
            severity="info",
            requires_action=False,
            metadata={"code": code, "source": "session_continuity_metrics"},
        )

    _continuity_active_alerts = current_codes


def _observability_metrics_snapshot() -> dict[str, Any]:
    duplicate_prevented = (
        int(_observability_metrics.get("turn_busy_rejected", 0) or 0)
        + int(_observability_metrics.get("turn_duplicate_in_progress", 0) or 0)
        + int(_observability_metrics.get("turn_duplicate_completed", 0) or 0)
    )
    continuity = _continuity_alerts_snapshot()
    return {
        **_observability_metrics,
        "duplicate_turn_prevention_count": duplicate_prevented,
        "resume_success_rate": continuity.get("resume_success_rate"),
        "attach_success_rate": continuity.get("attach_success_rate"),
        "continuity_status": continuity.get("continuity_status"),
        "alerts": continuity.get("alerts"),
    }


def _session_runtime_snapshot(session_id: str) -> dict[str, Any]:
    state = _session_runtime.get(session_id)
    if not state:
        state = {
            "session_id": session_id,
            "lifecycle_state": SESSION_STATE_IDLE,
            "last_event_seq": 0,
            "last_activity_at": _now_iso(),
            "active_connections": 0,
            "active_runs": 0,
            "last_event_type": None,
            "terminal_reason": None,
        }
        _session_runtime[session_id] = state
    return state


def _runtime_status_from_counters(state: dict[str, Any]) -> str:
    if str(state.get("lifecycle_state")) == SESSION_STATE_TERMINAL:
        return SESSION_STATE_TERMINAL
    return SESSION_STATE_RUNNING if int(state.get("active_runs", 0)) > 0 else SESSION_STATE_IDLE


def _sync_runtime_metadata(session_id: str) -> None:
    session = _sessions.get(session_id)
    if not session:
        return
    runtime = _session_runtime_snapshot(session_id)
    session.metadata["runtime"] = {
        "lifecycle_state": runtime.get("lifecycle_state", SESSION_STATE_IDLE),
        "last_event_seq": int(runtime.get("last_event_seq", 0)),
        "last_activity_at": runtime.get("last_activity_at"),
        "active_connections": int(runtime.get("active_connections", 0)),
        "active_runs": int(runtime.get("active_runs", 0)),
        "last_event_type": runtime.get("last_event_type"),
        "terminal_reason": runtime.get("terminal_reason"),
    }


def _record_session_event(session_id: str, event_type: Optional[str] = None) -> None:
    runtime = _session_runtime_snapshot(session_id)
    runtime["last_event_seq"] = int(runtime.get("last_event_seq", 0)) + 1
    runtime["last_activity_at"] = _now_iso()
    if event_type:
        runtime["last_event_type"] = event_type
    _sync_runtime_metadata(session_id)


def _session_turn_lock(session_id: str) -> asyncio.Lock:
    lock = _session_turn_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_turn_locks[session_id] = lock
    return lock


def _session_turn_snapshot(session_id: str) -> dict[str, Any]:
    snapshot = _session_turn_state.get(session_id)
    if not snapshot:
        snapshot = {
            "session_id": session_id,
            "active_turn_id": None,
            "turns": {},
            "last_turn_id": None,
        }
        _session_turn_state[session_id] = snapshot
    return snapshot


def _normalize_client_turn_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > 128:
        text = text[:128]
    return text


def _compute_turn_fingerprint(user_input: str, force_complex: bool, metadata: dict[str, Any]) -> str:
    # Keep fallback fingerprint coarse and stable across retries so it can block
    # accidental duplicate side effects from clients that do not send client_turn_id.
    payload = {
        "user_input": user_input,
        "force_complex": bool(force_complex),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_iso_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _trim_turn_history(state: dict[str, Any]) -> None:
    turns = state.get("turns", {})
    if not isinstance(turns, dict):
        return
    overflow = len(turns) - TURN_HISTORY_LIMIT
    if overflow <= 0:
        return
    active_turn_id = state.get("active_turn_id")
    for turn_id in list(turns.keys()):
        if overflow <= 0:
            break
        if turn_id == active_turn_id:
            continue
        turns.pop(turn_id, None)
        overflow -= 1


def _admit_turn(
    session_id: str,
    connection_id: str,
    user_input: str,
    force_complex: bool,
    metadata: dict[str, Any],
    client_turn_id: Optional[str],
) -> dict[str, Any]:
    state = _session_turn_snapshot(session_id)
    turns = state["turns"]
    assert isinstance(turns, dict)
    fingerprint = _compute_turn_fingerprint(user_input, force_complex, metadata)

    active_turn_id = state.get("active_turn_id")
    if active_turn_id:
        active_record = turns.get(active_turn_id)
        if isinstance(active_record, dict) and active_record.get("status") == TURN_STATUS_RUNNING:
            # Explicit idempotency key repeated while running.
            if client_turn_id and client_turn_id == active_turn_id:
                return {"decision": "duplicate_in_progress", "turn_id": active_turn_id, "record": active_record}
            # Fallback for clients without explicit turn IDs.
            if not client_turn_id and active_record.get("fingerprint") == fingerprint:
                return {"decision": "duplicate_in_progress", "turn_id": active_turn_id, "record": active_record}
            return {"decision": "busy", "turn_id": active_turn_id, "record": active_record}
        state["active_turn_id"] = None

    if not client_turn_id:
        now = datetime.now().timestamp()
        for prior_turn_id in reversed(list(turns.keys())):
            prior_record = turns.get(prior_turn_id)
            if not isinstance(prior_record, dict):
                continue
            if prior_record.get("fingerprint") != fingerprint:
                continue
            prior_status = str(prior_record.get("status"))
            if prior_status == TURN_STATUS_RUNNING:
                return {"decision": "duplicate_in_progress", "turn_id": str(prior_turn_id), "record": prior_record}
            if prior_status == TURN_STATUS_COMPLETED:
                finished = _parse_iso_timestamp(prior_record.get("finished_at") or prior_record.get("started_at"))
                if finished is None:
                    return {"decision": "duplicate_completed", "turn_id": str(prior_turn_id), "record": prior_record}
                if (now - finished.timestamp()) <= TURN_FINGERPRINT_DEDUPE_WINDOW_SECONDS:
                    return {"decision": "duplicate_completed", "turn_id": str(prior_turn_id), "record": prior_record}

    if client_turn_id and client_turn_id in turns:
        record = turns[client_turn_id]
        if isinstance(record, dict):
            status = str(record.get("status", TURN_STATUS_COMPLETED))
            if status == TURN_STATUS_RUNNING:
                return {"decision": "duplicate_in_progress", "turn_id": client_turn_id, "record": record}
            return {"decision": "duplicate_completed", "turn_id": client_turn_id, "record": record}

    turn_id = client_turn_id or f"turn_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    record = {
        "turn_id": turn_id,
        "client_turn_id": client_turn_id,
        "status": TURN_STATUS_RUNNING,
        "started_at": _now_iso(),
        "origin_connection_id": connection_id,
        "fingerprint": fingerprint,
    }
    turns[turn_id] = record
    state["active_turn_id"] = turn_id
    state["last_turn_id"] = turn_id
    _trim_turn_history(state)
    return {"decision": "accepted", "turn_id": turn_id, "record": record}


def _finalize_turn(session_id: str, turn_id: str, status: str, error_message: Optional[str] = None) -> None:
    state = _session_turn_snapshot(session_id)
    turns = state.get("turns", {})
    if not isinstance(turns, dict):
        return
    record = turns.get(turn_id)
    if not isinstance(record, dict):
        return
    record["status"] = status
    record["finished_at"] = _now_iso()
    if error_message:
        record["error_message"] = error_message
    if state.get("active_turn_id") == turn_id:
        state["active_turn_id"] = None


def _set_session_connections(session_id: str, count: int) -> None:
    runtime = _session_runtime_snapshot(session_id)
    runtime["active_connections"] = max(0, int(count))
    runtime["last_activity_at"] = _now_iso()
    runtime["lifecycle_state"] = _runtime_status_from_counters(runtime)
    _sync_runtime_metadata(session_id)


def _increment_session_active_runs(session_id: str) -> None:
    runtime = _session_runtime_snapshot(session_id)
    runtime["active_runs"] = int(runtime.get("active_runs", 0)) + 1
    runtime["lifecycle_state"] = SESSION_STATE_RUNNING
    runtime["terminal_reason"] = None
    runtime["last_activity_at"] = _now_iso()
    _sync_runtime_metadata(session_id)


def _decrement_session_active_runs(session_id: str) -> None:
    runtime = _session_runtime_snapshot(session_id)
    runtime["active_runs"] = max(0, int(runtime.get("active_runs", 0)) - 1)
    runtime["lifecycle_state"] = _runtime_status_from_counters(runtime)
    runtime["last_activity_at"] = _now_iso()
    _sync_runtime_metadata(session_id)


def _mark_session_terminal(session_id: str, reason: str) -> None:
    runtime = _session_runtime_snapshot(session_id)
    runtime["active_runs"] = 0
    runtime["active_connections"] = 0
    runtime["lifecycle_state"] = SESSION_STATE_TERMINAL
    runtime["terminal_reason"] = reason
    runtime["last_activity_at"] = _now_iso()
    _sync_runtime_metadata(session_id)


def _emit_cron_event(payload: dict) -> None:
    event = {
        "type": payload.get("type", "cron_event"),
        "data": payload,
        "timestamp": datetime.now().isoformat(),
    }
    for session_id in list(manager.session_connections.keys()):
        asyncio.create_task(manager.broadcast(session_id, event))


def _cron_wake_callback(session_id: str, mode: str, reason: str) -> None:
    if not _heartbeat_service:
        return
    if mode == "next":
        _heartbeat_service.request_heartbeat_next(session_id, reason=reason)
    else:
        _heartbeat_service.request_heartbeat_now(session_id, reason=reason)


def _enqueue_system_event(session_id: str, event: dict) -> None:
    queue = _system_events.setdefault(session_id, [])
    queue.append(event)
    if len(queue) > _system_events_max:
        _system_events[session_id] = queue[-_system_events_max:]


def _drain_system_events(session_id: str) -> list[dict]:
    events = _system_events.get(session_id, [])
    _system_events[session_id] = []
    return events


def _broadcast_system_event(session_id: str, event: dict) -> None:
    payload = {
        "type": "system_event",
        "data": event,
        "timestamp": datetime.now().isoformat(),
    }
    asyncio.create_task(manager.broadcast(session_id, payload))


def _notification_targets() -> dict:
    config = load_ops_config()
    notifications = config.get("notifications", {})
    if not isinstance(notifications, dict):
        notifications = {}

    channels = notifications.get("channels")
    if not isinstance(channels, list) or not channels:
        channels = ["dashboard", "email", "telegram"]
    normalized_channels = [str(ch).strip().lower() for ch in channels if str(ch).strip()]

    email_targets = notifications.get("email_targets")
    if not isinstance(email_targets, list) or not email_targets:
        fallback_email = (
            os.getenv("UA_NOTIFICATION_EMAIL")
            or os.getenv("UA_PRIMARY_EMAIL")
            or "kevinjdragan@gmail.com"
        )
        email_targets = [fallback_email]

    return {
        "channels": normalized_channels,
        "email_targets": [str(email).strip() for email in email_targets if str(email).strip()],
    }


def _add_notification(
    *,
    kind: str,
    title: str,
    message: str,
    session_id: Optional[str] = None,
    severity: str = "info",
    requires_action: bool = False,
    metadata: Optional[dict] = None,
) -> dict:
    notification_id = f"ntf_{int(time.time() * 1000)}_{len(_notifications) + 1}"
    targets = _notification_targets()
    timestamp = datetime.now().isoformat()
    record = {
        "id": notification_id,
        "kind": kind,
        "title": title,
        "message": message,
        "session_id": session_id,
        "severity": severity,
        "requires_action": requires_action,
        "status": "new",
        "created_at": timestamp,
        "updated_at": timestamp,
        "channels": targets["channels"],
        "email_targets": targets["email_targets"],
        "metadata": metadata or {},
    }
    _notifications.append(record)
    if len(_notifications) > _notifications_max:
        del _notifications[: len(_notifications) - _notifications_max]

    if session_id:
        event = {
            "event_id": f"evt_ntf_{notification_id}",
            "type": "notification",
            "payload": record,
            "created_at": timestamp,
        }
        _enqueue_system_event(session_id, event)
        if session_id in manager.session_connections:
            _broadcast_system_event(session_id, event)
    return record


def _normalize_notification_status(status: str) -> str:
    return str(status or "").strip().lower()


def _resolve_snooze_minutes(value: Optional[int]) -> int:
    if value is None:
        return max(1, min(NOTIFICATION_SNOOZE_MINUTES_DEFAULT, NOTIFICATION_SNOOZE_MINUTES_MAX))
    try:
        parsed = int(value)
    except Exception:
        parsed = NOTIFICATION_SNOOZE_MINUTES_DEFAULT
    return max(1, min(parsed, NOTIFICATION_SNOOZE_MINUTES_MAX))


def _parse_snooze_until(metadata: dict[str, Any]) -> Optional[float]:
    until_ts = metadata.get("snooze_until_ts")
    if isinstance(until_ts, (int, float)):
        return float(until_ts)
    until_raw = metadata.get("snooze_until")
    if isinstance(until_raw, str) and until_raw.strip():
        parsed = _parse_iso_timestamp(until_raw.strip())
        if parsed:
            return parsed.timestamp()
    return None


def _apply_notification_status(
    item: dict[str, Any],
    *,
    status_value: str,
    note: Optional[str] = None,
    snooze_minutes: Optional[int] = None,
) -> dict[str, Any]:
    normalized = _normalize_notification_status(status_value)
    item["status"] = normalized
    item["updated_at"] = datetime.now().isoformat()
    metadata = item.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        item["metadata"] = metadata
    if note:
        metadata["note"] = note
    if normalized == "snoozed":
        minutes = _resolve_snooze_minutes(snooze_minutes)
        until_ts = time.time() + (minutes * 60)
        metadata["snooze_minutes"] = minutes
        metadata["snooze_until_ts"] = until_ts
        metadata["snooze_until"] = datetime.fromtimestamp(until_ts).isoformat()
    else:
        metadata.pop("snooze_until_ts", None)
        metadata.pop("snooze_until", None)
        metadata.pop("snooze_minutes", None)
    return item


def _apply_notification_snooze_expiry() -> int:
    now_ts = time.time()
    changed = 0
    for item in _notifications:
        if _normalize_notification_status(item.get("status")) != "snoozed":
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        until_ts = _parse_snooze_until(metadata)
        if until_ts is None or until_ts > now_ts:
            continue
        item["status"] = "new"
        item["updated_at"] = datetime.now().isoformat()
        metadata["snooze_expired_at"] = item["updated_at"]
        metadata.pop("snooze_until_ts", None)
        metadata.pop("snooze_until", None)
        metadata.pop("snooze_minutes", None)
        changed += 1
    return changed


def _approval_status(approval_id: str) -> Optional[str]:
    for record in list_approvals():
        if record.get("approval_id") == approval_id:
            status = record.get("status")
            return str(status).lower() if status else None
    return None


def _pending_gate_is_approved(session_id: str) -> bool:
    pending = _pending_gated_requests.get(session_id)
    if not pending:
        return False
    approval_id = pending.get("approval_id")
    if not approval_id:
        return str(pending.get("status", "")).lower() == "approved"
    status = _approval_status(str(approval_id))
    return status == "approved"


def _broadcast_presence(payload: dict) -> None:
    event = {
        "type": "system_presence",
        "data": payload,
        "timestamp": datetime.now().isoformat(),
    }
    for session_id in list(manager.session_connections.keys()):
        asyncio.create_task(manager.broadcast(session_id, event))


def _read_run_log_tail(workspace_dir: str, max_bytes: int = 4096) -> Optional[str]:
    log_path = Path(workspace_dir) / "run.log"
    if not log_path.exists():
        return None
    try:
        with log_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(size - max_bytes, 0))
            return handle.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning("Failed to read run.log tail: %s", e)
        return None


def _read_heartbeat_state(workspace_dir: str) -> Optional[dict]:
    state_path = Path(workspace_dir) / "heartbeat_state.json"
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text())
    except Exception as exc:
        logger.warning("Failed to read heartbeat_state.json: %s", exc)
        return None


def _load_skill_catalog() -> list[dict]:
    skills_dir = os.getenv("UA_SKILLS_DIR") or str(BASE_DIR / ".claude" / "skills")
    overrides = load_ops_config().get("skills", {}).get("entries", {})
    normalized_overrides = {}
    if isinstance(overrides, dict):
        for key, payload in overrides.items():
            enabled = None
            if isinstance(payload, dict):
                enabled = payload.get("enabled")
            elif isinstance(payload, bool):
                enabled = payload
            if isinstance(enabled, bool):
                normalized_overrides[str(key).strip().lower()] = enabled

    entries: list[dict] = []
    try:
        import yaml
        from universal_agent.prompt_assets import _check_skill_requirements
    except Exception:
        yaml = None
        _check_skill_requirements = None  # type: ignore

    if not os.path.isdir(skills_dir) or yaml is None or _check_skill_requirements is None:
        return entries

    for skill_name in os.listdir(skills_dir):
        skill_path = os.path.join(skills_dir, skill_name)
        skill_md = os.path.join(skill_path, "SKILL.md")
        if not os.path.isdir(skill_path) or not os.path.exists(skill_md):
            continue
        try:
            content = Path(skill_md).read_text(encoding="utf-8")
            if not content.startswith("---"):
                continue
            parts = content.split("---", 2)
            if len(parts) < 3:
                continue
            frontmatter = yaml.safe_load(parts[1]) or {}
            name = frontmatter.get("name", skill_name)
            description = frontmatter.get("description", "No description")
            key = str(name).strip().lower()
            enabled_override = normalized_overrides.get(key)
            enabled = True if enabled_override is None else enabled_override
            available, reason = _check_skill_requirements(frontmatter)
            entries.append(
                {
                    "name": name,
                    "description": description,
                    "path": skill_md,
                    "enabled": enabled,
                    "available": available,
                    "disabled_reason": None if enabled else "disabled_by_ops_config",
                    "unavailable_reason": None if available else reason,
                }
            )
        except Exception:
            continue
    return entries


def _load_channel_status() -> list[dict]:
    overrides = load_ops_config().get("channels", {}).get("entries", {})
    normalized = {}
    if isinstance(overrides, dict):
        for key, payload in overrides.items():
            enabled = None
            if isinstance(payload, dict):
                enabled = payload.get("enabled")
            elif isinstance(payload, bool):
                enabled = payload
            if isinstance(enabled, bool):
                normalized[str(key).strip().lower()] = enabled

    channels = [
        {
            "id": "cli",
            "label": "CLI",
            "configured": True,
            "note": "Local CLI entrypoint",
        },
        {
            "id": "web",
            "label": "Web UI",
            "configured": (BASE_DIR / "web-ui").exists(),
            "note": "Gateway + Web UI stack",
        },
        {
            "id": "gateway",
            "label": "Gateway",
            "configured": True,
            "note": "FastAPI gateway service",
        },
        {
            "id": "telegram",
            "label": "Telegram",
            "configured": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
            "note": "Telegram bot integration",
        },
    ]
    for channel in channels:
        override = normalized.get(channel["id"])
        if override is None:
            channel["enabled"] = channel["configured"]
        else:
            channel["enabled"] = override
        channel["probe"] = _channel_probe_results.get(channel["id"])
    return channels


async def _probe_channel(channel_id: str, timeout: float = 4.0) -> dict:
    normalized = channel_id.strip().lower()
    checked_at = datetime.now().isoformat()
    base = {"id": normalized, "checked_at": checked_at}

    if normalized in {"gateway", "cli"}:
        return {**base, "status": "ok", "detail": "local"}

    if normalized == "web":
        url = os.getenv("UA_WEB_UI_URL", "").strip()
        if not url:
            return {**base, "status": "unknown", "detail": "UA_WEB_UI_URL not set"}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url)
            return {
                **base,
                "status": "ok" if resp.status_code < 500 else "error",
                "http_status": resp.status_code,
            }
        except Exception as exc:
            return {**base, "status": "error", "detail": str(exc)}

    if normalized == "telegram":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            return {**base, "status": "not_configured"}
        url = f"https://api.telegram.org/bot{token}/getMe"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url)
            return {
                **base,
                "status": "ok" if resp.status_code == 200 else "error",
                "http_status": resp.status_code,
            }
        except Exception as exc:
            return {**base, "status": "error", "detail": str(exc)}

    return {**base, "status": "unknown", "detail": "unsupported_channel"}


def get_gateway() -> InProcessGateway:
    global _gateway
    if _gateway is None:
        # Pass the configured workspace base to the gateway
        _gateway = InProcessGateway(workspace_base=WORKSPACES_DIR)
    return _gateway


def store_session(session: GatewaySession) -> None:
    _sessions[session.session_id] = session
    runtime = _session_runtime_snapshot(session.session_id)
    if "manager" in globals():
        runtime["active_connections"] = len(manager.session_connections.get(session.session_id, set()))
    runtime["lifecycle_state"] = _runtime_status_from_counters(runtime)
    _sync_runtime_metadata(session.session_id)


def get_session(session_id: str) -> Optional[GatewaySession]:
    return _sessions.get(session_id)


def _sanitize_session_id_or_400(session_id: str) -> str:
    try:
        return validate_session_id(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _sanitize_workspace_dir_or_400(workspace_dir: Optional[str]) -> Optional[str]:
    try:
        return resolve_workspace_dir(
            WORKSPACES_DIR,
            workspace_dir,
            allow_external=allow_external_workspaces_from_env(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _session_policy(session: GatewaySession) -> dict:
    policy = load_session_policy(
        session.workspace_dir,
        session_id=session.session_id,
        user_id=session.user_id,
    )
    save_session_policy(session.workspace_dir, policy)
    return policy


def _policy_metadata_snapshot(policy: dict[str, Any]) -> dict[str, Any]:
    memory = normalize_memory_policy(policy.get("memory") if isinstance(policy, dict) else None)
    return {
        "autonomy_mode": policy.get("autonomy_mode"),
        "identity_mode": policy.get("identity_mode"),
        "tool_profile": policy.get("tool_profile"),
        "memory_mode": memory.get("mode"),
        "session_memory_enabled": memory.get("session_memory_enabled"),
        "memory_tags": memory.get("tags", []),
        "long_term_tag_allowlist": memory.get("long_term_tag_allowlist", []),
    }


def _mark_run_cancel_requested(run_id: Optional[str], reason: str) -> Optional[str]:
    if not run_id:
        return None
    try:
        import universal_agent.main as main_module
        from universal_agent.durable.state import request_run_cancel

        if main_module.runtime_db_conn:
            request_run_cancel(main_module.runtime_db_conn, run_id, reason)
            logger.info("Marked run %s as cancel_requested", run_id)
    except Exception as cancel_err:
        logger.warning("Failed to mark run as cancelled: %s", cancel_err)
    return run_id


async def _cancel_session_execution(session_id: str, reason: str, run_id: Optional[str] = None) -> dict:
    if not run_id:
        session = get_session(session_id)
        if session:
            run_id = session.metadata.get("run_id")
    marked_run_id = _mark_run_cancel_requested(run_id, reason)

    await manager.broadcast(
        session_id,
        {
            "type": "cancelled",
            "data": {
                "reason": reason,
                "run_id": marked_run_id,
                "session_id": session_id,
            },
            "timestamp": datetime.now().isoformat(),
        },
    )

    _add_notification(
        kind="cancelled",
        title="Session Cancelled",
        message=reason,
        session_id=session_id,
        severity="warning",
        metadata={"source": "ops"},
    )

    return {"status": "cancel_requested", "session_id": session_id, "run_id": marked_run_id, "reason": reason}


def is_user_allowed(user_id: str) -> bool:
    """Check if user_id is in the allowlist (if active)."""
    if not ALLOWED_USERS:
        return True
    if user_id in ALLOWED_USERS:
        return True
    # Support numeric Telegram IDs in allowlist (e.g., "7843395933")
    if user_id.startswith("telegram_"):
        telegram_id = user_id.split("telegram_", 1)[1]
        return telegram_id in ALLOWED_USERS
    return False


def _require_ops_auth(request: Request) -> None:
    if not OPS_TOKEN:
        return
    header = request.headers.get("authorization", "")
    token = ""
    if header.lower().startswith("bearer "):
        token = header.split(" ", 1)[1].strip()
    if not token:
        token = request.headers.get("x-ua-ops-token", "").strip()
    if token != OPS_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


# =============================================================================
# WebSocket Connection Manager
# =============================================================================


class ConnectionManager:
    def __init__(self):
        # connection_id -> WebSocket
        self.active_connections: dict[str, WebSocket] = {}
        # session_id -> set of connection_ids
        self.session_connections: dict[str, set[str]] = {}

    async def connect(self, connection_id: str, websocket: WebSocket, session_id: str):
        await websocket.accept()
        self.active_connections[connection_id] = websocket
        
        if session_id not in self.session_connections:
            self.session_connections[session_id] = set()
        self.session_connections[session_id].add(connection_id)
        _set_session_connections(session_id, len(self.session_connections.get(session_id, set())))
        
        logger.info(f"Gateway WebSocket connected: {connection_id} (session: {session_id})")

    def disconnect(self, connection_id: str, session_id: str):
        if connection_id in self.active_connections:
            del self.active_connections[connection_id]
            logger.info(f"Gateway WebSocket disconnected: {connection_id}")
            
        if session_id in self.session_connections:
            self.session_connections[session_id].discard(connection_id)
            if not self.session_connections[session_id]:
                del self.session_connections[session_id]
        _set_session_connections(session_id, len(self.session_connections.get(session_id, set())))

    async def send_json(self, connection_id: str, data: dict, session_id: Optional[str] = None):
        if connection_id in self.active_connections:
            try:
                await self.active_connections[connection_id].send_text(json.dumps(data))
                if session_id:
                    _record_session_event(session_id, str(data.get("type", "")))
            except Exception as e:
                logger.error(f"Failed to send to {connection_id}: {e}")

    async def broadcast(self, session_id: str, data: dict, exclude_connection_id: Optional[str] = None):
        """Send a message to all connections associated with a session_id."""
        _record_session_event(session_id, str(data.get("type", "")))
        if session_id not in self.session_connections:
            return

        payload = json.dumps(data)
        # Snapshot the list to avoid runtime errors if connections drop during iteration
        targets = list(self.session_connections[session_id])
        
        for connection_id in targets:
            if connection_id == exclude_connection_id:
                continue
                
            if connection_id in self.active_connections:
                try:
                    await self.active_connections[connection_id].send_text(payload)
                except Exception as e:
                    logger.error(f"Failed to broadcast to {connection_id}: {e}")


manager = ConnectionManager()


# =============================================================================
# Lifespan
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Universal Agent Gateway Server starting...")
    logger.info(f"📁 Workspaces: {WORKSPACES_DIR}")
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    
    # Initialize runtime database (required by ProcessTurnAdapter -> setup_session)
    import universal_agent.main as main_module
    db_path = get_runtime_db_path()
    logger.info(f"📊 Connecting to runtime DB: {db_path}")
    main_module.runtime_db_conn = connect_runtime_db(db_path)
    # Enable WAL mode for concurrent access (CLI + gateway can coexist)
    main_module.runtime_db_conn.execute("PRAGMA journal_mode=WAL")
    main_module.runtime_db_conn.execute("PRAGMA busy_timeout=5000")
    ensure_schema(main_module.runtime_db_conn)
    
    # Load budget config (defined in main.py)
    main_module.budget_config = main_module.load_budget_config()
    
    # Initialize Heartbeat Service
    global _heartbeat_service, _cron_service, _ops_service
    if HEARTBEAT_ENABLED:
        logger.info("💓 Heartbeat System ENABLED")
        _heartbeat_service = HeartbeatService(
            get_gateway(),
            manager,
            system_event_provider=_drain_system_events,
        )
        await _heartbeat_service.start()
    else:
        logger.info("💤 Heartbeat System DISABLED (feature flag)")

    if CRON_ENABLED:
        logger.info("⏱️ Cron Service ENABLED")
        _cron_service = CronService(
            get_gateway(),
            WORKSPACES_DIR,
            event_sink=_emit_cron_event,
            wake_callback=_cron_wake_callback,
            system_event_callback=_enqueue_system_event,
        )
        await _cron_service.start()
    else:
        logger.info("⏲️ Cron Service DISABLED (feature flag)")
    
    # Always enabled Ops Service
    _ops_service = OpsService(get_gateway(), WORKSPACES_DIR)

    yield
    
    # Cleanup
    if _heartbeat_service:
        await _heartbeat_service.stop()
    if _cron_service:
        await _cron_service.stop()
        
    if main_module.runtime_db_conn:
        main_module.runtime_db_conn.close()
    logger.info("👋 Universal Agent Gateway Server shutting down...")


# =============================================================================
# FastAPI App
# =============================================================================

app = FastAPI(
    title="Universal Agent Gateway",
    description="External HTTP/WebSocket Gateway for Universal Agent",
    version="1.0.0",
    lifespan=lifespan,
)

# Instrument FastAPI with Logfire for automatic HTTP route tracing
try:
    import logfire as _logfire_gw
    if os.getenv("LOGFIRE_TOKEN") or os.getenv("LOGFIRE_WRITE_TOKEN"):
        _logfire_gw.instrument_fastapi(app)
        logger.info("✅ Logfire FastAPI instrumentation enabled for gateway server")
except Exception as _lf_err:
    logger.debug("Logfire FastAPI instrumentation not available: %s", _lf_err)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# REST Endpoints
# =============================================================================


@app.get("/")
async def root():
    return {
        "name": "Universal Agent Gateway",
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "sessions": "/api/v1/sessions",
            "stream": "/api/v1/sessions/{session_id}/stream",
            "health": "/api/v1/health",
        },
    }


@app.get("/api/v1/health")
async def health(response: Response):
    """
    Deep health check associated with DB connectivity.
    """
    import universal_agent.main as main_module
    
    db_status = "unknown"
    db_error = None
    
    is_healthy = True
    try:
        if main_module.runtime_db_conn:
            # Execute a lightweight query to verify connection
            main_module.runtime_db_conn.execute("SELECT 1")
            db_status = "connected"
        else:
            db_status = "disconnected"
            is_healthy = False
    except Exception as e:
        db_status = "error"
        db_error = str(e)
        is_healthy = False
        logger.error(f"Health check failed: {e}")

    if not is_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "healthy" if is_healthy else "unhealthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0",
        "db_status": db_status,
        "db_error": db_error,
        "deployment_profile": _deployment_profile_defaults(),
    }


@app.post("/api/v1/sessions", response_model=CreateSessionResponse)
async def create_session(request: CreateSessionRequest):
    # 1. Enforce Allowlist
    final_user_id = resolve_user_id(request.user_id)
    if not is_user_allowed(final_user_id):
        logger.warning(f"⛔ Access Denied: User '{final_user_id}' not in allowlist.")
        raise HTTPException(status_code=403, detail="Access denied: User not allowed.")

    workspace_dir = _sanitize_workspace_dir_or_400(request.workspace_dir)
    gateway = get_gateway()
    try:
        session = await gateway.create_session(
            user_id=final_user_id,
            workspace_dir=workspace_dir,
        )
        policy = _session_policy(session)
        session.metadata["policy"] = _policy_metadata_snapshot(policy)
        store_session(session)
        _increment_metric("sessions_created")
        if _heartbeat_service:
            _heartbeat_service.register_session(session)
        else:
            logger.warning("Heartbeat service not available in create_session")
        return CreateSessionResponse(
            session_id=session.session_id,
            user_id=session.user_id,
            workspace_dir=session.workspace_dir,
            metadata=session.metadata,
        )
    except Exception as e:
        logger.error(f"Failed to create session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/sessions")
async def list_sessions():
    gateway = get_gateway()
    summaries = gateway.list_sessions()
    return {
        "sessions": [
            SessionSummaryResponse(
                session_id=s.session_id,
                workspace_dir=s.workspace_dir,
                status=s.status,
                metadata=s.metadata,
            ).model_dump()
            for s in summaries
        ]
    }


@app.get("/api/v1/dashboard/summary")
async def dashboard_summary():
    _apply_notification_snooze_expiry()
    sessions_total = 0
    if _ops_service:
        try:
            sessions_total = len(_ops_service.list_sessions())
        except Exception:
            sessions_total = len(_sessions)
    else:
        sessions_total = len(_sessions)

    if _session_runtime:
        active_sessions = sum(
            1
            for runtime in _session_runtime.values()
            if str(runtime.get("lifecycle_state")) == SESSION_STATE_RUNNING
            or int(runtime.get("active_connections", 0)) > 0
        )
    else:
        active_sessions = sum(1 for s in _sessions.values() if s)
    pending_approvals = len(list_approvals(status="pending"))
    unread_notifications = sum(
        1 for item in _notifications if str(item.get("status", "new")).lower() in {"new", "pending"}
    )
    cron_total = 0
    cron_enabled = 0
    if _cron_service:
        jobs = _cron_service.list_jobs()
        cron_total = len(jobs)
        cron_enabled = sum(1 for job in jobs if bool(getattr(job, "enabled", False)))

    return {
        "sessions": {
            "active": active_sessions,
            "total": sessions_total,
        },
        "approvals": {
            "pending": pending_approvals,
            "total": len(list_approvals()),
        },
        "cron": {
            "total": cron_total,
            "enabled": cron_enabled,
        },
        "notifications": {
            "unread": unread_notifications,
            "total": len(_notifications),
        },
        "deployment_profile": _deployment_profile_defaults(),
    }


@app.get("/api/v1/dashboard/notifications")
async def dashboard_notifications(
    limit: int = 100,
    status: Optional[str] = None,
    session_id: Optional[str] = None,
):
    _apply_notification_snooze_expiry()
    items = list(_notifications)
    if status:
        status_norm = status.strip().lower()
        items = [item for item in items if str(item.get("status", "")).lower() == status_norm]
    if session_id:
        safe_session_id = _sanitize_session_id_or_400(session_id)
        items = [item for item in items if item.get("session_id") == safe_session_id]
    limit = max(1, min(limit, 500))
    return {"notifications": items[-limit:][::-1]}


@app.patch("/api/v1/dashboard/notifications/{notification_id}")
async def dashboard_notification_update(notification_id: str, payload: NotificationUpdateRequest):
    status_value = _normalize_notification_status(payload.status)
    if status_value not in {"new", "read", "acknowledged", "snoozed", "dismissed"}:
        raise HTTPException(status_code=400, detail="Invalid status")
    for item in reversed(_notifications):
        if item.get("id") == notification_id:
            _apply_notification_status(
                item,
                status_value=status_value,
                note=payload.note,
                snooze_minutes=payload.snooze_minutes,
            )
            return {"notification": item}
    raise HTTPException(status_code=404, detail="Notification not found")


@app.post("/api/v1/dashboard/notifications/bulk")
async def dashboard_notification_bulk_update(payload: NotificationBulkUpdateRequest):
    status_value = _normalize_notification_status(payload.status)
    if status_value not in {"new", "read", "acknowledged", "snoozed", "dismissed"}:
        raise HTTPException(status_code=400, detail="Invalid status")

    _apply_notification_snooze_expiry()
    kind_filter = str(payload.kind or "").strip().lower()
    current_status_filter = _normalize_notification_status(payload.current_status or "")
    limit = max(1, min(int(payload.limit or 200), 1000))
    updated: list[dict[str, Any]] = []

    for item in reversed(_notifications):
        if len(updated) >= limit:
            break
        if kind_filter and str(item.get("kind", "")).strip().lower() != kind_filter:
            continue
        if current_status_filter and _normalize_notification_status(item.get("status")) != current_status_filter:
            continue
        _apply_notification_status(
            item,
            status_value=status_value,
            note=payload.note,
            snooze_minutes=payload.snooze_minutes,
        )
        updated.append(item)

    return {
        "updated": len(updated),
        "status": status_value,
        "notifications": updated,
    }


@app.post("/api/v1/heartbeat/wake")
async def wake_heartbeat(request: HeartbeatWakeRequest):
    if not _heartbeat_service:
        raise HTTPException(status_code=400, detail="Heartbeat service not available.")

    reason = request.reason or "wake"
    mode = (request.mode or "now").strip().lower()
    if request.session_id:
        session_id = _sanitize_session_id_or_400(request.session_id)
        if mode == "next":
            _heartbeat_service.request_heartbeat_next(session_id, reason=reason)
        else:
            _heartbeat_service.request_heartbeat_now(session_id, reason=reason)
        return {"status": "queued", "session_id": session_id, "reason": reason, "mode": mode}

    for session_id in list(_sessions.keys()):
        if mode == "next":
            _heartbeat_service.request_heartbeat_next(session_id, reason=reason)
        else:
            _heartbeat_service.request_heartbeat_now(session_id, reason=reason)
    return {"status": "queued", "count": len(_sessions), "reason": reason, "mode": mode}


@app.get("/api/v1/heartbeat/last")
async def get_last_heartbeat(session_id: Optional[str] = None):
    if not _heartbeat_service:
        raise HTTPException(status_code=400, detail="Heartbeat service not available.")

    if session_id:
        session_id = _sanitize_session_id_or_400(session_id)
        session = get_session(session_id)
        if session:
            state = _read_heartbeat_state(session.workspace_dir) or {}
        else:
            # Allow heartbeat lookup for inactive sessions if workspace still exists.
            workspace_dir = WORKSPACES_DIR / session_id
            if not workspace_dir.exists():
                raise HTTPException(status_code=404, detail="Session not found.")
            state = _read_heartbeat_state(str(workspace_dir)) or {}
        busy = bool(_heartbeat_service and session_id in _heartbeat_service.busy_sessions)
        return {
            "session_id": session_id,
            "last_run": state.get("last_run"),
            "last_summary": state.get("last_summary"),
            "busy": busy,
        }

    payload: dict[str, dict] = {}
    for sid, session in _sessions.items():
        state = _read_heartbeat_state(session.workspace_dir) or {}
        if not state:
            continue
        busy = bool(_heartbeat_service and sid in _heartbeat_service.busy_sessions)
        payload[sid] = {
            "last_run": state.get("last_run"),
            "last_summary": state.get("last_summary"),
            "busy": busy,
        }
    return {"heartbeats": payload}


@app.post("/api/v1/system/event")
async def post_system_event(request: SystemEventRequest):
    event_type = (request.event_type or "system_event").strip() or "system_event"
    event = {
        "event_id": f"evt_{int(time.time() * 1000)}",
        "type": event_type,
        "payload": request.payload or {},
        "created_at": datetime.now().isoformat(),
    }

    target_sessions: list[str]
    if request.session_id:
        requested_session_id = _sanitize_session_id_or_400(request.session_id)
        if requested_session_id not in _sessions:
            raise HTTPException(status_code=404, detail="Session not found.")
        target_sessions = [requested_session_id]
    else:
        target_sessions = list(_sessions.keys())

    for sid in target_sessions:
        _enqueue_system_event(sid, event)
        if sid in manager.session_connections:
            _broadcast_system_event(sid, event)

    wake_flag = request.wake_heartbeat or request.wake_mode
    if wake_flag and _heartbeat_service and target_sessions:
        mode = "next"
        if isinstance(wake_flag, str):
            mode = wake_flag.strip().lower() or mode
        if mode not in {"now", "next"}:
            mode = "next"
        for sid in target_sessions:
            if mode == "next":
                _heartbeat_service.request_heartbeat_next(sid, reason=f"system_event:{event_type}")
            else:
                _heartbeat_service.request_heartbeat_now(sid, reason=f"system_event:{event_type}")

    return {"status": "queued", "count": len(target_sessions), "event": event}


@app.get("/api/v1/system/events")
async def list_system_events(session_id: str):
    session_id = _sanitize_session_id_or_400(session_id)
    if session_id not in _sessions:
        raise HTTPException(status_code=404, detail="Session not found.")
    return {"session_id": session_id, "events": _system_events.get(session_id, [])}


@app.post("/api/v1/system/presence")
async def set_system_presence(request: SystemPresenceRequest):
    node_id = request.node_id or "gateway"
    presence = {
        "node_id": node_id,
        "status": request.status or "online",
        "reason": request.reason,
        "metadata": request.metadata or {},
        "updated_at": datetime.now().isoformat(),
    }
    _system_presence[node_id] = presence
    _broadcast_presence(presence)
    return {"status": "ok", "presence": presence}


@app.get("/api/v1/system/presence")
async def get_system_presence():
    return {"nodes": list(_system_presence.values())}


@app.get("/api/v1/cron/jobs")
async def list_cron_jobs():
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Cron service not available.")
    return {"jobs": [job.to_dict() for job in _cron_service.list_jobs()]}


@app.post("/api/v1/cron/jobs")
async def create_cron_job(request: CronJobCreateRequest):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Cron service not available.")
    try:
        from universal_agent.cron_service import parse_run_at
        
        # Parse run_at (handles relative like "20m" or absolute ISO)
        run_at_ts = parse_run_at(request.run_at) if request.run_at else None
        
        job = _cron_service.add_job(
            user_id=request.user_id or "cron",
            workspace_dir=_sanitize_workspace_dir_or_400(request.workspace_dir),
            command=request.command,
            every_raw=request.every,
            cron_expr=request.cron_expr,
            timezone=request.timezone,
            run_at=run_at_ts,
            delete_after_run=request.delete_after_run,
            model=request.model,
            enabled=request.enabled,
            metadata=request.metadata or {},
        )
        return {"job": job.to_dict()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/v1/cron/jobs/{job_id}")
async def get_cron_job(job_id: str):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Cron service not available.")
    job = _cron_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Cron job not found")
    return {"job": job.to_dict()}


@app.put("/api/v1/cron/jobs/{job_id}")
async def update_cron_job(job_id: str, request: CronJobUpdateRequest):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Cron service not available.")
    job = _cron_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Cron job not found")
    
    from universal_agent.cron_service import parse_run_at
    
    # Build updates dict, only including non-None values
    updates: dict = {}
    if request.command is not None:
        updates["command"] = request.command
    if request.every is not None:
        updates["every"] = request.every
    if request.cron_expr is not None:
        updates["cron_expr"] = request.cron_expr
    if request.timezone is not None:
        updates["timezone"] = request.timezone
    if request.run_at is not None:
        updates["run_at"] = parse_run_at(request.run_at)
    if request.delete_after_run is not None:
        updates["delete_after_run"] = request.delete_after_run
    if request.model is not None:
        updates["model"] = request.model
    if request.enabled is not None:
        updates["enabled"] = request.enabled
    if request.workspace_dir is not None:
        updates["workspace_dir"] = _sanitize_workspace_dir_or_400(request.workspace_dir)
    if request.user_id is not None:
        updates["user_id"] = request.user_id
    if request.metadata is not None:
        updates["metadata"] = request.metadata
    
    try:
        job = _cron_service.update_job(job_id, updates)
        return {"job": job.to_dict()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/v1/cron/jobs/{job_id}")
async def delete_cron_job(job_id: str):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Cron service not available.")
    job = _cron_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Cron job not found")
    _cron_service.delete_job(job_id)
    return {"status": "deleted", "job_id": job_id}


@app.post("/api/v1/cron/jobs/{job_id}/run")
async def run_cron_job(job_id: str):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Cron service not available.")
    job = _cron_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Cron job not found")
    record = await _cron_service.run_job_now(job_id, reason="manual")
    return {"run": record.to_dict()}


@app.get("/api/v1/cron/jobs/{job_id}/runs")
async def list_cron_job_runs(job_id: str, limit: int = 200):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Cron service not available.")
    return {"runs": _cron_service.list_runs(job_id=job_id, limit=limit)}


@app.get("/api/v1/cron/runs")
async def list_cron_runs(limit: int = 200):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Cron service not available.")
    return {"runs": _cron_service.list_runs(limit=limit)}


@app.get("/api/v1/sessions/{session_id}")
async def get_session_info(session_id: str):
    session_id = _sanitize_session_id_or_400(session_id)
    session = get_session(session_id)
    if not session:
        _increment_metric("resume_attempts")
        gateway = get_gateway()
        try:
            session = await gateway.resume_session(session_id)
            policy = _session_policy(session)
            session.metadata["policy"] = _policy_metadata_snapshot(policy)
            store_session(session)
            _increment_metric("resume_successes")
            if _heartbeat_service:
                _heartbeat_service.register_session(session)
        except ValueError:
            _increment_metric("resume_failures")
            raise HTTPException(status_code=404, detail="Session not found")
            
    # Allowlist check for resume (optional, but good practice)
    if not is_user_allowed(session.user_id):
        raise HTTPException(status_code=403, detail="Access denied: User not allowed.")
        
    return CreateSessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        workspace_dir=session.workspace_dir,
        metadata=session.metadata,
    )


@app.get("/api/v1/sessions/{session_id}/policy")
async def get_session_policy(session_id: str):
    session_id = _sanitize_session_id_or_400(session_id)
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    policy = _session_policy(session)
    return {"session_id": session_id, "policy": policy}


@app.patch("/api/v1/sessions/{session_id}/policy")
async def patch_session_policy(session_id: str, payload: SessionPolicyPatchRequest):
    session_id = _sanitize_session_id_or_400(session_id)
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    updated = update_session_policy(
        session.workspace_dir,
        payload.patch or {},
        session_id=session.session_id,
        user_id=session.user_id,
    )
    session.metadata["policy"] = _policy_metadata_snapshot(updated)
    return {"session_id": session_id, "policy": updated}


@app.get("/api/v1/sessions/{session_id}/pending")
async def get_pending_gate(session_id: str):
    session_id = _sanitize_session_id_or_400(session_id)
    pending = _pending_gated_requests.get(session_id)
    return {"session_id": session_id, "pending": pending}


@app.post("/api/v1/sessions/{session_id}/resume")
async def resume_gated_request(session_id: str, payload: ResumeRequest):
    session_id = _sanitize_session_id_or_400(session_id)
    pending = _pending_gated_requests.get(session_id)
    if not pending:
        raise HTTPException(status_code=404, detail="No pending gated request")

    approval_id = pending.get("approval_id")
    if payload.approval_id and approval_id and payload.approval_id != approval_id:
        raise HTTPException(status_code=400, detail="approval_id mismatch")

    approval_record = None
    if approval_id:
        approval_record = update_approval(
            approval_id,
            {
                "status": "approved",
                "notes": payload.reason or "Approved via resume endpoint",
                "metadata": {"resumed_at": datetime.now().isoformat()},
            },
        )
    pending["status"] = "approved"
    pending["updated_at"] = datetime.now().isoformat()
    return {"session_id": session_id, "pending": pending, "approval": approval_record}


@app.delete("/api/v1/sessions/{session_id}")
async def delete_session(session_id: str):
    session_id = _sanitize_session_id_or_400(session_id)
    _mark_session_terminal(session_id, "deleted")
    _sessions.pop(session_id, None)
    _pending_gated_requests.pop(session_id, None)
    _session_turn_state.pop(session_id, None)
    _session_turn_locks.pop(session_id, None)
    gateway = get_gateway()
    await gateway.close_session(session_id)
    _session_runtime.pop(session_id, None)
    if _heartbeat_service:
        _heartbeat_service.unregister_session(session_id)
    return {"status": "deleted", "session_id": session_id}


# =============================================================================
# Ops / Control Plane Endpoints
# =============================================================================


@app.get("/api/v1/ops/sessions")
async def ops_list_sessions(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    status: str = "all",
    source: str = "all",
    owner: Optional[str] = None,
    memory_mode: str = "all",
):
    _require_ops_auth(request)
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")
    summaries = _ops_service.list_sessions(
        status_filter=status,
        source_filter=source,
        owner_filter=owner,
        memory_mode_filter=memory_mode,
    )
    return {
        "sessions": summaries[offset : offset + limit],
        "total": len(summaries),
        "limit": limit,
        "offset": offset,
    }


@app.post("/api/v1/ops/sessions/cancel")
async def ops_cancel_outstanding_sessions(request: Request, payload: OpsSessionCancelRequest):
    _require_ops_auth(request)
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")

    reason = (payload.reason or "Cancelled from ops bulk session controls").strip()
    if not reason:
        reason = "Cancelled from ops bulk session controls"

    sessions = _ops_service.list_sessions(status_filter="all")
    candidates: list[dict[str, Any]] = []
    for item in sessions:
        status = str(item.get("status", "")).lower()
        active_runs = int(item.get("active_runs") or 0)
        if status in {"running", "active"} or active_runs > 0:
            candidates.append(item)

    cancelled: list[str] = []
    for item in candidates:
        session_id = str(item.get("session_id", "")).strip()
        if not session_id:
            continue
        await _cancel_session_execution(session_id, reason)
        cancelled.append(session_id)

    return {
        "status": "cancel_requested",
        "reason": reason,
        "sessions_considered": len(candidates),
        "sessions_cancelled": cancelled,
    }


@app.get("/api/v1/ops/metrics/session-continuity")
async def ops_session_continuity_metrics(request: Request):
    _require_ops_auth(request)
    return {"metrics": _observability_metrics_snapshot()}


@app.get("/api/v1/ops/sessions/{session_id}")
async def ops_get_session(request: Request, session_id: str):
    _require_ops_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")
    details = _ops_service.get_session_details(session_id)
    if not details:
        raise HTTPException(status_code=404, detail="Session not found")
    return details


@app.get("/api/v1/ops/sessions/{session_id}/preview")
async def ops_session_preview(
    request: Request, session_id: str, limit: int = 200, max_bytes: int = 200_000
):
    _require_ops_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")
    result = _ops_service.tail_file(session_id, "activity_journal.log", limit=limit, max_bytes=max_bytes)
    return {"session_id": session_id, **result}


@app.post("/api/v1/ops/sessions/{session_id}/reset")
async def ops_session_reset(
    request: Request, session_id: str, payload: OpsSessionResetRequest
):
    _require_ops_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")
    result = _ops_service.reset_session(
        session_id,
        clear_logs=payload.clear_logs,
        clear_memory=payload.clear_memory,
        clear_work_products=payload.clear_work_products,
    )
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.post("/api/v1/ops/sessions/{session_id}/archive")
async def ops_session_archive(
    request: Request, session_id: str, payload: OpsSessionArchiveRequest
):
    _require_ops_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")
    result = _ops_service.archive_session(
        session_id,
        clear_memory=payload.clear_memory,
        clear_work_products=payload.clear_work_products,
    )
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.post("/api/v1/ops/sessions/{session_id}/cancel")
async def ops_session_cancel(
    request: Request, session_id: str, payload: OpsSessionCancelRequest
):
    _require_ops_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")
    if not _ops_service.get_session_details(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    reason = (payload.reason or "Cancelled from ops panel").strip()
    if not reason:
        reason = "Cancelled from ops panel"
    return await _cancel_session_execution(session_id, reason)


@app.post("/api/v1/ops/sessions/{session_id}/compact")
async def ops_session_compact(
    request: Request, session_id: str, payload: OpsSessionCompactRequest
):
    _require_ops_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")
    result = _ops_service.compact_session(session_id, payload.max_lines, payload.max_bytes)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.delete("/api/v1/ops/sessions/{session_id}")
async def ops_delete_session(request: Request, session_id: str, confirm: bool = False):
    _require_ops_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true is required")
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")
    
    deleted = await _ops_service.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted", "session_id": session_id}


@app.get("/api/v1/ops/logs/tail")
async def ops_logs_tail(
    request: Request,
    session_id: Optional[str] = None,
    path: Optional[str] = None,
    cursor: Optional[int] = None,
    limit: int = 200,
    max_bytes: int = 250_000,
):
    _require_ops_auth(request)
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")

    if session_id:
        session_id = _sanitize_session_id_or_400(session_id)
        result = _ops_service.tail_file(session_id, "run.log", cursor=cursor, limit=limit, max_bytes=max_bytes)
        file_path = str(_ops_service.workspaces_dir / session_id / "run.log")
        return {"file": file_path, **result}
    elif path:
        try:
            candidate = resolve_ops_log_path(WORKSPACES_DIR, path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result = _ops_service.read_log_slice(candidate, cursor=cursor, limit=limit, max_bytes=max_bytes)
        return {"file": str(candidate), **result}
    else:
        raise HTTPException(status_code=400, detail="session_id or path required")


@app.get("/api/v1/ops/skills")
async def ops_skills_status(request: Request):
    _require_ops_auth(request)
    return {"skills": _load_skill_catalog()}


@app.patch("/api/v1/ops/skills/{skill_key}")
async def ops_skill_update(request: Request, skill_key: str, payload: OpsSkillUpdateRequest):
    _require_ops_auth(request)
    config = load_ops_config()
    skills_cfg = config.get("skills", {})
    entries = skills_cfg.get("entries", {})
    if not isinstance(entries, dict):
        entries = {}
    normalized = skill_key.strip().lower()
    entry = entries.get(normalized, {})
    if not isinstance(entry, dict):
        entry = {}
    if payload.enabled is not None:
        entry["enabled"] = payload.enabled
    entries[normalized] = entry
    skills_cfg["entries"] = entries
    config["skills"] = skills_cfg
    write_ops_config(config)
    return {"status": "updated", "skill": normalized, "config": entry}


@app.get("/api/v1/ops/skills/{skill_key}/doc")
async def ops_skill_doc(request: Request, skill_key: str):
    _require_ops_auth(request)
    catalog = _load_skill_catalog()
    normalized = skill_key.strip().lower()
    logger.info(f"Docs requested for skill: '{skill_key}' (normalized: '{normalized}')")
    
    for s in catalog:
        name_norm = str(s.get("name", "")).strip().lower()
        if name_norm == normalized:
            path = s.get("path")
            logger.info(f"Found match: {s.get('name')} at path: {path}")
            if path and os.path.exists(path):
                return {"skill": s.get("name"), "content": Path(path).read_text(encoding="utf-8")}
            else:
                logger.warning(f"Skill path does not exist: {path}")
                # Fallback: check if path is relative to repo root?
                # The path should be absolute from _load_skill_catalog but let's be sure
                return {"skill": s.get("name"), "content": f"Use locally at: {path}\n\n(File not found at server runtime)"}
                
    logger.warning(f"Skill not found in catalog: {normalized}. Available: {[s.get('name') for s in catalog]}")
    raise HTTPException(status_code=404, detail="Skill documentation not found")


@app.get("/api/v1/ops/channels")
async def ops_channels_status(request: Request):
    _require_ops_auth(request)
    return {"channels": _load_channel_status()}


@app.post("/api/v1/ops/channels/{channel_id}/probe")
async def ops_channels_probe(request: Request, channel_id: str, timeout: float = 4.0):
    _require_ops_auth(request)
    result = await _probe_channel(channel_id, timeout=timeout)
    _channel_probe_results[channel_id.strip().lower()] = result
    return {"probe": result}


@app.post("/api/v1/ops/channels/{channel_id}/logout")
async def ops_channels_logout(request: Request, channel_id: str):
    _require_ops_auth(request)
    config = load_ops_config()
    channels_cfg = config.get("channels", {})
    entries = channels_cfg.get("entries", {})
    if not isinstance(entries, dict):
        entries = {}
    normalized = channel_id.strip().lower()
    entry = entries.get(normalized, {})
    if not isinstance(entry, dict):
        entry = {}
    entry["enabled"] = False
    entries[normalized] = entry
    channels_cfg["entries"] = entries
    config["channels"] = channels_cfg
    write_ops_config(config)
    return {"status": "disabled", "channel": normalized}


@app.get("/api/v1/ops/config")
async def ops_config_get(request: Request):
    _require_ops_auth(request)
    config = load_ops_config()
    return {"config": config, "base_hash": ops_config_hash(config)}


@app.get("/api/v1/ops/config/schema")
async def ops_config_schema_get(request: Request):
    _require_ops_auth(request)
    return {"schema": ops_config_schema()}


@app.get("/api/v1/ops/deployment/profile")
async def ops_deployment_profile_get(request: Request):
    _require_ops_auth(request)
    return {"deployment_profile": _deployment_profile_defaults()}


@app.post("/api/v1/ops/config")
async def ops_config_set(request: Request, payload: OpsConfigRequest):
    _require_ops_auth(request)
    current = load_ops_config()
    if payload.base_hash and payload.base_hash != ops_config_hash(current):
        raise HTTPException(status_code=409, detail="Config changed; reload and retry")
    write_ops_config(payload.config or {})
    updated = load_ops_config()
    return {"config": updated, "base_hash": ops_config_hash(updated)}


@app.patch("/api/v1/ops/config")
async def ops_config_patch(request: Request, payload: OpsConfigPatchRequest):
    _require_ops_auth(request)
    current = load_ops_config()
    if payload.base_hash and payload.base_hash != ops_config_hash(current):
        raise HTTPException(status_code=409, detail="Config changed; reload and retry")
    updated = apply_merge_patch(current, payload.patch or {})
    write_ops_config(updated)
    return {"config": updated, "base_hash": ops_config_hash(updated)}


@app.get("/api/v1/ops/approvals")
async def ops_approvals_list(request: Request, status: Optional[str] = None):
    _require_ops_auth(request)
    return {"approvals": list_approvals(status=status)}


@app.post("/api/v1/ops/approvals")
async def ops_approvals_create(request: Request, payload: OpsApprovalCreateRequest):
    _require_ops_auth(request)
    record = upsert_approval(payload.model_dump(exclude_none=True))
    return {"approval": record}


@app.patch("/api/v1/ops/approvals/{approval_id}")
async def ops_approvals_update(
    request: Request, approval_id: str, payload: OpsApprovalUpdateRequest
):
    _require_ops_auth(request)
    record = update_approval(approval_id, payload.model_dump(exclude_none=True))
    if record is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    for pending in _pending_gated_requests.values():
        if pending.get("approval_id") == approval_id:
            pending["status"] = record.get("status")
            pending["updated_at"] = datetime.now().isoformat()
    return {"approval": record}


@app.get("/api/v1/ops/models")
async def ops_models_list(request: Request):
    _require_ops_auth(request)
    models = []
    sonnet = os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL")
    haiku = os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL")
    if sonnet:
        models.append({"id": sonnet, "label": "default-sonnet"})
    if haiku:
        models.append({"id": haiku, "label": "default-haiku"})
    return {"models": models}


# =============================================================================
# WebSocket Streaming Endpoint
# =============================================================================


def agent_event_to_wire(event: AgentEvent) -> dict:
    return {
        "type": event.type.value if hasattr(event.type, "value") else str(event.type),
        "data": event.data,
        "timestamp": datetime.now().isoformat(),
        "time_offset": event.data.get("time_offset") if isinstance(event.data, dict) else None,
    }


@app.websocket("/api/v1/sessions/{session_id}/stream")
async def websocket_stream(websocket: WebSocket, session_id: str):
    try:
        session_id = validate_session_id(session_id)
    except ValueError:
        await websocket.close(code=4000, reason="Invalid session id format")
        return
    connection_id = f"gw_{session_id}_{time.time()}"
    # Register connection with session_id
    await manager.connect(connection_id, websocket, session_id)
    _increment_metric("ws_attach_attempts")

    gateway = get_gateway()
    session = get_session(session_id)

    if not session:
        _increment_metric("resume_attempts")
        try:
            session = await gateway.resume_session(session_id)
            store_session(session)
            _increment_metric("resume_successes")
            if _heartbeat_service:
                _heartbeat_service.register_session(session)
        except ValueError:
            _increment_metric("resume_failures")
            _increment_metric("ws_attach_failures")
            await websocket.close(code=4004, reason="Session not found")
            manager.disconnect(connection_id, session_id)
            return

    # 1. Enforce Allowlist for WebSocket
    if not is_user_allowed(session.user_id):
        logger.warning(f"⛔ Access Denied (WS): User '{session.user_id}' not in allowlist.")
        _increment_metric("ws_attach_failures")
        await websocket.close(code=4003, reason="Access denied")
        manager.disconnect(connection_id, session_id)
        return

    # Send initial connection success message
    await manager.send_json(
        connection_id,
        {
            "type": "connected",
            "data": {
                "session_id": session.session_id,
                "workspace_dir": session.workspace_dir,
            },
            "timestamp": datetime.now().isoformat(),
        },
        session_id=session_id,
    )
    _increment_metric("ws_attach_successes")

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                msg_type = msg.get("type", "")
                logger.info("WS message received (session=%s): %s", session_id, msg_type)

                if msg_type == "execute":
                    user_input = msg.get("data", {}).get("user_input", "")
                    if not user_input.strip():
                        await manager.send_json(
                            connection_id,
                            {
                                "type": "error",
                                "data": {"message": "Empty user_input"},
                                "timestamp": datetime.now().isoformat(),
                            },
                            session_id=session_id,
                        )
                        continue

                    raw_data = msg.get("data", {}) or {}
                    client_turn_id = _normalize_client_turn_id(raw_data.get("client_turn_id"))
                    metadata = raw_data.get("metadata", {}) or {}
                    if not isinstance(metadata, dict):
                        metadata = {"raw": metadata}
                    system_events = _drain_system_events(session_id)
                    if system_events:
                        metadata = {**metadata, "system_events": system_events}
                    policy = _session_policy(session)
                    memory_policy = normalize_memory_policy(policy.get("memory"))

                    resume_requested = user_input.strip().lower() in {"resume", "continue", "/resume"}
                    pending_gate = _pending_gated_requests.get(session_id)
                    clear_pending_gate_on_success = False
                    if resume_requested and pending_gate:
                        if not _pending_gate_is_approved(session_id):
                            await manager.send_json(
                                connection_id,
                                {
                                    "type": "error",
                                    "data": {
                                        "message": "Pending request is not approved yet. Approve it first, then resume."
                                    },
                                    "timestamp": datetime.now().isoformat(),
                                },
                                session_id=session_id,
                            )
                            continue
                        saved_request = pending_gate.get("request", {})
                        if not isinstance(saved_request, dict):
                            saved_request = {}
                        user_input = str(saved_request.get("user_input") or user_input)
                        force_complex = bool(saved_request.get("force_complex", raw_data.get("force_complex", False)))
                        saved_metadata = saved_request.get("metadata", {})
                        if not isinstance(saved_metadata, dict):
                            saved_metadata = {}
                        metadata = {
                            **saved_metadata,
                            "resumed": True,
                            "identity_mode": pending_gate.get("identity_mode") or saved_metadata.get("identity_mode", "persona"),
                            "autonomy_mode": policy.get("autonomy_mode", "yolo"),
                            "memory_policy": memory_policy,
                            "memory_mode": memory_policy.get("mode"),
                            "memory_tags": memory_policy.get("tags", []),
                        }
                        clear_pending_gate_on_success = True
                    else:
                        metadata = {
                            **metadata,
                            "identity_mode": policy.get("identity_mode", "persona"),
                            "autonomy_mode": policy.get("autonomy_mode", "yolo"),
                            "memory_policy": memory_policy,
                            "memory_mode": memory_policy.get("mode"),
                            "memory_tags": memory_policy.get("tags", []),
                        }
                        evaluation = evaluate_request_against_policy(
                            policy,
                            user_input=user_input,
                            metadata=metadata,
                        )
                        decision = str(evaluation.get("decision", "allow")).lower()
                        categories = evaluation.get("categories") or []
                        reasons = evaluation.get("reasons") or []

                        if decision == "deny":
                            reason_text = "; ".join(str(reason) for reason in reasons) or "Policy denied request."
                            _add_notification(
                                kind="policy_denied",
                                title="Request Blocked",
                                message=reason_text,
                                session_id=session_id,
                                severity="warning",
                                requires_action=True,
                                metadata={"categories": categories},
                            )
                            await manager.send_json(
                                connection_id,
                                {
                                    "type": "error",
                                    "data": {"message": reason_text, "categories": categories},
                                    "timestamp": datetime.now().isoformat(),
                                },
                                session_id=session_id,
                            )
                            continue

                        if decision == "require_approval":
                            approval = upsert_approval(
                                {
                                    "phase_id": f"session_gate_{session_id}",
                                    "status": "pending",
                                    "summary": f"Approval required for request categories: {', '.join(categories) or 'unknown'}",
                                    "requested_by": session.user_id,
                                    "metadata": {
                                        "session_id": session_id,
                                        "categories": categories,
                                        "reasons": reasons,
                                        "user_input": user_input,
                                    },
                                }
                            )
                            _pending_gated_requests[session_id] = {
                                "session_id": session_id,
                                "approval_id": approval.get("approval_id"),
                                "status": "pending",
                                "categories": categories,
                                "reasons": reasons,
                                "created_at": datetime.now().isoformat(),
                                "request": {
                                    "user_input": user_input,
                                    "force_complex": raw_data.get("force_complex", False),
                                    "metadata": metadata,
                                },
                                "identity_mode": policy.get("identity_mode", "persona"),
                            }
                            _add_notification(
                                kind="approval_required",
                                title="Approval Required",
                                message=f"Session {session_id} is waiting for approval.",
                                session_id=session_id,
                                severity="warning",
                                requires_action=True,
                                metadata={
                                    "approval_id": approval.get("approval_id"),
                                    "categories": categories,
                                },
                            )
                            await manager.send_json(
                                connection_id,
                                {
                                    "type": "status",
                                    "data": {
                                        "status": "approval_required",
                                        "approval_id": approval.get("approval_id"),
                                        "categories": categories,
                                        "reasons": reasons,
                                    },
                                    "timestamp": datetime.now().isoformat(),
                                },
                                session_id=session_id,
                            )
                            continue

                        force_complex = raw_data.get("force_complex", False)

                    async with _session_turn_lock(session_id):
                        admission = _admit_turn(
                            session_id=session_id,
                            connection_id=connection_id,
                            user_input=user_input,
                            force_complex=bool(force_complex),
                            metadata=metadata,
                            client_turn_id=client_turn_id,
                        )

                    decision = str(admission.get("decision", "accepted"))
                    admitted_turn_id = str(admission.get("turn_id") or "")
                    if decision == "busy":
                        _increment_metric("turn_busy_rejected")
                        await manager.send_json(
                            connection_id,
                            {
                                "type": "status",
                                "data": {
                                    "status": "turn_rejected_busy",
                                    "active_turn_id": admitted_turn_id,
                                    "message": "Another turn is currently running for this session.",
                                },
                                "timestamp": datetime.now().isoformat(),
                            },
                            session_id=session_id,
                        )
                        continue
                    if decision == "duplicate_in_progress":
                        _increment_metric("turn_duplicate_in_progress")
                        await manager.send_json(
                            connection_id,
                            {
                                "type": "status",
                                "data": {
                                    "status": "turn_in_progress",
                                    "turn_id": admitted_turn_id,
                                    "message": "This turn is already in progress.",
                                },
                                "timestamp": datetime.now().isoformat(),
                            },
                            session_id=session_id,
                        )
                        continue
                    if decision == "duplicate_completed":
                        _increment_metric("turn_duplicate_completed")
                        await manager.send_json(
                            connection_id,
                            {
                                "type": "status",
                                "data": {
                                    "status": "duplicate_turn_ignored",
                                    "turn_id": admitted_turn_id,
                                    "message": "Duplicate turn ignored; request already processed.",
                                },
                                "timestamp": datetime.now().isoformat(),
                            },
                            session_id=session_id,
                        )
                        await manager.send_json(
                            connection_id,
                            {
                                "type": "query_complete",
                                "data": {"turn_id": admitted_turn_id},
                                "timestamp": datetime.now().isoformat(),
                            },
                            session_id=session_id,
                        )
                        continue

                    request_metadata = {
                        **metadata,
                        "turn_id": admitted_turn_id,
                    }
                    if client_turn_id:
                        request_metadata["client_turn_id"] = client_turn_id

                    request = GatewayRequest(
                        user_input=user_input,
                        force_complex=force_complex,
                        metadata=request_metadata,
                    )
                    logger.info(
                        "WS execute start (session=%s, user_id=%s, len=%s)",
                        session_id,
                        session.user_id,
                        len(user_input),
                    )
                    _increment_session_active_runs(session_id)

                    async def run_execution(turn_id: str):
                        saw_streaming_text = False
                        tool_call_count = 0
                        execution_duration_seconds = 0.0
                        execution_start_ts = time.time()
                        if _heartbeat_service:
                            _heartbeat_service.busy_sessions.add(session.session_id)
                        try:
                            # Execute the request and stream to all attached clients for this session.
                            async for event in gateway.execute(session, request):
                                if event.type == EventType.TOOL_CALL:
                                    tool_call_count += 1
                                elif event.type == EventType.ITERATION_END and isinstance(event.data, dict):
                                    execution_duration_seconds = float(
                                        event.data.get("duration_seconds") or execution_duration_seconds
                                    )
                                    # Prefer engine-provided count if available
                                    if isinstance(event.data.get("tool_calls"), int):
                                        tool_call_count = int(event.data["tool_calls"])
                                if (
                                    event.type == EventType.TEXT
                                    and isinstance(event.data, dict)
                                    and event.data.get("final") is True
                                    and saw_streaming_text
                                ):
                                    continue
                                if (
                                    event.type == EventType.TEXT
                                    and isinstance(event.data, dict)
                                    and event.data.get("time_offset") is not None
                                ):
                                    saw_streaming_text = True
                                if event.type == EventType.ERROR:
                                    log_tail = None
                                    if session.workspace_dir:
                                        log_tail = _read_run_log_tail(session.workspace_dir)
                                    # Normalize error payload for clients
                                    if isinstance(event.data, dict):
                                        if "message" not in event.data and "error" in event.data:
                                            event.data["message"] = event.data.get("error")
                                        if log_tail and "log_tail" not in event.data:
                                            event.data["log_tail"] = log_tail
                                    logger.error(
                                        "Agent error event (session=%s): %s",
                                        session.session_id,
                                        event.data,
                                    )
                                await manager.broadcast(session_id, agent_event_to_wire(event))

                            # Generate checkpoint for next session/follow-up
                            try:
                                from universal_agent.session_checkpoint import SessionCheckpointGenerator
                                workspace_path = Path(session.workspace_dir)
                                generator = SessionCheckpointGenerator(workspace_path)
                                if execution_duration_seconds <= 0:
                                    execution_duration_seconds = round(time.time() - execution_start_ts, 3)
                                checkpoint_result = SimpleNamespace(
                                    tool_calls=tool_call_count,
                                    execution_time_seconds=execution_duration_seconds,
                                )
                                checkpoint = generator.generate_from_result(
                                    session_id=session.session_id,
                                    original_request=user_input,
                                    result=checkpoint_result,
                                )
                                generator.save(checkpoint)
                                logger.info(f"✅ Saved session checkpoint: {workspace_path / 'session_checkpoint.json'}")
                            except Exception as ckpt_err:
                                logger.warning(f"⚠️ Failed to save checkpoint: {ckpt_err}")

                            _add_notification(
                                kind="mission_complete",
                                title="Mission Completed",
                                message="Session completed execution successfully.",
                                session_id=session.session_id,
                                severity="info",
                                metadata={
                                    "tool_calls": tool_call_count,
                                    "duration_seconds": execution_duration_seconds,
                                },
                            )

                            await manager.broadcast(
                                session_id,
                                {
                                    "type": "query_complete",
                                    "data": {"turn_id": turn_id},
                                    "timestamp": datetime.now().isoformat(),
                                },
                            )

                            await manager.broadcast(
                                session_id,
                                {"type": "pong", "data": {}, "timestamp": datetime.now().isoformat()},
                            )
                            logger.info("WS execute complete (session=%s)", session_id)
                            if clear_pending_gate_on_success:
                                _pending_gated_requests.pop(session_id, None)
                            async with _session_turn_lock(session_id):
                                _finalize_turn(session_id, turn_id, TURN_STATUS_COMPLETED)
                        except Exception as e:
                            logger.error("Execution error for session %s: %s", session_id, e, exc_info=True)
                            _add_notification(
                                kind="assistance_needed",
                                title="Session Failed",
                                message=str(e),
                                session_id=session.session_id,
                                severity="error",
                                requires_action=True,
                            )
                            await manager.broadcast(
                                session_id,
                                {
                                    "type": "error",
                                    "data": {"message": str(e)},
                                    "timestamp": datetime.now().isoformat(),
                                },
                            )
                            async with _session_turn_lock(session_id):
                                _finalize_turn(session_id, turn_id, TURN_STATUS_FAILED, error_message=str(e))
                        finally:
                            _decrement_session_active_runs(session_id)
                            if _heartbeat_service:
                                _heartbeat_service.busy_sessions.discard(session.session_id)
                    
                    asyncio.create_task(run_execution(admitted_turn_id))
                
                elif msg_type == "input_response":
                    input_id = msg.get("data", {}).get("input_id", "default")
                    response = msg.get("data", {}).get("response", "")
                    
                    # 1. Try to resolve via gateway session (new path)
                    success = await gateway.resolve_input(session_id, input_id, response)
                    
                    # 2. Try to resolve via active adapter (in-process path)
                    if not success:
                         adapter = gateway._adapters.get(session_id)
                         if adapter and input_id in adapter._pending_inputs:
                             future = adapter._pending_inputs.pop(input_id)
                             if not future.done():
                                 future.set_result(response)
                                 success = True
                    
                    if not success:
                         logger.warning(f"Failed to resolve input {input_id} for session {session_id}")
                    else:
                         logger.info(f"Resolved input {input_id} for session {session_id}")
                
                elif msg_type == "broadcast_test":
                     # Test event to verify broadcast capability (Phase 1 verification)
                     payload = {
                         "type": "server_notice", 
                         "data": {"message": "Broadcast test received"},
                         "timestamp": datetime.now().isoformat()
                     }
                     # Broadcast to ALL connections for this session
                     await manager.broadcast(session_id, payload)

                elif msg_type == "cancel":
                    # User requested to stop the current agent run
                    reason = msg.get("data", {}).get("reason", "User requested stop")
                    run_id = session.metadata.get("run_id")
                    logger.info("Cancel request received (session=%s, run=%s, reason=%s)", session_id, run_id, reason)
                    await _cancel_session_execution(session_id, reason, run_id=run_id)

                else:
                    await manager.send_json(
                        connection_id,
                        {
                            "type": "error",
                            "data": {"message": f"Unknown message type: {msg_type}"},
                            "timestamp": datetime.now().isoformat(),
                        },
                        session_id=session_id,
                    )

            except json.JSONDecodeError:
                await manager.send_json(
                    connection_id,
                    {
                        "type": "error",
                        "data": {"message": "Invalid JSON"},
                        "timestamp": datetime.now().isoformat(),
                    },
                    session_id=session_id,
                )
            except Exception as e:
                logger.error(f"Error handling message: {e}")
                await manager.send_json(
                    connection_id,
                    {
                        "type": "error",
                        "data": {"message": str(e)},
                        "timestamp": datetime.now().isoformat(),
                    },
                    session_id=session_id,
                )

    except WebSocketDisconnect:
        manager.disconnect(connection_id, session_id)
        logger.info(f"Gateway WebSocket disconnected: {connection_id}")
    except Exception as e:
        manager.disconnect(connection_id, session_id)
        logger.error(f"Gateway WebSocket error: {e}")


# =============================================================================
# Main Entry Point
# =============================================================================


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("UA_GATEWAY_PORT", "8002"))
    host = os.getenv("UA_GATEWAY_HOST", "0.0.0.0")

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║         UNIVERSAL AGENT GATEWAY SERVER v1.0                  ║
╠══════════════════════════════════════════════════════════════╣
║  REST:      http://{host}:{port}/api/v1/sessions
║  WebSocket: ws://{host}:{port}/api/v1/sessions/{{id}}/stream
║  Docs:      http://{host}:{port}/docs
╚══════════════════════════════════════════════════════════════╝
    """)

    uvicorn.run(app, host=host, port=port, log_level="info")

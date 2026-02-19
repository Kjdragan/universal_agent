"""
Universal Agent Gateway Server — External HTTP/WebSocket API.

Exposes the InProcessGateway as a standalone service for external clients.
Server runs on port 8002 by default (configurable via UA_GATEWAY_PORT env var).

Usage:
    python -m universal_agent.gateway_server
"""

import asyncio
from collections import deque
import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
import httpx

# Load .env early so SDK/CLI subprocesses inherit API keys and settings.
BASE_DIR = Path(__file__).parent.parent.parent
load_dotenv(BASE_DIR / ".env", override=False)
from universal_agent.utils.env_aliases import apply_xai_key_aliases
apply_xai_key_aliases()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
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
from universal_agent.durable.state import (
    get_vp_session,
    list_vp_events,
    list_vp_missions,
    list_vp_session_events,
)
from universal_agent.heartbeat_service import HeartbeatService
from universal_agent.cron_service import CronService, parse_run_at
from universal_agent.ops_service import OpsService
from universal_agent.ops_config import (
    apply_merge_patch,
    load_ops_config,
    ops_config_hash,
    ops_config_schema,
    write_ops_config,
)
from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.approvals import list_approvals, update_approval, upsert_approval
from universal_agent.work_threads import (
    append_work_thread_decision,
    list_work_threads,
    update_work_thread,
    upsert_work_thread,
)
from universal_agent.hooks_service import HooksService
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
from universal_agent.utils.json_utils import extract_json_payload
from universal_agent.youtube_ingest import ingest_youtube_transcript, normalize_video_target
from universal_agent.mission_guardrails import build_mission_contract, MissionGuardrailTracker
from universal_agent.runtime_env import ensure_runtime_path, runtime_tool_status
from universal_agent.timeout_policy import (
    gateway_ws_send_timeout_seconds,
    session_cancel_wait_seconds,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
ensure_runtime_path()

# Feature flags (placeholders, no runtime behavior changes yet)
HEARTBEAT_ENABLED = heartbeat_enabled()
CRON_ENABLED = cron_enabled()
MEMORY_INDEX_ENABLED = memory_index_enabled()
MIN_HEARTBEAT_INTERVAL_SECONDS = 30 * 60
HEARTBEAT_INTERVAL_SECONDS = max(
    MIN_HEARTBEAT_INTERVAL_SECONDS,
    int(os.getenv("UA_HEARTBEAT_INTERVAL_SECONDS", str(MIN_HEARTBEAT_INTERVAL_SECONDS)) or MIN_HEARTBEAT_INTERVAL_SECONDS),
)
CALENDAR_HEARTBEAT_SESSION_MAX_IDLE_SECONDS = max(
    3600,
    int(os.getenv("UA_CALENDAR_HEARTBEAT_SESSION_MAX_IDLE_SECONDS", str(72 * 3600)) or (72 * 3600)),
)

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
    # Keep strict allowlist mode, but automatically include runtime technical identities
    # that are commonly used by first-party UA services.
    _runtime_identity_candidates = {
        (os.getenv("COMPOSIO_USER_ID") or "").strip(),
        (os.getenv("DEFAULT_USER_ID") or "").strip(),
        (os.getenv("UA_DASHBOARD_OWNER_ID") or "").strip(),
    }
    _telegram_allowed = (os.getenv("TELEGRAM_ALLOWED_USER_IDS") or "").strip()
    if _telegram_allowed:
        _runtime_identity_candidates.update(
            {item.strip() for item in _telegram_allowed.split(",") if item.strip()}
        )
    _runtime_identity_candidates.discard("")
    _added_runtime_identities = sorted(_runtime_identity_candidates - ALLOWED_USERS)
    if _added_runtime_identities:
        ALLOWED_USERS.update(_added_runtime_identities)
        logger.info(
            "➕ Added runtime identities to allowlist: %s",
            ", ".join(_added_runtime_identities),
        )
    logger.info(f"🔒 Authenticated Access Only. Allowed Users: {len(ALLOWED_USERS)}")
else:
    logger.info("🔓 Public Access Mode (No Allowlist configured)")

# Ops access token (optional hard gate for /api/v1/ops/* endpoints)
OPS_TOKEN = os.getenv("UA_OPS_TOKEN", "").strip()
SESSION_API_TOKEN = (os.getenv("UA_INTERNAL_API_TOKEN", "").strip() or OPS_TOKEN)


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
    user_id: Optional[str] = None
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
    schedule_time: Optional[str] = None  # Simplified time input (e.g., "in 20 minutes", "4:30 pm")
    repeat: Optional[bool] = None  # Simplified repeat toggle
    timeout_seconds: Optional[int] = None  # Per-job execution timeout
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
    schedule_time: Optional[str] = None  # Simplified time input for updates
    repeat: Optional[bool] = None  # Optional repeat override for simplified updates
    timeout_seconds: Optional[int] = None
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


class OpsRemoteSyncUpdateRequest(BaseModel):
    enabled: bool = False


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


class OpsWorkThreadUpsertRequest(BaseModel):
    thread_id: Optional[str] = None
    session_id: str
    title: Optional[str] = None
    target: Optional[str] = None
    branch: Optional[str] = None
    workspace_dir: Optional[str] = None
    summary: Optional[str] = None
    status: Optional[str] = None
    acceptance_criteria: Optional[list[str]] = None
    open_questions: Optional[list[str]] = None
    patch_version: Optional[int] = None
    test_status: Optional[str] = None
    risk_notes: Optional[str] = None
    metadata: Optional[dict] = None


class OpsWorkThreadUpdateRequest(BaseModel):
    title: Optional[str] = None
    target: Optional[str] = None
    branch: Optional[str] = None
    workspace_dir: Optional[str] = None
    summary: Optional[str] = None
    status: Optional[str] = None
    acceptance_criteria: Optional[list[str]] = None
    open_questions: Optional[list[str]] = None
    patch_version: Optional[int] = None
    test_status: Optional[str] = None
    risk_notes: Optional[str] = None
    decision: Optional[str] = None
    decision_note: Optional[str] = None
    metadata: Optional[dict] = None


class OpsWorkThreadDecisionRequest(BaseModel):
    session_id: str
    decision: str
    note: Optional[str] = None
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


class CalendarEventActionRequest(BaseModel):
    action: str
    run_at: Optional[str] = None
    timezone: Optional[str] = None
    note: Optional[str] = None


class CalendarEventChangeRequest(BaseModel):
    instruction: str
    timezone: Optional[str] = None


class CalendarEventChangeConfirmRequest(BaseModel):
    proposal_id: str
    approve: bool = True


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


class YouTubeIngestRequest(BaseModel):
    video_url: Optional[str] = None
    video_id: Optional[str] = None
    language: str = "en"
    timeout_seconds: int = 120
    max_chars: int = 180_000
    min_chars: int = 160
    request_id: Optional[str] = None


# =============================================================================
# Gateway Singleton
# =============================================================================

_gateway: Optional[InProcessGateway] = None
_sessions: dict[str, GatewaySession] = {}
_session_runtime: dict[str, dict[str, Any]] = {}
_heartbeat_service: Optional[HeartbeatService] = None
_cron_service: Optional[CronService] = None
_ops_service: Optional[OpsService] = None
_hooks_service: Optional[HooksService] = None
_system_events: dict[str, list[dict]] = {}
_system_presence: dict[str, dict] = {}
_system_events_max = int(os.getenv("UA_SYSTEM_EVENTS_MAX", "100"))
_channel_probe_results: dict[str, dict] = {}
_notifications: list[dict] = []
_notifications_max = int(os.getenv("UA_NOTIFICATIONS_MAX", "500"))
_continuity_active_alerts: set[str] = set()
_continuity_metric_events: deque[dict[str, Any]] = deque(
    maxlen=max(1000, int(os.getenv("UA_CONTINUITY_EVENT_MAXLEN", "20000") or 20000))
)
_pending_gated_requests: dict[str, dict] = {}
_session_turn_state: dict[str, dict[str, Any]] = {}
_session_turn_locks: dict[str, asyncio.Lock] = {}
_session_execution_tasks: dict[str, asyncio.Task[Any]] = {}
_calendar_missed_events: dict[str, dict[str, Any]] = {}
_calendar_missed_notifications: set[str] = set()
_calendar_change_proposals: dict[str, dict[str, Any]] = {}
_SYSTEM_CONFIGURATION_AGENT_SESSION_ID = "session_system_configuration_agent"
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
    "ws_send_failures": 0,
    "ws_send_timeouts": 0,
    "ws_stale_evictions": 0,
    "ws_disconnects_total": 0,
    "ws_close_codes": {},
    "ws_close_reasons": {},
    "ws_close_endpoints": {},
}
_scheduling_runtime_started_ts = time.time()
_scheduling_runtime_metrics: dict[str, Any] = {
    "started_at": datetime.now().isoformat(),
    "counters": {
        "calendar_events_requests": 0,
        "calendar_action_requests": 0,
        "calendar_change_request_requests": 0,
        "calendar_change_confirm_requests": 0,
        "heartbeat_last_requests": 0,
        "heartbeat_wake_requests": 0,
        "event_emissions_total": 0,
        "cron_events_total": 0,
        "heartbeat_events_total": 0,
        "event_bus_published": 0,
        "projection_applied": 0,
        "projection_seed_count": 0,
        "projection_seed_jobs": 0,
        "projection_seed_runs": 0,
        "projection_read_hits": 0,
        "push_replay_requests": 0,
        "push_stream_connects": 0,
        "push_stream_disconnects": 0,
        "push_stream_keepalives": 0,
        "push_stream_event_payloads": 0,
    },
    "event_counts": {
        "cron": {},
        "heartbeat": {},
    },
    "projection": {
        "builds": 0,
        "duration_ms_last": 0.0,
        "duration_ms_max": 0.0,
        "duration_ms_total": 0.0,
        "events_total": 0,
        "always_running_total": 0,
        "stasis_total": 0,
        "due_lag_samples": 0,
        "due_lag_seconds_last": 0.0,
        "due_lag_seconds_max": 0.0,
        "due_lag_seconds_total": 0.0,
    },
}
SCHED_PUSH_ENABLED = (
    os.getenv("UA_SCHED_PUSH_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
)
SCHED_EVENT_PROJECTION_ENABLED = (
    os.getenv("UA_SCHED_EVENT_PROJECTION_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
)

SESSION_STATE_IDLE = "idle"
SESSION_STATE_RUNNING = "running"
SESSION_STATE_TERMINAL = "terminal"
TURN_STATUS_RUNNING = "running"
TURN_STATUS_COMPLETED = "completed"
TURN_STATUS_FAILED = "failed"
TURN_STATUS_CANCELLED = "cancelled"
TURN_HISTORY_LIMIT = int(os.getenv("UA_TURN_HISTORY_LIMIT", "200"))
TURN_FINGERPRINT_DEDUPE_WINDOW_SECONDS = int(os.getenv("UA_TURN_FINGERPRINT_DEDUPE_WINDOW_SECONDS", "120"))
TURN_LINEAGE_DIRNAME = "turns"
CONTINUITY_RESUME_SUCCESS_MIN = float(os.getenv("UA_CONTINUITY_RESUME_SUCCESS_MIN", "0.90") or 0.90)
CONTINUITY_ATTACH_SUCCESS_MIN = float(os.getenv("UA_CONTINUITY_ATTACH_SUCCESS_MIN", "0.90") or 0.90)
CONTINUITY_FAILURE_WARN_THRESHOLD = int(os.getenv("UA_CONTINUITY_FAILURE_WARN_THRESHOLD", "3") or 3)
CONTINUITY_WINDOW_SECONDS = max(60, int(os.getenv("UA_CONTINUITY_WINDOW_SECONDS", "900") or 900))
CONTINUITY_RATE_MIN_ATTEMPTS = max(1, int(os.getenv("UA_CONTINUITY_RATE_MIN_ATTEMPTS", "3") or 3))
CONTINUITY_EVENT_RETENTION_SECONDS = max(
    CONTINUITY_WINDOW_SECONDS * 4,
    int(os.getenv("UA_CONTINUITY_EVENT_RETENTION_SECONDS", "3600") or 3600),
)
NOTIFICATION_SNOOZE_MINUTES_DEFAULT = int(os.getenv("UA_NOTIFICATION_SNOOZE_MINUTES_DEFAULT", "30") or 30)
NOTIFICATION_SNOOZE_MINUTES_MAX = int(os.getenv("UA_NOTIFICATION_SNOOZE_MINUTES_MAX", "1440") or 1440)
WS_SEND_TIMEOUT_SECONDS = gateway_ws_send_timeout_seconds()


def _now_iso() -> str:
    return datetime.now().isoformat()


def _increment_metric(name: str, amount: int = 1) -> None:
    current = int(_observability_metrics.get(name, 0) or 0)
    _observability_metrics[name] = current + max(0, int(amount))
    _record_continuity_metric_event(name, amount=amount)
    _sync_continuity_notifications()


def _increment_bucket_metric(name: str, bucket: str, amount: int = 1) -> None:
    target = _observability_metrics.get(name)
    if not isinstance(target, dict):
        target = {}
        _observability_metrics[name] = target
    key = bucket if bucket else "unknown"
    target[key] = int(target.get(key, 0) or 0) + max(0, int(amount))


def _record_ws_close(code: Optional[int], reason: Optional[str], endpoint: str) -> None:
    _increment_metric("ws_disconnects_total", 1)
    code_key = str(int(code)) if isinstance(code, int) else "unknown"
    _increment_bucket_metric("ws_close_codes", code_key)
    reason_text = str(reason or "").strip()
    if not reason_text:
        reason_text = "unknown"
    # Keep cardinality bounded to prevent metrics bloat.
    if len(reason_text) > 120:
        reason_text = reason_text[:120]
    _increment_bucket_metric("ws_close_reasons", reason_text)
    _increment_bucket_metric("ws_close_endpoints", endpoint or "unknown")


def _record_continuity_metric_event(name: str, amount: int = 1, ts: Optional[float] = None) -> None:
    tracked = {
        "resume_attempts",
        "resume_successes",
        "resume_failures",
        "ws_attach_attempts",
        "ws_attach_successes",
        "ws_attach_failures",
    }
    if name not in tracked:
        return
    count = max(0, int(amount))
    if count <= 0:
        return
    now_ts = float(ts if ts is not None else time.time())
    # Keep event fanout bounded for large increments while preserving rough shape.
    capped = min(count, 100)
    for _ in range(capped):
        _continuity_metric_events.append({"name": name, "ts": now_ts})


def _continuity_window_counts(now_ts: Optional[float] = None) -> dict[str, int]:
    ts_now = float(now_ts if now_ts is not None else time.time())
    retention_start = ts_now - float(CONTINUITY_EVENT_RETENTION_SECONDS)
    while _continuity_metric_events and float(_continuity_metric_events[0].get("ts", 0.0) or 0.0) < retention_start:
        _continuity_metric_events.popleft()

    window_start = ts_now - float(CONTINUITY_WINDOW_SECONDS)
    counts = {
        "resume_attempts": 0,
        "resume_successes": 0,
        "resume_failures": 0,
        "ws_attach_attempts": 0,
        "ws_attach_successes": 0,
        "ws_attach_failures": 0,
    }
    for item in _continuity_metric_events:
        item_ts = float(item.get("ts", 0.0) or 0.0)
        if item_ts < window_start:
            continue
        name = str(item.get("name") or "")
        if name in counts:
            counts[name] += 1
    return counts


def _scheduling_counter_inc(name: str, amount: int = 1) -> None:
    counters = _scheduling_runtime_metrics.setdefault("counters", {})
    current = int(counters.get(name, 0) or 0)
    counters[name] = current + max(0, int(amount))


class SchedulingEventBus:
    def __init__(self, max_events: int = 5000):
        self.max_events = max(100, int(max_events))
        self._events: deque[dict[str, Any]] = deque(maxlen=self.max_events)
        self._seq: int = 0
        self._subscribers: list[Any] = []
        self._condition = asyncio.Condition()

    def subscribe(self, callback: Any) -> None:
        if callback in self._subscribers:
            return
        self._subscribers.append(callback)

    def publish(self, source: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._seq += 1
        envelope = {
            "seq": self._seq,
            "source": str(source or "unknown"),
            "type": str(event_type or "event"),
            "timestamp": datetime.now().isoformat(),
            "data": payload if isinstance(payload, dict) else {"value": payload},
        }
        self._events.append(envelope)
        for callback in list(self._subscribers):
            try:
                callback(envelope)
            except Exception as exc:
                logger.warning("Scheduling event subscriber failed: %s", exc)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._notify_waiters())
        except RuntimeError:
            pass
        return envelope

    def snapshot(self, since_seq: int = 0, limit: int = 5000) -> list[dict[str, Any]]:
        low_water = max(0, int(since_seq))
        max_items = max(1, min(int(limit), self.max_events))
        items = [event for event in list(self._events) if int(event.get("seq", 0)) > low_water]
        return items[-max_items:]

    async def _notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    async def wait_for_events(self, since_seq: int = 0, timeout_seconds: float = 20.0, limit: int = 5000) -> list[dict[str, Any]]:
        current = self.snapshot(since_seq=since_seq, limit=limit)
        if current:
            return current
        wait_for = max(1.0, float(timeout_seconds))
        try:
            async with self._condition:
                await asyncio.wait_for(self._condition.wait(), timeout=wait_for)
        except asyncio.TimeoutError:
            return []
        return self.snapshot(since_seq=since_seq, limit=limit)

    def info(self) -> dict[str, Any]:
        return {
            "seq": self._seq,
            "max_events": self.max_events,
            "buffered_events": len(self._events),
            "subscriber_count": len(self._subscribers),
        }


class SchedulingProjectionState:
    def __init__(self, enabled: bool = False):
        self.enabled = bool(enabled)
        self.version: int = 0
        self.last_event_seq: int = 0
        self.last_updated_at: Optional[str] = None
        self.seeded: bool = False
        self.cron_jobs: dict[str, Any] = {}
        self.cron_runs_by_job: dict[str, list[dict[str, Any]]] = {}
        self.cron_run_ids: set[str] = set()
        self.heartbeat_last_by_session: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        self.version = 0
        self.last_event_seq = 0
        self.last_updated_at = None
        self.seeded = False
        self.cron_jobs = {}
        self.cron_runs_by_job = {}
        self.cron_run_ids = set()
        self.heartbeat_last_by_session = {}

    def _mark_changed(self, seq: Optional[int] = None) -> None:
        self.version += 1
        if seq is not None:
            self.last_event_seq = max(self.last_event_seq, int(seq))
        self.last_updated_at = datetime.now().isoformat()

    def _upsert_cron_job(self, job: dict[str, Any]) -> bool:
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            return False
        current = self.cron_jobs.get(job_id)
        if current is not None and getattr(current, "__dict__", {}) == job:
            return False
        self.cron_jobs[job_id] = SimpleNamespace(**job)
        return True

    def _delete_cron_job(self, job_id: str) -> bool:
        if job_id in self.cron_jobs:
            self.cron_jobs.pop(job_id, None)
            return True
        return False

    def _append_cron_run(self, run: dict[str, Any]) -> bool:
        run_id = str(run.get("run_id") or "").strip()
        if run_id and run_id in self.cron_run_ids:
            return False
        job_id = str(run.get("job_id") or "").strip()
        if not job_id:
            return False
        if run_id:
            self.cron_run_ids.add(run_id)
        bucket = self.cron_runs_by_job.setdefault(job_id, [])
        bucket.append(dict(run))
        # Keep bounded per job for memory safety.
        if len(bucket) > 5000:
            overflow = bucket[:-5000]
            bucket[:] = bucket[-5000:]
            for item in overflow:
                rid = str(item.get("run_id") or "").strip()
                if rid:
                    self.cron_run_ids.discard(rid)
        return True

    def seed_from_runtime(self) -> None:
        if not self.enabled or self.seeded:
            return
        changed = False
        if _cron_service:
            for job in _cron_service.list_jobs():
                changed = self._upsert_cron_job(job.to_dict()) or changed
            for run in _cron_service.list_runs(limit=5000):
                if isinstance(run, dict):
                    changed = self._append_cron_run(run) or changed
        for session_id in list(_sessions.keys()):
            if session_id not in self.heartbeat_last_by_session:
                self.heartbeat_last_by_session[session_id] = {
                    "type": "heartbeat_session_seen",
                    "timestamp": datetime.now().isoformat(),
                }
                changed = True
        self.seeded = True
        if changed:
            self._mark_changed()
        _scheduling_counter_inc("projection_seed_count")
        _scheduling_counter_inc("projection_seed_jobs", len(self.cron_jobs))
        _scheduling_counter_inc("projection_seed_runs", sum(len(v) for v in self.cron_runs_by_job.values()))

    def apply_event(self, envelope: dict[str, Any]) -> None:
        if not self.enabled or not isinstance(envelope, dict):
            return
        source = str(envelope.get("source") or "").strip().lower()
        event_type = str(envelope.get("type") or "").strip().lower()
        payload = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
        seq = int(envelope.get("seq") or 0)
        changed = False

        if source == "cron":
            if event_type in {"cron_job_created", "cron_job_updated"}:
                job_data = payload.get("job") if isinstance(payload.get("job"), dict) else None
                if job_data:
                    changed = self._upsert_cron_job(job_data)
            elif event_type == "cron_job_deleted":
                changed = self._delete_cron_job(str(payload.get("job_id") or ""))
            elif event_type in {"cron_run_started", "cron_run_completed"}:
                run_data = payload.get("run") if isinstance(payload.get("run"), dict) else None
                if run_data:
                    changed = self._append_cron_run(run_data)
        elif source == "heartbeat":
            session_id = str(payload.get("session_id") or "").strip()
            if session_id:
                previous = self.heartbeat_last_by_session.get(session_id)
                candidate = {
                    "type": event_type or "heartbeat_event",
                    "timestamp": str(envelope.get("timestamp") or datetime.now().isoformat()),
                }
                if previous != candidate:
                    self.heartbeat_last_by_session[session_id] = candidate
                    changed = True

        if changed:
            self._mark_changed(seq=seq)
            _scheduling_counter_inc("projection_applied")

    def list_cron_jobs(self) -> list[Any]:
        return list(self.cron_jobs.values())

    def list_cron_runs(self, limit: int = 2000) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        for rows in self.cron_runs_by_job.values():
            merged.extend(rows)
        merged.sort(key=lambda row: float(row.get("started_at") or row.get("scheduled_at") or 0.0), reverse=True)
        return merged[: max(1, int(limit))]

    def info(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "seeded": self.seeded,
            "version": self.version,
            "last_event_seq": self.last_event_seq,
            "last_updated_at": self.last_updated_at,
            "cron_jobs": len(self.cron_jobs),
            "cron_runs": sum(len(rows) for rows in self.cron_runs_by_job.values()),
            "heartbeat_sessions": len(self.heartbeat_last_by_session),
        }


_scheduling_event_bus = SchedulingEventBus(
    max_events=int(os.getenv("UA_SCHED_EVENT_BUS_MAX", "5000") or 5000)
)
_scheduling_projection = SchedulingProjectionState(enabled=SCHED_EVENT_PROJECTION_ENABLED)
_scheduling_event_bus.subscribe(_scheduling_projection.apply_event)


def _scheduling_record_event(source: str, event_type: Optional[str]) -> None:
    source_norm = (source or "unknown").strip().lower()
    event_norm = (event_type or f"{source_norm}_event").strip().lower() or f"{source_norm}_event"
    _scheduling_counter_inc("event_emissions_total")
    if source_norm == "cron":
        _scheduling_counter_inc("cron_events_total")
    elif source_norm == "heartbeat":
        _scheduling_counter_inc("heartbeat_events_total")
    bucket = _scheduling_runtime_metrics.setdefault("event_counts", {}).setdefault(source_norm, {})
    bucket[event_norm] = int(bucket.get(event_norm, 0) or 0) + 1


def _scheduling_record_projection_sample(
    *,
    duration_ms: float,
    events: list[dict[str, Any]],
    always_running: list[dict[str, Any]],
    stasis_count: int,
) -> None:
    projection = _scheduling_runtime_metrics.setdefault("projection", {})
    projection["builds"] = int(projection.get("builds", 0) or 0) + 1
    projection["duration_ms_last"] = round(duration_ms, 3)
    projection["duration_ms_total"] = float(projection.get("duration_ms_total", 0.0) or 0.0) + max(0.0, duration_ms)
    projection["duration_ms_max"] = max(float(projection.get("duration_ms_max", 0.0) or 0.0), max(0.0, duration_ms))
    projection["events_total"] = int(projection.get("events_total", 0) or 0) + max(0, len(events))
    projection["always_running_total"] = int(projection.get("always_running_total", 0) or 0) + max(0, len(always_running))
    projection["stasis_total"] = int(projection.get("stasis_total", 0) or 0) + max(0, int(stasis_count))

    now_ts = time.time()
    due_lags: list[float] = []
    for item in events:
        status = str(item.get("status") or "").strip().lower()
        if status not in {"scheduled", "running"}:
            continue
        scheduled_at = float(item.get("scheduled_at_epoch") or 0.0)
        if scheduled_at <= 0 or scheduled_at > now_ts:
            continue
        due_lags.append(max(0.0, now_ts - scheduled_at))

    if due_lags:
        lag_total = float(sum(due_lags))
        lag_last = float(max(due_lags))
        projection["due_lag_samples"] = int(projection.get("due_lag_samples", 0) or 0) + len(due_lags)
        projection["due_lag_seconds_total"] = float(projection.get("due_lag_seconds_total", 0.0) or 0.0) + lag_total
        projection["due_lag_seconds_last"] = round(lag_last, 3)
        projection["due_lag_seconds_max"] = max(float(projection.get("due_lag_seconds_max", 0.0) or 0.0), lag_last)
    else:
        projection["due_lag_seconds_last"] = 0.0


def _scheduling_runtime_metrics_snapshot() -> dict[str, Any]:
    data = json.loads(json.dumps(_scheduling_runtime_metrics))
    projection = data.setdefault("projection", {})
    builds = int(projection.get("builds", 0) or 0)
    duration_total = float(projection.get("duration_ms_total", 0.0) or 0.0)
    lag_samples = int(projection.get("due_lag_samples", 0) or 0)
    lag_total = float(projection.get("due_lag_seconds_total", 0.0) or 0.0)
    projection["duration_ms_avg"] = round(duration_total / builds, 3) if builds > 0 else 0.0
    projection["due_lag_seconds_avg"] = round(lag_total / lag_samples, 3) if lag_samples > 0 else 0.0
    data["uptime_seconds"] = round(max(0.0, time.time() - _scheduling_runtime_started_ts), 3)
    data["event_bus"] = _scheduling_event_bus.info()
    data["projection_state"] = _scheduling_projection.info()
    return data


def _continuity_alerts_snapshot() -> dict[str, Any]:
    now_ts = time.time()
    window_counts = _continuity_window_counts(now_ts=now_ts)
    window_start_ts = now_ts - float(CONTINUITY_WINDOW_SECONDS)
    resume_attempts = int(window_counts.get("resume_attempts", 0) or 0)
    resume_successes = int(window_counts.get("resume_successes", 0) or 0)
    ws_attach_attempts = int(window_counts.get("ws_attach_attempts", 0) or 0)
    ws_attach_successes = int(window_counts.get("ws_attach_successes", 0) or 0)
    resume_rate = round(resume_successes / resume_attempts, 4) if resume_attempts > 0 else None
    attach_rate = round(ws_attach_successes / ws_attach_attempts, 4) if ws_attach_attempts > 0 else None
    resume_failures = int(window_counts.get("resume_failures", 0) or 0)
    attach_failures = int(window_counts.get("ws_attach_failures", 0) or 0)
    rate_checks_enabled = (
        resume_attempts >= CONTINUITY_RATE_MIN_ATTEMPTS
        or ws_attach_attempts >= CONTINUITY_RATE_MIN_ATTEMPTS
    )
    alerts: list[dict[str, Any]] = []
    if (
        rate_checks_enabled
        and resume_rate is not None
        and resume_attempts >= CONTINUITY_RATE_MIN_ATTEMPTS
        and resume_rate < CONTINUITY_RESUME_SUCCESS_MIN
    ):
        alerts.append(
            {
                "code": "resume_success_rate_low",
                "severity": "warning",
                "message": "Resume success rate below threshold.",
                "actual": resume_rate,
                "threshold": CONTINUITY_RESUME_SUCCESS_MIN,
                "scope": "transport",
            }
        )
    if (
        rate_checks_enabled
        and attach_rate is not None
        and ws_attach_attempts >= CONTINUITY_RATE_MIN_ATTEMPTS
        and attach_rate < CONTINUITY_ATTACH_SUCCESS_MIN
    ):
        alerts.append(
            {
                "code": "attach_success_rate_low",
                "severity": "warning",
                "message": "Attach success rate below threshold.",
                "actual": attach_rate,
                "threshold": CONTINUITY_ATTACH_SUCCESS_MIN,
                "scope": "transport",
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
                "scope": "transport",
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
                "scope": "transport",
            }
        )

    runtime_faults = 0
    for runtime in _session_runtime.values():
        if not isinstance(runtime, dict):
            continue
        reason = str(runtime.get("terminal_reason") or "").strip().lower()
        if reason in {"error", "failed", "crashed", "exception"}:
            runtime_faults += 1

    transport_status = "degraded" if alerts else "ok"
    runtime_status = "degraded" if runtime_faults > 0 else "ok"
    return {
        "resume_success_rate": resume_rate,
        "attach_success_rate": attach_rate,
        "transport_status": transport_status,
        "runtime_status": runtime_status,
        "window_seconds": CONTINUITY_WINDOW_SECONDS,
        "window_started_at": datetime.fromtimestamp(window_start_ts, timezone.utc).isoformat(),
        "window_event_count": (
            resume_attempts
            + ws_attach_attempts
            + resume_failures
            + attach_failures
        ),
        "window": {
            "resume_attempts": resume_attempts,
            "resume_successes": resume_successes,
            "resume_failures": resume_failures,
            "resume_success_rate": resume_rate,
            "ws_attach_attempts": ws_attach_attempts,
            "ws_attach_successes": ws_attach_successes,
            "ws_attach_failures": attach_failures,
            "attach_success_rate": attach_rate,
        },
        "runtime_faults": runtime_faults,
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
    execution_runtime: dict[str, Any] = {}
    gateway = _gateway
    if gateway is not None and hasattr(gateway, "execution_runtime_snapshot"):
        try:
            execution_runtime = gateway.execution_runtime_snapshot()
        except Exception:
            execution_runtime = {}
    return {
        **_observability_metrics,
        "duplicate_turn_prevention_count": duplicate_prevented,
        "resume_success_rate": continuity.get("resume_success_rate"),
        "attach_success_rate": continuity.get("attach_success_rate"),
        "transport_status": continuity.get("transport_status"),
        "runtime_status": continuity.get("runtime_status"),
        "window_seconds": continuity.get("window_seconds"),
        "window_started_at": continuity.get("window_started_at"),
        "window_event_count": continuity.get("window_event_count"),
        "window": continuity.get("window"),
        "runtime_faults": continuity.get("runtime_faults"),
        "alerts": continuity.get("alerts"),
        "execution_runtime": execution_runtime,
    }


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _vp_mission_duration_seconds(started_at: Any, completed_at: Any) -> Optional[float]:
    started = _parse_iso_datetime(started_at)
    completed = _parse_iso_datetime(completed_at)
    if not started or not completed:
        return None
    return max(0.0, (completed - started).total_seconds())


def _parse_json_text(raw: Any) -> Any:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _vp_session_to_dict(row: Any) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    payload = {k: row[k] for k in row.keys()} if hasattr(row, "keys") else dict(row)
    metadata = _parse_json_text(payload.get("metadata_json"))
    if isinstance(metadata, dict):
        payload["metadata"] = metadata
    return payload


def _vp_recovery_snapshot(
    session_row: Any,
    parsed_session_events: list[dict[str, Any]],
) -> dict[str, Any]:
    recovery_attempts = 0
    recovery_successes = 0
    unresolved_recoveries = 0

    for item in parsed_session_events:
        event_type = str(item.get("event_type") or "")
        if event_type == "vp.session.degraded":
            recovery_attempts += 1
            unresolved_recoveries += 1
            continue
        if event_type == "vp.session.resumed" and unresolved_recoveries > 0:
            recovery_successes += 1
            unresolved_recoveries -= 1

    session_status = ""
    if session_row is not None:
        session_status = str(session_row["status"] or "")

    currently_orphaned = session_status in {"degraded", "recovering"}
    if currently_orphaned and unresolved_recoveries == 0:
        unresolved_recoveries = 1

    recovery_success_rate = (
        recovery_successes / recovery_attempts if recovery_attempts > 0 else None
    )
    orphan_rate = (
        unresolved_recoveries / recovery_attempts
        if recovery_attempts > 0
        else (1.0 if currently_orphaned else 0.0)
    )
    return {
        "attempts": recovery_attempts,
        "successes": recovery_successes,
        "success_rate": recovery_success_rate,
        "currently_orphaned": currently_orphaned,
        "orphan_signals": unresolved_recoveries,
        "orphan_rate": orphan_rate,
    }


def _vp_metrics_snapshot(
    vp_id: str,
    mission_limit: int,
    event_limit: int,
) -> dict[str, Any]:
    gateway = get_gateway()
    conn = getattr(gateway, "_runtime_db_conn", None)
    if conn is None:
        raise HTTPException(status_code=503, detail="Runtime DB not initialized")

    session_row = get_vp_session(conn, vp_id)
    missions = list_vp_missions(conn, vp_id=vp_id, limit=mission_limit)
    events = list_vp_events(conn, vp_id=vp_id, limit=event_limit)
    session_events = list_vp_session_events(conn, vp_id=vp_id, limit=event_limit)

    mission_counts: dict[str, int] = {}
    mission_ids: set[str] = set()
    mission_rows: list[dict[str, Any]] = []
    duration_samples: list[float] = []
    for row in missions:
        status = str(row["status"] or "unknown")
        mission_counts[status] = mission_counts.get(status, 0) + 1
        mission_id = str(row["mission_id"] or "")
        if mission_id:
            mission_ids.add(mission_id)

        duration_seconds = _vp_mission_duration_seconds(row["started_at"], row["completed_at"])
        if duration_seconds is not None:
            duration_samples.append(duration_seconds)

        mission_rows.append(
            {
                "mission_id": mission_id,
                "status": status,
                "objective": row["objective"],
                "run_id": row["run_id"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "updated_at": row["updated_at"],
                "result_ref": row["result_ref"],
                "duration_seconds": duration_seconds,
            }
        )

    event_counts: dict[str, int] = {}
    fallback_mission_ids: set[str] = set()
    parsed_events: list[dict[str, Any]] = []
    for row in events:
        event_type = str(row["event_type"] or "unknown")
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
        mission_id = str(row["mission_id"] or "")
        if event_type == "vp.mission.fallback" and mission_id:
            fallback_mission_ids.add(mission_id)

        parsed_events.append(
            {
                "event_id": row["event_id"],
                "mission_id": row["mission_id"],
                "vp_id": row["vp_id"],
                "event_type": event_type,
                "payload": _parse_json_text(row["payload_json"]),
                "created_at": row["created_at"],
            }
        )

    session_event_counts: dict[str, int] = {}
    parsed_session_events: list[dict[str, Any]] = []
    for row in session_events:
        event_type = str(row["event_type"] or "unknown")
        session_event_counts[event_type] = session_event_counts.get(event_type, 0) + 1
        parsed_session_events.append(
            {
                "event_id": row["event_id"],
                "vp_id": row["vp_id"],
                "event_type": event_type,
                "payload": _parse_json_text(row["payload_json"]),
                "created_at": row["created_at"],
            }
        )

    for mission in mission_rows:
        mission["fallback_seen"] = mission["mission_id"] in fallback_mission_ids

    duration_stats: dict[str, Any] = {
        "count": 0,
        "avg_seconds": None,
        "p50_seconds": None,
        "p95_seconds": None,
        "max_seconds": None,
    }
    if duration_samples:
        sorted_durations = sorted(duration_samples)
        count = len(sorted_durations)
        p50_index = int(round((count - 1) * 0.50))
        p95_index = int(round((count - 1) * 0.95))
        duration_stats = {
            "count": count,
            "avg_seconds": sum(sorted_durations) / count,
            "p50_seconds": sorted_durations[p50_index],
            "p95_seconds": sorted_durations[p95_index],
            "max_seconds": sorted_durations[-1],
        }

    fallback_mission_count = len(mission_ids.intersection(fallback_mission_ids))
    fallback_rate = (fallback_mission_count / len(mission_ids)) if mission_ids else 0.0
    recovery = _vp_recovery_snapshot(session_row, parsed_session_events)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vp_id": vp_id,
        "session": _vp_session_to_dict(session_row),
        "mission_counts": mission_counts,
        "event_counts": event_counts,
        "session_event_counts": session_event_counts,
        "fallback": {
            "missions_with_fallback": fallback_mission_count,
            "missions_considered": len(mission_ids),
            "rate": fallback_rate,
        },
        "latency_seconds": duration_stats,
        "recovery": {
            "attempts": recovery["attempts"],
            "successes": recovery["successes"],
            "success_rate": recovery["success_rate"],
        },
        "session_health": {
            "currently_orphaned": recovery["currently_orphaned"],
            "orphan_signals": recovery["orphan_signals"],
            "orphan_rate": recovery["orphan_rate"],
        },
        "recent_missions": mission_rows,
        "recent_events": parsed_events,
        "recent_session_events": parsed_session_events,
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
            "active_foreground_runs": 0,
            "last_event_type": None,
            "terminal_reason": None,
            "last_run_source": None,
            "last_run_started_at": None,
            "last_run_finished_at": None,
            "last_foreground_run_started_at": None,
            "last_foreground_run_finished_at": None,
        }
        _session_runtime[session_id] = state
    return state


def _normalize_run_source(value: Any) -> str:
    source = str(value or "user").strip().lower()
    return source or "user"


def _workspace_dir_for_session(session_id: str) -> Optional[Path]:
    session = _sessions.get(session_id)
    if not session:
        try:
            gateway = get_gateway()
            gateway_sessions = getattr(gateway, "_sessions", {})
            if isinstance(gateway_sessions, dict):
                candidate = gateway_sessions.get(session_id)
                if candidate is not None:
                    session = candidate
        except Exception:
            session = None
    if not session:
        return None
    workspace = Path(str(session.workspace_dir or "")).expanduser()
    if not str(workspace):
        return None
    return workspace


def _run_log_size(workspace_dir: Optional[Path]) -> int:
    if workspace_dir is None:
        return 0
    log_path = workspace_dir / "run.log"
    try:
        if not log_path.exists():
            return 0
        return int(log_path.stat().st_size)
    except Exception:
        return 0


def _turn_lineage_path(session_id: str, turn_id: str) -> Optional[Path]:
    workspace = _workspace_dir_for_session(session_id)
    if workspace is None:
        return None
    return workspace / TURN_LINEAGE_DIRNAME / f"{turn_id}.jsonl"


def _append_turn_lineage_event(session_id: str, turn_id: str, payload: dict[str, Any]) -> None:
    lineage_path = _turn_lineage_path(session_id, turn_id)
    if lineage_path is None:
        return
    try:
        lineage_path.parent.mkdir(parents=True, exist_ok=True)
        with lineage_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")
    except Exception as exc:
        logger.warning(
            "Failed to append turn lineage event (session=%s, turn=%s): %s",
            session_id,
            turn_id,
            exc,
        )


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
        "active_foreground_runs": int(runtime.get("active_foreground_runs", 0)),
        "last_event_type": runtime.get("last_event_type"),
        "terminal_reason": runtime.get("terminal_reason"),
        "last_run_source": runtime.get("last_run_source"),
        "last_run_started_at": runtime.get("last_run_started_at"),
        "last_run_finished_at": runtime.get("last_run_finished_at"),
        "last_foreground_run_started_at": runtime.get("last_foreground_run_started_at"),
        "last_foreground_run_finished_at": runtime.get("last_foreground_run_finished_at"),
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


def _register_execution_task(session_id: str, task: asyncio.Task[Any]) -> None:
    _session_execution_tasks[session_id] = task

    def _cleanup(done_task: asyncio.Task[Any]) -> None:
        current = _session_execution_tasks.get(session_id)
        if current is done_task:
            _session_execution_tasks.pop(session_id, None)

    task.add_done_callback(_cleanup)


async def _cancel_execution_task(session_id: str, timeout_seconds: float = 5.0) -> bool:
    task = _session_execution_tasks.get(session_id)
    if task is None or task.done():
        return False

    task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=max(0.1, float(timeout_seconds)))
    except asyncio.CancelledError:
        return True
    except asyncio.TimeoutError:
        logger.warning("Timed out waiting for execution task cancellation (session=%s)", session_id)
        return False
    except Exception as exc:
        logger.warning("Execution task cancellation raised (session=%s): %s", session_id, exc)
        return False
    return task.cancelled()


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
    run_source = _normalize_run_source(metadata.get("source") if isinstance(metadata, dict) else None)
    workspace = _workspace_dir_for_session(session_id)
    run_log_offset_start = _run_log_size(workspace)

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
        "run_source": run_source,
        "run_log_offset_start": run_log_offset_start,
        "run_log_offset_end": None,
        "completion": None,
    }
    turns[turn_id] = record
    state["active_turn_id"] = turn_id
    state["last_turn_id"] = turn_id
    _trim_turn_history(state)
    _append_turn_lineage_event(
        session_id,
        turn_id,
        {
            "event": "turn_started",
            "timestamp": _now_iso(),
            "session_id": session_id,
            "turn_id": turn_id,
            "client_turn_id": client_turn_id,
            "run_source": run_source,
            "run_log_offset_start": run_log_offset_start,
            "fingerprint": fingerprint,
            "request_preview": str(user_input or "")[:400],
        },
    )
    return {"decision": "accepted", "turn_id": turn_id, "record": record}


def _finalize_turn(
    session_id: str,
    turn_id: str,
    status: str,
    error_message: Optional[str] = None,
    completion: Optional[dict[str, Any]] = None,
) -> None:
    state = _session_turn_snapshot(session_id)
    turns = state.get("turns", {})
    if not isinstance(turns, dict):
        return
    record = turns.get(turn_id)
    if not isinstance(record, dict):
        return
    workspace = _workspace_dir_for_session(session_id)
    record["status"] = status
    record["finished_at"] = _now_iso()
    record["run_log_offset_end"] = _run_log_size(workspace)
    if completion is not None:
        record["completion"] = completion
    if error_message:
        record["error_message"] = error_message
    if state.get("active_turn_id") == turn_id:
        state["active_turn_id"] = None
    _append_turn_lineage_event(
        session_id,
        turn_id,
        {
            "event": "turn_finalized",
            "timestamp": _now_iso(),
            "session_id": session_id,
            "turn_id": turn_id,
            "status": status,
            "error_message": error_message,
            "run_log_offset_start": int(record.get("run_log_offset_start") or 0),
            "run_log_offset_end": int(record.get("run_log_offset_end") or 0),
            "run_source": record.get("run_source"),
            "completion": completion,
        },
    )


async def _admit_hook_turn(session_id: str, request: GatewayRequest) -> dict[str, Any]:
    if session_id not in _sessions:
        try:
            gateway = get_gateway()
            gateway_sessions = getattr(gateway, "_sessions", {})
            if isinstance(gateway_sessions, dict):
                candidate = gateway_sessions.get(session_id)
                if candidate is not None:
                    store_session(candidate)
                    if _heartbeat_service:
                        _heartbeat_service.register_session(candidate)
        except Exception as exc:
            logger.warning("Failed to sync hook session into gateway state (session=%s): %s", session_id, exc)
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    client_turn_id = _normalize_client_turn_id(
        metadata.get("hook_request_id") or metadata.get("hook_event_id")
    )
    async with _session_turn_lock(session_id):
        return _admit_turn(
            session_id=session_id,
            connection_id="hook_dispatch",
            user_input=str(request.user_input or ""),
            force_complex=bool(request.force_complex),
            metadata=metadata,
            client_turn_id=client_turn_id,
        )


async def _finalize_hook_turn(
    session_id: str,
    turn_id: str,
    status: str,
    error_message: Optional[str],
    completion: Optional[dict[str, Any]],
) -> None:
    async with _session_turn_lock(session_id):
        _finalize_turn(
            session_id=session_id,
            turn_id=turn_id,
            status=status,
            error_message=error_message,
            completion=completion,
        )


def _set_session_connections(session_id: str, count: int) -> None:
    runtime = _session_runtime_snapshot(session_id)
    runtime["active_connections"] = max(0, int(count))
    runtime["last_activity_at"] = _now_iso()
    runtime["lifecycle_state"] = _runtime_status_from_counters(runtime)
    _sync_runtime_metadata(session_id)


def _increment_session_active_runs(session_id: str, run_source: str = "user") -> None:
    runtime = _session_runtime_snapshot(session_id)
    source = _normalize_run_source(run_source)
    now_iso = _now_iso()
    runtime["active_runs"] = int(runtime.get("active_runs", 0)) + 1
    if source != "heartbeat":
        runtime["active_foreground_runs"] = int(runtime.get("active_foreground_runs", 0)) + 1
        runtime["last_foreground_run_started_at"] = now_iso
    runtime["lifecycle_state"] = SESSION_STATE_RUNNING
    runtime["terminal_reason"] = None
    runtime["last_activity_at"] = now_iso
    runtime["last_run_source"] = source
    runtime["last_run_started_at"] = now_iso
    _sync_runtime_metadata(session_id)


def _decrement_session_active_runs(session_id: str, run_source: str = "user") -> None:
    runtime = _session_runtime_snapshot(session_id)
    source = _normalize_run_source(run_source)
    now_iso = _now_iso()
    runtime["active_runs"] = max(0, int(runtime.get("active_runs", 0)) - 1)
    if source != "heartbeat":
        runtime["active_foreground_runs"] = max(0, int(runtime.get("active_foreground_runs", 0)) - 1)
        runtime["last_foreground_run_finished_at"] = now_iso
    runtime["lifecycle_state"] = _runtime_status_from_counters(runtime)
    runtime["last_activity_at"] = now_iso
    runtime["last_run_source"] = source
    runtime["last_run_finished_at"] = now_iso
    _sync_runtime_metadata(session_id)


def _mark_session_terminal(session_id: str, reason: str) -> None:
    runtime = _session_runtime_snapshot(session_id)
    runtime["active_runs"] = 0
    runtime["active_foreground_runs"] = 0
    runtime["active_connections"] = 0
    runtime["lifecycle_state"] = SESSION_STATE_TERMINAL
    runtime["terminal_reason"] = reason
    runtime["last_activity_at"] = _now_iso()
    _sync_runtime_metadata(session_id)


def _emit_cron_event(payload: dict) -> None:
    event_type = str(payload.get("type") or "cron_event")
    _scheduling_record_event("cron", event_type)
    _scheduling_event_bus.publish("cron", event_type, payload)
    _scheduling_counter_inc("event_bus_published")
    run_data = payload.get("run") if isinstance(payload.get("run"), dict) else None
    if event_type == "cron_run_completed" and run_data:
        run_status = str(run_data.get("status") or "unknown").strip().lower() or "unknown"
        job_id = str(run_data.get("job_id") or "").strip()
        run_id = str(run_data.get("run_id") or "").strip()
        job = _cron_service.get_job(job_id) if _cron_service and job_id else None
        command = str(getattr(job, "command", "") or "").strip()
        if not command:
            command = f"job {job_id}" if job_id else "chron job"
        if len(command) > 120:
            command = f"{command[:117]}..."

        session_id = ""
        if job:
            metadata = getattr(job, "metadata", {}) or {}
            if isinstance(metadata, dict):
                session_id = str(
                    metadata.get("session_id")
                    or metadata.get("target_session_id")
                    or metadata.get("target_session")
                    or ""
                ).strip()

        if run_status == "success":
            title = "Chron Run Succeeded"
            severity = "info"
            kind = "cron_run_success"
            message = f"{command}"
        else:
            title = "Chron Run Failed"
            severity = "error"
            kind = "cron_run_failed"
            error_text = str(run_data.get("error") or "").strip()
            message = f"{command}"
            if error_text:
                message = f"{message} | {error_text[:240]}"

        _add_notification(
            kind=kind,
            title=title,
            message=message,
            session_id=session_id or None,
            severity=severity,
            metadata={
                "job_id": job_id,
                "run_id": run_id,
                "status": run_status,
                "scheduled_at": run_data.get("scheduled_at"),
                "started_at": run_data.get("started_at"),
                "finished_at": run_data.get("finished_at"),
                "error": run_data.get("error"),
                "source": "cron",
            },
        )
    event = {
        "type": event_type,
        "data": payload,
        "timestamp": datetime.now().isoformat(),
    }
    for session_id in list(manager.session_connections.keys()):
        asyncio.create_task(manager.broadcast(session_id, event))


def _emit_heartbeat_event(payload: dict) -> None:
    event_type = str(payload.get("type") or "heartbeat_event")
    _scheduling_record_event("heartbeat", event_type)
    _scheduling_event_bus.publish("heartbeat", event_type, payload)
    _scheduling_counter_inc("event_bus_published")


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


def _calendar_timezone_or_default(value: Optional[str]) -> str:
    candidate = (value or os.getenv("USER_TIMEZONE") or "America/Chicago").strip()
    if not candidate:
        candidate = "America/Chicago"
    try:
        ZoneInfo(candidate)
        return candidate
    except Exception:
        return "America/Chicago"


def _calendar_parse_ts(value: Optional[str], timezone_name: str) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        pass
    try:
        if text.endswith("Z"):
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed.astimezone(timezone.utc).timestamp()


def _calendar_local_iso(ts: float, timezone_name: str) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(ZoneInfo(timezone_name)).isoformat()


def _calendar_event_id(source: str, source_ref: str, scheduled_at: float) -> str:
    return f"{source}|{source_ref}|{int(scheduled_at)}"


def _calendar_parse_event_id(event_id: str) -> tuple[str, str, int]:
    parts = str(event_id).split("|", 2)
    if len(parts) != 3:
        raise HTTPException(status_code=400, detail="Invalid calendar event id")
    source, source_ref, scheduled_part = parts
    if source not in {"cron", "heartbeat"}:
        raise HTTPException(status_code=400, detail="Unsupported calendar source")
    try:
        scheduled_at = int(scheduled_part)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid calendar event timestamp") from exc
    return source, source_ref, scheduled_at


def _calendar_normalize_window(
    *,
    start: Optional[str],
    end: Optional[str],
    view: str,
    timezone_name: str,
) -> tuple[float, float]:
    view_mode = (view or "week").strip().lower()
    if view_mode not in {"day", "week"}:
        view_mode = "week"
    now_local = datetime.now(ZoneInfo(timezone_name))
    if view_mode == "day":
        default_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        default_end_local = default_start_local + timedelta(days=1)
    else:
        # Sunday-based week start
        days_since_sunday = (now_local.weekday() + 1) % 7
        default_start_local = (now_local - timedelta(days=days_since_sunday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        default_end_local = default_start_local + timedelta(days=7)

    start_ts = _calendar_parse_ts(start, timezone_name)
    end_ts = _calendar_parse_ts(end, timezone_name)
    if start_ts is None:
        start_ts = default_start_local.astimezone(timezone.utc).timestamp()
    if end_ts is None:
        end_ts = default_end_local.astimezone(timezone.utc).timestamp()
    if end_ts <= start_ts:
        end_ts = start_ts + (86400 if view_mode == "day" else 7 * 86400)

    now_ts = time.time()
    # Rolling retention: last 30 days visible. Keep a short future horizon.
    min_start = now_ts - (30 * 86400)
    max_end = now_ts + (14 * 86400)
    start_ts = max(start_ts, min_start)
    end_ts = min(end_ts, max_end)
    if end_ts <= start_ts:
        end_ts = min(start_ts + 86400, max_end)
    return start_ts, end_ts


def _calendar_read_heartbeat_overrides(workspace_dir: str) -> dict[str, Any]:
    workspace = Path(workspace_dir)
    for name in ("HEARTBEAT.json", "heartbeat.json", ".heartbeat.json"):
        path = workspace / name
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if isinstance(payload, dict):
            return payload
        return {}
    return {}


def _calendar_write_heartbeat_overrides(session_id: str, payload: dict[str, Any]) -> str:
    workspace = WORKSPACES_DIR / session_id
    if not workspace.exists():
        raise HTTPException(status_code=404, detail="Session workspace not found")
    path = workspace / "HEARTBEAT.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(path)


def _calendar_merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _calendar_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _calendar_interval_seconds_from_text(text: str) -> Optional[int]:
    import re

    match = re.search(
        r"every\s+(\d+)\s*(second|seconds|sec|s|minute|minutes|min|m|hour|hours|h|day|days|d)\b",
        text.lower(),
    )
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit in {"second", "seconds", "sec", "s"}:
        return amount
    if unit in {"minute", "minutes", "min", "m"}:
        return amount * 60
    if unit in {"hour", "hours", "h"}:
        return amount * 3600
    if unit in {"day", "days", "d"}:
        return amount * 86400
    return None


def _calendar_parse_run_at_text(text: str, timezone_name: str) -> Optional[float]:
    import re

    lower = text.lower()
    rel = re.search(r"in\s+(\d+)\s*(minute|minutes|min|m|hour|hours|h|day|days|d)\b", lower)
    if rel:
        amount = int(rel.group(1))
        unit = rel.group(2)
        if unit in {"minute", "minutes", "min", "m"}:
            return time.time() + (amount * 60)
        if unit in {"hour", "hours", "h"}:
            return time.time() + (amount * 3600)
        if unit in {"day", "days", "d"}:
            return time.time() + (amount * 86400)
    at_match = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", lower)
    if at_match:
        hour = int(at_match.group(1))
        minute = int(at_match.group(2) or "0")
        ampm = at_match.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        local_now = datetime.now(ZoneInfo(timezone_name))
        candidate = local_now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
        if candidate <= local_now:
            candidate = candidate + timedelta(days=1)
        return candidate.astimezone(timezone.utc).timestamp()
    return None


def _calendar_status_from_cron_run(
    run: Optional[dict[str, Any]],
    now_ts: float,
    scheduled_at: float,
    enabled: bool,
    is_running: bool = False,
) -> str:
    if run:
        run_status = str(run.get("status", "")).lower()
        if run_status == "success":
            return "success"
        if run_status == "running":
            return "running"
        if run_status == "skipped":
            return "failed"
        return "failed"
    if is_running:
        return "running"
    if not enabled:
        return "disabled"
    return "missed" if scheduled_at < now_ts else "scheduled"


def _calendar_register_missed_event(event: dict[str, Any]) -> None:
    event_id = str(event.get("event_id") or "")
    if not event_id:
        return
    existing = _calendar_missed_events.get(event_id)
    if existing:
        return
    source = str(event.get("source") or "").strip().lower()
    source_ref = str(event.get("source_ref") or "").strip()
    scheduled_at = float(event.get("scheduled_at_epoch") or 0.0)
    superseded_ids: list[str] = []
    for existing_id, existing_record in _calendar_missed_events.items():
        if str(existing_record.get("status") or "").strip().lower() != "pending":
            continue
        existing_event = existing_record.get("event") if isinstance(existing_record.get("event"), dict) else {}
        if str(existing_event.get("source") or "").strip().lower() != source:
            continue
        if str(existing_event.get("source_ref") or "").strip() != source_ref:
            continue
        existing_scheduled_at = float(existing_event.get("scheduled_at_epoch") or 0.0)
        if existing_scheduled_at >= scheduled_at:
            # Keep the newest pending missed item only.
            return
        existing_record["status"] = "skipped_superseded"
        existing_record["updated_at"] = datetime.now().isoformat()
        superseded_ids.append(existing_id)

    for superseded_id in superseded_ids:
        _calendar_missed_notifications.discard(superseded_id)

    record = {
        "event_id": event_id,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "event": event,
    }
    _calendar_missed_events[event_id] = record
    if str(event.get("category") or "").strip().lower() == "low":
        return
    if event_id in _calendar_missed_notifications:
        return
    _calendar_missed_notifications.add(event_id)
    _add_notification(
        kind="calendar_missed",
        title="Missed Scheduled Event",
        message=f"{event.get('title', 'Scheduled event')} was missed and is awaiting action.",
        session_id=event.get("session_id"),
        severity="warning",
        requires_action=True,
        metadata={
            "event_id": event_id,
            "source": event.get("source"),
            "source_ref": event.get("source_ref"),
            "actions": ["approve_backfill_run", "reschedule", "delete_missed"],
        },
    )


def _calendar_missed_resolution(event_id: str) -> Optional[str]:
    record = _calendar_missed_events.get(event_id)
    if not record:
        return None
    status = str(record.get("status") or "").strip().lower()
    if status in {"approved_and_run", "rescheduled", "deleted", "skipped", "skipped_superseded"}:
        return status
    return None


def _calendar_cleanup_state() -> None:
    now_ts = time.time()
    stale_cutoff = now_ts - (30 * 86400)
    stale_event_ids: list[str] = []
    for event_id, record in _calendar_missed_events.items():
        event = record.get("event") if isinstance(record.get("event"), dict) else {}
        source = str(event.get("source") or "").strip().lower()
        # Heartbeat missed backfill/alerts are disabled by policy.
        if source == "heartbeat":
            stale_event_ids.append(event_id)
            continue
        scheduled_ts = _calendar_parse_ts(str(event.get("scheduled_at_utc") or ""), "UTC")
        if scheduled_ts is None:
            continue
        if float(scheduled_ts) < stale_cutoff:
            stale_event_ids.append(event_id)
    for event_id in stale_event_ids:
        _calendar_missed_events.pop(event_id, None)
        _calendar_missed_notifications.discard(event_id)

    # Remove legacy heartbeat-missed notifications from the dashboard stream.
    _notifications[:] = [
        item
        for item in _notifications
        if not (
            str(item.get("kind") or "").strip().lower() == "calendar_missed"
            and str((item.get("metadata") or {}).get("source") or "").strip().lower() == "heartbeat"
        )
    ]

    # Keep at most one pending missed item per (source, source_ref): newest wins.
    latest_pending_by_key: dict[tuple[str, str], tuple[str, float]] = {}
    for event_id, record in _calendar_missed_events.items():
        if str(record.get("status") or "").strip().lower() != "pending":
            continue
        event = record.get("event") if isinstance(record.get("event"), dict) else {}
        source = str(event.get("source") or "").strip().lower()
        source_ref = str(event.get("source_ref") or "").strip()
        if not source or not source_ref:
            continue
        scheduled_at = float(event.get("scheduled_at_epoch") or 0.0)
        if scheduled_at <= 0:
            parsed = _calendar_parse_ts(str(event.get("scheduled_at_utc") or ""), "UTC")
            scheduled_at = float(parsed or 0.0)
        key = (source, source_ref)
        current = latest_pending_by_key.get(key)
        if current is None or scheduled_at > current[1]:
            latest_pending_by_key[key] = (event_id, scheduled_at)

    for event_id, record in list(_calendar_missed_events.items()):
        if str(record.get("status") or "").strip().lower() != "pending":
            continue
        event = record.get("event") if isinstance(record.get("event"), dict) else {}
        key = (
            str(event.get("source") or "").strip().lower(),
            str(event.get("source_ref") or "").strip(),
        )
        latest = latest_pending_by_key.get(key)
        if latest and latest[0] != event_id:
            record["status"] = "skipped_superseded"
            record["updated_at"] = datetime.now().isoformat()
            _calendar_missed_notifications.discard(event_id)

    expired_proposals = [
        proposal_id
        for proposal_id, proposal in _calendar_change_proposals.items()
        if float(proposal.get("created_at_ts", 0.0)) < (now_ts - 86400)
    ]
    for proposal_id in expired_proposals:
        _calendar_change_proposals.pop(proposal_id, None)


def _calendar_match_cron_run(runs: list[dict[str, Any]], scheduled_at: float) -> Optional[dict[str, Any]]:
    best: Optional[dict[str, Any]] = None
    best_delta = 1e12
    for run in runs:
        raw_ts = run.get("scheduled_at")
        if raw_ts is None:
            continue
        try:
            run_ts = float(raw_ts)
        except Exception:
            continue
        delta = abs(run_ts - scheduled_at)
        if delta <= 90 and delta < best_delta:
            best = run
            best_delta = delta
    return best


def _calendar_iter_cron_occurrences(job: Any, start_ts: float, end_ts: float, max_count: int = 500) -> list[float]:
    occurrences: list[float] = []
    try:
        run_at = float(job.run_at) if job.run_at is not None else None
    except Exception:
        run_at = None
    if run_at is not None:
        if start_ts <= run_at <= end_ts:
            occurrences.append(run_at)
        return occurrences

    cron_expr = str(job.cron_expr or "").strip()
    if cron_expr:
        try:
            from croniter import croniter

            tz_name = getattr(job, "timezone", None) or "UTC"
            tz = ZoneInfo(str(tz_name))
            base_local = datetime.fromtimestamp(start_ts, timezone.utc).astimezone(tz) - timedelta(seconds=1)
            itr = croniter(cron_expr, base_local)
            for _ in range(max_count):
                next_dt = itr.get_next(datetime)
                next_ts = next_dt.astimezone(timezone.utc).timestamp()
                if next_ts > end_ts:
                    break
                if next_ts >= start_ts:
                    occurrences.append(next_ts)
        except Exception:
            return occurrences
        return occurrences

    every_seconds = int(getattr(job, "every_seconds", 0) or 0)
    if every_seconds <= 0:
        return occurrences
    base = float(getattr(job, "created_at", time.time()) or time.time())
    if base < start_ts:
        steps = int((start_ts - base) // every_seconds)
        base = base + (steps * every_seconds)
        if base < start_ts:
            base += every_seconds
    cursor = base
    count = 0
    while cursor <= end_ts and count < max_count:
        if cursor >= start_ts:
            occurrences.append(cursor)
            count += 1
        cursor += every_seconds
    return occurrences


def _calendar_project_cron_events(
    *,
    start_ts: float,
    end_ts: float,
    timezone_name: str,
    owner: Optional[str],
) -> list[dict[str, Any]]:
    use_projection = bool(_scheduling_projection.enabled)
    if use_projection:
        _scheduling_projection.seed_from_runtime()
        jobs = _scheduling_projection.list_cron_jobs()
        runs = _scheduling_projection.list_cron_runs(limit=2000)
        _scheduling_counter_inc("projection_read_hits")
    else:
        jobs = _cron_service.list_jobs() if _cron_service else []
        runs = _cron_service.list_runs(limit=2000) if _cron_service else []
    if not jobs:
        return []
    now_ts = time.time()
    runs_by_job: dict[str, list[dict[str, Any]]] = {}
    for row in runs:
        job_id = str(row.get("job_id") or "")
        if not job_id:
            continue
        runs_by_job.setdefault(job_id, []).append(row)

    events: list[dict[str, Any]] = []
    for job in jobs:
        if owner and str(getattr(job, "user_id", "")).lower() != owner.strip().lower():
            continue
        running_scheduled_at: Optional[float] = None
        if _cron_service:
            try:
                running_scheduled_at = _cron_service.running_job_scheduled_at.get(str(job.job_id))
            except Exception:
                running_scheduled_at = None
        occurrences = _calendar_iter_cron_occurrences(job, start_ts, end_ts, max_count=400)
        latest_missed_event: Optional[dict[str, Any]] = None
        for scheduled_at in occurrences:
            event_id = _calendar_event_id("cron", str(job.job_id), scheduled_at)
            matched_run = _calendar_match_cron_run(runs_by_job.get(str(job.job_id), []), scheduled_at)
            is_running = bool(
                matched_run is None
                and running_scheduled_at is not None
                and abs(float(running_scheduled_at) - float(scheduled_at)) <= 90
            )
            status_value = _calendar_status_from_cron_run(
                matched_run,
                now_ts=now_ts,
                scheduled_at=scheduled_at,
                enabled=bool(job.enabled),
                is_running=is_running,
            )
            metadata = job.metadata or {}
            workspace_dir = str(getattr(job, "workspace_dir", "") or "")
            workspace_session_id = Path(workspace_dir).name if workspace_dir else ""
            event = {
                "event_id": event_id,
                "source": "cron",
                "source_ref": str(job.job_id),
                "owner_id": str(job.user_id),
                "session_id": str(metadata.get("session_id") or workspace_session_id or ""),
                "channel": str(metadata.get("channel") or "cron"),
                "title": str(metadata.get("title") or f"Chron: {str(job.command)[:40]}"),
                "description": str(job.command),
                "category": str(metadata.get("priority") or "normal"),
                "color_key": "cron",
                "status": status_value,
                "scheduled_at_epoch": scheduled_at,
                "scheduled_at_utc": datetime.fromtimestamp(scheduled_at, timezone.utc).isoformat(),
                "scheduled_at_local": _calendar_local_iso(scheduled_at, timezone_name),
                "timezone_display": timezone_name,
                "always_running": False,
                "actions": [
                    "run_now",
                    "pause" if job.enabled else "resume",
                    "disable",
                    "open_logs",
                    "open_session",
                ],
            }
            if matched_run:
                event["run_status"] = matched_run.get("status")
                event["run_id"] = matched_run.get("run_id")
            if status_value == "missed":
                if scheduled_at < (now_ts - 48 * 3600):
                    continue
                if latest_missed_event is None or float(latest_missed_event.get("scheduled_at_epoch") or 0.0) < float(scheduled_at):
                    latest_missed_event = event
                continue
            events.append(event)
        if latest_missed_event:
            missed_event_id = str(latest_missed_event.get("event_id") or "")
            resolution = _calendar_missed_resolution(missed_event_id)
            if resolution in {"rescheduled", "deleted", "skipped", "skipped_superseded"}:
                continue
            if resolution == "approved_and_run":
                # Preserve the event for operator visibility, but mark it resolved.
                # A completed backfill run usually flips status to success via matched run.
                if str(latest_missed_event.get("status") or "").strip().lower() == "missed":
                    latest_missed_event["status"] = "success"
                latest_missed_event["resolution"] = "approved_and_run"
                latest_missed_event["actions"] = ["open_logs", "open_session"]
                events.append(latest_missed_event)
                continue
            if not resolution:
                latest_missed_event["actions"] = [
                    "approve_backfill_run",
                    "reschedule",
                    "delete_missed",
                    "open_logs",
                    "open_session",
                ]
                _calendar_register_missed_event(latest_missed_event)
            events.append(latest_missed_event)
    return events


def _calendar_should_include_heartbeat_summary(summary: dict[str, Any], now_ts: float) -> bool:
    session_id = str(summary.get("session_id") or "").strip()
    if not session_id:
        return False
    owner_id = str(summary.get("owner") or "").strip().lower()
    source = str(summary.get("source") or "").strip().lower()

    # Ignore cron-owned/session workspaces in heartbeat views.
    if session_id.startswith("cron_") or owner_id.startswith("cron:"):
        return False
    # Keep canonical chat/api session IDs by default; unknown local directories
    # are usually historical workspaces and should not produce always-running rows.
    if source == "local" and not session_id.startswith(("session_", "tg_", "api_")):
        return False

    active_connections = int(summary.get("active_connections") or 0)
    active_runs = int(summary.get("active_runs") or 0)
    if active_connections > 0 or active_runs > 0:
        return True
    if _heartbeat_service and session_id in _heartbeat_service.busy_sessions:
        return True

    # Only show "always running" heartbeat monitors for active sessions;
    # historical workspaces should not be treated as live monitors.
    return False


def _calendar_project_heartbeat_events(
    *,
    start_ts: float,
    end_ts: float,
    timezone_name: str,
    owner: Optional[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not _ops_service:
        return [], []
    now_ts = time.time()
    events: list[dict[str, Any]] = []
    always_running: list[dict[str, Any]] = []
    for summary in _ops_service.list_sessions(status_filter="all"):
        if not _calendar_should_include_heartbeat_summary(summary, now_ts):
            continue
        session_id = str(summary.get("session_id") or "")
        if not session_id:
            continue
        owner_id = str(summary.get("owner") or "unknown")
        if owner and owner_id.lower() != owner.strip().lower():
            continue
        workspace_dir = str(summary.get("workspace_dir") or str(WORKSPACES_DIR / session_id))
        overrides = _calendar_read_heartbeat_overrides(workspace_dir)
        hidden_flag = overrides.get("calendar_hidden", False)
        if isinstance(hidden_flag, str):
            hidden_flag = hidden_flag.strip().lower() in {"1", "true", "yes", "y", "on"}
        if bool(hidden_flag):
            continue
        if _heartbeat_service:
            schedule = _heartbeat_service._resolve_schedule(overrides)  # type: ignore[attr-defined]
            delivery = _heartbeat_service._resolve_delivery(overrides, session_id)  # type: ignore[attr-defined]
            every_seconds = int(getattr(schedule, "every_seconds", HEARTBEAT_INTERVAL_SECONDS) or HEARTBEAT_INTERVAL_SECONDS)
            delivery_mode = str(getattr(delivery, "mode", "last") or "last")
        else:
            every_seconds = HEARTBEAT_INTERVAL_SECONDS
            delivery_mode = "last"
        every_seconds = max(HEARTBEAT_INTERVAL_SECONDS, every_seconds)

        hb_state = _read_heartbeat_state(workspace_dir) or {}
        last_run = float(hb_state.get("last_run") or 0.0)
        raw_next_run = (last_run + every_seconds) if last_run > 0 else (now_ts + every_seconds)
        # Heartbeats do not backfill: if a run window is missed, schedule the next window.
        if delivery_mode != "none" and raw_next_run < now_ts:
            next_run = now_ts + every_seconds
        else:
            next_run = raw_next_run
        busy = bool(_heartbeat_service and session_id in _heartbeat_service.busy_sessions)
        if delivery_mode == "none":
            status_value = "disabled"
        elif busy:
            status_value = "running"
        else:
            status_value = "scheduled"

        # Main timeline event (next due) within active window.
        if start_ts <= next_run <= end_ts:
            event_id = _calendar_event_id("heartbeat", session_id, next_run)
            event = {
                "event_id": event_id,
                "source": "heartbeat",
                "source_ref": session_id,
                "owner_id": owner_id,
                "session_id": session_id,
                "channel": str(summary.get("channel") or "heartbeat"),
                "title": f"Heartbeat: {session_id[:14]}",
                "description": f"Heartbeat check every {every_seconds // 60} min",
                "category": "normal",
                "color_key": "heartbeat",
                "status": status_value,
                "scheduled_at_epoch": next_run,
                "scheduled_at_utc": datetime.fromtimestamp(next_run, timezone.utc).isoformat(),
                "scheduled_at_local": _calendar_local_iso(next_run, timezone_name),
                "timezone_display": timezone_name,
                "always_running": False,
                "actions": ["delete"],
            }
            events.append(event)

        active_connections = int(summary.get("active_connections") or 0)
        active_runs = int(summary.get("active_runs") or 0)
        is_live_monitor = active_connections > 0 or active_runs > 0 or busy

        always_event = {
            "event_id": _calendar_event_id("heartbeat", session_id, next_run),
            "source": "heartbeat",
            "source_ref": session_id,
            "owner_id": owner_id,
            "session_id": session_id,
            "channel": str(summary.get("channel") or "heartbeat"),
            "title": f"Heartbeat monitor ({session_id[:10]})",
            "description": f"Always running • every {max(1, every_seconds // 60)} min",
            "category": "normal",
            "color_key": "heartbeat",
            "status": status_value,
            "scheduled_at_epoch": next_run,
            "scheduled_at_utc": datetime.fromtimestamp(next_run, timezone.utc).isoformat(),
            "scheduled_at_local": _calendar_local_iso(next_run, timezone_name),
            "timezone_display": timezone_name,
            "always_running": True,
            "actions": ["delete"],
        }
        if is_live_monitor:
            always_running.append(always_event)
    return events, always_running


def _calendar_build_feed(
    *,
    start_ts: float,
    end_ts: float,
    timezone_name: str,
    source_filter: str,
    owner: Optional[str],
) -> dict[str, Any]:
    source_norm = (source_filter or "all").strip().lower()
    include_cron = source_norm in {"all", "cron"}
    include_heartbeat = source_norm in {"all", "heartbeat"}

    events: list[dict[str, Any]] = []
    always_running: list[dict[str, Any]] = []
    if include_cron:
        events.extend(
            _calendar_project_cron_events(
                start_ts=start_ts,
                end_ts=end_ts,
                timezone_name=timezone_name,
                owner=owner,
            )
        )
    if include_heartbeat:
        hb_events, hb_always = _calendar_project_heartbeat_events(
            start_ts=start_ts,
            end_ts=end_ts,
            timezone_name=timezone_name,
            owner=owner,
        )
        events.extend(hb_events)
        always_running.extend(hb_always)

    # Defensive dedupe for noisy projection/replay cycles.
    events_by_id = {str(item.get("event_id") or ""): item for item in events if str(item.get("event_id") or "").strip()}
    always_running_by_ref: dict[str, dict[str, Any]] = {}
    for item in always_running:
        source = str(item.get("source") or "").strip().lower()
        source_ref = str(item.get("source_ref") or "").strip()
        if not source or not source_ref:
            continue
        key = f"{source}|{source_ref}"
        existing = always_running_by_ref.get(key)
        if not existing:
            always_running_by_ref[key] = item
            continue
        existing_ts = float(existing.get("scheduled_at_epoch") or 0.0)
        candidate_ts = float(item.get("scheduled_at_epoch") or 0.0)
        if candidate_ts > existing_ts:
            always_running_by_ref[key] = item
    events = list(events_by_id.values())
    always_running = list(always_running_by_ref.values())

    events.sort(key=lambda item: float(item.get("scheduled_at_epoch") or 0.0))
    always_running.sort(key=lambda item: float(item.get("scheduled_at_epoch") or 0.0))
    return {
        "events": events,
        "always_running": always_running,
    }


def _calendar_apply_heartbeat_delivery_mode(session_id: str, mode: str) -> dict[str, Any]:
    workspace = WORKSPACES_DIR / session_id
    if not workspace.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    existing = _calendar_read_heartbeat_overrides(str(workspace))
    merged = _calendar_merge_dict(existing, {"delivery": {"mode": mode}})
    path = _calendar_write_heartbeat_overrides(session_id, merged)
    return {"path": path, "mode": mode}


def _calendar_apply_heartbeat_calendar_hidden(session_id: str, hidden: bool) -> dict[str, Any]:
    workspace = WORKSPACES_DIR / session_id
    if not workspace.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    existing = _calendar_read_heartbeat_overrides(str(workspace))
    merged = _calendar_merge_dict(existing, {"calendar_hidden": bool(hidden)})
    path = _calendar_write_heartbeat_overrides(session_id, merged)
    return {"path": path, "calendar_hidden": bool(hidden)}


def _calendar_apply_heartbeat_interval(session_id: str, every_seconds: int) -> dict[str, Any]:
    workspace = WORKSPACES_DIR / session_id
    if not workspace.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    normalized_seconds = max(HEARTBEAT_INTERVAL_SECONDS, int(every_seconds))
    existing = _calendar_read_heartbeat_overrides(str(workspace))
    merged = _calendar_merge_dict(existing, {"heartbeat": {"every_seconds": normalized_seconds}})
    path = _calendar_write_heartbeat_overrides(session_id, merged)
    return {"path": path, "every_seconds": normalized_seconds}


def _calendar_create_change_proposal(
    *,
    event_id: str,
    instruction: str,
    timezone_name: str,
) -> dict[str, Any]:
    source, source_ref, _ = _calendar_parse_event_id(event_id)
    text = instruction.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Instruction cannot be empty")
    lower = text.lower()
    warnings: list[str] = []
    confidence = "medium"
    operation: dict[str, Any]
    summary: str
    before: dict[str, Any]
    after: dict[str, Any]

    if source == "cron":
        if not _cron_service:
            raise HTTPException(status_code=503, detail="Chron service not available")
        job = _cron_service.get_job(source_ref)
        if not job:
            raise HTTPException(status_code=404, detail="Chron job not found")
        before = job.to_dict()
        if any(token in lower for token in ("pause", "disable", "stop")):
            operation = {"type": "cron_set_enabled", "enabled": False}
            summary = "Disable chron job"
            after = {**before, "enabled": False}
        elif any(token in lower for token in ("resume", "enable", "start")):
            operation = {"type": "cron_set_enabled", "enabled": True}
            summary = "Enable chron job"
            after = {**before, "enabled": True}
        elif "run now" in lower:
            operation = {"type": "cron_run_now"}
            summary = "Run chron job now"
            after = before
        else:
            every_seconds = _calendar_interval_seconds_from_text(lower)
            run_at_ts = _calendar_parse_run_at_text(lower, timezone_name)
            if every_seconds is not None:
                operation = {"type": "cron_set_interval", "every_seconds": every_seconds}
                summary = f"Set chron interval to every {every_seconds} seconds"
                after = {**before, "every_seconds": every_seconds}
                confidence = "high"
            elif run_at_ts is not None:
                operation = {"type": "cron_backfill_schedule", "run_at": run_at_ts}
                summary = "Create one-shot backfill run at requested time"
                after = {
                    "command": before.get("command"),
                    "run_at": datetime.fromtimestamp(run_at_ts, timezone.utc).isoformat(),
                    "delete_after_run": True,
                }
                confidence = "medium"
            else:
                operation = {"type": "none"}
                summary = "Could not safely map instruction to a chron change"
                after = before
                warnings.append("Instruction not recognized; try explicit phrasing like 'pause', 'resume', or 'every 30 minutes'.")
                confidence = "low"
    else:
        before = {"session_id": source_ref}
        if any(token in lower for token in ("pause", "disable", "stop")):
            operation = {"type": "heartbeat_set_delivery", "mode": "none"}
            summary = "Pause heartbeat delivery for session"
            after = {"delivery.mode": "none"}
        elif any(token in lower for token in ("resume", "enable", "start")):
            operation = {"type": "heartbeat_set_delivery", "mode": "last"}
            summary = "Resume heartbeat delivery for session"
            after = {"delivery.mode": "last"}
        elif "run now" in lower:
            operation = {"type": "heartbeat_run_now"}
            summary = "Trigger heartbeat immediately"
            after = before
        else:
            every_seconds = _calendar_interval_seconds_from_text(lower)
            if every_seconds is not None:
                normalized_seconds = max(HEARTBEAT_INTERVAL_SECONDS, int(every_seconds))
                if normalized_seconds != int(every_seconds):
                    warnings.append(
                        f"Heartbeat interval is capped to >= {HEARTBEAT_INTERVAL_SECONDS} seconds (30 minutes) to prevent runaway scheduling."
                    )
                operation = {"type": "heartbeat_set_interval", "every_seconds": normalized_seconds}
                summary = f"Set heartbeat interval to every {normalized_seconds} seconds"
                after = {"heartbeat.every_seconds": normalized_seconds}
            else:
                operation = {"type": "none"}
                summary = "Could not safely map instruction to a heartbeat change"
                after = before
                warnings.append("Instruction not recognized; try explicit phrasing like 'pause', 'resume', or 'every 30 minutes'.")
                confidence = "low"

    proposal_id = f"calprop_{uuid.uuid4().hex[:10]}"
    proposal = {
        "proposal_id": proposal_id,
        "event_id": event_id,
        "source": source,
        "source_ref": source_ref,
        "instruction": text,
        "summary": summary,
        "before": before,
        "after": after,
        "operation": operation,
        "warnings": warnings,
        "confidence": confidence,
        "status": "pending_confirmation",
        "created_at": datetime.now().isoformat(),
        "created_at_ts": time.time(),
    }
    _calendar_change_proposals[proposal_id] = proposal
    return proposal


async def _calendar_apply_event_action(
    *,
    source: str,
    source_ref: str,
    action: str,
    event_id: str,
    run_at: Optional[str],
    timezone_name: str,
) -> dict[str, Any]:
    action_norm = action.strip().lower()
    if source == "cron":
        if not _cron_service:
            raise HTTPException(status_code=503, detail="Chron service not available")
        job = _cron_service.get_job(source_ref)
        if not job:
            raise HTTPException(status_code=404, detail="Chron job not found")
        if action_norm == "run_now":
            record = await _cron_service.run_job_now(source_ref, reason="calendar_action")
            return {"status": "ok", "action": action_norm, "run": record.to_dict()}
        if action_norm == "pause":
            updated = _cron_service.update_job(source_ref, {"enabled": False})
            return {"status": "ok", "action": action_norm, "job": updated.to_dict()}
        if action_norm == "resume":
            updated = _cron_service.update_job(source_ref, {"enabled": True})
            return {"status": "ok", "action": action_norm, "job": updated.to_dict()}
        if action_norm == "disable":
            updated = _cron_service.update_job(source_ref, {"enabled": False})
            return {"status": "ok", "action": action_norm, "job": updated.to_dict()}
        if action_norm == "approve_backfill_run":
            _source, _source_ref, scheduled_at = _calendar_parse_event_id(event_id)
            record = await _cron_service.run_job_now(
                source_ref,
                reason="calendar_backfill_approved",
                scheduled_at=float(scheduled_at),
            )
            queue_entry = _calendar_missed_events.get(event_id)
            if queue_entry:
                queue_entry["status"] = "approved_and_run"
                queue_entry["updated_at"] = datetime.now().isoformat()
            return {"status": "ok", "action": action_norm, "run": record.to_dict()}
        if action_norm == "reschedule":
            run_at_ts = _calendar_parse_ts(run_at, timezone_name) if run_at else None
            if run_at_ts is None and run_at:
                run_at_ts = parse_run_at(run_at, timezone_name=timezone_name)
            if run_at_ts is None:
                run_at_ts = time.time() + 3600
            new_job = _cron_service.add_job(
                user_id=job.user_id,
                workspace_dir=job.workspace_dir,
                command=job.command,
                run_at=run_at_ts,
                delete_after_run=True,
                enabled=True,
                metadata={**(job.metadata or {}), "backfill_for_job_id": source_ref},
            )
            queue_entry = _calendar_missed_events.get(event_id)
            if queue_entry:
                queue_entry["status"] = "rescheduled"
                queue_entry["updated_at"] = datetime.now().isoformat()
                queue_entry["rescheduled_job_id"] = new_job.job_id
            return {"status": "ok", "action": action_norm, "job": new_job.to_dict()}
        if action_norm == "delete_missed":
            queue_entry = _calendar_missed_events.get(event_id)
            if queue_entry:
                queue_entry["status"] = "deleted"
                queue_entry["updated_at"] = datetime.now().isoformat()
            return {"status": "ok", "action": action_norm}
        if action_norm == "open_logs":
            return {
                "status": "ok",
                "action": action_norm,
                "path": f"/api/v1/ops/logs/tail?path=cron_runs.jsonl",
            }
        if action_norm == "open_session":
            session_id = str((job.metadata or {}).get("session_id") or "")
            return {"status": "ok", "action": action_norm, "session_id": session_id}

    if source == "heartbeat":
        session_id = source_ref
        if action_norm == "run_now":
            if not _heartbeat_service:
                raise HTTPException(status_code=503, detail="Heartbeat service not available")
            _heartbeat_service.request_heartbeat_now(session_id, reason="calendar_action")
            return {"status": "ok", "action": action_norm, "session_id": session_id}
        if action_norm in {"pause", "disable"}:
            result = _calendar_apply_heartbeat_delivery_mode(session_id, "none")
            return {"status": "ok", "action": action_norm, "session_id": session_id, **result}
        if action_norm == "delete":
            result = _calendar_apply_heartbeat_calendar_hidden(session_id, True)
            return {"status": "ok", "action": action_norm, "session_id": session_id, **result}
        if action_norm == "resume":
            result = _calendar_apply_heartbeat_delivery_mode(session_id, "last")
            return {"status": "ok", "action": action_norm, "session_id": session_id, **result}
        if action_norm in {"approve_backfill_run", "reschedule", "delete_missed"}:
            raise HTTPException(
                status_code=400,
                detail="Heartbeat backfill is disabled. Missed heartbeat windows are skipped automatically.",
            )
        if action_norm == "open_logs":
            return {
                "status": "ok",
                "action": action_norm,
                "path": f"/api/v1/ops/logs/tail?session_id={session_id}",
                "session_id": session_id,
            }
        if action_norm == "open_session":
            return {"status": "ok", "action": action_norm, "session_id": session_id}

    raise HTTPException(status_code=400, detail=f"Unsupported action '{action_norm}' for source '{source}'")


def _load_skill_catalog() -> list[dict]:
    logger.info("Loading skill catalog...")
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

    cancelled_task = await _cancel_execution_task(
        session_id,
        timeout_seconds=session_cancel_wait_seconds(),
    )

    if not cancelled_task:
        # Fallback for stale turns/tasks that predate task tracking or failed to unwind.
        state = _session_turn_snapshot(session_id)
        active_turn_id = str(state.get("active_turn_id") or "")
        if active_turn_id:
            active_record = state.get("turns", {}).get(active_turn_id) if isinstance(state.get("turns"), dict) else {}
            run_source = _normalize_run_source(
                active_record.get("run_source") if isinstance(active_record, dict) else None
            )
            async with _session_turn_lock(session_id):
                _finalize_turn(session_id, active_turn_id, TURN_STATUS_CANCELLED, error_message=reason)
            _decrement_session_active_runs(session_id, run_source=run_source)

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

    return {
        "status": "cancel_requested",
        "session_id": session_id,
        "run_id": marked_run_id,
        "reason": reason,
        "task_cancelled": cancelled_task,
    }


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


def _require_ops_auth(request: Request, token_override: Optional[str] = None) -> None:
    if not OPS_TOKEN:
        return
    header = request.headers.get("authorization", "")
    token = ""
    if header.lower().startswith("bearer "):
        token = header.split(" ", 1)[1].strip()
    if not token:
        token = request.headers.get("x-ua-ops-token", "").strip()
    if not token and token_override is not None:
        token = str(token_override).strip()
    if token != OPS_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _extract_auth_token_from_headers(headers: Any) -> str:
    header = str(headers.get("authorization", "")).strip()
    token = ""
    if header.lower().startswith("bearer "):
        token = header.split(" ", 1)[1].strip()
    if not token:
        token = str(headers.get("x-ua-internal-token", "")).strip()
    if not token:
        token = str(headers.get("x-ua-ops-token", "")).strip()
    return token


def _session_api_auth_required() -> bool:
    return _DEPLOYMENT_PROFILE == "vps" or bool(SESSION_API_TOKEN)


def _require_session_api_auth(request: Request) -> None:
    if not _session_api_auth_required():
        return
    if not SESSION_API_TOKEN:
        raise HTTPException(status_code=503, detail="Session API token is not configured.")
    token = _extract_auth_token_from_headers(request.headers)
    if token != SESSION_API_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _require_youtube_ingest_auth(request: Request) -> None:
    explicit_token = (os.getenv("UA_YOUTUBE_INGEST_TOKEN") or "").strip()
    if explicit_token:
        token = _extract_auth_token_from_headers(request.headers)
        if token != explicit_token:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return
    _require_session_api_auth(request)


async def _require_session_ws_auth(websocket: WebSocket) -> bool:
    if not _session_api_auth_required():
        return True
    if not SESSION_API_TOKEN:
        await websocket.close(code=1011, reason="Session API token is not configured.")
        return False
    token = _extract_auth_token_from_headers(websocket.headers)
    if token != SESSION_API_TOKEN:
        await websocket.close(code=4401, reason="Unauthorized")
        return False
    return True


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

    def _session_id_for_connection(self, connection_id: str) -> Optional[str]:
        for session_id, connection_ids in self.session_connections.items():
            if connection_id in connection_ids:
                return session_id
        return None

    async def _send_text_with_timeout(self, websocket: WebSocket, payload: str) -> None:
        await asyncio.wait_for(
            websocket.send_text(payload),
            timeout=WS_SEND_TIMEOUT_SECONDS,
        )

    async def send_json(self, connection_id: str, data: dict, session_id: Optional[str] = None):
        if connection_id in self.active_connections:
            try:
                await self._send_text_with_timeout(
                    self.active_connections[connection_id],
                    json.dumps(data),
                )
                if session_id:
                    _record_session_event(session_id, str(data.get("type", "")))
            except asyncio.TimeoutError:
                _increment_metric("ws_send_timeouts")
                _increment_metric("ws_send_failures")
                stale_session = session_id or self._session_id_for_connection(connection_id)
                if stale_session:
                    self.disconnect(connection_id, stale_session)
                _increment_metric("ws_stale_evictions")
                logger.warning("Timed out sending websocket payload to %s", connection_id)
            except Exception as e:
                _increment_metric("ws_send_failures")
                stale_session = session_id or self._session_id_for_connection(connection_id)
                if stale_session:
                    self.disconnect(connection_id, stale_session)
                _increment_metric("ws_stale_evictions")
                logger.error(f"Failed to send to {connection_id}: {e}")

    async def broadcast(self, session_id: str, data: dict, exclude_connection_id: Optional[str] = None):
        """Send a message to all connections associated with a session_id."""
        _record_session_event(session_id, str(data.get("type", "")))
        if session_id not in self.session_connections:
            return

        payload = json.dumps(data)
        # Snapshot the list to avoid runtime errors if connections drop during iteration
        targets = list(self.session_connections[session_id])
        stale_connections: list[str] = []
        
        for connection_id in targets:
            if connection_id == exclude_connection_id:
                continue
                
            if connection_id in self.active_connections:
                try:
                    await self._send_text_with_timeout(
                        self.active_connections[connection_id],
                        payload,
                    )
                except asyncio.TimeoutError:
                    _increment_metric("ws_send_timeouts")
                    _increment_metric("ws_send_failures")
                    stale_connections.append(connection_id)
                    logger.warning(
                        "Timed out broadcasting websocket payload to %s (session=%s)",
                        connection_id,
                        session_id,
                    )
                except Exception as e:
                    _increment_metric("ws_send_failures")
                    stale_connections.append(connection_id)
                    logger.error(f"Failed to broadcast to {connection_id}: {e}")

        for stale_connection in stale_connections:
            self.disconnect(stale_connection, session_id)
            _increment_metric("ws_stale_evictions")


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
    # Keep timeout aligned with durable.db connect_runtime_db() defaults to reduce
    # transient lock errors during concurrent cron + VP runtime activity.
    main_module.runtime_db_conn.execute("PRAGMA busy_timeout=60000")
    ensure_schema(main_module.runtime_db_conn)
    
    # Load budget config (defined in main.py)
    main_module.budget_config = main_module.load_budget_config()
    
    # Initialize Heartbeat Service
    global _heartbeat_service, _cron_service, _ops_service, _hooks_service
    if HEARTBEAT_ENABLED:
        logger.info("💓 Heartbeat System ENABLED")
        _heartbeat_service = HeartbeatService(
            get_gateway(),
            manager,
            system_event_provider=_drain_system_events,
            event_sink=_emit_heartbeat_event,
        )
        await _heartbeat_service.start()
    else:
        logger.info("💤 Heartbeat System DISABLED (feature flag)")

    if CRON_ENABLED:
        logger.info("⏱️ Chron Service ENABLED")
        _cron_service = CronService(
            get_gateway(),
            WORKSPACES_DIR,
            event_sink=_emit_cron_event,
            wake_callback=_cron_wake_callback,
            system_event_callback=_enqueue_system_event,
        )
        await _cron_service.start()
    else:
        logger.info("⏲️ Chron Service DISABLED (feature flag)")
    
    # Always enabled Ops Service
    _ops_service = OpsService(get_gateway(), WORKSPACES_DIR)
    
    # Initialize Hooks Service
    _hooks_service = HooksService(
        get_gateway(),
        turn_admitter=_admit_hook_turn,
        turn_finalizer=_finalize_hook_turn,
        run_counter_start=_increment_session_active_runs,
        run_counter_finish=_decrement_session_active_runs,
    )
    logger.info("🪝 Hooks Service Initialized")

    if _scheduling_projection.enabled:
        _scheduling_projection.seed_from_runtime()
        logger.info("📈 Scheduling projection enabled (event-driven chron projection path)")

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


@app.get("/api/v1/hooks/readyz")
async def hooks_readyz():
    """
    No-auth hooks readiness endpoint.

    This is intended for health probes and operational checks so they do not
    require hook auth tokens and do not trigger 401 noise.
    """
    if not _hooks_service:
        return {
            "ready": False,
            "service_initialized": False,
            "hooks_enabled": False,
            "reason": "hooks_service_not_initialized",
        }

    status = _hooks_service.readiness_status()
    status["service_initialized"] = True
    return status


@app.post("/api/v1/hooks/{subpath:path}")
async def hooks_endpoint(request: Request, subpath: str):
    if not _hooks_service:
        raise HTTPException(status_code=503, detail="Hooks service not initialized")
    return await _hooks_service.handle_request(request, subpath)


@app.post("/api/v1/youtube/ingest")
async def youtube_ingest_endpoint(request: Request, payload: YouTubeIngestRequest):
    """
    Local worker endpoint for transcript ingestion.

    Intended usage:
    - VPS control-plane forwards ingestion requests over Tailscale/reverse tunnel.
    - Local worker performs YouTube transcript extraction from a residential IP.
    """
    _require_youtube_ingest_auth(request)

    video_url, video_id = normalize_video_target(payload.video_url, payload.video_id)
    if not video_url:
        raise HTTPException(status_code=400, detail="video_url or valid video_id is required")

    result = await asyncio.to_thread(
        ingest_youtube_transcript,
        video_url=video_url,
        video_id=video_id,
        language=(payload.language or "en").strip() or "en",
        timeout_seconds=max(5, min(int(payload.timeout_seconds or 120), 600)),
        max_chars=max(5_000, min(int(payload.max_chars or 180_000), 800_000)),
        min_chars=max(20, min(int(payload.min_chars or 160), 5000)),
    )
    result["request_id"] = (payload.request_id or "").strip() or None
    result["worker_profile"] = _DEPLOYMENT_PROFILE
    return result


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
        "runtime_path": os.getenv("PATH", ""),
        "runtime_tools": runtime_tool_status(),
        "deployment_profile": _deployment_profile_defaults(),
    }


@app.get("/api/v1/dashboard/metrics/coder-vp")
async def dashboard_coder_vp_metrics(
    vp_id: str = "vp.coder.primary",
    mission_limit: int = 20,
    event_limit: int = 100,
):
    vp_identifier = (vp_id or "").strip()
    if not vp_identifier:
        raise HTTPException(status_code=400, detail="vp_id is required")

    clamped_mission_limit = max(1, min(int(mission_limit), 500))
    clamped_event_limit = max(1, min(int(event_limit), 1000))
    try:
        metrics = _vp_metrics_snapshot(
            vp_id=vp_identifier,
            mission_limit=clamped_mission_limit,
            event_limit=clamped_event_limit,
        )
        return {"status": "ok", "metrics": metrics}
    except HTTPException as exc:
        # Keep dashboard summary surfaces resilient when runtime DB is unavailable.
        return {"status": "unavailable", "detail": str(exc.detail), "metrics": None}


@app.post("/api/v1/sessions", response_model=CreateSessionResponse)
async def create_session(request: CreateSessionRequest, http_request: Request):
    _require_session_api_auth(http_request)
    if _DEPLOYMENT_PROFILE == "vps" and not str(request.user_id or "").strip():
        raise HTTPException(status_code=400, detail="user_id is required in vps profile")
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
        session.metadata["user_id"] = session.user_id
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
async def list_sessions(request: Request):
    _require_session_api_auth(request)
    if _ops_service:
        summaries = _ops_service.list_sessions(status_filter="all")
        return {
            "sessions": [
                SessionSummaryResponse(
                    session_id=str(item.get("session_id") or ""),
                    workspace_dir=str(item.get("workspace_dir") or ""),
                    status=str(item.get("status") or "unknown"),
                    user_id=str(item.get("owner") or "") or None,
                    metadata=item,
                ).model_dump()
                for item in summaries
            ]
        }

    gateway = get_gateway()
    summaries = gateway.list_sessions()
    in_memory = {}
    for summary in summaries:
        session = get_session(summary.session_id)
        if session:
            in_memory[summary.session_id] = session.user_id
    return {
        "sessions": [
            SessionSummaryResponse(
                session_id=s.session_id,
                workspace_dir=s.workspace_dir,
                status=s.status,
                user_id=(
                    (s.metadata.get("user_id") if isinstance(s.metadata, dict) else None)
                    or in_memory.get(s.session_id)
                ),
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

    _scheduling_counter_inc("heartbeat_wake_requests")
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

    _scheduling_counter_inc("heartbeat_last_requests")
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


_SIMPLE_INTERVAL_RE = re.compile(
    r"^(?:in\s+)?(\d+)\s*(second|seconds|sec|secs|s|minute|minutes|min|mins|m|hour|hours|hr|hrs|h|day|days|d)$",
    re.IGNORECASE,
)


def _normalize_interval_from_text(text: str) -> Optional[str]:
    raw = (text or "").strip().lower()
    if not raw:
        return None
    compact = raw.replace(" ", "")
    if re.match(r"^\d+[smhd]$", compact):
        return compact
    match = _SIMPLE_INTERVAL_RE.match(raw)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith(("second", "sec")) or unit == "s":
        suffix = "s"
    elif unit.startswith(("minute", "min")) or unit == "m":
        suffix = "m"
    elif unit.startswith(("hour", "hr")) or unit == "h":
        suffix = "h"
    else:
        suffix = "d"
    return f"{amount}{suffix}"


def _parse_time_of_day_for_daily_cron(text: str) -> Optional[tuple[int, int]]:
    raw = (text or "").strip().lower()
    if not raw:
        return None
    for prefix in ("today ", "tomorrow ", "tonight "):
        if raw.startswith(prefix):
            raw = raw[len(prefix):].strip()
            break
    if raw.startswith("at "):
        raw = raw[3:].strip()
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", raw)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridiem = (match.group(3) or "").lower()
    if minute < 0 or minute > 59:
        return None
    if meridiem:
        if hour < 1 or hour > 12:
            return None
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
    elif hour > 23:
        return None
    return hour, minute


def _resolve_simplified_schedule_fields(
    schedule_time: str,
    repeat: bool,
    timezone_name: str,
) -> tuple[Optional[str], Optional[str], Optional[float], bool]:
    text = (schedule_time or "").strip()
    if not text:
        raise ValueError("schedule_time is required when using simplified chron input.")

    if repeat:
        every = _normalize_interval_from_text(text)
        if every:
            return every, None, None, False
        tod = _parse_time_of_day_for_daily_cron(text)
        if tod:
            hour, minute = tod
            return None, f"{minute} {hour} * * *", None, False
        raise ValueError(
            "For repeating jobs, use a relative interval like 'in 30 minutes' or a clock time like '4:30 pm'."
        )

    run_at_ts = parse_run_at(text, timezone_name=timezone_name)
    if run_at_ts is None:
        raise ValueError(
            "Invalid schedule_time. Use natural text like 'in 20 minutes' or '4:30 pm'."
        )
    return None, None, run_at_ts, True


def _schedule_text_suggests_repeat(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    if re.search(r"\b(every|daily|weekly|weekday|weekdays|monthly|yearly)\b", lowered):
        return True
    if lowered.startswith("at "):
        return True
    return False


def _resolve_simplified_schedule_update_fields(
    *,
    schedule_time: str,
    repeat: Optional[bool],
    timezone_name: str,
    job: Any,
) -> tuple[Optional[str], Optional[str], Optional[float], bool]:
    if repeat is None:
        existing_repeat = bool(getattr(job, "cron_expr", None) or int(getattr(job, "every_seconds", 0) or 0) > 0)
        inferred_repeat = _schedule_text_suggests_repeat(schedule_time)
        repeat_mode = inferred_repeat or existing_repeat
    else:
        repeat_mode = bool(repeat)
    return _resolve_simplified_schedule_fields(
        schedule_time=schedule_time,
        repeat=repeat_mode,
        timezone_name=timezone_name,
    )


class _AgentScheduleInterpretation(BaseModel):
    status: str = "ok"
    every: Optional[str] = None
    cron_expr: Optional[str] = None
    run_at: Optional[Any] = None
    delete_after_run: Optional[bool] = None
    reason: Optional[str] = None
    confidence: Optional[str] = None


def _chron_job_schedule_snapshot(job: Any) -> dict[str, Any]:
    return {
        "job_id": str(getattr(job, "job_id", "") or ""),
        "every_seconds": int(getattr(job, "every_seconds", 0) or 0),
        "cron_expr": getattr(job, "cron_expr", None),
        "run_at_epoch": getattr(job, "run_at", None),
        "timezone": str(getattr(job, "timezone", "UTC") or "UTC"),
        "delete_after_run": bool(getattr(job, "delete_after_run", False)),
    }


def _normalize_agent_every_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw.startswith("every "):
        raw = raw[6:].strip()
    if raw.startswith("in "):
        raw = raw[3:].strip()
    normalized = _normalize_interval_from_text(raw)
    if normalized:
        return normalized
    if re.match(r"^\d+[smhd]$", raw):
        return raw
    if re.match(r"^\d+$", raw):
        return f"{int(raw)}s"
    return None


def _coerce_agent_schedule_to_update_fields(
    *,
    interpretation: _AgentScheduleInterpretation,
    repeat: Optional[bool],
    timezone_name: str,
) -> tuple[Optional[str], Optional[str], Optional[float], bool]:
    status = (interpretation.status or "ok").strip().lower()
    if status not in {"ok", "applied"}:
        reason = (interpretation.reason or "").strip() or (
            "System configuration agent could not safely interpret the schedule request."
        )
        raise HTTPException(status_code=400, detail=reason)

    every = _normalize_agent_every_value(interpretation.every)
    cron_expr = str(interpretation.cron_expr).strip() if interpretation.cron_expr else None
    run_at_ts: Optional[float] = None
    if interpretation.run_at not in (None, ""):
        run_at_ts = parse_run_at(interpretation.run_at, timezone_name=timezone_name)
        if run_at_ts is None:
            raise ValueError("System configuration agent returned an invalid run_at value.")

    selected = int(every is not None) + int(cron_expr is not None) + int(run_at_ts is not None)
    if selected != 1:
        raise ValueError("System configuration agent must return exactly one schedule mode.")

    if repeat is True and run_at_ts is not None:
        raise ValueError("repeat=true requires a repeating schedule (every or cron_expr).")
    if repeat is False and run_at_ts is None:
        raise ValueError("repeat=false requires a one-shot schedule (run_at).")

    if run_at_ts is not None:
        # Simplified one-shot updates are always modeled as run once, then delete.
        delete_after_run = True
    else:
        delete_after_run = False

    return every, cron_expr, run_at_ts, delete_after_run


async def _get_or_create_system_configuration_session() -> GatewaySession:
    gateway = get_gateway()
    try:
        return await gateway.resume_session(_SYSTEM_CONFIGURATION_AGENT_SESSION_ID)
    except Exception:
        workspace = str(WORKSPACES_DIR / _SYSTEM_CONFIGURATION_AGENT_SESSION_ID)
        return await gateway.create_session(
            user_id="ops:system-configuration-agent",
            workspace_dir=workspace,
        )


def _build_schedule_interpretation_prompt(
    *,
    schedule_time: str,
    repeat: Optional[bool],
    timezone_name: str,
    job: Any,
) -> str:
    context = {
        "instruction": schedule_time,
        "repeat_override": repeat,
        "timezone": timezone_name,
        "now_utc": datetime.now(timezone.utc).isoformat(),
        "job_schedule_before": _chron_job_schedule_snapshot(job),
    }
    return (
        "Internal system configuration task.\n"
        "Interpret a natural-language schedule update for a chron job.\n"
        "This is runtime configuration work; delegate to "
        "Task(subagent_type='system-configuration-agent', ...) if needed.\n"
        "Return ONLY one JSON object, no markdown and no extra text.\n"
        "Schema:\n"
        "{\n"
        '  "status": "ok|needs_clarification|cannot_comply",\n'
        '  "every": "30m|null",\n'
        '  "cron_expr": "5-field cron expr|null",\n'
        '  "run_at": "ISO-8601 timestamp with timezone|null",\n'
        '  "delete_after_run": true|false|null,\n'
        '  "reason": "short explanation",\n'
        '  "confidence": "low|medium|high"\n'
        "}\n"
        "Rules:\n"
        "- Exactly one of every, cron_expr, run_at must be non-null when status is ok.\n"
        "- If repeat_override=true, do not return run_at.\n"
        "- If repeat_override=false, return run_at and delete_after_run=true.\n"
        "- If repeat_override is null, infer intent from instruction and current schedule.\n"
        "- Keep timezone handling explicit.\n"
        "Context JSON:\n"
        f"{json.dumps(context, ensure_ascii=True, indent=2)}\n"
    )


async def _interpret_schedule_with_system_configuration_agent(
    *,
    schedule_time: str,
    repeat: Optional[bool],
    timezone_name: str,
    job: Any,
) -> _AgentScheduleInterpretation:
    session = await _get_or_create_system_configuration_session()
    gateway = get_gateway()
    prompt = _build_schedule_interpretation_prompt(
        schedule_time=schedule_time,
        repeat=repeat,
        timezone_name=timezone_name,
        job=job,
    )
    result = await gateway.run_query(
        session,
        GatewayRequest(
            user_input=prompt,
            force_complex=True,
            metadata={
                "source": "ops",
                "operation": "chron_schedule_interpretation",
                "subagent_type": "system-configuration-agent",
            },
        ),
    )
    payload = extract_json_payload(
        result.response_text,
        model=_AgentScheduleInterpretation,
        require_model=True,
    )
    if not isinstance(payload, _AgentScheduleInterpretation):
        raise ValueError("System configuration agent did not return a valid schedule payload.")
    return payload


async def _resolve_simplified_schedule_update_fields_with_agent(
    *,
    schedule_time: str,
    repeat: Optional[bool],
    timezone_name: str,
    job: Any,
) -> tuple[Optional[str], Optional[str], Optional[float], bool]:
    try:
        interpretation = await _interpret_schedule_with_system_configuration_agent(
            schedule_time=schedule_time,
            repeat=repeat,
            timezone_name=timezone_name,
            job=job,
        )
        return _coerce_agent_schedule_to_update_fields(
            interpretation=interpretation,
            repeat=repeat,
            timezone_name=timezone_name,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "System configuration schedule interpretation failed for job %s, falling back to deterministic parser: %s",
            getattr(job, "job_id", "unknown"),
            exc,
        )
        return _resolve_simplified_schedule_update_fields(
            schedule_time=schedule_time,
            repeat=repeat,
            timezone_name=timezone_name,
            job=job,
        )


@app.get("/api/v1/cron/jobs")
async def list_cron_jobs():
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Chron service not available.")
    return {
        "jobs": [
            {**job.to_dict(), "running": job.job_id in _cron_service.running_jobs}
            for job in _cron_service.list_jobs()
        ]
    }


@app.post("/api/v1/cron/jobs")
async def create_cron_job(request: CronJobCreateRequest):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Chron service not available.")
    try:
        # Parse run_at (relative, ISO, or natural text in the request timezone)
        run_at_ts = parse_run_at(request.run_at, timezone_name=request.timezone) if request.run_at else None
        every_raw = request.every
        cron_expr = request.cron_expr
        delete_after_run = request.delete_after_run

        if request.schedule_time is not None:
            every_raw, cron_expr, run_at_ts, delete_after_run = _resolve_simplified_schedule_fields(
                schedule_time=request.schedule_time,
                repeat=bool(request.repeat),
                timezone_name=request.timezone,
            )
        elif request.repeat is not None and not (request.every or request.cron_expr or request.run_at):
            raise ValueError("repeat requires schedule_time when legacy fields are not provided.")
        
        job = _cron_service.add_job(
            user_id=request.user_id or "cron",
            workspace_dir=_sanitize_workspace_dir_or_400(request.workspace_dir),
            command=request.command,
            every_raw=every_raw,
            cron_expr=cron_expr,
            timezone=request.timezone,
            run_at=run_at_ts,
            delete_after_run=delete_after_run,
            model=request.model,
            timeout_seconds=request.timeout_seconds,
            enabled=request.enabled,
            metadata=request.metadata or {},
        )
        return {"job": {**job.to_dict(), "running": job.job_id in _cron_service.running_jobs}}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/v1/cron/jobs/{job_id}")
async def get_cron_job(job_id: str):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Chron service not available.")
    job = _cron_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Chron job not found")
    return {"job": {**job.to_dict(), "running": job.job_id in _cron_service.running_jobs}}


@app.put("/api/v1/cron/jobs/{job_id}")
async def update_cron_job(job_id: str, request: CronJobUpdateRequest):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Chron service not available.")
    job = _cron_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Chron job not found")
    
    # Build updates dict, only including non-None values
    if request.repeat is not None and request.schedule_time is None:
        raise HTTPException(status_code=400, detail="repeat requires schedule_time for simplified updates.")
    updates: dict = {}
    effective_tz = request.timezone if request.timezone is not None else job.timezone
    if request.command is not None:
        updates["command"] = request.command
    if request.schedule_time is not None:
        every_raw, cron_expr, run_at_ts, delete_after_run = await _resolve_simplified_schedule_update_fields_with_agent(
            schedule_time=request.schedule_time,
            repeat=request.repeat,
            timezone_name=effective_tz,
            job=job,
        )
        # Scheduling modes are mutually exclusive.
        updates["every_seconds"] = 0
        updates["cron_expr"] = None
        updates["run_at"] = None
        if every_raw is not None:
            updates["every"] = every_raw
        if cron_expr is not None:
            updates["cron_expr"] = cron_expr
        if run_at_ts is not None:
            updates["run_at"] = run_at_ts
        updates["delete_after_run"] = delete_after_run
    if request.timeout_seconds is not None:
        updates["timeout_seconds"] = request.timeout_seconds
    if request.every is not None:
        updates["every"] = request.every
    if request.cron_expr is not None:
        updates["cron_expr"] = request.cron_expr
    if request.timezone is not None:
        updates["timezone"] = request.timezone
    if request.run_at is not None:
        updates["run_at"] = parse_run_at(request.run_at, timezone_name=effective_tz)
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
        return {"job": {**job.to_dict(), "running": job.job_id in _cron_service.running_jobs}}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/v1/cron/jobs/{job_id}")
async def delete_cron_job(job_id: str):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Chron service not available.")
    job = _cron_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Chron job not found")
    _cron_service.delete_job(job_id)
    return {"status": "deleted", "job_id": job_id}


@app.post("/api/v1/cron/jobs/{job_id}/run")
async def run_cron_job(job_id: str):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Chron service not available.")
    job = _cron_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Chron job not found")
    record = await _cron_service.run_job_now(job_id, reason="manual")
    return {"run": record.to_dict()}


@app.get("/api/v1/cron/jobs/{job_id}/runs")
async def list_cron_job_runs(job_id: str, limit: int = 200):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Chron service not available.")
    return {"runs": _cron_service.list_runs(job_id=job_id, limit=limit)}


@app.get("/api/v1/cron/runs")
async def list_cron_runs(limit: int = 200):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Chron service not available.")
    return {"runs": _cron_service.list_runs(limit=limit)}


@app.get("/api/v1/sessions/{session_id}")
async def get_session_info(session_id: str, request: Request):
    _require_session_api_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    session = get_session(session_id)
    if not session:
        _increment_metric("resume_attempts")
        gateway = get_gateway()
        try:
            session = await gateway.resume_session(session_id)
            session.metadata["user_id"] = session.user_id
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

    session.metadata.setdefault("user_id", session.user_id)

    return CreateSessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        workspace_dir=session.workspace_dir,
        metadata=session.metadata,
    )


@app.get("/api/v1/sessions/{session_id}/policy")
async def get_session_policy(session_id: str, request: Request):
    _require_session_api_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    policy = _session_policy(session)
    return {"session_id": session_id, "policy": policy}


@app.patch("/api/v1/sessions/{session_id}/policy")
async def patch_session_policy(session_id: str, payload: SessionPolicyPatchRequest, request: Request):
    _require_session_api_auth(request)
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
async def get_pending_gate(session_id: str, request: Request):
    _require_session_api_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    pending = _pending_gated_requests.get(session_id)
    return {"session_id": session_id, "pending": pending}


@app.post("/api/v1/sessions/{session_id}/resume")
async def resume_gated_request(session_id: str, payload: ResumeRequest, request: Request):
    _require_session_api_auth(request)
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
async def delete_session(session_id: str, request: Request):
    _require_session_api_auth(request)
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
    try:
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
    except HTTPException:
        raise
    except Exception:
        logger.exception("CRITICAL: Failed to list sessions")
        raise HTTPException(status_code=500, detail="Internal Server Error: check gateway logs")


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


@app.get("/api/v1/ops/calendar/events")
async def ops_calendar_events(
    request: Request,
    start: Optional[str] = None,
    end: Optional[str] = None,
    view: str = "week",
    source: str = "all",
    owner: Optional[str] = None,
    timezone_name: Optional[str] = None,
):
    _require_ops_auth(request)
    _scheduling_counter_inc("calendar_events_requests")
    started = time.perf_counter()
    tz_name = _calendar_timezone_or_default(timezone_name)
    start_ts, end_ts = _calendar_normalize_window(
        start=start,
        end=end,
        view=view,
        timezone_name=tz_name,
    )
    _calendar_cleanup_state()
    feed = _calendar_build_feed(
        start_ts=start_ts,
        end_ts=end_ts,
        timezone_name=tz_name,
        source_filter=source,
        owner=owner,
    )
    stasis_items = [item for item in _calendar_missed_events.values() if item.get("status") == "pending"]
    stasis_items.sort(key=lambda item: str(item.get("created_at") or ""))
    _scheduling_record_projection_sample(
        duration_ms=(time.perf_counter() - started) * 1000.0,
        events=feed["events"],
        always_running=feed["always_running"],
        stasis_count=len(stasis_items),
    )
    return {
        "timezone": tz_name,
        "view": view,
        "start_utc": datetime.fromtimestamp(start_ts, timezone.utc).isoformat(),
        "end_utc": datetime.fromtimestamp(end_ts, timezone.utc).isoformat(),
        "start_local": _calendar_local_iso(start_ts, tz_name),
        "end_local": _calendar_local_iso(end_ts, tz_name),
        "events": feed["events"],
        "always_running": feed["always_running"],
        "stasis_queue": stasis_items,
        "legend": {
            "heartbeat": "sky",
            "cron": "blue",
            "missed": "amber",
            "success": "emerald",
            "failed": "rose",
            "disabled": "slate",
        },
    }


@app.post("/api/v1/ops/calendar/events/{event_id}/action")
async def ops_calendar_event_action(
    request: Request,
    event_id: str,
    payload: CalendarEventActionRequest,
):
    _require_ops_auth(request)
    _scheduling_counter_inc("calendar_action_requests")
    source, source_ref, _scheduled_at = _calendar_parse_event_id(event_id)
    tz_name = _calendar_timezone_or_default(payload.timezone)
    result = await _calendar_apply_event_action(
        source=source,
        source_ref=source_ref,
        action=payload.action,
        event_id=event_id,
        run_at=payload.run_at,
        timezone_name=tz_name,
    )
    return {
        "event_id": event_id,
        "source": source,
        "source_ref": source_ref,
        **result,
    }


@app.post("/api/v1/ops/calendar/events/{event_id}/change-request")
async def ops_calendar_event_change_request(
    request: Request,
    event_id: str,
    payload: CalendarEventChangeRequest,
):
    _require_ops_auth(request)
    _scheduling_counter_inc("calendar_change_request_requests")
    tz_name = _calendar_timezone_or_default(payload.timezone)
    proposal = _calendar_create_change_proposal(
        event_id=event_id,
        instruction=payload.instruction,
        timezone_name=tz_name,
    )
    return {"proposal": proposal}


@app.post("/api/v1/ops/calendar/events/{event_id}/change-request/confirm")
async def ops_calendar_event_change_confirm(
    request: Request,
    event_id: str,
    payload: CalendarEventChangeConfirmRequest,
):
    _require_ops_auth(request)
    _scheduling_counter_inc("calendar_change_confirm_requests")
    proposal = _calendar_change_proposals.get(payload.proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if str(proposal.get("event_id")) != event_id:
        raise HTTPException(status_code=400, detail="Proposal does not match event")
    if not payload.approve:
        proposal["status"] = "rejected"
        proposal["resolved_at"] = datetime.now().isoformat()
        return {"status": "rejected", "proposal": proposal}

    operation = proposal.get("operation") or {}
    op_type = str(operation.get("type") or "").strip().lower()
    source = str(proposal.get("source") or "")
    source_ref = str(proposal.get("source_ref") or "")
    result: dict[str, Any]
    if op_type == "cron_set_enabled":
        if not _cron_service:
            raise HTTPException(status_code=503, detail="Chron service not available")
        updated = _cron_service.update_job(source_ref, {"enabled": bool(operation.get("enabled"))})
        result = {"job": updated.to_dict()}
    elif op_type == "cron_run_now":
        if not _cron_service:
            raise HTTPException(status_code=503, detail="Chron service not available")
        run = await _cron_service.run_job_now(source_ref, reason="calendar_change_request")
        result = {"run": run.to_dict()}
    elif op_type == "cron_set_interval":
        if not _cron_service:
            raise HTTPException(status_code=503, detail="Chron service not available")
        every_seconds = int(operation.get("every_seconds") or 0)
        if every_seconds <= 0:
            raise HTTPException(status_code=400, detail="Invalid interval")
        updated = _cron_service.update_job(source_ref, {"every_seconds": every_seconds, "run_at": None})
        result = {"job": updated.to_dict()}
    elif op_type == "cron_backfill_schedule":
        if not _cron_service:
            raise HTTPException(status_code=503, detail="Chron service not available")
        job = _cron_service.get_job(source_ref)
        if not job:
            raise HTTPException(status_code=404, detail="Chron job not found")
        run_at = float(operation.get("run_at") or 0.0)
        if run_at <= 0:
            raise HTTPException(status_code=400, detail="Invalid run_at")
        # Keep one-shot jobs on the same job_id when rescheduling so
        # calendar actions and run history stay correlated to the job
        # the user actually edited.
        if job.run_at is not None and bool(job.delete_after_run):
            updated = _cron_service.update_job(
                source_ref,
                {
                    "run_at": run_at,
                    "enabled": True,
                },
            )
            result = {
                "job": updated.to_dict(),
                "mode": "updated_existing_one_shot",
            }
        else:
            created = _cron_service.add_job(
                user_id=job.user_id,
                workspace_dir=job.workspace_dir,
                command=job.command,
                run_at=run_at,
                delete_after_run=True,
                enabled=True,
                metadata={**(job.metadata or {}), "backfill_for_job_id": source_ref},
            )
            result = {
                "job": created.to_dict(),
                "mode": "created_backfill_job",
            }
    elif op_type == "heartbeat_set_delivery":
        mode = str(operation.get("mode") or "last").strip().lower()
        if mode not in {"none", "last"}:
            raise HTTPException(status_code=400, detail="Unsupported heartbeat mode")
        applied = _calendar_apply_heartbeat_delivery_mode(source_ref, mode)
        result = {"heartbeat": applied}
    elif op_type == "heartbeat_run_now":
        if not _heartbeat_service:
            raise HTTPException(status_code=503, detail="Heartbeat service not available")
        _heartbeat_service.request_heartbeat_now(source_ref, reason="calendar_change_request")
        result = {"heartbeat": {"session_id": source_ref, "action": "run_now"}}
    elif op_type == "heartbeat_set_interval":
        every_seconds = int(operation.get("every_seconds") or 0)
        if every_seconds <= 0:
            raise HTTPException(status_code=400, detail="Invalid heartbeat interval")
        applied = _calendar_apply_heartbeat_interval(source_ref, every_seconds)
        result = {"heartbeat": applied}
    else:
        raise HTTPException(status_code=400, detail="Proposal operation cannot be applied")

    proposal["status"] = "applied"
    proposal["resolved_at"] = datetime.now().isoformat()
    proposal["result"] = result
    return {
        "status": "applied",
        "event_id": event_id,
        "source": source,
        "source_ref": source_ref,
        "proposal": proposal,
        "result": result,
    }


@app.get("/api/v1/ops/metrics/session-continuity")
async def ops_session_continuity_metrics(request: Request):
    _require_ops_auth(request)
    return {"metrics": _observability_metrics_snapshot()}


@app.get("/api/v1/ops/metrics/scheduling-runtime")
async def ops_scheduling_runtime_metrics(request: Request):
    _require_ops_auth(request)
    return {"metrics": _scheduling_runtime_metrics_snapshot()}


@app.get("/api/v1/ops/metrics/coder-vp")
async def ops_coder_vp_metrics(
    request: Request,
    vp_id: str = "vp.coder.primary",
    mission_limit: int = 50,
    event_limit: int = 200,
):
    _require_ops_auth(request)
    vp_identifier = (vp_id or "").strip()
    if not vp_identifier:
        raise HTTPException(status_code=400, detail="vp_id is required")

    clamped_mission_limit = max(1, min(int(mission_limit), 500))
    clamped_event_limit = max(1, min(int(event_limit), 1000))
    return _vp_metrics_snapshot(
        vp_id=vp_identifier,
        mission_limit=clamped_mission_limit,
        event_limit=clamped_event_limit,
    )


@app.get("/api/v1/ops/scheduling/events")
async def ops_scheduling_events(
    request: Request,
    since_seq: int = 0,
    limit: int = 500,
):
    _require_ops_auth(request)
    _scheduling_counter_inc("push_replay_requests")
    events = _scheduling_event_bus.snapshot(since_seq=max(0, int(since_seq)), limit=max(1, min(int(limit), 5000)))
    metrics = _scheduling_runtime_metrics_snapshot()
    projection_state = metrics.get("projection_state", {}) if isinstance(metrics, dict) else {}
    return {
        "events": events,
        "projection_version": int(projection_state.get("version", 0) or 0),
        "projection_last_event_seq": int(projection_state.get("last_event_seq", 0) or 0),
        "event_bus_seq": int((_scheduling_event_bus.info() or {}).get("seq", 0) or 0),
    }


@app.get("/api/v1/ops/scheduling/stream")
async def ops_scheduling_stream(
    request: Request,
    since_seq: int = 0,
    heartbeat_seconds: int = 20,
    limit: int = 500,
    once: bool = False,
    ops_token: Optional[str] = None,
):
    _require_ops_auth(request, token_override=ops_token)
    if not SCHED_PUSH_ENABLED:
        raise HTTPException(status_code=503, detail="Scheduling push stream disabled.")
    since = max(0, int(since_seq))
    max_items = max(1, min(int(limit), 5000))
    heartbeat_wait = max(2, min(int(heartbeat_seconds), 60))

    async def event_gen():
        nonlocal since
        _scheduling_counter_inc("push_stream_connects")
        emitted = 0
        try:
            while True:
                if await request.is_disconnected():
                    break
                events = await _scheduling_event_bus.wait_for_events(
                    since_seq=since,
                    timeout_seconds=float(heartbeat_wait),
                    limit=max_items,
                )
                metrics = _scheduling_runtime_metrics_snapshot()
                projection_state = metrics.get("projection_state", {}) if isinstance(metrics, dict) else {}
                projection_version = int(projection_state.get("version", 0) or 0)
                projection_last_event_seq = int(projection_state.get("last_event_seq", 0) or 0)
                if events:
                    for event in events:
                        seq = int(event.get("seq", 0) or 0)
                        if seq > since:
                            since = seq
                        payload = {
                            "kind": "event",
                            "event": event,
                            "projection_version": projection_version,
                            "projection_last_event_seq": projection_last_event_seq,
                        }
                        _scheduling_counter_inc("push_stream_event_payloads")
                        yield f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
                        emitted += 1
                        if once and emitted >= 1:
                            return
                else:
                    keepalive = {
                        "kind": "heartbeat",
                        "seq": since,
                        "projection_version": projection_version,
                        "projection_last_event_seq": projection_last_event_seq,
                    }
                    _scheduling_counter_inc("push_stream_keepalives")
                    yield f"data: {json.dumps(keepalive, separators=(',', ':'))}\n\n"
                    emitted += 1
                    if once and emitted >= 1:
                        return
        finally:
            _scheduling_counter_inc("push_stream_disconnects")

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_gen(), media_type="text/event-stream", headers=headers)


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
    try:
        return {"skills": _load_skill_catalog()}
    except Exception:
        logger.exception("CRITICAL: Failed to list skills")
        raise HTTPException(status_code=500, detail="Internal Server Error: check gateway logs")


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


def _remote_sync_enabled(config: dict[str, Any]) -> bool:
    section = config.get("remote_debug", {})
    if not isinstance(section, dict):
        return False
    return bool(section.get("local_workspace_sync_enabled", False))


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


@app.get("/api/v1/ops/remote-sync")
async def ops_remote_sync_get(request: Request):
    _require_ops_auth(request)
    config = load_ops_config()
    return {
        "enabled": _remote_sync_enabled(config),
        "default_enabled": False,
        "config_key": "remote_debug.local_workspace_sync_enabled",
        "base_hash": ops_config_hash(config),
    }


@app.post("/api/v1/ops/remote-sync")
async def ops_remote_sync_set(request: Request, payload: OpsRemoteSyncUpdateRequest):
    _require_ops_auth(request)
    config = load_ops_config()
    section = config.get("remote_debug", {})
    if not isinstance(section, dict):
        section = {}
    section["local_workspace_sync_enabled"] = bool(payload.enabled)
    config["remote_debug"] = section
    write_ops_config(config)
    updated = load_ops_config()
    return {
        "enabled": _remote_sync_enabled(updated),
        "default_enabled": False,
        "config_key": "remote_debug.local_workspace_sync_enabled",
        "base_hash": ops_config_hash(updated),
    }


@app.post("/api/v1/ops/workspaces/purge")
async def ops_workspaces_purge(request: Request, confirm: bool = False):
    """
    Purge all session workspaces and artifacts from the VPS filesystem.
    This frees up disk space and prevents old data from syncing to local environments.
    """
    _require_ops_auth(request)
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Confirmation required. Pass ?confirm=true to purge all workspaces and artifacts.",
        )

    deleted_workspaces_count = 0
    deleted_artifacts_count = 0
    errors = []

    # 1. Purge Workspaces
    if WORKSPACES_DIR.exists():
        for item in WORKSPACES_DIR.iterdir():
            if item.is_dir():
                # Safety check: simplistic heuristic to avoid deleting non-session dirs if any exist
                # But "Purge All" implies all. We'll trust the WORKSPACES_DIR is dedicated.
                try:
                    shutil.rmtree(item)
                    deleted_workspaces_count += 1
                except Exception as e:
                    errors.append(f"Failed to delete workspace {item.name}: {e}")

    # 2. Purge Artifacts
    artifacts_dir = resolve_artifacts_dir()
    if artifacts_dir.exists():
        for item in artifacts_dir.iterdir():
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                deleted_artifacts_count += 1
            except Exception as e:
                errors.append(f"Failed to delete artifact item {item.name}: {e}")

    logger.warning(
        f"🧹 PURGE COMPLETE: Deleted {deleted_workspaces_count} workspaces and {deleted_artifacts_count} artifact items."
    )

    return {
        "deleted_workspaces": deleted_workspaces_count,
        "deleted_artifacts_items": deleted_artifacts_count,
        "errors": errors,
        "status": "completed" if not errors else "completed_with_errors",
    }


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


@app.get("/api/v1/ops/work-threads")
async def ops_work_threads_list(
    request: Request,
    status: Optional[str] = None,
    session_id: Optional[str] = None,
):
    _require_ops_auth(request)
    return {"threads": list_work_threads(status=status, session_id=session_id)}


@app.post("/api/v1/ops/work-threads")
async def ops_work_threads_upsert(request: Request, payload: OpsWorkThreadUpsertRequest):
    _require_ops_auth(request)
    try:
        record = upsert_work_thread(payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"thread": record}


@app.post("/api/v1/ops/work-threads/decide")
async def ops_work_threads_decide(request: Request, payload: OpsWorkThreadDecisionRequest):
    _require_ops_auth(request)
    decided_by = (
        str(request.headers.get("x-ua-dashboard-owner") or "").strip()
        or "dashboard_operator"
    )
    try:
        record = append_work_thread_decision(
            session_id=payload.session_id,
            decision=payload.decision,
            note=payload.note,
            decided_by=decided_by,
            metadata=payload.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"thread": record}


@app.patch("/api/v1/ops/work-threads/{thread_id}")
async def ops_work_threads_update(
    request: Request,
    thread_id: str,
    payload: OpsWorkThreadUpdateRequest,
):
    _require_ops_auth(request)
    record = update_work_thread(thread_id, payload.model_dump(exclude_none=True))
    if record is None:
        raise HTTPException(status_code=404, detail="Work thread not found")
    return {"thread": record}


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
    if not await _require_session_ws_auth(websocket):
        return
    try:
        session_id = validate_session_id(session_id)
    except ValueError:
        await websocket.close(code=4000, reason="Invalid session id format")
        return
    connection_id = f"gw_{session_id}_{time.time()}"
    # Register connection with session_id
    await manager.connect(connection_id, websocket, session_id)
    
    gateway = get_gateway()
    session = get_session(session_id)

    if not session:
        try:
            session = await gateway.resume_session(session_id)
            # Success - count metrics
            _increment_metric("resume_attempts")
            _increment_metric("resume_successes")
            _increment_metric("ws_attach_attempts")
            
            session.metadata["user_id"] = session.user_id
            store_session(session)
            if _heartbeat_service:
                _heartbeat_service.register_session(session)
        except ValueError:
            # Session Not Found - do NOT count as continuity failure
            await websocket.close(code=4004, reason="Session not found")
            manager.disconnect(connection_id, session_id)
            return
        except Exception:
            # System error - count as failure
            _increment_metric("resume_attempts")
            _increment_metric("resume_failures")
            _increment_metric("ws_attach_attempts")
            _increment_metric("ws_attach_failures")
            logger.exception(f"Failed to resume session {session_id}")
            await websocket.close(code=1011, reason="Internal Error")
            manager.disconnect(connection_id, session_id)
            return
    else:
        # Session exists - count attempt
        _increment_metric("ws_attach_attempts")

    session.metadata.setdefault("user_id", session.user_id)

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
                    request_source = _normalize_run_source(request_metadata.get("source"))
                    request_metadata["source"] = request_source

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
                    mission_tracker = MissionGuardrailTracker(build_mission_contract(user_input))
                    _increment_session_active_runs(session_id, run_source=request_source)

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
                                    if isinstance(event.data, dict):
                                        mission_tracker.record_tool_call(str(event.data.get("name") or ""))
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

                            if execution_duration_seconds <= 0:
                                execution_duration_seconds = round(time.time() - execution_start_ts, 3)
                            goal_satisfaction = mission_tracker.evaluate()
                            completion_summary = {
                                "tool_calls": tool_call_count,
                                "duration_seconds": execution_duration_seconds,
                                "goal_satisfaction": goal_satisfaction,
                            }

                            # Generate checkpoint for next session/follow-up
                            try:
                                from universal_agent.session_checkpoint import SessionCheckpointGenerator
                                workspace_path = Path(session.workspace_dir)
                                generator = SessionCheckpointGenerator(workspace_path)
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

                            if not bool(goal_satisfaction.get("passed")):
                                goal_message = "Mission requirements were not satisfied; required tool checkpoints are missing."
                                _add_notification(
                                    kind="assistance_needed",
                                    title="Mission Guardrail Blocked Completion",
                                    message=goal_message,
                                    session_id=session.session_id,
                                    severity="error",
                                    requires_action=True,
                                    metadata={"goal_satisfaction": goal_satisfaction},
                                )
                                await manager.broadcast(
                                    session_id,
                                    {
                                        "type": "status",
                                        "data": {
                                            "status": "goal_satisfaction_failed",
                                            "turn_id": turn_id,
                                            "goal_satisfaction": goal_satisfaction,
                                        },
                                        "timestamp": datetime.now().isoformat(),
                                    },
                                )
                                await manager.broadcast(
                                    session_id,
                                    {
                                        "type": "error",
                                        "data": {
                                            "message": goal_message,
                                            "goal_satisfaction": goal_satisfaction,
                                        },
                                        "timestamp": datetime.now().isoformat(),
                                    },
                                )
                                await manager.broadcast(
                                    session_id,
                                    {
                                        "type": "query_complete",
                                        "data": {
                                            "turn_id": turn_id,
                                            "goal_satisfaction": goal_satisfaction,
                                            "completed": False,
                                        },
                                        "timestamp": datetime.now().isoformat(),
                                    },
                                )
                                async with _session_turn_lock(session_id):
                                    _finalize_turn(
                                        session_id,
                                        turn_id,
                                        TURN_STATUS_FAILED,
                                        error_message=goal_message,
                                        completion=completion_summary,
                                    )
                                return

                            _add_notification(
                                kind="mission_complete",
                                title="Mission Completed",
                                message="Session completed execution successfully.",
                                session_id=session.session_id,
                                severity="info",
                                metadata={
                                    "tool_calls": tool_call_count,
                                    "duration_seconds": execution_duration_seconds,
                                    "goal_satisfaction": goal_satisfaction,
                                },
                            )

                            await manager.broadcast(
                                session_id,
                                {
                                    "type": "query_complete",
                                    "data": {
                                        "turn_id": turn_id,
                                        "goal_satisfaction": goal_satisfaction,
                                        "completed": True,
                                    },
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
                                _finalize_turn(
                                    session_id,
                                    turn_id,
                                    TURN_STATUS_COMPLETED,
                                    completion=completion_summary,
                                )
                        except asyncio.CancelledError:
                            logger.warning("Execution cancelled for session %s turn %s", session_id, turn_id)
                            await manager.broadcast(
                                session_id,
                                {
                                    "type": "status",
                                    "data": {
                                        "status": "turn_cancelled",
                                        "turn_id": turn_id,
                                        "message": "Execution cancelled.",
                                    },
                                    "timestamp": datetime.now().isoformat(),
                                },
                            )
                            await manager.broadcast(
                                session_id,
                                {
                                    "type": "query_complete",
                                    "data": {"turn_id": turn_id, "cancelled": True},
                                    "timestamp": datetime.now().isoformat(),
                                },
                            )
                            async with _session_turn_lock(session_id):
                                _finalize_turn(
                                    session_id,
                                    turn_id,
                                    TURN_STATUS_CANCELLED,
                                    error_message="cancelled",
                                    completion={
                                        "tool_calls": tool_call_count,
                                        "duration_seconds": round(time.time() - execution_start_ts, 3),
                                    },
                                )
                            raise
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
                                _finalize_turn(
                                    session_id,
                                    turn_id,
                                    TURN_STATUS_FAILED,
                                    error_message=str(e),
                                    completion={
                                        "tool_calls": tool_call_count,
                                        "duration_seconds": round(time.time() - execution_start_ts, 3),
                                    },
                                )
                        finally:
                            _decrement_session_active_runs(session_id, run_source=request_source)
                            if _heartbeat_service:
                                _heartbeat_service.busy_sessions.discard(session.session_id)

                    execution_task = asyncio.create_task(run_execution(admitted_turn_id))
                    _register_execution_task(session_id, execution_task)
                
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

    except WebSocketDisconnect as exc:
        _record_ws_close(
            getattr(exc, "code", None),
            getattr(exc, "reason", None),
            endpoint="gateway_session_stream",
        )
        manager.disconnect(connection_id, session_id)
        logger.info(f"Gateway WebSocket disconnected: {connection_id}")
    except Exception as e:
        _record_ws_close(None, str(e), endpoint="gateway_session_stream")
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

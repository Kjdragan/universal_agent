from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from universal_agent.durable.db import (
    connect_runtime_db,
    get_coder_vp_db_path,
    get_runtime_db_path,
    get_vp_db_path,
)
from universal_agent.durable.state import get_vp_session
from universal_agent.durable.migrations import ensure_schema
from universal_agent.feature_flags import (
    coder_vp_display_name,
    coder_vp_id,
    dynamic_mcp_enabled,
    sdk_session_history_enabled,
    vp_dispatch_mode,
    vp_require_live_worker_for_dispatch,
    vp_explicit_intent_require_external,
    vp_external_dispatch_enabled,
    vp_worker_heartbeat_stale_seconds,
    vp_worker_recovery_poll_seconds,
    vp_worker_recovery_wait_seconds,
)
from universal_agent.memory.paths import resolve_shared_memory_workspace
from universal_agent.timeout_policy import (
    gateway_http_timeout_seconds,
    websocket_connect_kwargs,
)
from universal_agent.workspace import seed_workspace_bootstrap
from universal_agent.constants import TODO_EXECUTION_DISALLOWED_TOOLS
from universal_agent.vp import (
    CoderVPRuntime,
    MissionDispatchRequest,
    dispatch_mission_with_retry,
)
from universal_agent.sdk import session_history_adapter

try:
    from universal_agent.agent_core import AgentEvent, EventType
except Exception:  # pragma: no cover - import safety for tooling
    AgentEvent = Any  # type: ignore
    EventType = Any  # type: ignore

try:
    import logfire
except ImportError:
    logfire = None  # type: ignore

# Import ProcessTurnAdapter for unified execution engine
try:
    from universal_agent.execution_engine import ProcessTurnAdapter, EngineConfig
    EXECUTION_ENGINE_AVAILABLE = True
except ImportError:
    EXECUTION_ENGINE_AVAILABLE = False
    ProcessTurnAdapter = None  # type: ignore
    EngineConfig = None  # type: ignore

# Legacy import for backward compatibility (will be deprecated)
from universal_agent.api.agent_bridge import AgentBridge

# Optional dependencies for ExternalGateway
try:
    import httpx
    import websockets
    EXTERNAL_DEPS_AVAILABLE = True
except ImportError:
    EXTERNAL_DEPS_AVAILABLE = False
    httpx = None  # type: ignore
    websockets = None  # type: ignore

logger = logging.getLogger(__name__)

_EXPLICIT_GENERAL_VP_PATTERNS = (
    re.compile(r"\bgeneral(?:ist)?\s+vp\b", re.IGNORECASE),
    re.compile(r"\bvp\s+general(?:ist)?\b", re.IGNORECASE),
    re.compile(r"\bvp\s+general(?:ist)?\s+agent\b", re.IGNORECASE),
    re.compile(r"\bvp\.general\.primary\b", re.IGNORECASE),
    re.compile(r"\buse\s+(?:the\s+)?general(?:ist)?\s+vp\b", re.IGNORECASE),
    re.compile(r"\buse\s+(?:the\s+)?vp\s+general(?:ist)?\b", re.IGNORECASE),
)
_EXPLICIT_CODER_VP_PATTERNS = (
    re.compile(r"\bcoder\s+vp\b", re.IGNORECASE),
    re.compile(r"\bvp\s+coder\b", re.IGNORECASE),
    re.compile(r"\bvp\s+coder\s+agent\b", re.IGNORECASE),
    re.compile(r"\bcodie\b", re.IGNORECASE),
    re.compile(r"\bvp\.coder\.primary\b", re.IGNORECASE),
    re.compile(r"\buse\s+(?:the\s+)?coder\s+vp\b", re.IGNORECASE),
    re.compile(r"\buse\s+(?:the\s+)?vp\s+coder\b", re.IGNORECASE),
)

_PROMPT_INFERRED_VP_BLOCKED_SOURCES = {
    "cron",
    "webhook",
    "heartbeat",
    "heartbeat_synthetic",
    "task_run",
    "email_hook",
    "todo_dispatcher",
}
_PROMPT_INFERRED_VP_BLOCKED_RUN_KINDS = {
    "heartbeat",
    "heartbeat_email_wake",
    "heartbeat_cron_wake",
    "todo_execution",
    "email_triage",
    "hook",
    "task_run",
    "cron_job_dispatch",
}


def _metadata_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        raw = value.strip().lower()
        if not raw:
            return default
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _infer_explicit_vp_target(user_input: str) -> tuple[Optional[str], Optional[str]]:
    text = str(user_input or "").strip()
    if not text:
        return None, None
    if any(pattern.search(text) for pattern in _EXPLICIT_GENERAL_VP_PATTERNS):
        return "vp.general.primary", "general_task"
    if any(pattern.search(text) for pattern in _EXPLICIT_CODER_VP_PATTERNS):
        return coder_vp_id(), "coding_task"
    return None, None


def _allow_prompt_inferred_vp_routing(*, request_source: Any, request_run_kind: Any) -> bool:
    source = str(request_source or "").strip().lower()
    run_kind = str(request_run_kind or "").strip().lower()
    if source in _PROMPT_INFERRED_VP_BLOCKED_SOURCES:
        return False
    if run_kind in _PROMPT_INFERRED_VP_BLOCKED_RUN_KINDS:
        return False
    if run_kind.startswith("heartbeat"):
        return False
    return True


def _extra_disallowed_tools_for_request(metadata: dict[str, Any]) -> list[str]:
    run_kind = str(metadata.get("run_kind") or "").strip().lower()
    if run_kind == "todo_execution":
        return list(TODO_EXECUTION_DISALLOWED_TOOLS)
    return []


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass
class GatewaySession:
    session_id: str
    user_id: str
    workspace_dir: str
    metadata: dict[str, Any] = field(default_factory=dict)
    pending_inputs: dict[str, asyncio.Future[str]] = field(default_factory=dict, repr=False)


@dataclass
class GatewaySessionSummary:
    session_id: str
    workspace_dir: str
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GatewayRequest:
    user_input: str
    force_complex: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GatewayResult:
    response_text: str
    tool_calls: int = 0
    execution_time: float = 0.0
    code_execution_used: bool = False
    trace_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # Alias for backward compatibility with telegram_formatter expectation
    @property
    def execution_time_seconds(self) -> float:
        return self.execution_time


class Gateway:
    async def create_session(
        self,
        user_id: str,
        workspace_dir: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> GatewaySession:
        raise NotImplementedError

    async def resume_session(self, session_id: str) -> GatewaySession:
        raise NotImplementedError

    async def execute(
        self, session: GatewaySession, request: GatewayRequest
    ) -> AsyncIterator[AgentEvent]:
        raise NotImplementedError

    async def run_query(
        self, session: GatewaySession, request: GatewayRequest
    ) -> GatewayResult:
        raise NotImplementedError

    def list_sessions(self) -> list[GatewaySessionSummary]:
        raise NotImplementedError

    def list_live_sessions(self) -> list[GatewaySessionSummary]:
        raise NotImplementedError

    async def get_session_mcp_status(self, session_id: str) -> dict[str, Any]:
        raise NotImplementedError

    async def add_session_mcp_server(
        self, session_id: str, server_name: str, server_config: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def remove_session_mcp_server(self, session_id: str, server_name: str) -> dict[str, Any]:
        raise NotImplementedError


class InProcessGateway(Gateway):
    """
    In-process gateway that uses the unified execution engine (ProcessTurnAdapter).
    """
    
    def __init__(
        self,
        use_legacy_bridge: bool = False,
        hooks: Optional[dict] = None,
        workspace_base: Optional[Path] = None,
    ):
        self._use_legacy = use_legacy_bridge or not EXECUTION_ENGINE_AVAILABLE
        self._hooks = hooks
        self._workspace_base = workspace_base or Path("AGENT_RUN_WORKSPACES")
        # _execution_lock serializes create_session / resume_session which mutate
        # shared adapter dicts.  Per-session locks (_session_exec_locks) allow
        # different sessions to run concurrently inside execute().
        self._execution_lock = asyncio.Lock()
        # Per-session locks: each session can execute independently.
        self._session_exec_locks: dict[str, asyncio.Lock] = {}
        # Dedicated lock for CODER VP shared adapter/session (single-lane).
        self._coder_vp_lock = asyncio.Lock()
        self._execution_runtime: dict[str, Any] = {
            "lock_waiters_current": 0,
            "lock_waiters_peak": 0,
            "lock_wait_seconds_total": 0.0,
            "lock_wait_seconds_max": 0.0,
            "lock_hold_seconds_total": 0.0,
            "lock_hold_seconds_max": 0.0,
            "lock_acquire_count": 0,
            "lock_in_flight": 0,
            "lock_wait_last_seconds": 0.0,
            "lock_hold_last_seconds": 0.0,
            "lock_last_operation": None,
        }
        
        # Legacy bridge (deprecated)
        self._bridge: Optional[AgentBridge] = None
        
        # New unified engine adapters
        self._adapters: dict[str, ProcessTurnAdapter] = {}
        self._sessions: dict[str, GatewaySession] = {}
        self._coder_vp_adapter: Optional[ProcessTurnAdapter] = None
        self._coder_vp_session: Optional[GatewaySession] = None
        self._coder_vp_lease_owner = "simone-control-plane"
        self._runtime_db_conn = None
        self._coder_vp_db_conn = None
        self._vp_db_conn = None
        self._coder_vp_runtime: Optional[CoderVPRuntime] = None

        # Session reaper state (started lazily via start_reaper())
        self._reaper_running: bool = False
        self._reaper_task: Optional[asyncio.Task] = None

        try:
            self._runtime_db_conn = connect_runtime_db(get_runtime_db_path())
            ensure_schema(self._runtime_db_conn)
            # Keep CODIE lane telemetry isolated from Simone runtime queue/checkpoint
            # writes to prevent cross-lane sqlite lock contention.
            self._coder_vp_db_conn = connect_runtime_db(get_coder_vp_db_path())
            ensure_schema(self._coder_vp_db_conn)
            # Dedicated VP mission ledger DB for external primary workers.
            self._vp_db_conn = connect_runtime_db(get_vp_db_path())
            ensure_schema(self._vp_db_conn)
            self._coder_vp_runtime = CoderVPRuntime(
                conn=self._coder_vp_db_conn,
                workspace_base=self._workspace_base,
            )
        except Exception:
            if self._runtime_db_conn is not None:
                try:
                    self._runtime_db_conn.close()
                except Exception:
                    pass
            if self._coder_vp_db_conn is not None:
                try:
                    self._coder_vp_db_conn.close()
                except Exception:
                    pass
            if self._vp_db_conn is not None:
                try:
                    self._vp_db_conn.close()
                except Exception:
                    pass
            self._runtime_db_conn = None
            self._coder_vp_db_conn = None
            self._vp_db_conn = None
            self._coder_vp_runtime = None

        if self._use_legacy:
            self._bridge = AgentBridge(hooks=hooks)

    def register_existing_session(self, session: GatewaySession) -> GatewaySession:
        """Register an externally provisioned session with the gateway.

        Daemon sessions are created outside the normal gateway create/resume flow,
        but they still need to participate in execution, listing, and adapter
        lifecycle management.
        """
        self._sessions[session.session_id] = session
        return session

    def _get_session_exec_lock(self, session_id: str) -> asyncio.Lock:
        """Return (creating if needed) the per-session execution lock."""
        if session_id not in self._session_exec_locks:
            self._session_exec_locks[session_id] = asyncio.Lock()
        return self._session_exec_locks[session_id]

    @asynccontextmanager
    async def _timed_execution_lock(self, operation: str) -> AsyncIterator[None]:
        started_wait = time.monotonic()
        self._execution_runtime["lock_waiters_current"] = int(
            self._execution_runtime.get("lock_waiters_current", 0)
        ) + 1
        self._execution_runtime["lock_waiters_peak"] = max(
            int(self._execution_runtime.get("lock_waiters_peak", 0)),
            int(self._execution_runtime["lock_waiters_current"]),
        )

        acquired = False
        try:
            await self._execution_lock.acquire()
            acquired = True
            wait_seconds = max(0.0, time.monotonic() - started_wait)
            self._execution_runtime["lock_waiters_current"] = max(
                0, int(self._execution_runtime.get("lock_waiters_current", 0)) - 1
            )
            self._execution_runtime["lock_wait_last_seconds"] = wait_seconds
            self._execution_runtime["lock_wait_seconds_total"] = float(
                self._execution_runtime.get("lock_wait_seconds_total", 0.0)
            ) + wait_seconds
            self._execution_runtime["lock_wait_seconds_max"] = max(
                float(self._execution_runtime.get("lock_wait_seconds_max", 0.0)),
                wait_seconds,
            )
            self._execution_runtime["lock_acquire_count"] = int(
                self._execution_runtime.get("lock_acquire_count", 0)
            ) + 1
            self._execution_runtime["lock_in_flight"] = int(
                self._execution_runtime.get("lock_in_flight", 0)
            ) + 1
            self._execution_runtime["lock_last_operation"] = operation

            started_hold = time.monotonic()
            try:
                yield
            finally:
                hold_seconds = max(0.0, time.monotonic() - started_hold)
                self._execution_runtime["lock_hold_last_seconds"] = hold_seconds
                self._execution_runtime["lock_hold_seconds_total"] = float(
                    self._execution_runtime.get("lock_hold_seconds_total", 0.0)
                ) + hold_seconds
                self._execution_runtime["lock_hold_seconds_max"] = max(
                    float(self._execution_runtime.get("lock_hold_seconds_max", 0.0)),
                    hold_seconds,
                )
                self._execution_runtime["lock_in_flight"] = max(
                    0, int(self._execution_runtime.get("lock_in_flight", 0)) - 1
                )
                self._execution_lock.release()
        finally:
            if not acquired:
                self._execution_runtime["lock_waiters_current"] = max(
                    0, int(self._execution_runtime.get("lock_waiters_current", 0)) - 1
                )

    def execution_runtime_snapshot(self) -> dict[str, Any]:
        snapshot = dict(self._execution_runtime)
        lock_acquire_count = int(snapshot.get("lock_acquire_count", 0))
        if lock_acquire_count > 0:
            snapshot["lock_wait_seconds_avg"] = float(
                snapshot.get("lock_wait_seconds_total", 0.0)
            ) / lock_acquire_count
            snapshot["lock_hold_seconds_avg"] = float(
                snapshot.get("lock_hold_seconds_total", 0.0)
            ) / lock_acquire_count
        else:
            snapshot["lock_wait_seconds_avg"] = 0.0
            snapshot["lock_hold_seconds_avg"] = 0.0
        snapshot["lock_locked"] = bool(self._execution_lock.locked())
        snapshot["sessions_in_flight"] = sum(
            1 for lk in self._session_exec_locks.values() if lk.locked()
        )
        snapshot["sessions_with_lock"] = len(self._session_exec_locks)
        return snapshot

    def get_coder_vp_db_conn(self) -> Any:
        return self._coder_vp_db_conn

    def get_vp_db_conn(self) -> Any:
        return self._vp_db_conn

    async def create_session(
        self,
        user_id: str,
        workspace_dir: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> GatewaySession:
        async with self._timed_execution_lock("create_session"):
            if self._use_legacy:
                return await self._create_session_legacy(user_id, workspace_dir)
        
            # === NEW UNIFIED PATH ===
            # Generate session ID
            resolved_session_id = str(session_id or "").strip()
            if not resolved_session_id:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                resolved_session_id = f"session_{timestamp}_{uuid.uuid4().hex[:8]}"
            
            # Create workspace directory
            if workspace_dir:
                workspace_path = Path(workspace_dir).resolve()
            else:
                workspace_path = self._workspace_base / resolved_session_id
            
            workspace_path.mkdir(parents=True, exist_ok=True)
            (workspace_path / "work_products").mkdir(exist_ok=True)
            bootstrap_result = seed_workspace_bootstrap(str(workspace_path))
            
            # Preserve explicit session identity when provided; otherwise keep
            # the historical "workspace name becomes session id" behavior.
            if workspace_dir and not str(session_id or "").strip():
                resolved_session_id = workspace_path.name
            
            # Create adapter with unified engine config
            config = EngineConfig(
                workspace_dir=str(workspace_path),
                user_id=user_id,
            )
            adapter = ProcessTurnAdapter(config)
            
            # Initialize adapter
            await adapter.initialize()
            
            # Store adapter and session
            self._adapters[resolved_session_id] = adapter
            
            config_metadata = getattr(config, "metadata", {})
            
            session = GatewaySession(
                session_id=resolved_session_id,
                user_id=user_id,
                workspace_dir=str(workspace_path),
                metadata={
                    "engine": "process_turn",
                    "logfire_enabled": bool(os.getenv("LOGFIRE_TOKEN")),
                    "workspace_bootstrap": bootstrap_result,
                    "created_at": time.time(),
                    "last_activity_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            self._sessions[resolved_session_id] = session
            
            return session

    async def _create_session_legacy(
        self, user_id: str, workspace_dir: Optional[str] = None
    ) -> GatewaySession:
        """Legacy session creation using AgentBridge (deprecated)."""
        if not self._bridge:
            self._bridge = AgentBridge(hooks=self._hooks)
        
        session_info = await self._bridge.create_session(
            user_id=user_id, workspace_dir=workspace_dir
        )
        bootstrap_result = seed_workspace_bootstrap(session_info.workspace)
        metadata = {
            "session_url": session_info.session_url,
            "logfire_enabled": session_info.logfire_enabled,
            "engine": "agent_bridge_legacy",
            "workspace_bootstrap": bootstrap_result,
            "created_at": time.time(),
            "last_activity_at": datetime.now(timezone.utc).isoformat(),
        }
        if workspace_dir and workspace_dir != session_info.workspace:
            metadata["requested_workspace_dir"] = workspace_dir
        return GatewaySession(
            session_id=session_info.session_id,
            user_id=session_info.user_id,
            workspace_dir=session_info.workspace,
            metadata=metadata,
        )

    async def resume_session(self, session_id: str) -> GatewaySession:
        async with self._timed_execution_lock("resume_session"):
            if self._use_legacy:
                return await self._resume_session_legacy(session_id)
            return await self._resume_session_new(session_id)

    def _read_token_usage_from_trace(self, workspace_path: Path) -> dict:
        """Helper to read token usage from trace.json safely."""
        try:
            trace_path = workspace_path / "trace.json"
            if trace_path.exists():
                # Read specific fields efficiently if possible, or just load
                # Given trace.json can be large, we might want to optimize this later
                # For now, just try to read it
                with open(trace_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("token_usage", {})
        except Exception:
            pass
        return {}

    async def _resume_session_new(self, session_id: str) -> GatewaySession:
        """Resume a session on the unified engine path. Caller must hold _execution_lock."""
        # === NEW UNIFIED PATH ===
        from universal_agent.identity import resolve_user_id
        from universal_agent.run_catalog import RunCatalogService

        session = self._sessions.get(session_id)
        if session is None:
            # Try to find workspace on disk using the legacy "workspace dir ==
            # session_id" convention used by ordinary interactive sessions.
            workspace_path = self._workspace_base / session_id
            if not workspace_path.exists():
                raise ValueError(f"Unknown session_id: {session_id}")
            session = GatewaySession(
                session_id=session_id,
                user_id=resolve_user_id(),
                workspace_dir=str(workspace_path),
                metadata={},
            )

        if session_id in self._adapters:
            self._sessions[session_id] = session
            return session

        workspace_path = Path(str(session.workspace_dir or "")).resolve()
        if not workspace_path.exists():
            raise ValueError(f"Unknown session_id: {session_id}")

        config = EngineConfig(
            workspace_dir=str(workspace_path),
            user_id=str(session.user_id or resolve_user_id() or "unknown"),
        )
        adapter = ProcessTurnAdapter(config)
        await adapter.initialize()

        self._adapters[session_id] = adapter

        metadata = dict(session.metadata or {})
        metadata.setdefault("engine", "process_turn")
        metadata["resumed"] = True
        metadata["usage"] = self._read_token_usage_from_trace(workspace_path)
        active_run_id = str(metadata.get("active_run_id") or metadata.get("run_id") or "").strip()
        if not active_run_id:
            latest_run = RunCatalogService().find_latest_run_for_provider_session(session_id)
            if latest_run:
                latest_run_id = str(latest_run.get("run_id") or "").strip()
                latest_workspace_dir = str(latest_run.get("workspace_dir") or "").strip()
                if latest_run_id:
                    metadata["active_run_id"] = latest_run_id
                    metadata.setdefault("run_id", latest_run_id)
                if latest_workspace_dir:
                    metadata["active_run_workspace"] = latest_workspace_dir
        session.user_id = config.user_id or session.user_id or "unknown"
        session.workspace_dir = str(workspace_path)
        session.metadata = metadata
        self._sessions[session_id] = session

        return session

    async def _resume_session_legacy(self, session_id: str) -> GatewaySession:
        """Legacy session resume using AgentBridge (deprecated)."""
        if not self._bridge:
            self._bridge = AgentBridge(hooks=self._hooks)
        
        session_info = await self._bridge.resume_session(session_id)
        if not session_info:
            raise ValueError(f"Unknown session_id: {session_id}")
        return GatewaySession(
            session_id=session_info.session_id,
            user_id=session_info.user_id,
            workspace_dir=session_info.workspace,
            metadata={
                "session_url": session_info.session_url,
                "logfire_enabled": session_info.logfire_enabled,
                "engine": "agent_bridge_legacy",
            },
        )

    def _apply_request_config(
        self,
        adapter: ProcessTurnAdapter,
        request: GatewayRequest,
        *,
        run_source_override: Optional[str] = None,
        extra_metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        metadata = dict(request.metadata or {})
        if extra_metadata:
            metadata.update(extra_metadata)

        adapter.config.force_complex = request.force_complex
        run_source = run_source_override or metadata.get("source", "user")
        adapter.config.__dict__["_run_source"] = run_source
        adapter.config.__dict__["_request_metadata"] = dict(metadata)
        adapter.config.extra_disallowed_tools = _extra_disallowed_tools_for_request(metadata)

        memory_policy = metadata.get("memory_policy", {})
        if isinstance(memory_policy, dict):
            adapter.config.__dict__["_memory_policy"] = dict(memory_policy)
        else:
            adapter.config.__dict__["_memory_policy"] = {}

        # System events are ephemeral “out-of-band” signals that should be
        # prefixed into the next LLM prompt (Clawdbot parity).
        system_events = metadata.get("system_events")
        if isinstance(system_events, list):
            adapter.config.__dict__["_system_events"] = list(system_events)
        else:
            adapter.config.__dict__["_system_events"] = []

    def _dispatch_external_vp_mission(
        self,
        *,
        session: GatewaySession,
        request: GatewayRequest,
        vp_id: str,
        mission_type: str,
    ) -> str:
        conn = self._vp_db_conn
        if conn is None:
            raise RuntimeError("VP DB not initialized for external VP dispatch")
        metadata = request.metadata or {}
        constraints = metadata.get("constraints") if isinstance(metadata.get("constraints"), dict) else {}
        budget = metadata.get("budget") if isinstance(metadata.get("budget"), dict) else {}
        mission_row = dispatch_mission_with_retry(
            conn=conn,
            request=MissionDispatchRequest(
                vp_id=vp_id,
                mission_type=mission_type,
                objective=request.user_input,
                constraints=constraints,
                budget=budget,
                idempotency_key=str(metadata.get("idempotency_key") or "").strip(),
                source_session_id=session.session_id,
                source_turn_id=str(metadata.get("turn_id") or uuid.uuid4().hex),
                reply_mode=str(metadata.get("reply_mode") or "async"),
                priority=int(metadata.get("priority") or 100),
                run_id=str(metadata.get("run_id") or "").strip() or None,
            ),
            workspace_base=self._workspace_base,
        )
        return str(mission_row["mission_id"])

    def _external_vp_worker_health(self, vp_id: str) -> tuple[bool, str]:
        conn = self._vp_db_conn
        if conn is None:
            return False, "VP DB not initialized"

        session_row = get_vp_session(conn, vp_id)
        if session_row is None:
            return False, f"No VP session registered for `{vp_id}`"

        status = str(session_row["status"] or "").strip().lower()
        lease_owner = str(session_row["lease_owner"] or "").strip()
        lease_expires_raw = session_row["lease_expires_at"] if "lease_expires_at" in session_row.keys() else None
        heartbeat_raw = session_row["last_heartbeat_at"] if "last_heartbeat_at" in session_row.keys() else None

        now_utc = datetime.now(timezone.utc)
        stale_window_seconds = vp_worker_heartbeat_stale_seconds(default=180)
        lease_expires_at = _parse_iso_datetime(lease_expires_raw)
        heartbeat_at = _parse_iso_datetime(heartbeat_raw)

        lease_live = lease_expires_at is not None and lease_expires_at > now_utc
        heartbeat_fresh = (
            heartbeat_at is not None
            and (now_utc - heartbeat_at).total_seconds() <= stale_window_seconds
        )
        status_ok = status in {"idle", "active", "paused", "recovering"}
        worker_live = status_ok and (lease_live or heartbeat_fresh)
        if worker_live:
            return True, ""

        diagnostics = [
            f"status={status or 'unknown'}",
            f"lease_owner={lease_owner or 'none'}",
            f"lease_expires_at={lease_expires_raw or 'none'}",
            f"last_heartbeat_at={heartbeat_raw or 'none'}",
            f"stale_window_seconds={stale_window_seconds}",
        ]
        if not status_ok:
            diagnostics.append("reason=session_status_not_operational")
        elif not lease_live and not heartbeat_fresh:
            diagnostics.append("reason=no_live_lease_or_fresh_heartbeat")
        elif not lease_live:
            diagnostics.append("reason=lease_expired")
        else:
            diagnostics.append("reason=heartbeat_stale")
        return False, "; ".join(diagnostics)

    def _ensure_external_vp_worker_ready(self, vp_id: str) -> None:
        if not vp_require_live_worker_for_dispatch(default=True):
            return
        healthy, detail = self._external_vp_worker_health(vp_id)
        if healthy:
            return
        message = f"External VP worker `{vp_id}` is unavailable ({detail})."
        raise RuntimeError(message)

    async def _ensure_external_vp_worker_ready_with_retry(self, vp_id: str) -> tuple[bool, str]:
        if not vp_require_live_worker_for_dispatch(default=True):
            return False, ""

        try:
            self._ensure_external_vp_worker_ready(vp_id)
            return False, ""
        except RuntimeError as first_error:
            first_error_detail = str(first_error)

        wait_seconds = max(0, int(vp_worker_recovery_wait_seconds(default=20)))
        if wait_seconds <= 0:
            raise RuntimeError(first_error_detail)

        poll_seconds = max(1, int(vp_worker_recovery_poll_seconds(default=2)))
        deadline = time.monotonic() + wait_seconds
        retried = True
        last_detail = first_error_detail
        while time.monotonic() < deadline:
            healthy, detail = self._external_vp_worker_health(vp_id)
            if healthy:
                return retried, last_detail
            last_detail = detail or last_detail
            await asyncio.sleep(min(poll_seconds, max(0.1, deadline - time.monotonic())))

        raise RuntimeError(
            f"External VP worker `{vp_id}` is unavailable after waiting "
            f"{wait_seconds}s ({last_detail})."
        )

    async def _ensure_coder_vp_adapter(self, owner_user_id: str) -> tuple[ProcessTurnAdapter, Any]:
        if not self._coder_vp_runtime:
            raise RuntimeError("CODER VP runtime is not initialized")

        vp_session_row = self._coder_vp_runtime.ensure_session(
            lease_owner=self._coder_vp_lease_owner,
            owner_user_id=owner_user_id,
        )
        if vp_session_row is None:
            raise RuntimeError("CODER VP session could not be created")

        active_lease_owner = str(vp_session_row["lease_owner"] or "")
        if active_lease_owner and active_lease_owner != self._coder_vp_lease_owner:
            raise RuntimeError(
                f"CODER VP lease held by {active_lease_owner}; cannot delegate safely"
            )

        workspace_dir = str(vp_session_row["workspace_dir"] or "").strip()
        if not workspace_dir:
            raise RuntimeError("CODER VP session has no workspace_dir")

        if (
            self._coder_vp_adapter is not None
            and self._coder_vp_session is not None
            and self._coder_vp_session.workspace_dir == workspace_dir
        ):
            return self._coder_vp_adapter, vp_session_row

        config = EngineConfig(workspace_dir=workspace_dir, user_id=owner_user_id)
        adapter = ProcessTurnAdapter(config)
        await adapter.initialize()

        lane_session_id = Path(workspace_dir).name
        self._coder_vp_runtime.bind_session_identity(session_id=lane_session_id, status="active")

        self._coder_vp_adapter = adapter
        self._coder_vp_session = GatewaySession(
            session_id=lane_session_id,
            user_id=owner_user_id,
            workspace_dir=workspace_dir,
            metadata={
                "engine": "process_turn",
                "lane": "coder_vp",
                "vp_id": str(vp_session_row["vp_id"]),
            },
        )
        return adapter, vp_session_row

    async def execute(
        self, session: GatewaySession, request: GatewayRequest
    ) -> AsyncIterator[AgentEvent]:
        # Per-session lock: different sessions execute concurrently; same session
        # is still serialized to prevent interleaved turns.
        _sess_lock = self._get_session_exec_lock(session.session_id)
        async with _sess_lock:
            if self._use_legacy:
                async for event in self._execute_legacy(session, request):
                    yield event
                return

            # === NEW UNIFIED PATH ===
            adapter = self._adapters.get(session.session_id)
            if not adapter:
                # Acquire global lock only long enough to resume/create the adapter.
                async with self._timed_execution_lock("resume_in_execute"):
                    # Re-check after acquiring lock (another task may have resumed).
                    adapter = self._adapters.get(session.session_id)
                    if not adapter:
                        await self._resume_session_new(session.session_id)
                        adapter = self._adapters.get(session.session_id)
            
            if not adapter:
                raise RuntimeError(f"No adapter for session: {session.session_id}")

            active_adapter = adapter
            vp_mission_id: Optional[str] = None
            vp_trace_id: Optional[str] = None
            vp_had_error = False
            vp_error_detail: Optional[str] = None
            vp_display_name = coder_vp_display_name(default="CODIE")
            next_lease_heartbeat_at: Optional[float] = None
            vp_cancelled = False
            vp_exception: Optional[BaseException] = None
            request_metadata = dict(request.metadata or {})
            request.metadata = request_metadata
            request_source = str(request_metadata.get("source") or "user").strip().lower()
            request_run_kind = str(
                request_metadata.get("run_kind")
                or (session.metadata.get("run_kind") if isinstance(session.metadata, dict) else "")
                or ""
            ).strip().lower()
            requested_vp_id = str(request_metadata.get("delegate_vp_id") or "").strip()
            strict_external_vp = _metadata_bool(
                request_metadata.get("require_external_vp"),
                default=False,
            )
            inferred_explicit_vp = False

            if (
                not requested_vp_id
                and _allow_prompt_inferred_vp_routing(
                    request_source=request_source,
                    request_run_kind=request_run_kind,
                )
            ):
                inferred_vp_id, inferred_mission_type = _infer_explicit_vp_target(request.user_input)
                if inferred_vp_id:
                    inferred_explicit_vp = True
                    requested_vp_id = inferred_vp_id
                    request_metadata["delegate_vp_id"] = inferred_vp_id
                    if inferred_mission_type:
                        request_metadata.setdefault("mission_type", inferred_mission_type)
                    request_metadata.setdefault("vp_intent_source", "explicit_user_prompt")
                    if "require_external_vp" not in request_metadata:
                        strict_external_vp = vp_explicit_intent_require_external(default=True)
                        request_metadata["require_external_vp"] = strict_external_vp

            if requested_vp_id and request_source not in {"cron", "webhook", "heartbeat", "heartbeat_synthetic", "task_run", "email_hook"}:
                # Explicit VP language should default to external dispatch unless
                # operators explicitly disable it via UA_VP_EXTERNAL_DISPATCH_ENABLED=0.
                dispatch_default = bool(inferred_explicit_vp and strict_external_vp)
                external_dispatch_enabled = (
                    vp_external_dispatch_enabled(default=dispatch_default)
                    and vp_dispatch_mode(default="db_pull") == "db_pull"
                )
                mission_type = str(request_metadata.get("mission_type") or "task").strip() or "task"
                if external_dispatch_enabled:
                    try:
                        retried_worker_wait, retry_detail = await self._ensure_external_vp_worker_ready_with_retry(
                            requested_vp_id
                        )
                        if retried_worker_wait:
                            yield AgentEvent(
                                type=EventType.STATUS,
                                data={
                                    "status": (
                                        f"External VP worker `{requested_vp_id}` recovered; retrying dispatch."
                                    ),
                                    "is_log": True,
                                    "routing": "external_vp_dispatch_worker_recovered",
                                    "vp_id": requested_vp_id,
                                    "worker_retry_detail": retry_detail,
                                },
                            )
                        mission_id = self._dispatch_external_vp_mission(
                            session=session,
                            request=request,
                            vp_id=requested_vp_id,
                            mission_type=mission_type,
                        )
                        yield AgentEvent(
                            type=EventType.STATUS,
                            data={
                                "status": f"Delegated to external VP `{requested_vp_id}`.",
                                "is_log": True,
                                "vp_mission_id": mission_id,
                                "vp_id": requested_vp_id,
                                "routing": "delegated_to_external_vp",
                            },
                        )
                        yield AgentEvent(
                            type=EventType.TEXT,
                            data={
                                "text": (
                                    f"Mission queued to `{requested_vp_id}` as `{mission_id}`. "
                                    "Execution is asynchronous; progress and artifacts are available via VP ops endpoints."
                                ),
                                "final": True,
                            },
                        )
                        yield AgentEvent(
                            type=EventType.ITERATION_END,
                            data={"trace_id": request_metadata.get("trace_id")},
                        )
                        return
                    except Exception as exc:
                        vp_error_detail = str(exc)
                        if strict_external_vp:
                            error_message = (
                                f"External VP dispatch failed for `{requested_vp_id}`. "
                                "Strict policy is enabled, so fallback to Simone direct execution is blocked."
                            )
                            yield AgentEvent(
                                type=EventType.STATUS,
                                data={
                                    "status": error_message,
                                    "is_log": True,
                                    "routing": "external_vp_dispatch_failed_strict",
                                    "error": vp_error_detail,
                                },
                            )
                            yield AgentEvent(
                                type=EventType.ERROR,
                                data={
                                    "message": error_message,
                                    "error": vp_error_detail,
                                    "routing": "external_vp_dispatch_failed_strict",
                                    "vp_id": requested_vp_id,
                                },
                            )
                            yield AgentEvent(
                                type=EventType.ITERATION_END,
                                data={"trace_id": request_metadata.get("trace_id")},
                            )
                            return
                        self._apply_request_config(
                            adapter,
                            request,
                            extra_metadata={
                                "routing": "external_vp_dispatch_fallback",
                                "external_vp_dispatch_error": vp_error_detail,
                            },
                        )
                        yield AgentEvent(
                            type=EventType.STATUS,
                            data={
                                "status": (
                                    f"External VP dispatch failed for `{requested_vp_id}`; "
                                    "continuing on Simone primary path."
                                ),
                                "is_log": True,
                                "routing": "external_vp_dispatch_fallback",
                                "error": vp_error_detail,
                            },
                        )
                elif strict_external_vp:
                    strict_reason = (
                        f"External VP dispatch is disabled, but this turn requires `{requested_vp_id}` "
                        "via strict explicit VP policy."
                    )
                    if inferred_explicit_vp:
                        strict_reason += " Enable `UA_VP_EXTERNAL_DISPATCH_ENABLED=1` and running VP workers."
                    yield AgentEvent(
                        type=EventType.STATUS,
                        data={
                            "status": strict_reason,
                            "is_log": True,
                            "routing": "external_vp_dispatch_unavailable_strict",
                            "vp_id": requested_vp_id,
                        },
                    )
                    yield AgentEvent(
                        type=EventType.ERROR,
                        data={
                            "message": strict_reason,
                            "routing": "external_vp_dispatch_unavailable_strict",
                            "vp_id": requested_vp_id,
                        },
                    )
                    yield AgentEvent(
                        type=EventType.ITERATION_END,
                        data={"trace_id": request_metadata.get("trace_id")},
                    )
                    return

            # Keep webhook/cron executions pinned to their explicit run workspace
            # for deterministic artifacts/log paths and easier ops visibility.
            if (
                self._coder_vp_runtime
                and _allow_prompt_inferred_vp_routing(
                    request_source=request_source,
                    request_run_kind=request_run_kind,
                )
            ):
                decision = self._coder_vp_runtime.route_decision(request.user_input)
                if decision.use_coder_vp:
                    external_dispatch = (
                        vp_external_dispatch_enabled(default=False)
                        and vp_dispatch_mode(default="db_pull") == "db_pull"
                    )
                    if external_dispatch:
                        try:
                            retried_worker_wait, retry_detail = await self._ensure_external_vp_worker_ready_with_retry(
                                coder_vp_id()
                            )
                            if retried_worker_wait:
                                yield AgentEvent(
                                    type=EventType.STATUS,
                                    data={
                                        "status": f"{vp_display_name} worker recovered; retrying external dispatch.",
                                        "is_log": True,
                                        "routing": "coder_vp_external_worker_recovered",
                                        "vp_id": coder_vp_id(),
                                        "worker_retry_detail": retry_detail,
                                    },
                                )
                            vp_mission_id = self._dispatch_external_vp_mission(
                                session=session,
                                request=request,
                                vp_id=coder_vp_id(),
                                mission_type="coding_task",
                            )
                            yield AgentEvent(
                                type=EventType.STATUS,
                                data={
                                    "status": f"Delegated to {vp_display_name} external runtime.",
                                    "is_log": True,
                                    "vp_mission_id": vp_mission_id,
                                    "vp_id": coder_vp_id(),
                                    "routing": "delegated_to_coder_vp_external",
                                },
                            )
                            yield AgentEvent(
                                type=EventType.TEXT,
                                data={
                                    "text": (
                                        f"{vp_display_name} mission queued asynchronously as `{vp_mission_id}`. "
                                        "I will continue coordinating while the external worker executes."
                                    ),
                                    "final": True,
                                },
                            )
                            yield AgentEvent(
                                type=EventType.ITERATION_END,
                                data={"trace_id": (request.metadata or {}).get("trace_id")},
                            )
                            return
                        except Exception as exc:
                            vp_error_detail = str(exc)
                            active_adapter = adapter
                            self._apply_request_config(
                                active_adapter,
                                request,
                                extra_metadata={
                                    "routing": "fallback_primary",
                                    "coder_vp_dispatch_error": vp_error_detail,
                                },
                            )
                            yield AgentEvent(
                                type=EventType.STATUS,
                                data={
                                    "status": (
                                        f"{vp_display_name} external dispatch failed; "
                                        "using primary code-writer path."
                                    ),
                                    "is_log": True,
                                    "routing": "coder_vp_dispatch_fallback",
                                    "error": vp_error_detail,
                                },
                            )
                    else:
                        try:
                            async with self._coder_vp_lock:
                                vp_adapter, vp_session_row = await self._ensure_coder_vp_adapter(session.user_id)
                            active_adapter = vp_adapter
                            vp_run_id = getattr(vp_adapter.config, "run_id", None)
                            vp_mission_id = self._coder_vp_runtime.start_mission(
                                objective=request.user_input,
                                run_id=vp_run_id,
                                trace_id=(request.metadata or {}).get("trace_id"),
                                budget=(request.metadata or {}).get("budget")
                                if isinstance((request.metadata or {}).get("budget"), dict)
                                else None,
                            )
                            self._apply_request_config(
                                active_adapter,
                                request,
                                run_source_override="vp.coder",
                                extra_metadata={
                                    "source": "vp.coder",
                                    "vp_context": {
                                        "vp_id": str(vp_session_row["vp_id"]),
                                        "vp_mission_id": vp_mission_id,
                                        "vp_session_id": str(vp_session_row["session_id"] or ""),
                                    },
                                },
                            )
                            next_lease_heartbeat_at = time.monotonic() + 60.0
                            yield AgentEvent(
                                type=EventType.STATUS,
                                data={
                                    "status": f"Delegated to {vp_display_name} lane.",
                                    "is_log": True,
                                    "vp_mission_id": vp_mission_id,
                                    "vp_id": str(vp_session_row["vp_id"]),
                                    "routing": "delegated_to_coder_vp",
                                },
                            )
                        except Exception as exc:
                            vp_error_detail = str(exc)
                            active_adapter = adapter
                            self._apply_request_config(
                                active_adapter,
                                request,
                                extra_metadata={
                                    "routing": "fallback_primary",
                                    "coder_vp_bootstrap_error": vp_error_detail,
                                },
                            )
                            yield AgentEvent(
                                type=EventType.STATUS,
                                data={
                                    "status": f"{vp_display_name} lane bootstrap failed; using primary code-writer path.",
                                    "is_log": True,
                                    "routing": "coder_vp_bootstrap_fallback",
                                    "error": vp_error_detail,
                                },
                            )
                else:
                    self._apply_request_config(active_adapter, request)
                    if decision.shadow_mode and decision.intent_matched:
                        yield AgentEvent(
                            type=EventType.STATUS,
                            data={
                                "status": f"{vp_display_name} shadow mode candidate detected; served by Simone direct path.",
                                "is_log": True,
                                "routing": "coder_vp_shadow_mode",
                            },
                        )
            else:
                self._apply_request_config(active_adapter, request)

            # Execute through unified engine (possibly delegated to CODER VP lane)
            try:
                async for event in active_adapter.execute(request.user_input):
                    if (
                        vp_mission_id
                        and self._coder_vp_runtime is not None
                        and next_lease_heartbeat_at is not None
                        and time.monotonic() >= next_lease_heartbeat_at
                    ):
                        lease_ok = self._coder_vp_runtime.heartbeat_session_lease(self._coder_vp_lease_owner)
                        next_lease_heartbeat_at = time.monotonic() + 60.0
                        if not lease_ok:
                            vp_had_error = True
                            vp_error_detail = "vp lease heartbeat failed"
                            yield AgentEvent(
                                type=EventType.STATUS,
                                data={
                                    "status": f"{vp_display_name} lease heartbeat failed; falling back to primary code-writer path.",
                                    "is_log": True,
                                    "routing": "coder_vp_lease_degraded",
                                    "vp_mission_id": vp_mission_id,
                                    "error": vp_error_detail,
                                },
                            )
                            break

                    if event.type == EventType.STATUS and isinstance(event.data, dict):
                        if "token_usage" in event.data:
                            session.metadata["usage"] = event.data["token_usage"]
                    if vp_mission_id and event.type == EventType.ERROR:
                        vp_had_error = True
                        if isinstance(event.data, dict):
                            msg = event.data.get("message") or event.data.get("error")
                            if isinstance(msg, str) and msg.strip():
                                vp_error_detail = msg.strip()
                    if vp_mission_id and event.type == EventType.ITERATION_END and isinstance(event.data, dict):
                        vp_trace_id = event.data.get("trace_id")
                    yield event
            except BaseException as exc:
                if vp_mission_id:
                    vp_had_error = True
                    vp_error_detail = str(exc) or type(exc).__name__
                    vp_exception = exc
                    vp_cancelled = isinstance(exc, asyncio.CancelledError)
                    if not vp_cancelled:
                        yield AgentEvent(
                            type=EventType.STATUS,
                            data={
                                "status": f"{vp_display_name} lane raised an exception; falling back to primary code-writer path.",
                                "is_log": True,
                                "routing": "coder_vp_exception",
                                "vp_mission_id": vp_mission_id,
                                "error": vp_error_detail,
                            },
                        )
                else:
                    raise

            if vp_mission_id and self._coder_vp_runtime:
                if vp_had_error:
                    fallback_payload = {"error": vp_error_detail} if vp_error_detail else None
                    self._coder_vp_runtime.mark_mission_fallback(
                        vp_mission_id,
                        reason="vp_execution_error",
                        trace_id=vp_trace_id,
                        payload=fallback_payload,
                    )
                    if vp_cancelled:
                        failure_payload: dict[str, Any] = {
                            "cancelled": True,
                            "failed_before_fallback": True,
                        }
                        if vp_error_detail:
                            failure_payload["vp_error"] = vp_error_detail
                        self._coder_vp_runtime.mark_mission_failed(
                            vp_mission_id,
                            error_message="CODER VP execution cancelled.",
                            trace_id=vp_trace_id,
                            payload=failure_payload,
                        )
                        if vp_exception is not None:
                            raise vp_exception
                        raise asyncio.CancelledError()
                    yield AgentEvent(
                        type=EventType.STATUS,
                        data={
                            "status": f"{vp_display_name} failed; falling back to primary code-writer path.",
                            "is_log": True,
                            "routing": "coder_vp_fallback",
                            "vp_mission_id": vp_mission_id,
                        },
                    )

                    fallback_trace_id: Optional[str] = None
                    fallback_had_error = False
                    fallback_exception: Optional[BaseException] = None
                    self._apply_request_config(
                        adapter,
                        request,
                        extra_metadata={
                            "vp_fallback_mission_id": vp_mission_id,
                            "routing": "fallback_primary",
                        },
                    )
                    try:
                        async for event in adapter.execute(request.user_input):
                            if event.type == EventType.STATUS and isinstance(event.data, dict):
                                if "token_usage" in event.data:
                                    session.metadata["usage"] = event.data["token_usage"]
                            if event.type == EventType.ERROR:
                                fallback_had_error = True
                            if event.type == EventType.ITERATION_END and isinstance(event.data, dict):
                                fallback_trace_id = event.data.get("trace_id")
                            yield event
                    except BaseException as fallback_exc:
                        fallback_had_error = True
                        fallback_exception = fallback_exc

                    if fallback_had_error:
                        failure_payload: dict[str, Any] = {"failed_after_fallback": True}
                        if vp_error_detail:
                            failure_payload["vp_error"] = vp_error_detail
                        if fallback_exception is not None:
                            failure_payload["fallback_error"] = str(fallback_exception) or type(fallback_exception).__name__
                        self._coder_vp_runtime.mark_mission_failed(
                            vp_mission_id,
                            error_message=(
                                "Fallback primary path cancelled after CODER VP failure."
                                if isinstance(fallback_exception, asyncio.CancelledError)
                                else "Fallback primary path failed after CODER VP failure."
                            ),
                            trace_id=fallback_trace_id,
                            payload=failure_payload,
                        )
                        if fallback_exception is not None:
                            raise fallback_exception
                    else:
                        completion_payload: dict[str, Any] = {"completed_via": "fallback_primary_path"}
                        if vp_error_detail:
                            completion_payload["vp_error"] = vp_error_detail
                        self._coder_vp_runtime.mark_mission_completed(
                            vp_mission_id,
                            result_ref=f"workspace://{session.workspace_dir}",
                            trace_id=fallback_trace_id,
                            payload=completion_payload,
                        )
                else:
                    self._coder_vp_runtime.mark_mission_completed(
                        vp_mission_id,
                        result_ref=(
                            f"workspace://{self._coder_vp_session.workspace_dir}"
                            if self._coder_vp_session is not None
                            else None
                        ),
                        trace_id=vp_trace_id,
                        payload={"completed_via": "coder_vp"},
                    )

    async def _execute_legacy(
        self, session: GatewaySession, request: GatewayRequest
    ) -> AsyncIterator[AgentEvent]:
        """Legacy execution using AgentBridge (deprecated)."""
        if not self._bridge:
            raise RuntimeError("Legacy bridge not initialized")
        
        if (
            not self._bridge.current_agent
            or self._bridge.current_session_id != session.session_id
        ):
            await self._resume_session_legacy(session.session_id)
        agent = self._bridge.current_agent
        if not agent:
            raise RuntimeError("Gateway session is not initialized")
        async for event in agent.run_query(request.user_input):
            yield event

    async def run_query(
        self, session: GatewaySession, request: GatewayRequest
    ) -> GatewayResult:
        def _env_true(name: str, default: bool) -> bool:
            raw = (os.getenv(name) or "").strip().lower()
            if not raw:
                return bool(default)
            return raw in {"1", "true", "yes", "on"}

        sync_marker_enabled = _env_true("UA_RUNTIME_SYNC_READY_MARKER_ENABLED", True)
        sync_marker_filename = (
            (os.getenv("UA_RUNTIME_SYNC_READY_MARKER_FILENAME") or "").strip() or "sync_ready.json"
        )
        workspace_root = Path(session.workspace_dir).resolve()
        request_metadata = request.metadata or {}
        run_source = str(request_metadata.get("source") or "user").strip().lower() or "user"

        # Persist the canonical source onto automation-owned sessions so the
        # inactivity reaper can apply the correct TTL. Interactive sessions keep
        # their long-lived source classification even if they later receive a
        # background heartbeat or system event.
        try:
            if not isinstance(session.metadata, dict):
                session.metadata = {}
            session.metadata["last_activity_at"] = datetime.now(timezone.utc).isoformat()
            session.metadata["last_run_source"] = run_source
            existing_source = str(session.metadata.get("source") or "").strip().lower()
            if run_source != "user" and existing_source not in {"user", "interactive"}:
                session.metadata["source"] = run_source
            elif not existing_source:
                session.metadata["source"] = run_source
        except Exception:
            pass

        def _write_sync_ready_marker(
            *,
            state: str,
            ready: bool,
            started_at_epoch: Optional[float] = None,
            completed_at_epoch: Optional[float] = None,
            error: Optional[str] = None,
            execution_summary: Optional[dict[str, Any]] = None,
        ) -> None:
            if not sync_marker_enabled:
                return
            payload: dict[str, Any] = {
                "version": 1,
                "session_id": session.session_id,
                "state": str(state or "").strip().lower(),
                "ready": bool(ready),
                "run_source": run_source,
                "updated_at_epoch": time.time(),
            }
            if started_at_epoch is not None:
                payload["started_at_epoch"] = float(started_at_epoch)
            if completed_at_epoch is not None:
                payload["completed_at_epoch"] = float(completed_at_epoch)
            if error:
                payload["error"] = str(error)
            if execution_summary:
                payload["execution_summary"] = execution_summary
            try:
                marker_path = workspace_root / sync_marker_filename
                marker_path.parent.mkdir(parents=True, exist_ok=True)
                marker_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            except Exception:
                # Sync marker is best-effort and must not fail the run.
                pass

        start_time = time.time()
        _write_sync_ready_marker(
            state="in_progress",
            ready=False,
            started_at_epoch=start_time,
        )
        response_text = ""
        tool_calls = 0
        trace_id = None
        code_execution_used = False
        auth_required = False
        auth_link: Optional[str] = None
        errors: list[str] = []
        try:
            async for event in self.execute(session, request):
                if event.type == EventType.TEXT:
                    if isinstance(event.data, dict) and event.data.get("final") is True:
                        response_text = event.data.get("text", "")
                    else:
                        response_text += event.data.get("text", "")
                if event.type == EventType.TOOL_CALL:
                    tool_calls += 1
                    tool_name = (event.data.get("name") or "").upper()
                    if any(x in tool_name for x in ["CODE", "EXECUTE", "BASH", "PYTHON"]):
                        code_execution_used = True
                if event.type == EventType.AUTH_REQUIRED:
                    auth_required = True
                    if isinstance(event.data, dict):
                        link = event.data.get("auth_link")
                        if isinstance(link, str) and link.strip():
                            auth_link = link.strip()
                if event.type == EventType.ERROR:
                    if isinstance(event.data, dict):
                        msg = event.data.get("message") or event.data.get("error") or "Unknown error"
                        if isinstance(msg, str) and msg.strip():
                            errors.append(msg.strip())
                if event.type == EventType.ITERATION_END:
                    trace_id = event.data.get("trace_id")
        except BaseException as exc:
            duration = round(max(0.0, time.time() - start_time), 3)
            _write_sync_ready_marker(
                state="failed",
                ready=True,
                started_at_epoch=start_time,
                completed_at_epoch=time.time(),
                error=str(exc) or type(exc).__name__,
                execution_summary={"tool_calls": tool_calls, "duration_seconds": duration},
            )
            raise

        # Best-effort: sync session transcript into session memory after each query.
        # This reduces the chance of losing session memories when a session is abandoned
        # without an adapter close (e.g., browser closed, machine sleep, process killed).
        try:
            from universal_agent.feature_flags import memory_enabled

            if memory_enabled():
                from universal_agent.memory.orchestrator import get_memory_orchestrator

                workspace_dir = str(Path(session.workspace_dir).resolve())
                transcript_path = os.path.join(workspace_dir, "transcript.md")
                if os.path.exists(transcript_path):
                    shared_root = resolve_shared_memory_workspace(workspace_dir)
                    broker = get_memory_orchestrator(workspace_dir=shared_root)
                    # Force indexing so short runs still get captured.
                    broker.sync_session(
                        session_id=session.session_id,
                        transcript_path=transcript_path,
                        force=True,
                    )
        except Exception:
            # Memory is best-effort; never fail the user request due to persistence issues.
            pass
        
        # Fallback trace_id from legacy bridge
        if not trace_id and self._use_legacy and self._bridge:
            if self._bridge.current_agent and hasattr(self._bridge.current_agent, "trace"):
                trace_id = self._bridge.current_agent.trace.get("trace_id")
        
        duration = time.time() - start_time
        _write_sync_ready_marker(
            state="completed",
            ready=True,
            started_at_epoch=start_time,
            completed_at_epoch=time.time(),
            execution_summary={"tool_calls": tool_calls, "duration_seconds": round(duration, 3)},
        )

        # Stamp activity on completion so the session reaper measures from the
        # end of the most recent turn rather than the beginning.
        try:
            session.metadata["last_activity_at"] = datetime.now(timezone.utc).isoformat()
            session.metadata["last_run_source"] = run_source
        except Exception:
            pass

        return GatewayResult(
            response_text=response_text,
            tool_calls=tool_calls,
            execution_time=duration,
            code_execution_used=code_execution_used,
            trace_id=trace_id,
            metadata={
                "auth_required": auth_required,
                "auth_link": auth_link,
                "errors": errors,
            },
        )

    async def resolve_input(self, session_id: str, input_id: str, response: str) -> bool:
        """Resolve a pending input request for a session."""
        session = self._sessions.get(session_id)
        if not session or input_id not in session.pending_inputs:
            return False
        
        future = session.pending_inputs.pop(input_id)
        if not future.done():
            future.set_result(response)
            return True
        return False

    async def close_session(self, session_id: str) -> None:
        """Tear down a session and release all associated resources.

        Safe to call for sessions that are not in memory (no-op).
        If the session's execution lock is currently held this logs a warning
        but proceeds with cleanup — the adapter and session dicts are cleared
        so the next allocate will be fresh.
        """
        session_lock = self._session_exec_locks.get(session_id)
        if session_lock and session_lock.locked():
            logger.warning(
                "close_session(%s): session lock is held — closing anyway; "
                "in-flight execution may see a missing adapter.",
                session_id,
            )

        async with self._timed_execution_lock("close_session"):
            adapter = self._adapters.pop(session_id, None)
            self._sessions.pop(session_id, None)
            self._session_exec_locks.pop(session_id, None)

        if adapter is not None:
            teardown = getattr(adapter, "teardown", None)
            if callable(teardown):
                try:
                    result = teardown()
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:
                    logger.warning("Adapter teardown error for session %s: %s", session_id, exc)

        logger.info("Session closed and resources released: %s", session_id)

    # ---------------------------------------------------------------------------
    # Session Reaper — activity-based TTL cleanup
    # ---------------------------------------------------------------------------

    _ADMIN_SOURCES: frozenset[str] = frozenset(
        {"cron", "heartbeat", "hooks", "webhook", "ops", "system"}
    )
    _VP_SOURCES: frozenset[str] = frozenset({"vp_mission", "vp.coder", "vp.general"})

    def _session_inactivity_seconds(self, session: GatewaySession) -> Optional[float]:
        """Return seconds since last_activity_at, or None if no timestamp."""
        raw = session.metadata.get("last_activity_at") if isinstance(session.metadata, dict) else None
        if not raw:
            return None
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
        except Exception:
            return None

    def _reaper_ttl_seconds(self, session: GatewaySession) -> Optional[int]:
        """Return inactivity TTL for this session, or None to never auto-close.

        TTL classes (all inactivity-based — sessions still executing are safe):
          - source=cron / heartbeat / webhook / hooks / ops / system
                                               → UA_SESSION_ADMIN_TTL_SECONDS (default 600 s / 10 min)
          - source=vp_mission / vp.coder …  → UA_SESSION_VP_INACTIVITY_TTL_SECONDS (default 900 s / 15 min)
          - source=user (interactive)        → None (never auto-close)
        """
        metadata = session.metadata if isinstance(session.metadata, dict) else {}
        source = str(metadata.get("source") or metadata.get("run_source") or "user").strip().lower()
        # Also check request metadata stored on adapter config.
        if source == "user":
            lane = str(metadata.get("lane") or "").strip().lower()
            if lane and lane.startswith("vp"):
                source = "vp_mission"

        if source in self._ADMIN_SOURCES:
            return int(os.getenv("UA_SESSION_ADMIN_TTL_SECONDS", "600"))
        if source in self._VP_SOURCES:
            return int(os.getenv("UA_SESSION_VP_INACTIVITY_TTL_SECONDS", "900"))
        # Interactive user sessions: reap after long inactivity (default 4h).
        # Workspace files on disk are preserved — only the in-memory session
        # and adapter are released.  Dashboard still shows archived sessions.
        return int(os.getenv("UA_SESSION_USER_TTL_SECONDS", "14400"))

    async def _session_reaper(self) -> None:
        """Background task: periodically close sessions that have exceeded their inactivity TTL."""
        interval = max(30, int(os.getenv("UA_SESSION_REAPER_INTERVAL_SECONDS", "60")))
        logger.info("Session reaper started (interval=%ds)", interval)
        while self._reaper_running:
            try:
                await asyncio.sleep(interval)
                if not self._reaper_running:
                    break
                candidates = list(self._sessions.values())
                for session in candidates:
                    ttl = self._reaper_ttl_seconds(session)
                    if ttl is None:
                        continue  # interactive session — never auto-close
                    # Daemon sessions are persistent by design; never reap them.
                    if session.session_id.startswith("daemon_"):
                        continue
                    lock = self._session_exec_locks.get(session.session_id)
                    if lock and lock.locked():
                        continue  # executing right now — skip
                    inactivity = self._session_inactivity_seconds(session)
                    if inactivity is None:
                        # No activity timestamp yet — use session creation age as fallback
                        # with a grace window (double the TTL) before reaping.
                        created = session.metadata.get("created_at") if isinstance(session.metadata, dict) else None
                        if created:
                            try:
                                age = time.time() - float(created)
                                if age < ttl * 2:
                                    continue  # too young, give it time to stamp activity
                                # Old enough without any activity — treat as inactive
                                inactivity = age
                            except Exception:
                                continue
                        else:
                            continue  # no created_at either — skip this cycle
                    if inactivity >= ttl:
                        logger.info(
                            "Reaper closing stale session %s (source=%s, inactive=%.0fs, ttl=%ds)",
                            session.session_id,
                            session.metadata.get("source", "unknown") if isinstance(session.metadata, dict) else "unknown",
                            inactivity,
                            ttl,
                        )
                        try:
                            await self.close_session(session.session_id)
                        except Exception as exc:
                            logger.warning("Reaper error closing session %s: %s", session.session_id, exc)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Session reaper loop error: %s", exc)
        logger.info("Session reaper stopped")

    def start_reaper(self) -> None:
        """Start the session reaper background task (idempotent)."""
        if getattr(self, "_reaper_running", False):
            return
        self._reaper_running = True
        self._reaper_task: Optional[asyncio.Task] = asyncio.create_task(self._session_reaper())
        logger.info("Session reaper task created")

    async def stop_reaper(self) -> None:
        """Stop the session reaper background task."""
        self._reaper_running = False
        task = getattr(self, "_reaper_task", None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info("Session reaper task stopped")

    def list_sessions(self, since_hours: int = 24) -> list[GatewaySessionSummary]:
        """List active and recently-completed sessions.

        Only sessions that are currently in memory (live) or whose workspace
        directories were modified within *since_hours* (default 48 h) are
        returned.  Older disk-only session directories are excluded to prevent
        inflated counts and dashboard noise.

        Args:
            since_hours: Workspace directories older than this many hours are
                excluded from the disk scan.  In-memory (live) sessions are
                always included regardless of age.  Pass 0 to include all.
        """
        import stat as _stat

        summaries: list[GatewaySessionSummary] = []
        cutoff_seconds = since_hours * 3600 if since_hours > 0 else 0
        now_ts = time.time()

        # 1. Always include in-memory (live) sessions.
        for session_id, session in self._sessions.items():
            workspace_path = Path(session.workspace_dir)
            trace_file = workspace_path / "trace.json"
            runtime = session.metadata.get("runtime", {}) if isinstance(session.metadata, dict) else {}
            runtime_state = runtime.get("lifecycle_state") if isinstance(runtime, dict) else None
            if isinstance(runtime_state, str) and runtime_state:
                status = runtime_state
            else:
                status = "complete" if trace_file.exists() else "active"
            summaries.append(
                GatewaySessionSummary(
                    session_id=session_id,
                    workspace_dir=session.workspace_dir,
                    status=status,
                    metadata=session.metadata,
                )
            )

        # 2. Scan workspace base for sessions not in memory, limited to since_hours.
        #    These are presented as "archived" (completed, read-only) — not active.
        live_ids = {s.session_id for s in summaries}
        if self._workspace_base.exists():
            for session_dir in sorted(self._workspace_base.iterdir(), reverse=True):
                if not (session_dir.is_dir() and session_dir.name.startswith("session_")):
                    continue
                if session_dir.name in live_ids:
                    continue
                # Apply 48h recency filter via directory mtime.
                if cutoff_seconds > 0:
                    try:
                        mtime = session_dir.stat().st_mtime
                        if (now_ts - mtime) > cutoff_seconds:
                            continue  # too old — exclude from results
                    except OSError:
                        continue
                trace_file = session_dir / "trace.json"
                status = "archived" if trace_file.exists() else "archived_incomplete"
                summaries.append(
                    GatewaySessionSummary(
                        session_id=session_dir.name,
                        workspace_dir=str(session_dir),
                        status=status,
                        metadata={"archived": True},
                    )
                )

        # 3. Legacy bridge sessions (deprecated path).
        if self._use_legacy and self._bridge:
            for session in self._bridge.list_sessions():
                if session["session_id"] not in [s.session_id for s in summaries]:
                    summaries.append(
                        GatewaySessionSummary(
                            session_id=session["session_id"],
                            workspace_dir=session["workspace_path"],
                            status=session.get("status", "unknown"),
                            metadata={
                                "timestamp": session.get("timestamp"),
                                "files": session.get("files", {}),
                                "engine": "agent_bridge_legacy",
                            },
                        )
                    )

        if sdk_session_history_enabled(default=False):
            try:
                sdk_rows = session_history_adapter.list_session_summaries_for_workspace(
                    self._workspace_base,
                    limit=200,
                )
                by_session_id = {item.session_id: item for item in summaries}
                for row in sdk_rows:
                    session_id = str(row.get("session_id", "") or "").strip()
                    if not session_id:
                        continue
                    if session_id in by_session_id:
                        existing = by_session_id[session_id]
                        existing_meta = (
                            dict(existing.metadata)
                            if isinstance(existing.metadata, dict)
                            else {}
                        )
                        existing_meta["sdk_history"] = {
                            "summary": row.get("summary"),
                            "cwd": row.get("cwd"),
                            "last_modified": row.get("last_modified"),
                            "file_size": row.get("file_size"),
                        }
                        existing.metadata = existing_meta
                        continue
                    summaries.append(
                        GatewaySessionSummary(
                            session_id=session_id,
                            workspace_dir=str(row.get("workspace_dir") or ""),
                            status="history_only",
                            metadata={"source": "sdk_history", "sdk_history": row},
                        )
                    )
            except Exception:
                if logfire:
                    logfire.warning("gateway_sdk_history_augmentation_failed")
        
        return summaries[:50]  # Limit to 50 sessions

    def list_live_sessions(self) -> list[GatewaySessionSummary]:
        """List only currently-live execution sessions.

        This excludes archived workspace directories, SDK history, and other
        disk-derived summaries that should not be used as runtime targets.
        """
        summaries: list[GatewaySessionSummary] = []

        for session_id, session in self._sessions.items():
            workspace_path = Path(session.workspace_dir)
            trace_file = workspace_path / "trace.json"
            runtime = session.metadata.get("runtime", {}) if isinstance(session.metadata, dict) else {}
            runtime_state = runtime.get("lifecycle_state") if isinstance(runtime, dict) else None
            if isinstance(runtime_state, str) and runtime_state:
                status = runtime_state
            else:
                status = "complete" if trace_file.exists() else "active"
            summaries.append(
                GatewaySessionSummary(
                    session_id=session_id,
                    workspace_dir=session.workspace_dir,
                    status=status,
                    metadata=session.metadata,
                )
            )

        if self._use_legacy and self._bridge:
            live_ids = {item.session_id for item in summaries}
            for session in self._bridge.list_sessions():
                session_id = str(session.get("session_id") or "").strip()
                if not session_id or session_id in live_ids:
                    continue
                summaries.append(
                    GatewaySessionSummary(
                        session_id=session_id,
                        workspace_dir=str(session.get("workspace_path") or ""),
                        status=str(session.get("status") or "unknown"),
                        metadata={
                            "timestamp": session.get("timestamp"),
                            "files": session.get("files", {}),
                            "engine": "agent_bridge_legacy",
                        },
                    )
                )

        return summaries

    async def get_session_mcp_status(self, session_id: str) -> dict[str, Any]:
        if not dynamic_mcp_enabled(default=False):
            raise ValueError("Dynamic MCP controls are disabled by feature flag")
        adapter = self._adapters.get(session_id)
        if adapter is None:
            raise ValueError("Session is not active")
        return await adapter.get_mcp_status()

    async def add_session_mcp_server(
        self, session_id: str, server_name: str, server_config: dict[str, Any]
    ) -> dict[str, Any]:
        if not dynamic_mcp_enabled(default=False):
            raise ValueError("Dynamic MCP controls are disabled by feature flag")
        adapter = self._adapters.get(session_id)
        if adapter is None:
            raise ValueError("Session is not active")
        return await adapter.add_mcp_server(server_name, server_config)

    async def remove_session_mcp_server(self, session_id: str, server_name: str) -> dict[str, Any]:
        if not dynamic_mcp_enabled(default=False):
            raise ValueError("Dynamic MCP controls are disabled by feature flag")
        adapter = self._adapters.get(session_id)
        if adapter is None:
            raise ValueError("Session is not active")
        return await adapter.remove_mcp_server(server_name)

    async def close_session(self, session_id: str) -> None:
        """Close and clean up a single session's adapter and state."""
        adapter = self._adapters.pop(session_id, None)
        if adapter:
            try:
                await adapter.close()
            except Exception:
                pass
        self._sessions.pop(session_id, None)

    async def close(self) -> None:
        """Clean up all active adapters and sessions."""
        if self._coder_vp_runtime is not None:
            try:
                self._coder_vp_runtime.release_session_lease(self._coder_vp_lease_owner)
            except Exception:
                pass

        if self._coder_vp_adapter:
            try:
                await self._coder_vp_adapter.close()
            except Exception:
                pass
            self._coder_vp_adapter = None
            self._coder_vp_session = None

        # 1. Close all active adapters
        for session_id in list(self._adapters.keys()):
            adapter = self._adapters.pop(session_id)
            try:
                await adapter.close()
            except Exception:
                pass
        
        # 2. Close legacy bridge if it exists
        if self._bridge:
            # AgentBridge doesn't currently have a close but we'll add one
            if hasattr(self._bridge, "close"):
                try:
                    await self._bridge.close()
                except Exception:
                    pass
            self._bridge = None
        
        self._sessions.clear()

        if self._runtime_db_conn is not None:
            try:
                self._runtime_db_conn.close()
            except Exception:
                pass
            self._runtime_db_conn = None
        if self._coder_vp_db_conn is not None:
            try:
                self._coder_vp_db_conn.close()
            except Exception:
                pass
            self._coder_vp_db_conn = None
        if self._vp_db_conn is not None:
            try:
                self._vp_db_conn.close()
            except Exception:
                pass
            self._vp_db_conn = None


class ExternalGateway(Gateway):
    """
    Gateway client that connects to an external gateway server via HTTP/WebSocket.
    
    Requires httpx and websockets packages to be installed.
    """

    def __init__(self, base_url: str, timeout: float | None = None):
        if not EXTERNAL_DEPS_AVAILABLE:
            raise RuntimeError(
                "ExternalGateway requires 'httpx' and 'websockets' packages. "
                "Install with: pip install httpx websockets"
            )
        self._base_url = base_url.rstrip("/")
        self._timeout = (
            float(timeout) if timeout is not None else gateway_http_timeout_seconds()
        )
        self._http_client: Optional[httpx.AsyncClient] = None
        self._auth_headers = self._build_auth_headers()
        self._ws_headers_param = self._detect_ws_headers_param()

    def _build_auth_headers(self) -> dict[str, str]:
        token = (
            (os.getenv("UA_INTERNAL_API_TOKEN") or "").strip()
            or (os.getenv("UA_OPS_TOKEN") or "").strip()
        )
        if not token:
            return {}
        return {
            "authorization": f"Bearer {token}",
            "x-ua-internal-token": token,
            "x-ua-ops-token": token,
        }

    def _detect_ws_headers_param(self) -> str:
        try:
            params = inspect.signature(websockets.connect).parameters
        except Exception:
            return "extra_headers"
        if "additional_headers" in params:
            return "additional_headers"
        return "extra_headers"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers=self._auth_headers or None,
            )
        return self._http_client

    async def close(self) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    async def create_session(
        self,
        user_id: str,
        workspace_dir: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> GatewaySession:
        client = await self._get_client()
        payload = {"user_id": user_id}
        if workspace_dir:
            payload["workspace_dir"] = workspace_dir
        if session_id:
            payload["session_id"] = session_id
        resp = await client.post("/api/v1/sessions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return GatewaySession(
            session_id=data["session_id"],
            user_id=data["user_id"],
            workspace_dir=data["workspace_dir"],
            metadata=data.get("metadata", {}),
        )

    async def resume_session(self, session_id: str) -> GatewaySession:
        client = await self._get_client()
        resp = await client.get(f"/api/v1/sessions/{session_id}")
        if resp.status_code == 404:
            raise ValueError(f"Unknown session_id: {session_id}")
        resp.raise_for_status()
        data = resp.json()
        return GatewaySession(
            session_id=data["session_id"],
            user_id=data["user_id"],
            workspace_dir=data["workspace_dir"],
            metadata=data.get("metadata", {}),
        )

    async def execute(
        self, session: GatewaySession, request: GatewayRequest
    ) -> AsyncIterator[AgentEvent]:
        ws_url = self._base_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/api/v1/sessions/{session.session_id}/stream"
        completed = False
        ws_kwargs: dict[str, Any] = {}
        if self._auth_headers:
            ws_kwargs[self._ws_headers_param] = self._auth_headers
        ws_kwargs.update(websocket_connect_kwargs(websockets.connect))

        async with websockets.connect(ws_url, **ws_kwargs) as ws:
            connected_msg = await ws.recv()
            connected_data = json.loads(connected_msg)
            if connected_data.get("type") != "connected":
                raise RuntimeError(f"Unexpected connection response: {connected_data}")

            execute_msg = {
                "type": "execute",
                "data": {
                    "user_input": request.user_input,
                    "force_complex": request.force_complex,
                    "metadata": request.metadata,
                },
            }
            await ws.send(json.dumps(execute_msg))

            while True:
                try:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    event_type_str = data.get("type", "")

                    if event_type_str == "query_complete":
                        completed = True
                        break
                    if event_type_str == "error":
                        err_data = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
                        err_msg = err_data.get("message") or err_data.get("error") or "Unknown error"
                        parts = [err_msg]
                        if err_data.get("detail"):
                            parts.append(f"Detail: {err_data['detail']}")
                        if err_data.get("log_tail"):
                            parts.append("Log tail:\n" + err_data["log_tail"])
                        raise RuntimeError("\n".join(parts))

                    try:
                        event_type = EventType(event_type_str)
                    except ValueError:
                        event_type = EventType.STATUS

                    yield AgentEvent(type=event_type, data=data.get("data", {}))

                except websockets.exceptions.ConnectionClosed as e:
                    if not completed:
                        raise RuntimeError(f"WebSocket closed before completion (code={e.code}, reason={e.reason})")
                    break

    async def run_query(
        self, session: GatewaySession, request: GatewayRequest
    ) -> GatewayResult:
        response_text = ""
        tool_calls = 0
        async for event in self.execute(session, request):
            if event.type == EventType.TEXT:
                response_text += event.data.get("text", "")
            if event.type == EventType.TOOL_CALL:
                tool_calls += 1
        return GatewayResult(
            response_text=response_text,
            tool_calls=tool_calls,
            trace_id=None,
        )

    def list_sessions(self) -> list[GatewaySessionSummary]:
        raise NotImplementedError(
            "list_sessions() is synchronous; use list_sessions_async() instead"
        )

    def list_live_sessions(self) -> list[GatewaySessionSummary]:
        raise NotImplementedError(
            "list_live_sessions() is synchronous; use list_sessions_async() instead"
        )

    async def list_sessions_async(self) -> list[GatewaySessionSummary]:
        client = await self._get_client()
        resp = await client.get("/api/v1/sessions")
        resp.raise_for_status()
        data = resp.json()
        summaries = []
        for s in data.get("sessions", []):
            summaries.append(
                GatewaySessionSummary(
                    session_id=s["session_id"],
                    workspace_dir=s["workspace_dir"],
                    status=s.get("status", "unknown"),
                    metadata=s.get("metadata", {}),
                )
            )
        return summaries

    async def get_session_mcp_status(self, session_id: str) -> dict[str, Any]:
        client = await self._get_client()
        resp = await client.get(f"/api/v1/ops/sessions/{session_id}/mcp")
        resp.raise_for_status()
        return resp.json()

    async def add_session_mcp_server(
        self, session_id: str, server_name: str, server_config: dict[str, Any]
    ) -> dict[str, Any]:
        client = await self._get_client()
        resp = await client.post(
            f"/api/v1/ops/sessions/{session_id}/mcp",
            json={"server_name": server_name, "server_config": server_config},
        )
        resp.raise_for_status()
        return resp.json()

    async def remove_session_mcp_server(self, session_id: str, server_name: str) -> dict[str, Any]:
        client = await self._get_client()
        resp = await client.request(
            "DELETE",
            f"/api/v1/ops/sessions/{session_id}/mcp",
            json={"server_name": server_name},
        )
        resp.raise_for_status()
        return resp.json()

    async def health_check(self) -> bool:
        try:
            client = await self._get_client()
            resp = await client.get("/api/v1/health")
            return resp.status_code == 200
        except Exception:
            return False

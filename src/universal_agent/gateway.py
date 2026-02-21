from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from universal_agent.durable.db import (
    connect_runtime_db,
    get_coder_vp_db_path,
    get_runtime_db_path,
    get_vp_db_path,
)
from universal_agent.durable.migrations import ensure_schema
from universal_agent.feature_flags import (
    coder_vp_display_name,
    coder_vp_id,
    vp_dispatch_mode,
    vp_explicit_intent_require_external,
    vp_external_dispatch_enabled,
)
from universal_agent.memory.paths import resolve_shared_memory_workspace
from universal_agent.timeout_policy import (
    gateway_http_timeout_seconds,
    websocket_connect_kwargs,
)
from universal_agent.workspace import seed_workspace_bootstrap
from universal_agent.vp import (
    CoderVPRuntime,
    MissionDispatchRequest,
    dispatch_mission_with_retry,
)

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
        self, user_id: str, workspace_dir: Optional[str] = None
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
        # process_turn (and parts of the legacy bridge) rely on global process state
        # (stdout/stderr redirection, env vars, module-level globals). Serialize
        # all gateway execution to prevent cross-session contamination.
        self._execution_lock = asyncio.Lock()
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
        return snapshot

    def get_coder_vp_db_conn(self) -> Any:
        return self._coder_vp_db_conn

    def get_vp_db_conn(self) -> Any:
        return self._vp_db_conn

    async def create_session(
        self, user_id: str, workspace_dir: Optional[str] = None
    ) -> GatewaySession:
        async with self._timed_execution_lock("create_session"):
            if self._use_legacy:
                return await self._create_session_legacy(user_id, workspace_dir)
        
            # === NEW UNIFIED PATH ===
            # Generate session ID
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_id = f"session_{timestamp}_{uuid.uuid4().hex[:8]}"
            
            # Create workspace directory
            if workspace_dir:
                workspace_path = Path(workspace_dir).resolve()
            else:
                workspace_path = self._workspace_base / session_id
            
            workspace_path.mkdir(parents=True, exist_ok=True)
            (workspace_path / "work_products").mkdir(exist_ok=True)
            bootstrap_result = seed_workspace_bootstrap(str(workspace_path))
            
            # Update session_id to match workspace name if custom workspace provided
            if workspace_dir:
                session_id = workspace_path.name
            
            # Create adapter with unified engine config
            config = EngineConfig(
                workspace_dir=str(workspace_path),
                user_id=user_id,
            )
            adapter = ProcessTurnAdapter(config)
            
            # Initialize adapter
            await adapter.initialize()
            
            # Store adapter and session
            self._adapters[session_id] = adapter
            
            config_metadata = getattr(config, "metadata", {})
            
            session = GatewaySession(
                session_id=session_id,
                user_id=user_id,
                workspace_dir=str(workspace_path),
                metadata={
                    "engine": "process_turn",
                    "logfire_enabled": bool(os.getenv("LOGFIRE_TOKEN")),
                    "workspace_bootstrap": bootstrap_result,
                },
            )
            self._sessions[session_id] = session
            
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
        # Check if session exists in memory
        if session_id in self._sessions:
            return self._sessions[session_id]

        # Try to find workspace on disk
        workspace_path = self._workspace_base / session_id
        if not workspace_path.exists():
            raise ValueError(f"Unknown session_id: {session_id}")

        # Recreate adapter for existing workspace
        from universal_agent.identity import resolve_user_id

        config = EngineConfig(
            workspace_dir=str(workspace_path),
            user_id=resolve_user_id(),
        )
        adapter = ProcessTurnAdapter(config)
        await adapter.initialize()

        self._adapters[session_id] = adapter

        token_usage = self._read_token_usage_from_trace(workspace_path)
        
        session = GatewaySession(
            session_id=session_id,
            user_id=config.user_id or "unknown",
            workspace_dir=str(workspace_path),
            metadata={
                "engine": "process_turn",
                "resumed": True,
                "usage": token_usage,
            },
        )
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
        async with self._timed_execution_lock("execute"):
            if self._use_legacy:
                async for event in self._execute_legacy(session, request):
                    yield event
                return
            
            # === NEW UNIFIED PATH ===
            adapter = self._adapters.get(session.session_id)
            if not adapter:
                # Try to resume session (lock already held)
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
            requested_vp_id = str(request_metadata.get("delegate_vp_id") or "").strip()
            strict_external_vp = _metadata_bool(
                request_metadata.get("require_external_vp"),
                default=False,
            )
            inferred_explicit_vp = False

            if not requested_vp_id and request_source not in {"cron", "webhook"}:
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

            if requested_vp_id and request_source not in {"cron", "webhook"}:
                external_dispatch_enabled = (
                    vp_external_dispatch_enabled(default=False)
                    and vp_dispatch_mode(default="db_pull") == "db_pull"
                )
                mission_type = str(request_metadata.get("mission_type") or "task").strip() or "task"
                if external_dispatch_enabled:
                    try:
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

            # Keep webhook/cron executions pinned to their explicit session workspace
            # for deterministic artifacts/log paths and easier ops visibility.
            if self._coder_vp_runtime and request_source not in {"cron", "webhook"}:
                decision = self._coder_vp_runtime.route_decision(request.user_input)
                if decision.use_coder_vp:
                    external_dispatch = (
                        vp_external_dispatch_enabled(default=False)
                        and vp_dispatch_mode(default="db_pull") == "db_pull"
                    )
                    if external_dispatch:
                        try:
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
                "run_source": str((request.metadata or {}).get("source") or "unknown"),
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
        except Exception as exc:
            duration = round(max(0.0, time.time() - start_time), 3)
            _write_sync_ready_marker(
                state="failed",
                ready=True,
                started_at_epoch=start_time,
                completed_at_epoch=time.time(),
                error=str(exc),
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

    def list_sessions(self) -> list[GatewaySessionSummary]:
        summaries: list[GatewaySessionSummary] = []
        
        # Include in-memory sessions
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
        
        # Scan workspace base for additional sessions
        if self._workspace_base.exists():
            for session_dir in sorted(self._workspace_base.iterdir(), reverse=True):
                if session_dir.is_dir() and session_dir.name.startswith("session_"):
                    if session_dir.name not in self._sessions:
                        trace_file = session_dir / "trace.json"
                        status = "complete" if trace_file.exists() else "incomplete"
                        summaries.append(
                            GatewaySessionSummary(
                                session_id=session_dir.name,
                                workspace_dir=str(session_dir),
                                status=status,
                                metadata={"discovered": True},
                            )
                        )
        
        # Also include legacy sessions if bridge exists
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
        
        return summaries[:50]  # Limit to 50 sessions

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
        self, user_id: str, workspace_dir: Optional[str] = None
    ) -> GatewaySession:
        client = await self._get_client()
        payload = {"user_id": user_id}
        if workspace_dir:
            payload["workspace_dir"] = workspace_dir
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

    async def health_check(self) -> bool:
        try:
            client = await self._get_client()
            resp = await client.get("/api/v1/health")
            return resp.status_code == 200
        except Exception:
            return False

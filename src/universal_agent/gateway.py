from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Optional

try:
    from universal_agent.agent_core import AgentEvent, EventType
except Exception:  # pragma: no cover - import safety for tooling
    AgentEvent = Any  # type: ignore
    EventType = Any  # type: ignore

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
        
        # Legacy bridge (deprecated)
        self._bridge: Optional[AgentBridge] = None
        
        # New unified engine adapters
        self._adapters: dict[str, ProcessTurnAdapter] = {}
        self._sessions: dict[str, GatewaySession] = {}
        
        if self._use_legacy:
            self._bridge = AgentBridge(hooks=hooks)

    async def create_session(
        self, user_id: str, workspace_dir: Optional[str] = None
    ) -> GatewaySession:
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
        metadata = {
            "session_url": session_info.session_url,
            "logfire_enabled": session_info.logfire_enabled,
            "engine": "agent_bridge_legacy",
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
        if self._use_legacy:
            return await self._resume_session_legacy(session_id)
        
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
        
        session = GatewaySession(
            session_id=session_id,
            user_id=config.user_id or "unknown",
            workspace_dir=str(workspace_path),
            metadata={
                "engine": "process_turn",
                "resumed": True,
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

    async def execute(
        self, session: GatewaySession, request: GatewayRequest
    ) -> AsyncIterator[AgentEvent]:
        if self._use_legacy:
            async for event in self._execute_legacy(session, request):
                yield event
            return
        
        # === NEW UNIFIED PATH ===
        adapter = self._adapters.get(session.session_id)
        if not adapter:
            # Try to resume session
            await self.resume_session(session.session_id)
            adapter = self._adapters.get(session.session_id)
        
        if not adapter:
            raise RuntimeError(f"No adapter for session: {session.session_id}")
        
        # Execute through unified engine
        async for event in adapter.execute(request.user_input):
            yield event

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
        start_time = time.time()
        response_text = ""
        tool_calls = 0
        trace_id = None
        code_execution_used = False
        
        async for event in self.execute(session, request):
            if event.type == EventType.TEXT:
                response_text += event.data.get("text", "")
            if event.type == EventType.TOOL_CALL:
                tool_calls += 1
                tool_name = (event.data.get("name") or "").upper()
                if any(x in tool_name for x in ["CODE", "EXECUTE", "BASH", "PYTHON"]):
                    code_execution_used = True
            if event.type == EventType.ITERATION_END:
                trace_id = event.data.get("trace_id")
        
        # Fallback trace_id from legacy bridge
        if not trace_id and self._use_legacy and self._bridge:
            if self._bridge.current_agent and hasattr(self._bridge.current_agent, "trace"):
                trace_id = self._bridge.current_agent.trace.get("trace_id")
        
        duration = time.time() - start_time
        
        return GatewayResult(
            response_text=response_text,
            tool_calls=tool_calls,
            execution_time=duration,
            code_execution_used=code_execution_used,
            trace_id=trace_id,
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

    async def close(self) -> None:
        """Clean up all active adapters and sessions."""
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


class ExternalGateway(Gateway):
    """
    Gateway client that connects to an external gateway server via HTTP/WebSocket.
    
    Requires httpx and websockets packages to be installed.
    """

    def __init__(self, base_url: str, timeout: float = 30.0):
        if not EXTERNAL_DEPS_AVAILABLE:
            raise RuntimeError(
                "ExternalGateway requires 'httpx' and 'websockets' packages. "
                "Install with: pip install httpx websockets"
            )
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
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

        async with websockets.connect(ws_url) as ws:
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
                        break
                    if event_type_str == "error":
                        raise RuntimeError(data.get("data", {}).get("message", "Unknown error"))

                    try:
                        event_type = EventType(event_type_str)
                    except ValueError:
                        event_type = EventType.STATUS

                    yield AgentEvent(type=event_type, data=data.get("data", {}))

                except websockets.exceptions.ConnectionClosed:
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

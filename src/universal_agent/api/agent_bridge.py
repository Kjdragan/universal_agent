"""
Agent Bridge Module - Bridges FastAPI server and UniversalAgent.

This module provides the integration layer between the WebSocket server
and the core UniversalAgent class from agent_core.py.
"""

import asyncio
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Callable, Optional

from universal_agent.agent_core import UniversalAgent, AgentEvent, EventType
from universal_agent.api.events import (
    WebSocketEvent,
    EventType as WSEventType,
    SessionInfo,
    create_connected_event,
    create_error_event,
    create_status_event,
)
from universal_agent.identity import resolve_user_id


class AgentBridge:
    """
    Bridge between FastAPI WebSocket server and UniversalAgent.

    Manages agent lifecycle, session persistence, and event conversion.
    """

    def __init__(self, hooks: Optional[dict] = None):
        self.current_agent: Optional[UniversalAgent] = None
        self.current_session_id: Optional[str] = None
        self.workspace_base = Path("AGENT_RUN_WORKSPACES").resolve()
        self.event_callbacks: list[Callable[[WebSocketEvent], None]] = []
        self._hooks = hooks
        self._session_roots: set[Path] = {self.workspace_base}
        self._session_registry: dict[str, Path] = {}

    async def create_session(
        self, 
        user_id: Optional[str] = None, 
        workspace_dir: Optional[str] = None
    ) -> SessionInfo:
        """Create a new agent session."""
        # Resolve user_id if not provided
        if not user_id:
            user_id = resolve_user_id()
            print(f"DEBUG BRIDGE: Resolved user_id={user_id}")
            
        # Create workspace directory
        if workspace_dir:
            workspace_path = Path(workspace_dir).resolve()
            workspace_path.mkdir(parents=True, exist_ok=True)
            session_id = workspace_path.name
            self._session_roots.add(workspace_path.parent)
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_id = f"session_{timestamp}_{uuid.uuid4().hex[:8]}"
            workspace_path = self.workspace_base / session_id
            workspace_path.mkdir(parents=True, exist_ok=True)

        # Unify architecture: Use shared hooks from hooks.py
        from universal_agent.hooks import AgentHookSet
        hooks_manager = AgentHookSet(
            run_id=session_id,
            active_workspace=str(workspace_path),
            enable_skills=True
        )

        # Initialize agent
        self.current_agent = UniversalAgent(
            workspace_dir=str(workspace_path),
            user_id=user_id,
            hooks=hooks_manager.build_hooks(),
        )
        await self.current_agent.initialize()

        self.current_session_id = session_id
        self._session_registry[session_id] = workspace_path

        # Build session info
        return SessionInfo(
            session_id=session_id,
            workspace=str(workspace_path),
            user_id=user_id,
            session_url=self.current_agent.session.mcp.url if self.current_agent.session else None,
            logfire_enabled=bool(os.getenv("LOGFIRE_TOKEN")),
        )

    async def resume_session(self, session_id: str) -> Optional[SessionInfo]:
        """Resume an existing session (create new agent in same workspace)."""
        workspace_dir = self.workspace_base / session_id
        if not workspace_dir.exists():
            # Fall back to registry for non-standard roots
            workspace_dir = self._session_registry.get(session_id, workspace_dir)
        if not workspace_dir.exists():
            return None

        # Create new agent instance in existing workspace
        user_id = resolve_user_id()
        self.current_agent = UniversalAgent(
            workspace_dir=str(workspace_dir),
            user_id=user_id,
            hooks=self._hooks,
        )
        await self.current_agent.initialize()

        self.current_session_id = session_id
        self._session_registry[session_id] = workspace_dir
        self._session_roots.add(workspace_dir.parent)

        return SessionInfo(
            session_id=session_id,
            workspace=str(workspace_dir),
            user_id="user_ui",
            session_url=self.current_agent.session.mcp.url if self.current_agent.session else None,
            logfire_enabled=bool(os.getenv("LOGFIRE_TOKEN")),
        )

    async def execute_query(self, query: str) -> AsyncGenerator[WebSocketEvent, None]:
        """Execute a query and yield WebSocket events."""
        if not self.current_agent:
            yield create_error_event("No active session. Create a session first.")
            return

        try:
            # Stream events from agent
            async for agent_event in self.current_agent.run_query(query):
                # Convert AgentEvent to WebSocketEvent
                ws_event = self._convert_agent_event(agent_event)
                yield ws_event

            # Send completion event
            yield WebSocketEvent(
                type=WSEventType.QUERY_COMPLETE,
                data={"session_id": self.current_session_id},
            )

        except Exception as e:
            yield create_error_event(str(e))

    def _convert_agent_event(self, agent_event: AgentEvent) -> WebSocketEvent:
        """Convert AgentEvent from agent_core to WebSocketEvent."""
        # Extract token usage from agent trace if available
        token_usage = None
        if self.current_agent and hasattr(self.current_agent, "trace"):
            token_usage = self.current_agent.trace.get("token_usage")

        data = agent_event.data.copy() if agent_event.data else {}
        if token_usage:
            data["token_usage"] = token_usage

        return WebSocketEvent(
            type=WSEventType(agent_event.type.value),
            data=data,
            timestamp=agent_event.timestamp,
        )

    def get_current_workspace(self) -> Optional[str]:
        """Get current workspace directory."""
        if self.current_agent:
            return self.current_agent.workspace_dir
        return None

    def list_sessions(self) -> list[dict]:
        """List all available sessions."""
        sessions: list[dict] = []

        def _session_entry(session_dir: Path) -> Optional[dict]:
            if not session_dir.exists() or not session_dir.is_dir():
                return None
            trace_file = session_dir / "trace.json"
            status = "complete" if trace_file.exists() else "incomplete"
            files = self._get_session_files(session_dir)
            return {
                "session_id": session_dir.name,
                "timestamp": session_dir.stat().st_mtime,
                "workspace_path": str(session_dir),
                "status": status,
                "files": files,
            }

        seen: set[str] = set()

        for session_id, session_dir in self._session_registry.items():
            entry = _session_entry(session_dir)
            if entry:
                sessions.append(entry)
                seen.add(entry["session_id"])

        for root in sorted(self._session_roots):
            if not root.exists():
                continue
            for session_dir in sorted(root.iterdir(), reverse=True):
                if (
                    session_dir.is_dir()
                    and session_dir.name.startswith("session_")
                    and session_dir.name not in seen
                ):
                    entry = _session_entry(session_dir)
                    if entry:
                        sessions.append(entry)
                        seen.add(entry["session_id"])

        sessions.sort(key=lambda row: row.get("timestamp", 0), reverse=True)
        return sessions[:50]

    def _get_session_files(self, session_dir: Path) -> dict:
        """Get files organized by type from a session."""
        files = {
            "work_products": [],
            "search_results": [],
            "workbench_activity": [],
            "other": [],
        }

        for item in session_dir.rglob("*"):
            if item.is_file():
                rel_path = item.relative_to(session_dir)
                path_str = str(rel_path)

                if "work_products" in path_str:
                    files["work_products"].append({
                        "name": item.name,
                        "path": path_str,
                        "size": item.stat().st_size,
                    })
                elif "search_results" in path_str:
                    files["search_results"].append({
                        "name": item.name,
                        "path": path_str,
                        "size": item.stat().st_size,
                    })
                elif "workbench_activity" in path_str:
                    files["workbench_activity"].append({
                        "name": item.name,
                        "path": path_str,
                        "size": item.stat().st_size,
                    })
                else:
                    files["other"].append({
                        "name": item.name,
                        "path": path_str,
                        "size": item.stat().st_size,
                    })

        return files

    def get_session_file(self, session_id: str, file_path: str) -> Optional[tuple[str, str, bytes]]:
        """Get file content from session. Returns (content_type, filename, content)."""
        session_dir = self.workspace_base / session_id
        if not session_dir.exists():
            return None

        file_full = session_dir / file_path
        if not file_full.exists() or not file_full.is_file():
            return None

        # Determine content type
        import mimetypes
        content_type, _ = mimetypes.guess_type(str(file_full))
        if not content_type:
            content_type = "application/octet-stream"

        # Read file content
        with open(file_full, "rb") as f:
            content = f.read()

        return (content_type, file_full.name, content)

    async def close(self) -> None:
        """Clean up the active agent session."""
        if self.current_agent:
            try:
                # Call UniversalAgent.close()
                if hasattr(self.current_agent, "close"):
                    await self.current_agent.close()
            except Exception:
                pass
            self.current_agent = None
        self.current_session_id = None
        self._session_registry.clear()


# Global bridge instance (lazy init to check env at runtime)
_agent_bridge = None
_gateway_bridge = None
_process_turn_bridge = None


def get_agent_bridge():
    """
    Get the global agent bridge instance.
    
    If UA_GATEWAY_URL is set, returns a GatewayBridge that forwards to the external
    gateway server. Otherwise, returns the in-process AgentBridge.
    
    This allows the Web UI to use the same canonical execution engine as the CLI
    when running in gateway mode.
    """
    global _agent_bridge, _gateway_bridge, _process_turn_bridge
    
    gateway_url = os.getenv("UA_GATEWAY_URL")
    force_legacy = os.getenv("UA_FORCE_LEGACY_AGENT_BRIDGE", "").lower() in {"1", "true", "yes"}
    
    if gateway_url:
        # Use gateway mode - forward to external gateway server
        if _gateway_bridge is None:
            from universal_agent.api.gateway_bridge import GatewayBridge
            _gateway_bridge = GatewayBridge(gateway_url)
            print(f"🌐 Web UI using external gateway: {gateway_url}")
        return _gateway_bridge

    if not force_legacy:
        # Use canonical process_turn path in-process
        if _process_turn_bridge is None:
            from universal_agent.api.process_turn_bridge import ProcessTurnBridge
            _process_turn_bridge = ProcessTurnBridge()
            print("🏠 Web UI using in-process gateway (process_turn)")
        return _process_turn_bridge

    # Use legacy direct mode - run agent in-process
    if _agent_bridge is None:
        _agent_bridge = AgentBridge()
        print("🏠 Web UI using in-process agent (legacy direct mode)")
    return _agent_bridge

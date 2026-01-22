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


class AgentBridge:
    """
    Bridge between FastAPI WebSocket server and UniversalAgent.

    Manages agent lifecycle, session persistence, and event conversion.
    """

    def __init__(self):
        self.current_agent: Optional[UniversalAgent] = None
        self.current_session_id: Optional[str] = None
        self.workspace_base = Path("AGENT_RUN_WORKSPACES").resolve()
        self.event_callbacks: list[Callable[[WebSocketEvent], None]] = []

    async def create_session(self, user_id: str = "user_ui") -> SessionInfo:
        """Create a new agent session."""
        # Create workspace directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_id = f"session_{timestamp}_{uuid.uuid4().hex[:8]}"
        workspace_dir = self.workspace_base / session_id
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # Initialize agent
        self.current_agent = UniversalAgent(
            workspace_dir=str(workspace_dir),
            user_id=user_id,
        )
        await self.current_agent.initialize()

        self.current_session_id = session_id

        # Build session info
        return SessionInfo(
            session_id=session_id,
            workspace=str(workspace_dir),
            user_id=user_id,
            session_url=self.current_agent.session.mcp.url if self.current_agent.session else None,
            logfire_enabled=bool(os.getenv("LOGFIRE_TOKEN")),
        )

    async def resume_session(self, session_id: str) -> Optional[SessionInfo]:
        """Resume an existing session (create new agent in same workspace)."""
        workspace_dir = self.workspace_base / session_id
        if not workspace_dir.exists():
            return None

        # Create new agent instance in existing workspace
        self.current_agent = UniversalAgent(
            workspace_dir=str(workspace_dir),
            user_id="user_ui",
        )
        await self.current_agent.initialize()

        self.current_session_id = session_id

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
        sessions = []
        if not self.workspace_base.exists():
            return sessions

        for session_dir in sorted(self.workspace_base.iterdir(), reverse=True):
            if session_dir.is_dir() and session_dir.name.startswith("session_"):
                # Check for trace.json to determine status
                trace_file = session_dir / "trace.json"
                status = "complete" if trace_file.exists() else "incomplete"

                # Get file info
                files = self._get_session_files(session_dir)

                sessions.append({
                    "session_id": session_dir.name,
                    "timestamp": session_dir.stat().st_mtime,
                    "workspace_path": str(session_dir),
                    "status": status,
                    "files": files,
                })

        return sessions[:50]  # Return latest 50

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


# Global bridge instance
_agent_bridge = AgentBridge()


def get_agent_bridge() -> AgentBridge:
    """Get the global agent bridge instance."""
    return _agent_bridge

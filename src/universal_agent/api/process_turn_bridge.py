"""
ProcessTurnBridge - In-process bridge that uses the canonical process_turn path.

This replaces AgentBridge for the Web UI when running without an external gateway,
so the UI and CLI share identical execution behavior.
"""

from __future__ import annotations

import os
from typing import AsyncGenerator, Optional

from universal_agent.agent_core import AgentEvent, EventType
from universal_agent.api.events import (
    EventType as WSEventType,
    SessionInfo,
    WebSocketEvent,
    create_error_event,
)
from universal_agent.gateway import GatewayRequest, GatewaySession, InProcessGateway
from universal_agent.identity import resolve_user_id
from universal_agent.run_catalog import RunCatalogService
from universal_agent.workspace_catalog import list_workspace_summaries


def _resolve_session_run_id(workspace_dir: str) -> Optional[str]:
    workspace_key = str(workspace_dir or "").strip()
    if not workspace_key:
        return None
    summary = RunCatalogService().find_run_for_workspace(workspace_key)
    if not summary:
        return None
    run_id = str(summary.get("run_id") or "").strip()
    return run_id or None


class ProcessTurnBridge:
    """Bridge between FastAPI server and InProcessGateway (process_turn)."""

    def __init__(self):
        self.gateway = InProcessGateway()
        self.current_session: Optional[GatewaySession] = None

    async def create_session(self, user_id: Optional[str] = None) -> SessionInfo:
        """Create a new session via the in-process gateway."""
        resolved_user_id = user_id or resolve_user_id()
        session = await self.gateway.create_session(user_id=resolved_user_id)
        self.current_session = session

        return SessionInfo(
            session_id=session.session_id,
            workspace=session.workspace_dir,
            user_id=session.user_id,
            session_url=None,
            logfire_enabled=bool(os.getenv("LOGFIRE_TOKEN")),
            run_id=_resolve_session_run_id(session.workspace_dir),
            is_live_session=True,
        )

    async def resume_session(self, session_id: str) -> Optional[SessionInfo]:
        """Resume an existing session via the in-process gateway."""
        try:
            session = await self.gateway.resume_session(session_id)
        except Exception:
            return None

        self.current_session = session
        return SessionInfo(
            session_id=session.session_id,
            workspace=session.workspace_dir,
            user_id=session.user_id,
            session_url=None,
            logfire_enabled=bool(os.getenv("LOGFIRE_TOKEN")),
            run_id=_resolve_session_run_id(session.workspace_dir),
            is_live_session=True,
        )

    async def execute_query(self, query: str) -> AsyncGenerator[WebSocketEvent, None]:
        """Execute a query and yield WebSocket events."""
        if not self.current_session:
            yield create_error_event("No active session. Create a session first.")
            return

        request = GatewayRequest(user_input=query)

        async for agent_event in self.gateway.execute(self.current_session, request):
            yield self._convert_agent_event(agent_event)

        yield WebSocketEvent(
            type=WSEventType.QUERY_COMPLETE,
            data={"session_id": self.current_session.session_id},
        )

    async def send_input_response(self, input_id: str, response: str) -> bool:
        """Send user input response back to the running process."""
        if not self.current_session:
            return False

        session_id = self.current_session.session_id
        if await self.gateway.resolve_input(session_id, input_id, response):
            return True

        # Fallback to adapter pending inputs (same strategy as gateway_server)
        adapter = self.gateway._adapters.get(session_id)
        if adapter and input_id in adapter._pending_inputs:
            future = adapter._pending_inputs.pop(input_id)
            if not future.done():
                future.set_result(response)
                return True

        return False

    async def send_cancel(self, reason: str = "User requested stop") -> bool:
        """Best-effort cancel for process_turn bridge (not yet supported)."""
        return False

    def get_current_workspace(self) -> Optional[str]:
        """Get current workspace directory."""
        if self.current_session:
            return self.current_session.workspace_dir
        return None

    def list_sessions(self) -> list[dict]:
        """List sessions via the gateway."""
        live_by_workspace = {
            str(s.workspace_dir): s
            for s in self.gateway.list_live_sessions()
        }
        sessions = list_workspace_summaries(
            self.gateway._workspace_base,
            limit=50,
            run_catalog=RunCatalogService(),
        )
        for item in sessions:
            live = live_by_workspace.get(str(item.get("workspace_path") or ""))
            if live is None:
                continue
            item["status"] = live.status
            item["metadata"] = live.metadata
            item["user_id"] = str(live.metadata.get("user_id") or "")
        return sessions

    async def close(self) -> None:
        """Release bridge resources for this connection scope."""
        await self.gateway.close()

    def _convert_agent_event(self, agent_event: AgentEvent) -> WebSocketEvent:
        """Convert AgentEvent from gateway to WebSocketEvent."""
        data = agent_event.data.copy() if agent_event.data else {}
        return WebSocketEvent(
            type=WSEventType(agent_event.type.value),
            data=data,
            timestamp=agent_event.timestamp,
        )

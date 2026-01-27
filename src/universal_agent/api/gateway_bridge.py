"""
Gateway Bridge - Forwards Web UI requests to the external Gateway Server.

When UA_GATEWAY_URL is set, this bridge acts as a thin proxy:
- Creates sessions via REST to the gateway
- Streams events via WebSocket from the gateway
- Converts gateway events to Web UI event format

This allows the Web UI to use the same canonical execution engine as the CLI.
"""

import asyncio
import json
import logging
import os
from typing import AsyncGenerator, Optional
from pathlib import Path

import httpx
import websockets

from universal_agent.api.events import (
    WebSocketEvent,
    EventType as WSEventType,
    SessionInfo,
    create_connected_event,
    create_error_event,
)

logger = logging.getLogger(__name__)


class GatewayBridge:
    """
    Bridge that forwards Web UI requests to the external Gateway Server.
    
    This replaces the in-process AgentBridge execution with gateway-based execution,
    ensuring the Web UI uses the same canonical engine as the CLI.
    """

    def __init__(self, gateway_url: str):
        self.gateway_url = gateway_url.rstrip("/")
        self.ws_url = self.gateway_url.replace("http://", "ws://").replace("https://", "wss://")
        self.current_session_id: Optional[str] = None
        self.current_workspace: Optional[str] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        
    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def create_session(self, user_id: Optional[str] = None) -> SessionInfo:
        """Create a new session via the gateway server."""
        client = await self._get_client()
        
        try:
            response = await client.post(
                f"{self.gateway_url}/api/v1/sessions",
                json={"user_id": user_id},
            )
            response.raise_for_status()
            data = response.json()
            
            self.current_session_id = data["session_id"]
            self.current_workspace = data["workspace_dir"]
            
            logger.info(f"Gateway session created: {self.current_session_id}")
            
            return SessionInfo(
                session_id=data["session_id"],
                workspace=data["workspace_dir"],
                user_id=data.get("user_id", user_id or "user_ui"),
                session_url=None,
                logfire_enabled=bool(os.getenv("LOGFIRE_TOKEN")),
            )
            
        except httpx.HTTPError as e:
            logger.error(f"Failed to create gateway session: {e}")
            raise RuntimeError(f"Gateway session creation failed: {e}")

    async def resume_session(self, session_id: str) -> Optional[SessionInfo]:
        """Resume an existing session via the gateway server."""
        client = await self._get_client()
        
        try:
            response = await client.get(
                f"{self.gateway_url}/api/v1/sessions/{session_id}"
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
            
            self.current_session_id = data["session_id"]
            self.current_workspace = data["workspace_dir"]
            
            return SessionInfo(
                session_id=data["session_id"],
                workspace=data["workspace_dir"],
                user_id=data.get("user_id", "user_ui"),
                session_url=None,
                logfire_enabled=bool(os.getenv("LOGFIRE_TOKEN")),
            )
            
        except httpx.HTTPError as e:
            logger.error(f"Failed to resume gateway session: {e}")
            return None

    async def execute_query(self, query: str) -> AsyncGenerator[WebSocketEvent, None]:
        """Execute a query via the gateway WebSocket and yield events."""
        if not self.current_session_id:
            yield create_error_event("No active session. Create a session first.")
            return

        ws_endpoint = f"{self.ws_url}/api/v1/sessions/{self.current_session_id}/stream"
        
        try:
            async with websockets.connect(ws_endpoint) as ws:
                # Wait for connected event from gateway
                initial_msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
                initial_data = json.loads(initial_msg)
                if initial_data.get("type") == "connected":
                    logger.info(f"Connected to gateway stream for session {self.current_session_id}")
                
                # Send execute request (gateway protocol)
                execute_msg = {
                    "type": "execute",
                    "data": {"user_input": query},
                }
                await ws.send(json.dumps(execute_msg))
                
                # Stream events from gateway
                async for message in ws:
                    try:
                        event_data = json.loads(message)
                        event_type_str = event_data.get("type", "")
                        
                        # Convert gateway event to Web UI event
                        ws_event = self._convert_gateway_event(event_type_str, event_data)
                        if ws_event:
                            yield ws_event
                        
                        # Check for completion
                        if event_type_str == "query_complete":
                            break
                            
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse gateway event: {e}")
                        
        except asyncio.TimeoutError:
            yield create_error_event("Gateway connection timeout")
        except websockets.exceptions.ConnectionClosed as e:
            yield create_error_event(f"Gateway connection closed: {e}")
        except Exception as e:
            logger.error(f"Gateway execution error: {e}")
            yield create_error_event(str(e))

    def _convert_gateway_event(self, event_type: str, event_data: dict) -> Optional[WebSocketEvent]:
        """Convert gateway event format to Web UI WebSocketEvent."""
        try:
            # Map gateway event types to Web UI event types
            type_map = {
                "text": WSEventType.TEXT,
                "tool_call": WSEventType.TOOL_CALL,
                "tool_result": WSEventType.TOOL_RESULT,
                "thinking": WSEventType.THINKING,
                "status": WSEventType.STATUS,
                "auth_required": WSEventType.AUTH_REQUIRED,
                "error": WSEventType.ERROR,
                "session_info": WSEventType.SESSION_INFO,
                "iteration_end": WSEventType.ITERATION_END,
                "work_product": WSEventType.WORK_PRODUCT,
                "connected": WSEventType.CONNECTED,
                "query_complete": WSEventType.QUERY_COMPLETE,
                "pong": WSEventType.PONG,
            }
            
            ws_type = type_map.get(event_type)
            if ws_type is None:
                logger.warning(f"Unknown gateway event type: {event_type}")
                return None
            
            return WebSocketEvent(
                type=ws_type,
                data=event_data.get("data", {}),
                timestamp=event_data.get("timestamp", 0),
            )
            
        except Exception as e:
            logger.error(f"Failed to convert gateway event: {e}")
            return None

    def get_current_workspace(self) -> Optional[str]:
        """Get current workspace directory."""
        return self.current_workspace

    def list_sessions(self) -> list[dict]:
        """List sessions from gateway (sync wrapper for async call)."""
        # For listing, we need to make a synchronous-ish call
        # This is used by REST endpoints, so we'll do it via a new event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Create a new task if we're already in an async context
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, self._list_sessions_async())
                    return future.result(timeout=10)
            else:
                return loop.run_until_complete(self._list_sessions_async())
        except Exception as e:
            logger.error(f"Failed to list sessions: {e}")
            return []

    async def _list_sessions_async(self) -> list[dict]:
        """List sessions from gateway."""
        client = await self._get_client()
        try:
            response = await client.get(f"{self.gateway_url}/api/v1/sessions")
            response.raise_for_status()
            data = response.json()
            return data.get("sessions", [])
        except Exception as e:
            logger.error(f"Failed to list gateway sessions: {e}")
            return []

    def get_session_file(self, session_id: str, file_path: str) -> Optional[tuple[str, str, bytes]]:
        """Get file content from session workspace."""
        # Files are stored locally in AGENT_RUN_WORKSPACES, not on gateway
        # So we read them directly
        base_dir = Path(__file__).parent.parent.parent.parent
        workspaces_dir = base_dir / "AGENT_RUN_WORKSPACES"
        session_dir = workspaces_dir / session_id
        
        if not session_dir.exists():
            return None
        
        file_full = session_dir / file_path
        try:
            file_full = file_full.resolve()
            if not str(file_full).startswith(str(session_dir.resolve())):
                return None
            if not file_full.exists():
                return None
            
            import mimetypes
            content_type, _ = mimetypes.guess_type(str(file_full))
            content_type = content_type or "application/octet-stream"
            
            with open(file_full, "rb") as f:
                content = f.read()
            
            return (content_type, file_full.name, content)
            
        except Exception as e:
            logger.error(f"Failed to read file {file_path}: {e}")
            return None

    async def close(self):
        """Close the HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

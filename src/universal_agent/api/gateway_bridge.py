"""
Gateway Bridge - Forwards Web UI requests to the external Gateway Server.

When UA_GATEWAY_URL is set, this bridge acts as a thin proxy:
- Creates sessions via REST to the gateway
- Streams events via WebSocket from the gateway
- Converts gateway events to Web UI event format

This allows the Web UI to use the same canonical execution engine as the CLI.
"""

import asyncio
import inspect
import json
import logging
import os
import time
from typing import Any, AsyncGenerator, Optional
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
from universal_agent.timeout_policy import (
    gateway_http_timeout_seconds,
    gateway_ws_handshake_timeout_seconds,
    websocket_connect_kwargs,
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
        self._current_ws: Any = None

    def _gateway_auth_token(self) -> str:
        return (
            (os.getenv("UA_INTERNAL_API_TOKEN") or "").strip()
            or (os.getenv("UA_OPS_TOKEN") or "").strip()
        )

    def _gateway_headers(self) -> dict[str, str]:
        token = self._gateway_auth_token()
        if not token:
            return {}
        return {
            "authorization": f"Bearer {token}",
            "x-ua-internal-token": token,
            "x-ua-ops-token": token,
        }

    def websocket_connect_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        headers = self._gateway_headers()
        if headers:
            items = list(headers.items())
            try:
                params = inspect.signature(websockets.connect).parameters
            except (TypeError, ValueError):
                params = {}
            if "additional_headers" in params:
                kwargs["additional_headers"] = items
            elif "extra_headers" in params:
                kwargs["extra_headers"] = items

        kwargs.update(websocket_connect_kwargs(websockets.connect))
        return kwargs
        
    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=gateway_http_timeout_seconds()
            )
        return self._http_client

    async def create_session(self, user_id: Optional[str] = None) -> SessionInfo:
        """Create a new session via the gateway server."""
        client = await self._get_client()
        
        try:
            response = await client.post(
                f"{self.gateway_url}/api/v1/sessions",
                json={"user_id": user_id},
                headers=self._gateway_headers(),
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
                f"{self.gateway_url}/api/v1/sessions/{session_id}",
                headers=self._gateway_headers(),
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
        saw_streaming_text = False
        
        try:
            async with websockets.connect(ws_endpoint, **self.websocket_connect_kwargs()) as ws:
                self._current_ws = ws
                # Wait for connected event from gateway
                initial_msg = await asyncio.wait_for(
                    ws.recv(),
                    timeout=gateway_ws_handshake_timeout_seconds(),
                )
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
                        if event_type_str == "text":
                            data_obj = event_data.get("data", {}) if isinstance(event_data.get("data"), dict) else {}
                            if event_data.get("time_offset") is not None:
                                saw_streaming_text = True
                            if saw_streaming_text and data_obj.get("final") is True:
                                continue
                        # Convert gateway event to Web UI event
                        ws_event = self._convert_gateway_event(event_type_str, event_data)
                        if ws_event:
                            yield ws_event
                        
                        # Handle interactive input response from Web UI
                        if event_type_str == "input_required":
                            # Store the websocket for this session to allow sending responses back
                            self._current_ws = ws
                        
                        # Check for completion
                        if event_type_str == "query_complete":
                            break
                            
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse gateway event: {e}")
            self._current_ws = None
                        
        except asyncio.TimeoutError:
            self._current_ws = None
            yield create_error_event("Gateway connection timeout")
        except websockets.exceptions.ConnectionClosed as e:
            self._current_ws = None
            yield create_error_event(f"Gateway connection closed: {e}")
        except Exception as e:
            self._current_ws = None
            logger.error(f"Gateway execution error: {e}")
            yield create_error_event(str(e))

    async def close(self) -> None:
        """Release http client resources held by the bridge."""
        self._current_ws = None
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    def _convert_gateway_event(self, event_type: str, event_data: dict) -> Optional[WebSocketEvent]:
        """Convert gateway event format to Web UI WebSocketEvent."""
        try:
            if event_type in {"heartbeat_summary", "heartbeat_indicator"}:
                payload = event_data.get("data", {}) if isinstance(event_data.get("data"), dict) else {}
                return WebSocketEvent(
                    type=WSEventType.SYSTEM_EVENT,
                    data={
                        "type": event_type,
                        "payload": payload,
                        "created_at": event_data.get("timestamp"),
                    },
                    timestamp=time.time(),
                )

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
                "cancelled": WSEventType.CANCELLED,
                "pong": WSEventType.PONG,
                "input_required": WSEventType.INPUT_REQUIRED,
                "input_response": WSEventType.INPUT_RESPONSE,
                "system_event": WSEventType.SYSTEM_EVENT,
                "system_presence": WSEventType.SYSTEM_PRESENCE,
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

    async def send_input_response(self, input_id: str, response: str) -> bool:
        """Send user input response back to the gateway."""
        if self._current_ws is None:
            logger.error("No active Gateway WebSocket to send input response")
            return False
        
        try:
            msg = {
                "type": "input_response",
                "data": {
                    "input_id": input_id,
                    "response": response
                }
            }
            await self._current_ws.send(json.dumps(msg))
            return True
        except Exception as e:
            logger.error(f"Failed to send input response to gateway: {e}")
            return False

    async def send_cancel(self, reason: str = "User requested stop") -> bool:
        """Forward cancel request to the active gateway stream."""
        if self._current_ws is None:
            logger.warning("No active Gateway WebSocket to send cancel")
            return False
        try:
            msg = {
                "type": "cancel",
                "data": {"reason": reason},
            }
            await self._current_ws.send(json.dumps(msg))
            return True
        except Exception as e:
            logger.error(f"Failed to send cancel to gateway: {e}")
            return False

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
            response = await client.get(
                f"{self.gateway_url}/api/v1/sessions",
                headers=self._gateway_headers(),
            )
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
        
        logger.info(f"DEBUG: get_session_file path={file_path} session_dir={session_dir}")

        # Try session directory first
        file_full = (session_dir / file_path).resolve()
        
        # Check if valid session path
        if str(file_full).startswith(str(session_dir.resolve())) and file_full.exists() and file_full.is_file():
            pass
        else:
             # Fallback: Check project root.
             # Handle API stripping leading slash from absolute paths (browser normalizes // to /)
             path_str = str(file_path)
             session_dir_str = str(session_dir)
             
             if not path_str.startswith("/"):
                 # Check if it matches session_dir stripped
                 session_no_slash = session_dir_str.lstrip("/")
                 if path_str.startswith(session_no_slash):
                     path_str = "/" + path_str
                 elif path_str.startswith("home/") or path_str.startswith("Users/"): 
                     path_str = "/" + path_str
             
             file_chk = Path(path_str)
             
             rel_path = None
             try:
                 # Check if the requested path is relative to session_dir
                 rel_path = file_chk.relative_to(session_dir)
             except ValueError:
                 pass
            
             base_resolved = base_dir.resolve()
             file_project = None

             if rel_path:
                 file_project = (session_dir / rel_path).resolve()
             elif not file_chk.is_absolute():
                 # Maybe request was just "web-ui/page.tsx" (relative)
                 file_project = (base_dir / file_path).resolve()

             if file_project and str(file_project).startswith(str(base_resolved)) and file_project.exists() and file_project.is_file():
                 file_full = file_project
             else:
                 return None

        try:
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

"""
Universal Agent FastAPI Server - Modern Web API for the UI.

Provides REST API endpoints and WebSocket for real-time communication
with the Universal Agent system.

Server runs on port 8001 by default (configurable via PORT env var).
"""

import asyncio
import time
import json
import logging
import mimetypes
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Project paths
BASE_DIR = Path(__file__).parent.parent.parent.parent
WORKSPACES_DIR = BASE_DIR / "AGENT_RUN_WORKSPACES"
ARTIFACTS_DIR = Path(os.getenv("UA_ARTIFACTS_DIR", str(BASE_DIR / "artifacts"))).expanduser()
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

# Import agent bridge
from universal_agent.api.agent_bridge import get_agent_bridge
from universal_agent.api.events import (
    WebSocketEvent,
    EventType as WSEventType,
    SessionInfo,
    create_connected_event,
    create_error_event,
    ApprovalResponse,
)


# =============================================================================
# Pydantic Models for REST API
# =============================================================================


class ChatRequest(BaseModel):
    """Request to start a new chat."""
    query: str
    session_id: Optional[str] = None  # Resume existing session
    user_id: Optional[str] = "user_ui"


class ApprovalRequest(BaseModel):
    """Approval for URW phase."""
    phase_id: str
    approved: bool
    followup_input: Optional[str] = None


class SessionCreateRequest(BaseModel):
    """Request to create a new session."""
    user_id: Optional[str] = "user_ui"


# =============================================================================
# Connection Manager for WebSocket
# =============================================================================


class ConnectionManager:
    """Manages WebSocket connections."""

    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, connection_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[connection_id] = websocket
        logger.info(f"WebSocket connected: {connection_id}")

    def disconnect(self, connection_id: str):
        if connection_id in self.active_connections:
            del self.active_connections[connection_id]
            logger.info(f"WebSocket disconnected: {connection_id}")

    async def send_event(self, connection_id: str, event: WebSocketEvent):
        if connection_id in self.active_connections:
            await self.active_connections[connection_id].send_text(event.to_json())

    async def broadcast(self, event: WebSocketEvent):
        for connection in self.active_connections.values():
            try:
                await connection.send_text(event.to_json())
            except Exception:
                pass


manager = ConnectionManager()


# =============================================================================
# Lifespan Manager
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    logger.info("🚀 Universal Agent API Server starting...")
    logger.info(f"📁 Workspaces: {WORKSPACES_DIR}")

    # Ensure workspaces directory exists
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)

    yield

    logger.info("👋 Universal Agent API Server shutting down...")


# =============================================================================
# FastAPI App
# =============================================================================

app = FastAPI(
    title="Universal Agent API",
    description="Modern Web API for Universal Agent System",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS middleware - allow frontend on different port
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For development - restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# REST API Endpoints
# =============================================================================


@app.get("/")
async def root():
    """API root endpoint."""
    return {
        "name": "Universal Agent API",
        "version": "2.0.0",
        "status": "running",
        "endpoints": {
            "websocket": "/ws/agent",
            "sessions": "/api/sessions",
            "files": "/api/files",
            "health": "/api/health",
        },
    }


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0",
    }


@app.post("/api/sessions")
async def create_session(request: SessionCreateRequest):
    """Create a new agent session."""
    bridge = get_agent_bridge()
    try:
        session_info = await bridge.create_session(
            user_id=request.user_id or "user_ui"
        )
        return {
            "session_id": session_info.session_id,
            "workspace": session_info.workspace,
            "user_id": session_info.user_id,
        }
    except Exception as e:
        logger.error(f"Failed to create session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sessions")
async def list_sessions():
    """List all agent sessions."""
    bridge = get_agent_bridge()
    sessions = bridge.list_sessions()
    return {"sessions": sessions}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """Get session details."""
    bridge = get_agent_bridge()
    session_dir = WORKSPACES_DIR / session_id

    if not session_dir.exists():
        raise HTTPException(status_code=404, detail="Session not found")

    # Get trace file if exists
    trace_file = session_dir / "trace.json"
    trace_data = None
    if trace_file.exists():
        try:
            with open(trace_file) as f:
                trace_data = json.load(f)
        except Exception:
            pass

    return {
        "session_id": session_id,
        "workspace": str(session_dir),
        "trace": trace_data,
    }


@app.get("/api/files")
async def list_files(session_id: Optional[str] = None, path: str = ""):
    """List files in a session workspace."""
    bridge = get_agent_bridge()

    # Determine which workspace to use
    if session_id:
        workspace = WORKSPACES_DIR / session_id
    else:
        workspace = Path(bridge.get_current_workspace() or "")

    if not workspace.exists():
        return {"files": [], "error": "Workspace not found"}

    # Navigate to path
    target_path = workspace / path if path else workspace

    # Security check
    try:
        target_path = target_path.resolve()
        workspace_resolved = workspace.resolve()
        if not str(target_path).startswith(str(workspace_resolved)):
            raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not target_path.exists():
        return {"files": [], "path": path}

    if target_path.is_file():
        return {"files": [], "path": path, "is_file": True}

    files = []
    for item in sorted(target_path.iterdir()):
        try:
            stat = item.stat()
            file_info = {
                "name": item.name,
                "path": str(item.relative_to(workspace)),
                "is_dir": item.is_dir(),
                "size": stat.st_size if item.is_file() else None,
                "modified": stat.st_mtime,
            }
            files.append(file_info)
        except Exception:
            pass

    return {
        "files": files,
        "path": path,
        "workspace": str(workspace),
    }


@app.get("/api/files/{session_id}/{file_path:path}")
async def get_file(session_id: str, file_path: str):
    """Get file content from session workspace."""
    bridge = get_agent_bridge()
    result = bridge.get_session_file(session_id, file_path)

    if result is None:
        raise HTTPException(status_code=404, detail="File not found")

    content_type, filename, content = result

    # For HTML files, return as HTML
    if filename.endswith(".html"):
        return Response(content=content, media_type="text/html")

    # For JSON files, return as JSON
    if filename.endswith(".json"):
        try:
            data = json.loads(content.decode("utf-8"))
            # Return pretty-printed JSON
            return Response(content=json.dumps(data, indent=2), media_type="application/json")
        except Exception:
            pass

    # For text files, return as text
    if filename.endswith((".txt", ".md", ".log", ".py", ".js", ".ts", ".tsx", ".css")):
        return Response(content=content, media_type="text/plain")

    # Default: return as download
    return Response(content=content, media_type=content_type)


@app.get("/api/artifacts")
async def list_artifacts(path: str = ""):
    """List files under the persistent artifacts root."""
    root = ARTIFACTS_DIR
    target_path = root / path if path else root

    # Security check
    try:
        target_path = target_path.resolve()
        root_resolved = root.resolve()
        if not str(target_path).startswith(str(root_resolved)):
            raise HTTPException(status_code=403, detail="Access denied")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not target_path.exists():
        return {"files": [], "path": path, "artifacts_root": str(root)}

    if target_path.is_file():
        return {"files": [], "path": path, "is_file": True, "artifacts_root": str(root)}

    files = []
    for item in sorted(target_path.iterdir()):
        try:
            stat = item.stat()
            files.append(
                {
                    "name": item.name,
                    "path": str(item.relative_to(root)),
                    "is_dir": item.is_dir(),
                    "size": stat.st_size if item.is_file() else None,
                    "modified": stat.st_mtime,
                }
            )
        except Exception:
            pass

    return {"files": files, "path": path, "artifacts_root": str(root)}


@app.get("/api/artifacts/files/{file_path:path}")
async def get_artifact_file(file_path: str):
    """Get file content from the persistent artifacts root."""
    root = ARTIFACTS_DIR
    target = (root / file_path)

    # Security check
    try:
        target_resolved = target.resolve()
        root_resolved = root.resolve()
        if not str(target_resolved).startswith(str(root_resolved)):
            raise HTTPException(status_code=403, detail="Access denied")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not target_resolved.exists() or not target_resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    content = target_resolved.read_bytes()
    mime, _ = mimetypes.guess_type(str(target_resolved))
    content_type = mime or "application/octet-stream"

    filename = target_resolved.name
    if filename.endswith(".html"):
        return Response(content=content, media_type="text/html")
    if filename.endswith(".json"):
        try:
            data = json.loads(content.decode("utf-8"))
            return Response(content=json.dumps(data, indent=2), media_type="application/json")
        except Exception:
            pass
    if filename.endswith((".txt", ".md", ".log", ".py", ".js", ".ts", ".tsx", ".css")):
        return Response(content=content, media_type="text/plain")

    return Response(content=content, media_type=content_type)


@app.post("/api/approvals")
async def submit_approval(approval: ApprovalRequest):
    """Submit approval for URW phase."""
    # For now, just acknowledge - actual approval handling would be in the agent
    return {
        "status": "approved" if approval.approved else "rejected",
        "phase_id": approval.phase_id,
    }


# =============================================================================
# WebSocket Endpoint
# =============================================================================


@app.websocket("/ws/agent")
async def websocket_agent(websocket: WebSocket, session_id: Optional[str] = None):
    """
    WebSocket endpoint for real-time agent communication.

    Protocol:
    - Client sends: {"type": "query", "text": "..."} or {"type": "ping"}
    - Server sends: WebSocketEvent objects as JSON
    """
    connection_id = f"conn_{datetime.now().timestamp()}"

    await manager.connect(connection_id, websocket)
    bridge = get_agent_bridge()
    in_flight = False
    last_query_text: Optional[str] = None
    last_query_ts: Optional[float] = None
    gateway_forward_task: Optional[asyncio.Task] = None
    query_stream_event_types = {
        "text",
        "tool_call",
        "tool_result",
        "thinking",
        "status",
        "iteration_end",
        "query_complete",
        "work_product",
        "auth_required",
        "error",
        "input_required",
        "input_response",
        "pong",
    }

    try:
        # Send connected event
        session_info = None
        if session_id:
            logger.info(f"Attempting to resume session: {session_id}")
            session_info = await bridge.resume_session(session_id)
            if not session_info:
                logger.warning(f"Session {session_id} not found, creating new one.")

        if not session_info:
             session_info = await bridge.create_session()

        await manager.send_event(
            connection_id,
            create_connected_event(session_info),
        )

        # In gateway mode, keep a passive subscription to the gateway session stream
        # so background broadcasts (heartbeat, system events) appear in the Web UI.
        gateway_url = os.getenv("UA_GATEWAY_URL")
        if gateway_url:
            from universal_agent.api.gateway_bridge import GatewayBridge

            converter = GatewayBridge(gateway_url)
            session_id = session_info.session_id
            ws_endpoint = f"{converter.ws_url}/api/v1/sessions/{session_id}/stream"

            async def _forward_gateway_broadcasts() -> None:
                while True:
                    try:
                        async with websockets.connect(ws_endpoint) as ws:
                            # Initial "connected" message from the gateway stream
                            try:
                                initial_msg = await asyncio.wait_for(ws.recv(), timeout=10.0)
                                initial_data = json.loads(initial_msg)
                                if initial_data.get("type") != "connected":
                                    logger.warning("Unexpected gateway handshake message: %s", initial_data)
                            except Exception:
                                # Best-effort handshake; continue anyway.
                                pass

                            async for message in ws:
                                try:
                                    event_data = json.loads(message)
                                except json.JSONDecodeError:
                                    continue

                                event_type = (event_data.get("type") or "").strip()
                                if not event_type or event_type == "connected":
                                    continue
                                # When this UI connection is already streaming an active query via
                                # bridge.execute_query(), suppress duplicate runtime events from the
                                # passive gateway forwarder and keep only background/system signals.
                                if in_flight and event_type in query_stream_event_types:
                                    continue

                                ws_event = converter._convert_gateway_event(event_type, event_data)
                                if ws_event:
                                    await manager.send_event(connection_id, ws_event)
                    except asyncio.CancelledError:
                        return
                    except Exception as exc:
                        logger.warning("Gateway broadcast forwarder error: %s", exc)
                        await asyncio.sleep(1.0)

            gateway_forward_task = asyncio.create_task(_forward_gateway_broadcasts())

        # Main message loop
        while True:
            # Receive message from client
            data = await websocket.receive_text()

            try:
                # Parse message
                client_event = WebSocketEvent.from_json(data)
                if client_event.type == WSEventType.QUERY:
                    query = client_event.data.get("text", "")
                    if query.strip():
                        now_ts = time.time()
                        if in_flight:
                            logger.warning("Duplicate query ignored (in_flight)", extra={"query": query[:200]})
                            continue
                        if last_query_text == query and last_query_ts and (now_ts - last_query_ts) < 2.0:
                            logger.warning("Duplicate query ignored (recent)", extra={"query": query[:200]})
                            continue
                        in_flight = True
                        last_query_text = query
                        last_query_ts = now_ts
                        # Run query in background task to avoid deadlocking the message loop.
                        # This allows us to receive INPUT_RESPONSE while the query is still active.
                        async def stream_query():
                            try:
                                async for agent_event in bridge.execute_query(query):
                                    await manager.send_event(connection_id, agent_event)
                            except Exception as e:
                                logger.error(f"Error streaming query: {e}")
                                await manager.send_event(connection_id, create_error_event(str(e)))
                            finally:
                                nonlocal in_flight
                                in_flight = False
                        
                        asyncio.create_task(stream_query())

                elif client_event.type == WSEventType.INPUT_RESPONSE:
                    # Handle interactive input response from Web UI
                    input_id = client_event.data.get("input_id")
                    response = client_event.data.get("response", "")
                    if input_id:
                        from universal_agent.api.gateway_bridge import GatewayBridge
                        from universal_agent.api.process_turn_bridge import ProcessTurnBridge
                        if isinstance(bridge, GatewayBridge):
                            await bridge.send_input_response(input_id, response)
                        elif isinstance(bridge, ProcessTurnBridge):
                            await bridge.send_input_response(input_id, response)
                        else:
                            # Legacy local mode - no input bridge available
                            pass

                elif client_event.type == WSEventType.PING:
                    # Send pong
                    await manager.send_event(
                        connection_id,
                        WebSocketEvent(type=WSEventType.PONG),
                    )

            except json.JSONDecodeError:
                await manager.send_event(
                    connection_id,
                    create_error_event("Invalid JSON format"),
                )
            except Exception as e:
                logger.error(f"Error handling message: {e}")
                await manager.send_event(
                    connection_id,
                    create_error_event(str(e)),
                )

    except WebSocketDisconnect:
        manager.disconnect(connection_id)
        logger.info(f"WebSocket disconnected normally: {connection_id}")

    except Exception as e:
        manager.disconnect(connection_id)
        logger.error(f"WebSocket error: {e}")
    finally:
        if gateway_forward_task:
            gateway_forward_task.cancel()
            try:
                await gateway_forward_task
            except Exception:
                pass


# =============================================================================
# Main Entry Point
# =============================================================================


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("UA_API_PORT", "8001"))
    host = os.getenv("UA_API_HOST", "0.0.0.0")

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║         UNIVERSAL AGENT API SERVER v2.0                      ║
║══════════════════════════════════════════════════════════════║
║  API:     http://{host}:{port}
║  WebSocket: ws://{host}:{port}/ws/agent
║  Docs:    http://{host}:{port}/docs
╚══════════════════════════════════════════════════════════════╝
    """)

    uvicorn.run(app, host=host, port=port, log_level="info")

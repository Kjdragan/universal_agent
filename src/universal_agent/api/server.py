"""
Universal Agent FastAPI Server - Modern Web API for the UI.

Provides REST API endpoints and WebSocket for real-time communication
with the Universal Agent system.

Server runs on port 8001 by default (configurable via PORT env var).
"""

import asyncio
import json
import logging
import mimetypes
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

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
async def websocket_agent(websocket: WebSocket):
    """
    WebSocket endpoint for real-time agent communication.

    Protocol:
    - Client sends: {"type": "query", "text": "..."} or {"type": "ping"}
    - Server sends: WebSocketEvent objects as JSON
    """
    connection_id = f"conn_{datetime.now().timestamp()}"

    await manager.connect(connection_id, websocket)
    bridge = get_agent_bridge()

    try:
        # Send connected event
        session_info = await bridge.create_session()
        await manager.send_event(
            connection_id,
            create_connected_event(session_info),
        )

        # Main message loop
        while True:
            # Receive message from client
            data = await websocket.receive_text()

            try:
                # Parse message
                client_event = WebSocketEvent.from_json(data)

                # Handle different event types
                if client_event.type == WSEventType.QUERY:
                    query = client_event.data.get("text", "")
                    if query.strip():
                        # Stream agent events
                        async for agent_event in bridge.execute_query(query):
                            await manager.send_event(connection_id, agent_event)

                elif client_event.type == WSEventType.APPROVAL:
                    # Handle approval response
                    phase_id = client_event.data.get("phase_id")
                    approved = client_event.data.get("approved", True)
                    # TODO: Integrate with URW orchestrator

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

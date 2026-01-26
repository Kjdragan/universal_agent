"""
Universal Agent Gateway Server — External HTTP/WebSocket API.

Exposes the InProcessGateway as a standalone service for external clients.
Server runs on port 8002 by default (configurable via UA_GATEWAY_PORT env var).

Usage:
    python -m universal_agent.gateway_server
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from universal_agent.gateway import (
    InProcessGateway,
    GatewaySession,
    GatewayRequest,
    GatewaySessionSummary,
)
from universal_agent.agent_core import AgentEvent, EventType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent
WORKSPACES_DIR = BASE_DIR / "AGENT_RUN_WORKSPACES"


# =============================================================================
# Pydantic Models
# =============================================================================


class CreateSessionRequest(BaseModel):
    user_id: Optional[str] = "user_gateway"
    workspace_dir: Optional[str] = None


class CreateSessionResponse(BaseModel):
    session_id: str
    user_id: str
    workspace_dir: str
    metadata: dict = {}


class SessionSummaryResponse(BaseModel):
    session_id: str
    workspace_dir: str
    status: str
    metadata: dict = {}


class ExecuteRequest(BaseModel):
    user_input: str
    force_complex: bool = False
    metadata: dict = {}


class GatewayEventWire(BaseModel):
    type: str
    data: dict
    timestamp: str


# =============================================================================
# Gateway Singleton
# =============================================================================

_gateway: Optional[InProcessGateway] = None
_sessions: dict[str, GatewaySession] = {}


def get_gateway() -> InProcessGateway:
    global _gateway
    if _gateway is None:
        _gateway = InProcessGateway()
    return _gateway


def store_session(session: GatewaySession) -> None:
    _sessions[session.session_id] = session


def get_session(session_id: str) -> Optional[GatewaySession]:
    return _sessions.get(session_id)


# =============================================================================
# WebSocket Connection Manager
# =============================================================================


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, connection_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[connection_id] = websocket
        logger.info(f"Gateway WebSocket connected: {connection_id}")

    def disconnect(self, connection_id: str):
        if connection_id in self.active_connections:
            del self.active_connections[connection_id]
            logger.info(f"Gateway WebSocket disconnected: {connection_id}")

    async def send_json(self, connection_id: str, data: dict):
        if connection_id in self.active_connections:
            await self.active_connections[connection_id].send_text(json.dumps(data))


manager = ConnectionManager()


# =============================================================================
# Lifespan
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Universal Agent Gateway Server starting...")
    logger.info(f"📁 Workspaces: {WORKSPACES_DIR}")
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    yield
    logger.info("👋 Universal Agent Gateway Server shutting down...")


# =============================================================================
# FastAPI App
# =============================================================================

app = FastAPI(
    title="Universal Agent Gateway",
    description="External HTTP/WebSocket Gateway for Universal Agent",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# REST Endpoints
# =============================================================================


@app.get("/")
async def root():
    return {
        "name": "Universal Agent Gateway",
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "sessions": "/api/v1/sessions",
            "stream": "/api/v1/sessions/{session_id}/stream",
            "health": "/api/v1/health",
        },
    }


@app.get("/api/v1/health")
async def health():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0",
    }


@app.post("/api/v1/sessions", response_model=CreateSessionResponse)
async def create_session(request: CreateSessionRequest):
    gateway = get_gateway()
    try:
        session = await gateway.create_session(
            user_id=request.user_id or "user_gateway",
            workspace_dir=request.workspace_dir,
        )
        store_session(session)
        return CreateSessionResponse(
            session_id=session.session_id,
            user_id=session.user_id,
            workspace_dir=session.workspace_dir,
            metadata=session.metadata,
        )
    except Exception as e:
        logger.error(f"Failed to create session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/sessions")
async def list_sessions():
    gateway = get_gateway()
    summaries = gateway.list_sessions()
    return {
        "sessions": [
            SessionSummaryResponse(
                session_id=s.session_id,
                workspace_dir=s.workspace_dir,
                status=s.status,
                metadata=s.metadata,
            ).model_dump()
            for s in summaries
        ]
    }


@app.get("/api/v1/sessions/{session_id}")
async def get_session_info(session_id: str):
    session = get_session(session_id)
    if not session:
        gateway = get_gateway()
        try:
            session = await gateway.resume_session(session_id)
            store_session(session)
        except ValueError:
            raise HTTPException(status_code=404, detail="Session not found")
    return CreateSessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        workspace_dir=session.workspace_dir,
        metadata=session.metadata,
    )


@app.delete("/api/v1/sessions/{session_id}")
async def delete_session(session_id: str):
    if session_id in _sessions:
        del _sessions[session_id]
    return {"status": "deleted", "session_id": session_id}


# =============================================================================
# WebSocket Streaming Endpoint
# =============================================================================


def agent_event_to_wire(event: AgentEvent) -> dict:
    return {
        "type": event.type.value if hasattr(event.type, "value") else str(event.type),
        "data": event.data,
        "timestamp": datetime.now().isoformat(),
        "time_offset": event.data.get("time_offset") if isinstance(event.data, dict) else None,
    }


@app.websocket("/api/v1/sessions/{session_id}/stream")
async def websocket_stream(websocket: WebSocket, session_id: str):
    connection_id = f"gw_{session_id}_{time.time()}"
    await manager.connect(connection_id, websocket)

    gateway = get_gateway()
    session = get_session(session_id)

    if not session:
        try:
            session = await gateway.resume_session(session_id)
            store_session(session)
        except ValueError:
            await websocket.close(code=4004, reason="Session not found")
            manager.disconnect(connection_id)
            return

    await manager.send_json(
        connection_id,
        {
            "type": "connected",
            "data": {
                "session_id": session.session_id,
                "workspace_dir": session.workspace_dir,
            },
            "timestamp": datetime.now().isoformat(),
        },
    )

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                msg_type = msg.get("type", "")

                if msg_type == "execute":
                    user_input = msg.get("data", {}).get("user_input", "")
                    if not user_input.strip():
                        await manager.send_json(
                            connection_id,
                            {
                                "type": "error",
                                "data": {"message": "Empty user_input"},
                                "timestamp": datetime.now().isoformat(),
                            },
                        )
                        continue

                    request = GatewayRequest(
                        user_input=user_input,
                        force_complex=msg.get("data", {}).get("force_complex", False),
                        metadata=msg.get("data", {}).get("metadata", {}),
                    )

                    async for event in gateway.execute(session, request):
                        await manager.send_json(connection_id, agent_event_to_wire(event))

                    await manager.send_json(
                        connection_id,
                        {
                            "type": "query_complete",
                            "data": {},
                            "timestamp": datetime.now().isoformat(),
                        },
                    )

                elif msg_type == "ping":
                    await manager.send_json(
                        connection_id,
                        {"type": "pong", "data": {}, "timestamp": datetime.now().isoformat()},
                    )

                else:
                    await manager.send_json(
                        connection_id,
                        {
                            "type": "error",
                            "data": {"message": f"Unknown message type: {msg_type}"},
                            "timestamp": datetime.now().isoformat(),
                        },
                    )

            except json.JSONDecodeError:
                await manager.send_json(
                    connection_id,
                    {
                        "type": "error",
                        "data": {"message": "Invalid JSON"},
                        "timestamp": datetime.now().isoformat(),
                    },
                )
            except Exception as e:
                logger.error(f"Error handling message: {e}")
                await manager.send_json(
                    connection_id,
                    {
                        "type": "error",
                        "data": {"message": str(e)},
                        "timestamp": datetime.now().isoformat(),
                    },
                )

    except WebSocketDisconnect:
        manager.disconnect(connection_id)
        logger.info(f"Gateway WebSocket disconnected: {connection_id}")
    except Exception as e:
        manager.disconnect(connection_id)
        logger.error(f"Gateway WebSocket error: {e}")


# =============================================================================
# Main Entry Point
# =============================================================================


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("UA_GATEWAY_PORT", "8002"))
    host = os.getenv("UA_GATEWAY_HOST", "0.0.0.0")

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║         UNIVERSAL AGENT GATEWAY SERVER v1.0                  ║
╠══════════════════════════════════════════════════════════════╣
║  REST:      http://{host}:{port}/api/v1/sessions
║  WebSocket: ws://{host}:{port}/api/v1/sessions/{{id}}/stream
║  Docs:      http://{host}:{port}/docs
╚══════════════════════════════════════════════════════════════╝
    """)

    uvicorn.run(app, host=host, port=port, log_level="info")

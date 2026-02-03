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

from dotenv import load_dotenv

# Load .env early so SDK/CLI subprocesses inherit API keys and settings.
BASE_DIR = Path(__file__).parent.parent.parent
load_dotenv(BASE_DIR / ".env", override=False)

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
from universal_agent.feature_flags import heartbeat_enabled, memory_index_enabled
from universal_agent.identity import resolve_user_id
from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
from universal_agent.durable.migrations import ensure_schema
from universal_agent.heartbeat_service import HeartbeatService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Feature flags (placeholders, no runtime behavior changes yet)
HEARTBEAT_ENABLED = heartbeat_enabled()
MEMORY_INDEX_ENABLED = memory_index_enabled()

# 1. Configurable Workspaces Directory
# Default to AGENT_RUN_WORKSPACES in project root, but allow override via env var
_default_ws_dir = BASE_DIR / "AGENT_RUN_WORKSPACES"
env_ws_dir = os.getenv("UA_WORKSPACES_DIR")
if env_ws_dir:
    WORKSPACES_DIR = Path(env_ws_dir).resolve()
    logger.info(f"📁 Workspaces Directory Overridden: {WORKSPACES_DIR}")
else:
    WORKSPACES_DIR = _default_ws_dir

# 2. Allowlist Configuration
ALLOWED_USERS = set()
_allowed_users_str = os.getenv("UA_ALLOWED_USERS", "").strip()
if _allowed_users_str:
    ALLOWED_USERS = {u.strip() for u in _allowed_users_str.split(",") if u.strip()}
    logger.info(f"🔒 Authenticated Access Only. Allowed Users: {len(ALLOWED_USERS)}")
else:
    logger.info("🔓 Public Access Mode (No Allowlist configured)")


# =============================================================================
# Pydantic Models
# =============================================================================


class CreateSessionRequest(BaseModel):
    user_id: Optional[str] = None
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
_heartbeat_service: Optional[HeartbeatService] = None


def _read_run_log_tail(workspace_dir: str, max_bytes: int = 4096) -> Optional[str]:
    log_path = Path(workspace_dir) / "run.log"
    if not log_path.exists():
        return None
    try:
        with log_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(size - max_bytes, 0))
            return handle.read().decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning("Failed to read run.log tail: %s", e)
        return None


def get_gateway() -> InProcessGateway:
    global _gateway
    if _gateway is None:
        # Pass the configured workspace base to the gateway
        _gateway = InProcessGateway(workspace_base=WORKSPACES_DIR)
    return _gateway


def store_session(session: GatewaySession) -> None:
    _sessions[session.session_id] = session


def get_session(session_id: str) -> Optional[GatewaySession]:
    return _sessions.get(session_id)


def is_user_allowed(user_id: str) -> bool:
    """Check if user_id is in the allowlist (if active)."""
    if not ALLOWED_USERS:
        return True
    if user_id in ALLOWED_USERS:
        return True
    # Support numeric Telegram IDs in allowlist (e.g., "7843395933")
    if user_id.startswith("telegram_"):
        telegram_id = user_id.split("telegram_", 1)[1]
        return telegram_id in ALLOWED_USERS
    return False


# =============================================================================
# WebSocket Connection Manager
# =============================================================================


class ConnectionManager:
    def __init__(self):
        # connection_id -> WebSocket
        self.active_connections: dict[str, WebSocket] = {}
        # session_id -> set of connection_ids
        self.session_connections: dict[str, set[str]] = {}

    async def connect(self, connection_id: str, websocket: WebSocket, session_id: str):
        await websocket.accept()
        self.active_connections[connection_id] = websocket
        
        if session_id not in self.session_connections:
            self.session_connections[session_id] = set()
        self.session_connections[session_id].add(connection_id)
        
        logger.info(f"Gateway WebSocket connected: {connection_id} (session: {session_id})")

    def disconnect(self, connection_id: str, session_id: str):
        if connection_id in self.active_connections:
            del self.active_connections[connection_id]
            logger.info(f"Gateway WebSocket disconnected: {connection_id}")
            
        if session_id in self.session_connections:
            self.session_connections[session_id].discard(connection_id)
            if not self.session_connections[session_id]:
                del self.session_connections[session_id]

    async def send_json(self, connection_id: str, data: dict):
        if connection_id in self.active_connections:
            try:
                await self.active_connections[connection_id].send_text(json.dumps(data))
            except Exception as e:
                logger.error(f"Failed to send to {connection_id}: {e}")

    async def broadcast(self, session_id: str, data: dict, exclude_connection_id: Optional[str] = None):
        """Send a message to all connections associated with a session_id."""
        if session_id not in self.session_connections:
            return

        payload = json.dumps(data)
        # Snapshot the list to avoid runtime errors if connections drop during iteration
        targets = list(self.session_connections[session_id])
        
        for connection_id in targets:
            if connection_id == exclude_connection_id:
                continue
                
            if connection_id in self.active_connections:
                try:
                    await self.active_connections[connection_id].send_text(payload)
                except Exception as e:
                    logger.error(f"Failed to broadcast to {connection_id}: {e}")


manager = ConnectionManager()


# =============================================================================
# Lifespan
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Universal Agent Gateway Server starting...")
    logger.info(f"📁 Workspaces: {WORKSPACES_DIR}")
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    
    # Initialize runtime database (required by ProcessTurnAdapter -> setup_session)
    import universal_agent.main as main_module
    db_path = get_runtime_db_path()
    logger.info(f"📊 Connecting to runtime DB: {db_path}")
    main_module.runtime_db_conn = connect_runtime_db(db_path)
    # Enable WAL mode for concurrent access (CLI + gateway can coexist)
    main_module.runtime_db_conn.execute("PRAGMA journal_mode=WAL")
    main_module.runtime_db_conn.execute("PRAGMA busy_timeout=5000")
    ensure_schema(main_module.runtime_db_conn)
    
    # Load budget config (defined in main.py)
    main_module.budget_config = main_module.load_budget_config()
    
    # Initialize Heartbeat Service
    global _heartbeat_service
    if HEARTBEAT_ENABLED:
        logger.info("💓 Heartbeat System ENABLED")
        _heartbeat_service = HeartbeatService(get_gateway(), manager)
        await _heartbeat_service.start()
    else:
        logger.info("💤 Heartbeat System DISABLED (feature flag)")
    
    yield
    
    # Cleanup
    if _heartbeat_service:
        await _heartbeat_service.stop()
        
    if main_module.runtime_db_conn:
        main_module.runtime_db_conn.close()
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


from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Response, status

# ... (imports)

@app.get("/api/v1/health")
async def health(response: Response):
    """
    Deep health check associated with DB connectivity.
    """
    import universal_agent.main as main_module
    
    db_status = "unknown"
    db_error = None
    
    is_healthy = True
    try:
        if main_module.runtime_db_conn:
            # Execute a lightweight query to verify connection
            main_module.runtime_db_conn.execute("SELECT 1")
            db_status = "connected"
        else:
            db_status = "disconnected"
            is_healthy = False
    except Exception as e:
        db_status = "error"
        db_error = str(e)
        is_healthy = False
        logger.error(f"Health check failed: {e}")

    if not is_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "healthy" if is_healthy else "unhealthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0",
        "db_status": db_status,
        "db_error": db_error
    }


@app.post("/api/v1/sessions", response_model=CreateSessionResponse)
async def create_session(request: CreateSessionRequest):
    # 1. Enforce Allowlist
    final_user_id = resolve_user_id(request.user_id)
    if not is_user_allowed(final_user_id):
        logger.warning(f"⛔ Access Denied: User '{final_user_id}' not in allowlist.")
        raise HTTPException(status_code=403, detail="Access denied: User not allowed.")

    gateway = get_gateway()
    try:
        session = await gateway.create_session(
            user_id=final_user_id,
            workspace_dir=request.workspace_dir,
        )
        store_session(session)
        if _heartbeat_service:
            _heartbeat_service.register_session(session)
        else:
            logger.warning("Heartbeat service not available in create_session")
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
            if _heartbeat_service:
                _heartbeat_service.register_session(session)
        except ValueError:
            raise HTTPException(status_code=404, detail="Session not found")
            
    # Allowlist check for resume (optional, but good practice)
    if not is_user_allowed(session.user_id):
        raise HTTPException(status_code=403, detail="Access denied: User not allowed.")
        
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
    # Register connection with session_id
    await manager.connect(connection_id, websocket, session_id)

    gateway = get_gateway()
    session = get_session(session_id)

    if not session:
        try:
            session = await gateway.resume_session(session_id)
            store_session(session)
            if _heartbeat_service:
                _heartbeat_service.register_session(session)
        except ValueError:
            await websocket.close(code=4004, reason="Session not found")
            manager.disconnect(connection_id, session_id)
            return

    # 1. Enforce Allowlist for WebSocket
    if not is_user_allowed(session.user_id):
        logger.warning(f"⛔ Access Denied (WS): User '{session.user_id}' not in allowlist.")
        await websocket.close(code=4003, reason="Access denied")
        manager.disconnect(connection_id, session_id)
        return

    # Send initial connection success message
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
                logger.info("WS message received (session=%s): %s", session_id, msg_type)

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
                    logger.info(
                        "WS execute start (session=%s, user_id=%s, len=%s)",
                        session_id,
                        session.user_id,
                        len(user_input),
                    )

                    async def run_execution():
                        if _heartbeat_service:
                            _heartbeat_service.busy_sessions.add(session.session_id)
                        try:
                            # Execute the request and stream back to THIS connection
                            async for event in gateway.execute(session, request):
                                if event.type == EventType.ERROR:
                                    log_tail = None
                                    if session.workspace_dir:
                                        log_tail = _read_run_log_tail(session.workspace_dir)
                                    # Normalize error payload for clients
                                    if isinstance(event.data, dict):
                                        if "message" not in event.data and "error" in event.data:
                                            event.data["message"] = event.data.get("error")
                                        if log_tail and "log_tail" not in event.data:
                                            event.data["log_tail"] = log_tail
                                    logger.error(
                                        "Agent error event (session=%s): %s",
                                        session.session_id,
                                        event.data,
                                    )
                                await manager.send_json(connection_id, agent_event_to_wire(event))

                            await manager.send_json(
                                connection_id,
                                {
                                    "type": "query_complete",
                                    "data": {},
                                    "timestamp": datetime.now().isoformat(),
                                },
                            )

                            await manager.send_json(
                                connection_id,
                                {"type": "pong", "data": {}, "timestamp": datetime.now().isoformat()},
                            )
                            logger.info("WS execute complete (session=%s)", session_id)
                        except Exception as e:
                            logger.error("Execution error for session %s: %s", session_id, e, exc_info=True)
                            await manager.send_json(
                                connection_id,
                                {
                                    "type": "error",
                                    "data": {"message": str(e)},
                                    "timestamp": datetime.now().isoformat(),
                                },
                            )
                        finally:
                            if _heartbeat_service:
                                _heartbeat_service.busy_sessions.discard(session.session_id)
                    
                    asyncio.create_task(run_execution())
                
                elif msg_type == "input_response":
                    input_id = msg.get("data", {}).get("input_id", "default")
                    response = msg.get("data", {}).get("response", "")
                    
                    # 1. Try to resolve via gateway session (new path)
                    success = await gateway.resolve_input(session_id, input_id, response)
                    
                    # 2. Try to resolve via active adapter (in-process path)
                    if not success:
                         adapter = gateway._adapters.get(session_id)
                         if adapter and input_id in adapter._pending_inputs:
                             future = adapter._pending_inputs.pop(input_id)
                             if not future.done():
                                 future.set_result(response)
                                 success = True
                    
                    if not success:
                         logger.warning(f"Failed to resolve input {input_id} for session {session_id}")
                    else:
                         logger.info(f"Resolved input {input_id} for session {session_id}")
                
                elif msg_type == "broadcast_test":
                     # Test event to verify broadcast capability (Phase 1 verification)
                     payload = {
                         "type": "server_notice", 
                         "data": {"message": "Broadcast test received"},
                         "timestamp": datetime.now().isoformat()
                     }
                     # Broadcast to ALL connections for this session
                     await manager.broadcast(session_id, payload)

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
        manager.disconnect(connection_id, session_id)
        logger.info(f"Gateway WebSocket disconnected: {connection_id}")
    except Exception as e:
        manager.disconnect(connection_id, session_id)
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

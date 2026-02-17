"""
Universal Agent FastAPI Server - Modern Web API for the UI.

Provides REST API endpoints and WebSocket for real-time communication
with the Universal Agent system.

Server runs on port 8001 by default (configurable via PORT env var).
"""

import asyncio
import base64
import time
import json
import logging
import mimetypes
import os
import hmac
import hashlib
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx
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


_USE_BRAINSTORM_TONIGHT_PATTERNS = [
    re.compile(
        r"^/(?:use[-_ ]?brainstorm[-_ ]?tonight|brainstorm[-_ ]?tonight)\s*(?P<target>.+)?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:please\s+)?(?:use|promote)\s+(?:this\s+)?brainstorm(?:\s+idea)?\s+tonight\s*(?P<target>.+)?$",
        re.IGNORECASE,
    ),
]


# =============================================================================
# Auth Helpers
# =============================================================================


DASHBOARD_AUTH_COOKIE = "ua_dashboard_auth"
DEFAULT_OWNER = "owner_primary"
OWNER_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass
class DashboardAuthResult:
    authenticated: bool
    auth_required: bool
    owner_id: str
    expires_at: Optional[int] = None


def _normalize_owner_id(value: Optional[str]) -> str:
    candidate = (value or "").strip()
    if candidate and OWNER_PATTERN.match(candidate):
        return candidate
    fallback = (os.getenv("UA_DASHBOARD_OWNER_ID") or DEFAULT_OWNER).strip()
    if fallback and OWNER_PATTERN.match(fallback):
        return fallback
    return DEFAULT_OWNER


def _env_flag(name: str) -> Optional[bool]:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return None
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return None


def _owners_configured() -> bool:
    def _has_records(payload: Any) -> bool:
        rows: list[Any]
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict) and isinstance(payload.get("owners"), list):
            rows = payload.get("owners", [])
        else:
            return False
        for row in rows:
            if not isinstance(row, dict):
                continue
            owner_id = str(row.get("owner_id") or "").strip()
            password_hash = str(row.get("password_hash") or "").strip()
            if owner_id and password_hash:
                return True
        return False

    env_json = (os.getenv("UA_DASHBOARD_OWNERS_JSON") or "").strip()
    if env_json:
        try:
            if _has_records(json.loads(env_json)):
                return True
        except Exception:
            pass
    owners_file = (os.getenv("UA_DASHBOARD_OWNERS_FILE") or "").strip()
    if not owners_file:
        owners_file = str((BASE_DIR / "config" / "dashboard_owners.json").resolve())
    try:
        path = Path(owners_file)
        if not path.exists() or path.stat().st_size <= 0:
            return False
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _has_records(payload)
    except Exception:
        return False


def _dashboard_auth_required() -> bool:
    explicit = _env_flag("UA_DASHBOARD_AUTH_ENABLED")
    if explicit is not None:
        return explicit
    if _owners_configured():
        return True
    return bool((os.getenv("UA_DASHBOARD_PASSWORD") or "").strip())


def _parse_use_brainstorm_tonight_command(query: str) -> tuple[bool, str]:
    text = str(query or "").strip()
    if not text:
        return False, ""
    for pattern in _USE_BRAINSTORM_TONIGHT_PATTERNS:
        match = pattern.fullmatch(text)
        if not match:
            continue
        target = str(match.group("target") or "").strip().lstrip(":=- ").strip("`\"'")
        return True, target
    return False, ""


async def _try_handle_todoist_quick_command(connection_id: str, query: str) -> bool:
    is_match, target = _parse_use_brainstorm_tonight_command(query)
    if not is_match:
        return False

    if not target:
        await manager.send_event(
            connection_id,
            WebSocketEvent(
                type=WSEventType.TEXT,
                data={
                    "text": (
                        "Usage: `/use-brainstorm-tonight <task_id|dedupe_key>` "
                        "(example: `/use-brainstorm-tonight dedupe:retry-policy`)"
                    ),
                    "final": True,
                },
            ),
        )
        await manager.send_event(
            connection_id,
            WebSocketEvent(type=WSEventType.QUERY_COMPLETE, data={"quick_command": "use_brainstorm_tonight"}),
        )
        return True

    try:
        from universal_agent.services.todoist_service import TodoService

        result = TodoService().promote_idea_to_heartbeat_candidate(target)
    except Exception as exc:
        await manager.send_event(
            connection_id,
            WebSocketEvent(
                type=WSEventType.ERROR,
                data={"message": f"Todoist quick command failed: {exc}"},
            ),
        )
        await manager.send_event(
            connection_id,
            WebSocketEvent(type=WSEventType.QUERY_COMPLETE, data={"quick_command": "use_brainstorm_tonight"}),
        )
        return True

    if not bool(result.get("success")):
        error = str(result.get("error") or "promotion failed")
        await manager.send_event(
            connection_id,
            WebSocketEvent(
                type=WSEventType.TEXT,
                data={"text": f"Could not promote brainstorm item: {error}", "final": True},
            ),
        )
        await manager.send_event(
            connection_id,
            WebSocketEvent(type=WSEventType.QUERY_COMPLETE, data={"quick_command": "use_brainstorm_tonight"}),
        )
        return True

    previous = str(result.get("previous_section") or "")
    task_id = str(result.get("task_id") or "")
    content = str(result.get("content") or "")
    msg = (
        f"Promoted brainstorm to Heartbeat Candidate: {task_id}"
        + (f" ({content})" if content else "")
        + (f" [from {previous}]" if previous else "")
    )
    await manager.send_event(
        connection_id,
        WebSocketEvent(type=WSEventType.TEXT, data={"text": msg, "final": True}),
    )
    await manager.send_event(
        connection_id,
        WebSocketEvent(type=WSEventType.QUERY_COMPLETE, data={"quick_command": "use_brainstorm_tonight"}),
    )
    return True


def _dashboard_session_secret() -> str:
    secret = (
        (os.getenv("UA_DASHBOARD_SESSION_SECRET") or "").strip()
        or (os.getenv("UA_OPS_TOKEN") or "").strip()
        or (os.getenv("UA_DASHBOARD_PASSWORD") or "").strip()
    )
    return secret or "ua-dashboard-dev-secret"


def _extract_auth_token(headers: Any) -> str:
    header = str(headers.get("authorization", "")).strip()
    if header.lower().startswith("bearer "):
        return header.split(" ", 1)[1].strip()
    for key in ("x-ua-internal-token", "x-ua-ops-token"):
        value = str(headers.get(key, "")).strip()
        if value:
            return value
    return ""


def _internal_service_token() -> str:
    return (
        (os.getenv("UA_INTERNAL_API_TOKEN") or "").strip()
        or (os.getenv("UA_OPS_TOKEN") or "").strip()
    )


def _base64url_decode(value: str) -> bytes:
    padded = value + "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _decode_dashboard_session_token(token: str) -> DashboardAuthResult:
    auth_required = _dashboard_auth_required()
    default_owner = _normalize_owner_id(None)
    if not auth_required:
        return DashboardAuthResult(True, auth_required, default_owner, None)

    raw = (token or "").strip()
    if "." not in raw:
        return DashboardAuthResult(False, auth_required, default_owner, None)
    payload_b64, sig = raw.split(".", 1)
    if not payload_b64 or not sig:
        return DashboardAuthResult(False, auth_required, default_owner, None)

    expected_sig = base64.urlsafe_b64encode(
        hmac.new(_dashboard_session_secret().encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii").rstrip("=")
    if not hmac.compare_digest(sig, expected_sig):
        return DashboardAuthResult(False, auth_required, default_owner, None)

    try:
        payload_raw = _base64url_decode(payload_b64).decode("utf-8")
        payload = json.loads(payload_raw)
        exp = int(payload.get("exp") or 0)
    except Exception:
        return DashboardAuthResult(False, auth_required, default_owner, None)

    now = int(time.time())
    if exp <= now:
        return DashboardAuthResult(False, auth_required, default_owner, None)

    owner_id = _normalize_owner_id(str(payload.get("owner_id") or ""))
    return DashboardAuthResult(True, auth_required, owner_id, exp)


def _authenticate_dashboard_request(request: Request) -> DashboardAuthResult:
    internal_token = _internal_service_token()
    header_token = _extract_auth_token(request.headers)
    if internal_token and header_token and hmac.compare_digest(header_token, internal_token):
        return DashboardAuthResult(True, _dashboard_auth_required(), _normalize_owner_id(None), None)

    cookie_token = request.cookies.get(DASHBOARD_AUTH_COOKIE, "")
    return _decode_dashboard_session_token(cookie_token)


def _authenticate_dashboard_ws(websocket: WebSocket) -> DashboardAuthResult:
    internal_token = _internal_service_token()
    header_token = _extract_auth_token(websocket.headers)
    if internal_token and header_token and hmac.compare_digest(header_token, internal_token):
        return DashboardAuthResult(True, _dashboard_auth_required(), _normalize_owner_id(None), None)

    cookie_token = websocket.cookies.get(DASHBOARD_AUTH_COOKIE, "")
    return _decode_dashboard_session_token(cookie_token)


def _request_auth_owner(request: Request) -> str:
    auth = getattr(request.state, "dashboard_auth", None)
    if isinstance(auth, DashboardAuthResult) and auth.authenticated:
        return auth.owner_id
    return _normalize_owner_id(None)


def _gateway_url() -> str:
    return (os.getenv("UA_GATEWAY_URL") or "").strip().rstrip("/")


def _gateway_headers() -> dict[str, str]:
    token = _internal_service_token()
    if not token:
        return {}
    return {
        "authorization": f"Bearer {token}",
        "x-ua-internal-token": token,
        "x-ua-ops-token": token,
    }


async def _fetch_gateway_session_owner(session_id: str) -> Optional[str]:
    gateway_url = _gateway_url()
    if not gateway_url:
        return None
    url = f"{gateway_url}/api/v1/sessions/{session_id}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, headers=_gateway_headers())
    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to verify session ownership ({response.status_code})",
        )
    payload = response.json()
    owner = str(payload.get("user_id") or "").strip()
    return owner or None


async def _enforce_session_owner(session_id: str, owner_id: str, auth_required: bool) -> None:
    if not _gateway_url():
        return
    session_owner = await _fetch_gateway_session_owner(session_id)
    if session_owner and hmac.compare_digest(session_owner, owner_id):
        return
    if session_owner and not hmac.compare_digest(session_owner, owner_id):
        raise HTTPException(status_code=403, detail="Access denied: session owner mismatch.")
    if auth_required:
        raise HTTPException(status_code=403, detail="Access denied: unable to verify session owner.")


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
    user_id: Optional[str] = None


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


@app.middleware("http")
async def require_dashboard_auth(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    if path in {"/api/health"}:
        return await call_next(request)

    auth = _authenticate_dashboard_request(request)
    request.state.dashboard_auth = auth
    if auth.auth_required and not auth.authenticated:
        return JSONResponse(
            {
                "detail": "Dashboard login required.",
                "authenticated": False,
                "auth_required": True,
                "owner_id": auth.owner_id,
            },
            status_code=401,
        )
    return await call_next(request)


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
async def create_session(request: SessionCreateRequest, http_request: Request):
    """Create a new agent session."""
    auth = getattr(http_request.state, "dashboard_auth", _authenticate_dashboard_request(http_request))
    owner_id = auth.owner_id if isinstance(auth, DashboardAuthResult) else _normalize_owner_id(None)
    requested_user = (request.user_id or "").strip()
    if requested_user and not hmac.compare_digest(requested_user, owner_id):
        raise HTTPException(status_code=403, detail="Access denied: cannot create session for another owner.")

    bridge = get_agent_bridge()
    try:
        session_info = await bridge.create_session(
            user_id=owner_id,
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
async def list_sessions(request: Request):
    """List all agent sessions."""
    auth = getattr(request.state, "dashboard_auth", _authenticate_dashboard_request(request))
    owner_id = auth.owner_id if isinstance(auth, DashboardAuthResult) else _normalize_owner_id(None)
    bridge = get_agent_bridge()
    sessions = bridge.list_sessions()
    if isinstance(auth, DashboardAuthResult) and not auth.auth_required:
        return {"sessions": sessions}
    filtered = []
    for session in sessions:
        if not isinstance(session, dict):
            continue
        session_owner = (
            str(session.get("user_id") or "").strip()
            or str((session.get("metadata") or {}).get("user_id") if isinstance(session.get("metadata"), dict) else "").strip()
        )
        if session_owner and hmac.compare_digest(session_owner, owner_id):
            filtered.append(session)
    return {"sessions": filtered}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    """Get session details."""
    auth = getattr(request.state, "dashboard_auth", _authenticate_dashboard_request(request))
    owner_id = auth.owner_id if isinstance(auth, DashboardAuthResult) else _normalize_owner_id(None)
    auth_required = auth.auth_required if isinstance(auth, DashboardAuthResult) else _dashboard_auth_required()
    await _enforce_session_owner(session_id, owner_id, auth_required)

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
async def list_files(request: Request, session_id: Optional[str] = None, path: str = ""):
    """List files in a session workspace."""
    auth = getattr(request.state, "dashboard_auth", _authenticate_dashboard_request(request))
    owner_id = auth.owner_id if isinstance(auth, DashboardAuthResult) else _normalize_owner_id(None)
    auth_required = auth.auth_required if isinstance(auth, DashboardAuthResult) else _dashboard_auth_required()

    # Determine which workspace to use
    if session_id:
        await _enforce_session_owner(session_id, owner_id, auth_required)
        workspace = WORKSPACES_DIR / session_id
    else:
        raise HTTPException(status_code=400, detail="session_id is required")

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
async def get_file(session_id: str, file_path: str, request: Request):
    """Get file content from session workspace."""
    auth = getattr(request.state, "dashboard_auth", _authenticate_dashboard_request(request))
    owner_id = auth.owner_id if isinstance(auth, DashboardAuthResult) else _normalize_owner_id(None)
    auth_required = auth.auth_required if isinstance(auth, DashboardAuthResult) else _dashboard_auth_required()
    await _enforce_session_owner(session_id, owner_id, auth_required)

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
    auth = _authenticate_dashboard_ws(websocket)
    if auth.auth_required and not auth.authenticated:
        await websocket.close(code=4401, reason="Dashboard login required")
        return

    owner_id = auth.owner_id
    auth_required = auth.auth_required
    if session_id:
        try:
            await _enforce_session_owner(session_id, owner_id, auth_required)
        except HTTPException as exc:
            await websocket.close(code=4403, reason=str(exc.detail))
            return
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
        "cancelled",
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
             session_info = await bridge.create_session(user_id=owner_id)

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
                        async with websockets.connect(
                            ws_endpoint, **converter.websocket_connect_kwargs()
                        ) as ws:
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
                        if await _try_handle_todoist_quick_command(connection_id, query):
                            last_query_text = query
                            last_query_ts = now_ts
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

                elif client_event.type == WSEventType.CANCEL:
                    reason = str(client_event.data.get("reason") or "User requested stop")
                    handled = False
                    from universal_agent.api.gateway_bridge import GatewayBridge
                    from universal_agent.api.process_turn_bridge import ProcessTurnBridge
                    if isinstance(bridge, GatewayBridge):
                        handled = await bridge.send_cancel(reason)
                    elif isinstance(bridge, ProcessTurnBridge):
                        handled = await bridge.send_cancel(reason)

                    if not handled:
                        await manager.send_event(
                            connection_id,
                            create_error_event("Cancel requested but no active cancellable run was found."),
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

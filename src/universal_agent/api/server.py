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
import shutil
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
VPS_WORKSPACES_MIRROR_DIR = Path(
    os.getenv(
        "UA_VPS_WORKSPACES_MIRROR_DIR",
        str(WORKSPACES_DIR / "remote_vps_workspaces"),
    )
).expanduser()
VPS_ARTIFACTS_MIRROR_DIR = Path(
    os.getenv(
        "UA_VPS_ARTIFACTS_MIRROR_DIR",
        str(ARTIFACTS_DIR / "remote_vps_artifacts"),
    )
).expanduser()
VPS_PULL_SCRIPT = BASE_DIR / "scripts" / "pull_remote_workspaces_now.sh"
VPS_SYNC_SCRIPT = BASE_DIR / "scripts" / "sync_remote_workspaces.sh"

TEXT_EXTENSIONS = (
    ".txt",
    ".md",
    ".log",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".css",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".sh",
    ".html",
    ".xml",
    ".csv",
)
_STORAGE_ROOT_SOURCES = {"local", "mirror"}
_STORAGE_SCOPES = {"workspaces", "artifacts", "vps"}
_SESSION_PREFIXES = ("session_", "session-hook_", "session_hook_", "tg_", "api_", "vp_")
_PROTECTED_STORAGE_DELETE_SUFFIXES = (".db", ".db-shm", ".db-wal")
_SYSTEM_SESSION_OWNERS = {
    "webhook",
    "user_ui",
    "user_cli",
    "ops_tutorial_review",
    "cron_system",
    "ops:system-configuration-agent",
}

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
from universal_agent.runtime_env import ensure_runtime_path, runtime_tool_status
from universal_agent.timeout_policy import (
    gateway_owner_lookup_timeout_seconds,
    gateway_ws_send_timeout_seconds,
    gateway_ws_handshake_timeout_seconds,
)
ensure_runtime_path()




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


def _is_system_session_owner(owner_id: str) -> bool:
    normalized_owner = str(owner_id or "").strip().lower()
    if not normalized_owner:
        return False
    if normalized_owner in _SYSTEM_SESSION_OWNERS:
        return True
    return (
        normalized_owner.startswith("cron:")
        or normalized_owner.startswith("worker_")
        or normalized_owner.startswith("vp.")
    )


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
    async with httpx.AsyncClient(timeout=gateway_owner_lookup_timeout_seconds()) as client:
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
        if _is_system_session_owner(session_owner):
            # System-owned sessions are observable from the dashboard owner lane.
            return
        # Hook sessions are automation/system workflows and may be resumed with a
        # runtime identity that differs from the dashboard owner lane. Allow the
        # primary dashboard owner to inspect these sessions.
        if session_id.startswith("session_hook_") and hmac.compare_digest(owner_id, _normalize_owner_id(None)):
            return
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


async def _close_bridge(bridge: Any) -> None:
    close_fn = getattr(bridge, "close", None)
    if close_fn is None:
        return
    try:
        maybe_coro = close_fn()
        if asyncio.iscoroutine(maybe_coro):
            await maybe_coro
    except Exception:
        logger.warning("Bridge close failed", exc_info=True)


class VpsSyncRequest(BaseModel):
    """Request payload for VPS mirror sync."""
    session_id: Optional[str] = None
    full_resync: bool = False
    include_in_progress: bool = False


class VpsStorageDeleteRequest(BaseModel):
    """Batch delete request for storage explorer entries."""

    scope: str = "workspaces"
    root_source: str = "local"
    paths: list[str] = []
    allow_protected: bool = False


def _normalize_storage_scope(raw_scope: str) -> str:
    scope_clean = (raw_scope or "").strip().lower()
    if scope_clean not in _STORAGE_SCOPES:
        raise HTTPException(status_code=400, detail="scope must be 'workspaces', 'artifacts', or 'vps'")
    return scope_clean


def _normalize_storage_root_source(raw_root_source: str) -> str:
    root_source_clean = (raw_root_source or "").strip().lower() or "local"
    if root_source_clean not in _STORAGE_ROOT_SOURCES:
        raise HTTPException(status_code=400, detail="root_source must be 'local' or 'mirror'")
    return root_source_clean


def _storage_root(scope: str, root_source: str) -> Path:
    scope_clean = _normalize_storage_scope(scope)
    source_clean = _normalize_storage_root_source(root_source)
    if scope_clean == "vps":
        return BASE_DIR
    if scope_clean == "workspaces":
        return WORKSPACES_DIR if source_clean == "local" else VPS_WORKSPACES_MIRROR_DIR
    return ARTIFACTS_DIR if source_clean == "local" else VPS_ARTIFACTS_MIRROR_DIR


def _resolve_path_under_root(root: Path, path: str = "") -> Path:
    target = root / path if path else root
    try:
        target = target.resolve()
        root_resolved = root.resolve()
        if target != root_resolved and root_resolved not in target.parents:
            raise HTTPException(status_code=403, detail="Access denied")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid path: {exc}") from exc
    return target


def _list_directory(root: Path, path: str = "") -> dict[str, Any]:
    target_path = _resolve_path_under_root(root, path)
    if not target_path.exists():
        return {"files": [], "path": path, "root": str(root)}
    if target_path.is_file():
        return {"files": [], "path": path, "root": str(root), "is_file": True}

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
    return {"files": files, "path": path, "root": str(root)}


def _read_file_from_root(root: Path, file_path: str) -> Response:
    target_resolved = _resolve_path_under_root(root, file_path)
    if not target_resolved.exists() or not target_resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    content = target_resolved.read_bytes()
    filename = target_resolved.name
    mime, _ = mimetypes.guess_type(str(target_resolved))
    content_type = mime or "application/octet-stream"

    if filename.endswith(".html"):
        return Response(content=content, media_type="text/html")
    if filename.endswith(".json"):
        try:
            data = json.loads(content.decode("utf-8"))
            return Response(content=json.dumps(data, indent=2), media_type="application/json")
        except Exception:
            pass
    if filename.endswith(TEXT_EXTENSIONS):
        return Response(content=content, media_type="text/plain")
    return Response(content=content, media_type=content_type)


def _is_protected_storage_delete_target(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(suffix) for suffix in _PROTECTED_STORAGE_DELETE_SUFFIXES)


def _find_protected_file_under_directory(directory: Path) -> Optional[Path]:
    try:
        for candidate in directory.rglob("*"):
            if not candidate.is_file():
                continue
            if _is_protected_storage_delete_target(candidate):
                return candidate
    except Exception:
        return None
    return None


def _delete_paths_from_root(root: Path, paths: list[str], *, allow_protected: bool = False) -> dict[str, Any]:
    deleted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    seen: set[str] = set()
    for raw_path in paths:
        path_clean = str(raw_path or "").strip().strip("/")
        if not path_clean:
            continue
        if path_clean in seen:
            continue
        seen.add(path_clean)

        try:
            target = _resolve_path_under_root(root, path_clean)
        except HTTPException as exc:
            errors.append({"path": path_clean, "error": str(exc.detail)})
            continue

        if target == root:
            errors.append({"path": path_clean, "error": "Cannot delete storage root"})
            continue
        if not target.exists():
            skipped.append({"path": path_clean, "reason": "not_found"})
            continue

        if not allow_protected:
            protected_match: Optional[Path] = None
            if target.is_file() and _is_protected_storage_delete_target(target):
                protected_match = target
            elif target.is_dir():
                protected_match = _find_protected_file_under_directory(target)

            if protected_match is not None:
                try:
                    protected_relpath = str(protected_match.relative_to(root))
                except Exception:
                    protected_relpath = protected_match.name
                errors.append(
                    {
                        "path": path_clean,
                        "error": "Protected runtime DB content detected; retry with allow_protected=true.",
                        "code": "protected_requires_override",
                        "protected_path": protected_relpath,
                    }
                )
                continue

        try:
            if target.is_dir():
                shutil.rmtree(target)
                deleted.append({"path": path_clean, "kind": "directory"})
            else:
                target.unlink()
                deleted.append({"path": path_clean, "kind": "file"})
        except Exception as exc:
            errors.append({"path": path_clean, "error": str(exc)})

    return {
        "deleted": deleted,
        "skipped": skipped,
        "errors": errors,
        "deleted_count": len(deleted),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "protected_blocked_count": sum(
            1 for item in errors if str(item.get("code") or "") == "protected_requires_override"
        ),
        "allow_protected": bool(allow_protected),
    }


def _copytree_merge(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)


def _local_sync_without_ssh(session_id: Optional[str], workspace_mirror: Path, artifacts_mirror: Path) -> dict[str, Any]:
    """
    Fallback mirror strategy when SSH key-based sync is unavailable on a VPS host.

    This copies directly from configured remote roots (which are local paths on the VPS).
    """
    remote_ws_root = Path(os.getenv("UA_REMOTE_WORKSPACES_DIR", "/opt/universal_agent/AGENT_RUN_WORKSPACES")).expanduser()
    remote_artifacts_root = Path(os.getenv("UA_REMOTE_ARTIFACTS_DIR", "/opt/universal_agent/artifacts")).expanduser()

    workspace_mirror.mkdir(parents=True, exist_ok=True)
    artifacts_mirror.mkdir(parents=True, exist_ok=True)

    copied_workspaces = 0
    cleaned_session_id = (session_id or "").strip()
    if cleaned_session_id:
        src_dir = remote_ws_root / cleaned_session_id
        if src_dir.exists() and src_dir.is_dir():
            _copytree_merge(src_dir, workspace_mirror / cleaned_session_id)
            copied_workspaces = 1
    else:
        if remote_ws_root.exists():
            for entry in remote_ws_root.iterdir():
                if not entry.is_dir():
                    continue
                _copytree_merge(entry, workspace_mirror / entry.name)
                copied_workspaces += 1

    if remote_artifacts_root.exists():
        _copytree_merge(remote_artifacts_root, artifacts_mirror)

    return {
        "ok": True,
        "returncode": 0,
        "stdout": "",
        "stderr": "local_fallback_sync_used (no SSH key available for sync script)",
        "workspace_root": str(workspace_mirror),
        "artifacts_root": str(artifacts_mirror),
        "session_id": cleaned_session_id or None,
        "local_fallback": True,
        "copied_workspaces": copied_workspaces,
    }


async def _run_vps_pull_sync(
    session_id: Optional[str] = None,
    *,
    full_resync: bool = False,
    include_in_progress: bool = False,
) -> dict[str, Any]:
    if not VPS_PULL_SCRIPT.exists():
        raise HTTPException(status_code=500, detail=f"Sync script missing: {VPS_PULL_SCRIPT}")

    VPS_WORKSPACES_MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    VPS_ARTIFACTS_MIRROR_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [str(VPS_PULL_SCRIPT)]
    cleaned_session_id = (session_id or "").strip()
    if cleaned_session_id:
        cmd.append(cleaned_session_id)
    if full_resync:
        cmd.append("--no-skip-synced")
    if include_in_progress:
        cmd.append("--ignore-ready-marker")

    env = os.environ.copy()
    env["UA_LOCAL_MIRROR_DIR"] = str(VPS_WORKSPACES_MIRROR_DIR)
    env["UA_LOCAL_ARTIFACTS_MIRROR_DIR"] = str(VPS_ARTIFACTS_MIRROR_DIR)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(BASE_DIR),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise HTTPException(status_code=504, detail="VPS sync timed out after 180 seconds")

    stdout_text = (stdout or b"").decode("utf-8", errors="replace")
    stderr_text = (stderr or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0 and "SSH key does not exist" in stderr_text:
        logger.warning("VPS sync script failed due missing SSH key; falling back to local copy sync")
        return _local_sync_without_ssh(session_id, VPS_WORKSPACES_MIRROR_DIR, VPS_ARTIFACTS_MIRROR_DIR)

    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": stdout_text[-12000:],
        "stderr": stderr_text[-12000:],
        "workspace_root": str(VPS_WORKSPACES_MIRROR_DIR),
        "artifacts_root": str(VPS_ARTIFACTS_MIRROR_DIR),
        "session_id": cleaned_session_id or None,
    }


async def _run_vps_sync_status_probe() -> dict[str, Any]:
    if not VPS_SYNC_SCRIPT.exists():
        return {
            "ok": False,
            "sync_state": "error",
            "error": f"Sync script missing: {VPS_SYNC_SCRIPT}",
        }

    remote_host = os.getenv("UA_REMOTE_SSH_HOST", "root@100.106.113.93")
    remote_dir = os.getenv("UA_REMOTE_WORKSPACES_DIR", "/opt/universal_agent/AGENT_RUN_WORKSPACES")
    local_dir = str(VPS_WORKSPACES_MIRROR_DIR)
    remote_artifacts_dir = os.getenv("UA_REMOTE_ARTIFACTS_DIR", "/opt/universal_agent/artifacts")
    local_artifacts_dir = str(VPS_ARTIFACTS_MIRROR_DIR)
    manifest_file = os.getenv(
        "UA_REMOTE_SYNC_MANIFEST_FILE",
        str(BASE_DIR / "AGENT_RUN_WORKSPACES" / "remote_vps_sync_state" / "synced_workspaces.txt"),
    )
    ssh_port = os.getenv("UA_REMOTE_SSH_PORT", "22")
    ssh_key = os.getenv("UA_REMOTE_SSH_KEY", str(Path.home() / ".ssh" / "id_ed25519"))
    include_artifacts = (os.getenv("UA_REMOTE_SYNC_INCLUDE_ARTIFACTS", "true").strip().lower() in {"1", "true", "yes", "on"})
    require_ready_marker = (
        os.getenv("UA_REMOTE_SYNC_REQUIRE_READY_MARKER", "true").strip().lower() in {"1", "true", "yes", "on"}
    )
    ready_marker_name = os.getenv("UA_REMOTE_SYNC_READY_MARKER_FILENAME", "sync_ready.json")
    ready_min_age = os.getenv("UA_REMOTE_SYNC_READY_MIN_AGE_SECONDS", "45")
    ready_session_prefix = os.getenv("UA_REMOTE_SYNC_READY_SESSION_PREFIX", "session_,tg_")

    cmd = [
        str(VPS_SYNC_SCRIPT),
        "--status-json",
        "--once",
        "--host",
        remote_host,
        "--remote-dir",
        remote_dir,
        "--local-dir",
        local_dir,
        "--remote-artifacts-dir",
        remote_artifacts_dir,
        "--local-artifacts-dir",
        local_artifacts_dir,
        "--manifest-file",
        manifest_file,
        "--ssh-port",
        ssh_port,
        "--ready-marker-name",
        ready_marker_name,
        "--ready-min-age-seconds",
        ready_min_age,
    ]
    if not include_artifacts:
        cmd.append("--no-artifacts")
    if require_ready_marker:
        cmd.append("--require-ready-marker")
    else:
        cmd.append("--ignore-ready-marker")
    if ready_session_prefix.strip():
        cmd.extend(["--ready-session-prefix", ready_session_prefix.strip()])
    if ssh_key and Path(ssh_key).expanduser().exists():
        cmd.extend(["--ssh-key", ssh_key])

    env = os.environ.copy()
    env["UA_LOCAL_MIRROR_DIR"] = str(VPS_WORKSPACES_MIRROR_DIR)
    env["UA_LOCAL_ARTIFACTS_MIRROR_DIR"] = str(VPS_ARTIFACTS_MIRROR_DIR)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(BASE_DIR),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {
            "ok": False,
            "sync_state": "unknown",
            "error": "VPS sync status probe timed out after 90 seconds",
        }

    stdout_text = (stdout or b"").decode("utf-8", errors="replace")
    stderr_text = (stderr or b"").decode("utf-8", errors="replace")
    lines = [line.strip() for line in stdout_text.splitlines() if line.strip()]
    payload_raw = lines[-1] if lines else "{}"
    try:
        payload = json.loads(payload_raw)
        if not isinstance(payload, dict):
            raise ValueError("status probe did not return a JSON object")
    except Exception as exc:
        return {
            "ok": False,
            "sync_state": "unknown",
            "error": f"Failed to parse status probe output: {exc}",
            "stdout": stdout_text[-12000:],
            "stderr": stderr_text[-12000:],
            "returncode": proc.returncode,
        }

    payload["ok"] = bool(payload.get("ok")) and proc.returncode == 0
    payload["returncode"] = proc.returncode
    payload["stdout"] = stdout_text[-12000:]
    payload["stderr"] = stderr_text[-12000:]
    return payload


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _workspace_source_type(workspace_id: str) -> str:
    sid = workspace_id.lower()
    if sid.startswith("session_hook_"):
        return "hook"
    if sid.startswith("tg_"):
        return "telegram"
    if sid.startswith("vp_"):
        return "vp"
    if sid.startswith("session_") or sid.startswith("api_"):
        return "web"
    return "other"


def _load_json_file(path: Path) -> Optional[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _workspace_size_bytes(root: Path) -> int:
    total = 0
    try:
        for node in root.rglob("*"):
            if node.is_file():
                total += node.stat().st_size
    except Exception:
        return total
    return total


def _read_workspace_sync_marker(workspace_dir: Path) -> dict[str, Any]:
    marker_path = workspace_dir / "sync_ready.json"
    if not marker_path.exists() or not marker_path.is_file():
        return {
            "marker_exists": False,
            "marker_path": str(marker_path),
            "ready": False,
            "state": "unknown",
            "completed_at_epoch": None,
            "updated_at_epoch": None,
        }

    marker_payload = _load_json_file(marker_path) or {}
    return {
        "marker_exists": True,
        "marker_path": str(marker_path),
        "ready": bool(marker_payload.get("ready") is True),
        "state": str(marker_payload.get("state") or "unknown"),
        "completed_at_epoch": _as_float(marker_payload.get("completed_at_epoch")),
        "updated_at_epoch": _as_float(marker_payload.get("updated_at_epoch")),
    }


def _looks_like_session_workspace(workspace_dir: Path) -> bool:
    workspace_id = workspace_dir.name.lower()
    if workspace_id.startswith(_SESSION_PREFIXES):
        return True
    return any(
        (workspace_dir / marker_name).exists()
        for marker_name in ("run.log", "session_checkpoint.json", "trace.json", "sync_ready.json")
    )


def _storage_session_items(
    source: str = "all",
    limit: int = 100,
    include_size: bool = True,
    *,
    root: Optional[Path] = None,
) -> list[dict[str, Any]]:
    root_path = root or VPS_WORKSPACES_MIRROR_DIR
    root_resolved = root_path.resolve()
    root_name = root_resolved.name.lower()
    if root_name in {"remote_vps_workspaces", "remote_vps_sync_state"}:
        default_mode = "mirror"
    else:
        default_mode = "local"
    root_source = "mirror" if "remote_vps_" in root_name else default_mode

    root = root_resolved
    if not root.exists() or not root.is_dir():
        return []

    source_filter = (source or "all").strip().lower()
    items: list[dict[str, Any]] = []
    for workspace_dir in root.iterdir():
        if not workspace_dir.is_dir():
            continue
        if not _looks_like_session_workspace(workspace_dir):
            continue
        workspace_id = workspace_dir.name
        source_type = _workspace_source_type(workspace_id)
        if source_filter != "all" and source_type != source_filter:
            continue

        marker = _read_workspace_sync_marker(workspace_dir)
        completed_epoch = marker.get("completed_at_epoch") or marker.get("updated_at_epoch")
        modified_epoch = None
        try:
            modified_epoch = workspace_dir.stat().st_mtime
        except Exception:
            modified_epoch = None
        run_log_path = workspace_dir / "run.log"
        items.append(
            {
                "session_id": workspace_id,
                "source_type": source_type,
                "status": marker.get("state") or "unknown",
                "ready": bool(marker.get("ready")),
                "completed_at_epoch": completed_epoch,
                "updated_at_epoch": marker.get("updated_at_epoch"),
                "modified_epoch": modified_epoch,
                "size_bytes": None,
                "root_path": workspace_id,
                "run_log_path": f"{workspace_id}/run.log" if run_log_path.exists() else None,
                "marker_path": marker.get("marker_path"),
                "marker_exists": marker.get("marker_exists"),
                "root_source": root_source,
                "_workspace_dir": workspace_dir,
            }
        )

    items.sort(
        key=lambda row: (
            _as_float(row.get("completed_at_epoch"))
            or _as_float(row.get("updated_at_epoch"))
            or _as_float(row.get("modified_epoch"))
            or 0.0
        ),
        reverse=True,
    )
    selected_items = items[: max(1, limit)]
    if include_size:
        for row in selected_items:
            workspace_dir = row.get("_workspace_dir")
            if isinstance(workspace_dir, Path):
                row["size_bytes"] = _workspace_size_bytes(workspace_dir)
    for row in selected_items:
        row.pop("_workspace_dir", None)
    return selected_items


def _extract_artifact_field(payload: Optional[dict[str, Any]], keys: tuple[str, ...]) -> Optional[str]:
    if not payload:
        return None
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _storage_artifact_items(limit: int = 100, *, root: Optional[Path] = None) -> list[dict[str, Any]]:
    root = root or VPS_ARTIFACTS_MIRROR_DIR
    if not root.exists() or not root.is_dir():
        return []

    run_dirs: dict[Path, Optional[Path]] = {}
    for manifest in root.rglob("manifest.json"):
        if manifest.is_file():
            run_dirs[manifest.parent] = manifest

    if not run_dirs:
        for entry in root.iterdir():
            if entry.is_dir():
                run_dirs[entry] = (entry / "manifest.json") if (entry / "manifest.json").exists() else None

    items: list[dict[str, Any]] = []
    for run_dir, manifest_path in run_dirs.items():
        payload = _load_json_file(manifest_path) if manifest_path else None
        rel_path = str(run_dir.relative_to(root))
        readme_path = run_dir / "README.md"
        implementation_path = run_dir / "IMPLEMENTATION.md"

        modified_epoch = None
        try:
            modified_epoch = run_dir.stat().st_mtime
        except Exception:
            modified_epoch = None
        if manifest_path and manifest_path.exists():
            try:
                modified_epoch = max(modified_epoch or 0, manifest_path.stat().st_mtime)
            except Exception:
                pass

        item = {
            "path": rel_path,
            "slug": _extract_artifact_field(payload, ("slug", "run_slug")) or run_dir.name,
            "title": _extract_artifact_field(payload, ("title", "name", "run_title")) or run_dir.name,
            "status": _extract_artifact_field(payload, ("status", "run_status")) or "unknown",
            "video_id": _extract_artifact_field(payload, ("video_id", "youtube_video_id")),
            "video_url": _extract_artifact_field(payload, ("video_url", "youtube_url", "url")),
            "updated_at_epoch": _as_float(payload.get("updated_at_epoch")) if payload else modified_epoch,
            "manifest_path": f"{rel_path}/manifest.json" if manifest_path and manifest_path.exists() else None,
            "readme_path": f"{rel_path}/README.md" if readme_path.exists() else None,
            "implementation_path": (
                f"{rel_path}/IMPLEMENTATION.md" if implementation_path.exists() else None
            ),
        }
        items.append(item)

    items.sort(key=lambda row: _as_float(row.get("updated_at_epoch")) or 0.0, reverse=True)
    return items[: max(1, limit)]


# =============================================================================
# Connection Manager for WebSocket
# =============================================================================


class ConnectionManager:
    """Manages WebSocket connections."""

    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}
        self._send_timeout_seconds = gateway_ws_send_timeout_seconds()

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
            try:
                await asyncio.wait_for(
                    self.active_connections[connection_id].send_text(event.to_json()),
                    timeout=self._send_timeout_seconds,
                )
            except Exception:
                self.disconnect(connection_id)
                raise

    async def broadcast(self, event: WebSocketEvent):
        stale_ids: list[str] = []
        for connection_id, connection in list(self.active_connections.items()):
            try:
                await asyncio.wait_for(
                    connection.send_text(event.to_json()),
                    timeout=self._send_timeout_seconds,
                )
            except Exception:
                stale_ids.append(connection_id)
        for connection_id in stale_ids:
            self.disconnect(connection_id)


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
            "session_stream": "/api/v1/sessions/{session_id}/stream",
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
        "runtime_path": os.getenv("PATH", ""),
        "runtime_tools": runtime_tool_status(),
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
    finally:
        await _close_bridge(bridge)


@app.get("/api/sessions")
async def list_sessions(request: Request):
    """List all agent sessions."""
    auth = getattr(request.state, "dashboard_auth", _authenticate_dashboard_request(request))
    owner_id = auth.owner_id if isinstance(auth, DashboardAuthResult) else _normalize_owner_id(None)
    bridge = get_agent_bridge()
    try:
        sessions = bridge.list_sessions()
    finally:
        await _close_bridge(bridge)
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
    try:
        result = bridge.get_session_file(session_id, file_path)
    finally:
        await _close_bridge(bridge)

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
    payload = _list_directory(ARTIFACTS_DIR, path)
    payload["artifacts_root"] = str(ARTIFACTS_DIR)
    return payload


@app.get("/api/artifacts/files/{file_path:path}")
async def get_artifact_file(file_path: str):
    """Get file content from the persistent artifacts root."""
    return _read_file_from_root(ARTIFACTS_DIR, file_path)


@app.get("/api/vps/sync/status")
async def vps_sync_status():
    """Get local mirror status for VPS workspaces/artifacts."""
    workspaces_count = 0
    artifacts_count = 0
    if VPS_WORKSPACES_MIRROR_DIR.exists():
        workspaces_count = len([item for item in VPS_WORKSPACES_MIRROR_DIR.iterdir() if item.is_dir()])
    if VPS_ARTIFACTS_MIRROR_DIR.exists():
        artifacts_count = len(list(VPS_ARTIFACTS_MIRROR_DIR.iterdir()))
    probe = await _run_vps_sync_status_probe()
    pending_ready_count = int(probe.get("pending_ready_count") or 0) if probe.get("ok") else 0
    sync_state = str(probe.get("sync_state") or "unknown")
    if probe.get("ok") and sync_state not in {"in_sync", "behind"}:
        sync_state = "behind" if pending_ready_count > 0 else "in_sync"
    if not probe.get("ok"):
        sync_state = "unknown"
    return {
        "canonical_workspace_root": str(WORKSPACES_DIR),
        "canonical_artifacts_root": str(ARTIFACTS_DIR),
        "remote_workspace_root": str(
            Path(os.getenv("UA_REMOTE_WORKSPACES_DIR", "/opt/universal_agent/AGENT_RUN_WORKSPACES")).expanduser()
        ),
        "remote_artifacts_root": str(
            Path(os.getenv("UA_REMOTE_ARTIFACTS_DIR", "/opt/universal_agent/artifacts")).expanduser()
        ),
        "workspace_root": str(VPS_WORKSPACES_MIRROR_DIR),
        "artifacts_root": str(VPS_ARTIFACTS_MIRROR_DIR),
        "workspace_count": workspaces_count,
        "artifacts_items_count": artifacts_count,
        "sync_state": sync_state,
        "probe_ok": bool(probe.get("ok")),
        "probe_error": probe.get("error"),
        "pending_ready_count": pending_ready_count,
        "latest_ready_remote_epoch": _as_float(probe.get("latest_ready_remote_epoch")),
        "latest_ready_local_epoch": _as_float(probe.get("latest_ready_local_epoch")),
        "lag_seconds": _as_float(probe.get("lag_seconds")),
        "ready_total_count": int(probe.get("ready_total_count") or 0) if probe.get("ok") else 0,
        "sync_script": str(VPS_PULL_SCRIPT),
        "sync_script_exists": VPS_PULL_SCRIPT.exists(),
        "sync_status_script": str(VPS_SYNC_SCRIPT),
        "sync_status_script_exists": VPS_SYNC_SCRIPT.exists(),
    }


@app.post("/api/vps/sync/now")
async def vps_sync_now(payload: VpsSyncRequest):
    """
    Pull latest VPS workspaces/artifacts into local mirror directories.

    This uses the existing SSH/Tailscale sync script.
    """
    result = await _run_vps_pull_sync(
        payload.session_id,
        full_resync=bool(payload.full_resync),
        include_in_progress=bool(payload.include_in_progress),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    return result


@app.get("/api/vps/storage/sessions")
async def vps_storage_sessions(source: str = "all", limit: int = 100, root_source: str = "local"):
    source_clean = (source or "all").strip().lower()
    if source_clean not in {"all", "web", "hook", "telegram", "vp"}:
        raise HTTPException(status_code=400, detail="source must be one of: all, web, hook, telegram, vp")
    root_source_clean = _normalize_storage_root_source(root_source)
    workspace_root = _storage_root("workspaces", root_source_clean)
    limit_clamped = max(1, min(limit, 500))
    sessions = _storage_session_items(source_clean, limit_clamped, include_size=True, root=workspace_root)
    return {
        "workspace_root": str(workspace_root),
        "root_source": root_source_clean,
        "source": source_clean,
        "limit": limit_clamped,
        "sessions": sessions,
    }


@app.get("/api/vps/storage/artifacts")
async def vps_storage_artifacts(limit: int = 100, root_source: str = "local"):
    root_source_clean = _normalize_storage_root_source(root_source)
    artifacts_root = _storage_root("artifacts", root_source_clean)
    limit_clamped = max(1, min(limit, 500))
    artifacts = _storage_artifact_items(limit_clamped, root=artifacts_root)
    return {
        "artifacts_root": str(artifacts_root),
        "root_source": root_source_clean,
        "limit": limit_clamped,
        "artifacts": artifacts,
    }


@app.get("/api/vps/storage/overview")
async def vps_storage_overview(root_source: str = "local"):
    root_source_clean = _normalize_storage_root_source(root_source)
    workspace_root = _storage_root("workspaces", root_source_clean)
    artifacts_root = _storage_root("artifacts", root_source_clean)
    probe = await _run_vps_sync_status_probe()
    pending_ready_count = int(probe.get("pending_ready_count") or 0) if probe.get("ok") else 0
    sync_state = str(probe.get("sync_state") or "unknown")
    if probe.get("ok") and sync_state not in {"in_sync", "behind"}:
        sync_state = "behind" if pending_ready_count > 0 else "in_sync"
    if not probe.get("ok"):
        sync_state = "unknown"

    sessions = _storage_session_items("all", 500, include_size=False, root=workspace_root)
    latest_sessions: dict[str, Optional[dict[str, Any]]] = {"web": None, "hook": None, "telegram": None, "vp": None}
    for session in sessions:
        source_type = str(session.get("source_type") or "")
        if source_type in latest_sessions and latest_sessions[source_type] is None:
            latest_sessions[source_type] = session
        if all(latest_sessions.values()):
            break

    artifacts = _storage_artifact_items(1, root=artifacts_root)
    latest_artifact = artifacts[0] if artifacts else None

    return {
        "sync_state": sync_state,
        "pending_ready_count": pending_ready_count,
        "latest_ready_remote_epoch": _as_float(probe.get("latest_ready_remote_epoch")),
        "latest_ready_local_epoch": _as_float(probe.get("latest_ready_local_epoch")),
        "lag_seconds": _as_float(probe.get("lag_seconds")),
        "latest_sessions": latest_sessions,
        "latest_artifact": latest_artifact,
        "workspace_root": str(workspace_root),
        "artifacts_root": str(artifacts_root),
        "root_source": root_source_clean,
        "probe_ok": bool(probe.get("ok")),
        "probe_error": probe.get("error"),
    }


@app.get("/api/vps/files")
async def list_vps_files(scope: str = "workspaces", path: str = "", root_source: str = "local"):
    """
    List mirrored VPS files.

    scope=workspaces -> configured UA_VPS_WORKSPACES_MIRROR_DIR
    scope=artifacts  -> configured UA_VPS_ARTIFACTS_MIRROR_DIR
    """
    scope_clean = _normalize_storage_scope(scope)
    root_source_clean = _normalize_storage_root_source(root_source)
    root = _storage_root(scope_clean, root_source_clean)
    payload = _list_directory(root, path)
    payload["scope"] = scope_clean
    payload["root_source"] = root_source_clean
    return payload


@app.get("/api/vps/file")
async def read_vps_file(scope: str = "workspaces", path: str = "", root_source: str = "local"):
    """Read a file from mirrored VPS files."""
    scope_clean = _normalize_storage_scope(scope)
    root_source_clean = _normalize_storage_root_source(root_source)
    path_clean = (path or "").strip()
    if not path_clean:
        raise HTTPException(status_code=400, detail="path is required")
    root = _storage_root(scope_clean, root_source_clean)
    return _read_file_from_root(root, path_clean)


@app.post("/api/vps/files/delete")
async def delete_vps_files(payload: VpsStorageDeleteRequest):
    scope_clean = _normalize_storage_scope(payload.scope)
    root_source_clean = _normalize_storage_root_source(payload.root_source)
    if not isinstance(payload.paths, list) or not payload.paths:
        raise HTTPException(status_code=400, detail="paths must include at least one entry")

    root = _storage_root(scope_clean, root_source_clean)
    result = _delete_paths_from_root(root, payload.paths, allow_protected=bool(payload.allow_protected))
    result["scope"] = scope_clean
    result["root_source"] = root_source_clean
    result["root"] = str(root)
    return result


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


@app.websocket("/api/v1/sessions/{session_id}/stream")
async def websocket_session_stream_proxy(websocket: WebSocket, session_id: str):
    """
    Pass-through endpoint for public routing compatibility.

    Proxies raw gateway stream protocol so deployments can route
    `/api/v1/sessions/{id}/stream` to either API or Gateway service.
    """
    session_id = str(session_id or "").strip()
    if not session_id:
        await websocket.close(code=4400, reason="Invalid session id")
        return

    auth = _authenticate_dashboard_ws(websocket)
    internal_token = _internal_service_token()
    header_token = _extract_auth_token(websocket.headers)
    internal_auth = bool(
        internal_token and header_token and hmac.compare_digest(header_token, internal_token)
    )
    if not internal_auth and auth.auth_required and not auth.authenticated:
        await websocket.close(code=4401, reason="Dashboard login required")
        return

    if not internal_auth:
        try:
            await _enforce_session_owner(session_id, auth.owner_id, auth.auth_required)
        except HTTPException as exc:
            await websocket.close(code=4403, reason=str(exc.detail))
            return

    gateway_url = _gateway_url()
    if not gateway_url:
        await websocket.close(code=1011, reason="Gateway URL not configured")
        return

    from universal_agent.api.gateway_bridge import GatewayBridge

    bridge = GatewayBridge(gateway_url)
    ws_endpoint = f"{bridge.ws_url}/api/v1/sessions/{session_id}/stream"

    await websocket.accept()
    try:
        async with websockets.connect(ws_endpoint, **bridge.websocket_connect_kwargs()) as upstream:
            async def _client_to_gateway() -> None:
                while True:
                    message = await websocket.receive_text()
                    await upstream.send(message)

            async def _gateway_to_client() -> None:
                async for message in upstream:
                    await websocket.send_text(message)

            client_to_gateway = asyncio.create_task(_client_to_gateway())
            gateway_to_client = asyncio.create_task(_gateway_to_client())
            done, pending = await asyncio.wait(
                {client_to_gateway, gateway_to_client},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc and not isinstance(exc, (WebSocketDisconnect, websockets.exceptions.ConnectionClosed)):
                    raise exc
    except WebSocketDisconnect as exc:
        logger.info(
            "Session stream proxy websocket disconnected session=%s code=%s reason=%s",
            session_id,
            getattr(exc, "code", None),
            getattr(exc, "reason", None),
        )
    except websockets.exceptions.ConnectionClosed:
        try:
            await websocket.close()
        except Exception:
            pass
    except Exception as exc:
        logger.warning("Gateway session stream proxy failed for %s: %s", session_id, exc)
        try:
            await websocket.close(code=1011, reason="Gateway stream unavailable")
        except Exception:
            pass
    finally:
        await _close_bridge(bridge)


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
                                initial_msg = await asyncio.wait_for(
                                    ws.recv(),
                                    timeout=gateway_ws_handshake_timeout_seconds(),
                                )
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

    except WebSocketDisconnect as exc:
        manager.disconnect(connection_id)
        logger.info(
            "WebSocket disconnected normally: %s code=%s reason=%s",
            connection_id,
            getattr(exc, "code", None),
            getattr(exc, "reason", None),
        )

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
        await _close_bridge(bridge)


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

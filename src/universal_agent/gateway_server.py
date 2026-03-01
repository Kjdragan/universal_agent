"""
Universal Agent Gateway Server — External HTTP/WebSocket API.

Exposes the InProcessGateway as a standalone service for external clients.
Server runs on port 8002 by default (configurable via UA_GATEWAY_PORT env var).

Usage:
    python -m universal_agent.gateway_server
"""

import asyncio
import contextlib
from collections import deque
import base64
import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import sqlite3
import threading
import time
import urllib.parse
import uuid
import io
import tarfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

BASE_DIR = Path(__file__).parent.parent.parent
from universal_agent.auth.ops_auth import (
    allow_legacy_ops_auth,
    issue_ops_jwt,
    validate_ops_token,
)
from universal_agent.runtime_bootstrap import bootstrap_runtime_environment
from universal_agent.runtime_role import FactoryRole, build_factory_runtime_policy
from universal_agent.delegation.redis_bus import (
    MISSION_CONSUMER_GROUP,
    MISSION_DLQ_STREAM,
    MISSION_STREAM,
    RedisMissionBus,
)
from universal_agent.delegation.schema import MissionEnvelope, MissionPayload

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from universal_agent.gateway import (
    InProcessGateway,
    GatewaySession,
    GatewayRequest,
    GatewaySessionSummary,
)
from universal_agent.agent_core import AgentEvent, EventType
from universal_agent.feature_flags import heartbeat_enabled, memory_index_enabled, cron_enabled
from universal_agent.identity import resolve_user_id
from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import (
    append_vp_event,
    finalize_vp_mission,
    get_vp_mission,
    get_vp_bridge_cursor,
    get_vp_session,
    list_vp_events,
    list_vp_missions,
    list_vp_sessions,
    list_vp_session_events,
    upsert_vp_bridge_cursor,
)
from universal_agent.vp import (
    MissionDispatchRequest,
    cancel_mission,
    dispatch_mission_with_retry,
    is_sqlite_lock_error,
)
from universal_agent.heartbeat_service import HeartbeatService
from universal_agent.cron_service import CronService, parse_run_at
from universal_agent.ops_service import OpsService
from universal_agent.ops_config import (
    apply_merge_patch,
    load_ops_config,
    ops_config_hash,
    ops_config_schema,
    write_ops_config,
)
from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.approvals import list_approvals, update_approval, upsert_approval
from universal_agent.work_threads import (
    append_work_thread_decision,
    list_work_threads,
    update_work_thread,
    upsert_work_thread,
)
from universal_agent.hooks_service import HooksService
from universal_agent.security_paths import (
    allow_external_workspaces_from_env,
    resolve_ops_log_path,
    resolve_workspace_dir,
    validate_session_id,
)
from universal_agent.session_policy import (
    evaluate_request_against_policy,
    load_session_policy,
    normalize_memory_policy,
    save_session_policy,
    update_session_policy,
)
from universal_agent.utils.json_utils import extract_json_payload
from universal_agent.youtube_ingest import ingest_youtube_transcript, normalize_video_target
from universal_agent.signals_ingest import (
    extract_valid_events,
    process_signals_ingest_payload,
    to_csi_analytics_action,
    to_manual_youtube_payload,
)
from universal_agent.mission_guardrails import build_mission_contract, MissionGuardrailTracker
from universal_agent.memory.orchestrator import get_memory_orchestrator
from universal_agent.memory.paths import resolve_shared_memory_workspace
from universal_agent.csi_confidence import confidence_baseline as _csi_confidence_baseline_model
from universal_agent.csi_confidence import score_event_confidence as _csi_score_event_confidence
from universal_agent.runtime_env import ensure_runtime_path, runtime_tool_status
from universal_agent.timeout_policy import (
    gateway_ws_send_timeout_seconds,
    session_cancel_wait_seconds,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
ensure_runtime_path()

# Feature flags (placeholders, no runtime behavior changes yet)
HEARTBEAT_ENABLED = heartbeat_enabled()
CRON_ENABLED = cron_enabled()
MEMORY_INDEX_ENABLED = memory_index_enabled()
MIN_HEARTBEAT_INTERVAL_SECONDS = 30 * 60
HEARTBEAT_INTERVAL_SECONDS = max(
    MIN_HEARTBEAT_INTERVAL_SECONDS,
    int(os.getenv("UA_HEARTBEAT_INTERVAL_SECONDS", str(MIN_HEARTBEAT_INTERVAL_SECONDS)) or MIN_HEARTBEAT_INTERVAL_SECONDS),
)
CALENDAR_HEARTBEAT_SESSION_MAX_IDLE_SECONDS = max(
    3600,
    int(os.getenv("UA_CALENDAR_HEARTBEAT_SESSION_MAX_IDLE_SECONDS", str(72 * 3600)) or (72 * 3600)),
)
CALENDAR_HEARTBEAT_STALE_CONNECTION_SECONDS = max(
    300,
    int(os.getenv("UA_CALENDAR_HEARTBEAT_STALE_CONNECTION_SECONDS", "1800") or 1800),
)

# 1. Configurable Workspaces Directory
# Default to AGENT_RUN_WORKSPACES in project root, but allow override via env var
_default_ws_dir = BASE_DIR / "AGENT_RUN_WORKSPACES"
env_ws_dir = os.getenv("UA_WORKSPACES_DIR")
if env_ws_dir:
    WORKSPACES_DIR = Path(env_ws_dir).resolve()
    logger.info(f"📁 Workspaces Directory Overridden: {WORKSPACES_DIR}")
else:
    WORKSPACES_DIR = _default_ws_dir

ARTIFACTS_DIR = resolve_artifacts_dir()
YOUTUBE_TUTORIAL_ARTIFACT_DIR_CANONICAL = "youtube-tutorial-creation"

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
)

_DEPLOYMENT_PROFILE = (os.getenv("UA_DEPLOYMENT_PROFILE") or "local_workstation").strip().lower()
if _DEPLOYMENT_PROFILE not in {"local_workstation", "standalone_node", "vps"}:
    _DEPLOYMENT_PROFILE = "local_workstation"

_FACTORY_POLICY = build_factory_runtime_policy()
_FACTORY_ID = (
    str(os.getenv("UA_FACTORY_ID") or "").strip()
    or str(os.getenv("INFISICAL_MACHINE_IDENTITY_NAME") or "").strip()
    or f"factory-{uuid.uuid4().hex[:8]}"
)


def _redis_url_from_env() -> str:
    explicit = str(os.getenv("UA_REDIS_URL") or "").strip()
    if explicit:
        return explicit
    host = str(os.getenv("UA_REDIS_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = int(str(os.getenv("UA_REDIS_PORT") or "6379").strip() or 6379)
    password = str(os.getenv("REDIS_PASSWORD") or "").strip()
    db = int(str(os.getenv("UA_REDIS_DB") or "0").strip() or 0)
    if password:
        encoded = urllib.parse.quote(password, safe="")
        return f"redis://:{encoded}@{host}:{port}/{db}"
    return f"redis://{host}:{port}/{db}"


def _delegation_redis_enabled() -> bool:
    raw = str(os.getenv("UA_DELEGATION_REDIS_ENABLED") or "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}

_LOCAL_WORKER_ALLOWED_PATHS = {
    "/api/v1/health",
    "/api/v1/hooks/readyz",
}

AUTONOMOUS_DAILY_BRIEFING_JOB_KEY = "autonomous_daily_briefing"
AUTONOMOUS_DAILY_BRIEFING_DEFAULT_CRON = "0 7 * * *"
AUTONOMOUS_DAILY_BRIEFING_DEFAULT_TIMEZONE = (
    (os.getenv("UA_AUTONOMOUS_BRIEFING_TIMEZONE") or "").strip()
    or (os.getenv("UA_DEFAULT_TIMEZONE") or "").strip()
    or "UTC"
)
TODOIST_CHRON_MAPPING_FILENAME = "todoist_chron_mappings.json"
TODOIST_CHRON_MAPPING_STORE_VERSION = 1
TODOIST_CHRON_MAPPING_MAX_ENTRIES = max(
    100,
    int(os.getenv("UA_TODOIST_CHRON_MAPPING_MAX_ENTRIES", "5000") or 5000),
)
TODOIST_CHRON_RECONCILE_ENABLED = (
    str(os.getenv("UA_TODOIST_CHRON_RECONCILE_ENABLED", "1")).strip().lower() in {"1", "true", "yes", "on"}
)
TODOIST_CHRON_RECONCILE_REMOVE_STALE = (
    str(os.getenv("UA_TODOIST_CHRON_RECONCILE_REMOVE_STALE", "1")).strip().lower() in {"1", "true", "yes", "on"}
)
TODOIST_CHRON_RECONCILE_INTERVAL_SECONDS = max(
    60.0,
    float(os.getenv("UA_TODOIST_CHRON_RECONCILE_INTERVAL_SECONDS", "600") or 600.0),
)
AUTONOMOUS_DAILY_BRIEFING_WINDOW_SECONDS = max(
    3600,
    int(os.getenv("UA_AUTONOMOUS_DAILY_BRIEFING_WINDOW_SECONDS", str(24 * 3600)) or (24 * 3600)),
)
AUTONOMOUS_DAILY_BRIEFING_MAX_ITEMS = max(
    5,
    int(os.getenv("UA_AUTONOMOUS_DAILY_BRIEFING_MAX_ITEMS", "200") or 200),
)
_todoist_chron_mapping_lock = threading.Lock()


def _deployment_profile_defaults() -> dict:
    if _DEPLOYMENT_PROFILE == "standalone_node":
        return {
            "profile": _DEPLOYMENT_PROFILE,
            "allowlist_required": False,
            "ops_token_required": True,
            "remote_access": "vpn_recommended",
            "notes": "Single-owner appliance posture with explicit ops token.",
        }
    if _DEPLOYMENT_PROFILE == "vps":
        return {
            "profile": _DEPLOYMENT_PROFILE,
            "allowlist_required": True,
            "ops_token_required": True,
            "remote_access": "vpn_or_strict_firewall",
            "notes": "Internet-exposed posture requires strict auth and network controls.",
        }
    return {
        "profile": _DEPLOYMENT_PROFILE,
        "allowlist_required": False,
        "ops_token_required": False,
        "remote_access": "local_only_default",
        "notes": "Development workstation defaults prioritize local iteration speed.",
    }


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


def _list_directory_under_root(root: Path, path: str = "") -> dict[str, Any]:
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


def _safe_slug_component(value: str) -> str:
    raw = str(value or "").strip().lower()
    out: list[str] = []
    prev_sep = False
    for ch in raw:
        is_alnum = ("a" <= ch <= "z") or ("0" <= ch <= "9")
        if is_alnum:
            out.append(ch)
            prev_sep = False
            continue
        if not prev_sep:
            out.append("-")
            prev_sep = True
    slug = "".join(out).strip("-")
    return slug or "item"


_TUTORIAL_REPO_NAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_tutorial_repo_name(value: str) -> str:
    cleaned = _TUTORIAL_REPO_NAME_SANITIZE_RE.sub("-", str(value or "").strip()).strip("._-")
    return cleaned[:120] if cleaned else ""


def _tutorial_bootstrap_target_root_default() -> str:
    configured = (
        (os.getenv("UA_TUTORIAL_BOOTSTRAP_TARGET_ROOT") or "").strip()
        or (os.getenv("UA_TUTORIAL_BOOTSTRAP_REPO_ROOT") or "").strip()
    )
    if configured:
        return configured
    if _DEPLOYMENT_PROFILE == "vps":
        return "/home/kjdragan/YoutubeCodeExamples"
    return "/home/kjdragan/YoutubeCodeExamples"


def _normalize_tutorial_bootstrap_execution_target(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"", "local", "desktop", "local_desktop"}:
        return "local"
    if normalized in {"server", "vps"}:
        return "server"
    return ""


def _tutorial_bootstrap_open_metadata(repo_dir: str) -> tuple[str, str]:
    normalized_repo_dir = str(repo_dir or "").strip()
    if not normalized_repo_dir:
        return "", ""
    try:
        resolved_path = Path(normalized_repo_dir).expanduser().resolve()
        open_uri = resolved_path.as_uri()
    except Exception:
        open_uri = f"file://{urllib.parse.quote(normalized_repo_dir)}"
    hint = "If your browser blocks file:// links, copy the repo path and open it in your file manager."
    return open_uri, hint


def _tutorial_bootstrap_enrich_job(job: dict[str, Any]) -> dict[str, Any]:
    record = dict(job)
    repo_dir = str(record.get("repo_dir") or "").strip()
    if not repo_dir:
        target_root = str(record.get("target_root") or "").strip()
        repo_name = str(record.get("repo_name") or "").strip()
        if target_root and repo_name:
            repo_dir = str((Path(target_root).expanduser() / repo_name).resolve())
            record["repo_dir"] = repo_dir
    if repo_dir:
        repo_open_uri, repo_open_hint = _tutorial_bootstrap_open_metadata(repo_dir)
        if repo_open_uri:
            record["repo_open_uri"] = repo_open_uri
        if repo_open_hint:
            record["repo_open_hint"] = repo_open_hint
    return record


def _storage_explorer_href(*, scope: str, path: str, preview: Optional[str] = None) -> str:
    normalized_path = str(path or "").strip().replace("\\", "/").strip("/")
    normalized_preview = str(preview or "").strip().replace("\\", "/").strip("/")

    # File deep-links should open the containing directory and preview the file.
    if normalized_preview and (not normalized_path or normalized_path == normalized_preview):
        normalized_path = normalized_preview.rsplit("/", 1)[0] if "/" in normalized_preview else ""

    if not normalized_path and not normalized_preview:
        return "/storage?tab=explorer"

    params: dict[str, str] = {"tab": "explorer", "scope": str(scope or "artifacts"), "root_source": "local"}
    if normalized_path:
        params["path"] = normalized_path
    if normalized_preview:
        params["preview"] = normalized_preview
    return f"/storage?{urllib.parse.urlencode(params)}"


def _artifact_rel_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ARTIFACTS_DIR.resolve()).as_posix()
    except Exception:
        return ""


def _tutorial_root_dirs() -> list[Path]:
    return [ARTIFACTS_DIR / YOUTUBE_TUTORIAL_ARTIFACT_DIR_CANONICAL]


def _is_tutorial_run_rel_path(rel_path: str) -> bool:
    normalized = str(rel_path or "").strip().strip("/")
    if not normalized:
        return False
    root_name = YOUTUBE_TUTORIAL_ARTIFACT_DIR_CANONICAL
    return (
        normalized == root_name or normalized.startswith(f"{root_name}/")
    )


def _artifact_api_file_url(rel_path: str) -> str:
    normalized = str(rel_path or "").strip().strip("/")
    if not normalized:
        return ""
    return f"/api/artifacts/files/{urllib.parse.quote(normalized, safe='/')}"


def _tutorial_manifest(run_dir: Path) -> dict[str, Any]:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists() or not manifest_path.is_file():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _tutorial_key_files(run_dir: Path, *, max_code_files: int = 4) -> list[dict[str, Any]]:
    ordered: list[tuple[str, Path]] = []
    for label, rel in (
        ("README", "README.md"),
        ("Concept", "CONCEPT.md"),
        ("Implementation Guide", "IMPLEMENTATION.md"),
    ):
        path = run_dir / rel
        if path.exists() and path.is_file():
            ordered.append((label, path))

    impl_dir = run_dir / "implementation"
    if impl_dir.exists() and impl_dir.is_dir():
        code: list[Path] = []
        for pattern in ("*.py", "*.ts", "*.tsx", "*.js", "*.sh", "*.ipynb", "*.gs", "*.html", "*.css", "*.jsx", "*.sql", "*.java", "*.go", "*.rs", "*.json"):
            code.extend(impl_dir.glob(pattern))
        code = [p for p in code if p.is_file()]
        code.sort(key=lambda p: p.name.lower())
        for path in code[: max(1, max_code_files)]:
            ordered.append((f"Code: {path.name}", path))

    files: list[dict[str, Any]] = []
    for label, path in ordered:
        rel = _artifact_rel_path(path)
        files.append(
            {
                "label": label,
                "name": path.name,
                "path": str(path),
                "rel_path": rel,
                "api_url": _artifact_api_file_url(rel),
                "storage_href": _storage_explorer_href(scope="artifacts", path=rel, preview=rel),
            }
        )
    return files


def _tutorial_has_code_implementation(run_dir: Path, manifest: dict) -> bool:
    """Determine whether a tutorial run involves code implementation.

    Checks manifest field first (set by agent), falls back to heuristic
    checking for actual code files in implementation/ directory.
    """
    # Prefer explicit manifest field if the agent set it
    manifest_flag = manifest.get("implementation_required")
    if manifest_flag is not None:
        return bool(manifest_flag)
        
    if (run_dir / "IMPLEMENTATION.md").exists() and (run_dir / "IMPLEMENTATION.md").is_file():
        return True

    # Fallback heuristic: check for code files in implementation/
    impl_dir = run_dir / "implementation"
    if not impl_dir.exists() or not impl_dir.is_dir():
        return False
    code_extensions = {".py", ".ts", ".tsx", ".js", ".sh", ".ipynb", ".gs", ".html", ".css", ".jsx", ".sql", ".java", ".go", ".rs", ".json"}
    for child in impl_dir.rglob("*"):
        if child.is_file() and child.suffix.lower() in code_extensions:
            return True
    return False


def _list_tutorial_runs(limit: int = 100) -> list[dict[str, Any]]:
    roots = [root for root in _tutorial_root_dirs() if root.exists() and root.is_dir()]
    if not roots:
        return []

    runs: list[tuple[float, dict[str, Any]]] = []
    for root in roots:
        # Support both legacy date-based layout and flat run folders by indexing
        # actual manifest files recursively under the tutorial root.
        seen_run_dirs: set[Path] = set()
        manifest_paths = sorted(
            root.rglob("manifest.json"),
            key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
            reverse=True,
        )
        for manifest_path in manifest_paths:
            run_dir = manifest_path.parent
            if run_dir in seen_run_dirs:
                continue
            seen_run_dirs.add(run_dir)
            try:
                mtime = float(manifest_path.stat().st_mtime)
            except Exception:
                try:
                    mtime = float(run_dir.stat().st_mtime)
                except Exception:
                    mtime = 0.0
            manifest = _tutorial_manifest(run_dir)
            if not manifest:
                continue
            run_rel = _artifact_rel_path(run_dir)
            if not run_rel:
                continue
            files = _tutorial_key_files(run_dir)
            title = str(manifest.get("title") or "").strip() or run_dir.name
            video_id = str(manifest.get("video_id") or "").strip()
            video_url = str(manifest.get("video_url") or "").strip()
            if not video_url and video_id:
                video_url = f"https://www.youtube.com/watch?v={video_id}"
            run_item = {
                "run_path": run_rel,
                "run_dir": str(run_dir),
                "run_name": run_dir.name,
                "created_at": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                "status": str(manifest.get("status") or "").strip() or "unknown",
                "title": title,
                "video_id": video_id,
                "video_url": video_url,
                "channel_id": str(manifest.get("channel_id") or "").strip(),
                "manifest_path": _artifact_rel_path(run_dir / "manifest.json"),
                "run_api_url": f"/api/artifacts?path={urllib.parse.quote(run_rel, safe='/')}",
                "run_storage_href": _storage_explorer_href(scope="artifacts", path=run_rel),
                "files": files,
                "implementation_required": _tutorial_has_code_implementation(run_dir, manifest),
            }
            runs.append((mtime, run_item))

    runs.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in runs[: max(1, min(limit, 1000))]]


def _remember_tutorial_review_job(job: dict[str, Any]) -> None:
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        return
    _tutorial_review_jobs[job_id] = dict(job)
    if len(_tutorial_review_jobs) <= _tutorial_review_jobs_max:
        return
    ordered = sorted(
        _tutorial_review_jobs.values(),
        key=lambda row: float(row.get("queued_at_epoch") or 0.0),
        reverse=True,
    )
    keep = {str(row.get("job_id")) for row in ordered[:_tutorial_review_jobs_max]}
    for existing in list(_tutorial_review_jobs.keys()):
        if existing not in keep:
            _tutorial_review_jobs.pop(existing, None)


def _tutorial_bootstrap_prune_locked() -> None:
    if len(_tutorial_bootstrap_jobs) <= _tutorial_bootstrap_jobs_max:
        return
    ordered = sorted(
        _tutorial_bootstrap_jobs.values(),
        key=lambda row: float(row.get("queued_at_epoch") or 0.0),
        reverse=True,
    )
    keep = {str(row.get("job_id") or "") for row in ordered[:_tutorial_bootstrap_jobs_max]}
    for existing in list(_tutorial_bootstrap_jobs.keys()):
        if existing not in keep:
            _tutorial_bootstrap_jobs.pop(existing, None)
    _tutorial_bootstrap_queue_copy = [job_id for job_id in _tutorial_bootstrap_queue if job_id in keep]
    _tutorial_bootstrap_queue.clear()
    _tutorial_bootstrap_queue.extend(_tutorial_bootstrap_queue_copy)


def _remember_tutorial_bootstrap_job(job: dict[str, Any]) -> dict[str, Any]:
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        return {}
    record = _tutorial_bootstrap_enrich_job(dict(job))
    with _tutorial_bootstrap_jobs_lock:
        _tutorial_bootstrap_jobs[job_id] = record
        status = str(record.get("status") or "").strip().lower()
        dispatch_backend = str(record.get("dispatch_backend") or "http_queue").strip().lower()
        if status == "queued" and dispatch_backend == "http_queue":
            if job_id not in _tutorial_bootstrap_queue:
                _tutorial_bootstrap_queue.append(job_id)
        else:
            _tutorial_bootstrap_queue_copy = [queued_id for queued_id in _tutorial_bootstrap_queue if queued_id != job_id]
            _tutorial_bootstrap_queue.clear()
            _tutorial_bootstrap_queue.extend(_tutorial_bootstrap_queue_copy)
        _tutorial_bootstrap_prune_locked()
        return _tutorial_bootstrap_enrich_job(dict(_tutorial_bootstrap_jobs.get(job_id) or record))


def _tutorial_bootstrap_list_jobs(*, limit: int = 100, run_path: str = "") -> list[dict[str, Any]]:
    with _tutorial_bootstrap_jobs_lock:
        rows = list(_tutorial_bootstrap_jobs.values())
    normalized_run_path = str(run_path or "").strip().strip("/")
    if normalized_run_path:
        rows = [
            row
            for row in rows
            if str(row.get("tutorial_run_path") or "").strip().strip("/") == normalized_run_path
        ]
    rows.sort(key=lambda row: float(row.get("queued_at_epoch") or 0.0), reverse=True)
    clamped = max(1, min(int(limit), 1000))
    return [_tutorial_bootstrap_enrich_job(dict(row)) for row in rows[:clamped]]


def _tutorial_bootstrap_find_active_job(*, run_path: str, execution_target: str = "local") -> Optional[dict[str, Any]]:
    normalized_run_path = str(run_path or "").strip().strip("/")
    normalized_target = str(execution_target or "").strip().lower() or "local"
    with _tutorial_bootstrap_jobs_lock:
        rows = sorted(
            _tutorial_bootstrap_jobs.values(),
            key=lambda row: float(row.get("queued_at_epoch") or 0.0),
            reverse=True,
        )
    for row in rows:
        if str(row.get("tutorial_run_path") or "").strip().strip("/") != normalized_run_path:
            continue
        if str(row.get("execution_target") or "").strip().lower() != normalized_target:
            continue
        status = str(row.get("status") or "").strip().lower()
        if status in {"queued", "running"}:
            return _tutorial_bootstrap_enrich_job(dict(row))
    return None


def _tutorial_bootstrap_claim_next(*, worker_id: str) -> Optional[dict[str, Any]]:
    worker = str(worker_id or "").strip() or f"worker-{uuid.uuid4().hex[:8]}"
    now_ts = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    with _tutorial_bootstrap_jobs_lock:
        # Recover stale running jobs so they can be retried.
        for job in _tutorial_bootstrap_jobs.values():
            status = str(job.get("status") or "").strip().lower()
            claimed_ts = float(job.get("claimed_at_epoch") or 0.0)
            if status == "running" and claimed_ts > 0 and (now_ts - claimed_ts) > _tutorial_bootstrap_claim_ttl_seconds:
                job["status"] = "queued"
                job["error"] = f"Requeued after stale claim timeout ({_tutorial_bootstrap_claim_ttl_seconds}s)"
                job["requeued_at"] = now_iso
                if str(job.get("job_id") or "") not in _tutorial_bootstrap_queue:
                    _tutorial_bootstrap_queue.append(str(job.get("job_id") or ""))

        while _tutorial_bootstrap_queue:
            job_id = str(_tutorial_bootstrap_queue.popleft() or "").strip()
            if not job_id:
                continue
            job = _tutorial_bootstrap_jobs.get(job_id)
            if not isinstance(job, dict):
                continue
            status = str(job.get("status") or "").strip().lower()
            dispatch_backend = str(job.get("dispatch_backend") or "http_queue").strip().lower()
            if status != "queued":
                continue
            if dispatch_backend != "http_queue":
                continue
            claims = int(job.get("claim_attempts") or 0) + 1
            job["status"] = "running"
            job["worker_id"] = worker
            job["claim_attempts"] = claims
            job["claimed_at"] = now_iso
            job["claimed_at_epoch"] = now_ts
            job["started_at"] = now_iso
            enriched = _tutorial_bootstrap_enrich_job(job)
            _tutorial_bootstrap_jobs[job_id] = enriched
            return dict(enriched)
    return None


def _tutorial_bootstrap_mark_running(*, job_id: str, worker_id: str) -> dict[str, Any]:
    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        raise HTTPException(status_code=400, detail="job_id is required")
    worker = str(worker_id or "").strip() or f"worker-{uuid.uuid4().hex[:8]}"
    now_ts = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    with _tutorial_bootstrap_jobs_lock:
        job = _tutorial_bootstrap_jobs.get(normalized_job_id)
        if not isinstance(job, dict):
            raise HTTPException(status_code=404, detail="Bootstrap job not found")
        dispatch_backend = str(job.get("dispatch_backend") or "http_queue").strip().lower()
        if dispatch_backend not in {"http_queue", "redis_stream"}:
            raise HTTPException(status_code=409, detail=f"Unsupported dispatch backend for running transition: {dispatch_backend}")
        status = str(job.get("status") or "").strip().lower()
        assigned_worker = str(job.get("worker_id") or "").strip()
        if assigned_worker and assigned_worker != worker:
            raise HTTPException(status_code=409, detail="Job is assigned to a different worker")
        if status not in {"queued", "running"}:
            raise HTTPException(status_code=409, detail=f"Job is not claimable from status={status or 'unknown'}")
        if status == "running":
            return _tutorial_bootstrap_enrich_job(dict(job))
        claims = int(job.get("claim_attempts") or 0) + 1
        job["status"] = "running"
        job["worker_id"] = worker
        job["claim_attempts"] = claims
        job["claimed_at"] = now_iso
        job["claimed_at_epoch"] = now_ts
        job["started_at"] = now_iso
        enriched = _tutorial_bootstrap_enrich_job(job)
        _tutorial_bootstrap_jobs[normalized_job_id] = enriched
        return dict(enriched)


def _tutorial_bootstrap_update_result(
    *,
    job_id: str,
    worker_id: str,
    status: str,
    repo_dir: str,
    stdout: str,
    stderr: str,
    error: str,
) -> dict[str, Any]:
    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        raise HTTPException(status_code=400, detail="job_id is required")
    status_normalized = str(status or "").strip().lower()
    if status_normalized not in {"completed", "failed"}:
        raise HTTPException(status_code=400, detail="status must be completed or failed")
    finished_at = datetime.now(timezone.utc).isoformat()
    with _tutorial_bootstrap_jobs_lock:
        job = _tutorial_bootstrap_jobs.get(normalized_job_id)
        if not isinstance(job, dict):
            raise HTTPException(status_code=404, detail="Bootstrap job not found")
        assigned_worker = str(job.get("worker_id") or "").strip()
        provided_worker = str(worker_id or "").strip()
        if assigned_worker and provided_worker and assigned_worker != provided_worker:
            raise HTTPException(status_code=409, detail="Job is assigned to a different worker")
        job["status"] = status_normalized
        job["completed_at"] = finished_at
        job["finished_at"] = finished_at
        job["repo_dir"] = str(repo_dir or "").strip()
        job["stdout"] = str(stdout or "")[-6000:]
        job["stderr"] = str(stderr or "")[-4000:]
        job["error"] = str(error or "")[-1200:]
        enriched = _tutorial_bootstrap_enrich_job(job)
        _tutorial_bootstrap_jobs[normalized_job_id] = enriched
        return dict(enriched)


def _factory_capabilities_payload() -> dict[str, Any]:
    provider_override = str(os.getenv("LLM_PROVIDER_OVERRIDE") or "").strip()
    capabilities = {
        "factory_id": _FACTORY_ID,
        "factory_role": _FACTORY_POLICY.role,
        "deployment_profile": _DEPLOYMENT_PROFILE,
        "gateway_mode": _FACTORY_POLICY.gateway_mode,
        "delegation_mode": _FACTORY_POLICY.delegation_mode,
        "heartbeat_scope": _FACTORY_POLICY.heartbeat_scope,
        "start_ui": bool(_FACTORY_POLICY.start_ui),
        "enable_telegram_poll": bool(_FACTORY_POLICY.enable_telegram_poll),
        "enable_vp_coder": str(os.getenv("ENABLE_VP_CODER", "true")).strip().lower() == "true",
        "llm_provider_override": provider_override or None,
        "redis_delegation_enabled": bool(_delegation_bus_enabled and _delegation_mission_bus is not None),
        "redis_stream_name": _delegation_bus_stream if _delegation_bus_enabled else None,
        "redis_consumer_group": _delegation_bus_group if _delegation_bus_enabled else None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return capabilities


def _factory_capability_labels() -> list[str]:
    payload = _factory_capabilities_payload()
    labels: list[str] = []
    if payload.get("gateway_mode") == "full":
        labels.append("gateway_full")
    if payload.get("gateway_mode") == "health_only":
        labels.append("gateway_health_only")
    if payload.get("start_ui"):
        labels.append("ui")
    if payload.get("enable_telegram_poll"):
        labels.append("telegram_poll")
    if payload.get("enable_vp_coder"):
        labels.append("vp_coder")
    if payload.get("redis_delegation_enabled"):
        labels.append("delegation_redis")
    labels.append(f"delegation_mode:{payload.get('delegation_mode')}")
    labels.append(f"heartbeat_scope:{payload.get('heartbeat_scope')}")
    if payload.get("llm_provider_override"):
        labels.append(f"llm_override:{payload.get('llm_provider_override')}")
    return sorted(set([str(label) for label in labels if str(label).strip()]))


def _upsert_factory_registration(payload: dict[str, Any], *, source: str) -> dict[str, Any]:
    now_iso = datetime.now(timezone.utc).isoformat()
    factory_id = str(payload.get("factory_id") or "").strip() or _FACTORY_ID
    role = str(payload.get("factory_role") or payload.get("role") or _FACTORY_POLICY.role).strip() or _FACTORY_POLICY.role
    record = {
        "factory_id": factory_id,
        "factory_role": role,
        "deployment_profile": str(payload.get("deployment_profile") or _DEPLOYMENT_PROFILE).strip() or _DEPLOYMENT_PROFILE,
        "source": source,
        "registration_status": str(payload.get("registration_status") or "online").strip() or "online",
        "heartbeat_latency_ms": payload.get("heartbeat_latency_ms"),
        "capabilities": payload.get("capabilities") if isinstance(payload.get("capabilities"), list) else [],
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        "last_seen_at": now_iso,
        "updated_at": now_iso,
    }
    with _factory_registration_lock:
        previous = _factory_registrations.get(factory_id)
        if isinstance(previous, dict):
            record["first_seen_at"] = str(previous.get("first_seen_at") or now_iso)
        else:
            record["first_seen_at"] = now_iso
        _factory_registrations[factory_id] = record
    return dict(record)


def _register_local_factory_presence() -> dict[str, Any]:
    capabilities_payload = _factory_capabilities_payload()
    payload = {
        "factory_id": _FACTORY_ID,
        "factory_role": _FACTORY_POLICY.role,
        "deployment_profile": _DEPLOYMENT_PROFILE,
        "capabilities": _factory_capability_labels(),
        "metadata": {
            "self_registration": True,
            "capabilities_payload": capabilities_payload,
        },
        "registration_status": "online",
    }
    return _upsert_factory_registration(payload, source="gateway_startup")


def _publish_tutorial_bootstrap_mission(
    *,
    request: Request,
    job: dict[str, Any],
) -> tuple[bool, str]:
    if _delegation_mission_bus is None or not _FACTORY_POLICY.can_publish_delegations:
        return False, ""
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        return False, ""
    base_url = str(request.base_url).rstrip("/")
    run_path = str(job.get("tutorial_run_path") or "").strip()
    payload = MissionEnvelope(
        job_id=job_id,
        idempotency_key=f"tutorial-bootstrap:{job_id}",
        priority=1,
        timeout_seconds=max(30, min(int(job.get("timeout_seconds") or 900), 3600)),
        max_retries=3,
        payload=MissionPayload(
            task=f"Bootstrap tutorial repo for run_path={run_path}",
            context={
                "mission_kind": "tutorial_bootstrap_repo",
                "job_id": job_id,
                "gateway_url": base_url,
                "tutorial_run_path": run_path,
                "repo_name": str(job.get("repo_name") or ""),
                "target_root": str(job.get("target_root") or ""),
                "python_version": str(job.get("python_version") or ""),
                "timeout_seconds": int(job.get("timeout_seconds") or 900),
                "_retry_count": 0,
            },
        ),
    )
    message_id = _delegation_mission_bus.publish_mission(payload)
    _delegation_metrics["last_publish_at"] = datetime.now(timezone.utc).isoformat()
    _delegation_metrics["published_total"] = int(_delegation_metrics.get("published_total") or 0) + 1
    return True, message_id


def _tutorial_review_prompt(
    *,
    run: dict[str, Any],
    review_output_dir: Path,
    extra_note: str = "",
) -> str:
    run_dir = str(run.get("run_dir") or "")
    video_url = str(run.get("video_url") or "")
    video_id = str(run.get("video_id") or "")
    title = str(run.get("title") or run.get("run_name") or "Tutorial").strip()
    key_files = run.get("files") if isinstance(run.get("files"), list) else []
    lines = [
        "Analyze this YouTube tutorial artifact package for immediate project value.",
        "",
        f"Tutorial Title: {title}",
        f"Video URL: {video_url}",
        f"Video ID: {video_id}",
        f"Artifact Run Directory: {run_dir}",
        "",
        "Key files to inspect first:",
    ]
    if key_files:
        for entry in key_files:
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("label") or entry.get("name") or "file")
            path = str(entry.get("path") or "")
            if path:
                lines.append(f"- {label}: {path}")
    else:
        lines.append(f"- {run_dir}")

    if extra_note.strip():
        lines.extend(["", "Operator note:", extra_note.strip()])

    review_md = review_output_dir / "REVIEW.md"
    review_json = review_output_dir / "SUMMARY.json"
    lines.extend(
        [
            "",
            "Deliverables (required):",
            f"1) Write an actionable markdown review to: {review_md}",
            f"2) Write a machine-readable summary JSON to: {review_json}",
            "",
            "REVIEW.md must include these sections:",
            "- Executive Summary",
            "- What Was Built (with concrete file references)",
            "- Immediate Reuse Opportunities for Universal Agent",
            "- Skill Candidate Assessment (yes/no, why, rough SKILL.md scope if yes)",
            "- Recommended Next Actions (prioritized, with effort/risk)",
            "",
            "SUMMARY.json schema:",
            '{ "title": "...", "decision": "adopt_now|prototype|archive", "confidence": 0.0-1.0, '
            '"skill_candidate": {"recommended": true|false, "name": "...", "why": "..."}, '
            '"top_actions": [{"action": "...", "effort": "S|M|L", "impact": "low|med|high"}] }',
            "",
            "Be concrete, cite exact files, and avoid generic commentary.",
        ]
    )
    return "\n".join(lines)


async def _run_tutorial_review_job(
    *,
    job_id: str,
    run: dict[str, Any],
    review_rel_path: str,
    note: str = "",
) -> None:
    queued = _tutorial_review_jobs.get(job_id, {})
    try:
        started = {
            **queued,
            "job_id": job_id,
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "review_run_path": review_rel_path,
        }
        _remember_tutorial_review_job(started)

        gateway = get_gateway()
        session_workspace = WORKSPACES_DIR / f"session_tutorial_review_{uuid.uuid4().hex[:10]}"
        session = await gateway.create_session(
            user_id="ops_tutorial_review",
            workspace_dir=str(session_workspace),
        )
        running = {
            **_tutorial_review_jobs.get(job_id, started),
            "session_id": session.session_id,
        }
        _remember_tutorial_review_job(running)
        review_output_dir = ARTIFACTS_DIR / review_rel_path
        review_output_dir.mkdir(parents=True, exist_ok=True)

        prompt = _tutorial_review_prompt(
            run=run,
            review_output_dir=review_output_dir,
            extra_note=note,
        )
        request = GatewayRequest(
            user_input=prompt,
            metadata={
                "source": "ops",
                "tutorial_review": True,
                "tutorial_run_path": str(run.get("run_path") or ""),
                "tutorial_review_output_path": review_rel_path,
            },
        )
        result = await gateway.run_query(session, request)
        updated = {
            **_tutorial_review_jobs.get(job_id, running),
            "job_id": job_id,
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "session_id": session.session_id,
            "review_run_path": review_rel_path,
            "result_preview": str(getattr(result, "response", "") or "")[:400],
        }
        _remember_tutorial_review_job(updated)
        _add_notification(
            kind="tutorial_review_ready",
            title="Simone Tutorial Review Ready",
            message=f"Review completed for: {str(run.get('title') or run.get('run_name') or 'tutorial')}",
            session_id=session.session_id,
            severity="info",
            metadata={
                "tutorial_run_path": str(run.get("run_path") or ""),
                "review_run_path": review_rel_path,
                "review_storage_href": _storage_explorer_href(scope="artifacts", path=review_rel_path),
                "source": "tutorial_review_worker",
            },
        )
    except Exception as exc:
        updated = {
            **_tutorial_review_jobs.get(job_id, queued),
            "job_id": job_id,
            "status": "failed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error": str(exc),
            "review_run_path": review_rel_path,
        }
        _remember_tutorial_review_job(updated)
        _add_notification(
            kind="tutorial_review_failed",
            title="Simone Tutorial Review Failed",
            message=f"Review failed for {str(run.get('title') or run.get('run_name') or 'tutorial')}: {exc}",
            severity="error",
            requires_action=True,
            metadata={
                "tutorial_run_path": str(run.get("run_path") or ""),
                "review_run_path": review_rel_path,
                "source": "tutorial_review_worker",
            },
        )

# 2. Allowlist Configuration
ALLOWED_USERS = set()
_allowed_users_str = os.getenv("UA_ALLOWED_USERS", "").strip()
if _allowed_users_str:
    ALLOWED_USERS = {u.strip() for u in _allowed_users_str.split(",") if u.strip()}
    # Keep strict allowlist mode, but automatically include runtime technical identities
    # that are commonly used by first-party UA services.
    _runtime_identity_candidates = {
        (os.getenv("COMPOSIO_USER_ID") or "").strip(),
        (os.getenv("DEFAULT_USER_ID") or "").strip(),
        (os.getenv("UA_DASHBOARD_OWNER_ID") or "").strip(),
    }
    _telegram_allowed = (os.getenv("TELEGRAM_ALLOWED_USER_IDS") or "").strip()
    if _telegram_allowed:
        _runtime_identity_candidates.update(
            {item.strip() for item in _telegram_allowed.split(",") if item.strip()}
        )
    _runtime_identity_candidates.discard("")
    _added_runtime_identities = sorted(_runtime_identity_candidates - ALLOWED_USERS)
    if _added_runtime_identities:
        ALLOWED_USERS.update(_added_runtime_identities)
        logger.info(
            "➕ Added runtime identities to allowlist: %s",
            ", ".join(_added_runtime_identities),
        )
    logger.info(f"🔒 Authenticated Access Only. Allowed Users: {len(ALLOWED_USERS)}")
else:
    logger.info("🔓 Public Access Mode (No Allowlist configured)")

# Ops access token (optional hard gate for /api/v1/ops/* endpoints)
OPS_TOKEN = os.getenv("UA_OPS_TOKEN", "").strip()
SESSION_API_TOKEN = (os.getenv("UA_INTERNAL_API_TOKEN", "").strip() or OPS_TOKEN)
OPS_JWT_SECRET = os.getenv("UA_OPS_JWT_SECRET", "").strip()
OPS_AUTH_ALLOW_LEGACY = allow_legacy_ops_auth()
_OPS_LEGACY_DEPRECATION_EMITTED = False


def _refresh_ops_auth_config_from_env() -> None:
    global OPS_TOKEN, SESSION_API_TOKEN, OPS_JWT_SECRET, OPS_AUTH_ALLOW_LEGACY
    OPS_TOKEN = os.getenv("UA_OPS_TOKEN", "").strip()
    SESSION_API_TOKEN = (os.getenv("UA_INTERNAL_API_TOKEN", "").strip() or OPS_TOKEN)
    OPS_JWT_SECRET = os.getenv("UA_OPS_JWT_SECRET", "").strip()
    OPS_AUTH_ALLOW_LEGACY = allow_legacy_ops_auth()


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
    user_id: Optional[str] = None
    metadata: dict = {}


class ExecuteRequest(BaseModel):
    user_input: str
    force_complex: bool = False
    metadata: dict = {}


class OpsTokenIssueRequest(BaseModel):
    subject: Optional[str] = None


class OpsTokenIssueResponse(BaseModel):
    token: str
    token_type: str = "Bearer"
    ttl_seconds: int
    expires_at: str


class FactoryRegistrationRequest(BaseModel):
    factory_id: Optional[str] = None
    factory_role: Optional[str] = None
    registration_status: str = "online"
    heartbeat_latency_ms: Optional[float] = None
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GatewayEventWire(BaseModel):
    type: str
    data: dict
    timestamp: str


class VpMissionDispatchRequest(BaseModel):
    vp_id: str
    mission_type: str = "task"
    objective: str
    constraints: dict = {}
    budget: dict = {}
    idempotency_key: Optional[str] = None
    source_session_id: Optional[str] = None
    source_turn_id: Optional[str] = None
    reply_mode: str = "async"
    priority: int = 100
    run_id: Optional[str] = None


class VpMissionCancelRequest(BaseModel):
    reason: Optional[str] = None


class HeartbeatWakeRequest(BaseModel):
    session_id: Optional[str] = None
    reason: Optional[str] = None
    mode: Optional[str] = None  # now | next


class CronJobCreateRequest(BaseModel):
    user_id: Optional[str] = None
    workspace_dir: Optional[str] = None
    command: str
    schedule_time: Optional[str] = None  # Simplified time input (e.g., "in 20 minutes", "4:30 pm")
    repeat: Optional[bool] = None  # Simplified repeat toggle
    timeout_seconds: Optional[int] = None  # Per-job execution timeout
    every: Optional[str] = None  # Simple interval (e.g., "30m", "1h")
    cron_expr: Optional[str] = None  # 5-field cron expression (e.g., "0 7 * * 1")
    timezone: str = "UTC"  # Timezone for cron expression
    run_at: Optional[str] = None  # One-shot: relative ("20m") or absolute ISO timestamp
    delete_after_run: bool = False  # One-shot: delete after successful run
    model: Optional[str] = None  # Model override for this job
    enabled: bool = True
    metadata: dict = {}


class CronJobUpdateRequest(BaseModel):
    command: Optional[str] = None
    schedule_time: Optional[str] = None  # Simplified time input for updates
    repeat: Optional[bool] = None  # Optional repeat override for simplified updates
    timeout_seconds: Optional[int] = None
    every: Optional[str] = None
    cron_expr: Optional[str] = None
    timezone: Optional[str] = None
    run_at: Optional[str] = None
    delete_after_run: Optional[bool] = None
    model: Optional[str] = None
    enabled: Optional[bool] = None
    workspace_dir: Optional[str] = None
    user_id: Optional[str] = None
    metadata: Optional[dict] = None


class SystemEventRequest(BaseModel):
    session_id: Optional[str] = None
    event_type: Optional[str] = None
    payload: Optional[dict] = None
    wake_heartbeat: Optional[str] = None  # now | next | truthy
    wake_mode: Optional[str] = None


class SystemPresenceRequest(BaseModel):
    node_id: Optional[str] = None
    status: Optional[str] = None
    reason: Optional[str] = None
    metadata: Optional[dict] = None


class OpsConfigRequest(BaseModel):
    config: dict = {}
    base_hash: Optional[str] = None


class OpsConfigPatchRequest(BaseModel):
    patch: dict = {}
    base_hash: Optional[str] = None


class OpsRemoteSyncUpdateRequest(BaseModel):
    enabled: bool = False


class OpsSkillUpdateRequest(BaseModel):
    enabled: Optional[bool] = None


class OpsVpBridgeCursorUpdateRequest(BaseModel):
    action: str = "set"  # set | reset_to_latest | reset_to_zero
    rowid: Optional[int] = None


class OpsApprovalCreateRequest(BaseModel):
    approval_id: Optional[str] = None
    phase_id: Optional[str] = None
    status: Optional[str] = None
    summary: Optional[str] = None
    requested_by: Optional[str] = None
    metadata: dict = {}


class OpsApprovalUpdateRequest(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    metadata: Optional[dict] = None


class OpsWorkThreadUpsertRequest(BaseModel):
    thread_id: Optional[str] = None
    session_id: str
    title: Optional[str] = None
    target: Optional[str] = None
    branch: Optional[str] = None
    workspace_dir: Optional[str] = None
    summary: Optional[str] = None
    status: Optional[str] = None
    acceptance_criteria: Optional[list[str]] = None
    open_questions: Optional[list[str]] = None
    patch_version: Optional[int] = None
    test_status: Optional[str] = None
    risk_notes: Optional[str] = None
    metadata: Optional[dict] = None


class OpsWorkThreadUpdateRequest(BaseModel):
    title: Optional[str] = None
    target: Optional[str] = None
    branch: Optional[str] = None
    workspace_dir: Optional[str] = None
    summary: Optional[str] = None
    status: Optional[str] = None
    acceptance_criteria: Optional[list[str]] = None
    open_questions: Optional[list[str]] = None
    patch_version: Optional[int] = None
    test_status: Optional[str] = None
    risk_notes: Optional[str] = None
    decision: Optional[str] = None
    decision_note: Optional[str] = None
    metadata: Optional[dict] = None


class OpsWorkThreadDecisionRequest(BaseModel):
    session_id: str
    decision: str
    note: Optional[str] = None
    metadata: Optional[dict] = None


class OpsSessionResetRequest(BaseModel):
    clear_logs: bool = True
    clear_memory: bool = True
    clear_work_products: bool = False


class OpsSessionCompactRequest(BaseModel):
    max_lines: int = 400
    max_bytes: int = 200_000


class OpsSessionArchiveRequest(BaseModel):
    clear_memory: bool = False
    clear_work_products: bool = False


class OpsSessionCancelRequest(BaseModel):
    reason: Optional[str] = None


class OpsCsiSessionPurgeRequest(BaseModel):
    dry_run: bool = False
    keep_latest: int = 2
    older_than_minutes: int = 30
    include_active: bool = False


class CalendarEventActionRequest(BaseModel):
    action: str
    run_at: Optional[str] = None
    timezone: Optional[str] = None
    note: Optional[str] = None


class CalendarEventChangeRequest(BaseModel):
    instruction: str
    timezone: Optional[str] = None


class CalendarEventChangeConfirmRequest(BaseModel):
    proposal_id: str
    approve: bool = True


class NotificationUpdateRequest(BaseModel):
    status: str
    note: Optional[str] = None
    snooze_minutes: Optional[int] = None


class NotificationBulkUpdateRequest(BaseModel):
    status: str
    note: Optional[str] = None
    kind: Optional[str] = None
    current_status: Optional[str] = None
    snooze_minutes: Optional[int] = None
    limit: int = 200


class NotificationPurgeRequest(BaseModel):
    clear_all: bool = False
    kind: Optional[str] = None
    current_status: Optional[str] = None
    older_than_hours: Optional[int] = None


class ActivitySendToSimoneRequest(BaseModel):
    instruction: str
    priority: Optional[str] = None
    extra_context: Optional[dict] = None


class ActivityEventActionRequest(BaseModel):
    action: str
    note: Optional[str] = None
    snooze_minutes: Optional[int] = None


class CSISpecialistLoopActionRequest(BaseModel):
    action: str
    note: Optional[str] = None
    follow_up_budget: Optional[int] = None


class CSISpecialistLoopTriageRequest(BaseModel):
    apply: bool = True
    max_items: int = 50
    request_followup: bool = False
    note: Optional[str] = None


class CSISpecialistLoopCleanupRequest(BaseModel):
    apply: bool = True
    older_than_days: int = 7
    max_items: int = 200
    note: Optional[str] = None


class DashboardEventPresetCreateRequest(BaseModel):
    name: str
    filters: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False


class DashboardEventPresetUpdateRequest(BaseModel):
    name: Optional[str] = None
    filters: Optional[dict[str, Any]] = None
    is_default: Optional[bool] = None
    mark_used: bool = False


class OpsNotificationCreateRequest(BaseModel):
    kind: str
    title: str
    message: str
    severity: str = "warning"
    requires_action: bool = False
    session_id: Optional[str] = None
    metadata: Optional[dict] = None


class DashboardSystemCommandRequest(BaseModel):
    text: str
    source_page: Optional[str] = None
    source_context: Optional[dict] = None
    timezone: Optional[str] = None
    dry_run: bool = False


class TutorialReviewDispatchRequest(BaseModel):
    run_path: str
    note: Optional[str] = None


class TutorialBootstrapRepoRequest(BaseModel):
    run_path: str
    repo_name: Optional[str] = None
    target_root: Optional[str] = None
    python_version: Optional[str] = None
    timeout_seconds: int = 900
    execution_target: str = "local"


class TutorialBootstrapJobClaimRequest(BaseModel):
    worker_id: Optional[str] = None


class TutorialBootstrapJobResultRequest(BaseModel):
    worker_id: Optional[str] = None
    status: str
    repo_dir: Optional[str] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    error: Optional[str] = None


class SessionPolicyPatchRequest(BaseModel):
    patch: dict = {}


class ResumeRequest(BaseModel):
    approval_id: Optional[str] = None
    reason: Optional[str] = None


class YouTubeIngestRequest(BaseModel):
    video_url: Optional[str] = None
    video_id: Optional[str] = None
    language: str = "en"
    timeout_seconds: int = 120
    max_chars: int = 180_000
    min_chars: int = 160
    request_id: Optional[str] = None

class VisionDescribeRequest(BaseModel):
    image_base64: str = Field(..., description="Base64 encoded image string (e.g. data:image/png;base64,...)")
    prompt: str = Field("Describe this image in detail.", description="Instructions for the vision model")


# =============================================================================
# Gateway Singleton
# =============================================================================

_gateway: Optional[InProcessGateway] = None
_sessions: dict[str, GatewaySession] = {}
_session_runtime: dict[str, dict[str, Any]] = {}
_heartbeat_service: Optional[HeartbeatService] = None
_cron_service: Optional[CronService] = None
_ops_service: Optional[OpsService] = None
_hooks_service: Optional[HooksService] = None
_system_events: dict[str, list[dict]] = {}
_system_presence: dict[str, dict] = {}
_system_events_max = int(os.getenv("UA_SYSTEM_EVENTS_MAX", "100"))
_vp_event_bridge_enabled = (os.getenv("UA_VP_EVENT_BRIDGE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"})
_vp_event_bridge_interval_seconds = max(
    0.2,
    float(os.getenv("UA_VP_EVENT_BRIDGE_INTERVAL_SECONDS", "1.0") or "1.0"),
)
_vp_event_bridge_cursor_key = "gateway.session_feed"
_vp_event_bridge_task: Optional[asyncio.Task[Any]] = None
_vp_event_bridge_stop_event: Optional[asyncio.Event] = None
_todoist_chron_reconcile_task: Optional[asyncio.Task[Any]] = None
_todoist_chron_reconcile_stop_event: Optional[asyncio.Event] = None
_vp_event_bridge_last_rowid = 0
_vp_event_bridge_metrics: dict[str, Any] = {
    "cycles": 0,
    "events_bridged_total": 0,
    "events_bridged_last": 0,
    "errors": 0,
    "last_error": None,
    "last_run_at": None,
    "manual_updates": 0,
    "last_manual_update_at": None,
}
_vp_stale_reconcile_enabled = (
    os.getenv("UA_VP_STALE_RECONCILE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
)
_vp_stale_reconcile_seconds = max(
    60,
    int(os.getenv("UA_VP_STALE_RECONCILE_SECONDS", "").strip() or 15 * 60),
)
_channel_probe_results: dict[str, dict] = {}
_notifications: list[dict] = []
_notifications_max = int(os.getenv("UA_NOTIFICATIONS_MAX", "500"))
_activity_events_retention_days = max(
    7,
    int(os.getenv("UA_ACTIVITY_EVENTS_RETENTION_DAYS", "90") or 90),
)
_activity_events_default_window_days = max(
    1,
    int(os.getenv("UA_ACTIVITY_DEFAULT_WINDOW_DAYS", "7") or 7),
)
_activity_stream_retention_days = max(
    1,
    int(os.getenv("UA_ACTIVITY_STREAM_RETENTION_DAYS", "14") or 14),
)
_dashboard_events_sse_enabled = (
    os.getenv("UA_DASHBOARD_EVENTS_SSE_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
)
_activity_digest_enabled = (
    os.getenv("UA_ACTIVITY_DIGEST_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
)
_activity_digest_exclude_kinds = {
    token.strip().lower()
    for token in (os.getenv("UA_ACTIVITY_DIGEST_EXCLUDE_KINDS", "") or "").split(",")
    if token.strip()
}
_activity_digest_max_sample_ids = max(
    1,
    min(100, int(os.getenv("UA_ACTIVITY_DIGEST_MAX_SAMPLE_IDS", "20") or 20)),
)
_activity_store_lock = threading.Lock()
_csi_dispatch_recent: dict[str, float] = {}
_csi_dispatch_lock = threading.Lock()
_csi_specialist_loop_lock = threading.Lock()
_csi_specialist_followup_budget = max(
    1,
    int(os.getenv("UA_CSI_SPECIALIST_FOLLOWUP_BUDGET", "3") or 3),
)
_csi_specialist_confidence_target = max(
    0.5,
    min(0.95, float(os.getenv("UA_CSI_SPECIALIST_CONFIDENCE_TARGET", "0.72") or 0.72)),
)
_csi_specialist_followup_cooldown_minutes = max(
    5,
    int(os.getenv("UA_CSI_SPECIALIST_FOLLOWUP_COOLDOWN_MINUTES", "45") or 45),
)
_csi_specialist_min_signal_volume = max(
    1,
    int(os.getenv("UA_CSI_SPECIALIST_MIN_SIGNAL_VOLUME", "5") or 5),
)
_csi_specialist_low_signal_streak_threshold = max(
    1,
    int(os.getenv("UA_CSI_SPECIALIST_LOW_SIGNAL_STREAK_THRESHOLD", "2") or 2),
)
_csi_specialist_low_signal_suppress_minutes = max(
    15,
    int(os.getenv("UA_CSI_SPECIALIST_LOW_SIGNAL_SUPPRESS_MINUTES", "120") or 120),
)
_csi_specialist_stale_evidence_minutes = max(
    30,
    int(os.getenv("UA_CSI_SPECIALIST_STALE_EVIDENCE_MINUTES", "180") or 180),
)
_csi_specialist_alert_cooldown_minutes = max(
    10,
    int(os.getenv("UA_CSI_SPECIALIST_ALERT_COOLDOWN_MINUTES", "120") or 120),
)
try:
    _csi_specialist_confidence_drift_threshold = max(
        0.05,
        min(0.4, float(os.getenv("UA_CSI_SPECIALIST_CONFIDENCE_DRIFT_THRESHOLD", "0.12") or 0.12)),
    )
except Exception:
    _csi_specialist_confidence_drift_threshold = 0.12
_tutorial_review_jobs: dict[str, dict[str, Any]] = {}
_tutorial_review_jobs_max = int(os.getenv("UA_TUTORIAL_REVIEW_JOBS_MAX", "300") or 300)
_tutorial_bootstrap_jobs: dict[str, dict[str, Any]] = {}
_tutorial_bootstrap_queue: deque[str] = deque()
_tutorial_bootstrap_jobs_lock = threading.Lock()
_tutorial_bootstrap_jobs_max = int(os.getenv("UA_TUTORIAL_BOOTSTRAP_JOBS_MAX", "300") or 300)
_tutorial_bootstrap_claim_ttl_seconds = max(
    60,
    int(os.getenv("UA_TUTORIAL_BOOTSTRAP_CLAIM_TTL_SECONDS", "1800") or 1800),
)
_delegation_mission_bus: Optional[RedisMissionBus] = None
_delegation_bus_enabled = _delegation_redis_enabled()
_delegation_bus_stream = str(os.getenv("UA_DELEGATION_STREAM_NAME") or MISSION_STREAM).strip() or MISSION_STREAM
_delegation_bus_group = str(os.getenv("UA_DELEGATION_CONSUMER_GROUP") or MISSION_CONSUMER_GROUP).strip() or MISSION_CONSUMER_GROUP
_delegation_bus_dlq_stream = str(os.getenv("UA_DELEGATION_DLQ_STREAM") or MISSION_DLQ_STREAM).strip() or MISSION_DLQ_STREAM
_delegation_metrics: dict[str, Any] = {
    "redis_enabled": _delegation_bus_enabled,
    "connected": False,
    "last_error": None,
    "last_publish_at": None,
    "published_total": 0,
}
_factory_registration_lock = threading.Lock()
_factory_registrations: dict[str, dict[str, Any]] = {}
_continuity_active_alerts: set[str] = set()
_continuity_metric_events: deque[dict[str, Any]] = deque(
    maxlen=max(1000, int(os.getenv("UA_CONTINUITY_EVENT_MAXLEN", "20000") or 20000))
)
_pending_gated_requests: dict[str, dict] = {}
_session_turn_state: dict[str, dict[str, Any]] = {}
_session_turn_locks: dict[str, asyncio.Lock] = {}
_session_execution_tasks: dict[str, asyncio.Task[Any]] = {}
_calendar_missed_events: dict[str, dict[str, Any]] = {}
_calendar_missed_notifications: set[str] = set()
_calendar_change_proposals: dict[str, dict[str, Any]] = {}
_SYSTEM_CONFIGURATION_AGENT_SESSION_ID = "session_system_configuration_agent"
_observability_metrics: dict[str, Any] = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "sessions_created": 0,
    "ws_attach_attempts": 0,
    "ws_attach_successes": 0,
    "ws_attach_failures": 0,
    "resume_attempts": 0,
    "resume_successes": 0,
    "resume_failures": 0,
    "turn_busy_rejected": 0,
    "turn_duplicate_in_progress": 0,
    "turn_duplicate_completed": 0,
    "ws_send_failures": 0,
    "ws_send_timeouts": 0,
    "ws_stale_evictions": 0,
    "ws_disconnects_total": 0,
    "ws_close_codes": {},
    "ws_close_reasons": {},
    "ws_close_endpoints": {},
}
_scheduling_runtime_started_ts = time.time()
_scheduling_runtime_metrics: dict[str, Any] = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "counters": {
        "calendar_events_requests": 0,
        "calendar_action_requests": 0,
        "calendar_change_request_requests": 0,
        "calendar_change_confirm_requests": 0,
        "heartbeat_last_requests": 0,
        "heartbeat_wake_requests": 0,
        "event_emissions_total": 0,
        "cron_events_total": 0,
        "heartbeat_events_total": 0,
        "event_bus_published": 0,
        "projection_applied": 0,
        "projection_seed_count": 0,
        "projection_seed_jobs": 0,
        "projection_seed_runs": 0,
        "projection_read_hits": 0,
        "push_replay_requests": 0,
        "push_stream_connects": 0,
        "push_stream_disconnects": 0,
        "push_stream_keepalives": 0,
        "push_stream_event_payloads": 0,
    },
    "event_counts": {
        "cron": {},
        "heartbeat": {},
    },
    "projection": {
        "builds": 0,
        "duration_ms_last": 0.0,
        "duration_ms_max": 0.0,
        "duration_ms_total": 0.0,
        "events_total": 0,
        "always_running_total": 0,
        "stasis_total": 0,
        "due_lag_samples": 0,
        "due_lag_seconds_last": 0.0,
        "due_lag_seconds_max": 0.0,
        "due_lag_seconds_total": 0.0,
    },
    "todoist_chron_reconciliation": {
        "runs": 0,
        "last_run_at": None,
        "last_duration_ms": 0.0,
        "last_error": None,
        "last_result": {},
    },
}
_activity_runtime_started_ts = time.time()
_activity_runtime_metrics: dict[str, Any] = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "counters": {
        "events_sse_connects": 0,
        "events_sse_disconnects": 0,
        "events_sse_payloads": 0,
        "events_sse_heartbeats": 0,
        "events_sse_errors": 0,
        "digest_compacted_total": 0,
        "digest_immediate_bypass_total": 0,
    },
}
SCHED_PUSH_ENABLED = (
    os.getenv("UA_SCHED_PUSH_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
)
SCHED_EVENT_PROJECTION_ENABLED = (
    os.getenv("UA_SCHED_EVENT_PROJECTION_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
)

SESSION_STATE_IDLE = "idle"
SESSION_STATE_RUNNING = "running"
SESSION_STATE_TERMINAL = "terminal"
TURN_STATUS_RUNNING = "running"
TURN_STATUS_COMPLETED = "completed"
TURN_STATUS_FAILED = "failed"
TURN_STATUS_CANCELLED = "cancelled"
TURN_HISTORY_LIMIT = int(os.getenv("UA_TURN_HISTORY_LIMIT", "200"))
TURN_FINGERPRINT_DEDUPE_WINDOW_SECONDS = int(os.getenv("UA_TURN_FINGERPRINT_DEDUPE_WINDOW_SECONDS", "120"))
TURN_LINEAGE_DIRNAME = "turns"
CONTINUITY_RESUME_SUCCESS_MIN = float(os.getenv("UA_CONTINUITY_RESUME_SUCCESS_MIN", "0.90") or 0.90)
CONTINUITY_ATTACH_SUCCESS_MIN = float(os.getenv("UA_CONTINUITY_ATTACH_SUCCESS_MIN", "0.90") or 0.90)
CONTINUITY_FAILURE_WARN_THRESHOLD = int(os.getenv("UA_CONTINUITY_FAILURE_WARN_THRESHOLD", "3") or 3)
CONTINUITY_WINDOW_SECONDS = max(60, int(os.getenv("UA_CONTINUITY_WINDOW_SECONDS", "900") or 900))
CONTINUITY_RATE_MIN_ATTEMPTS = max(1, int(os.getenv("UA_CONTINUITY_RATE_MIN_ATTEMPTS", "3") or 3))
CONTINUITY_EVENT_RETENTION_SECONDS = max(
    CONTINUITY_WINDOW_SECONDS * 4,
    int(os.getenv("UA_CONTINUITY_EVENT_RETENTION_SECONDS", "3600") or 3600),
)
NOTIFICATION_SNOOZE_MINUTES_DEFAULT = int(os.getenv("UA_NOTIFICATION_SNOOZE_MINUTES_DEFAULT", "30") or 30)
NOTIFICATION_SNOOZE_MINUTES_MAX = int(os.getenv("UA_NOTIFICATION_SNOOZE_MINUTES_MAX", "1440") or 1440)
WS_SEND_TIMEOUT_SECONDS = gateway_ws_send_timeout_seconds()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _increment_metric(name: str, amount: int = 1) -> None:
    current = int(_observability_metrics.get(name, 0) or 0)
    _observability_metrics[name] = current + max(0, int(amount))
    _record_continuity_metric_event(name, amount=amount)
    _sync_continuity_notifications()


def _increment_bucket_metric(name: str, bucket: str, amount: int = 1) -> None:
    target = _observability_metrics.get(name)
    if not isinstance(target, dict):
        target = {}
        _observability_metrics[name] = target
    key = bucket if bucket else "unknown"
    target[key] = int(target.get(key, 0) or 0) + max(0, int(amount))


def _record_ws_close(code: Optional[int], reason: Optional[str], endpoint: str) -> None:
    _increment_metric("ws_disconnects_total", 1)
    code_key = str(int(code)) if isinstance(code, int) else "unknown"
    _increment_bucket_metric("ws_close_codes", code_key)
    reason_text = str(reason or "").strip()
    if not reason_text:
        reason_text = "unknown"
    # Keep cardinality bounded to prevent metrics bloat.
    if len(reason_text) > 120:
        reason_text = reason_text[:120]
    _increment_bucket_metric("ws_close_reasons", reason_text)
    _increment_bucket_metric("ws_close_endpoints", endpoint or "unknown")


def _record_continuity_metric_event(name: str, amount: int = 1, ts: Optional[float] = None) -> None:
    tracked = {
        "resume_attempts",
        "resume_successes",
        "resume_failures",
        "ws_attach_attempts",
        "ws_attach_successes",
        "ws_attach_failures",
    }
    if name not in tracked:
        return
    count = max(0, int(amount))
    if count <= 0:
        return
    now_ts = float(ts if ts is not None else time.time())
    # Keep event fanout bounded for large increments while preserving rough shape.
    capped = min(count, 100)
    for _ in range(capped):
        _continuity_metric_events.append({"name": name, "ts": now_ts})


def _continuity_window_counts(now_ts: Optional[float] = None) -> dict[str, int]:
    ts_now = float(now_ts if now_ts is not None else time.time())
    retention_start = ts_now - float(CONTINUITY_EVENT_RETENTION_SECONDS)
    while _continuity_metric_events and float(_continuity_metric_events[0].get("ts", 0.0) or 0.0) < retention_start:
        _continuity_metric_events.popleft()

    window_start = ts_now - float(CONTINUITY_WINDOW_SECONDS)
    counts = {
        "resume_attempts": 0,
        "resume_successes": 0,
        "resume_failures": 0,
        "ws_attach_attempts": 0,
        "ws_attach_successes": 0,
        "ws_attach_failures": 0,
    }
    for item in _continuity_metric_events:
        item_ts = float(item.get("ts", 0.0) or 0.0)
        if item_ts < window_start:
            continue
        name = str(item.get("name") or "")
        if name in counts:
            counts[name] += 1
    return counts


def _scheduling_counter_inc(name: str, amount: int = 1) -> None:
    counters = _scheduling_runtime_metrics.setdefault("counters", {})
    current = int(counters.get(name, 0) or 0)
    counters[name] = current + max(0, int(amount))


def _activity_counter_inc(name: str, amount: int = 1) -> None:
    counters = _activity_runtime_metrics.setdefault("counters", {})
    current = int(counters.get(name, 0) or 0)
    counters[name] = current + max(0, int(amount))


def _activity_runtime_metrics_snapshot() -> dict[str, Any]:
    data = json.loads(json.dumps(_activity_runtime_metrics))
    counters = data.setdefault("counters", {})
    digest_keys: set[str] = set()
    try:
        conn = _activity_connect()
        try:
            _ensure_activity_schema(conn)
            rows = conn.execute(
                """
                SELECT metadata_json
                FROM activity_events
                WHERE event_class = 'notification'
                  AND created_at >= datetime('now', ?)
                LIMIT 5000
                """,
                (f"-{max(1, int(_activity_events_default_window_days))} days",),
            ).fetchall()
            for row in rows:
                metadata = _activity_json_loads_obj(row["metadata_json"], default={})
                if not isinstance(metadata, dict):
                    continue
                if not bool(metadata.get("digest_compacted")):
                    continue
                key = str(metadata.get("digest_key") or "").strip()
                if key:
                    digest_keys.add(key)
        finally:
            conn.close()
    except Exception:
        pass
    counters["digest_buckets_open"] = len(digest_keys)
    data["uptime_seconds"] = round(max(0.0, time.time() - _activity_runtime_started_ts), 3)
    data["retention_days"] = {
        "activity_events": int(_activity_events_retention_days),
        "activity_stream": int(_activity_stream_retention_days),
    }
    data["feature_flags"] = {
        "dashboard_events_sse_enabled": bool(_dashboard_events_sse_enabled),
        "activity_digest_enabled": bool(_activity_digest_enabled),
    }
    return data


class SchedulingEventBus:
    def __init__(self, max_events: int = 5000):
        self.max_events = max(100, int(max_events))
        self._events: deque[dict[str, Any]] = deque(maxlen=self.max_events)
        self._seq: int = 0
        self._subscribers: list[Any] = []
        self._condition = asyncio.Condition()

    def subscribe(self, callback: Any) -> None:
        if callback in self._subscribers:
            return
        self._subscribers.append(callback)

    def publish(self, source: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._seq += 1
        envelope = {
            "seq": self._seq,
            "source": str(source or "unknown"),
            "type": str(event_type or "event"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": payload if isinstance(payload, dict) else {"value": payload},
        }
        self._events.append(envelope)
        for callback in list(self._subscribers):
            try:
                callback(envelope)
            except Exception as exc:
                logger.warning("Scheduling event subscriber failed: %s", exc)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._notify_waiters())
        except RuntimeError:
            pass
        return envelope

    def snapshot(self, since_seq: int = 0, limit: int = 5000) -> list[dict[str, Any]]:
        low_water = max(0, int(since_seq))
        max_items = max(1, min(int(limit), self.max_events))
        items = [event for event in list(self._events) if int(event.get("seq", 0)) > low_water]
        return items[-max_items:]

    async def _notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    async def wait_for_events(self, since_seq: int = 0, timeout_seconds: float = 20.0, limit: int = 5000) -> list[dict[str, Any]]:
        current = self.snapshot(since_seq=since_seq, limit=limit)
        if current:
            return current
        wait_for = max(1.0, float(timeout_seconds))
        try:
            async with self._condition:
                await asyncio.wait_for(self._condition.wait(), timeout=wait_for)
        except asyncio.TimeoutError:
            return []
        return self.snapshot(since_seq=since_seq, limit=limit)

    def info(self) -> dict[str, Any]:
        return {
            "seq": self._seq,
            "max_events": self.max_events,
            "buffered_events": len(self._events),
            "subscriber_count": len(self._subscribers),
        }


class SchedulingProjectionState:
    def __init__(self, enabled: bool = False):
        self.enabled = bool(enabled)
        self.version: int = 0
        self.last_event_seq: int = 0
        self.last_updated_at: Optional[str] = None
        self.seeded: bool = False
        self.cron_jobs: dict[str, Any] = {}
        self.cron_runs_by_job: dict[str, list[dict[str, Any]]] = {}
        self.cron_run_ids: set[str] = set()
        self.heartbeat_last_by_session: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        self.version = 0
        self.last_event_seq = 0
        self.last_updated_at = None
        self.seeded = False
        self.cron_jobs = {}
        self.cron_runs_by_job = {}
        self.cron_run_ids = set()
        self.heartbeat_last_by_session = {}

    def _mark_changed(self, seq: Optional[int] = None) -> None:
        self.version += 1
        if seq is not None:
            self.last_event_seq = max(self.last_event_seq, int(seq))
        self.last_updated_at = datetime.now(timezone.utc).isoformat()

    def _upsert_cron_job(self, job: dict[str, Any]) -> bool:
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            return False
        current = self.cron_jobs.get(job_id)
        if current is not None and getattr(current, "__dict__", {}) == job:
            return False
        self.cron_jobs[job_id] = SimpleNamespace(**job)
        return True

    def _delete_cron_job(self, job_id: str) -> bool:
        if job_id in self.cron_jobs:
            self.cron_jobs.pop(job_id, None)
            return True
        return False

    def _append_cron_run(self, run: dict[str, Any]) -> bool:
        run_id = str(run.get("run_id") or "").strip()
        if run_id and run_id in self.cron_run_ids:
            return False
        job_id = str(run.get("job_id") or "").strip()
        if not job_id:
            return False
        if run_id:
            self.cron_run_ids.add(run_id)
        bucket = self.cron_runs_by_job.setdefault(job_id, [])
        bucket.append(dict(run))
        # Keep bounded per job for memory safety.
        if len(bucket) > 5000:
            overflow = bucket[:-5000]
            bucket[:] = bucket[-5000:]
            for item in overflow:
                rid = str(item.get("run_id") or "").strip()
                if rid:
                    self.cron_run_ids.discard(rid)
        return True

    def seed_from_runtime(self) -> None:
        if not self.enabled or self.seeded:
            return
        changed = False
        if _cron_service:
            for job in _cron_service.list_jobs():
                changed = self._upsert_cron_job(job.to_dict()) or changed
            for run in _cron_service.list_runs(limit=5000):
                if isinstance(run, dict):
                    changed = self._append_cron_run(run) or changed
        for session_id in list(_sessions.keys()):
            if session_id not in self.heartbeat_last_by_session:
                self.heartbeat_last_by_session[session_id] = {
                    "type": "heartbeat_session_seen",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                changed = True
        self.seeded = True
        if changed:
            self._mark_changed()
        _scheduling_counter_inc("projection_seed_count")
        _scheduling_counter_inc("projection_seed_jobs", len(self.cron_jobs))
        _scheduling_counter_inc("projection_seed_runs", sum(len(v) for v in self.cron_runs_by_job.values()))

    def apply_event(self, envelope: dict[str, Any]) -> None:
        if not self.enabled or not isinstance(envelope, dict):
            return
        source = str(envelope.get("source") or "").strip().lower()
        event_type = str(envelope.get("type") or "").strip().lower()
        payload = envelope.get("data") if isinstance(envelope.get("data"), dict) else {}
        seq = int(envelope.get("seq") or 0)
        changed = False

        if source == "cron":
            if event_type in {"cron_job_created", "cron_job_updated"}:
                job_data = payload.get("job") if isinstance(payload.get("job"), dict) else None
                if job_data:
                    changed = self._upsert_cron_job(job_data)
            elif event_type == "cron_job_deleted":
                changed = self._delete_cron_job(str(payload.get("job_id") or ""))
            elif event_type in {"cron_run_started", "cron_run_completed"}:
                run_data = payload.get("run") if isinstance(payload.get("run"), dict) else None
                if run_data:
                    changed = self._append_cron_run(run_data)
        elif source == "heartbeat":
            session_id = str(payload.get("session_id") or "").strip()
            if session_id:
                previous = self.heartbeat_last_by_session.get(session_id)
                candidate = {
                    "type": event_type or "heartbeat_event",
                    "timestamp": str(envelope.get("timestamp") or datetime.now(timezone.utc).isoformat()),
                }
                if previous != candidate:
                    self.heartbeat_last_by_session[session_id] = candidate
                    changed = True

        if changed:
            self._mark_changed(seq=seq)
            _scheduling_counter_inc("projection_applied")

    def list_cron_jobs(self) -> list[Any]:
        return list(self.cron_jobs.values())

    def list_cron_runs(self, limit: int = 2000) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        for rows in self.cron_runs_by_job.values():
            merged.extend(rows)
        merged.sort(key=lambda row: float(row.get("started_at") or row.get("scheduled_at") or 0.0), reverse=True)
        return merged[: max(1, int(limit))]

    def info(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "seeded": self.seeded,
            "version": self.version,
            "last_event_seq": self.last_event_seq,
            "last_updated_at": self.last_updated_at,
            "cron_jobs": len(self.cron_jobs),
            "cron_runs": sum(len(rows) for rows in self.cron_runs_by_job.values()),
            "heartbeat_sessions": len(self.heartbeat_last_by_session),
        }


_scheduling_event_bus = SchedulingEventBus(
    max_events=int(os.getenv("UA_SCHED_EVENT_BUS_MAX", "5000") or 5000)
)
_scheduling_projection = SchedulingProjectionState(enabled=SCHED_EVENT_PROJECTION_ENABLED)
_scheduling_event_bus.subscribe(_scheduling_projection.apply_event)


def _scheduling_record_event(source: str, event_type: Optional[str]) -> None:
    source_norm = (source or "unknown").strip().lower()
    event_norm = (event_type or f"{source_norm}_event").strip().lower() or f"{source_norm}_event"
    _scheduling_counter_inc("event_emissions_total")
    if source_norm == "cron":
        _scheduling_counter_inc("cron_events_total")
    elif source_norm == "heartbeat":
        _scheduling_counter_inc("heartbeat_events_total")
    bucket = _scheduling_runtime_metrics.setdefault("event_counts", {}).setdefault(source_norm, {})
    bucket[event_norm] = int(bucket.get(event_norm, 0) or 0) + 1


def _scheduling_record_projection_sample(
    *,
    duration_ms: float,
    events: list[dict[str, Any]],
    always_running: list[dict[str, Any]],
    stasis_count: int,
) -> None:
    projection = _scheduling_runtime_metrics.setdefault("projection", {})
    projection["builds"] = int(projection.get("builds", 0) or 0) + 1
    projection["duration_ms_last"] = round(duration_ms, 3)
    projection["duration_ms_total"] = float(projection.get("duration_ms_total", 0.0) or 0.0) + max(0.0, duration_ms)
    projection["duration_ms_max"] = max(float(projection.get("duration_ms_max", 0.0) or 0.0), max(0.0, duration_ms))
    projection["events_total"] = int(projection.get("events_total", 0) or 0) + max(0, len(events))
    projection["always_running_total"] = int(projection.get("always_running_total", 0) or 0) + max(0, len(always_running))
    projection["stasis_total"] = int(projection.get("stasis_total", 0) or 0) + max(0, int(stasis_count))

    now_ts = time.time()
    due_lags: list[float] = []
    for item in events:
        status = str(item.get("status") or "").strip().lower()
        if status not in {"scheduled", "running"}:
            continue
        scheduled_at = float(item.get("scheduled_at_epoch") or 0.0)
        if scheduled_at <= 0 or scheduled_at > now_ts:
            continue
        due_lags.append(max(0.0, now_ts - scheduled_at))

    if due_lags:
        lag_total = float(sum(due_lags))
        lag_last = float(max(due_lags))
        projection["due_lag_samples"] = int(projection.get("due_lag_samples", 0) or 0) + len(due_lags)
        projection["due_lag_seconds_total"] = float(projection.get("due_lag_seconds_total", 0.0) or 0.0) + lag_total
        projection["due_lag_seconds_last"] = round(lag_last, 3)
        projection["due_lag_seconds_max"] = max(float(projection.get("due_lag_seconds_max", 0.0) or 0.0), lag_last)
    else:
        projection["due_lag_seconds_last"] = 0.0


def _scheduling_runtime_metrics_snapshot() -> dict[str, Any]:
    data = json.loads(json.dumps(_scheduling_runtime_metrics))
    projection = data.setdefault("projection", {})
    builds = int(projection.get("builds", 0) or 0)
    duration_total = float(projection.get("duration_ms_total", 0.0) or 0.0)
    lag_samples = int(projection.get("due_lag_samples", 0) or 0)
    lag_total = float(projection.get("due_lag_seconds_total", 0.0) or 0.0)
    projection["duration_ms_avg"] = round(duration_total / builds, 3) if builds > 0 else 0.0
    projection["due_lag_seconds_avg"] = round(lag_total / lag_samples, 3) if lag_samples > 0 else 0.0
    data["uptime_seconds"] = round(max(0.0, time.time() - _scheduling_runtime_started_ts), 3)
    data["event_bus"] = _scheduling_event_bus.info()
    data["projection_state"] = _scheduling_projection.info()
    return data


def _continuity_alerts_snapshot() -> dict[str, Any]:
    now_ts = time.time()
    window_counts = _continuity_window_counts(now_ts=now_ts)
    window_start_ts = now_ts - float(CONTINUITY_WINDOW_SECONDS)
    resume_attempts = int(window_counts.get("resume_attempts", 0) or 0)
    resume_successes = int(window_counts.get("resume_successes", 0) or 0)
    ws_attach_attempts = int(window_counts.get("ws_attach_attempts", 0) or 0)
    ws_attach_successes = int(window_counts.get("ws_attach_successes", 0) or 0)
    resume_rate = round(resume_successes / resume_attempts, 4) if resume_attempts > 0 else None
    attach_rate = round(ws_attach_successes / ws_attach_attempts, 4) if ws_attach_attempts > 0 else None
    resume_failures = int(window_counts.get("resume_failures", 0) or 0)
    attach_failures = int(window_counts.get("ws_attach_failures", 0) or 0)
    rate_checks_enabled = (
        resume_attempts >= CONTINUITY_RATE_MIN_ATTEMPTS
        or ws_attach_attempts >= CONTINUITY_RATE_MIN_ATTEMPTS
    )
    alerts: list[dict[str, Any]] = []
    if (
        rate_checks_enabled
        and resume_rate is not None
        and resume_attempts >= CONTINUITY_RATE_MIN_ATTEMPTS
        and resume_rate < CONTINUITY_RESUME_SUCCESS_MIN
    ):
        alerts.append(
            {
                "code": "resume_success_rate_low",
                "severity": "warning",
                "message": "Resume success rate below threshold.",
                "actual": resume_rate,
                "threshold": CONTINUITY_RESUME_SUCCESS_MIN,
                "scope": "transport",
            }
        )
    if (
        rate_checks_enabled
        and attach_rate is not None
        and ws_attach_attempts >= CONTINUITY_RATE_MIN_ATTEMPTS
        and attach_rate < CONTINUITY_ATTACH_SUCCESS_MIN
    ):
        alerts.append(
            {
                "code": "attach_success_rate_low",
                "severity": "warning",
                "message": "Attach success rate below threshold.",
                "actual": attach_rate,
                "threshold": CONTINUITY_ATTACH_SUCCESS_MIN,
                "scope": "transport",
            }
        )
    if resume_failures >= CONTINUITY_FAILURE_WARN_THRESHOLD:
        alerts.append(
            {
                "code": "resume_failures_high",
                "severity": "warning",
                "message": "Resume failures exceeded warning threshold.",
                "actual": resume_failures,
                "threshold": CONTINUITY_FAILURE_WARN_THRESHOLD,
                "scope": "transport",
            }
        )
    if attach_failures >= CONTINUITY_FAILURE_WARN_THRESHOLD:
        alerts.append(
            {
                "code": "attach_failures_high",
                "severity": "warning",
                "message": "Attach failures exceeded warning threshold.",
                "actual": attach_failures,
                "threshold": CONTINUITY_FAILURE_WARN_THRESHOLD,
                "scope": "transport",
            }
        )

    runtime_faults = 0
    for runtime in _session_runtime.values():
        if not isinstance(runtime, dict):
            continue
        reason = str(runtime.get("terminal_reason") or "").strip().lower()
        if reason in {"error", "failed", "crashed", "exception"}:
            runtime_faults += 1

    transport_status = "degraded" if alerts else "ok"
    runtime_status = "degraded" if runtime_faults > 0 else "ok"
    return {
        "resume_success_rate": resume_rate,
        "attach_success_rate": attach_rate,
        "transport_status": transport_status,
        "runtime_status": runtime_status,
        "window_seconds": CONTINUITY_WINDOW_SECONDS,
        "window_started_at": datetime.fromtimestamp(window_start_ts, timezone.utc).isoformat(),
        "window_event_count": (
            resume_attempts
            + ws_attach_attempts
            + resume_failures
            + attach_failures
        ),
        "window": {
            "resume_attempts": resume_attempts,
            "resume_successes": resume_successes,
            "resume_failures": resume_failures,
            "resume_success_rate": resume_rate,
            "ws_attach_attempts": ws_attach_attempts,
            "ws_attach_successes": ws_attach_successes,
            "ws_attach_failures": attach_failures,
            "attach_success_rate": attach_rate,
        },
        "runtime_faults": runtime_faults,
        "alerts": alerts,
    }


def _sync_continuity_notifications() -> None:
    global _continuity_active_alerts
    # This function can be invoked early during app initialization in tests;
    # guard in case notification utilities are not yet fully available.
    if "_add_notification" not in globals():
        return
    snapshot = _continuity_alerts_snapshot()
    alerts = snapshot.get("alerts") or []
    if not isinstance(alerts, list):
        return
    by_code = {
        str(alert.get("code")): alert
        for alert in alerts
        if isinstance(alert, dict) and alert.get("code")
    }
    current_codes = set(by_code.keys())
    newly_active = current_codes - _continuity_active_alerts
    recovered = _continuity_active_alerts - current_codes

    for code in sorted(newly_active):
        alert = by_code.get(code, {})
        message = str(alert.get("message") or code)
        actual = alert.get("actual")
        threshold = alert.get("threshold")
        details = f" actual={actual}, threshold={threshold}" if actual is not None and threshold is not None else ""
        _add_notification(
            kind="continuity_alert",
            title="Session Continuity Alert",
            message=f"{message}.{details}",
            severity="warning",
            requires_action=False,
            metadata={"code": code, "alert": alert, "source": "session_continuity_metrics"},
        )

    for code in sorted(recovered):
        _add_notification(
            kind="continuity_recovered",
            title="Session Continuity Recovered",
            message=f"Continuity alert resolved: {code}.",
            severity="info",
            requires_action=False,
            metadata={"code": code, "source": "session_continuity_metrics"},
        )

    _continuity_active_alerts = current_codes


def _observability_metrics_snapshot() -> dict[str, Any]:
    duplicate_prevented = (
        int(_observability_metrics.get("turn_busy_rejected", 0) or 0)
        + int(_observability_metrics.get("turn_duplicate_in_progress", 0) or 0)
        + int(_observability_metrics.get("turn_duplicate_completed", 0) or 0)
    )
    continuity = _continuity_alerts_snapshot()
    execution_runtime: dict[str, Any] = {}
    gateway = _gateway
    if gateway is not None and hasattr(gateway, "execution_runtime_snapshot"):
        try:
            execution_runtime = gateway.execution_runtime_snapshot()
        except Exception:
            execution_runtime = {}
    return {
        **_observability_metrics,
        "duplicate_turn_prevention_count": duplicate_prevented,
        "resume_success_rate": continuity.get("resume_success_rate"),
        "attach_success_rate": continuity.get("attach_success_rate"),
        "transport_status": continuity.get("transport_status"),
        "runtime_status": continuity.get("runtime_status"),
        "window_seconds": continuity.get("window_seconds"),
        "window_started_at": continuity.get("window_started_at"),
        "window_event_count": continuity.get("window_event_count"),
        "window": continuity.get("window"),
        "runtime_faults": continuity.get("runtime_faults"),
        "alerts": continuity.get("alerts"),
        "execution_runtime": execution_runtime,
    }


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _vp_mission_duration_seconds(started_at: Any, completed_at: Any) -> Optional[float]:
    started = _parse_iso_datetime(started_at)
    completed = _parse_iso_datetime(completed_at)
    if not started or not completed:
        return None
    try:
        return max(0.0, (completed - started).total_seconds())
    except Exception:
        return None


def _parse_json_text(raw: Any) -> Any:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _vp_session_to_dict(row: Any) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    payload = {k: row[k] for k in row.keys()} if hasattr(row, "keys") else dict(row)
    metadata = _parse_json_text(payload.get("metadata_json"))
    if isinstance(metadata, dict):
        payload["metadata"] = metadata
    normalized_status = str(payload.get("status") or "unknown").strip().lower()
    stale = False
    stale_reason = ""
    if normalized_status in {"active", "running", "healthy"}:
        now_utc = datetime.now(timezone.utc)
        heartbeat_dt = _parse_iso_datetime(payload.get("last_heartbeat_at"))
        updated_dt = _parse_iso_datetime(payload.get("updated_at"))
        stale_window_seconds = max(
            60,
            int(
                os.getenv("UA_VP_STALE_SESSION_SECONDS", "").strip() or 15 * 60
            ),
        )
        if heartbeat_dt is not None:
            stale = (now_utc - heartbeat_dt).total_seconds() > stale_window_seconds
            if stale:
                stale_reason = "heartbeat_timeout"
        elif updated_dt is not None:
            stale = (now_utc - updated_dt).total_seconds() > stale_window_seconds
            if stale:
                stale_reason = "update_timeout"
        else:
            stale = True
            stale_reason = "missing_timestamps"
    payload["stale"] = stale
    payload["stale_reason"] = stale_reason or None
    payload["effective_status"] = "stale" if stale else normalized_status
    return payload


def _vp_mission_to_dict(row: Any) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    payload = {k: row[k] for k in row.keys()} if hasattr(row, "keys") else dict(row)
    budget = _parse_json_text(payload.get("budget_json"))
    if isinstance(budget, dict):
        payload["budget"] = budget
    mission_payload = _parse_json_text(payload.get("payload_json"))
    if isinstance(mission_payload, dict):
        payload["payload"] = mission_payload
    payload["duration_seconds"] = _vp_mission_duration_seconds(
        payload.get("started_at"), payload.get("completed_at")
    )
    return payload


def _vp_mission_source_context_from_row(row: Any) -> dict[str, str]:
    payload = _parse_json_text(row["payload_json"]) if "payload_json" in row.keys() else None
    if not isinstance(payload, dict):
        return {}
    context: dict[str, str] = {}
    for key in ("source_session_id", "source_turn_id", "reply_mode"):
        value = str(payload.get(key) or "").strip()
        if value:
            context[key] = value
    return context


def _vp_is_running_mission_stale(
    row: Any,
    *,
    now_utc: datetime,
    stale_seconds: int,
) -> tuple[bool, str]:
    status = str(row["status"] or "").strip().lower()
    if status != "running":
        return False, ""

    claim_expires_at = _parse_iso_datetime(row["claim_expires_at"])
    if claim_expires_at is not None:
        if claim_expires_at < now_utc:
            return True, "claim_expired"
        return False, ""

    for candidate_key in ("updated_at", "started_at", "created_at"):
        candidate_dt = _parse_iso_datetime(row[candidate_key])
        if candidate_dt is None:
            continue
        age_seconds = (now_utc - candidate_dt).total_seconds()
        if age_seconds > stale_seconds:
            return True, f"{candidate_key}_timeout"
        return False, ""
    return True, "missing_timestamps"


def _reconcile_stale_vp_missions_once(
    conn: Any,
    *,
    lane_label: str,
    stale_seconds: Optional[int] = None,
    max_rows: int = 1000,
) -> int:
    if conn is None:
        return 0
    now_utc = datetime.now(timezone.utc)
    stale_window = max(60, int(stale_seconds if stale_seconds is not None else _vp_stale_reconcile_seconds))
    rows = conn.execute(
        """
        SELECT *
        FROM vp_missions
        WHERE status = 'running'
        ORDER BY updated_at ASC
        LIMIT ?
        """,
        (max(1, min(int(max_rows), 10000)),),
    ).fetchall()
    reconciled = 0
    for row in rows:
        stale, stale_reason = _vp_is_running_mission_stale(
            row,
            now_utc=now_utc,
            stale_seconds=stale_window,
        )
        if not stale:
            continue

        mission_id = str(row["mission_id"] or "").strip()
        vp_id = str(row["vp_id"] or "").strip()
        if not mission_id or not vp_id:
            continue
        final_status = "cancelled" if int(row["cancel_requested"] or 0) == 1 else "failed"
        finalized = finalize_vp_mission(
            conn,
            mission_id,
            final_status,
            result_ref=str(row["result_ref"] or "").strip() or None,
        )
        if not finalized:
            continue

        context = _vp_mission_source_context_from_row(row)
        append_vp_event(
            conn,
            event_id=f"vp-event-{uuid.uuid4().hex}",
            mission_id=mission_id,
            vp_id=vp_id,
            event_type=f"vp.mission.{final_status}",
            payload={
                **context,
                "reason": "stale_running_reconciled",
                "stale_reason": stale_reason,
                "storage_lane": lane_label,
                "reconciled_by": "gateway.startup",
                "previous_status": "running",
            },
        )
        reconciled += 1

    return reconciled


def _reconcile_stale_vp_missions_on_startup() -> int:
    if not _vp_stale_reconcile_enabled:
        return 0
    gateway = get_gateway()
    conns: list[tuple[str, Any]] = []
    if hasattr(gateway, "get_vp_db_conn"):
        try:
            conns.append(("external", gateway.get_vp_db_conn()))
        except Exception:
            pass
    if hasattr(gateway, "get_coder_vp_db_conn"):
        try:
            conns.append(("coder", gateway.get_coder_vp_db_conn()))
        except Exception:
            pass

    seen_files: set[str] = set()
    reconciled_total = 0
    for lane_label, conn in conns:
        if conn is None:
            continue
        db_file = ""
        try:
            row = conn.execute("PRAGMA database_list").fetchone()
            db_file = str(row["file"] or "") if row else ""
        except Exception:
            db_file = ""
        dedupe_key = f"{lane_label}:{db_file}" if db_file else f"{lane_label}:{id(conn)}"
        if dedupe_key in seen_files:
            continue
        seen_files.add(dedupe_key)
        reconciled_total += _reconcile_stale_vp_missions_once(
            conn,
            lane_label=lane_label,
            stale_seconds=_vp_stale_reconcile_seconds,
        )
    return reconciled_total


def _vp_api_error(operation: str, exc: Exception) -> HTTPException:
    request_id = f"vp-{uuid.uuid4().hex[:10]}"
    if isinstance(exc, sqlite3.OperationalError) and is_sqlite_lock_error(exc):
        logger.warning(
            "VP API lock contention: op=%s request_id=%s error=%s",
            operation,
            request_id,
            exc,
        )
        return HTTPException(
            status_code=503,
            detail={
                "code": "vp_db_locked",
                "message": "VP storage is temporarily busy; retry shortly.",
                "retryable": True,
                "request_id": request_id,
            },
        )

    logger.exception(
        "VP API failure: op=%s request_id=%s",
        operation,
        request_id,
    )
    return HTTPException(
        status_code=500,
        detail={
            "code": "vp_internal_error",
            "message": "VP operation failed unexpectedly.",
            "request_id": request_id,
        },
    )


def _external_vp_conn(gateway: Any) -> Any:
    vp_conn = None
    if hasattr(gateway, "get_vp_db_conn"):
        try:
            vp_conn = gateway.get_vp_db_conn()
        except Exception:
            vp_conn = None
    if vp_conn is None:
        raise HTTPException(status_code=503, detail="VP DB not initialized")
    return vp_conn


def _vp_recovery_snapshot(
    session_row: Any,
    parsed_session_events: list[dict[str, Any]],
) -> dict[str, Any]:
    recovery_attempts = 0
    recovery_successes = 0
    unresolved_recoveries = 0

    for item in parsed_session_events:
        event_type = str(item.get("event_type") or "")
        if event_type == "vp.session.degraded":
            recovery_attempts += 1
            unresolved_recoveries += 1
            continue
        if event_type == "vp.session.resumed" and unresolved_recoveries > 0:
            recovery_successes += 1
            unresolved_recoveries -= 1

    session_status = ""
    if session_row is not None:
        session_status = str(session_row["status"] or "")

    currently_orphaned = session_status in {"degraded", "recovering"}
    if currently_orphaned and unresolved_recoveries == 0:
        unresolved_recoveries = 1

    recovery_success_rate = (
        recovery_successes / recovery_attempts if recovery_attempts > 0 else None
    )
    orphan_rate = (
        unresolved_recoveries / recovery_attempts
        if recovery_attempts > 0
        else (1.0 if currently_orphaned else 0.0)
    )
    return {
        "attempts": recovery_attempts,
        "successes": recovery_successes,
        "success_rate": recovery_success_rate,
        "currently_orphaned": currently_orphaned,
        "orphan_signals": unresolved_recoveries,
        "orphan_rate": orphan_rate,
    }


def _vp_metrics_snapshot(
    vp_id: str,
    mission_limit: int,
    event_limit: int,
    *,
    storage_lane: str = "external",
) -> dict[str, Any]:
    gateway = get_gateway()
    primary_conn = None
    if storage_lane == "coder":
        if hasattr(gateway, "get_coder_vp_db_conn"):
            try:
                primary_conn = gateway.get_coder_vp_db_conn()
            except Exception:
                primary_conn = None
    else:
        if hasattr(gateway, "get_vp_db_conn"):
            try:
                primary_conn = gateway.get_vp_db_conn()
            except Exception:
                primary_conn = None
    runtime_conn = getattr(gateway, "_runtime_db_conn", None)
    conn = primary_conn or runtime_conn
    if conn is None:
        raise HTTPException(status_code=503, detail="Runtime DB not initialized")

    session_row = get_vp_session(conn, vp_id)
    missions = list_vp_missions(conn, vp_id=vp_id, limit=mission_limit)
    events = list_vp_events(conn, vp_id=vp_id, limit=event_limit)
    session_events = list_vp_session_events(conn, vp_id=vp_id, limit=event_limit)

    # Backward compatibility: if VP data was written to runtime_state.db on older
    # builds, keep dashboard metrics visible during transition.
    if (
        conn is primary_conn
        and runtime_conn is not None
        and not session_row
        and not missions
        and not events
        and not session_events
    ):
        conn = runtime_conn
        session_row = get_vp_session(conn, vp_id)
        missions = list_vp_missions(conn, vp_id=vp_id, limit=mission_limit)
        events = list_vp_events(conn, vp_id=vp_id, limit=event_limit)
        session_events = list_vp_session_events(conn, vp_id=vp_id, limit=event_limit)

    mission_counts: dict[str, int] = {}
    mission_ids: set[str] = set()
    mission_rows: list[dict[str, Any]] = []
    duration_samples: list[float] = []
    for row in missions:
        status = str(row["status"] or "unknown")
        mission_counts[status] = mission_counts.get(status, 0) + 1
        mission_id = str(row["mission_id"] or "")
        if mission_id:
            mission_ids.add(mission_id)

        duration_seconds = _vp_mission_duration_seconds(row["started_at"], row["completed_at"])
        if duration_seconds is not None:
            duration_samples.append(duration_seconds)

        mission_rows.append(
            {
                "mission_id": mission_id,
                "status": status,
                "objective": row["objective"],
                "run_id": row["run_id"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "updated_at": row["updated_at"],
                "result_ref": row["result_ref"],
                "duration_seconds": duration_seconds,
            }
        )

    event_counts: dict[str, int] = {}
    fallback_mission_ids: set[str] = set()
    parsed_events: list[dict[str, Any]] = []
    for row in events:
        event_type = str(row["event_type"] or "unknown")
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
        mission_id = str(row["mission_id"] or "")
        if event_type == "vp.mission.fallback" and mission_id:
            fallback_mission_ids.add(mission_id)

        parsed_events.append(
            {
                "event_id": row["event_id"],
                "mission_id": row["mission_id"],
                "vp_id": row["vp_id"],
                "event_type": event_type,
                "payload": _parse_json_text(row["payload_json"]),
                "created_at": row["created_at"],
            }
        )

    session_event_counts: dict[str, int] = {}
    parsed_session_events: list[dict[str, Any]] = []
    for row in session_events:
        event_type = str(row["event_type"] or "unknown")
        session_event_counts[event_type] = session_event_counts.get(event_type, 0) + 1
        parsed_session_events.append(
            {
                "event_id": row["event_id"],
                "vp_id": row["vp_id"],
                "event_type": event_type,
                "payload": _parse_json_text(row["payload_json"]),
                "created_at": row["created_at"],
            }
        )

    for mission in mission_rows:
        mission["fallback_seen"] = mission["mission_id"] in fallback_mission_ids

    duration_stats: dict[str, Any] = {
        "count": 0,
        "avg_seconds": None,
        "p50_seconds": None,
        "p95_seconds": None,
        "max_seconds": None,
    }
    if duration_samples:
        sorted_durations = sorted(duration_samples)
        count = len(sorted_durations)
        p50_index = int(round((count - 1) * 0.50))
        p95_index = int(round((count - 1) * 0.95))
        duration_stats = {
            "count": count,
            "avg_seconds": sum(sorted_durations) / count,
            "p50_seconds": sorted_durations[p50_index],
            "p95_seconds": sorted_durations[p95_index],
            "max_seconds": sorted_durations[-1],
        }

    fallback_mission_count = len(mission_ids.intersection(fallback_mission_ids))
    fallback_rate = (fallback_mission_count / len(mission_ids)) if mission_ids else 0.0
    recovery = _vp_recovery_snapshot(session_row, parsed_session_events)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vp_id": vp_id,
        "session": _vp_session_to_dict(session_row),
        "mission_counts": mission_counts,
        "event_counts": event_counts,
        "session_event_counts": session_event_counts,
        "fallback": {
            "missions_with_fallback": fallback_mission_count,
            "missions_considered": len(mission_ids),
            "rate": fallback_rate,
        },
        "latency_seconds": duration_stats,
        "recovery": {
            "attempts": recovery["attempts"],
            "successes": recovery["successes"],
            "success_rate": recovery["success_rate"],
        },
        "session_health": {
            "currently_orphaned": recovery["currently_orphaned"],
            "orphan_signals": recovery["orphan_signals"],
            "orphan_rate": recovery["orphan_rate"],
        },
        "recent_missions": mission_rows,
        "recent_events": parsed_events,
        "recent_session_events": parsed_session_events,
    }


def _session_runtime_snapshot(session_id: str) -> dict[str, Any]:
    state = _session_runtime.get(session_id)
    if not state:
        state = {
            "session_id": session_id,
            "lifecycle_state": SESSION_STATE_IDLE,
            "last_event_seq": 0,
            "last_activity_at": _now_iso(),
            "active_connections": 0,
            "active_runs": 0,
            "active_foreground_runs": 0,
            "last_event_type": None,
            "terminal_reason": None,
            "last_run_source": None,
            "last_run_started_at": None,
            "last_run_finished_at": None,
            "last_foreground_run_started_at": None,
            "last_foreground_run_finished_at": None,
        }
        _session_runtime[session_id] = state
    return state


def _normalize_run_source(value: Any) -> str:
    source = str(value or "user").strip().lower()
    return source or "user"


def _workspace_dir_for_session(session_id: str) -> Optional[Path]:
    session = _sessions.get(session_id)
    if not session:
        try:
            gateway = get_gateway()
            gateway_sessions = getattr(gateway, "_sessions", {})
            if isinstance(gateway_sessions, dict):
                candidate = gateway_sessions.get(session_id)
                if candidate is not None:
                    session = candidate
        except Exception:
            session = None
    if not session:
        return None
    workspace = Path(str(session.workspace_dir or "")).expanduser()
    if not str(workspace):
        return None
    return workspace


def _run_log_size(workspace_dir: Optional[Path]) -> int:
    if workspace_dir is None:
        return 0
    log_path = workspace_dir / "run.log"
    try:
        if not log_path.exists():
            return 0
        return int(log_path.stat().st_size)
    except Exception:
        return 0


def _turn_lineage_path(session_id: str, turn_id: str) -> Optional[Path]:
    workspace = _workspace_dir_for_session(session_id)
    if workspace is None:
        return None
    return workspace / TURN_LINEAGE_DIRNAME / f"{turn_id}.jsonl"


def _append_turn_lineage_event(session_id: str, turn_id: str, payload: dict[str, Any]) -> None:
    lineage_path = _turn_lineage_path(session_id, turn_id)
    if lineage_path is None:
        return
    try:
        lineage_path.parent.mkdir(parents=True, exist_ok=True)
        with lineage_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")
    except Exception as exc:
        logger.warning(
            "Failed to append turn lineage event (session=%s, turn=%s): %s",
            session_id,
            turn_id,
            exc,
        )


def _runtime_status_from_counters(state: dict[str, Any]) -> str:
    if str(state.get("lifecycle_state")) == SESSION_STATE_TERMINAL:
        return SESSION_STATE_TERMINAL
    return SESSION_STATE_RUNNING if int(state.get("active_runs", 0)) > 0 else SESSION_STATE_IDLE


def _sync_runtime_metadata(session_id: str) -> None:
    session = _sessions.get(session_id)
    if not session:
        return
    runtime = _session_runtime_snapshot(session_id)
    session.metadata["runtime"] = {
        "lifecycle_state": runtime.get("lifecycle_state", SESSION_STATE_IDLE),
        "last_event_seq": int(runtime.get("last_event_seq", 0)),
        "last_activity_at": runtime.get("last_activity_at"),
        "active_connections": int(runtime.get("active_connections", 0)),
        "active_runs": int(runtime.get("active_runs", 0)),
        "active_foreground_runs": int(runtime.get("active_foreground_runs", 0)),
        "last_event_type": runtime.get("last_event_type"),
        "terminal_reason": runtime.get("terminal_reason"),
        "last_run_source": runtime.get("last_run_source"),
        "last_run_started_at": runtime.get("last_run_started_at"),
        "last_run_finished_at": runtime.get("last_run_finished_at"),
        "last_foreground_run_started_at": runtime.get("last_foreground_run_started_at"),
        "last_foreground_run_finished_at": runtime.get("last_foreground_run_finished_at"),
    }


def _record_session_event(session_id: str, event_type: Optional[str] = None) -> None:
    runtime = _session_runtime_snapshot(session_id)
    runtime["last_event_seq"] = int(runtime.get("last_event_seq", 0)) + 1
    runtime["last_activity_at"] = _now_iso()
    if event_type:
        runtime["last_event_type"] = event_type
    _sync_runtime_metadata(session_id)


def _session_turn_lock(session_id: str) -> asyncio.Lock:
    lock = _session_turn_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_turn_locks[session_id] = lock
    return lock


def _register_execution_task(session_id: str, task: asyncio.Task[Any]) -> None:
    _session_execution_tasks[session_id] = task

    def _cleanup(done_task: asyncio.Task[Any]) -> None:
        current = _session_execution_tasks.get(session_id)
        if current is done_task:
            _session_execution_tasks.pop(session_id, None)

    task.add_done_callback(_cleanup)


async def _cancel_execution_task(session_id: str, timeout_seconds: float = 5.0) -> bool:
    task = _session_execution_tasks.get(session_id)
    if task is None or task.done():
        return False

    task.cancel()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=max(0.1, float(timeout_seconds)))
    except asyncio.CancelledError:
        return True
    except asyncio.TimeoutError:
        logger.warning("Timed out waiting for execution task cancellation (session=%s)", session_id)
        return False
    except Exception as exc:
        logger.warning("Execution task cancellation raised (session=%s): %s", session_id, exc)
        return False
    return task.cancelled()


def _session_turn_snapshot(session_id: str) -> dict[str, Any]:
    snapshot = _session_turn_state.get(session_id)
    if not snapshot:
        snapshot = {
            "session_id": session_id,
            "active_turn_id": None,
            "turns": {},
            "last_turn_id": None,
        }
        _session_turn_state[session_id] = snapshot
    return snapshot


def _normalize_client_turn_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > 128:
        text = text[:128]
    return text


def _compute_turn_fingerprint(user_input: str, force_complex: bool, metadata: dict[str, Any]) -> str:
    # Keep fallback fingerprint coarse and stable across retries so it can block
    # accidental duplicate side effects from clients that do not send client_turn_id.
    payload = {
        "user_input": user_input,
        "force_complex": bool(force_complex),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_iso_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_notification_timestamp(value: Optional[str]) -> str:
    raw = str(value or "").strip()
    if not raw:
        return _utc_now_iso()
    parsed = _parse_iso_timestamp(raw)
    if parsed is None:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _notification_created_epoch(item: dict[str, Any]) -> Optional[float]:
    raw = item.get("created_at")
    if isinstance(raw, (int, float)):
        return float(raw)
    parsed = _parse_iso_timestamp(raw)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _has_recent_notification(
    *,
    kind: str,
    metadata_match: Optional[dict[str, str]] = None,
    within_seconds: int = 3600,
) -> bool:
    kind_norm = str(kind or "").strip().lower()
    now_ts = time.time()
    for item in reversed(_notifications):
        if str(item.get("kind") or "").strip().lower() != kind_norm:
            continue
        created_ts = _notification_created_epoch(item)
        if created_ts is None:
            continue
        if created_ts < (now_ts - max(1, int(within_seconds))):
            return False
        if metadata_match:
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                continue
            matched = True
            for key, expected in metadata_match.items():
                if str(metadata.get(key) or "").strip().lower() != str(expected).strip().lower():
                    matched = False
                    break
            if not matched:
                continue
        return True
    return False


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return max(0, int(default))
    try:
        return max(0, int(raw))
    except Exception:
        return max(0, int(default))


def _csi_dispatch_cooldown_seconds(event_type: str) -> int:
    event = str(event_type or "").strip().lower()
    default_seconds = _env_int("UA_CSI_ANALYTICS_DEFAULT_COOLDOWN_SECONDS", 0)
    per_event_env = {
        "analysis_task_completed": "UA_CSI_ANALYSIS_TASK_COMPLETED_COOLDOWN_SECONDS",
        "rss_insight_emerging": "UA_CSI_RSS_INSIGHT_EMERGING_COOLDOWN_SECONDS",
        "category_quality_report": "UA_CSI_CATEGORY_QUALITY_REPORT_COOLDOWN_SECONDS",
    }
    env_name = per_event_env.get(event)
    if env_name:
        fallback = {
            "analysis_task_completed": 1800,
            "rss_insight_emerging": 900,
            "category_quality_report": 1800,
        }.get(event, default_seconds)
        return _env_int(env_name, fallback)
    return default_seconds


def _csi_dispatch_is_throttled(source: str, event_type: str) -> tuple[bool, int]:
    cooldown = _csi_dispatch_cooldown_seconds(event_type)
    if cooldown <= 0:
        return False, 0
    key = f"{str(source or '').strip().lower()}:{str(event_type or '').strip().lower()}"
    now_ts = time.time()
    with _csi_dispatch_lock:
        last_ts = _csi_dispatch_recent.get(key)
    if last_ts is None:
        return False, 0
    remaining = cooldown - int(now_ts - float(last_ts))
    if remaining > 0:
        return True, remaining
    return False, 0


def _csi_record_dispatch(source: str, event_type: str) -> None:
    key = f"{str(source or '').strip().lower()}:{str(event_type or '').strip().lower()}"
    now_ts = time.time()
    with _csi_dispatch_lock:
        _csi_dispatch_recent[key] = now_ts
        if len(_csi_dispatch_recent) > 512:
            stale_before = now_ts - 24 * 3600
            stale_keys = [k for k, ts in _csi_dispatch_recent.items() if float(ts) < stale_before]
            for stale_key in stale_keys:
                _csi_dispatch_recent.pop(stale_key, None)


def _csi_should_digest_event(event_type: str) -> bool:
    lowered = str(event_type or "").strip().lower()
    return lowered in {
        "analysis_task_completed",
        "hourly_token_usage_report",
        "category_quality_report",
    }


def _csi_emit_digest_notification(event: Any, detail: str) -> None:
    occurred = _parse_iso_timestamp(getattr(event, "occurred_at", None)) or datetime.now(timezone.utc)
    hour_key = occurred.strftime("%Y-%m-%dT%H:00:00Z")
    metadata_match = {
        "digest_bucket": "csi_hourly_pipeline",
        "hour_key": hour_key,
    }
    event_type = str(getattr(event, "event_type", "") or "unknown")
    event_id = str(getattr(event, "event_id", "") or "")
    for item in reversed(_notifications):
        if str(item.get("kind") or "") != "csi_pipeline_digest":
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        if str(metadata.get("digest_bucket") or "") != metadata_match["digest_bucket"]:
            continue
        if str(metadata.get("hour_key") or "") != metadata_match["hour_key"]:
            continue
        counts = metadata.get("event_counts")
        if not isinstance(counts, dict):
            counts = {}
            metadata["event_counts"] = counts
        counts[event_type] = int(counts.get(event_type) or 0) + 1
        metadata["last_event_type"] = event_type
        metadata["last_event_id"] = event_id
        metadata["last_detail"] = detail[:1200]
        total = sum(int(v or 0) for v in counts.values())
        parts = [f"{k}:{int(v)}" for k, v in sorted(counts.items(), key=lambda kv: kv[0])]
        item["summary"] = f"Hourly pipeline digest ({hour_key}) — {total} events ({', '.join(parts)})"
        item["full_message"] = (
            f"CSI hourly pipeline digest for {hour_key}\n"
            f"Total digested events: {total}\n"
            f"Breakdown: {', '.join(parts)}\n\n"
            f"Most recent event detail:\n{detail[:4000]}"
        )
        item["message"] = item["full_message"]
        item["updated_at"] = _utc_now_iso()
        _persist_notification_activity(item)
        return

    _add_notification(
        kind="csi_pipeline_digest",
        title="CSI Pipeline Hourly Digest",
        message=f"CSI hourly pipeline digest started for {hour_key}.",
        summary=f"Hourly pipeline digest ({hour_key}) — 1 event ({event_type}:1)",
        full_message=(
            f"CSI hourly pipeline digest for {hour_key}\n"
            f"Total digested events: 1\n"
            f"Breakdown: {event_type}:1\n\n"
            f"Most recent event detail:\n{detail[:4000]}"
        ),
        severity="info",
        requires_action=False,
        metadata={
            **metadata_match,
            "event_counts": {event_type: 1},
            "last_event_type": event_type,
            "last_event_id": event_id,
            "last_detail": detail[:1200],
        },
        created_at=getattr(event, "occurred_at", None) or getattr(event, "received_at", None),
    )


def _csi_is_high_value_event(event_type: str) -> bool:
    lowered = str(event_type or "").strip().lower()
    return lowered in {
        "rss_trend_report",
        "reddit_trend_report",
        "rss_insight_emerging",
        "rss_insight_daily",
        "report_product_ready",
        "opportunity_bundle_ready",
        "delivery_health_auto_remediation_failed",
        "delivery_reliability_slo_breached",
    }


def _csi_event_has_anomaly(event_type: str, subject: Any) -> bool:
    lowered = str(event_type or "").strip().lower()
    if lowered == "delivery_health_regression":
        return True
    if lowered == "delivery_reliability_slo_breached":
        return True
    if "failed" in lowered or "error" in lowered or "regression" in lowered:
        return True
    subject_obj = subject if isinstance(subject, dict) else {}
    if lowered == "category_quality_report":
        action = str(subject_obj.get("action") or "").strip().lower()
        if action and action not in {"no_change", "ok", "healthy"}:
            return True
    return False


def _csi_event_notification_policy(event: Any) -> dict[str, Any]:
    event_type = str(getattr(event, "event_type", "") or "").strip().lower()
    subject = getattr(event, "subject", None)
    is_digest = _csi_should_digest_event(event_type)
    has_anomaly = _csi_event_has_anomaly(event_type, subject)
    high_value = _csi_is_high_value_event(event_type)
    subject_obj = subject if isinstance(subject, dict) else {}
    severity = "info"
    if event_type == "delivery_health_recovered":
        severity = "success"
    elif event_type == "delivery_health_regression":
        status_hint = str(subject_obj.get("status") or "").strip().lower()
        severity = "error" if status_hint == "failing" else "warning"
    elif event_type == "delivery_reliability_slo_recovered":
        severity = "success"
    elif event_type == "delivery_reliability_slo_breached":
        severity = "error"
    elif event_type == "delivery_health_auto_remediation_succeeded":
        severity = "success"
    elif event_type == "delivery_health_auto_remediation_failed":
        severity = "error"
    elif has_anomaly:
        severity = "error"
    elif "quality" in event_type and not is_digest:
        severity = "warning"
    requires_action = bool(
        has_anomaly
        or high_value
        or event_type
        in {
            "delivery_health_regression",
            "delivery_health_auto_remediation_failed",
            "delivery_reliability_slo_breached",
        }
    )
    if event_type in {"delivery_health_recovered", "delivery_reliability_slo_recovered"}:
        requires_action = False
    todoist_sync = bool(
        (has_anomaly and event_type != "delivery_health_recovered")
        or event_type in {
            "rss_insight_daily",
            "report_product_ready",
            "opportunity_bundle_ready",
            "rss_trend_report",
            "reddit_trend_report",
            "delivery_health_regression",
            "delivery_reliability_slo_breached",
        }
    )
    return {
        "event_type": event_type,
        "is_digest": is_digest,
        "high_value": high_value,
        "has_anomaly": has_anomaly,
        "severity": severity,
        "requires_action": requires_action,
        "todoist_sync": todoist_sync,
    }


def _csi_source_bucket(event: Any) -> str:
    event_type = str(getattr(event, "event_type", "") or "").strip().lower()
    source = str(getattr(event, "source", "") or "").strip().lower()
    subject = getattr(event, "subject", None)
    subject_obj = subject if isinstance(subject, dict) else {}
    report_type = str(subject_obj.get("report_type") or event_type).strip().lower()
    text = " ".join([event_type, source, report_type])
    if "reddit" in text:
        return "reddit"
    if "rss" in text:
        return "rss"
    if "youtube" in text:
        return "youtube"
    if "analysis_task" in text:
        return "analysis_task"
    return source or "csi"


def _csi_specialist_topic_key(event: Any) -> tuple[str, str]:
    subject = getattr(event, "subject", None)
    subject_obj = subject if isinstance(subject, dict) else {}
    report_key = str(subject_obj.get("report_key") or "").strip()
    source_bucket = _csi_source_bucket(event)
    event_type = str(getattr(event, "event_type", "") or "").strip().lower() or "unknown"
    if report_key:
        key = f"{source_bucket}:{report_key}"
        return key[:280], f"{source_bucket} :: {report_key}"
    occurred = _parse_iso_timestamp(getattr(event, "occurred_at", None)) or datetime.now(timezone.utc)
    day_key = occurred.strftime("%Y-%m-%d")
    key = f"{source_bucket}:{event_type}:{day_key}"
    return key[:280], f"{source_bucket} :: {event_type} ({day_key})"


def _csi_confidence_baseline(event_type: str) -> float:
    return float(_csi_confidence_baseline_model(event_type))


def _csi_parse_mix_json(raw: Any) -> dict[str, int]:
    parsed = _activity_json_loads_obj(raw, default={})
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in parsed.items():
        try:
            out[str(key)] = int(value or 0)
        except Exception:
            continue
    return out


def _csi_estimate_confidence(
    *,
    event_type: str,
    subject: Optional[dict[str, Any]],
    events_count: int,
    source_mix: dict[str, int],
) -> dict[str, Any]:
    scoring = _csi_score_event_confidence(
        event_type=event_type,
        subject=subject if isinstance(subject, dict) else {},
        events_count=int(events_count),
        source_mix=source_mix if isinstance(source_mix, dict) else {},
    )
    score = float(scoring.get("score") or 0.0)
    method = str(scoring.get("method") or "heuristic").strip().lower() or "heuristic"
    evidence = scoring.get("evidence") if isinstance(scoring.get("evidence"), dict) else {}
    return {
        "score": round(max(0.0, min(0.95, score)), 3),
        "method": method,
        "evidence": evidence,
    }


def _csi_should_request_followup(last_followup_requested_at: Optional[str]) -> bool:
    if not last_followup_requested_at:
        return True
    parsed = _parse_iso_timestamp(last_followup_requested_at)
    if parsed is None:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    cooldown_seconds = int(_csi_specialist_followup_cooldown_minutes) * 60
    return (time.time() - parsed.timestamp()) >= max(60, cooldown_seconds)


def _csi_update_specialist_loop(event: Any, detail: str) -> dict[str, Any]:
    event_type = str(getattr(event, "event_type", "") or "").strip().lower()
    if not _csi_is_high_value_event(event_type):
        return {"updated": False, "request_followup": False}
    subject_obj = getattr(event, "subject", None)
    if not isinstance(subject_obj, dict):
        subject_obj = {}
    topic_key, topic_label = _csi_specialist_topic_key(event)
    source_bucket = _csi_source_bucket(event)
    event_id = str(getattr(event, "event_id", "") or "")
    event_at = _normalize_notification_timestamp(getattr(event, "occurred_at", None) or getattr(event, "received_at", None))
    now_iso = _utc_now_iso()

    with _csi_specialist_loop_lock:
        conn = _activity_connect()
        try:
            _ensure_activity_schema(conn)
            row = conn.execute(
                "SELECT * FROM csi_specialist_loops WHERE topic_key = ? LIMIT 1",
                (topic_key,),
            ).fetchone()
            previous_confidence_score = 0.0
            previous_low_signal_streak = 0
            previous_suppressed_until = None
            if row is None:
                source_mix = {source_bucket: 1}
                events_count = 1
                followup_remaining = int(_csi_specialist_followup_budget)
                confidence_target = float(_csi_specialist_confidence_target)
                last_followup_requested_at = None
                created_at = now_iso
            else:
                source_mix = _csi_parse_mix_json(row["source_mix_json"])
                source_mix[source_bucket] = int(source_mix.get(source_bucket) or 0) + 1
                events_count = int(row["events_count"] or 0) + 1
                followup_remaining = int(row["follow_up_budget_remaining"] or 0)
                confidence_target = float(row["confidence_target"] or _csi_specialist_confidence_target)
                last_followup_requested_at = str(row["last_followup_requested_at"] or "") or None
                created_at = str(row["created_at"] or now_iso)
                previous_confidence_score = float(row["confidence_score"] or 0.0)
                previous_low_signal_streak = int(row["low_signal_streak"] or 0)
                previous_suppressed_until = str(row["suppressed_until"] or "") or None

            confidence = _csi_estimate_confidence(
                event_type=event_type,
                subject=subject_obj,
                events_count=events_count,
                source_mix=source_mix,
            )
            confidence_score = float(confidence.get("score") or 0.0)
            confidence_method = str(confidence.get("method") or "heuristic")
            confidence_evidence = confidence.get("evidence") if isinstance(confidence.get("evidence"), dict) else {}
            signal_volume = int(confidence_evidence.get("signal_volume") or 0)
            freshness_minutes = int(confidence_evidence.get("freshness_minutes") or 0)
            low_signal = signal_volume < int(_csi_specialist_min_signal_volume)
            if low_signal:
                # Guardrail: low-signal loops should not auto-close solely from baseline bonuses.
                confidence_score = min(confidence_score, max(0.0, confidence_target - 0.02))
                confidence_evidence["low_signal_guardrail_applied"] = True
            low_signal_streak = previous_low_signal_streak + 1 if low_signal else 0
            stale_evidence = freshness_minutes > int(_csi_specialist_stale_evidence_minutes)
            now_dt = _parse_iso_timestamp(now_iso) or datetime.now(timezone.utc)
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=timezone.utc)
            suppressed_until = previous_suppressed_until
            suppressed_active = False
            if suppressed_until:
                suppressed_dt = _parse_iso_timestamp(suppressed_until)
                if suppressed_dt is not None:
                    if suppressed_dt.tzinfo is None:
                        suppressed_dt = suppressed_dt.replace(tzinfo=timezone.utc)
                    suppressed_active = suppressed_dt.timestamp() > now_dt.timestamp()
            quality_alerts: list[dict[str, Any]] = []
            drift_amount = max(0.0, previous_confidence_score - confidence_score)
            if previous_confidence_score > 0 and drift_amount >= float(_csi_specialist_confidence_drift_threshold):
                quality_alerts.append(
                    {
                        "kind": "csi_specialist_confidence_drift",
                        "title": "CSI Specialist Confidence Drift",
                        "message": (
                            f"Loop {topic_label} confidence dropped by {drift_amount:.2f} "
                            f"({previous_confidence_score:.2f} → {confidence_score:.2f})."
                        ),
                        "severity": "warning",
                        "metadata": {
                            "topic_key": topic_key,
                            "drift_amount": round(drift_amount, 3),
                            "previous_confidence_score": previous_confidence_score,
                            "confidence_score": confidence_score,
                            "confidence_target": confidence_target,
                            "confidence_method": confidence_method,
                        },
                    }
                )
            if stale_evidence:
                quality_alerts.append(
                    {
                        "kind": "csi_specialist_evidence_stale",
                        "title": "CSI Specialist Evidence Stale",
                        "message": (
                            f"Loop {topic_label} evidence is stale "
                            f"({freshness_minutes}m > {_csi_specialist_stale_evidence_minutes}m)."
                        ),
                        "severity": "warning",
                        "metadata": {
                            "topic_key": topic_key,
                            "freshness_minutes": freshness_minutes,
                            "stale_threshold_minutes": int(_csi_specialist_stale_evidence_minutes),
                            "confidence_method": confidence_method,
                        },
                    }
                )
            request_followup = False
            status_value = "open"
            closed_at = None
            followup_note = ""
            if confidence_score >= confidence_target:
                status_value = "closed"
                closed_at = now_iso
                followup_note = "confidence_target_reached"
                low_signal_streak = 0
                suppressed_until = None
            elif low_signal_streak >= int(_csi_specialist_low_signal_streak_threshold):
                status_value = "suppressed_low_signal"
                followup_note = "low_signal_suppression_triggered"
                suppressed_until_dt = now_dt + timedelta(minutes=int(_csi_specialist_low_signal_suppress_minutes))
                suppressed_until = suppressed_until_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                if previous_low_signal_streak < int(_csi_specialist_low_signal_streak_threshold):
                    quality_alerts.append(
                        {
                            "kind": "csi_specialist_low_signal_suppressed",
                            "title": "CSI Specialist Low-Signal Suppression",
                            "message": (
                                f"Loop {topic_label} entered low-signal suppression for "
                                f"{int(_csi_specialist_low_signal_suppress_minutes)}m "
                                f"(signal_volume={signal_volume})."
                            ),
                            "severity": "warning",
                            "metadata": {
                                "topic_key": topic_key,
                                "signal_volume": signal_volume,
                                "low_signal_streak": low_signal_streak,
                                "low_signal_streak_threshold": int(_csi_specialist_low_signal_streak_threshold),
                                "suppressed_until": suppressed_until,
                            },
                        }
                    )
            elif suppressed_active:
                status_value = "suppressed_low_signal"
                followup_note = "low_signal_suppression_active"
            elif followup_remaining <= 0:
                status_value = "budget_exhausted"
                followup_note = "followup_budget_exhausted"
            elif stale_evidence:
                followup_note = "stale_evidence_hold"
            elif _csi_should_request_followup(last_followup_requested_at):
                request_followup = True
                followup_remaining -= 1
                followup_note = "followup_requested"
            else:
                followup_note = "cooldown_active"

            last_followup_requested = now_iso if request_followup else last_followup_requested_at
            conn.execute(
                """
                INSERT INTO csi_specialist_loops (
                    topic_key, topic_label, status, confidence_target, confidence_score,
                    follow_up_budget_total, follow_up_budget_remaining, events_count, source_mix_json,
                    confidence_method, evidence_json,
                    last_event_type, last_event_id, last_event_at, last_followup_requested_at,
                    low_signal_streak, suppressed_until,
                    created_at, updated_at, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(topic_key) DO UPDATE SET
                    topic_label=excluded.topic_label,
                    status=excluded.status,
                    confidence_target=excluded.confidence_target,
                    confidence_score=excluded.confidence_score,
                    follow_up_budget_total=excluded.follow_up_budget_total,
                    follow_up_budget_remaining=excluded.follow_up_budget_remaining,
                    events_count=excluded.events_count,
                    source_mix_json=excluded.source_mix_json,
                    confidence_method=excluded.confidence_method,
                    evidence_json=excluded.evidence_json,
                    last_event_type=excluded.last_event_type,
                    last_event_id=excluded.last_event_id,
                    last_event_at=excluded.last_event_at,
                    last_followup_requested_at=excluded.last_followup_requested_at,
                    low_signal_streak=excluded.low_signal_streak,
                    suppressed_until=excluded.suppressed_until,
                    updated_at=excluded.updated_at,
                    closed_at=excluded.closed_at
                """,
                (
                    topic_key,
                    topic_label,
                    status_value,
                    confidence_target,
                    confidence_score,
                    int(_csi_specialist_followup_budget),
                    max(0, followup_remaining),
                    events_count,
                    _activity_json_dumps(source_mix, fallback="{}"),
                    confidence_method,
                    _activity_json_dumps(confidence_evidence, fallback="{}"),
                    event_type,
                    event_id,
                    event_at,
                    last_followup_requested,
                    low_signal_streak,
                    suppressed_until,
                    created_at,
                    now_iso,
                    closed_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    # Packet 17: generate correlation_id for traceable follow-up
    _followup_correlation_id: str | None = None
    if request_followup:
        try:
            from universal_agent.csi_followup_contract import build_followup_request
            _fu_req = build_followup_request(
                topic_key=topic_key,
                reason=f"confidence {confidence_score:.3f} < target {confidence_target:.3f}",
                budget_remaining=max(0, followup_remaining),
                budget_total=int(_csi_specialist_followup_budget),
                request_type="targeted_followup",
                quality_threshold=confidence_target,
            )
            _followup_correlation_id = _fu_req.correlation_id
        except Exception:
            import uuid as _uuid_mod
            _followup_correlation_id = f"fu_{_uuid_mod.uuid4().hex[:12]}"

    followup_message = (
        "CSI specialist follow-up request.\n"
        f"topic_key: {topic_key}\n"
        f"topic_label: {topic_label}\n"
        f"event_type: {event_type}\n"
        f"event_id: {event_id}\n"
        f"correlation_id: {_followup_correlation_id or ''}\n"
        f"confidence_score: {confidence_score}\n"
        f"confidence_target: {confidence_target}\n"
        f"confidence_method: {confidence_method}\n"
        f"follow_up_budget_remaining: {max(0, followup_remaining)}\n"
        f"low_signal_streak: {low_signal_streak}\n"
        f"suppressed_until: {suppressed_until or ''}\n"
        "Request one focused follow-up CSI analysis task and summarize confidence deltas."
    )

    return {
        "updated": True,
        "topic_key": topic_key,
        "topic_label": topic_label,
        "status": status_value,
        "confidence_score": confidence_score,
        "confidence_target": confidence_target,
        "confidence_method": confidence_method,
        "confidence_evidence": confidence_evidence,
        "events_count": events_count,
        "low_signal_streak": low_signal_streak,
        "suppressed_until": suppressed_until,
        "quality_alerts": quality_alerts,
        "follow_up_budget_remaining": max(0, followup_remaining),
        "request_followup": request_followup,
        "followup_correlation_id": _followup_correlation_id,
        "followup_message": followup_message,
        "source_mix": source_mix,
        "note": followup_note,
    }


def _csi_emit_specialist_synthesis(event: Any, detail: str) -> None:
    event_type = str(getattr(event, "event_type", "") or "").strip().lower()
    if not _csi_is_high_value_event(event_type):
        return
    occurred = _parse_iso_timestamp(getattr(event, "occurred_at", None)) or datetime.now(timezone.utc)
    source_bucket = _csi_source_bucket(event)
    event_id = str(getattr(event, "event_id", "") or "")

    def _upsert_bucket(*, kind: str, bucket_key: str, title: str, report_class: str, window_hours: int) -> None:
        metadata_match = {"digest_bucket": kind, "bucket_key": bucket_key}
        report_key = f"{kind}:{bucket_key}"
        for item in reversed(_notifications):
            if str(item.get("kind") or "") != kind:
                continue
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                continue
            if str(metadata.get("digest_bucket") or "") != metadata_match["digest_bucket"]:
                continue
            if str(metadata.get("bucket_key") or "") != metadata_match["bucket_key"]:
                continue
            counts = metadata.get("event_counts")
            if not isinstance(counts, dict):
                counts = {}
                metadata["event_counts"] = counts
            source_mix = metadata.get("source_mix")
            if not isinstance(source_mix, dict):
                source_mix = {}
                metadata["source_mix"] = source_mix
            counts[event_type] = int(counts.get(event_type) or 0) + 1
            source_mix[source_bucket] = int(source_mix.get(source_bucket) or 0) + 1
            metadata["last_event_type"] = event_type
            metadata["last_event_id"] = event_id
            metadata["last_detail"] = detail[:1500]
            metadata["window_hours"] = window_hours
            metadata["report_class"] = report_class
            metadata["report_key"] = report_key
            total = sum(int(v or 0) for v in counts.values())
            parts = [f"{k}:{int(v)}" for k, v in sorted(counts.items(), key=lambda kv: kv[0])]
            source_parts = [f"{k}:{int(v)}" for k, v in sorted(source_mix.items(), key=lambda kv: kv[0])]
            item["summary"] = f"{title} ({bucket_key}) — {total} signals ({', '.join(parts)})"
            item["full_message"] = (
                f"{title} ({bucket_key})\n"
                f"Total signals reviewed: {total}\n"
                f"Signal mix: {', '.join(parts)}\n"
                f"Source mix: {', '.join(source_parts)}\n"
                f"Follow-up policy: bounded to {_csi_specialist_followup_budget} targeted CSI follow-up loops unless confidence is high.\n\n"
                f"Most recent signal detail:\n{detail[:4500]}"
            )
            item["message"] = item["full_message"]
            item["updated_at"] = _utc_now_iso()
            _persist_notification_activity(item)
            return

        _add_notification(
            kind=kind,
            title=title,
            message=f"{title} initialized for {bucket_key}.",
            summary=f"{title} ({bucket_key}) — 1 signal ({event_type}:1)",
            full_message=(
                f"{title} ({bucket_key})\n"
                "Total signals reviewed: 1\n"
                f"Signal mix: {event_type}:1\n"
                f"Source mix: {source_bucket}:1\n"
                f"Follow-up policy: bounded to {_csi_specialist_followup_budget} targeted CSI follow-up loops unless confidence is high.\n\n"
                f"Most recent signal detail:\n{detail[:4500]}"
            ),
            severity="info",
            requires_action=(report_class == "specialist_daily"),
            metadata={
                **metadata_match,
                "report_key": report_key,
                "report_class": report_class,
                "window_hours": window_hours,
                "event_counts": {event_type: 1},
                "source_mix": {source_bucket: 1},
                "last_event_type": event_type,
                "last_event_id": event_id,
                "last_detail": detail[:1500],
            },
            created_at=getattr(event, "occurred_at", None) or getattr(event, "received_at", None),
        )

    hour_key = occurred.strftime("%Y-%m-%dT%H:00:00Z")
    day_key = occurred.strftime("%Y-%m-%d")
    _upsert_bucket(
        kind="csi_specialist_hourly_synthesis",
        bucket_key=hour_key,
        title="CSI Specialist Hourly Synthesis",
        report_class="specialist_hourly",
        window_hours=1,
    )
    _upsert_bucket(
        kind="csi_specialist_daily_rollup",
        bucket_key=day_key,
        title="CSI Specialist Daily Rollup",
        report_class="specialist_daily",
        window_hours=24,
    )


def _trim_turn_history(state: dict[str, Any]) -> None:
    turns = state.get("turns", {})
    if not isinstance(turns, dict):
        return
    overflow = len(turns) - TURN_HISTORY_LIMIT
    if overflow <= 0:
        return
    active_turn_id = state.get("active_turn_id")
    for turn_id in list(turns.keys()):
        if overflow <= 0:
            break
        if turn_id == active_turn_id:
            continue
        turns.pop(turn_id, None)
        overflow -= 1


def _admit_turn(
    session_id: str,
    connection_id: str,
    user_input: str,
    force_complex: bool,
    metadata: dict[str, Any],
    client_turn_id: Optional[str],
) -> dict[str, Any]:
    state = _session_turn_snapshot(session_id)
    turns = state["turns"]
    assert isinstance(turns, dict)
    fingerprint = _compute_turn_fingerprint(user_input, force_complex, metadata)
    run_source = _normalize_run_source(metadata.get("source") if isinstance(metadata, dict) else None)
    workspace = _workspace_dir_for_session(session_id)
    run_log_offset_start = _run_log_size(workspace)

    active_turn_id = state.get("active_turn_id")
    if active_turn_id:
        active_record = turns.get(active_turn_id)
        if isinstance(active_record, dict) and active_record.get("status") == TURN_STATUS_RUNNING:
            # Explicit idempotency key repeated while running.
            if client_turn_id and client_turn_id == active_turn_id:
                return {"decision": "duplicate_in_progress", "turn_id": active_turn_id, "record": active_record}
            # Fallback for clients without explicit turn IDs.
            if not client_turn_id and active_record.get("fingerprint") == fingerprint:
                return {"decision": "duplicate_in_progress", "turn_id": active_turn_id, "record": active_record}
            return {"decision": "busy", "turn_id": active_turn_id, "record": active_record}
        state["active_turn_id"] = None

    if not client_turn_id:
        now = datetime.now().timestamp()
        for prior_turn_id in reversed(list(turns.keys())):
            prior_record = turns.get(prior_turn_id)
            if not isinstance(prior_record, dict):
                continue
            if prior_record.get("fingerprint") != fingerprint:
                continue
            prior_status = str(prior_record.get("status"))
            if prior_status == TURN_STATUS_RUNNING:
                return {"decision": "duplicate_in_progress", "turn_id": str(prior_turn_id), "record": prior_record}
            if prior_status == TURN_STATUS_COMPLETED:
                finished = _parse_iso_timestamp(prior_record.get("finished_at") or prior_record.get("started_at"))
                if finished is None:
                    return {"decision": "duplicate_completed", "turn_id": str(prior_turn_id), "record": prior_record}
                if (now - finished.timestamp()) <= TURN_FINGERPRINT_DEDUPE_WINDOW_SECONDS:
                    return {"decision": "duplicate_completed", "turn_id": str(prior_turn_id), "record": prior_record}

    if client_turn_id and client_turn_id in turns:
        record = turns[client_turn_id]
        if isinstance(record, dict):
            status = str(record.get("status", TURN_STATUS_COMPLETED))
            if status == TURN_STATUS_RUNNING:
                return {"decision": "duplicate_in_progress", "turn_id": client_turn_id, "record": record}
            return {"decision": "duplicate_completed", "turn_id": client_turn_id, "record": record}

    turn_id = client_turn_id or f"turn_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    record = {
        "turn_id": turn_id,
        "client_turn_id": client_turn_id,
        "status": TURN_STATUS_RUNNING,
        "started_at": _now_iso(),
        "origin_connection_id": connection_id,
        "fingerprint": fingerprint,
        "run_source": run_source,
        "run_log_offset_start": run_log_offset_start,
        "run_log_offset_end": None,
        "completion": None,
    }
    turns[turn_id] = record
    state["active_turn_id"] = turn_id
    state["last_turn_id"] = turn_id
    _trim_turn_history(state)
    _append_turn_lineage_event(
        session_id,
        turn_id,
        {
            "event": "turn_started",
            "timestamp": _now_iso(),
            "session_id": session_id,
            "turn_id": turn_id,
            "client_turn_id": client_turn_id,
            "run_source": run_source,
            "run_log_offset_start": run_log_offset_start,
            "fingerprint": fingerprint,
            "request_preview": str(user_input or "")[:400],
        },
    )
    return {"decision": "accepted", "turn_id": turn_id, "record": record}


def _finalize_turn(
    session_id: str,
    turn_id: str,
    status: str,
    error_message: Optional[str] = None,
    completion: Optional[dict[str, Any]] = None,
) -> None:
    state = _session_turn_snapshot(session_id)
    turns = state.get("turns", {})
    if not isinstance(turns, dict):
        return
    record = turns.get(turn_id)
    if not isinstance(record, dict):
        return
    workspace = _workspace_dir_for_session(session_id)
    record["status"] = status
    record["finished_at"] = _now_iso()
    record["run_log_offset_end"] = _run_log_size(workspace)
    if completion is not None:
        record["completion"] = completion
    if error_message:
        record["error_message"] = error_message
    if state.get("active_turn_id") == turn_id:
        state["active_turn_id"] = None
    _append_turn_lineage_event(
        session_id,
        turn_id,
        {
            "event": "turn_finalized",
            "timestamp": _now_iso(),
            "session_id": session_id,
            "turn_id": turn_id,
            "status": status,
            "error_message": error_message,
            "run_log_offset_start": int(record.get("run_log_offset_start") or 0),
            "run_log_offset_end": int(record.get("run_log_offset_end") or 0),
            "run_source": record.get("run_source"),
            "completion": completion,
        },
    )


async def _admit_hook_turn(session_id: str, request: GatewayRequest) -> dict[str, Any]:
    if session_id not in _sessions:
        try:
            gateway = get_gateway()
            gateway_sessions = getattr(gateway, "_sessions", {})
            if isinstance(gateway_sessions, dict):
                candidate = gateway_sessions.get(session_id)
                if candidate is not None:
                    store_session(candidate)
                    if _heartbeat_service:
                        _heartbeat_service.register_session(candidate)
        except Exception as exc:
            logger.warning("Failed to sync hook session into gateway state (session=%s): %s", session_id, exc)
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    client_turn_id = _normalize_client_turn_id(
        metadata.get("hook_request_id") or metadata.get("hook_event_id")
    )
    async with _session_turn_lock(session_id):
        return _admit_turn(
            session_id=session_id,
            connection_id="hook_dispatch",
            user_input=str(request.user_input or ""),
            force_complex=bool(request.force_complex),
            metadata=metadata,
            client_turn_id=client_turn_id,
        )


async def _finalize_hook_turn(
    session_id: str,
    turn_id: str,
    status: str,
    error_message: Optional[str],
    completion: Optional[dict[str, Any]],
) -> None:
    async with _session_turn_lock(session_id):
        _finalize_turn(
            session_id=session_id,
            turn_id=turn_id,
            status=status,
            error_message=error_message,
            completion=completion,
        )


def _set_session_connections(session_id: str, count: int) -> None:
    runtime = _session_runtime_snapshot(session_id)
    runtime["active_connections"] = max(0, int(count))
    runtime["last_activity_at"] = _now_iso()
    runtime["lifecycle_state"] = _runtime_status_from_counters(runtime)
    _sync_runtime_metadata(session_id)


def _increment_session_active_runs(session_id: str, run_source: str = "user") -> None:
    runtime = _session_runtime_snapshot(session_id)
    source = _normalize_run_source(run_source)
    now_iso = _now_iso()
    runtime["active_runs"] = int(runtime.get("active_runs", 0)) + 1
    if source != "heartbeat":
        runtime["active_foreground_runs"] = int(runtime.get("active_foreground_runs", 0)) + 1
        runtime["last_foreground_run_started_at"] = now_iso
    runtime["lifecycle_state"] = SESSION_STATE_RUNNING
    runtime["terminal_reason"] = None
    runtime["last_activity_at"] = now_iso
    runtime["last_run_source"] = source
    runtime["last_run_started_at"] = now_iso
    _sync_runtime_metadata(session_id)


def _decrement_session_active_runs(session_id: str, run_source: str = "user") -> None:
    runtime = _session_runtime_snapshot(session_id)
    source = _normalize_run_source(run_source)
    now_iso = _now_iso()
    runtime["active_runs"] = max(0, int(runtime.get("active_runs", 0)) - 1)
    if source != "heartbeat":
        runtime["active_foreground_runs"] = max(0, int(runtime.get("active_foreground_runs", 0)) - 1)
        runtime["last_foreground_run_finished_at"] = now_iso
    runtime["lifecycle_state"] = _runtime_status_from_counters(runtime)
    runtime["last_activity_at"] = now_iso
    runtime["last_run_source"] = source
    runtime["last_run_finished_at"] = now_iso
    _sync_runtime_metadata(session_id)


def _mark_session_terminal(session_id: str, reason: str) -> None:
    runtime = _session_runtime_snapshot(session_id)
    runtime["active_runs"] = 0
    runtime["active_foreground_runs"] = 0
    runtime["active_connections"] = 0
    runtime["lifecycle_state"] = SESSION_STATE_TERMINAL
    runtime["terminal_reason"] = reason
    runtime["last_activity_at"] = _now_iso()
    _sync_runtime_metadata(session_id)


def _emit_cron_event(payload: dict) -> None:
    event_type = str(payload.get("type") or "cron_event")
    _scheduling_record_event("cron", event_type)
    _scheduling_event_bus.publish("cron", event_type, payload)
    _scheduling_counter_inc("event_bus_published")
    run_data = payload.get("run") if isinstance(payload.get("run"), dict) else None
    if event_type == "cron_run_completed" and run_data:
        run_status = str(run_data.get("status") or "unknown").strip().lower() or "unknown"
        job_id = str(run_data.get("job_id") or "").strip()
        run_id = str(run_data.get("run_id") or "").strip()
        job = _cron_service.get_job(job_id) if _cron_service and job_id else None
        command = str(getattr(job, "command", "") or "").strip()
        if not command:
            command = f"job {job_id}" if job_id else "chron job"
        if len(command) > 120:
            command = f"{command[:117]}..."

        session_id = ""
        job_metadata: dict[str, Any] = {}
        if job:
            metadata = getattr(job, "metadata", {}) or {}
            if isinstance(metadata, dict):
                job_metadata = dict(metadata)
                session_id = str(
                    metadata.get("session_id")
                    or metadata.get("target_session_id")
                    or metadata.get("target_session")
                    or ""
                ).strip()
        is_autonomous = bool(job_metadata.get("autonomous"))
        system_job = str(job_metadata.get("system_job") or "").strip()
        is_daily_briefing_job = system_job == AUTONOMOUS_DAILY_BRIEFING_JOB_KEY
        briefing_payload: dict[str, Any] = {}
        if is_daily_briefing_job:
            try:
                briefing_payload = _generate_autonomous_daily_briefing_artifact(
                    now_ts=float(run_data.get("finished_at") or time.time()),
                )
            except Exception:
                logger.exception("Failed generating deterministic autonomous daily briefing artifact")
                briefing_payload = {}

        if run_status == "success" and is_daily_briefing_job and briefing_payload:
            title = "Daily Autonomous Briefing Ready"
            severity = "info"
            kind = "autonomous_daily_briefing_ready"
            message = str(briefing_payload.get("summary_line") or command)
        elif run_status == "success":
            title = "Autonomous Task Completed" if is_autonomous else "Chron Run Succeeded"
            severity = "info"
            kind = "autonomous_run_completed" if is_autonomous else "cron_run_success"
            message = f"{command}"
        else:
            title = "Autonomous Task Failed" if is_autonomous else "Chron Run Failed"
            severity = "error"
            kind = "autonomous_run_failed" if is_autonomous else "cron_run_failed"
            error_text = str(run_data.get("error") or "").strip()
            message = f"{command}"
            if error_text:
                message = f"{message} | {error_text[:240]}"
            if is_daily_briefing_job and briefing_payload:
                message = (
                    f"{message} | fallback briefing summary: "
                    f"{str(briefing_payload.get('summary_line') or '').strip()[:180]}"
                )

        markdown_payload = briefing_payload.get("markdown") if isinstance(briefing_payload, dict) else None
        json_payload = briefing_payload.get("json") if isinstance(briefing_payload, dict) else None

        _add_notification(
            kind=kind,
            title=title,
            message=message,
            session_id=session_id or None,
            severity=severity,
            metadata={
                "job_id": job_id,
                "run_id": run_id,
                "status": run_status,
                "scheduled_at": run_data.get("scheduled_at"),
                "started_at": run_data.get("started_at"),
                "finished_at": run_data.get("finished_at"),
                "error": run_data.get("error"),
                "source": "cron",
                "autonomous": is_autonomous,
                "system_job": system_job,
                "todoist_task_id": str(job_metadata.get("todoist_task_id") or ""),
                "report_api_url": str((markdown_payload or {}).get("api_url") or ""),
                "report_storage_href": str((markdown_payload or {}).get("storage_href") or ""),
                "report_json_api_url": str((json_payload or {}).get("api_url") or ""),
                "report_relative_path": str((markdown_payload or {}).get("relative_path") or ""),
                "report_counts": (
                    dict(briefing_payload.get("counts") or {})
                    if isinstance(briefing_payload.get("counts"), dict)
                    else {}
                ),
            },
        )
    event = {
        "type": event_type,
        "data": payload,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    for session_id in list(manager.session_connections.keys()):
        asyncio.create_task(manager.broadcast(session_id, event))


def _emit_heartbeat_event(payload: dict) -> None:
    event_type = str(payload.get("type") or "heartbeat_event")
    _scheduling_record_event("heartbeat", event_type)
    _scheduling_event_bus.publish("heartbeat", event_type, payload)
    _scheduling_counter_inc("event_bus_published")
    if event_type == "heartbeat_completed":
        ok_only = bool(payload.get("ok_only"))
        if not ok_only:
            session_id = str(payload.get("session_id") or "").strip() or None
            heartbeat_artifact_links = _heartbeat_artifact_links_from_payload(payload)
            _add_notification(
                kind="autonomous_heartbeat_completed",
                title="Autonomous Heartbeat Activity Completed",
                message=f"Heartbeat completed independent work for {session_id or 'session'}.",
                session_id=session_id,
                severity="info",
                metadata={
                    "source": "heartbeat",
                    "session_id": session_id,
                    "timestamp": str(payload.get("timestamp") or ""),
                    "suppressed_reason": str(payload.get("suppressed_reason") or ""),
                    "sent": bool(payload.get("sent")),
                    "guard_reason": str(payload.get("guard_reason") or ""),
                    "guard": payload.get("guard") if isinstance(payload.get("guard"), dict) else {},
                    "heartbeat_artifacts": heartbeat_artifact_links,
                    "heartbeat_artifact_count": len(heartbeat_artifact_links),
                },
            )


def _cron_wake_callback(session_id: str, mode: str, reason: str) -> None:
    if not _heartbeat_service:
        return
    if mode == "next":
        _heartbeat_service.request_heartbeat_next(session_id, reason=reason)
    else:
        _heartbeat_service.request_heartbeat_now(session_id, reason=reason)


def _enqueue_system_event(session_id: str, event: dict) -> None:
    queue = _system_events.setdefault(session_id, [])
    queue.append(event)
    if len(queue) > _system_events_max:
        _system_events[session_id] = queue[-_system_events_max:]


def _drain_system_events(session_id: str) -> list[dict]:
    events = _system_events.get(session_id, [])
    _system_events[session_id] = []
    return events


def _broadcast_system_event(session_id: str, event: dict) -> None:
    payload = {
        "type": "system_event",
        "data": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    asyncio.create_task(manager.broadcast(session_id, payload))


def _vp_event_bridge_prime_cursor_to_latest() -> None:
    global _vp_event_bridge_last_rowid
    gateway = get_gateway()
    conn = getattr(gateway, "get_vp_db_conn", lambda: None)()
    if conn is None:
        return
    persisted_cursor = get_vp_bridge_cursor(conn, _vp_event_bridge_cursor_key)
    if persisted_cursor is None:
        row = conn.execute("SELECT COALESCE(MAX(rowid), 0) AS max_rowid FROM vp_events").fetchone()
        persisted_cursor = int(row["max_rowid"] or 0) if row else 0
        upsert_vp_bridge_cursor(conn, _vp_event_bridge_cursor_key, persisted_cursor)
    _vp_event_bridge_last_rowid = max(0, int(persisted_cursor))


def _vp_event_bridge_snapshot() -> dict[str, Any]:
    gateway = get_gateway()
    conn = getattr(gateway, "get_vp_db_conn", lambda: None)()
    if conn is None:
        return {
            "enabled": _vp_event_bridge_enabled,
            "interval_seconds": _vp_event_bridge_interval_seconds,
            "cursor_key": _vp_event_bridge_cursor_key,
            "db_ready": False,
            "in_memory_cursor": _vp_event_bridge_last_rowid,
            "persisted_cursor": None,
            "max_rowid": None,
            "backlog_rows": None,
            "task_running": bool(_vp_event_bridge_task and not _vp_event_bridge_task.done()),
            "stop_requested": bool(_vp_event_bridge_stop_event and _vp_event_bridge_stop_event.is_set()),
            **_vp_event_bridge_metrics,
        }

    row = conn.execute("SELECT COALESCE(MAX(rowid), 0) AS max_rowid FROM vp_events").fetchone()
    max_rowid = int(row["max_rowid"] or 0) if row else 0
    persisted_cursor = get_vp_bridge_cursor(conn, _vp_event_bridge_cursor_key)
    persisted_value = max(0, int(persisted_cursor or 0))
    in_memory_cursor = max(0, int(_vp_event_bridge_last_rowid))
    effective_cursor = max(in_memory_cursor, persisted_value)

    return {
        "enabled": _vp_event_bridge_enabled,
        "interval_seconds": _vp_event_bridge_interval_seconds,
        "cursor_key": _vp_event_bridge_cursor_key,
        "db_ready": True,
        "in_memory_cursor": in_memory_cursor,
        "persisted_cursor": persisted_value,
        "max_rowid": max_rowid,
        "backlog_rows": max(0, max_rowid - effective_cursor),
        "task_running": bool(_vp_event_bridge_task and not _vp_event_bridge_task.done()),
        "stop_requested": bool(_vp_event_bridge_stop_event and _vp_event_bridge_stop_event.is_set()),
        **_vp_event_bridge_metrics,
    }


def _vp_event_bridge_control_cursor(
    *,
    action: str,
    requested_rowid: Optional[int],
) -> dict[str, Any]:
    global _vp_event_bridge_last_rowid
    gateway = get_gateway()
    conn = getattr(gateway, "get_vp_db_conn", lambda: None)()
    if conn is None:
        raise HTTPException(status_code=503, detail="VP DB not initialized")

    normalized_action = str(action or "").strip().lower()
    row = conn.execute("SELECT COALESCE(MAX(rowid), 0) AS max_rowid FROM vp_events").fetchone()
    max_rowid = int(row["max_rowid"] or 0) if row else 0
    previous_cursor = get_vp_bridge_cursor(conn, _vp_event_bridge_cursor_key)
    previous_value = max(0, int(previous_cursor or 0))

    if normalized_action == "reset_to_latest":
        target_rowid = max_rowid
    elif normalized_action == "reset_to_zero":
        target_rowid = 0
    elif normalized_action == "set":
        if requested_rowid is None:
            raise HTTPException(status_code=400, detail="rowid is required when action='set'")
        parsed = int(requested_rowid)
        if parsed < 0:
            raise HTTPException(status_code=400, detail="rowid must be >= 0")
        target_rowid = min(parsed, max_rowid)
    else:
        raise HTTPException(
            status_code=400,
            detail="Unsupported action. Use: set | reset_to_latest | reset_to_zero",
        )

    upsert_vp_bridge_cursor(conn, _vp_event_bridge_cursor_key, target_rowid)
    _vp_event_bridge_last_rowid = target_rowid
    _vp_event_bridge_metrics["manual_updates"] = int(
        _vp_event_bridge_metrics.get("manual_updates", 0) or 0
    ) + 1
    _vp_event_bridge_metrics["last_manual_update_at"] = datetime.now(timezone.utc).isoformat()
    _vp_event_bridge_metrics["last_error"] = None

    return {
        "action": normalized_action,
        "requested_rowid": requested_rowid,
        "target_rowid": target_rowid,
        "max_rowid": max_rowid,
        "previous_rowid": previous_value,
        "clamped": (
            normalized_action == "set"
            and requested_rowid is not None
            and int(requested_rowid) != target_rowid
        ),
    }


def _vp_source_context(conn: Any, mission_id: str) -> dict[str, Any]:
    mission = get_vp_mission(conn, mission_id)
    if mission is None:
        return {}
    payload = _parse_json_text(mission["payload_json"]) if "payload_json" in mission.keys() else None
    if not isinstance(payload, dict):
        payload = {}
    return {
        "source_session_id": str(payload.get("source_session_id") or "").strip(),
        "source_turn_id": str(payload.get("source_turn_id") or "").strip(),
        "reply_mode": str(payload.get("reply_mode") or "").strip(),
        "mission_status": str(mission["status"] or "").strip(),
        "result_ref": str(mission["result_ref"] or "").strip(),
        "objective": str(mission["objective"] or "").strip(),
    }


def _vp_bridge_event_record(*, event_row: Any, mission_context: dict[str, Any]) -> dict[str, Any]:
    raw_payload = _parse_json_text(event_row["payload_json"]) if "payload_json" in event_row.keys() else None
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    created_at = str(event_row["created_at"] or datetime.now(timezone.utc).isoformat())
    return {
        "event_id": f"evt_vp_{event_row['event_id']}",
        "type": "vp_mission_event",
        "payload": {
            "event_type": str(event_row["event_type"] or ""),
            "mission_id": str(event_row["mission_id"] or ""),
            "vp_id": str(event_row["vp_id"] or ""),
            "source_session_id": mission_context.get("source_session_id"),
            "source_turn_id": mission_context.get("source_turn_id"),
            "reply_mode": mission_context.get("reply_mode"),
            "mission_status": mission_context.get("mission_status"),
            "result_ref": mission_context.get("result_ref"),
            "objective": mission_context.get("objective"),
            "event_payload": payload,
            "event_created_at": created_at,
        },
        "created_at": created_at,
    }


def _bridge_vp_events_once(*, limit: int = 200) -> int:
    global _vp_event_bridge_last_rowid
    _vp_event_bridge_metrics["cycles"] = int(_vp_event_bridge_metrics.get("cycles", 0) or 0) + 1
    _vp_event_bridge_metrics["last_run_at"] = datetime.now(timezone.utc).isoformat()
    _vp_event_bridge_metrics["events_bridged_last"] = 0
    gateway = get_gateway()
    conn = getattr(gateway, "get_vp_db_conn", lambda: None)()
    if conn is None:
        return 0
    rows = conn.execute(
        """
        SELECT rowid AS rowid, *
        FROM vp_events
        WHERE rowid > ?
        ORDER BY rowid ASC
        LIMIT ?
        """,
        (_vp_event_bridge_last_rowid, max(1, min(int(limit), 2000))),
    ).fetchall()
    if not rows:
        return 0

    bridged = 0
    cursor_advanced = False
    for row in rows:
        rowid = int(row["rowid"] or 0)
        mission_id = str(row["mission_id"] or "").strip()
        mission_context = _vp_source_context(conn, mission_id) if mission_id else {}
        if not mission_context.get("source_session_id"):
            fallback_payload = _parse_json_text(row["payload_json"]) if "payload_json" in row.keys() else None
            if isinstance(fallback_payload, dict):
                source_session_id = str(fallback_payload.get("source_session_id") or "").strip()
                source_turn_id = str(fallback_payload.get("source_turn_id") or "").strip()
                if source_session_id:
                    mission_context["source_session_id"] = source_session_id
                if source_turn_id:
                    mission_context["source_turn_id"] = source_turn_id
        source_session_id = str(mission_context.get("source_session_id") or "").strip()
        if source_session_id:
            event = _vp_bridge_event_record(event_row=row, mission_context=mission_context)
            _enqueue_system_event(source_session_id, event)
            if source_session_id in manager.session_connections:
                _broadcast_system_event(source_session_id, event)
            bridged += 1
        next_cursor = max(_vp_event_bridge_last_rowid, rowid)
        if next_cursor != _vp_event_bridge_last_rowid:
            _vp_event_bridge_last_rowid = next_cursor
            cursor_advanced = True
    if cursor_advanced:
        upsert_vp_bridge_cursor(conn, _vp_event_bridge_cursor_key, _vp_event_bridge_last_rowid)
    _vp_event_bridge_metrics["events_bridged_last"] = bridged
    _vp_event_bridge_metrics["events_bridged_total"] = int(
        _vp_event_bridge_metrics.get("events_bridged_total", 0) or 0
    ) + bridged
    if bridged > 0:
        _vp_event_bridge_metrics["last_error"] = None
    return bridged


async def _vp_event_bridge_loop() -> None:
    global _vp_event_bridge_stop_event
    stop_event = _vp_event_bridge_stop_event or asyncio.Event()
    _vp_event_bridge_stop_event = stop_event
    while not stop_event.is_set():
        try:
            _bridge_vp_events_once()
        except Exception as exc:
            logger.warning("VP event bridge iteration failed: %s", exc)
            _vp_event_bridge_metrics["errors"] = int(_vp_event_bridge_metrics.get("errors", 0) or 0) + 1
            _vp_event_bridge_metrics["last_error"] = str(exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_vp_event_bridge_interval_seconds)
        except asyncio.TimeoutError:
            continue


def _activity_json_dumps(value: Any, *, fallback: str = "{}") -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except Exception:
        return fallback


def _activity_json_loads_obj(raw: Any, *, default: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return default
    try:
        parsed = json.loads(raw)
    except Exception:
        return default
    if isinstance(default, dict) and isinstance(parsed, dict):
        return parsed
    if isinstance(default, list) and isinstance(parsed, list):
        return parsed
    return default


def _activity_summary_text(text: str, *, max_chars: int = 240) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= max_chars:
        return compact
    return f"{compact[: max_chars - 3]}..."


def _activity_source_domain(kind: str, metadata: Optional[dict[str, Any]] = None) -> str:
    lowered = str(kind or "").strip().lower()
    metadata = metadata if isinstance(metadata, dict) else {}
    if lowered.startswith("csi"):
        return "csi"
    if lowered.startswith("youtube") or "tutorial" in lowered:
        return "tutorial"
    if lowered.startswith("autonomous") or lowered.startswith("cron"):
        return "cron"
    if lowered.startswith("heartbeat"):
        return "heartbeat"
    if lowered.startswith("continuity"):
        return "continuity"
    if lowered.startswith("system"):
        return "system"
    if str(metadata.get("pipeline") or "").strip().startswith("csi_"):
        return "csi"
    return "system"


def _activity_entity_ref(
    *,
    source_domain: str,
    session_id: Optional[str],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    ref: dict[str, Any] = {}
    if session_id:
        ref["session_id"] = session_id
        ref["session_href"] = f"/dashboard/sessions?session_id={urllib.parse.quote(session_id)}"
    if source_domain == "csi":
        ref["tab"] = "csi"
        ref["route"] = "/dashboard/csi"
        report_key = str(metadata.get("report_key") or "").strip()
        if report_key:
            ref["report_key"] = report_key
            ref["report_href"] = f"/dashboard/csi?report_key={urllib.parse.quote(report_key)}"
        artifact_paths = metadata.get("artifact_paths")
        if isinstance(artifact_paths, dict):
            ref["artifact_paths"] = artifact_paths
            preferred_artifact = str(artifact_paths.get("markdown") or artifact_paths.get("json") or "").strip()
            if preferred_artifact:
                ref["artifact_href"] = f"/dashboard/csi?artifact_path={urllib.parse.quote(preferred_artifact)}"
    elif source_domain == "tutorial":
        ref["tab"] = "tutorials"
        ref["route"] = "/dashboard/tutorials"
    elif source_domain == "cron":
        ref["tab"] = "cron-jobs"
        ref["route"] = "/dashboard/cron-jobs"
    else:
        ref["tab"] = "events"
        ref["route"] = "/dashboard/events"
    return ref


def _activity_actions(
    *,
    source_domain: str,
    entity_ref: dict[str, Any],
    requires_action: bool,
    event_class: str = "notification",
    status: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    metadata = metadata if isinstance(metadata, dict) else {}
    status_norm = str(status or "new").strip().lower()
    is_notification = str(event_class or "").strip().lower() == "notification"
    pinned = bool(metadata.get("pinned"))
    actions: list[dict[str, Any]] = []
    route = str(entity_ref.get("route") or "")
    if route:
        actions.append({"id": "view", "label": "View", "type": "link", "href": route})
    if entity_ref.get("session_href"):
        actions.append({"id": "open_session", "label": "Open Session", "type": "link", "href": entity_ref["session_href"]})
    if source_domain == "csi":
        actions.append({"id": "view_csi", "label": "View in CSI", "type": "link", "href": "/dashboard/csi"})
        report_href = str(entity_ref.get("report_href") or "").strip()
        if report_href:
            actions.append({"id": "open_report", "label": "Open Report", "type": "link", "href": report_href})
        artifact_href = str(entity_ref.get("artifact_href") or "").strip()
        if artifact_href:
            actions.append({"id": "open_artifact", "label": "Open Artifact", "type": "link", "href": artifact_href})
    if requires_action:
        actions.append({"id": "send_to_simone", "label": "Send to Simone", "type": "action"})
    if is_notification:
        if status_norm not in {"read", "acknowledged", "dismissed"}:
            actions.append({"id": "mark_read", "label": "Mark Read", "type": "action"})
        if status_norm == "snoozed":
            actions.append({"id": "unsnooze", "label": "Unsnooze", "type": "action"})
        else:
            actions.append({"id": "snooze", "label": "Snooze 60m", "type": "action"})
    if pinned:
        actions.append({"id": "unpin", "label": "Unpin", "type": "action"})
    else:
        actions.append({"id": "pin", "label": "Pin", "type": "action"})
    return actions


def _merge_activity_actions(*action_sets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action_list in action_sets:
        for action in action_list:
            if not isinstance(action, dict):
                continue
            action_id = str(action.get("id") or "").strip().lower()
            if not action_id or action_id in seen:
                continue
            seen.add(action_id)
            merged.append(action)
    return merged


def _activity_connect() -> sqlite3.Connection:
    conn = connect_runtime_db(get_runtime_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_activity_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS activity_events (
            id TEXT PRIMARY KEY,
            event_class TEXT NOT NULL DEFAULT 'notification',
            source_domain TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            full_message TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            status TEXT NOT NULL DEFAULT 'new',
            requires_action INTEGER NOT NULL DEFAULT 0,
            session_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            entity_ref_json TEXT NOT NULL DEFAULT '{}',
            actions_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            channels_json TEXT NOT NULL DEFAULT '[]',
            email_targets_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS idx_activity_events_created_at ON activity_events(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_activity_events_source_domain ON activity_events(source_domain, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_activity_events_kind ON activity_events(kind, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_activity_events_status ON activity_events(status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_activity_events_requires_action ON activity_events(requires_action, created_at DESC);

        CREATE TABLE IF NOT EXISTS activity_event_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            action TEXT NOT NULL,
            actor TEXT NOT NULL,
            outcome TEXT NOT NULL DEFAULT 'ok',
            note TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_activity_event_audit_event_id ON activity_event_audit(event_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_activity_event_audit_action ON activity_event_audit(action, created_at DESC);

        CREATE TABLE IF NOT EXISTS activity_event_stream (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            op TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_activity_event_stream_created_at ON activity_event_stream(created_at DESC);

        CREATE TABLE IF NOT EXISTS dashboard_event_filter_presets (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            name TEXT NOT NULL,
            filters_json TEXT NOT NULL DEFAULT '{}',
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_used_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_dashboard_event_filter_presets_owner ON dashboard_event_filter_presets(owner_id, updated_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS uq_dashboard_event_filter_presets_owner_name
            ON dashboard_event_filter_presets(owner_id, name);

        CREATE TABLE IF NOT EXISTS csi_specialist_loops (
            topic_key TEXT PRIMARY KEY,
            topic_label TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            confidence_target REAL NOT NULL DEFAULT 0.72,
            confidence_score REAL NOT NULL DEFAULT 0.0,
            follow_up_budget_total INTEGER NOT NULL DEFAULT 3,
            follow_up_budget_remaining INTEGER NOT NULL DEFAULT 3,
            events_count INTEGER NOT NULL DEFAULT 0,
            source_mix_json TEXT NOT NULL DEFAULT '{}',
            last_event_type TEXT,
            last_event_id TEXT,
            last_event_at TEXT,
            last_followup_requested_at TEXT,
            low_signal_streak INTEGER NOT NULL DEFAULT 0,
            suppressed_until TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            closed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_csi_specialist_loops_status ON csi_specialist_loops(status, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_csi_specialist_loops_updated_at ON csi_specialist_loops(updated_at DESC);
        """
    )
    try:
        loop_columns = {
            str(row["name"]): str(row["type"] or "")
            for row in conn.execute("PRAGMA table_info(csi_specialist_loops)").fetchall()
        }
    except Exception:
        loop_columns = {}
    if "confidence_method" not in loop_columns:
        conn.execute(
            "ALTER TABLE csi_specialist_loops ADD COLUMN confidence_method TEXT NOT NULL DEFAULT 'heuristic'"
        )
    if "evidence_json" not in loop_columns:
        conn.execute(
            "ALTER TABLE csi_specialist_loops ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '{}'"
        )
    if "low_signal_streak" not in loop_columns:
        conn.execute(
            "ALTER TABLE csi_specialist_loops ADD COLUMN low_signal_streak INTEGER NOT NULL DEFAULT 0"
        )
    if "suppressed_until" not in loop_columns:
        conn.execute(
            "ALTER TABLE csi_specialist_loops ADD COLUMN suppressed_until TEXT"
        )
    conn.commit()


def _activity_actor_from_request(request: Optional[Request]) -> str:
    if request is None:
        return "dashboard_user"
    for header in ("x-user-id", "x-actor-id", "x-user", "x-forwarded-user"):
        value = str(request.headers.get(header) or "").strip()
        if value:
            return value[:128]
    return "dashboard_user"


def _activity_cursor_encode(created_at_utc: str, event_id: str) -> str:
    payload = {"created_at_utc": str(created_at_utc or ""), "id": str(event_id or "")}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def _activity_cursor_decode(cursor: Optional[str]) -> Optional[tuple[str, str]]:
    value = str(cursor or "").strip()
    if not value:
        return None
    padded = value + "=" * (-len(value) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        payload = json.loads(decoded)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    created = str(payload.get("created_at_utc") or "").strip()
    event_id = str(payload.get("id") or "").strip()
    if not created or not event_id:
        return None
    return created, event_id


def _activity_stream_append(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    op: str,
    payload: dict[str, Any],
) -> int:
    row = conn.execute(
        """
        INSERT INTO activity_event_stream (event_id, op, payload_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            str(event_id or "").strip(),
            str(op or "").strip().lower() or "upsert",
            _activity_json_dumps(payload, fallback="{}"),
            _utc_now_iso(),
        ),
    )
    return int(row.lastrowid or 0)


def _activity_stream_latest_seq(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(seq) AS max_seq FROM activity_event_stream").fetchone()
    if row is None:
        return 0
    return int(row["max_seq"] or 0)


def _activity_stream_event_matches_filters(
    event: dict[str, Any],
    *,
    source_domain: Optional[str] = None,
    kind: Optional[str] = None,
    severity: Optional[str] = None,
    status_value: Optional[str] = None,
    requires_action: Optional[bool] = None,
    pinned: Optional[bool] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> bool:
    if not isinstance(event, dict):
        return False
    if source_domain and str(event.get("source_domain") or "").strip().lower() != str(source_domain).strip().lower():
        return False
    if kind and str(event.get("kind") or "").strip().lower() != str(kind).strip().lower():
        return False
    if severity and str(event.get("severity") or "").strip().lower() != str(severity).strip().lower():
        return False
    if status_value and str(event.get("status") or "").strip().lower() != str(status_value).strip().lower():
        return False
    if requires_action is not None and bool(event.get("requires_action")) != bool(requires_action):
        return False
    if pinned is not None:
        metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        if bool(metadata.get("pinned")) != bool(pinned):
            return False
    event_created = _normalize_notification_timestamp(event.get("created_at_utc") or event.get("created_at"))
    if since and event_created < _normalize_notification_timestamp(since):
        return False
    if until and event_created > _normalize_notification_timestamp(until):
        return False
    return True


def _activity_stream_read(
    *,
    since_seq: int,
    limit: int,
    source_domain: Optional[str] = None,
    kind: Optional[str] = None,
    severity: Optional[str] = None,
    status_value: Optional[str] = None,
    requires_action: Optional[bool] = None,
    pinned: Optional[bool] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> tuple[list[dict[str, Any]], int]:
    conn = _activity_connect()
    try:
        _ensure_activity_schema(conn)
        rows = conn.execute(
            """
            SELECT seq, event_id, op, payload_json, created_at
            FROM activity_event_stream
            WHERE seq > ?
            ORDER BY seq ASC
            LIMIT ?
            """,
            (max(0, int(since_seq)), max(1, min(int(limit), 5000))),
        ).fetchall()
        events: list[dict[str, Any]] = []
        max_seq_seen = max(0, int(since_seq))
        for row in rows:
            seq = int(row["seq"] or 0)
            if seq > max_seq_seen:
                max_seq_seen = seq
            op = str(row["op"] or "upsert").strip().lower() or "upsert"
            payload = _activity_json_loads_obj(row["payload_json"], default={})
            if not isinstance(payload, dict):
                payload = {}
            event_data = payload.get("event") if isinstance(payload.get("event"), dict) else payload
            if op == "upsert" and not _activity_stream_event_matches_filters(
                event_data,
                source_domain=source_domain,
                kind=kind,
                severity=severity,
                status_value=status_value,
                requires_action=requires_action,
                pinned=pinned,
                since=since,
                until=until,
            ):
                continue
            events.append(
                {
                    "seq": seq,
                    "event_id": str(row["event_id"] or ""),
                    "op": op,
                    "event": event_data,
                    "created_at_utc": _normalize_notification_timestamp(row["created_at"]),
                }
            )
        return events, max_seq_seen
    finally:
        conn.close()


def _dashboard_owner_from_request(request: Optional[Request]) -> str:
    if request is None:
        return "owner_primary"
    for header in ("x-ua-dashboard-owner", "x-user-id", "x-actor-id", "x-user"):
        value = str(request.headers.get(header) or "").strip()
        if value:
            return value[:128]
    return "owner_primary"


def _normalize_event_preset_filters(filters: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(filters, dict):
        return {}
    allowed = {
        "source_domain",
        "severity",
        "status",
        "kind",
        "time_window",
        "actionable_only",
        "pinned_only",
        "since",
        "until",
    }
    out: dict[str, Any] = {}
    for key, value in filters.items():
        normalized_key = str(key or "").strip()
        if normalized_key not in allowed:
            continue
        if isinstance(value, bool):
            out[normalized_key] = value
        elif value is None:
            out[normalized_key] = None
        else:
            out[normalized_key] = str(value)
    return out


def _list_event_filter_presets(owner_id: str) -> list[dict[str, Any]]:
    conn = _activity_connect()
    try:
        _ensure_activity_schema(conn)
        rows = conn.execute(
            """
            SELECT id, owner_id, name, filters_json, is_default, created_at, updated_at, last_used_at
            FROM dashboard_event_filter_presets
            WHERE owner_id = ?
            ORDER BY is_default DESC, updated_at DESC, name ASC
            """,
            (str(owner_id or "").strip(),),
        ).fetchall()
        presets: list[dict[str, Any]] = []
        for row in rows:
            presets.append(
                {
                    "id": str(row["id"] or ""),
                    "owner_id": str(row["owner_id"] or ""),
                    "name": str(row["name"] or ""),
                    "filters": _activity_json_loads_obj(row["filters_json"], default={}),
                    "is_default": bool(int(row["is_default"] or 0)),
                    "created_at_utc": _normalize_notification_timestamp(row["created_at"]),
                    "updated_at_utc": _normalize_notification_timestamp(row["updated_at"]),
                    "last_used_at_utc": _normalize_notification_timestamp(row["last_used_at"]) if row["last_used_at"] else None,
                }
            )
        return presets
    finally:
        conn.close()


def _create_event_filter_preset(
    *,
    owner_id: str,
    name: str,
    filters: dict[str, Any],
    is_default: bool,
) -> dict[str, Any]:
    owner = str(owner_id or "").strip() or "owner_primary"
    preset_name = str(name or "").strip()
    if not preset_name:
        raise ValueError("name is required")
    normalized_filters = _normalize_event_preset_filters(filters)
    now_iso = _utc_now_iso()
    preset_id = f"evt_preset_{uuid.uuid4().hex}"
    with _activity_store_lock:
        conn = _activity_connect()
        try:
            _ensure_activity_schema(conn)
            if is_default:
                conn.execute(
                    "UPDATE dashboard_event_filter_presets SET is_default = 0 WHERE owner_id = ?",
                    (owner,),
                )
            conn.execute(
                """
                INSERT INTO dashboard_event_filter_presets (
                    id, owner_id, name, filters_json, is_default, created_at, updated_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    preset_id,
                    owner,
                    preset_name,
                    _activity_json_dumps(normalized_filters, fallback="{}"),
                    1 if is_default else 0,
                    now_iso,
                    now_iso,
                    now_iso,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            if "uq_dashboard_event_filter_presets_owner_name" in str(exc).lower():
                raise ValueError(f"Preset '{preset_name}' already exists") from exc
            raise
        finally:
            conn.close()
    rows = _list_event_filter_presets(owner)
    for row in rows:
        if str(row.get("id") or "") == preset_id:
            return row
    return {
        "id": preset_id,
        "owner_id": owner,
        "name": preset_name,
        "filters": normalized_filters,
        "is_default": bool(is_default),
        "created_at_utc": now_iso,
        "updated_at_utc": now_iso,
        "last_used_at_utc": now_iso,
    }


def _update_event_filter_preset(
    *,
    owner_id: str,
    preset_id: str,
    name: Optional[str],
    filters: Optional[dict[str, Any]],
    is_default: Optional[bool],
    mark_used: bool = False,
) -> Optional[dict[str, Any]]:
    owner = str(owner_id or "").strip() or "owner_primary"
    target_id = str(preset_id or "").strip()
    if not target_id:
        return None
    with _activity_store_lock:
        conn = _activity_connect()
        try:
            _ensure_activity_schema(conn)
            row = conn.execute(
                """
                SELECT id, owner_id, name, filters_json, is_default, created_at, updated_at, last_used_at
                FROM dashboard_event_filter_presets
                WHERE id = ? AND owner_id = ?
                LIMIT 1
                """,
                (target_id, owner),
            ).fetchone()
            if row is None:
                return None
            next_name = str(name).strip() if name is not None else str(row["name"] or "")
            if not next_name:
                raise ValueError("name cannot be empty")
            current_filters = _activity_json_loads_obj(row["filters_json"], default={})
            next_filters = _normalize_event_preset_filters(filters if filters is not None else current_filters)
            next_default = bool(is_default) if is_default is not None else bool(int(row["is_default"] or 0))
            now_iso = _utc_now_iso()
            if next_default:
                conn.execute(
                    "UPDATE dashboard_event_filter_presets SET is_default = 0 WHERE owner_id = ? AND id != ?",
                    (owner, target_id),
                )
            conn.execute(
                """
                UPDATE dashboard_event_filter_presets
                SET name = ?, filters_json = ?, is_default = ?, updated_at = ?, last_used_at = ?
                WHERE id = ? AND owner_id = ?
                """,
                (
                    next_name,
                    _activity_json_dumps(next_filters, fallback="{}"),
                    1 if next_default else 0,
                    now_iso,
                    now_iso if mark_used else row["last_used_at"],
                    target_id,
                    owner,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            if "uq_dashboard_event_filter_presets_owner_name" in str(exc).lower():
                raise ValueError(f"Preset '{str(name or '').strip()}' already exists") from exc
            raise
        finally:
            conn.close()
    rows = _list_event_filter_presets(owner)
    for item in rows:
        if str(item.get("id") or "") == target_id:
            return item
    return None


def _delete_event_filter_preset(*, owner_id: str, preset_id: str) -> bool:
    owner = str(owner_id or "").strip() or "owner_primary"
    target_id = str(preset_id or "").strip()
    if not target_id:
        return False
    with _activity_store_lock:
        conn = _activity_connect()
        try:
            _ensure_activity_schema(conn)
            result = conn.execute(
                "DELETE FROM dashboard_event_filter_presets WHERE id = ? AND owner_id = ?",
                (target_id, owner),
            )
            conn.commit()
            return int(result.rowcount or 0) > 0
        finally:
            conn.close()


def _activity_prune_old(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM activity_events WHERE created_at < datetime('now', ?)",
        (f"-{int(_activity_events_retention_days)} days",),
    )
    conn.execute(
        "DELETE FROM activity_event_stream WHERE created_at < datetime('now', ?)",
        (f"-{int(_activity_stream_retention_days)} days",),
    )
    conn.commit()


def _activity_upsert_record(record: dict[str, Any]) -> None:
    with _activity_store_lock:
        conn = _activity_connect()
        try:
            _ensure_activity_schema(conn)
            conn.execute(
                """
                INSERT INTO activity_events (
                    id, event_class, source_domain, kind, title, summary, full_message, severity,
                    status, requires_action, session_id, created_at, updated_at, entity_ref_json,
                    actions_json, metadata_json, channels_json, email_targets_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    event_class=excluded.event_class,
                    source_domain=excluded.source_domain,
                    kind=excluded.kind,
                    title=excluded.title,
                    summary=excluded.summary,
                    full_message=excluded.full_message,
                    severity=excluded.severity,
                    status=excluded.status,
                    requires_action=excluded.requires_action,
                    session_id=excluded.session_id,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at,
                    entity_ref_json=excluded.entity_ref_json,
                    actions_json=excluded.actions_json,
                    metadata_json=excluded.metadata_json,
                    channels_json=excluded.channels_json,
                    email_targets_json=excluded.email_targets_json
                """,
                (
                    str(record.get("id") or ""),
                    str(record.get("event_class") or "notification"),
                    str(record.get("source_domain") or "system"),
                    str(record.get("kind") or "event"),
                    str(record.get("title") or "Event"),
                    str(record.get("summary") or ""),
                    str(record.get("full_message") or ""),
                    str(record.get("severity") or "info"),
                    str(record.get("status") or "new"),
                    1 if bool(record.get("requires_action")) else 0,
                    str(record.get("session_id") or "") or None,
                    _normalize_notification_timestamp(record.get("created_at")),
                    _normalize_notification_timestamp(record.get("updated_at")),
                    _activity_json_dumps(record.get("entity_ref") or {}, fallback="{}"),
                    _activity_json_dumps(record.get("actions") or [], fallback="[]"),
                    _activity_json_dumps(record.get("metadata") or {}, fallback="{}"),
                    _activity_json_dumps(record.get("channels") or [], fallback="[]"),
                    _activity_json_dumps(record.get("email_targets") or [], fallback="[]"),
                ),
            )
            row = conn.execute(
                "SELECT * FROM activity_events WHERE id = ? LIMIT 1",
                (str(record.get("id") or ""),),
            ).fetchone()
            if row is not None:
                _activity_stream_append(
                    conn,
                    event_id=str(row["id"] or ""),
                    op="upsert",
                    payload={"event": _activity_row_to_dashboard_event(row)},
                )
            _activity_prune_old(conn)
        finally:
            conn.close()


def _activity_record_from_notification(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    created_at = _normalize_notification_timestamp(record.get("created_at"))
    updated_at = _normalize_notification_timestamp(record.get("updated_at") or created_at)
    summary = str(record.get("summary") or "").strip() or _activity_summary_text(str(record.get("message") or ""))
    full_message = str(record.get("full_message") or record.get("message") or "")
    source_domain = _activity_source_domain(str(record.get("kind") or ""), metadata)
    entity_ref = _activity_entity_ref(
        source_domain=source_domain,
        session_id=str(record.get("session_id") or "").strip() or None,
        metadata=metadata,
    )
    actions = _activity_actions(
        source_domain=source_domain,
        entity_ref=entity_ref,
        requires_action=bool(record.get("requires_action")),
        event_class="notification",
        status=str(record.get("status") or "new"),
        metadata=metadata,
    )
    return {
        "id": str(record.get("id") or ""),
        "event_class": "notification",
        "source_domain": source_domain,
        "kind": str(record.get("kind") or "notification"),
        "title": str(record.get("title") or "Notification"),
        "summary": summary,
        "full_message": full_message,
        "severity": str(record.get("severity") or "info"),
        "status": str(record.get("status") or "new"),
        "requires_action": bool(record.get("requires_action")),
        "session_id": str(record.get("session_id") or "") or None,
        "created_at": created_at,
        "updated_at": updated_at,
        "entity_ref": entity_ref,
        "actions": actions,
        "metadata": metadata,
        "channels": list(record.get("channels") or []),
        "email_targets": list(record.get("email_targets") or []),
    }


def _persist_notification_activity(record: dict[str, Any]) -> None:
    try:
        _activity_upsert_record(_activity_record_from_notification(record))
    except Exception as exc:
        logger.debug("Failed persisting notification activity record: %s", exc)


def _activity_row_to_notification(row: sqlite3.Row) -> dict[str, Any]:
    metadata = _activity_json_loads_obj(row["metadata_json"], default={})
    return {
        "id": str(row["id"] or ""),
        "kind": str(row["kind"] or ""),
        "title": str(row["title"] or ""),
        "message": str(row["full_message"] or ""),
        "summary": str(row["summary"] or ""),
        "full_message": str(row["full_message"] or ""),
        "session_id": str(row["session_id"] or "") or None,
        "severity": str(row["severity"] or "info"),
        "requires_action": bool(int(row["requires_action"] or 0)),
        "status": str(row["status"] or "new"),
        "created_at": _normalize_notification_timestamp(row["created_at"]),
        "updated_at": _normalize_notification_timestamp(row["updated_at"]),
        "channels": _activity_json_loads_obj(row["channels_json"], default=[]),
        "email_targets": _activity_json_loads_obj(row["email_targets_json"], default=[]),
        "metadata": metadata if isinstance(metadata, dict) else {},
    }


def _load_notifications_from_activity_store(limit: int) -> list[dict[str, Any]]:
    conn = _activity_connect()
    try:
        _ensure_activity_schema(conn)
        rows = conn.execute(
            """
            SELECT *
            FROM activity_events
            WHERE event_class = 'notification'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [_activity_row_to_notification(row) for row in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _activity_row_to_dashboard_event(row: sqlite3.Row) -> dict[str, Any]:
    event_class = str(row["event_class"] or "notification")
    metadata = _activity_json_loads_obj(row["metadata_json"], default={})
    if not isinstance(metadata, dict):
        metadata = {}
    entity_ref = _activity_json_loads_obj(row["entity_ref_json"], default={})
    if not isinstance(entity_ref, dict):
        entity_ref = {}
    stored_actions = _activity_json_loads_obj(row["actions_json"], default=[])
    if not isinstance(stored_actions, list):
        stored_actions = []
    computed_actions = _activity_actions(
        source_domain=str(row["source_domain"] or "system"),
        entity_ref=entity_ref,
        requires_action=bool(int(row["requires_action"] or 0)),
        event_class=event_class,
        status=str(row["status"] or "new"),
        metadata=metadata,
    )
    return {
        "id": str(row["id"] or ""),
        "event_class": event_class,
        "source_domain": str(row["source_domain"] or "system"),
        "kind": str(row["kind"] or ""),
        "title": str(row["title"] or ""),
        "summary": str(row["summary"] or ""),
        "full_message": str(row["full_message"] or ""),
        "severity": str(row["severity"] or "info"),
        "status": str(row["status"] or "new"),
        "requires_action": bool(int(row["requires_action"] or 0)),
        "session_id": str(row["session_id"] or "") or None,
        "created_at_utc": _normalize_notification_timestamp(row["created_at"]),
        "updated_at_utc": _normalize_notification_timestamp(row["updated_at"]),
        "entity_ref": entity_ref,
        "actions": _merge_activity_actions(stored_actions, computed_actions),
        "metadata": metadata,
    }


def _persist_system_activity_event(event: dict[str, Any], *, session_id: Optional[str]) -> None:
    metadata = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    created_at = _normalize_notification_timestamp(event.get("created_at"))
    source_domain = "system"
    kind = str(event.get("type") or "system_event")
    title = f"System Event: {kind}"
    summary = _activity_summary_text(_activity_json_dumps(metadata, fallback="{}"), max_chars=220)
    event_id = str(event.get("event_id") or f"evt_{int(time.time() * 1000)}")
    record = {
        "id": f"activity_{event_id}_{session_id or 'all'}",
        "event_class": "system_event",
        "source_domain": source_domain,
        "kind": kind,
        "title": title,
        "summary": summary,
        "full_message": _activity_json_dumps(metadata, fallback="{}"),
        "severity": "info",
        "status": "new",
        "requires_action": False,
        "session_id": session_id,
        "created_at": created_at,
        "updated_at": created_at,
        "entity_ref": _activity_entity_ref(
            source_domain=source_domain,
            session_id=session_id,
            metadata=metadata if isinstance(metadata, dict) else {},
        ),
        "actions": _activity_actions(
            source_domain=source_domain,
            entity_ref=_activity_entity_ref(
                source_domain=source_domain,
                session_id=session_id,
                metadata=metadata if isinstance(metadata, dict) else {},
            ),
            requires_action=False,
            event_class="system_event",
            status="new",
            metadata=metadata if isinstance(metadata, dict) else {},
        ),
        "metadata": metadata if isinstance(metadata, dict) else {},
        "channels": ["dashboard"],
        "email_targets": [],
    }
    try:
        _activity_upsert_record(record)
    except Exception as exc:
        logger.debug("Failed persisting system activity event: %s", exc)


def _query_activity_events(
    *,
    limit: int,
    source_domain: Optional[str] = None,
    kind: Optional[str] = None,
    severity: Optional[str] = None,
    status_value: Optional[str] = None,
    requires_action: Optional[bool] = None,
    pinned: Optional[bool] = None,
    cursor: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    apply_default_window: bool = True,
) -> list[dict[str, Any]]:
    conn = _activity_connect()
    try:
        _ensure_activity_schema(conn)
        where: list[str] = []
        params: list[Any] = []
        if source_domain:
            where.append("LOWER(source_domain) = ?")
            params.append(source_domain.strip().lower())
        if kind:
            where.append("LOWER(kind) = ?")
            params.append(kind.strip().lower())
        if severity:
            where.append("LOWER(severity) = ?")
            params.append(severity.strip().lower())
        if status_value:
            where.append("LOWER(status) = ?")
            params.append(status_value.strip().lower())
        if requires_action is not None:
            where.append("requires_action = ?")
            params.append(1 if requires_action else 0)
        cursor_parts = _activity_cursor_decode(cursor)
        if cursor_parts is not None:
            cursor_created, cursor_id = cursor_parts
            where.append("(created_at < ? OR (created_at = ? AND id < ?))")
            params.extend([cursor_created, cursor_created, cursor_id])
        if since:
            where.append("created_at >= ?")
            params.append(_normalize_notification_timestamp(since))
        if until:
            where.append("created_at <= ?")
            params.append(_normalize_notification_timestamp(until))
        if apply_default_window and since is None and until is None:
            where.append("created_at >= datetime('now', ?)")
            params.append(f"-{int(_activity_events_default_window_days)} days")
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        rows = conn.execute(
            f"""
            SELECT *
            FROM activity_events
            {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (*params, max(1, min(int(limit), 1000))),
        ).fetchall()
        items = [_activity_row_to_dashboard_event(row) for row in rows]
        if pinned is not None:
            items = [
                item for item in items
                if bool((item.get("metadata") or {}).get("pinned")) == bool(pinned)
            ]
        return items
    finally:
        conn.close()


def _query_activity_event_counters(
    *,
    event_class: Optional[str] = None,
    source_domain: Optional[str] = None,
    kind: Optional[str] = None,
    severity: Optional[str] = None,
    status_value: Optional[str] = None,
    pinned: Optional[bool] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    apply_default_window: bool = True,
) -> dict[str, Any]:
    conn = _activity_connect()
    try:
        _ensure_activity_schema(conn)
        where: list[str] = []
        params: list[Any] = []
        if source_domain:
            where.append("LOWER(source_domain) = ?")
            params.append(source_domain.strip().lower())
        if event_class:
            where.append("LOWER(event_class) = ?")
            params.append(event_class.strip().lower())
        if kind:
            where.append("LOWER(kind) = ?")
            params.append(kind.strip().lower())
        if severity:
            where.append("LOWER(severity) = ?")
            params.append(severity.strip().lower())
        if status_value:
            where.append("LOWER(status) = ?")
            params.append(status_value.strip().lower())
        if since:
            where.append("created_at >= ?")
            params.append(_normalize_notification_timestamp(since))
        if until:
            where.append("created_at <= ?")
            params.append(_normalize_notification_timestamp(until))
        if apply_default_window and since is None and until is None:
            where.append("created_at >= datetime('now', ?)")
            params.append(f"-{int(_activity_events_default_window_days)} days")
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        rows = conn.execute(
            f"""
            SELECT source_domain, status, requires_action, metadata_json
            FROM activity_events
            {where_sql}
            """,
            tuple(params),
        ).fetchall()
        known_sources = ["csi", "tutorial", "cron", "continuity", "heartbeat", "system"]
        by_source: dict[str, dict[str, int]] = {
            key: {"unread": 0, "actionable": 0, "total": 0}
            for key in known_sources
        }
        totals = {"unread": 0, "actionable": 0, "total": 0}
        for row in rows:
            metadata = _activity_json_loads_obj(row["metadata_json"], default={})
            if not isinstance(metadata, dict):
                metadata = {}
            if pinned is not None and bool(metadata.get("pinned")) != bool(pinned):
                continue
            source_key = str(row["source_domain"] or "system").strip().lower() or "system"
            if source_key not in by_source:
                by_source[source_key] = {"unread": 0, "actionable": 0, "total": 0}
            status_norm = str(row["status"] or "new").strip().lower()
            actionable = bool(int(row["requires_action"] or 0))
            unread = status_norm in {"new", "pending"}
            by_source[source_key]["total"] += 1
            totals["total"] += 1
            if actionable:
                by_source[source_key]["actionable"] += 1
                totals["actionable"] += 1
            if unread:
                by_source[source_key]["unread"] += 1
                totals["unread"] += 1
        return {"totals": totals, "by_source": by_source}
    finally:
        conn.close()


def _get_activity_event(activity_id: str) -> Optional[dict[str, Any]]:
    conn = _activity_connect()
    try:
        _ensure_activity_schema(conn)
        row = conn.execute(
            "SELECT * FROM activity_events WHERE id = ? LIMIT 1",
            (str(activity_id),),
        ).fetchone()
        if row is None:
            return None
        return _activity_row_to_dashboard_event(row)
    finally:
        conn.close()


def _list_csi_specialist_loops(limit: int = 50, status_filter: Optional[str] = None) -> list[dict[str, Any]]:
    conn = _activity_connect()
    try:
        _ensure_activity_schema(conn)
        where = ""
        params: list[Any] = []
        if status_filter:
            where = "WHERE LOWER(status) = ?"
            params.append(str(status_filter).strip().lower())
        rows = conn.execute(
            f"""
            SELECT *
            FROM csi_specialist_loops
            {where}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (*params, max(1, min(int(limit), 500))),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            evidence = _activity_json_loads_obj(row["evidence_json"], default={})
            if not isinstance(evidence, dict):
                evidence = {}
            out.append(
                {
                    "topic_key": str(row["topic_key"] or ""),
                    "topic_label": str(row["topic_label"] or ""),
                    "status": str(row["status"] or "open"),
                    "confidence_target": float(row["confidence_target"] or 0.0),
                    "confidence_score": float(row["confidence_score"] or 0.0),
                    "confidence_method": str(row["confidence_method"] or "heuristic"),
                    "confidence_evidence": evidence,
                    "follow_up_budget_total": int(row["follow_up_budget_total"] or 0),
                    "follow_up_budget_remaining": int(row["follow_up_budget_remaining"] or 0),
                    "events_count": int(row["events_count"] or 0),
                    "source_mix": _csi_parse_mix_json(row["source_mix_json"]),
                    "last_event_type": str(row["last_event_type"] or ""),
                    "last_event_id": str(row["last_event_id"] or ""),
                    "last_event_at": str(row["last_event_at"] or "") or None,
                    "last_followup_requested_at": str(row["last_followup_requested_at"] or "") or None,
                    "low_signal_streak": int(row["low_signal_streak"] or 0),
                    "suppressed_until": str(row["suppressed_until"] or "") or None,
                    "created_at": str(row["created_at"] or _utc_now_iso()),
                    "updated_at": str(row["updated_at"] or _utc_now_iso()),
                    "closed_at": str(row["closed_at"] or "") or None,
                }
            )
        return out
    finally:
        conn.close()


def _get_csi_specialist_loop(topic_key: str) -> Optional[dict[str, Any]]:
    cleaned_topic_key = str(topic_key or "").strip()
    if not cleaned_topic_key:
        return None
    conn = _activity_connect()
    try:
        _ensure_activity_schema(conn)
        row = conn.execute(
            "SELECT * FROM csi_specialist_loops WHERE topic_key = ? LIMIT 1",
            (cleaned_topic_key,),
        ).fetchone()
        if row is None:
            return None
        evidence = _activity_json_loads_obj(row["evidence_json"], default={})
        if not isinstance(evidence, dict):
            evidence = {}
        return {
            "topic_key": str(row["topic_key"] or ""),
            "topic_label": str(row["topic_label"] or ""),
            "status": str(row["status"] or "open"),
            "confidence_target": float(row["confidence_target"] or 0.0),
            "confidence_score": float(row["confidence_score"] or 0.0),
            "confidence_method": str(row["confidence_method"] or "heuristic"),
            "confidence_evidence": evidence,
            "follow_up_budget_total": int(row["follow_up_budget_total"] or 0),
            "follow_up_budget_remaining": int(row["follow_up_budget_remaining"] or 0),
            "events_count": int(row["events_count"] or 0),
            "source_mix": _csi_parse_mix_json(row["source_mix_json"]),
            "last_event_type": str(row["last_event_type"] or ""),
            "last_event_id": str(row["last_event_id"] or ""),
            "last_event_at": str(row["last_event_at"] or "") or None,
            "last_followup_requested_at": str(row["last_followup_requested_at"] or "") or None,
            "low_signal_streak": int(row["low_signal_streak"] or 0),
            "suppressed_until": str(row["suppressed_until"] or "") or None,
            "created_at": str(row["created_at"] or _utc_now_iso()),
            "updated_at": str(row["updated_at"] or _utc_now_iso()),
            "closed_at": str(row["closed_at"] or "") or None,
        }
    finally:
        conn.close()


def _csi_loop_audit_event_id(topic_key: str) -> str:
    cleaned = str(topic_key or "").strip()
    if not cleaned:
        return "csi_loop:unknown"
    return f"csi_loop:{cleaned}"


def _csi_loop_status_from_state(
    *,
    confidence_score: float,
    confidence_target: float,
    follow_up_budget_remaining: int,
    suppressed_until: Optional[str],
) -> str:
    if float(confidence_score) >= float(confidence_target):
        return "closed"
    suppressed_text = str(suppressed_until or "").strip()
    if suppressed_text:
        suppressed_dt = _parse_iso_timestamp(suppressed_text)
        if suppressed_dt is not None:
            if suppressed_dt.tzinfo is None:
                suppressed_dt = suppressed_dt.replace(tzinfo=timezone.utc)
            if suppressed_dt.timestamp() > time.time():
                return "suppressed_low_signal"
    if int(follow_up_budget_remaining) <= 0:
        return "budget_exhausted"
    return "open"


def _csi_specialist_followup_message(
    *,
    loop: dict[str, Any],
    trigger: str,
    note: Optional[str] = None,
) -> str:
    return (
        "CSI specialist follow-up request.\n"
        f"topic_key: {loop.get('topic_key')}\n"
        f"topic_label: {loop.get('topic_label')}\n"
        f"status: {loop.get('status')}\n"
        f"trigger: {trigger}\n"
        f"confidence_score: {loop.get('confidence_score')}\n"
        f"confidence_target: {loop.get('confidence_target')}\n"
        f"confidence_method: {loop.get('confidence_method')}\n"
        f"follow_up_budget_remaining: {loop.get('follow_up_budget_remaining')}\n"
        f"events_count: {loop.get('events_count')}\n"
        f"last_event_type: {loop.get('last_event_type')}\n"
        f"last_event_id: {loop.get('last_event_id')}\n"
        f"last_event_at: {loop.get('last_event_at')}\n"
        f"operator_note: {str(note or '').strip()}\n"
        "Request one focused follow-up CSI analysis task and summarize confidence deltas."
    )


async def _csi_dispatch_specialist_followup(
    *,
    loop: dict[str, Any],
    trigger: str,
    note: Optional[str] = None,
) -> tuple[bool, str]:
    if not _hooks_service:
        return False, "Hooks service not initialized"
    message = _csi_specialist_followup_message(loop=loop, trigger=trigger, note=note)
    ok, reason = await _hooks_service.dispatch_internal_action(
        {
            "kind": "agent",
            "name": "CSITrendFollowUpRequest",
            "session_key": "csi_trend_specialist",
            "to": "trend-specialist",
            "message": message,
            "timeout_seconds": int(max(60, _env_int("UA_CSI_ANALYTICS_HOOK_TIMEOUT_SECONDS", 420))),
        }
    )
    return bool(ok), str(reason or "")


def _apply_csi_specialist_loop_action(
    *,
    topic_key: str,
    action: str,
    actor: str,
    note: Optional[str] = None,
    follow_up_budget: Optional[int] = None,
) -> dict[str, Any]:
    cleaned_topic_key = str(topic_key or "").strip()
    cleaned_action = str(action or "").strip().lower()
    if not cleaned_topic_key:
        raise HTTPException(status_code=400, detail="topic_key is required")
    if cleaned_action not in {"unsuppress", "reset_budget", "reopen", "close"}:
        raise HTTPException(status_code=400, detail=f"Unsupported action '{cleaned_action}'")

    now_iso = _utc_now_iso()
    with _csi_specialist_loop_lock:
        conn = _activity_connect()
        try:
            _ensure_activity_schema(conn)
            row = conn.execute(
                "SELECT * FROM csi_specialist_loops WHERE topic_key = ? LIMIT 1",
                (cleaned_topic_key,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Specialist loop not found")

            confidence_target = float(row["confidence_target"] or _csi_specialist_confidence_target)
            confidence_score = float(row["confidence_score"] or 0.0)
            follow_up_budget_total = int(row["follow_up_budget_total"] or _csi_specialist_followup_budget)
            follow_up_budget_remaining = int(row["follow_up_budget_remaining"] or 0)
            low_signal_streak = int(row["low_signal_streak"] or 0)
            suppressed_until = str(row["suppressed_until"] or "") or None
            status_value = str(row["status"] or "open")
            closed_at = str(row["closed_at"] or "") or None

            if cleaned_action == "unsuppress":
                low_signal_streak = 0
                suppressed_until = None
                closed_at = None
                status_value = _csi_loop_status_from_state(
                    confidence_score=confidence_score,
                    confidence_target=confidence_target,
                    follow_up_budget_remaining=follow_up_budget_remaining,
                    suppressed_until=suppressed_until,
                )
            elif cleaned_action == "reset_budget":
                target_budget = int(follow_up_budget or 0)
                if target_budget <= 0:
                    target_budget = int(_csi_specialist_followup_budget)
                follow_up_budget_total = max(1, target_budget)
                follow_up_budget_remaining = max(1, follow_up_budget_total)
                closed_at = None
                status_value = _csi_loop_status_from_state(
                    confidence_score=confidence_score,
                    confidence_target=confidence_target,
                    follow_up_budget_remaining=follow_up_budget_remaining,
                    suppressed_until=suppressed_until,
                )
            elif cleaned_action == "reopen":
                closed_at = None
                status_value = "open"
                if follow_up_budget_remaining <= 0:
                    follow_up_budget_remaining = max(1, follow_up_budget_total)
            elif cleaned_action == "close":
                status_value = "closed"
                closed_at = now_iso
                low_signal_streak = 0
                suppressed_until = None

            conn.execute(
                """
                UPDATE csi_specialist_loops
                SET status = ?,
                    follow_up_budget_total = ?,
                    follow_up_budget_remaining = ?,
                    low_signal_streak = ?,
                    suppressed_until = ?,
                    updated_at = ?,
                    closed_at = ?
                WHERE topic_key = ?
                """,
                (
                    status_value,
                    int(max(1, follow_up_budget_total)),
                    int(max(0, follow_up_budget_remaining)),
                    int(max(0, low_signal_streak)),
                    suppressed_until,
                    now_iso,
                    closed_at,
                    cleaned_topic_key,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    updated = _get_csi_specialist_loop(cleaned_topic_key)
    if updated is None:
        raise HTTPException(status_code=404, detail="Specialist loop not found")
    _record_activity_audit(
        event_id=_csi_loop_audit_event_id(cleaned_topic_key),
        action=f"csi_loop_{cleaned_action}",
        actor=actor,
        outcome="ok",
        note=note,
        metadata={
            "topic_key": cleaned_topic_key,
            "status": updated.get("status"),
            "follow_up_budget_remaining": updated.get("follow_up_budget_remaining"),
        },
    )
    return updated


async def _csi_operator_request_followup(
    *,
    topic_key: str,
    actor: str,
    note: Optional[str] = None,
    trigger: str = "operator_manual",
) -> dict[str, Any]:
    cleaned_topic_key = str(topic_key or "").strip()
    loop = _get_csi_specialist_loop(cleaned_topic_key)
    if loop is None:
        raise HTTPException(status_code=404, detail="Specialist loop not found")
    if str(loop.get("status") or "").strip().lower() == "closed":
        raise HTTPException(status_code=400, detail="Cannot request follow-up for a closed loop")
    if int(loop.get("follow_up_budget_remaining") or 0) <= 0:
        raise HTTPException(status_code=400, detail="Follow-up budget exhausted for this loop")

    ok, reason = await _csi_dispatch_specialist_followup(loop=loop, trigger=trigger, note=note)
    if not ok:
        _record_activity_audit(
            event_id=_csi_loop_audit_event_id(cleaned_topic_key),
            action="csi_loop_request_followup",
            actor=actor,
            outcome="failed",
            note=note,
            metadata={
                "topic_key": cleaned_topic_key,
                "reason": reason,
            },
        )
        return {"ok": False, "reason": reason, "loop": loop}

    now_iso = _utc_now_iso()
    with _csi_specialist_loop_lock:
        conn = _activity_connect()
        try:
            _ensure_activity_schema(conn)
            row = conn.execute(
                "SELECT * FROM csi_specialist_loops WHERE topic_key = ? LIMIT 1",
                (cleaned_topic_key,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Specialist loop not found")
            remaining = max(0, int(row["follow_up_budget_remaining"] or 0) - 1)
            confidence_score = float(row["confidence_score"] or 0.0)
            confidence_target = float(row["confidence_target"] or _csi_specialist_confidence_target)
            suppressed_until = str(row["suppressed_until"] or "") or None
            status_value = _csi_loop_status_from_state(
                confidence_score=confidence_score,
                confidence_target=confidence_target,
                follow_up_budget_remaining=remaining,
                suppressed_until=suppressed_until,
            )
            closed_at = now_iso if status_value == "closed" else None
            conn.execute(
                """
                UPDATE csi_specialist_loops
                SET follow_up_budget_remaining = ?,
                    status = ?,
                    last_followup_requested_at = ?,
                    updated_at = ?,
                    closed_at = ?
                WHERE topic_key = ?
                """,
                (
                    remaining,
                    status_value,
                    now_iso,
                    now_iso,
                    closed_at,
                    cleaned_topic_key,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    updated = _get_csi_specialist_loop(cleaned_topic_key)
    if updated is None:
        raise HTTPException(status_code=404, detail="Specialist loop not found")
    _record_activity_audit(
        event_id=_csi_loop_audit_event_id(cleaned_topic_key),
        action="csi_loop_request_followup",
        actor=actor,
        outcome="ok",
        note=note,
        metadata={
            "topic_key": cleaned_topic_key,
            "trigger": trigger,
            "follow_up_budget_remaining": updated.get("follow_up_budget_remaining"),
        },
    )
    return {"ok": True, "reason": reason, "loop": updated}


def _record_activity_audit(
    *,
    event_id: str,
    action: str,
    actor: str,
    outcome: str = "ok",
    note: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    cleaned_event_id = str(event_id or "").strip()
    cleaned_action = str(action or "").strip().lower()
    if not cleaned_event_id or not cleaned_action:
        return
    with _activity_store_lock:
        conn = _activity_connect()
        try:
            _ensure_activity_schema(conn)
            conn.execute(
                """
                INSERT INTO activity_event_audit (
                    event_id, action, actor, outcome, note, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cleaned_event_id,
                    cleaned_action,
                    str(actor or "dashboard_user")[:128],
                    str(outcome or "ok").strip().lower() or "ok",
                    str(note or "").strip() or None,
                    _activity_json_dumps(metadata or {}, fallback="{}"),
                    _utc_now_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def _list_activity_audit(
    *,
    event_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    conn = _activity_connect()
    try:
        _ensure_activity_schema(conn)
        where: list[str] = []
        params: list[Any] = []
        if event_id:
            where.append("event_id = ?")
            params.append(str(event_id).strip())
        if action:
            where.append("LOWER(action) = ?")
            params.append(str(action).strip().lower())
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        rows = conn.execute(
            f"""
            SELECT id, event_id, action, actor, outcome, note, metadata_json, created_at
            FROM activity_event_audit
            {where_sql}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (*params, max(1, min(int(limit), 1000))),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "id": int(row["id"] or 0),
                    "event_id": str(row["event_id"] or ""),
                    "action": str(row["action"] or ""),
                    "actor": str(row["actor"] or ""),
                    "outcome": str(row["outcome"] or "ok"),
                    "note": str(row["note"] or "") or None,
                    "metadata": _activity_json_loads_obj(row["metadata_json"], default={}),
                    "created_at_utc": _normalize_notification_timestamp(row["created_at"]),
                }
            )
        return out
    finally:
        conn.close()


def _delete_activity_events(ids: list[str]) -> None:
    cleaned = [str(item).strip() for item in ids if str(item).strip()]
    if not cleaned:
        return
    with _activity_store_lock:
        conn = _activity_connect()
        try:
            _ensure_activity_schema(conn)
            placeholders = ",".join(["?"] * len(cleaned))
            existing_rows = conn.execute(
                f"SELECT id FROM activity_events WHERE id IN ({placeholders})",
                cleaned,
            ).fetchall()
            existing_ids = [str(row["id"] or "").strip() for row in existing_rows if str(row["id"] or "").strip()]
            conn.execute(f"DELETE FROM activity_events WHERE id IN ({placeholders})", cleaned)
            for event_id in existing_ids:
                _activity_stream_append(
                    conn,
                    event_id=event_id,
                    op="delete",
                    payload={"id": event_id},
                )
            _activity_prune_old(conn)
            conn.commit()
        finally:
            conn.close()


def _replace_notification_cache_record(updated: dict[str, Any]) -> None:
    notification_id = str(updated.get("id") or "").strip()
    if not notification_id:
        return
    for idx, existing in enumerate(_notifications):
        if str(existing.get("id") or "") == notification_id:
            _notifications[idx] = updated
            return
    _notifications.append(updated)
    if len(_notifications) > _notifications_max:
        del _notifications[: len(_notifications) - _notifications_max]


def _query_notification_activity_rows(
    *,
    kind: Optional[str] = None,
    status_value: Optional[str] = None,
    older_than_hours: Optional[int] = None,
    limit: int = 200,
    apply_default_window: bool = False,
) -> list[dict[str, Any]]:
    conn = _activity_connect()
    try:
        _ensure_activity_schema(conn)
        where = ["event_class = 'notification'"]
        params: list[Any] = []
        if kind:
            where.append("LOWER(kind) = ?")
            params.append(kind.strip().lower())
        if status_value:
            where.append("LOWER(status) = ?")
            params.append(status_value.strip().lower())
        if older_than_hours is not None:
            clamped = max(1, min(int(older_than_hours), 24 * 365))
            where.append("created_at <= datetime('now', ?)")
            params.append(f"-{clamped} hours")
        if apply_default_window:
            where.append("created_at >= datetime('now', ?)")
            params.append(f"-{int(_activity_events_default_window_days)} days")
        where_sql = f"WHERE {' AND '.join(where)}"
        rows = conn.execute(
            f"""
            SELECT *
            FROM activity_events
            {where_sql}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, max(1, min(int(limit), 5000))),
        ).fetchall()
        return [_activity_row_to_notification(row) for row in rows]
    finally:
        conn.close()


def _apply_activity_snooze_expiry() -> int:
    changed = 0
    items = _query_notification_activity_rows(status_value="snoozed", limit=5000, apply_default_window=False)
    now_ts = time.time()
    for item in items:
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        until_ts = _parse_snooze_until(metadata)
        if until_ts is None or until_ts > now_ts:
            continue
        item["status"] = "new"
        item["updated_at"] = _utc_now_iso()
        metadata["snooze_expired_at"] = item["updated_at"]
        metadata.pop("snooze_until_ts", None)
        metadata.pop("snooze_until", None)
        metadata.pop("snooze_minutes", None)
        _persist_notification_activity(item)
        _replace_notification_cache_record(item)
        changed += 1
    return changed


def _bulk_update_activity_notifications(
    *,
    status_value: str,
    note: Optional[str] = None,
    kind: Optional[str] = None,
    current_status: Optional[str] = None,
    snooze_minutes: Optional[int] = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    candidates = _query_notification_activity_rows(
        kind=kind,
        status_value=current_status,
        limit=limit,
        apply_default_window=False,
    )
    if not candidates:
        updated_in_memory: list[dict[str, Any]] = []
        for item in reversed(_notifications):
            if len(updated_in_memory) >= limit:
                break
            if kind and str(item.get("kind") or "").strip().lower() != kind:
                continue
            if current_status and _normalize_notification_status(item.get("status")) != current_status:
                continue
            _apply_notification_status(
                item,
                status_value=status_value,
                note=note,
                snooze_minutes=snooze_minutes,
            )
            updated_in_memory.append(item)
        return updated_in_memory

    updated: list[dict[str, Any]] = []
    for item in candidates:
        _apply_notification_status(
            item,
            status_value=status_value,
            note=note,
            snooze_minutes=snooze_minutes,
        )
        _replace_notification_cache_record(item)
        updated.append(item)
    return updated


def _purge_activity_notifications(
    *,
    clear_all: bool,
    kind: Optional[str] = None,
    current_status: Optional[str] = None,
    older_than_hours: Optional[int] = None,
) -> int:
    deleted_ids: list[str] = []
    if clear_all and not kind and not current_status and older_than_hours is None:
        conn = _activity_connect()
        try:
            _ensure_activity_schema(conn)
            rows = conn.execute("SELECT id FROM activity_events WHERE event_class = 'notification'").fetchall()
            deleted_ids = [str(row["id"] or "") for row in rows if str(row["id"] or "").strip()]
        finally:
            conn.close()
        if deleted_ids:
            _delete_activity_events(deleted_ids)
    else:
        rows = _query_notification_activity_rows(
            kind=kind,
            status_value=current_status,
            older_than_hours=older_than_hours,
            limit=5000,
            apply_default_window=False,
        )
        deleted_ids = [str(item.get("id") or "") for item in rows if str(item.get("id") or "").strip()]
        if deleted_ids:
            _delete_activity_events(deleted_ids)
    if not deleted_ids:
        cutoff_ts = None
        if older_than_hours is not None:
            cutoff_ts = time.time() - (max(1, min(int(older_than_hours), 24 * 365)) * 3600)
        deleted_memory: list[str] = []
        kept_memory: list[dict[str, Any]] = []
        for item in _notifications:
            matches = True
            if kind and str(item.get("kind") or "").strip().lower() != kind:
                matches = False
            if current_status and _normalize_notification_status(item.get("status")) != current_status:
                matches = False
            if cutoff_ts is not None:
                created_ts = _notification_created_epoch(item)
                if created_ts is None or created_ts > cutoff_ts:
                    matches = False
            if matches:
                deleted_memory.append(str(item.get("id") or "").strip())
            else:
                kept_memory.append(item)
        if deleted_memory:
            _notifications[:] = kept_memory
            deleted_ids = [item for item in deleted_memory if item]
    if deleted_ids:
        deleted_set = set(deleted_ids)
        _notifications[:] = [item for item in _notifications if str(item.get("id") or "") not in deleted_set]
    return len(deleted_ids)


def _notification_targets() -> dict:
    config = load_ops_config()
    notifications = config.get("notifications", {})
    if not isinstance(notifications, dict):
        notifications = {}

    channels = notifications.get("channels")
    if not isinstance(channels, list) or not channels:
        channels = ["dashboard", "email", "telegram"]
    normalized_channels = [str(ch).strip().lower() for ch in channels if str(ch).strip()]

    email_targets = notifications.get("email_targets")
    if not isinstance(email_targets, list) or not email_targets:
        fallback_email = (
            os.getenv("UA_NOTIFICATION_EMAIL")
            or os.getenv("UA_PRIMARY_EMAIL")
            or "kevinjdragan@gmail.com"
        )
        email_targets = [fallback_email]

    return {
        "channels": normalized_channels,
        "email_targets": [str(email).strip() for email in email_targets if str(email).strip()],
    }


def _activity_digest_should_compact(
    *,
    kind: str,
    severity: str,
    requires_action: bool,
    metadata: Optional[dict[str, Any]],
) -> bool:
    if not _activity_digest_enabled:
        return False
    kind_norm = str(kind or "").strip().lower()
    severity_norm = str(severity or "info").strip().lower()
    metadata = metadata if isinstance(metadata, dict) else {}
    if severity_norm in {"warning", "error", "critical"}:
        return False
    if bool(requires_action):
        return False
    if bool(metadata.get("pinned")):
        return False
    if bool(metadata.get("digest_compacted")):
        return False
    if kind_norm in _activity_digest_exclude_kinds:
        return False
    bypass_prefixes = (
        "simone_handoff_",
        "csi_pipeline_digest",
        "continuity_",
    )
    if kind_norm.startswith(bypass_prefixes):
        return False
    high_value_csi = {
        "csi_insight",
        "csi_specialist_daily_rollup",
        "csi_specialist_hourly_synthesis",
    }
    if kind_norm in high_value_csi:
        return False
    event_type = str(metadata.get("event_type") or "").strip().lower()
    if event_type in {
        "rss_trend_report",
        "reddit_trend_report",
        "rss_insight_daily",
        "rss_insight_emerging",
        "report_product_ready",
        "opportunity_bundle_ready",
    }:
        return False
    return True


def _activity_digest_bucket_key(
    *,
    kind: str,
    severity: str,
    source_domain: str,
    created_at: str,
) -> tuple[str, str]:
    parsed = _parse_iso_timestamp(created_at) or datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    hour_key = parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")
    digest_key = f"{source_domain}:{str(kind or '').strip().lower()}:{str(severity or 'info').strip().lower()}:{hour_key}"
    return digest_key, hour_key


def _activity_upsert_digest_notification(
    *,
    digest_key: str,
    hour_key: str,
    source_domain: str,
    kind: str,
    title: str,
    message: str,
    session_id: Optional[str],
    severity: str,
    metadata: Optional[dict[str, Any]],
    created_at: str,
) -> Optional[dict[str, Any]]:
    metadata_obj = dict(metadata) if isinstance(metadata, dict) else {}
    sample_ref = str(metadata_obj.get("event_id") or metadata_obj.get("report_key") or "").strip()
    for item in reversed(_notifications):
        if str(item.get("kind") or "").strip().lower() != str(kind or "").strip().lower():
            continue
        existing_meta = item.get("metadata")
        if not isinstance(existing_meta, dict):
            continue
        if str(existing_meta.get("digest_key") or "").strip() != digest_key:
            continue
        sample_ids = existing_meta.get("sample_ids")
        if not isinstance(sample_ids, list):
            sample_ids = []
        if sample_ref:
            sample_ids.append(sample_ref)
        sample_ids = [str(v).strip() for v in sample_ids if str(v).strip()][-_activity_digest_max_sample_ids:]
        event_count = int(existing_meta.get("digest_event_count") or 1) + 1
        existing_meta.update(
            {
                "digest_compacted": True,
                "digest_key": digest_key,
                "digest_hour": hour_key,
                "digest_event_count": event_count,
                "digest_last_sample_at": created_at,
                "sample_ids": sample_ids,
                "source_domain": source_domain,
                "event_type": str(metadata_obj.get("event_type") or existing_meta.get("event_type") or ""),
            }
        )
        item["title"] = title
        item["summary"] = f"{title} ({hour_key}) — {event_count} events"
        item["full_message"] = (
            f"{title} ({hour_key})\n"
            f"Compacted events: {event_count}\n"
            f"Source: {source_domain}\n"
            f"Latest sample:\n{message[:8000]}"
        )
        item["message"] = item["full_message"]
        item["updated_at"] = _utc_now_iso()
        item["created_at"] = _normalize_notification_timestamp(item.get("created_at") or created_at)
        item["status"] = "new"
        _replace_notification_cache_record(item)
        _persist_notification_activity(item)
        return item

    return None


def _add_notification(
    *,
    kind: str,
    title: str,
    message: str,
    summary: Optional[str] = None,
    full_message: Optional[str] = None,
    session_id: Optional[str] = None,
    severity: str = "info",
    requires_action: bool = False,
    metadata: Optional[dict] = None,
    created_at: Optional[str] = None,
) -> dict:
    metadata_obj = dict(metadata) if isinstance(metadata, dict) else {}
    summary_text = summary
    full_message_text = full_message if full_message is not None else message
    timestamp = _normalize_notification_timestamp(created_at)
    source_domain = _activity_source_domain(str(kind or ""), metadata_obj)
    should_compact = _activity_digest_should_compact(
        kind=str(kind or ""),
        severity=str(severity or "info"),
        requires_action=bool(requires_action),
        metadata=metadata_obj,
    )
    if _activity_digest_enabled and not should_compact:
        _activity_counter_inc("digest_immediate_bypass_total")
    if should_compact:
        digest_key, hour_key = _activity_digest_bucket_key(
            kind=str(kind or ""),
            severity=str(severity or "info"),
            source_domain=source_domain,
            created_at=timestamp,
        )
        compacted = _activity_upsert_digest_notification(
            digest_key=digest_key,
            hour_key=hour_key,
            source_domain=source_domain,
            kind=str(kind or ""),
            title=str(title or "Notification Digest"),
            message=str(full_message_text),
            session_id=session_id,
            severity=str(severity or "info"),
            metadata={
                **metadata_obj,
                "digest_compacted": True,
                "digest_key": digest_key,
                "digest_hour": hour_key,
                "digest_event_count": 1,
                "digest_last_sample_at": timestamp,
                "sample_ids": [str(metadata_obj.get("event_id") or "").strip()] if str(metadata_obj.get("event_id") or "").strip() else [],
                "source_domain": source_domain,
            },
            created_at=timestamp,
        )
        if compacted is not None:
            _activity_counter_inc("digest_compacted_total")
            return compacted
        _activity_counter_inc("digest_compacted_total")
        metadata_obj = {
            **metadata_obj,
            "digest_compacted": True,
            "digest_key": digest_key,
            "digest_hour": hour_key,
            "digest_event_count": 1,
            "digest_last_sample_at": timestamp,
            "sample_ids": [str(metadata_obj.get("event_id") or "").strip()] if str(metadata_obj.get("event_id") or "").strip() else [],
            "source_domain": source_domain,
        }
        if summary_text is None:
            summary_text = f"{title} ({hour_key}) — 1 event"
        full_message_text = (
            f"{title} ({hour_key})\\n"
            "Compacted events: 1\\n"
            f"Source: {source_domain}\\n"
            f"Latest sample:\\n{str(full_message_text)[:8000]}"
        )

    notification_id = f"ntf_{int(time.time() * 1000)}_{len(_notifications) + 1}"
    targets = _notification_targets()
    record = {
        "id": notification_id,
        "kind": kind,
        "title": title,
        "message": full_message_text,
        "summary": summary_text if summary_text is not None else _activity_summary_text(message),
        "full_message": full_message_text,
        "session_id": session_id,
        "severity": severity,
        "requires_action": requires_action,
        "status": "new",
        "created_at": timestamp,
        "updated_at": timestamp,
        "channels": targets["channels"],
        "email_targets": targets["email_targets"],
        "metadata": {
            **metadata_obj,
            "source_domain": source_domain,
        },
    }
    _notifications.append(record)
    if len(_notifications) > _notifications_max:
        del _notifications[: len(_notifications) - _notifications_max]

    if session_id:
        event = {
            "event_id": f"evt_ntf_{notification_id}",
            "type": "notification",
            "payload": record,
            "created_at": timestamp,
        }
        _enqueue_system_event(session_id, event)
        if session_id in manager.session_connections:
            _broadcast_system_event(session_id, event)
    _persist_notification_activity(record)
    return record


def _hook_notification_sink(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    metadata = payload.get("metadata")
    _add_notification(
        kind=str(payload.get("kind") or "hook_event"),
        title=str(payload.get("title") or "Hook Event"),
        message=str(payload.get("message") or "Hook event"),
        session_id=str(payload.get("session_id") or "").strip() or None,
        severity=str(payload.get("severity") or "info"),
        requires_action=bool(payload.get("requires_action")),
        metadata=metadata if isinstance(metadata, dict) else None,
    )


def _normalize_notification_status(status: str) -> str:
    return str(status or "").strip().lower()


def _resolve_snooze_minutes(value: Optional[int]) -> int:
    if value is None:
        return max(1, min(NOTIFICATION_SNOOZE_MINUTES_DEFAULT, NOTIFICATION_SNOOZE_MINUTES_MAX))
    try:
        parsed = int(value)
    except Exception:
        parsed = NOTIFICATION_SNOOZE_MINUTES_DEFAULT
    return max(1, min(parsed, NOTIFICATION_SNOOZE_MINUTES_MAX))


def _parse_snooze_until(metadata: dict[str, Any]) -> Optional[float]:
    until_ts = metadata.get("snooze_until_ts")
    if isinstance(until_ts, (int, float)):
        return float(until_ts)
    until_raw = metadata.get("snooze_until")
    if isinstance(until_raw, str) and until_raw.strip():
        parsed = _parse_iso_timestamp(until_raw.strip())
        if parsed:
            return parsed.timestamp()
    return None


def _apply_notification_status(
    item: dict[str, Any],
    *,
    status_value: str,
    note: Optional[str] = None,
    snooze_minutes: Optional[int] = None,
) -> dict[str, Any]:
    normalized = _normalize_notification_status(status_value)
    item["status"] = normalized
    item["updated_at"] = _utc_now_iso()
    metadata = item.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        item["metadata"] = metadata
    if note:
        metadata["note"] = note
    if normalized == "snoozed":
        minutes = _resolve_snooze_minutes(snooze_minutes)
        until_ts = time.time() + (minutes * 60)
        metadata["snooze_minutes"] = minutes
        metadata["snooze_until_ts"] = until_ts
        metadata["snooze_until"] = datetime.fromtimestamp(until_ts, tz=timezone.utc).isoformat()
    else:
        metadata.pop("snooze_until_ts", None)
        metadata.pop("snooze_until", None)
        metadata.pop("snooze_minutes", None)
    _persist_notification_activity(item)
    return item


def _apply_notification_snooze_expiry() -> int:
    now_ts = time.time()
    changed = 0
    for item in _notifications:
        if _normalize_notification_status(item.get("status")) != "snoozed":
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            continue
        until_ts = _parse_snooze_until(metadata)
        if until_ts is None or until_ts > now_ts:
            continue
        item["status"] = "new"
        item["updated_at"] = _utc_now_iso()
        metadata["snooze_expired_at"] = item["updated_at"]
        metadata.pop("snooze_until_ts", None)
        metadata.pop("snooze_until", None)
        metadata.pop("snooze_minutes", None)
        changed += 1
        _persist_notification_activity(item)
    return changed


def _approval_status(approval_id: str) -> Optional[str]:
    for record in list_approvals():
        if record.get("approval_id") == approval_id:
            status = record.get("status")
            return str(status).lower() if status else None
    return None


def _pending_gate_is_approved(session_id: str) -> bool:
    pending = _pending_gated_requests.get(session_id)
    if not pending:
        return False
    approval_id = pending.get("approval_id")
    if not approval_id:
        return str(pending.get("status", "")).lower() == "approved"
    status = _approval_status(str(approval_id))
    return status == "approved"


def _broadcast_presence(payload: dict) -> None:
    event = {
        "type": "system_presence",
        "data": payload,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    for session_id in list(manager.session_connections.keys()):
        asyncio.create_task(manager.broadcast(session_id, event))


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


def _read_heartbeat_state(workspace_dir: str) -> Optional[dict]:
    state_path = Path(workspace_dir) / "heartbeat_state.json"
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text())
    except Exception as exc:
        logger.warning("Failed to read heartbeat_state.json: %s", exc)
        return None


def _calendar_timezone_or_default(value: Optional[str]) -> str:
    candidate = (value or os.getenv("USER_TIMEZONE") or "America/Chicago").strip()
    if not candidate:
        candidate = "America/Chicago"
    try:
        ZoneInfo(candidate)
        return candidate
    except Exception:
        return "America/Chicago"


def _calendar_parse_ts(value: Optional[str], timezone_name: str) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        pass
    try:
        if text.endswith("Z"):
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    return parsed.astimezone(timezone.utc).timestamp()


def _calendar_local_iso(ts: float, timezone_name: str) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(ZoneInfo(timezone_name)).isoformat()


def _calendar_event_id(source: str, source_ref: str, scheduled_at: float) -> str:
    return f"{source}|{source_ref}|{int(scheduled_at)}"


def _calendar_parse_event_id(event_id: str) -> tuple[str, str, int]:
    parts = str(event_id).split("|", 2)
    if len(parts) != 3:
        raise HTTPException(status_code=400, detail="Invalid calendar event id")
    source, source_ref, scheduled_part = parts
    if source not in {"cron", "heartbeat"}:
        raise HTTPException(status_code=400, detail="Unsupported calendar source")
    try:
        scheduled_at = int(scheduled_part)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid calendar event timestamp") from exc
    return source, source_ref, scheduled_at


def _calendar_normalize_window(
    *,
    start: Optional[str],
    end: Optional[str],
    view: str,
    timezone_name: str,
) -> tuple[float, float]:
    view_mode = (view or "week").strip().lower()
    if view_mode not in {"day", "week"}:
        view_mode = "week"
    now_local = datetime.now(ZoneInfo(timezone_name))
    if view_mode == "day":
        default_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        default_end_local = default_start_local + timedelta(days=1)
    else:
        # Sunday-based week start
        days_since_sunday = (now_local.weekday() + 1) % 7
        default_start_local = (now_local - timedelta(days=days_since_sunday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        default_end_local = default_start_local + timedelta(days=7)

    start_ts = _calendar_parse_ts(start, timezone_name)
    end_ts = _calendar_parse_ts(end, timezone_name)
    if start_ts is None:
        start_ts = default_start_local.astimezone(timezone.utc).timestamp()
    if end_ts is None:
        end_ts = default_end_local.astimezone(timezone.utc).timestamp()
    if end_ts <= start_ts:
        end_ts = start_ts + (86400 if view_mode == "day" else 7 * 86400)

    now_ts = time.time()
    # Rolling retention: last 30 days visible. Keep a short future horizon.
    min_start = now_ts - (30 * 86400)
    max_end = now_ts + (14 * 86400)
    start_ts = max(start_ts, min_start)
    end_ts = min(end_ts, max_end)
    if end_ts <= start_ts:
        end_ts = min(start_ts + 86400, max_end)
    return start_ts, end_ts


def _calendar_read_heartbeat_overrides(workspace_dir: str) -> dict[str, Any]:
    workspace = Path(workspace_dir)
    for name in ("HEARTBEAT.json", "heartbeat.json", ".heartbeat.json"):
        path = workspace / name
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if isinstance(payload, dict):
            return payload
        return {}
    return {}


def _calendar_write_heartbeat_overrides(session_id: str, payload: dict[str, Any]) -> str:
    workspace = WORKSPACES_DIR / session_id
    if not workspace.exists():
        raise HTTPException(status_code=404, detail="Session workspace not found")
    path = workspace / "HEARTBEAT.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(path)


def _calendar_merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _calendar_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _calendar_interval_seconds_from_text(text: str) -> Optional[int]:
    import re

    match = re.search(
        r"every\s+(\d+)\s*(second|seconds|sec|s|minute|minutes|min|m|hour|hours|h|day|days|d)\b",
        text.lower(),
    )
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit in {"second", "seconds", "sec", "s"}:
        return amount
    if unit in {"minute", "minutes", "min", "m"}:
        return amount * 60
    if unit in {"hour", "hours", "h"}:
        return amount * 3600
    if unit in {"day", "days", "d"}:
        return amount * 86400
    return None


def _calendar_parse_run_at_text(text: str, timezone_name: str) -> Optional[float]:
    import re

    lower = text.lower()
    rel = re.search(r"in\s+(\d+)\s*(minute|minutes|min|m|hour|hours|h|day|days|d)\b", lower)
    if rel:
        amount = int(rel.group(1))
        unit = rel.group(2)
        if unit in {"minute", "minutes", "min", "m"}:
            return time.time() + (amount * 60)
        if unit in {"hour", "hours", "h"}:
            return time.time() + (amount * 3600)
        if unit in {"day", "days", "d"}:
            return time.time() + (amount * 86400)
    at_match = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", lower)
    if at_match:
        hour = int(at_match.group(1))
        minute = int(at_match.group(2) or "0")
        ampm = at_match.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        local_now = datetime.now(ZoneInfo(timezone_name))
        candidate = local_now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
        if candidate <= local_now:
            candidate = candidate + timedelta(days=1)
        return candidate.astimezone(timezone.utc).timestamp()
    return None


def _calendar_status_from_cron_run(
    run: Optional[dict[str, Any]],
    now_ts: float,
    scheduled_at: float,
    enabled: bool,
    is_running: bool = False,
) -> str:
    if run:
        run_status = str(run.get("status", "")).lower()
        if run_status == "success":
            return "success"
        if run_status == "running":
            return "running"
        if run_status == "skipped":
            return "failed"
        return "failed"
    if is_running:
        return "running"
    if not enabled:
        return "disabled"
    return "missed" if scheduled_at < now_ts else "scheduled"


def _calendar_register_missed_event(event: dict[str, Any]) -> None:
    event_id = str(event.get("event_id") or "")
    if not event_id:
        return
    existing = _calendar_missed_events.get(event_id)
    if existing:
        return
    source = str(event.get("source") or "").strip().lower()
    source_ref = str(event.get("source_ref") or "").strip()
    scheduled_at = float(event.get("scheduled_at_epoch") or 0.0)
    superseded_ids: list[str] = []
    for existing_id, existing_record in _calendar_missed_events.items():
        if str(existing_record.get("status") or "").strip().lower() != "pending":
            continue
        existing_event = existing_record.get("event") if isinstance(existing_record.get("event"), dict) else {}
        if str(existing_event.get("source") or "").strip().lower() != source:
            continue
        if str(existing_event.get("source_ref") or "").strip() != source_ref:
            continue
        existing_scheduled_at = float(existing_event.get("scheduled_at_epoch") or 0.0)
        if existing_scheduled_at >= scheduled_at:
            # Keep the newest pending missed item only.
            return
        existing_record["status"] = "skipped_superseded"
        existing_record["updated_at"] = datetime.now(timezone.utc).isoformat()
        superseded_ids.append(existing_id)

    for superseded_id in superseded_ids:
        _calendar_missed_notifications.discard(superseded_id)

    record = {
        "event_id": event_id,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "event": event,
    }
    _calendar_missed_events[event_id] = record
    if str(event.get("category") or "").strip().lower() == "low":
        return
    if event_id in _calendar_missed_notifications:
        return
    _calendar_missed_notifications.add(event_id)
    _add_notification(
        kind="calendar_missed",
        title="Missed Scheduled Event",
        message=f"{event.get('title', 'Scheduled event')} was missed and is awaiting action.",
        session_id=event.get("session_id"),
        severity="warning",
        requires_action=True,
        metadata={
            "event_id": event_id,
            "source": event.get("source"),
            "source_ref": event.get("source_ref"),
            "actions": ["approve_backfill_run", "reschedule", "delete_missed"],
        },
    )


def _calendar_missed_resolution(event_id: str) -> Optional[str]:
    record = _calendar_missed_events.get(event_id)
    if not record:
        return None
    status = str(record.get("status") or "").strip().lower()
    if status in {"approved_and_run", "rescheduled", "deleted", "skipped", "skipped_superseded"}:
        return status
    return None


def _calendar_cleanup_state() -> None:
    now_ts = time.time()
    stale_cutoff = now_ts - (30 * 86400)
    stale_event_ids: list[str] = []
    for event_id, record in _calendar_missed_events.items():
        event = record.get("event") if isinstance(record.get("event"), dict) else {}
        source = str(event.get("source") or "").strip().lower()
        # Heartbeat missed backfill/alerts are disabled by policy.
        if source == "heartbeat":
            stale_event_ids.append(event_id)
            continue
        scheduled_ts = _calendar_parse_ts(str(event.get("scheduled_at_utc") or ""), "UTC")
        if scheduled_ts is None:
            continue
        if float(scheduled_ts) < stale_cutoff:
            stale_event_ids.append(event_id)
    for event_id in stale_event_ids:
        _calendar_missed_events.pop(event_id, None)
        _calendar_missed_notifications.discard(event_id)

    # Remove legacy heartbeat-missed notifications from the dashboard stream.
    _notifications[:] = [
        item
        for item in _notifications
        if not (
            str(item.get("kind") or "").strip().lower() == "calendar_missed"
            and str((item.get("metadata") or {}).get("source") or "").strip().lower() == "heartbeat"
        )
    ]

    # Keep at most one pending missed item per (source, source_ref): newest wins.
    latest_pending_by_key: dict[tuple[str, str], tuple[str, float]] = {}
    for event_id, record in _calendar_missed_events.items():
        if str(record.get("status") or "").strip().lower() != "pending":
            continue
        event = record.get("event") if isinstance(record.get("event"), dict) else {}
        source = str(event.get("source") or "").strip().lower()
        source_ref = str(event.get("source_ref") or "").strip()
        if not source or not source_ref:
            continue
        scheduled_at = float(event.get("scheduled_at_epoch") or 0.0)
        if scheduled_at <= 0:
            parsed = _calendar_parse_ts(str(event.get("scheduled_at_utc") or ""), "UTC")
            scheduled_at = float(parsed or 0.0)
        key = (source, source_ref)
        current = latest_pending_by_key.get(key)
        if current is None or scheduled_at > current[1]:
            latest_pending_by_key[key] = (event_id, scheduled_at)

    for event_id, record in list(_calendar_missed_events.items()):
        if str(record.get("status") or "").strip().lower() != "pending":
            continue
        event = record.get("event") if isinstance(record.get("event"), dict) else {}
        key = (
            str(event.get("source") or "").strip().lower(),
            str(event.get("source_ref") or "").strip(),
        )
        latest = latest_pending_by_key.get(key)
        if latest and latest[0] != event_id:
            record["status"] = "skipped_superseded"
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
            _calendar_missed_notifications.discard(event_id)

    expired_proposals = [
        proposal_id
        for proposal_id, proposal in _calendar_change_proposals.items()
        if float(proposal.get("created_at_ts", 0.0)) < (now_ts - 86400)
    ]
    for proposal_id in expired_proposals:
        _calendar_change_proposals.pop(proposal_id, None)


def _calendar_match_cron_run(runs: list[dict[str, Any]], scheduled_at: float) -> Optional[dict[str, Any]]:
    best: Optional[dict[str, Any]] = None
    best_delta = 1e12
    for run in runs:
        raw_ts = run.get("scheduled_at")
        if raw_ts is None:
            continue
        try:
            run_ts = float(raw_ts)
        except Exception:
            continue
        delta = abs(run_ts - scheduled_at)
        if delta <= 90 and delta < best_delta:
            best = run
            best_delta = delta
    return best


def _calendar_iter_cron_occurrences(job: Any, start_ts: float, end_ts: float, max_count: int = 500) -> list[float]:
    occurrences: list[float] = []
    try:
        run_at = float(job.run_at) if job.run_at is not None else None
    except Exception:
        run_at = None
    if run_at is not None:
        if start_ts <= run_at <= end_ts:
            occurrences.append(run_at)
        return occurrences

    cron_expr = str(job.cron_expr or "").strip()
    if cron_expr:
        try:
            from croniter import croniter

            tz_name = getattr(job, "timezone", None) or "UTC"
            tz = ZoneInfo(str(tz_name))
            base_local = datetime.fromtimestamp(start_ts, timezone.utc).astimezone(tz) - timedelta(seconds=1)
            itr = croniter(cron_expr, base_local)
            for _ in range(max_count):
                next_dt = itr.get_next(datetime)
                next_ts = next_dt.astimezone(timezone.utc).timestamp()
                if next_ts > end_ts:
                    break
                if next_ts >= start_ts:
                    occurrences.append(next_ts)
        except Exception:
            return occurrences
        return occurrences

    every_seconds = int(getattr(job, "every_seconds", 0) or 0)
    if every_seconds <= 0:
        return occurrences
    base = float(getattr(job, "created_at", time.time()) or time.time())
    if base < start_ts:
        steps = int((start_ts - base) // every_seconds)
        base = base + (steps * every_seconds)
        if base < start_ts:
            base += every_seconds
    cursor = base
    count = 0
    while cursor <= end_ts and count < max_count:
        if cursor >= start_ts:
            occurrences.append(cursor)
            count += 1
        cursor += every_seconds
    return occurrences


def _calendar_project_cron_events(
    *,
    start_ts: float,
    end_ts: float,
    timezone_name: str,
    owner: Optional[str],
) -> list[dict[str, Any]]:
    use_projection = bool(_scheduling_projection.enabled)
    if use_projection:
        _scheduling_projection.seed_from_runtime()
        jobs = _scheduling_projection.list_cron_jobs()
        runs = _scheduling_projection.list_cron_runs(limit=2000)
        _scheduling_counter_inc("projection_read_hits")
    else:
        jobs = _cron_service.list_jobs() if _cron_service else []
        runs = _cron_service.list_runs(limit=2000) if _cron_service else []
    if not jobs:
        return []
    now_ts = time.time()
    runs_by_job: dict[str, list[dict[str, Any]]] = {}
    for row in runs:
        job_id = str(row.get("job_id") or "")
        if not job_id:
            continue
        runs_by_job.setdefault(job_id, []).append(row)

    events: list[dict[str, Any]] = []
    for job in jobs:
        if owner and str(getattr(job, "user_id", "")).lower() != owner.strip().lower():
            continue
        running_scheduled_at: Optional[float] = None
        if _cron_service:
            try:
                running_scheduled_at = _cron_service.running_job_scheduled_at.get(str(job.job_id))
            except Exception:
                running_scheduled_at = None
        occurrences = _calendar_iter_cron_occurrences(job, start_ts, end_ts, max_count=400)
        latest_missed_event: Optional[dict[str, Any]] = None
        for scheduled_at in occurrences:
            event_id = _calendar_event_id("cron", str(job.job_id), scheduled_at)
            matched_run = _calendar_match_cron_run(runs_by_job.get(str(job.job_id), []), scheduled_at)
            is_running = bool(
                matched_run is None
                and running_scheduled_at is not None
                and abs(float(running_scheduled_at) - float(scheduled_at)) <= 90
            )
            status_value = _calendar_status_from_cron_run(
                matched_run,
                now_ts=now_ts,
                scheduled_at=scheduled_at,
                enabled=bool(job.enabled),
                is_running=is_running,
            )
            metadata = job.metadata or {}
            workspace_dir = str(getattr(job, "workspace_dir", "") or "")
            workspace_session_id = Path(workspace_dir).name if workspace_dir else ""
            event = {
                "event_id": event_id,
                "source": "cron",
                "source_ref": str(job.job_id),
                "owner_id": str(job.user_id),
                "session_id": str(metadata.get("session_id") or workspace_session_id or ""),
                "channel": str(metadata.get("channel") or "cron"),
                "title": str(metadata.get("title") or f"Chron: {str(job.command)[:40]}"),
                "description": str(job.command),
                "category": str(metadata.get("priority") or "normal"),
                "color_key": "cron",
                "status": status_value,
                "scheduled_at_epoch": scheduled_at,
                "scheduled_at_utc": datetime.fromtimestamp(scheduled_at, timezone.utc).isoformat(),
                "scheduled_at_local": _calendar_local_iso(scheduled_at, timezone_name),
                "timezone_display": timezone_name,
                "always_running": False,
                "actions": [
                    "run_now",
                    "pause" if job.enabled else "resume",
                    "disable",
                    "open_logs",
                    "open_session",
                ],
            }
            if matched_run:
                event["run_status"] = matched_run.get("status")
                event["run_id"] = matched_run.get("run_id")
            if status_value == "missed":
                if scheduled_at < (now_ts - 48 * 3600):
                    continue
                if latest_missed_event is None or float(latest_missed_event.get("scheduled_at_epoch") or 0.0) < float(scheduled_at):
                    latest_missed_event = event
                continue
            events.append(event)
        if latest_missed_event:
            missed_event_id = str(latest_missed_event.get("event_id") or "")
            resolution = _calendar_missed_resolution(missed_event_id)
            if resolution in {"rescheduled", "deleted", "skipped", "skipped_superseded"}:
                continue
            if resolution == "approved_and_run":
                # Preserve the event for operator visibility, but mark it resolved.
                # A completed backfill run usually flips status to success via matched run.
                if str(latest_missed_event.get("status") or "").strip().lower() == "missed":
                    latest_missed_event["status"] = "success"
                latest_missed_event["resolution"] = "approved_and_run"
                latest_missed_event["actions"] = ["open_logs", "open_session"]
                events.append(latest_missed_event)
                continue
            if not resolution:
                latest_missed_event["actions"] = [
                    "approve_backfill_run",
                    "reschedule",
                    "delete_missed",
                    "open_logs",
                    "open_session",
                ]
                _calendar_register_missed_event(latest_missed_event)
            events.append(latest_missed_event)
    return events


def _calendar_should_include_heartbeat_summary(summary: dict[str, Any], now_ts: float) -> bool:
    session_id = str(summary.get("session_id") or "").strip()
    if not session_id:
        return False
    owner_id = str(summary.get("owner") or "").strip().lower()
    source = str(summary.get("source") or "").strip().lower()

    # Ignore cron-owned/session workspaces in heartbeat views.
    if session_id.startswith("cron_") or owner_id.startswith("cron:"):
        return False
    # Keep canonical chat/api session IDs by default; unknown local directories
    # are usually historical workspaces and should not produce always-running rows.
    if source == "local" and not session_id.startswith(("session_", "tg_", "api_")):
        return False

    active_connections = int(summary.get("active_connections") or 0)
    active_runs = int(summary.get("active_runs") or 0)
    busy = bool(_heartbeat_service and session_id in _heartbeat_service.busy_sessions)
    if active_runs > 0 or busy:
        return True

    if active_connections > 0:
        # Stale websocket/runtime counts can survive disconnect races and make
        # old sessions look "always running". Guard with recent activity.
        last_activity_raw = summary.get("last_activity") or summary.get("last_modified")
        last_activity_ts: Optional[float] = None
        parsed_last_activity = _parse_iso_timestamp(last_activity_raw)
        if parsed_last_activity is not None:
            if parsed_last_activity.tzinfo is None:
                parsed_last_activity = parsed_last_activity.replace(tzinfo=timezone.utc)
            else:
                parsed_last_activity = parsed_last_activity.astimezone(timezone.utc)
            last_activity_ts = parsed_last_activity.timestamp()
        heartbeat_last_raw = summary.get("heartbeat_last")
        if heartbeat_last_raw is not None:
            try:
                heartbeat_last_ts = float(heartbeat_last_raw)
                if heartbeat_last_ts > 0:
                    last_activity_ts = max(last_activity_ts or 0.0, heartbeat_last_ts)
            except Exception:
                pass
        if last_activity_ts is not None and (now_ts - last_activity_ts) > CALENDAR_HEARTBEAT_STALE_CONNECTION_SECONDS:
            return False
        return True

    # Only show "always running" heartbeat monitors for active sessions;
    # historical workspaces should not be treated as live monitors.
    return False


def _calendar_heartbeat_session_label(session_id: str) -> str:
    sid = str(session_id or "").strip()
    if not sid:
        return "unknown"

    # Preserve full short hash suffix so similarly-timed sessions stay distinct.
    parts = sid.split("_")
    if sid.startswith("session_") and len(parts) >= 4:
        hash_suffix = parts[-1][-8:]
        base = "_".join(parts[:3])
        return f"{base}[{hash_suffix}]"

    if len(sid) <= 30:
        return sid
    return f"{sid[:20]}…{sid[-8:]}"


def _calendar_project_heartbeat_events(
    *,
    start_ts: float,
    end_ts: float,
    timezone_name: str,
    owner: Optional[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not _ops_service:
        return [], []
    now_ts = time.time()
    events: list[dict[str, Any]] = []
    always_running: list[dict[str, Any]] = []
    for summary in _ops_service.list_sessions(status_filter="all"):
        if not _calendar_should_include_heartbeat_summary(summary, now_ts):
            continue
        session_id = str(summary.get("session_id") or "")
        if not session_id:
            continue
        owner_id = str(summary.get("owner") or "unknown")
        if owner and owner_id.lower() != owner.strip().lower():
            continue
        workspace_dir = str(summary.get("workspace_dir") or str(WORKSPACES_DIR / session_id))
        overrides = _calendar_read_heartbeat_overrides(workspace_dir)
        hidden_flag = overrides.get("calendar_hidden", False)
        if isinstance(hidden_flag, str):
            hidden_flag = hidden_flag.strip().lower() in {"1", "true", "yes", "y", "on"}
        if bool(hidden_flag):
            continue
        if _heartbeat_service:
            schedule = _heartbeat_service._resolve_schedule(overrides)  # type: ignore[attr-defined]
            delivery = _heartbeat_service._resolve_delivery(overrides, session_id)  # type: ignore[attr-defined]
            every_seconds = int(getattr(schedule, "every_seconds", HEARTBEAT_INTERVAL_SECONDS) or HEARTBEAT_INTERVAL_SECONDS)
            delivery_mode = str(getattr(delivery, "mode", "last") or "last")
        else:
            every_seconds = HEARTBEAT_INTERVAL_SECONDS
            delivery_mode = "last"
        every_seconds = max(HEARTBEAT_INTERVAL_SECONDS, every_seconds)

        hb_state = _read_heartbeat_state(workspace_dir) or {}
        last_run = float(hb_state.get("last_run") or 0.0)
        raw_next_run = (last_run + every_seconds) if last_run > 0 else (now_ts + every_seconds)
        # Heartbeats do not backfill: if a run window is missed, schedule the next window.
        if delivery_mode != "none" and raw_next_run < now_ts:
            next_run = now_ts + every_seconds
        else:
            next_run = raw_next_run
        busy = bool(_heartbeat_service and session_id in _heartbeat_service.busy_sessions)
        if delivery_mode == "none":
            status_value = "disabled"
        elif busy:
            status_value = "running"
        else:
            status_value = "scheduled"

        # Main timeline event (next due) within active window.
        session_label = _calendar_heartbeat_session_label(session_id)
        if start_ts <= next_run <= end_ts:
            event_id = _calendar_event_id("heartbeat", session_id, next_run)
            event = {
                "event_id": event_id,
                "source": "heartbeat",
                "source_ref": session_id,
                "owner_id": owner_id,
                "session_id": session_id,
                "channel": str(summary.get("channel") or "heartbeat"),
                "title": f"Heartbeat: {session_label}",
                "description": f"Heartbeat check every {every_seconds // 60} min",
                "category": "normal",
                "color_key": "heartbeat",
                "status": status_value,
                "scheduled_at_epoch": next_run,
                "scheduled_at_utc": datetime.fromtimestamp(next_run, timezone.utc).isoformat(),
                "scheduled_at_local": _calendar_local_iso(next_run, timezone_name),
                "timezone_display": timezone_name,
                "always_running": False,
                "actions": ["delete"],
            }
            events.append(event)

        active_connections = int(summary.get("active_connections") or 0)
        active_runs = int(summary.get("active_runs") or 0)
        is_live_monitor = active_connections > 0 or active_runs > 0 or busy

        always_event = {
            "event_id": _calendar_event_id("heartbeat", session_id, next_run),
            "source": "heartbeat",
            "source_ref": session_id,
            "owner_id": owner_id,
            "session_id": session_id,
            "channel": str(summary.get("channel") or "heartbeat"),
            "title": f"Heartbeat monitor ({session_label})",
            "description": f"Always running • every {max(1, every_seconds // 60)} min",
            "category": "normal",
            "color_key": "heartbeat",
            "status": status_value,
            "scheduled_at_epoch": next_run,
            "scheduled_at_utc": datetime.fromtimestamp(next_run, timezone.utc).isoformat(),
            "scheduled_at_local": _calendar_local_iso(next_run, timezone_name),
            "timezone_display": timezone_name,
            "always_running": True,
            "actions": ["delete"],
        }
        if is_live_monitor:
            always_running.append(always_event)
    return events, always_running


def _calendar_build_feed(
    *,
    start_ts: float,
    end_ts: float,
    timezone_name: str,
    source_filter: str,
    owner: Optional[str],
) -> dict[str, Any]:
    source_norm = (source_filter or "all").strip().lower()
    include_cron = source_norm in {"all", "cron"}
    include_heartbeat = source_norm in {"all", "heartbeat"}

    events: list[dict[str, Any]] = []
    always_running: list[dict[str, Any]] = []
    if include_cron:
        events.extend(
            _calendar_project_cron_events(
                start_ts=start_ts,
                end_ts=end_ts,
                timezone_name=timezone_name,
                owner=owner,
            )
        )
    if include_heartbeat:
        hb_events, hb_always = _calendar_project_heartbeat_events(
            start_ts=start_ts,
            end_ts=end_ts,
            timezone_name=timezone_name,
            owner=owner,
        )
        events.extend(hb_events)
        always_running.extend(hb_always)

    # Defensive dedupe for noisy projection/replay cycles.
    events_by_id = {str(item.get("event_id") or ""): item for item in events if str(item.get("event_id") or "").strip()}
    always_running_by_ref: dict[str, dict[str, Any]] = {}
    for item in always_running:
        source = str(item.get("source") or "").strip().lower()
        source_ref = str(item.get("source_ref") or "").strip()
        if not source or not source_ref:
            continue
        key = f"{source}|{source_ref}"
        existing = always_running_by_ref.get(key)
        if not existing:
            always_running_by_ref[key] = item
            continue
        existing_ts = float(existing.get("scheduled_at_epoch") or 0.0)
        candidate_ts = float(item.get("scheduled_at_epoch") or 0.0)
        if candidate_ts > existing_ts:
            always_running_by_ref[key] = item
    events = list(events_by_id.values())
    always_running = list(always_running_by_ref.values())

    events.sort(key=lambda item: float(item.get("scheduled_at_epoch") or 0.0))
    always_running.sort(key=lambda item: float(item.get("scheduled_at_epoch") or 0.0))
    return {
        "events": events,
        "always_running": always_running,
    }


def _calendar_apply_heartbeat_delivery_mode(session_id: str, mode: str) -> dict[str, Any]:
    workspace = WORKSPACES_DIR / session_id
    if not workspace.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    existing = _calendar_read_heartbeat_overrides(str(workspace))
    merged = _calendar_merge_dict(existing, {"delivery": {"mode": mode}})
    path = _calendar_write_heartbeat_overrides(session_id, merged)
    return {"path": path, "mode": mode}


def _calendar_apply_heartbeat_calendar_hidden(session_id: str, hidden: bool) -> dict[str, Any]:
    workspace = WORKSPACES_DIR / session_id
    if not workspace.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    existing = _calendar_read_heartbeat_overrides(str(workspace))
    merged = _calendar_merge_dict(existing, {"calendar_hidden": bool(hidden)})
    path = _calendar_write_heartbeat_overrides(session_id, merged)
    return {"path": path, "calendar_hidden": bool(hidden)}


def _calendar_apply_heartbeat_interval(session_id: str, every_seconds: int) -> dict[str, Any]:
    workspace = WORKSPACES_DIR / session_id
    if not workspace.exists():
        raise HTTPException(status_code=404, detail="Session not found")
    normalized_seconds = max(HEARTBEAT_INTERVAL_SECONDS, int(every_seconds))
    existing = _calendar_read_heartbeat_overrides(str(workspace))
    merged = _calendar_merge_dict(existing, {"heartbeat": {"every_seconds": normalized_seconds}})
    path = _calendar_write_heartbeat_overrides(session_id, merged)
    return {"path": path, "every_seconds": normalized_seconds}


def _calendar_create_change_proposal(
    *,
    event_id: str,
    instruction: str,
    timezone_name: str,
) -> dict[str, Any]:
    source, source_ref, _ = _calendar_parse_event_id(event_id)
    text = instruction.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Instruction cannot be empty")
    lower = text.lower()
    warnings: list[str] = []
    confidence = "medium"
    operation: dict[str, Any]
    summary: str
    before: dict[str, Any]
    after: dict[str, Any]

    if source == "cron":
        if not _cron_service:
            raise HTTPException(status_code=503, detail="Chron service not available")
        job = _cron_service.get_job(source_ref)
        if not job:
            raise HTTPException(status_code=404, detail="Chron job not found")
        before = job.to_dict()
        if any(token in lower for token in ("pause", "disable", "stop")):
            operation = {"type": "cron_set_enabled", "enabled": False}
            summary = "Disable chron job"
            after = {**before, "enabled": False}
        elif any(token in lower for token in ("resume", "enable", "start")):
            operation = {"type": "cron_set_enabled", "enabled": True}
            summary = "Enable chron job"
            after = {**before, "enabled": True}
        elif "run now" in lower:
            operation = {"type": "cron_run_now"}
            summary = "Run chron job now"
            after = before
        else:
            every_seconds = _calendar_interval_seconds_from_text(lower)
            run_at_ts = _calendar_parse_run_at_text(lower, timezone_name)
            if every_seconds is not None:
                operation = {"type": "cron_set_interval", "every_seconds": every_seconds}
                summary = f"Set chron interval to every {every_seconds} seconds"
                after = {**before, "every_seconds": every_seconds}
                confidence = "high"
            elif run_at_ts is not None:
                operation = {"type": "cron_backfill_schedule", "run_at": run_at_ts}
                summary = "Create one-shot backfill run at requested time"
                after = {
                    "command": before.get("command"),
                    "run_at": datetime.fromtimestamp(run_at_ts, timezone.utc).isoformat(),
                    "delete_after_run": True,
                }
                confidence = "medium"
            else:
                operation = {"type": "none"}
                summary = "Could not safely map instruction to a chron change"
                after = before
                warnings.append("Instruction not recognized; try explicit phrasing like 'pause', 'resume', or 'every 30 minutes'.")
                confidence = "low"
    else:
        before = {"session_id": source_ref}
        if any(token in lower for token in ("pause", "disable", "stop")):
            operation = {"type": "heartbeat_set_delivery", "mode": "none"}
            summary = "Pause heartbeat delivery for session"
            after = {"delivery.mode": "none"}
        elif any(token in lower for token in ("resume", "enable", "start")):
            operation = {"type": "heartbeat_set_delivery", "mode": "last"}
            summary = "Resume heartbeat delivery for session"
            after = {"delivery.mode": "last"}
        elif "run now" in lower:
            operation = {"type": "heartbeat_run_now"}
            summary = "Trigger heartbeat immediately"
            after = before
        else:
            every_seconds = _calendar_interval_seconds_from_text(lower)
            if every_seconds is not None:
                normalized_seconds = max(HEARTBEAT_INTERVAL_SECONDS, int(every_seconds))
                if normalized_seconds != int(every_seconds):
                    warnings.append(
                        f"Heartbeat interval is capped to >= {HEARTBEAT_INTERVAL_SECONDS} seconds (30 minutes) to prevent runaway scheduling."
                    )
                operation = {"type": "heartbeat_set_interval", "every_seconds": normalized_seconds}
                summary = f"Set heartbeat interval to every {normalized_seconds} seconds"
                after = {"heartbeat.every_seconds": normalized_seconds}
            else:
                operation = {"type": "none"}
                summary = "Could not safely map instruction to a heartbeat change"
                after = before
                warnings.append("Instruction not recognized; try explicit phrasing like 'pause', 'resume', or 'every 30 minutes'.")
                confidence = "low"

    proposal_id = f"calprop_{uuid.uuid4().hex[:10]}"
    proposal = {
        "proposal_id": proposal_id,
        "event_id": event_id,
        "source": source,
        "source_ref": source_ref,
        "instruction": text,
        "summary": summary,
        "before": before,
        "after": after,
        "operation": operation,
        "warnings": warnings,
        "confidence": confidence,
        "status": "pending_confirmation",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_at_ts": time.time(),
    }
    _calendar_change_proposals[proposal_id] = proposal
    return proposal


async def _calendar_apply_event_action(
    *,
    source: str,
    source_ref: str,
    action: str,
    event_id: str,
    run_at: Optional[str],
    timezone_name: str,
) -> dict[str, Any]:
    action_norm = action.strip().lower()
    if source == "cron":
        if not _cron_service:
            raise HTTPException(status_code=503, detail="Chron service not available")
        job = _cron_service.get_job(source_ref)
        if not job:
            raise HTTPException(status_code=404, detail="Chron job not found")
        if action_norm == "run_now":
            record = await _cron_service.run_job_now(source_ref, reason="calendar_action")
            return {"status": "ok", "action": action_norm, "run": record.to_dict()}
        if action_norm == "pause":
            updated = _cron_service.update_job(source_ref, {"enabled": False})
            return {"status": "ok", "action": action_norm, "job": updated.to_dict()}
        if action_norm == "resume":
            updated = _cron_service.update_job(source_ref, {"enabled": True})
            return {"status": "ok", "action": action_norm, "job": updated.to_dict()}
        if action_norm == "disable":
            updated = _cron_service.update_job(source_ref, {"enabled": False})
            return {"status": "ok", "action": action_norm, "job": updated.to_dict()}
        if action_norm == "approve_backfill_run":
            _source, _source_ref, scheduled_at = _calendar_parse_event_id(event_id)
            record = await _cron_service.run_job_now(
                source_ref,
                reason="calendar_backfill_approved",
                scheduled_at=float(scheduled_at),
            )
            queue_entry = _calendar_missed_events.get(event_id)
            if queue_entry:
                queue_entry["status"] = "approved_and_run"
                queue_entry["updated_at"] = datetime.now(timezone.utc).isoformat()
            return {"status": "ok", "action": action_norm, "run": record.to_dict()}
        if action_norm == "reschedule":
            run_at_ts = _calendar_parse_ts(run_at, timezone_name) if run_at else None
            if run_at_ts is None and run_at:
                run_at_ts = parse_run_at(run_at, timezone_name=timezone_name)
            if run_at_ts is None:
                run_at_ts = time.time() + 3600
            new_job = _cron_service.add_job(
                user_id=job.user_id,
                workspace_dir=job.workspace_dir,
                command=job.command,
                run_at=run_at_ts,
                delete_after_run=True,
                enabled=True,
                metadata={**(job.metadata or {}), "backfill_for_job_id": source_ref},
            )
            queue_entry = _calendar_missed_events.get(event_id)
            if queue_entry:
                queue_entry["status"] = "rescheduled"
                queue_entry["updated_at"] = datetime.now(timezone.utc).isoformat()
                queue_entry["rescheduled_job_id"] = new_job.job_id
            return {"status": "ok", "action": action_norm, "job": new_job.to_dict()}
        if action_norm == "delete_missed":
            queue_entry = _calendar_missed_events.get(event_id)
            if queue_entry:
                queue_entry["status"] = "deleted"
                queue_entry["updated_at"] = datetime.now(timezone.utc).isoformat()
            return {"status": "ok", "action": action_norm}
        if action_norm == "open_logs":
            return {
                "status": "ok",
                "action": action_norm,
                "path": f"/api/v1/ops/logs/tail?path=cron_runs.jsonl",
            }
        if action_norm == "open_session":
            session_id = str((job.metadata or {}).get("session_id") or "")
            return {"status": "ok", "action": action_norm, "session_id": session_id}

    if source == "heartbeat":
        session_id = source_ref
        if action_norm == "run_now":
            if not _heartbeat_service:
                raise HTTPException(status_code=503, detail="Heartbeat service not available")
            _heartbeat_service.request_heartbeat_now(session_id, reason="calendar_action")
            return {"status": "ok", "action": action_norm, "session_id": session_id}
        if action_norm in {"pause", "disable"}:
            result = _calendar_apply_heartbeat_delivery_mode(session_id, "none")
            return {"status": "ok", "action": action_norm, "session_id": session_id, **result}
        if action_norm == "delete":
            result = _calendar_apply_heartbeat_calendar_hidden(session_id, True)
            return {"status": "ok", "action": action_norm, "session_id": session_id, **result}
        if action_norm == "resume":
            result = _calendar_apply_heartbeat_delivery_mode(session_id, "last")
            return {"status": "ok", "action": action_norm, "session_id": session_id, **result}
        if action_norm in {"approve_backfill_run", "reschedule", "delete_missed"}:
            raise HTTPException(
                status_code=400,
                detail="Heartbeat backfill is disabled. Missed heartbeat windows are skipped automatically.",
            )
        if action_norm == "open_logs":
            return {
                "status": "ok",
                "action": action_norm,
                "path": f"/api/v1/ops/logs/tail?session_id={session_id}",
                "session_id": session_id,
            }
        if action_norm == "open_session":
            return {"status": "ok", "action": action_norm, "session_id": session_id}

    raise HTTPException(status_code=400, detail=f"Unsupported action '{action_norm}' for source '{source}'")


def _load_skill_catalog() -> list[dict]:
    logger.info("Loading skill catalog...")
    skills_dir = os.getenv("UA_SKILLS_DIR") or str(BASE_DIR / ".claude" / "skills")
    overrides = load_ops_config().get("skills", {}).get("entries", {})
    normalized_overrides = {}
    if isinstance(overrides, dict):
        for key, payload in overrides.items():
            enabled = None
            if isinstance(payload, dict):
                enabled = payload.get("enabled")
            elif isinstance(payload, bool):
                enabled = payload
            if isinstance(enabled, bool):
                normalized_overrides[str(key).strip().lower()] = enabled

    entries: list[dict] = []
    try:
        import yaml
        from universal_agent.prompt_assets import _check_skill_requirements
    except Exception:
        yaml = None
        _check_skill_requirements = None  # type: ignore

    if not os.path.isdir(skills_dir) or yaml is None or _check_skill_requirements is None:
        return entries

    for skill_name in os.listdir(skills_dir):
        skill_path = os.path.join(skills_dir, skill_name)
        skill_md = os.path.join(skill_path, "SKILL.md")
        if not os.path.isdir(skill_path) or not os.path.exists(skill_md):
            continue
        try:
            content = Path(skill_md).read_text(encoding="utf-8")
            if not content.startswith("---"):
                continue
            parts = content.split("---", 2)
            if len(parts) < 3:
                continue
            frontmatter = yaml.safe_load(parts[1]) or {}
            name = frontmatter.get("name", skill_name)
            description = frontmatter.get("description", "No description")
            key = str(name).strip().lower()
            enabled_override = normalized_overrides.get(key)
            enabled = True if enabled_override is None else enabled_override
            available, reason = _check_skill_requirements(frontmatter)
            entries.append(
                {
                    "name": name,
                    "description": description,
                    "path": skill_md,
                    "enabled": enabled,
                    "available": available,
                    "disabled_reason": None if enabled else "disabled_by_ops_config",
                    "unavailable_reason": None if available else reason,
                }
            )
        except Exception:
            continue
    return entries


def _load_channel_status() -> list[dict]:
    overrides = load_ops_config().get("channels", {}).get("entries", {})
    normalized = {}
    if isinstance(overrides, dict):
        for key, payload in overrides.items():
            enabled = None
            if isinstance(payload, dict):
                enabled = payload.get("enabled")
            elif isinstance(payload, bool):
                enabled = payload
            if isinstance(enabled, bool):
                normalized[str(key).strip().lower()] = enabled

    channels = [
        {
            "id": "cli",
            "label": "CLI",
            "configured": True,
            "note": "Local CLI entrypoint",
        },
        {
            "id": "web",
            "label": "Web UI",
            "configured": (BASE_DIR / "web-ui").exists(),
            "note": "Gateway + Web UI stack",
        },
        {
            "id": "gateway",
            "label": "Gateway",
            "configured": True,
            "note": "FastAPI gateway service",
        },
        {
            "id": "telegram",
            "label": "Telegram",
            "configured": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
            "note": "Telegram bot integration",
        },
    ]
    for channel in channels:
        override = normalized.get(channel["id"])
        if override is None:
            channel["enabled"] = channel["configured"]
        else:
            channel["enabled"] = override
        channel["probe"] = _channel_probe_results.get(channel["id"])
    return channels


async def _probe_channel(channel_id: str, timeout: float = 4.0) -> dict:
    normalized = channel_id.strip().lower()
    checked_at = datetime.now(timezone.utc).isoformat()
    base = {"id": normalized, "checked_at": checked_at}

    if normalized in {"gateway", "cli"}:
        return {**base, "status": "ok", "detail": "local"}

    if normalized == "web":
        url = os.getenv("UA_WEB_UI_URL", "").strip()
        if not url:
            return {**base, "status": "unknown", "detail": "UA_WEB_UI_URL not set"}
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url)
            return {
                **base,
                "status": "ok" if resp.status_code < 500 else "error",
                "http_status": resp.status_code,
            }
        except Exception as exc:
            return {**base, "status": "error", "detail": str(exc)}

    if normalized == "telegram":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            return {**base, "status": "not_configured"}
        url = f"https://api.telegram.org/bot{token}/getMe"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url)
            return {
                **base,
                "status": "ok" if resp.status_code == 200 else "error",
                "http_status": resp.status_code,
            }
        except Exception as exc:
            return {**base, "status": "error", "detail": str(exc)}

    return {**base, "status": "unknown", "detail": "unsupported_channel"}


def get_gateway() -> InProcessGateway:
    global _gateway
    if _gateway is None:
        # Pass the configured workspace base to the gateway
        _gateway = InProcessGateway(workspace_base=WORKSPACES_DIR)
    return _gateway


def store_session(session: GatewaySession) -> None:
    _sessions[session.session_id] = session
    runtime = _session_runtime_snapshot(session.session_id)
    if "manager" in globals():
        runtime["active_connections"] = len(manager.session_connections.get(session.session_id, set()))
    runtime["lifecycle_state"] = _runtime_status_from_counters(runtime)
    _sync_runtime_metadata(session.session_id)


def get_session(session_id: str) -> Optional[GatewaySession]:
    return _sessions.get(session_id)


def _sanitize_session_id_or_400(session_id: str) -> str:
    try:
        return validate_session_id(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _sanitize_workspace_dir_or_400(workspace_dir: Optional[str]) -> Optional[str]:
    try:
        return resolve_workspace_dir(
            WORKSPACES_DIR,
            workspace_dir,
            allow_external=allow_external_workspaces_from_env(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _session_policy(session: GatewaySession) -> dict:
    policy = load_session_policy(
        session.workspace_dir,
        session_id=session.session_id,
        user_id=session.user_id,
    )
    save_session_policy(session.workspace_dir, policy)
    return policy


def _policy_metadata_snapshot(policy: dict[str, Any]) -> dict[str, Any]:
    memory = normalize_memory_policy(policy.get("memory") if isinstance(policy, dict) else None)
    return {
        "autonomy_mode": policy.get("autonomy_mode"),
        "identity_mode": policy.get("identity_mode"),
        "tool_profile": policy.get("tool_profile"),
        "memory_enabled": memory.get("enabled"),
        "memory_session_enabled": memory.get("sessionMemory"),
        "memory_sources": memory.get("sources", []),
        "memory_scope": memory.get("scope"),
    }


def _mark_run_cancel_requested(run_id: Optional[str], reason: str) -> Optional[str]:
    if not run_id:
        return None
    try:
        import universal_agent.main as main_module
        from universal_agent.durable.state import request_run_cancel

        if main_module.runtime_db_conn:
            request_run_cancel(main_module.runtime_db_conn, run_id, reason)
            logger.info("Marked run %s as cancel_requested", run_id)
    except Exception as cancel_err:
        logger.warning("Failed to mark run as cancelled: %s", cancel_err)
    return run_id


async def _cancel_session_execution(session_id: str, reason: str, run_id: Optional[str] = None) -> dict:
    if not run_id:
        session = get_session(session_id)
        if session:
            run_id = session.metadata.get("run_id")

    cancelled_task = await _cancel_execution_task(
        session_id,
        timeout_seconds=session_cancel_wait_seconds(),
    )

    if not cancelled_task:
        # Fallback for stale turns/tasks that predate task tracking or failed to unwind.
        state = _session_turn_snapshot(session_id)
        active_turn_id = str(state.get("active_turn_id") or "")
        if active_turn_id:
            active_record = state.get("turns", {}).get(active_turn_id) if isinstance(state.get("turns"), dict) else {}
            run_source = _normalize_run_source(
                active_record.get("run_source") if isinstance(active_record, dict) else None
            )
            async with _session_turn_lock(session_id):
                _finalize_turn(session_id, active_turn_id, TURN_STATUS_CANCELLED, error_message=reason)
            _decrement_session_active_runs(session_id, run_source=run_source)

    marked_run_id = _mark_run_cancel_requested(run_id, reason)

    await manager.broadcast(
        session_id,
        {
            "type": "cancelled",
            "data": {
                "reason": reason,
                "run_id": marked_run_id,
                "session_id": session_id,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    _add_notification(
        kind="cancelled",
        title="Session Cancelled",
        message=reason,
        session_id=session_id,
        severity="warning",
        metadata={"source": "ops"},
    )

    return {
        "status": "cancel_requested",
        "session_id": session_id,
        "run_id": marked_run_id,
        "reason": reason,
        "task_cancelled": cancelled_task,
    }


def is_user_allowed(user_id: str) -> bool:
    """Check if user_id is in the allowlist (if active)."""
    if not ALLOWED_USERS:
        return True
    if user_id in ALLOWED_USERS:
        return True

    # Always allow local system/service accounts so their sessions can be viewed in the UI
    system_users = {
        "webhook",
        "user_ui",
        "user_cli",
        "ops_tutorial_review",
        "cron_system",
        "ops:system-configuration-agent",
    }
    if user_id in system_users or user_id.startswith("cron:") or user_id.startswith("worker_") or user_id.startswith("vp."):
        return True

    # Support numeric Telegram IDs in allowlist (e.g., "7843395933")
    if user_id.startswith("telegram_"):
        telegram_id = user_id.split("telegram_", 1)[1]
        return telegram_id in ALLOWED_USERS
    return False


def _require_ops_auth(request: Request, token_override: Optional[str] = None) -> None:
    global _OPS_LEGACY_DEPRECATION_EMITTED
    if not OPS_TOKEN and not OPS_JWT_SECRET:
        return
    header = request.headers.get("authorization", "")
    token = ""
    if header.lower().startswith("bearer "):
        token = header.split(" ", 1)[1].strip()
    if not token:
        token = request.headers.get("x-ua-ops-token", "").strip()
    if not token and token_override is not None:
        token = str(token_override).strip()
    verdict = validate_ops_token(
        token,
        jwt_secret=OPS_JWT_SECRET,
        legacy_token=OPS_TOKEN,
        allow_legacy=OPS_AUTH_ALLOW_LEGACY,
    )
    if not verdict.ok:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if verdict.mode == "legacy" and not _OPS_LEGACY_DEPRECATION_EMITTED:
        logger.warning(
            "Legacy UA_OPS_TOKEN auth accepted. Migrate callers to JWT Bearer tokens "
            "from /auth/ops-token."
        )
        _OPS_LEGACY_DEPRECATION_EMITTED = True


def _require_ops_token_issuance_auth(request: Request) -> None:
    token = _extract_auth_token_from_headers(request.headers)
    if SESSION_API_TOKEN and token == SESSION_API_TOKEN:
        return
    if OPS_TOKEN and token == OPS_TOKEN:
        return
    if not SESSION_API_TOKEN and not OPS_TOKEN:
        raise HTTPException(
            status_code=503,
            detail=(
                "Ops token issuance requires bootstrap credentials. Configure UA_INTERNAL_API_TOKEN "
                "or UA_OPS_TOKEN."
            ),
        )
    raise HTTPException(status_code=401, detail="Unauthorized")


def _require_delegation_publish_allowed() -> None:
    if not _FACTORY_POLICY.can_publish_delegations:
        raise HTTPException(
            status_code=403,
            detail=f"Delegation publish is disabled for FACTORY_ROLE={_FACTORY_POLICY.role}",
        )


def _require_delegation_consume_allowed() -> None:
    if not _FACTORY_POLICY.can_listen_delegations:
        raise HTTPException(
            status_code=403,
            detail=f"Delegation consume is disabled for FACTORY_ROLE={_FACTORY_POLICY.role}",
        )


def _require_headquarters_role_for_fleet() -> None:
    if _FACTORY_POLICY.role != FactoryRole.HEADQUARTERS.value:
        raise HTTPException(
            status_code=403,
            detail=(
                "Factory registration endpoints are only available for "
                f"FACTORY_ROLE={FactoryRole.HEADQUARTERS.value}"
            ),
        )


def _extract_auth_token_from_headers(headers: Any) -> str:
    header = str(headers.get("authorization", "")).strip()
    token = ""
    if header.lower().startswith("bearer "):
        token = header.split(" ", 1)[1].strip()
    if not token:
        token = str(headers.get("x-ua-internal-token", "")).strip()
    if not token:
        token = str(headers.get("x-ua-ops-token", "")).strip()
    return token


def _session_api_auth_required() -> bool:
    return _DEPLOYMENT_PROFILE == "vps" or bool(SESSION_API_TOKEN)


def _require_session_api_auth(request: Request) -> None:
    if not _session_api_auth_required():
        return
    if not SESSION_API_TOKEN:
        raise HTTPException(status_code=503, detail="Session API token is not configured.")
    token = _extract_auth_token_from_headers(request.headers)
    if token != SESSION_API_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _require_youtube_ingest_auth(request: Request) -> None:
    explicit_token = (os.getenv("UA_YOUTUBE_INGEST_TOKEN") or "").strip()
    if explicit_token:
        token = _extract_auth_token_from_headers(request.headers)
        if token != explicit_token:
            raise HTTPException(status_code=401, detail="Unauthorized")
        return
    _require_session_api_auth(request)


async def _require_session_ws_auth(websocket: WebSocket) -> bool:
    if not _session_api_auth_required():
        return True
    if not SESSION_API_TOKEN:
        await websocket.close(code=1011, reason="Session API token is not configured.")
        return False
    token = _extract_auth_token_from_headers(websocket.headers)
    if token != SESSION_API_TOKEN:
        await websocket.close(code=4401, reason="Unauthorized")
        return False
    return True


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
        _set_session_connections(session_id, len(self.session_connections.get(session_id, set())))
        
        logger.info(f"Gateway WebSocket connected: {connection_id} (session: {session_id})")

    def disconnect(self, connection_id: str, session_id: str):
        if connection_id in self.active_connections:
            del self.active_connections[connection_id]
            logger.info(f"Gateway WebSocket disconnected: {connection_id}")
            
        if session_id in self.session_connections:
            self.session_connections[session_id].discard(connection_id)
            if not self.session_connections[session_id]:
                del self.session_connections[session_id]
        _set_session_connections(session_id, len(self.session_connections.get(session_id, set())))

    def _session_id_for_connection(self, connection_id: str) -> Optional[str]:
        for session_id, connection_ids in self.session_connections.items():
            if connection_id in connection_ids:
                return session_id
        return None

    async def _send_text_with_timeout(self, websocket: WebSocket, payload: str) -> None:
        await asyncio.wait_for(
            websocket.send_text(payload),
            timeout=WS_SEND_TIMEOUT_SECONDS,
        )

    async def send_json(self, connection_id: str, data: dict, session_id: Optional[str] = None):
        if connection_id in self.active_connections:
            try:
                await self._send_text_with_timeout(
                    self.active_connections[connection_id],
                    json.dumps(data),
                )
                if session_id:
                    _record_session_event(session_id, str(data.get("type", "")))
            except asyncio.TimeoutError:
                _increment_metric("ws_send_timeouts")
                _increment_metric("ws_send_failures")
                stale_session = session_id or self._session_id_for_connection(connection_id)
                if stale_session:
                    self.disconnect(connection_id, stale_session)
                _increment_metric("ws_stale_evictions")
                logger.warning("Timed out sending websocket payload to %s", connection_id)
            except Exception as e:
                _increment_metric("ws_send_failures")
                stale_session = session_id or self._session_id_for_connection(connection_id)
                if stale_session:
                    self.disconnect(connection_id, stale_session)
                _increment_metric("ws_stale_evictions")
                logger.error(f"Failed to send to {connection_id}: {e}")

    async def broadcast(self, session_id: str, data: dict, exclude_connection_id: Optional[str] = None):
        """Send a message to all connections associated with a session_id."""
        _record_session_event(session_id, str(data.get("type", "")))
        if session_id not in self.session_connections:
            return

        payload = json.dumps(data)
        # Snapshot the list to avoid runtime errors if connections drop during iteration
        targets = list(self.session_connections[session_id])
        stale_connections: list[str] = []
        
        for connection_id in targets:
            if connection_id == exclude_connection_id:
                continue
                
            if connection_id in self.active_connections:
                try:
                    await self._send_text_with_timeout(
                        self.active_connections[connection_id],
                        payload,
                    )
                except asyncio.TimeoutError:
                    _increment_metric("ws_send_timeouts")
                    _increment_metric("ws_send_failures")
                    stale_connections.append(connection_id)
                    logger.warning(
                        "Timed out broadcasting websocket payload to %s (session=%s)",
                        connection_id,
                        session_id,
                    )
                except Exception as e:
                    _increment_metric("ws_send_failures")
                    stale_connections.append(connection_id)
                    logger.error(f"Failed to broadcast to {connection_id}: {e}")

        for stale_connection in stale_connections:
            self.disconnect(stale_connection, session_id)
            _increment_metric("ws_stale_evictions")


manager = ConnectionManager()


# =============================================================================
# Lifespan
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _FACTORY_POLICY, _delegation_mission_bus
    bootstrap_state = bootstrap_runtime_environment(profile=_DEPLOYMENT_PROFILE)
    _FACTORY_POLICY = bootstrap_state.policy
    _refresh_ops_auth_config_from_env()
    _maybe_instrument_logfire_fastapi()

    logger.info("🚀 Universal Agent Gateway Server starting...")
    logger.info(f"📁 Workspaces: {WORKSPACES_DIR}")
    logger.info("🏭 Factory role resolved: %s (gateway_mode=%s)", _FACTORY_POLICY.role, _FACTORY_POLICY.gateway_mode)

    _delegation_mission_bus = None
    _delegation_metrics["connected"] = False
    _delegation_metrics["last_error"] = None
    if _delegation_bus_enabled and (_FACTORY_POLICY.can_publish_delegations or _FACTORY_POLICY.can_listen_delegations):
        try:
            redis_url = _redis_url_from_env()
            _delegation_mission_bus = RedisMissionBus.from_url(
                redis_url,
                stream_name=_delegation_bus_stream,
                consumer_group=_delegation_bus_group,
                dlq_stream=_delegation_bus_dlq_stream,
            )
            _delegation_mission_bus.ensure_group()
            _delegation_metrics["connected"] = True
            logger.info(
                "📬 Delegation Redis bus connected stream=%s group=%s",
                _delegation_bus_stream,
                _delegation_bus_group,
            )
        except Exception as exc:
            _delegation_mission_bus = None
            _delegation_metrics["last_error"] = str(exc)
            logger.warning("Delegation Redis bus unavailable; falling back to http queue: %s", exc)

    _register_local_factory_presence()
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    
    # Initialize runtime database (required by ProcessTurnAdapter -> setup_session)
    import universal_agent.main as main_module
    db_path = get_runtime_db_path()
    logger.info(f"📊 Connecting to runtime DB: {db_path}")
    main_module.runtime_db_conn = connect_runtime_db(db_path)
    # Enable WAL mode for concurrent access (CLI + gateway can coexist)
    main_module.runtime_db_conn.execute("PRAGMA journal_mode=WAL")
    # Keep timeout aligned with durable.db connect_runtime_db() defaults to reduce
    # transient lock errors during concurrent cron + VP runtime activity.
    main_module.runtime_db_conn.execute("PRAGMA busy_timeout=60000")
    ensure_schema(main_module.runtime_db_conn)
    _ensure_activity_schema(main_module.runtime_db_conn)
    _activity_prune_old(main_module.runtime_db_conn)
    persisted_notifications = _load_notifications_from_activity_store(_notifications_max)
    if persisted_notifications:
        _notifications[:] = list(reversed(persisted_notifications))
    
    # Load budget config (defined in main.py)
    main_module.budget_config = main_module.load_budget_config()
    
    # Initialize Heartbeat Service
    global _heartbeat_service, _cron_service, _ops_service, _hooks_service
    global _vp_event_bridge_task, _vp_event_bridge_stop_event
    global _todoist_chron_reconcile_task, _todoist_chron_reconcile_stop_event
    if HEARTBEAT_ENABLED:
        logger.info("💓 Heartbeat System ENABLED")
        _heartbeat_service = HeartbeatService(
            get_gateway(),
            manager,
            system_event_provider=_drain_system_events,
            event_sink=_emit_heartbeat_event,
        )
        await _heartbeat_service.start()
    else:
        logger.info("💤 Heartbeat System DISABLED (feature flag)")

    if CRON_ENABLED:
        logger.info("⏱️ Chron Service ENABLED")
        _cron_service = CronService(
            get_gateway(),
            WORKSPACES_DIR,
            event_sink=_emit_cron_event,
            wake_callback=_cron_wake_callback,
            system_event_callback=_enqueue_system_event,
        )
        await _cron_service.start()
        try:
            _ensure_autonomous_daily_briefing_job()
        except Exception:
            logger.exception("Failed ensuring autonomous daily briefing chron job")
        if TODOIST_CHRON_RECONCILE_ENABLED:
            try:
                initial = _reconcile_todoist_chron_mappings(
                    remove_stale=TODOIST_CHRON_RECONCILE_REMOVE_STALE,
                    dry_run=False,
                )
                logger.info(
                    "🔁 Todoist<->Chron reconciliation startup run: inspected=%s relinked=%s removed=%s ok=%s",
                    initial.get("inspected"),
                    initial.get("relinked"),
                    initial.get("removed"),
                    initial.get("ok"),
                )
            except Exception:
                logger.exception("Failed Todoist<->Chron reconciliation startup run")
            _todoist_chron_reconcile_stop_event = asyncio.Event()
            _todoist_chron_reconcile_task = asyncio.create_task(_todoist_chron_reconcile_loop())
            logger.info(
                "🔁 Todoist<->Chron reconciliation loop enabled (interval=%.1fs, remove_stale=%s)",
                TODOIST_CHRON_RECONCILE_INTERVAL_SECONDS,
                TODOIST_CHRON_RECONCILE_REMOVE_STALE,
            )
        else:
            logger.info("⏸️ Todoist<->Chron reconciliation loop disabled (UA_TODOIST_CHRON_RECONCILE_ENABLED)")
    else:
        logger.info("⏲️ Chron Service DISABLED (feature flag)")
    
    # Always enabled Ops Service
    _ops_service = OpsService(get_gateway(), WORKSPACES_DIR)
    
    # Initialize Hooks Service
    _hooks_service = HooksService(
        get_gateway(),
        turn_admitter=_admit_hook_turn,
        turn_finalizer=_finalize_hook_turn,
        run_counter_start=_increment_session_active_runs,
        run_counter_finish=_decrement_session_active_runs,
        notification_sink=_hook_notification_sink,
    )
    logger.info("🪝 Hooks Service Initialized")
    try:
        recovered = await _hooks_service.recover_interrupted_youtube_sessions(WORKSPACES_DIR)
    except Exception:
        logger.exception("Failed recovering interrupted youtube hook sessions on startup")
    else:
        if recovered > 0:
            logger.warning("🔁 Recovered %d interrupted youtube hook session(s) on startup", recovered)
        else:
            logger.info("🔁 No interrupted youtube hook sessions required recovery")

    if _scheduling_projection.enabled:
        _scheduling_projection.seed_from_runtime()
        logger.info("📈 Scheduling projection enabled (event-driven chron projection path)")

    if _vp_event_bridge_enabled:
        _vp_event_bridge_prime_cursor_to_latest()

    try:
        reconciled = _reconcile_stale_vp_missions_on_startup()
    except Exception:
        logger.exception("Failed reconciling stale running VP missions on startup")
    else:
        if not _vp_stale_reconcile_enabled:
            logger.info("⏸️ VP stale-mission reconciliation disabled (UA_VP_STALE_RECONCILE_ENABLED)")
        elif reconciled > 0:
            logger.warning("🧹 Reconciled %d stale running VP mission(s) on startup", reconciled)
        else:
            logger.info("🧹 No stale running VP missions detected on startup")

    if _vp_event_bridge_enabled:
        _vp_event_bridge_stop_event = asyncio.Event()
        _vp_event_bridge_task = asyncio.create_task(_vp_event_bridge_loop())
        logger.info(
            "🔁 VP event bridge enabled (interval=%.2fs, cursor=%s)",
            _vp_event_bridge_interval_seconds,
            _vp_event_bridge_last_rowid,
        )
    else:
        logger.info("⏸️ VP event bridge disabled (UA_VP_EVENT_BRIDGE_ENABLED)")

    yield
    
    # Cleanup
    if _vp_event_bridge_stop_event is not None:
        _vp_event_bridge_stop_event.set()
    if _vp_event_bridge_task is not None:
        try:
            await _vp_event_bridge_task
        except Exception:
            pass
        _vp_event_bridge_task = None
    _vp_event_bridge_stop_event = None
    if _todoist_chron_reconcile_stop_event is not None:
        _todoist_chron_reconcile_stop_event.set()
    if _todoist_chron_reconcile_task is not None:
        try:
            await _todoist_chron_reconcile_task
        except Exception:
            pass
        _todoist_chron_reconcile_task = None
    _todoist_chron_reconcile_stop_event = None
    if _heartbeat_service:
        await _heartbeat_service.stop()
    if _cron_service:
        await _cron_service.stop()
        
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

_LOGFIRE_FASTAPI_INSTRUMENTED = False


def _maybe_instrument_logfire_fastapi() -> None:
    global _LOGFIRE_FASTAPI_INSTRUMENTED
    if _LOGFIRE_FASTAPI_INSTRUMENTED:
        return
    try:
        import logfire as _logfire_gw

        if os.getenv("LOGFIRE_TOKEN") or os.getenv("LOGFIRE_WRITE_TOKEN"):
            _logfire_gw.instrument_fastapi(app)
            _LOGFIRE_FASTAPI_INSTRUMENTED = True
            logger.info("✅ Logfire FastAPI instrumentation enabled for gateway server")
    except Exception as _lf_err:
        logger.debug("Logfire FastAPI instrumentation not available: %s", _lf_err)


_maybe_instrument_logfire_fastapi()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def enforce_factory_role_http_surface(request: Request, call_next):
    if _FACTORY_POLICY.gateway_mode == "health_only":
        if request.url.path not in _LOCAL_WORKER_ALLOWED_PATHS:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        f"Route disabled for FACTORY_ROLE={_FACTORY_POLICY.role}; "
                        "LOCAL_WORKER exposes health-only API surface."
                    )
                },
            )
    return await call_next(request)


@app.middleware("http")
async def enforce_ops_auth_http_surface(request: Request, call_next):
    if request.url.path.startswith("/api/v1/ops/"):
        try:
            _require_ops_auth(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


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
            "signals_ingest": "/api/v1/signals/ingest",
        },
    }


@app.get("/api/v1/hooks/readyz")
async def hooks_readyz():
    """
    No-auth hooks readiness endpoint.

    This is intended for health probes and operational checks so they do not
    require hook auth tokens and do not trigger 401 noise.
    """
    if not _hooks_service:
        return {
            "ready": False,
            "service_initialized": False,
            "hooks_enabled": False,
            "reason": "hooks_service_not_initialized",
        }

    status = _hooks_service.readiness_status()
    status["service_initialized"] = True
    return status


@app.post("/auth/ops-token", response_model=OpsTokenIssueResponse)
async def issue_ops_token_endpoint(request: Request, payload: OpsTokenIssueRequest):
    if _FACTORY_POLICY.role != FactoryRole.HEADQUARTERS.value:
        raise HTTPException(
            status_code=403,
            detail=f"/auth/ops-token is only available for FACTORY_ROLE={FactoryRole.HEADQUARTERS.value}",
        )
    _require_ops_token_issuance_auth(request)
    if not OPS_JWT_SECRET:
        raise HTTPException(status_code=503, detail="UA_OPS_JWT_SECRET is not configured")
    subject = str(payload.subject or "ops").strip() or "ops"
    token, expires_at = issue_ops_jwt(
        jwt_secret=OPS_JWT_SECRET,
        subject=subject,
        ttl_seconds=3600,
    )
    return OpsTokenIssueResponse(
        token=token,
        ttl_seconds=3600,
        expires_at=expires_at.isoformat(),
    )


@app.post("/api/v1/hooks/{subpath:path}")
async def hooks_endpoint(request: Request, subpath: str):
    if not _hooks_service:
        raise HTTPException(status_code=503, detail="Hooks service not initialized")
    return await _hooks_service.handle_request(request, subpath)


@app.post("/api/v1/signals/ingest")
async def signals_ingest_endpoint(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid_json"})
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"ok": False, "error": "invalid_json"})
    status_code, body = process_signals_ingest_payload(payload, dict(request.headers))
    if status_code in {200, 207} and _hooks_service:
        dispatch_count = 0
        analytics_dispatch_count = 0
        analytics_throttled_count = 0
        for event in extract_valid_events(payload):
            manual_payload = to_manual_youtube_payload(event)
            if manual_payload:
                ok, _reason = await _hooks_service.dispatch_internal_payload(
                    subpath="youtube/manual",
                    payload=manual_payload,
                    headers={"x-csi-source": "signals_ingest"},
                )
                if ok:
                    dispatch_count += 1
                continue

            analytics_action = to_csi_analytics_action(event)
            if not analytics_action:
                continue

            is_throttled, _remaining = _csi_dispatch_is_throttled(event.source, event.event_type)
            if is_throttled:
                analytics_throttled_count += 1
                continue

            ok, _reason = await _hooks_service.dispatch_internal_action(analytics_action)
            if not ok:
                continue
            _csi_record_dispatch(event.source, event.event_type)
            analytics_dispatch_count += 1

            policy = _csi_event_notification_policy(event)
            event_type_norm = str(event.event_type or "").strip().lower()
            notification_kind = "csi_insight"
            title = f"CSI Insight: {event.event_type or 'Report'} from {event.source or 'Unknown'}"
            if event_type_norm == "delivery_health_regression":
                notification_kind = "csi_delivery_health_regression"
                title = "CSI Delivery Health Regression Detected"
            elif event_type_norm == "delivery_health_recovered":
                notification_kind = "csi_delivery_health_recovered"
                title = "CSI Delivery Health Recovered"
            elif event_type_norm == "delivery_reliability_slo_breached":
                notification_kind = "csi_delivery_reliability_slo_breached"
                title = "CSI Reliability SLO Breached"
            elif event_type_norm == "delivery_reliability_slo_recovered":
                notification_kind = "csi_delivery_reliability_slo_recovered"
                title = "CSI Reliability SLO Recovered"
            elif event_type_norm == "delivery_health_auto_remediation_failed":
                notification_kind = "csi_delivery_health_auto_remediation_failed"
                title = "CSI Auto-Remediation Failed"
            elif event_type_norm == "delivery_health_auto_remediation_succeeded":
                notification_kind = "csi_delivery_health_auto_remediation_succeeded"
                title = "CSI Auto-Remediation Succeeded"
            message = analytics_action.get("message", "No content")

            # Packet 14: normalize traceability fields on every CSI notification
            _session_key = str(analytics_action.get("session_key") or "").strip()
            subject_obj = event.subject if isinstance(event.subject, dict) else {}
            _report_key = str(subject_obj.get("report_key") or "").strip()
            _artifact_paths = subject_obj.get("artifact_paths") if isinstance(subject_obj.get("artifact_paths"), dict) else None
            _source = str(event.source or "").strip()

            # Packet 16: compute report quality score
            _quality_result: dict[str, Any] | None = None
            try:
                from universal_agent.csi_quality_score import score_report_quality
                _source_mix: dict[str, int] = {}
                for opp in (subject_obj.get("opportunities") or []):
                    if isinstance(opp, dict) and isinstance(opp.get("source_mix"), dict):
                        for k, v in opp["source_mix"].items():
                            _source_mix[k] = _source_mix.get(k, 0) + int(v or 0)
                if not _source_mix and _source:
                    _source_mix[_source] = 1
                _quality_result = score_report_quality(
                    subject=subject_obj,
                    source_mix=_source_mix,
                )
            except Exception:
                pass

            metadata = {
                "event_type": event.event_type,
                "event_id": event.event_id,
                "source": _source,
                "session_key": _session_key or None,
                "report_key": _report_key or None,
                "artifact_paths": _artifact_paths,
                "quality": _quality_result,
                "notification_policy": {
                    "high_value": bool(policy.get("high_value")),
                    "has_anomaly": bool(policy.get("has_anomaly")),
                },
            }
            if isinstance(event.subject, dict):
                if _artifact_paths:
                    md_path = _artifact_paths.get("markdown")
                    if md_path:
                        message += f"\n\nReport Artifact: {md_path}"
                if event_type_norm in {"delivery_health_regression", "delivery_health_recovered"}:
                    metadata["delivery_health_status"] = str(event.subject.get("status") or "")
                    metadata["failing_sources"] = event.subject.get("failing_sources")
                    metadata["degraded_sources"] = event.subject.get("degraded_sources")
                    remediation = event.subject.get("remediation") if isinstance(event.subject.get("remediation"), dict) else {}
                    steps = remediation.get("steps") if isinstance(remediation.get("steps"), list) else []
                    if steps:
                        metadata["remediation_steps"] = steps[:8]
                        first_command = ""
                        for step in steps:
                            if isinstance(step, dict):
                                first_command = str(step.get("runbook_command") or "").strip()
                                if first_command:
                                    break
                        if first_command:
                            metadata["primary_runbook_command"] = first_command
                elif event_type_norm in {"delivery_health_auto_remediation_failed", "delivery_health_auto_remediation_succeeded"}:
                    metadata["delivery_health_status"] = str(event.subject.get("health_status") or "")
                    metadata["auto_remediation_status"] = str(event.subject.get("status") or "")
                    metadata["executed_actions"] = event.subject.get("executed_actions")
                    metadata["skipped_actions"] = event.subject.get("skipped_actions")
                    executed = event.subject.get("executed_actions")
                    if isinstance(executed, list):
                        first_command = ""
                        for action in executed:
                            if isinstance(action, dict):
                                result = action.get("result") if isinstance(action.get("result"), dict) else {}
                                maybe = str(result.get("runbook_command") or "").strip()
                                if maybe:
                                    first_command = maybe
                                    break
                        if first_command:
                            metadata["primary_runbook_command"] = first_command
                elif event_type_norm in {"delivery_reliability_slo_breached", "delivery_reliability_slo_recovered"}:
                    metadata["slo_status"] = str(event.subject.get("status") or "")
                    metadata["target_day_utc"] = str(event.subject.get("target_day_utc") or "")
                    metrics = event.subject.get("metrics") if isinstance(event.subject.get("metrics"), dict) else {}
                    if metrics:
                        metadata["slo_metrics"] = metrics
                    thresholds = event.subject.get("thresholds") if isinstance(event.subject.get("thresholds"), dict) else {}
                    if thresholds:
                        metadata["slo_thresholds"] = thresholds
                    top_root_causes = (
                        event.subject.get("top_root_causes")
                        if isinstance(event.subject.get("top_root_causes"), list)
                        else []
                    )
                    if top_root_causes:
                        metadata["top_root_causes"] = top_root_causes[:3]
                        first_command = ""
                        for cause in top_root_causes:
                            if isinstance(cause, dict):
                                maybe = str(cause.get("runbook_command") or "").strip()
                                if maybe:
                                    first_command = maybe
                                    break
                        if first_command:
                            metadata["primary_runbook_command"] = first_command

            full_signal_message = (
                "Received new CSI signal. Review in the CSI dashboard tab or Todoist.\n\n"
                f"{message}"
            )
            if bool(policy.get("is_digest")):
                _csi_emit_digest_notification(event, full_signal_message)
            else:
                _add_notification(
                    kind=notification_kind,
                    title=title,
                    message=full_signal_message,
                    summary=_activity_summary_text(message, max_chars=260),
                    full_message=full_signal_message,
                    severity=str(policy.get("severity") or "info"),
                    requires_action=bool(policy.get("requires_action")),
                    metadata=metadata,
                    created_at=event.occurred_at or event.received_at,
                )
            _csi_emit_specialist_synthesis(event, full_signal_message)
            loop_state = _csi_update_specialist_loop(event, full_signal_message)
            if loop_state.get("updated"):
                for alert in loop_state.get("quality_alerts", []) if isinstance(loop_state.get("quality_alerts"), list) else []:
                    if not isinstance(alert, dict):
                        continue
                    kind = str(alert.get("kind") or "").strip()
                    if not kind:
                        continue
                    topic_key = str((alert.get("metadata") or {}).get("topic_key") or loop_state.get("topic_key") or "").strip()
                    cooldown_seconds = int(_csi_specialist_alert_cooldown_minutes) * 60
                    if _has_recent_notification(
                        kind=kind,
                        metadata_match={"topic_key": topic_key} if topic_key else None,
                        within_seconds=max(60, cooldown_seconds),
                    ):
                        continue
                    metadata = alert.get("metadata") if isinstance(alert.get("metadata"), dict) else {}
                    metadata = {
                        **metadata,
                        "topic_key": topic_key or metadata.get("topic_key"),
                        "confidence_method": loop_state.get("confidence_method"),
                        "confidence_target": loop_state.get("confidence_target"),
                        "confidence_score": loop_state.get("confidence_score"),
                    }
                    _add_notification(
                        kind=kind,
                        title=str(alert.get("title") or "CSI Specialist Quality Alert"),
                        message=str(alert.get("message") or "CSI specialist quality guardrail triggered."),
                        severity=str(alert.get("severity") or "warning"),
                        requires_action=True,
                        metadata=metadata,
                        created_at=event.occurred_at or event.received_at,
                    )

                loop_status = str(loop_state.get("status") or "")
                if loop_status == "closed":
                    _add_notification(
                        kind="csi_specialist_confidence_reached",
                        title="CSI Specialist Confidence Reached",
                        message=(
                            f"Loop {loop_state.get('topic_label')} reached confidence "
                            f"{loop_state.get('confidence_score')} (target {loop_state.get('confidence_target')})."
                        ),
                        severity="success",
                        requires_action=False,
                        metadata={
                            "topic_key": loop_state.get("topic_key"),
                            "confidence_score": loop_state.get("confidence_score"),
                            "confidence_target": loop_state.get("confidence_target"),
                            "confidence_method": loop_state.get("confidence_method"),
                            "confidence_evidence": loop_state.get("confidence_evidence"),
                            "events_count": loop_state.get("events_count"),
                            "low_signal_streak": loop_state.get("low_signal_streak"),
                            "suppressed_until": loop_state.get("suppressed_until"),
                        },
                        created_at=event.occurred_at or event.received_at,
                    )
                elif loop_status == "budget_exhausted":
                    _add_notification(
                        kind="csi_specialist_followup_budget_exhausted",
                        title="CSI Specialist Follow-up Budget Exhausted",
                        message=(
                            f"Loop {loop_state.get('topic_label')} exhausted follow-up budget "
                            f"before reaching target confidence."
                        ),
                        severity="warning",
                        requires_action=True,
                        metadata={
                            "topic_key": loop_state.get("topic_key"),
                            "confidence_score": loop_state.get("confidence_score"),
                            "confidence_target": loop_state.get("confidence_target"),
                            "confidence_method": loop_state.get("confidence_method"),
                            "confidence_evidence": loop_state.get("confidence_evidence"),
                            "events_count": loop_state.get("events_count"),
                            "low_signal_streak": loop_state.get("low_signal_streak"),
                            "suppressed_until": loop_state.get("suppressed_until"),
                        },
                        created_at=event.occurred_at or event.received_at,
                    )
                elif bool(loop_state.get("request_followup")) and _hooks_service:
                    followup_payload = {
                        "kind": "agent",
                        "name": "CSITrendFollowUpRequest",
                        "session_key": "csi_trend_specialist",
                        "to": "trend-specialist",
                        "message": str(loop_state.get("followup_message") or ""),
                        "timeout_seconds": int(
                            max(60, _env_int("UA_CSI_ANALYTICS_HOOK_TIMEOUT_SECONDS", 420))
                        ),
                    }
                    follow_ok, follow_reason = await _hooks_service.dispatch_internal_action(followup_payload)
                    _add_notification(
                        kind="csi_specialist_followup_requested" if follow_ok else "csi_specialist_followup_request_failed",
                        title="CSI Specialist Follow-up Requested" if follow_ok else "CSI Specialist Follow-up Request Failed",
                        message=(
                            f"Loop {loop_state.get('topic_label')} follow-up dispatch "
                            f"{'succeeded' if follow_ok else f'failed: {follow_reason}'}."
                        ),
                        severity="info" if follow_ok else "warning",
                        requires_action=not follow_ok,
                        metadata={
                            "topic_key": loop_state.get("topic_key"),
                            "confidence_score": loop_state.get("confidence_score"),
                            "confidence_target": loop_state.get("confidence_target"),
                            "confidence_method": loop_state.get("confidence_method"),
                            "confidence_evidence": loop_state.get("confidence_evidence"),
                            "follow_up_budget_remaining": loop_state.get("follow_up_budget_remaining"),
                            "source_mix": loop_state.get("source_mix"),
                            "dispatch_reason": follow_reason,
                            "low_signal_streak": loop_state.get("low_signal_streak"),
                            "suppressed_until": loop_state.get("suppressed_until"),
                        },
                        created_at=event.occurred_at or event.received_at,
                    )

            # Create Todoist task when credentials are configured. Missing credentials
            # are treated as a sync skip, not a system error.
            if not bool(policy.get("todoist_sync")):
                continue
            has_api_key = bool((os.getenv("TODOIST_API_KEY") or "").strip())
            has_api_token = bool((os.getenv("TODOIST_API_TOKEN") or "").strip())
            if not (has_api_key or has_api_token):
                logger.info(
                    "Skipping CSI Todoist sync because credentials are not configured "
                    "(TODOIST_API_TOKEN/TODOIST_API_KEY missing)."
                )
                if not _has_recent_notification(
                    kind="system_notice",
                    metadata_match={"integration": "todoist", "reason": "credentials_missing"},
                    within_seconds=1800,
                ):
                    _add_notification(
                        kind="system_notice",
                        title="Todoist Sync Skipped",
                        message=(
                            "CSI signal received, but Todoist sync is disabled because "
                            "TODOIST_API_TOKEN/TODOIST_API_KEY is not configured."
                        ),
                        severity="info",
                        metadata={"integration": "todoist", "reason": "credentials_missing"},
                    )
            else:
                try:
                    from universal_agent.services.todoist_service import TodoService

                    todoist = TodoService()
                    todoist.create_task(
                        content=title,
                        description=f"{message}\n\nReview in CSI Dashboard Tab.",
                        labels=["CSI"],
                        priority="high",
                        project_key="csi",
                    )
                except Exception as exc:
                    logger.exception("Failed to create Todoist task for CSI signal")
                    debug_info = (
                        f"TODOIST_API_KEY={'found' if has_api_key else 'missing'} "
                        f"TODOIST_API_TOKEN={'found' if has_api_token else 'missing'}"
                    )
                    _add_notification(
                        kind="system_error",
                        title="Todoist Sync Failed",
                        message=(
                            "Could not sync CSI task to Todoist. "
                            "Check Todoist credentials (TODOIST_API_TOKEN or TODOIST_API_KEY) "
                            f"and taxonomy. Error: {exc}. Debug: {debug_info}"
                        ),
                        severity="error",
                        metadata={"integration": "todoist", "reason": "task_sync_failed"},
                    )

        if dispatch_count > 0:
            body["internal_dispatches"] = dispatch_count
        if analytics_dispatch_count > 0:
            body["analytics_internal_dispatches"] = analytics_dispatch_count
        if analytics_throttled_count > 0:
            body["analytics_throttled"] = analytics_throttled_count
    return JSONResponse(status_code=status_code, content=body)


@app.post("/api/v1/youtube/ingest")
async def youtube_ingest_endpoint(request: Request, payload: YouTubeIngestRequest):
    """
    Local worker endpoint for transcript ingestion.

    Intended usage:
    - VPS control-plane forwards ingestion requests over Tailscale/reverse tunnel.
    - Local worker performs YouTube transcript extraction from a residential IP.
    """
    _require_youtube_ingest_auth(request)

    video_url, video_id = normalize_video_target(payload.video_url, payload.video_id)
    if not video_url:
        raise HTTPException(status_code=400, detail="video_url or valid video_id is required")

    result = await asyncio.to_thread(
        ingest_youtube_transcript,
        video_url=video_url,
        video_id=video_id,
        language=(payload.language or "en").strip() or "en",
        timeout_seconds=max(5, min(int(payload.timeout_seconds or 120), 600)),
        max_chars=max(5_000, min(int(payload.max_chars or 180_000), 800_000)),
        min_chars=max(20, min(int(payload.min_chars or 160), 5000)),
    )
    result["request_id"] = (payload.request_id or "").strip() or None
    result["worker_profile"] = _DEPLOYMENT_PROFILE
    return result


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
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
        "db_status": db_status,
        "db_error": db_error,
        "runtime_path": os.getenv("PATH", ""),
        "runtime_tools": runtime_tool_status(),
        "deployment_profile": _deployment_profile_defaults(),
    }


@app.get("/api/v1/factory/capabilities")
async def factory_capabilities(request: Request):
    _require_ops_auth(request)
    return {
        "factory": _factory_capabilities_payload(),
        "delegation": dict(_delegation_metrics),
    }


@app.post("/api/v1/factory/registrations")
async def register_factory_presence(request: Request, payload: FactoryRegistrationRequest):
    _require_ops_auth(request)
    _require_headquarters_role_for_fleet()
    body = payload.model_dump(exclude_none=True)
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    metadata = dict(metadata)
    metadata.setdefault("remote_host", request.client.host if request.client else "")
    body["metadata"] = metadata
    record = _upsert_factory_registration(body, source="api_registration")
    return {"ok": True, "registration": record}


@app.get("/api/v1/factory/registrations")
async def list_factory_registrations(
    request: Request,
    limit: int = 200,
    registration_status: str = "",
):
    _require_ops_auth(request)
    _require_headquarters_role_for_fleet()
    clamped_limit = max(1, min(int(limit), 1000))
    status_filter = str(registration_status or "").strip().lower()
    with _factory_registration_lock:
        rows = [dict(record) for record in _factory_registrations.values()]
    if status_filter:
        rows = [
            row
            for row in rows
            if str(row.get("registration_status") or "").strip().lower() == status_filter
        ]
    rows.sort(key=lambda row: str(row.get("last_seen_at") or ""), reverse=True)
    return {
        "registrations": rows[:clamped_limit],
        "count": len(rows),
        "headquarters_factory_id": _FACTORY_ID,
    }


@app.get("/api/artifacts")
async def list_artifacts(path: str = ""):
    """List files under the persistent artifacts root."""
    payload = _list_directory_under_root(ARTIFACTS_DIR, path)
    payload["artifacts_root"] = str(ARTIFACTS_DIR)
    return payload


@app.get("/api/artifacts/files/{file_path:path}")
async def get_artifact_file(file_path: str):
    """Get file content from the persistent artifacts root."""
    return _read_file_from_root(ARTIFACTS_DIR, file_path)


@app.get("/api/v1/dashboard/metrics/coder-vp")
async def dashboard_coder_vp_metrics(
    vp_id: str = "vp.coder.primary",
    mission_limit: int = 20,
    event_limit: int = 100,
):
    vp_identifier = (vp_id or "").strip()
    if not vp_identifier:
        raise HTTPException(status_code=400, detail="vp_id is required")

    clamped_mission_limit = max(1, min(int(mission_limit), 500))
    clamped_event_limit = max(1, min(int(event_limit), 1000))
    try:
        metrics = _vp_metrics_snapshot(
            vp_id=vp_identifier,
            mission_limit=clamped_mission_limit,
            event_limit=clamped_event_limit,
            storage_lane="coder",
        )
        return {"status": "ok", "metrics": metrics}
    except HTTPException as exc:
        # Keep dashboard summary surfaces resilient when runtime DB is unavailable.
        return {"status": "unavailable", "detail": str(exc.detail), "metrics": None}


@app.post("/api/v1/sessions", response_model=CreateSessionResponse)
async def create_session(request: CreateSessionRequest, http_request: Request):
    _require_session_api_auth(http_request)
    if _DEPLOYMENT_PROFILE == "vps" and not str(request.user_id or "").strip():
        raise HTTPException(status_code=400, detail="user_id is required in vps profile")
    # 1. Enforce Allowlist
    final_user_id = resolve_user_id(request.user_id)
    if not is_user_allowed(final_user_id):
        logger.warning(f"⛔ Access Denied: User '{final_user_id}' not in allowlist.")
        raise HTTPException(status_code=403, detail="Access denied: User not allowed.")

    workspace_dir = _sanitize_workspace_dir_or_400(request.workspace_dir)
    gateway = get_gateway()
    try:
        session = await gateway.create_session(
            user_id=final_user_id,
            workspace_dir=workspace_dir,
        )
        session.metadata["user_id"] = session.user_id
        policy = _session_policy(session)
        session.metadata["policy"] = _policy_metadata_snapshot(policy)
        store_session(session)
        _increment_metric("sessions_created")
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
async def list_sessions(request: Request):
    _require_session_api_auth(request)
    if _ops_service:
        summaries = _ops_service.list_sessions(status_filter="all")
        return {
            "sessions": [
                SessionSummaryResponse(
                    session_id=str(item.get("session_id") or ""),
                    workspace_dir=str(item.get("workspace_dir") or ""),
                    status=str(item.get("status") or "unknown"),
                    user_id=str(item.get("owner") or "") or None,
                    metadata=item,
                ).model_dump()
                for item in summaries
            ]
        }

    gateway = get_gateway()
    summaries = gateway.list_sessions()
    in_memory = {}
    for summary in summaries:
        session = get_session(summary.session_id)
        if session:
            in_memory[summary.session_id] = session.user_id
    return {
        "sessions": [
            SessionSummaryResponse(
                session_id=s.session_id,
                workspace_dir=s.workspace_dir,
                status=s.status,
                user_id=(
                    (s.metadata.get("user_id") if isinstance(s.metadata, dict) else None)
                    or in_memory.get(s.session_id)
                ),
                metadata=s.metadata,
            ).model_dump()
            for s in summaries
        ]
    }


@app.get("/api/v1/dashboard/summary")
async def dashboard_summary():
    _apply_notification_snooze_expiry()
    _apply_activity_snooze_expiry()
    sessions_total = 0
    if _ops_service:
        try:
            sessions_total = len(_ops_service.list_sessions())
        except Exception:
            sessions_total = len(_sessions)
    else:
        sessions_total = len(_sessions)

    if _session_runtime:
        active_sessions = sum(
            1
            for runtime in _session_runtime.values()
            if str(runtime.get("lifecycle_state")) == SESSION_STATE_RUNNING
            or int(runtime.get("active_connections", 0)) > 0
        )
    else:
        active_sessions = sum(1 for s in _sessions.values() if s)
    pending_approvals = len(list_approvals(status="pending"))
    unread_notifications = 0
    total_notifications = 0
    try:
        counters = _query_activity_event_counters(
            event_class="notification",
            status_value=None,
            apply_default_window=False,
        )
        unread_notifications = int(((counters.get("totals") or {}).get("unread") or 0))
        total_notifications = int(((counters.get("totals") or {}).get("total") or 0))
    except Exception:
        unread_notifications = sum(
            1 for item in _notifications if str(item.get("status", "new")).lower() in {"new", "pending"}
        )
        total_notifications = len(_notifications)
    cron_total = 0
    cron_enabled = 0
    if _cron_service:
        jobs = _cron_service.list_jobs()
        cron_total = len(jobs)
        cron_enabled = sum(1 for job in jobs if bool(getattr(job, "enabled", False)))
    reconcile_metrics = (
        _scheduling_runtime_metrics_snapshot()
        .get("todoist_chron_reconciliation", {})
    )

    return {
        "sessions": {
            "active": active_sessions,
            "total": sessions_total,
        },
        "approvals": {
            "pending": pending_approvals,
            "total": len(list_approvals()),
        },
        "cron": {
            "total": cron_total,
            "enabled": cron_enabled,
        },
        "todoist_chron_reconciliation": {
            "runs": int((reconcile_metrics or {}).get("runs", 0) or 0),
            "last_run_at": (reconcile_metrics or {}).get("last_run_at"),
            "last_error": (reconcile_metrics or {}).get("last_error"),
            "last_result": (reconcile_metrics or {}).get("last_result") or {},
        },
        "notifications": {
            "unread": unread_notifications,
            "total": total_notifications,
        },
        "deployment_profile": _deployment_profile_defaults(),
    }


@app.get("/api/v1/dashboard/csi/reports")
async def dashboard_csi_reports(limit: int = 15, include_suppressed: bool = False):
    def _reports_from_notifications(max_items: int) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        for item in reversed(_notifications):
            kind = str(item.get("kind") or "").strip().lower()
            if not kind.startswith("csi"):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            event_type = str(metadata.get("event_type") or kind).strip() or kind
            title = str(item.get("title") or "CSI Signal").strip()
            message = str(item.get("message") or "").strip()
            created_at = _normalize_notification_timestamp(item.get("created_at"))
            markdown = f"### {title}\n\n{message}" if message else f"### {title}"
            reports.append(
                {
                    "id": str(item.get("id") or f"ntf_{len(reports)+1}"),
                    "report_type": event_type,
                    "report_data": {"markdown_content": markdown},
                    "usage": None,
                    "created_at": created_at,
                    "source": "notification_fallback",
                }
            )
            if len(reports) >= max_items:
                break
        return reports

    def _reports_from_hook_sessions(max_items: int) -> list[dict[str, Any]]:
        if not _ops_service:
            return []
        try:
            summaries = _ops_service.list_sessions(status_filter="all")
        except Exception:
            return []
        csi_rows: list[dict[str, Any]] = []
        for row in summaries:
            session_id = str(row.get("session_id") or "").strip()
            if not session_id.lower().startswith("session_hook_csi_"):
                continue
            csi_rows.append(row)

        def _sort_key(row: dict[str, Any]) -> tuple[float, str]:
            ts = _parse_iso_timestamp(row.get("last_activity"))
            if ts is None:
                ts = _parse_iso_timestamp(row.get("last_modified"))
            epoch = ts.timestamp() if ts else 0.0
            return (epoch, str(row.get("session_id") or ""))

        csi_rows.sort(key=_sort_key, reverse=True)
        reports: list[dict[str, Any]] = []
        for row in csi_rows[:max_items]:
            session_id = str(row.get("session_id") or "").strip()
            suffix = session_id.removeprefix("session_hook_csi_")
            report_type = suffix or "csi_event"
            description = str(row.get("description") or "").strip()
            owner = str(row.get("owner") or "unknown").strip()
            created_at = _normalize_notification_timestamp(
                str(row.get("last_activity") or row.get("last_modified") or _utc_now_iso())
            )
            markdown_lines = [
                f"### CSI Session: {session_id}",
                f"- event: `{report_type}`",
                f"- owner: `{owner}`",
            ]
            if description:
                markdown_lines.extend(["", description])
            reports.append(
                {
                    "id": f"session:{session_id}",
                    "report_type": report_type,
                    "report_data": {"markdown_content": "\n".join(markdown_lines)},
                    "usage": None,
                    "created_at": created_at,
                    "source": "session_fallback",
                    "session_id": session_id,
                }
            )
        return reports

    def _reports_from_specialist_activity(max_items: int) -> list[dict[str, Any]]:
        try:
            rows = _query_activity_events(
                limit=max(max_items * 5, 60),
                source_domain="csi",
                apply_default_window=False,
            )
        except Exception:
            return []
        wanted_kinds = {"csi_specialist_hourly_synthesis", "csi_specialist_daily_rollup"}
        out: list[dict[str, Any]] = []
        for item in rows:
            kind = str(item.get("kind") or "").strip().lower()
            if kind not in wanted_kinds:
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            report_class = str(metadata.get("report_class") or "").strip() or (
                "specialist_daily" if kind.endswith("daily_rollup") else "specialist_hourly"
            )
            full_message = str(item.get("full_message") or item.get("summary") or "").strip()
            if not full_message:
                full_message = f"CSI specialist report ({kind})"
            out.append(
                {
                    "id": str(item.get("id") or ""),
                    "report_type": kind,
                    "report_class": report_class,
                    "window_hours": float(metadata.get("window_hours") or (24 if report_class == "specialist_daily" else 1)),
                    "source_mix": metadata.get("source_mix") if isinstance(metadata.get("source_mix"), dict) else {},
                    "report_data": {"markdown_content": full_message},
                    "usage": None,
                    "created_at": str(item.get("created_at_utc") or _utc_now_iso()),
                    "window_start_utc": None,
                    "window_end_utc": None,
                    "model_name": "trend-specialist",
                    "metadata": {
                        "report_key": str(metadata.get("report_key") or ""),
                        "kind": kind,
                    },
                }
            )
            if len(out) >= max_items:
                break
        return out

    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row is not None

    def _parse_usage(*, prompt: Any, completion: Any, total: Any) -> dict[str, int] | None:
        if prompt is None and completion is None and total is None:
            return None
        prompt_tokens = int(prompt or 0)
        completion_tokens = int(completion or 0)
        total_tokens = int(total or (prompt_tokens + completion_tokens))
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _window_hours(start_raw: Any, end_raw: Any) -> Optional[float]:
        start = _parse_iso_timestamp(start_raw)
        end = _parse_iso_timestamp(end_raw)
        if not start or not end:
            return None
        seconds = max(0.0, end.timestamp() - start.timestamp())
        return round(seconds / 3600.0, 2)

    def _source_mix_for_report(report_type: str, report_data: dict[str, Any]) -> dict[str, int]:
        totals = report_data.get("totals") if isinstance(report_data.get("totals"), dict) else {}
        total_items = int(
            report_data.get("total_items")
            or totals.get("items")
            or report_data.get("items")
            or 0
        )
        lowered = report_type.lower()
        if "reddit" in lowered:
            return {"reddit": total_items}
        if "rss" in lowered or lowered in {"daily", "emerging", "trend"}:
            return {"rss": total_items}
        if lowered == "hourly_report_product":
            return {
                "insight_reports": int(report_data.get("insight_report_count") or 0),
                "trend_report": 1 if bool(report_data.get("has_trend_report")) else 0,
            }
        if lowered == "opportunity_bundle":
            mix = report_data.get("source_mix")
            if isinstance(mix, dict):
                return {str(k): int(v or 0) for k, v in mix.items()}
            opportunities = report_data.get("opportunities")
            if isinstance(opportunities, list):
                aggregated: dict[str, int] = {}
                for item in opportunities:
                    if not isinstance(item, dict):
                        continue
                    item_mix = item.get("source_mix")
                    if not isinstance(item_mix, dict):
                        continue
                    for key, value in item_mix.items():
                        name = str(key or "").strip() or "unknown"
                        aggregated[name] = int(aggregated.get(name) or 0) + int(value or 0)
                if aggregated:
                    return aggregated
            quality = report_data.get("quality_summary")
            if isinstance(quality, dict):
                return {"signal_volume": int(quality.get("signal_volume") or 0)}
            return {"opportunities": int(len(report_data.get("opportunities") or []))}
        return {"items": total_items}

    def _theme_set(report_data: dict[str, Any]) -> set[str]:
        raw_themes = report_data.get("top_themes")
        if not isinstance(raw_themes, list):
            return set()
        out: set[str] = set()
        for item in raw_themes[:20]:
            if isinstance(item, dict):
                label = str(item.get("theme") or item.get("label") or item.get("name") or "").strip().lower()
            else:
                label = str(item or "").strip().lower()
            if label:
                out.add(label)
        return out

    def _report_class(report_type: str) -> str:
        lowered = report_type.lower()
        if "opportunity" in lowered:
            return "opportunity"
        if "daily" in lowered:
            return "daily"
        if "emerging" in lowered:
            return "emerging"
        if "product" in lowered:
            return "product"
        if "trend" in lowered:
            return "trend"
        return "insight"

    def _token_set(report_data: dict[str, Any]) -> set[str]:
        tokens: set[str] = set()
        tokens |= _theme_set(report_data)
        markdown = str(report_data.get("markdown_content") or "")
        for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]{2,}", markdown.lower()):
            if word in {"the", "and", "for", "with", "that", "this", "from", "report", "insight", "daily", "emerging"}:
                continue
            tokens.add(word)
            if len(tokens) >= 300:
                break
        return tokens

    def _entity_set(report_data: dict[str, Any]) -> set[str]:
        entities: set[str] = set()
        for key in ("top_channels", "top_subreddits", "top_sources", "top_themes"):
            value = report_data.get(key)
            if not isinstance(value, list):
                continue
            for item in value[:20]:
                if isinstance(item, dict):
                    label = str(
                        item.get("name")
                        or item.get("theme")
                        or item.get("channel")
                        or item.get("subreddit")
                        or item.get("label")
                        or ""
                    ).strip().lower()
                else:
                    label = str(item or "").strip().lower()
                if label:
                    entities.add(label)
        return entities

    def _apply_quality_gates(in_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not in_reports:
            return in_reports
        ordered = sorted(
            in_reports,
            key=lambda item: (_parse_iso_timestamp(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
            reverse=True,
        )
        daily = next((item for item in ordered if str(item.get("report_class") or "") == "daily"), None)
        emerging = next((item for item in ordered if str(item.get("report_class") or "") == "emerging"), None)
        if not daily or not emerging:
            for report in ordered:
                report.setdefault("quality_gate", {"status": "not_applicable"})
            return ordered
        daily_data = daily.get("report_data") if isinstance(daily.get("report_data"), dict) else {}
        emerging_data = emerging.get("report_data") if isinstance(emerging.get("report_data"), dict) else {}
        daily_tokens = _token_set(daily_data)
        emerging_tokens = _token_set(emerging_data)
        token_union = daily_tokens | emerging_tokens
        token_overlap = len(daily_tokens & emerging_tokens) / len(token_union) if token_union else 1.0
        divergence = round(1.0 - token_overlap, 3)
        novelty_ratio = round(
            (len(emerging_tokens - daily_tokens) / max(1, len(emerging_tokens))) if emerging_tokens else 0.0,
            3,
        )
        daily_entities = _entity_set(daily_data)
        emerging_entities = _entity_set(emerging_data)
        entity_union = daily_entities | emerging_entities
        entity_overlap = round(
            (len(daily_entities & emerging_entities) / len(entity_union)) if entity_union else 0.0,
            3,
        )

        status_value = "divergent"
        recommendation = "publish_both"
        if divergence < 0.12 and novelty_ratio < 0.2 and entity_overlap >= 0.65:
            status_value = "near_duplicate"
            recommendation = "suppress_emerging"
        elif divergence < 0.2:
            status_value = "high_overlap"
            recommendation = "review_overlap_note"
        elif len(token_union) < 8:
            status_value = "sparse_signal"
            recommendation = "collect_more_data"

        quality_gate = {
            "status": status_value,
            "recommendation": recommendation,
            "token_overlap_jaccard": round(token_overlap, 3),
            "novelty_ratio": novelty_ratio,
            "entity_overlap_ratio": entity_overlap,
        }
        daily["divergence_score"] = divergence
        daily["divergence_note"] = (
            "Low divergence: windows overlap heavily or signal volume is sparse."
            if divergence < 0.2
            else "Daily and emerging windows show meaningful divergence."
        )
        daily["quality_gate"] = quality_gate
        emerging["divergence_score"] = divergence
        emerging["divergence_note"] = daily["divergence_note"]
        emerging["quality_gate"] = quality_gate
        if status_value == "near_duplicate":
            emerging["suppressed"] = True

        for report in ordered:
            report.setdefault("quality_gate", {"status": "not_applicable"})
        if include_suppressed:
            return ordered
        return [item for item in ordered if not bool(item.get("suppressed"))]

    clamped_limit = max(1, min(int(limit), 50))
    db_detail: Optional[str] = None
    db_path = Path(os.getenv("CSI_DB_PATH", "/opt/universal_agent/CSI_Ingester/development/var/csi.db"))
    if db_path.exists():
        conn = None
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            reports: list[dict[str, Any]] = []

            if _table_exists(conn, "insight_reports"):
                for row in conn.execute(
                    "SELECT * FROM insight_reports ORDER BY created_at DESC LIMIT ?",
                    (max(clamped_limit * 3, 40),),
                ).fetchall():
                    report_json = _activity_json_loads_obj(row["report_json"], default={})
                    report_data = report_json if isinstance(report_json, dict) else {}
                    report_data["markdown_content"] = str(row["report_markdown"] or "")
                    report_type = str(row["report_type"] or "insight")
                    reports.append(
                        {
                            "id": int(row["id"]),
                            "report_type": report_type,
                            "report_class": _report_class(report_type),
                            "window_hours": _window_hours(row["window_start_utc"], row["window_end_utc"]),
                            "source_mix": _source_mix_for_report(report_type, report_data),
                            "report_data": report_data,
                            "usage": _parse_usage(
                                prompt=row["prompt_tokens"],
                                completion=row["completion_tokens"],
                                total=row["total_tokens"],
                            ),
                            "created_at": _normalize_notification_timestamp(row["created_at"]),
                            "window_start_utc": row["window_start_utc"],
                            "window_end_utc": row["window_end_utc"],
                            "model_name": row["model_name"],
                            "metadata": {"report_key": row["report_key"]},
                        }
                    )

            if _table_exists(conn, "trend_reports"):
                for row in conn.execute(
                    "SELECT * FROM trend_reports ORDER BY created_at DESC LIMIT ?",
                    (max(clamped_limit * 3, 40),),
                ).fetchall():
                    report_json = _activity_json_loads_obj(row["report_json"], default={})
                    report_data = report_json if isinstance(report_json, dict) else {}
                    report_data["markdown_content"] = str(row["report_markdown"] or "")
                    report_type = "rss_trend_report"
                    reports.append(
                        {
                            "id": int(row["id"]),
                            "report_type": report_type,
                            "report_class": "trend",
                            "window_hours": _window_hours(row["window_start_utc"], row["window_end_utc"]),
                            "source_mix": _source_mix_for_report(report_type, report_data),
                            "report_data": report_data,
                            "usage": _parse_usage(
                                prompt=row["prompt_tokens"],
                                completion=row["completion_tokens"],
                                total=row["total_tokens"],
                            ),
                            "created_at": _normalize_notification_timestamp(row["created_at"]),
                            "window_start_utc": row["window_start_utc"],
                            "window_end_utc": row["window_end_utc"],
                            "model_name": row["model_name"],
                            "metadata": {"report_key": row["report_key"]},
                        }
                    )

            if _table_exists(conn, "events"):
                for row in conn.execute(
                    """
                    SELECT event_id, occurred_at, subject_json
                    FROM events
                    WHERE event_type IN ('report_product_ready', 'opportunity_bundle_ready')
                    ORDER BY occurred_at DESC
                    LIMIT ?
                    """,
                    (max(clamped_limit * 2, 20),),
                ).fetchall():
                    subject = _activity_json_loads_obj(row["subject_json"], default={})
                    if not isinstance(subject, dict):
                        subject = {}
                    artifact_paths = subject.get("artifact_paths") if isinstance(subject.get("artifact_paths"), dict) else {}
                    report_type_value = str(subject.get("report_type") or "hourly_report_product")
                    if report_type_value == "opportunity_bundle":
                        quality_summary = subject.get("quality_summary") if isinstance(subject.get("quality_summary"), dict) else {}
                        opportunities = subject.get("opportunities") if isinstance(subject.get("opportunities"), list) else []
                        markdown = (
                            "## CSI Opportunity Bundle\n\n"
                            f"- generated_at_utc: `{subject.get('generated_at_utc')}`\n"
                            f"- window_hours: `{subject.get('window_hours')}`\n"
                            f"- confidence_method: `{subject.get('confidence_method')}`\n"
                            f"- opportunities: `{len(opportunities)}`\n"
                            f"- quality_summary: `{json.dumps(quality_summary, ensure_ascii=False)}`\n"
                        )
                        if opportunities:
                            markdown += "\n### Top Opportunities\n"
                            for item in opportunities[:5]:
                                title = str(item.get("title") or "").strip() or "Untitled opportunity"
                                confidence = item.get("confidence_score")
                                novelty = item.get("novelty_score")
                                markdown += f"- {title} (confidence={confidence}, novelty={novelty})\n"
                    else:
                        markdown = (
                            "## CSI Report Product\n\n"
                            f"- generated_at_utc: `{subject.get('generated_at_utc')}`\n"
                            f"- window_hours: `{subject.get('window_hours')}`\n"
                            f"- has_trend_report: `{subject.get('has_trend_report')}`\n"
                            f"- insight_report_count: `{subject.get('insight_report_count')}`\n"
                        )
                    if artifact_paths:
                        markdown += (
                            "\n### Artifacts\n"
                            f"- markdown: `{artifact_paths.get('markdown')}`\n"
                            f"- json: `{artifact_paths.get('json')}`\n"
                        )
                    reports.append(
                        {
                            "id": f"evt:{row['event_id']}",
                            "report_type": report_type_value,
                            "report_class": _report_class(report_type_value),
                            "window_hours": float(subject.get("window_hours") or 0),
                            "source_mix": _source_mix_for_report(
                                report_type_value,
                                subject,
                            ),
                            "report_data": {
                                "markdown_content": markdown,
                                "artifact_paths": artifact_paths,
                                "subject": subject,
                                "report_key": str(subject.get("report_key") or ""),
                            },
                            "usage": None,
                            "created_at": _normalize_notification_timestamp(row["occurred_at"]),
                            "window_start_utc": None,
                            "window_end_utc": None,
                            "model_name": None,
                            "metadata": {
                                "event_id": row["event_id"],
                                "report_key": str(subject.get("report_key") or ""),
                                "bundle_id": str(subject.get("bundle_id") or ""),
                            },
                        }
                    )

            specialist_reports = _reports_from_specialist_activity(max(clamped_limit, 10))
            if specialist_reports:
                reports.extend(specialist_reports)

            if reports:
                reports = _apply_quality_gates(reports)
                return {"status": "ok", "source": "csi_db_aggregated", "reports": reports[:clamped_limit]}
            db_detail = "CSI database is reachable but no report rows were found."
        except Exception as exc:
            db_detail = f"CSI database read failed: {exc}"
        finally:
            if conn is not None:
                conn.close()
    else:
        db_detail = f"CSI database not found at {db_path}."

    notification_reports = _reports_from_notifications(clamped_limit)
    specialist_reports = _reports_from_specialist_activity(clamped_limit)
    if specialist_reports:
        notification_reports = sorted(
            [*specialist_reports, *notification_reports],
            key=lambda item: (_parse_iso_timestamp(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
            reverse=True,
        )[:clamped_limit]
    if notification_reports:
        notification_reports = _apply_quality_gates(notification_reports)
        return {"status": "ok", "source": "notification_fallback", "detail": db_detail, "reports": notification_reports}

    session_reports = _reports_from_hook_sessions(clamped_limit)
    if session_reports:
        return {"status": "ok", "source": "session_fallback", "detail": db_detail, "reports": session_reports}

    return {"status": "ok", "source": "empty", "detail": db_detail, "reports": []}


@app.get("/api/v1/dashboard/csi/health")
async def dashboard_csi_health():
    db_path = Path(os.getenv("CSI_DB_PATH", "/opt/universal_agent/CSI_Ingester/development/var/csi.db"))
    if not db_path.exists():
        return {"status": "unavailable", "detail": f"CSI database not found at {db_path}"}
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        timezone_name = str(
            (os.getenv("UA_DEFAULT_TIMEZONE") or os.getenv("USER_TIMEZONE") or "America/Chicago")
        ).strip() or "America/Chicago"
        try:
            local_tz = ZoneInfo(timezone_name)
        except Exception:
            local_tz = timezone.utc
            timezone_name = "UTC"

        def _as_utc_dt(raw: Any) -> Optional[datetime]:
            parsed = _parse_iso_timestamp(raw)
            if parsed is None:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

        last_event_row = conn.execute(
            "SELECT occurred_at, event_type, source FROM events ORDER BY occurred_at DESC LIMIT 1"
        ).fetchone()
        undelivered = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM events WHERE delivered = 0 AND occurred_at >= datetime('now', '-24 hours')"
            ).fetchone()["c"]
            or 0
        )
        dlq_count = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM dead_letter WHERE created_at >= datetime('now', '-24 hours')"
            ).fetchone()["c"]
            or 0
        )
        delivery_targets: list[dict[str, Any]] = []
        delivery_attempts_total = 0
        delivery_attempts_failed = 0
        delivery_rows: list[sqlite3.Row] = []
        try:
            delivery_rows = conn.execute(
                """
                SELECT
                    target,
                    COUNT(*) AS attempts,
                    SUM(CASE WHEN delivered = 1 THEN 1 ELSE 0 END) AS delivered_count,
                    SUM(CASE WHEN delivered = 0 THEN 1 ELSE 0 END) AS failed_count
                FROM delivery_attempts
                WHERE attempted_at >= datetime('now', '-24 hours')
                GROUP BY target
                ORDER BY target ASC
                """
            ).fetchall()
        except Exception:
            delivery_rows = []
        if delivery_rows:
            for row in delivery_rows:
                attempts = int(row["attempts"] or 0)
                failed = int(row["failed_count"] or 0)
                delivered_count = int(row["delivered_count"] or 0)
                delivery_attempts_total += attempts
                delivery_attempts_failed += failed
                failure_ratio = round((failed / attempts), 4) if attempts > 0 else 0.0
                delivery_targets.append(
                    {
                        "target": str(row["target"] or "unknown"),
                        "attempts_last_24h": attempts,
                        "delivered_last_24h": delivered_count,
                        "failed_last_24h": failed,
                        "failure_ratio": failure_ratio,
                        "status": "ok" if failed == 0 else ("degraded" if failure_ratio < 0.25 else "failing"),
                    }
                )
        source_rows = conn.execute(
            """
            SELECT source, MAX(occurred_at) AS last_seen, COUNT(*) AS total
            FROM events
            WHERE occurred_at >= datetime('now', '-48 hours')
            GROUP BY source
            ORDER BY source ASC
            """
        ).fetchall()
        source_last6_rows = conn.execute(
            """
            SELECT source, COUNT(*) AS total
            FROM events
            WHERE occurred_at >= datetime('now', '-6 hours')
            GROUP BY source
            """
        ).fetchall()
        source_fail_rows = conn.execute(
            """
            SELECT source, COUNT(*) AS total
            FROM events
            WHERE occurred_at >= datetime('now', '-24 hours')
              AND (LOWER(event_type) LIKE '%fail%' OR LOWER(event_type) LIKE '%error%')
            GROUP BY source
            """
        ).fetchall()
        event_rows = conn.execute(
            """
            SELECT event_type, MAX(occurred_at) AS last_seen, COUNT(*) AS total
            FROM events
            WHERE occurred_at >= datetime('now', '-48 hours')
            GROUP BY event_type
            ORDER BY event_type ASC
            """
        ).fetchall()
        tracked_types = {
            "rss_trend_report": 180,
            "reddit_trend_report": 180,
            "rss_insight_emerging": 180,
            "report_product_ready": 180,
            "opportunity_bundle_ready": 180,
            "hourly_token_usage_report": 180,
            "category_quality_report": 240,
            "analysis_task_completed": 480,
        }
        event_last_seen = {str(row["event_type"]): str(row["last_seen"] or "") for row in event_rows}
        now_utc = datetime.now(timezone.utc)
        now_ts = now_utc.timestamp()
        stale: list[dict[str, Any]] = []
        for event_type, max_minutes in tracked_types.items():
            seen = event_last_seen.get(event_type)
            seen_dt = _as_utc_dt(seen) if seen else None
            seen_ts = seen_dt.timestamp() if seen_dt else None
            lag_minutes = None
            if seen_ts is not None:
                lag_minutes = round((now_ts - seen_ts) / 60.0, 2)
            if seen_ts is None or (lag_minutes is not None and lag_minutes > max_minutes):
                stale.append(
                    {
                        "event_type": event_type,
                        "last_seen": seen,
                        "lag_minutes": lag_minutes,
                        "expected_max_lag_minutes": max_minutes,
                    }
                )

        overnight_end_local = now_utc.astimezone(local_tz).replace(hour=8, minute=0, second=0, microsecond=0)
        if now_utc.astimezone(local_tz) < overnight_end_local:
            overnight_end_local -= timedelta(days=1)
        overnight_start_local = overnight_end_local.replace(hour=0, minute=0, second=0, microsecond=0)
        overnight_start_utc = overnight_start_local.astimezone(timezone.utc)
        overnight_end_utc = overnight_end_local.astimezone(timezone.utc)
        overnight_hours = max(1.0, (overnight_end_utc.timestamp() - overnight_start_utc.timestamp()) / 3600.0)

        overnight_rows = conn.execute(
            """
            SELECT event_type, COUNT(*) AS total
            FROM events
            WHERE occurred_at >= ? AND occurred_at < ?
            GROUP BY event_type
            """,
            (overnight_start_utc.isoformat(), overnight_end_utc.isoformat()),
        ).fetchall()
        overnight_counts = {str(row["event_type"]): int(row["total"] or 0) for row in overnight_rows}
        overnight_continuity: list[dict[str, Any]] = []
        for event_type, max_lag_minutes in tracked_types.items():
            expected = max(1, int(round((overnight_hours * 60.0) / float(max_lag_minutes))))
            observed = int(overnight_counts.get(event_type) or 0)
            missing = max(0, expected - observed)
            overnight_continuity.append(
                {
                    "event_type": event_type,
                    "expected_runs": expected,
                    "observed_runs": observed,
                    "missing_runs": missing,
                    "status": "ok" if missing == 0 else "missing",
                    "expected_max_lag_minutes": max_lag_minutes,
                }
            )

        source_last6 = {str(row["source"] or ""): int(row["total"] or 0) for row in source_last6_rows}
        source_failures = {str(row["source"] or ""): int(row["total"] or 0) for row in source_fail_rows}
        source_health: list[dict[str, Any]] = []
        for row in source_rows:
            source_name = str(row["source"] or "unknown")
            last_seen_raw = str(row["last_seen"] or "")
            last_seen_dt = _as_utc_dt(last_seen_raw)
            lag_minutes = round((now_ts - last_seen_dt.timestamp()) / 60.0, 2) if last_seen_dt else None
            last6 = int(source_last6.get(source_name) or 0)
            failures = int(source_failures.get(source_name) or 0)
            status_value = "ok"
            if failures > 0:
                status_value = "degraded"
            if lag_minutes is not None and lag_minutes > 360:
                status_value = "stale"
            source_health.append(
                {
                    "source": source_name,
                    "last_seen": last_seen_raw or None,
                    "lag_minutes": lag_minutes,
                    "events_last_48h": int(row["total"] or 0),
                    "events_last_6h": last6,
                    "throughput_per_hour_6h": round(float(last6) / 6.0, 2),
                    "failures_last_24h": failures,
                    "status": status_value,
                }
            )
        specialist_quality = {
            "total_loops": 0,
            "open_loops": 0,
            "suppressed_low_signal": 0,
            "budget_exhausted": 0,
            "closed_loops": 0,
            "stale_evidence_loops": 0,
            "evidence_model_loops": 0,
        }
        try:
            specialist_rows = conn.execute(
                "SELECT status, confidence_method, evidence_json FROM csi_specialist_loops"
            ).fetchall()
        except Exception:
            specialist_rows = []
        for row in specialist_rows:
            specialist_quality["total_loops"] += 1
            status_name = str(row["status"] or "").strip().lower()
            if status_name == "open":
                specialist_quality["open_loops"] += 1
            elif status_name == "suppressed_low_signal":
                specialist_quality["suppressed_low_signal"] += 1
            elif status_name == "budget_exhausted":
                specialist_quality["budget_exhausted"] += 1
            elif status_name == "closed":
                specialist_quality["closed_loops"] += 1
            if str(row["confidence_method"] or "").strip().lower() == "evidence_model":
                specialist_quality["evidence_model_loops"] += 1
            evidence = _activity_json_loads_obj(row["evidence_json"], default={})
            if isinstance(evidence, dict) and int(evidence.get("freshness_minutes") or 0) > int(_csi_specialist_stale_evidence_minutes):
                specialist_quality["stale_evidence_loops"] += 1
        return {
            "status": "ok",
            "db_path": str(db_path),
            "timezone": timezone_name,
            "last_event": dict(last_event_row) if last_event_row else None,
            "undelivered_last_24h": undelivered,
            "dead_letter_last_24h": dlq_count,
            "delivery_attempts_last_24h": delivery_attempts_total,
            "delivery_failures_last_24h": delivery_attempts_failed,
            "delivery_targets": delivery_targets,
            "sources": [dict(row) for row in source_rows],
            "source_health": source_health,
            "specialist_quality": specialist_quality,
            "event_types": [dict(row) for row in event_rows],
            "stale_pipelines": stale,
            "overnight_continuity": {
                "window_start_utc": overnight_start_utc.isoformat(),
                "window_end_utc": overnight_end_utc.isoformat(),
                "window_start_local": overnight_start_local.isoformat(),
                "window_end_local": overnight_end_local.isoformat(),
                "checks": overnight_continuity,
            },
        }
    except Exception as exc:
        return {"status": "error", "detail": f"Failed loading CSI health: {exc}"}
    finally:
        if conn is not None:
            conn.close()


@app.get("/api/v1/dashboard/csi/delivery-health")
async def dashboard_csi_delivery_health(
    window_hours: int = 24,
    stale_minutes: int = 240,
    max_failed_attempt_ratio: Optional[float] = None,
    min_rss_events: Optional[int] = None,
    min_reddit_events: Optional[int] = None,
    max_dlq_recent: Optional[int] = None,
):
    clamped_window = max(1, min(int(window_hours), 24 * 30))
    stale_threshold_minutes = max(30, min(int(stale_minutes), 24 * 60 * 7))
    tuned_max_failed_attempt_ratio = (
        max(0.0, min(float(max_failed_attempt_ratio), 1.0))
        if max_failed_attempt_ratio is not None
        else max(0.0, min(float(os.getenv("UA_CSI_DELIVERY_MAX_FAILED_ATTEMPT_RATIO", "0.2") or 0.2), 1.0))
    )
    tuned_min_rss_events = (
        max(0, int(min_rss_events))
        if min_rss_events is not None
        else max(0, int(os.getenv("UA_CSI_DELIVERY_MIN_RSS_EVENTS_24H", "1") or 1))
    )
    tuned_min_reddit_events = (
        max(0, int(min_reddit_events))
        if min_reddit_events is not None
        else max(0, int(os.getenv("UA_CSI_DELIVERY_MIN_REDDIT_EVENTS_24H", "1") or 1))
    )
    tuned_max_dlq_recent = (
        max(0, int(max_dlq_recent))
        if max_dlq_recent is not None
        else max(0, int(os.getenv("UA_CSI_DELIVERY_MAX_DLQ_RECENT", "0") or 0))
    )
    tuned_adapter_consecutive_failures = max(
        1,
        int(os.getenv("UA_CSI_DELIVERY_ADAPTER_CONSECUTIVE_FAILURES", "3") or 3),
    )
    db_path = Path(os.getenv("CSI_DB_PATH", "/opt/universal_agent/CSI_Ingester/development/var/csi.db"))
    if not db_path.exists():
        return {"status": "unavailable", "detail": f"CSI database not found at {db_path}"}
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        source_names = ["youtube_channel_rss", "reddit_discovery", "csi_analytics"]
        window_expr = f"-{clamped_window} hours"
        source_rows: list[dict[str, Any]] = []
        now_ts = time.time()
        adapter_health_map: dict[str, dict[str, Any]] = {}
        try:
            adapter_health_rows = conn.execute(
                """
                SELECT source_key, state_json, updated_at
                FROM source_state
                WHERE source_key LIKE 'adapter_health:%'
                ORDER BY updated_at DESC
                """
            ).fetchall()
        except Exception:
            adapter_health_rows = []
        adapter_health: list[dict[str, Any]] = []
        for row in adapter_health_rows:
            parsed = _activity_json_loads_obj(row["state_json"], default={})
            if not isinstance(parsed, dict):
                parsed = {}
            adapter_name = str(parsed.get("adapter") or str(row["source_key"] or "").replace("adapter_health:", ""))
            adapter_entry = {
                "adapter": adapter_name,
                "source_key": str(row["source_key"] or ""),
                "updated_at": str(row["updated_at"] or ""),
                "state": parsed,
            }
            adapter_health.append(adapter_entry)
            adapter_health_map[adapter_name] = adapter_entry

        expected_min_events_by_source = {
            "youtube_channel_rss": tuned_min_rss_events,
            "reddit_discovery": tuned_min_reddit_events,
            "csi_analytics": 0,
        }

        for source_name in source_names:
            event_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN delivered = 1 THEN 1 ELSE 0 END) AS delivered_total,
                    SUM(CASE WHEN delivered = 0 THEN 1 ELSE 0 END) AS undelivered_total,
                    MAX(created_at) AS last_event_at,
                    MAX(CASE WHEN delivered = 1 THEN created_at ELSE NULL END) AS last_delivered_at
                FROM events
                WHERE source = ?
                  AND created_at >= datetime('now', ?)
                """,
                (source_name, window_expr),
            ).fetchone()
            attempts_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS attempts,
                    SUM(CASE WHEN da.delivered = 1 THEN 1 ELSE 0 END) AS attempts_delivered,
                    SUM(CASE WHEN da.delivered = 0 THEN 1 ELSE 0 END) AS attempts_failed,
                    MAX(da.attempted_at) AS last_attempt_at
                FROM delivery_attempts da
                JOIN events e ON e.event_id = da.event_id
                WHERE e.source = ?
                  AND da.attempted_at >= datetime('now', ?)
                """,
                (source_name, window_expr),
            ).fetchone()
            try:
                dlq_row = conn.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM dead_letter
                    WHERE created_at >= datetime('now', ?)
                      AND json_extract(event_json, '$.source') = ?
                    """,
                    (window_expr, source_name),
                ).fetchone()
            except sqlite3.OperationalError:
                dlq_row = conn.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM dead_letter
                    WHERE created_at >= datetime('now', ?)
                      AND event_json LIKE ?
                    """,
                    (window_expr, f'%"source":"{source_name}"%'),
                ).fetchone()

            last_error_row = conn.execute(
                """
                SELECT da.error_class, da.error_detail, da.attempted_at
                FROM delivery_attempts da
                JOIN events e ON e.event_id = da.event_id
                WHERE e.source = ?
                  AND da.delivered = 0
                  AND da.attempted_at >= datetime('now', ?)
                ORDER BY da.attempted_at DESC, da.id DESC
                LIMIT 1
                """,
                (source_name, window_expr),
            ).fetchone()

            total = int((event_row["total"] if event_row is not None else 0) or 0)
            delivered_total = int((event_row["delivered_total"] if event_row is not None else 0) or 0)
            undelivered_total = int((event_row["undelivered_total"] if event_row is not None else 0) or 0)
            attempts = int((attempts_row["attempts"] if attempts_row is not None else 0) or 0)
            attempts_delivered = int((attempts_row["attempts_delivered"] if attempts_row is not None else 0) or 0)
            attempts_failed = int((attempts_row["attempts_failed"] if attempts_row is not None else 0) or 0)
            dlq_total = int((dlq_row["total"] if dlq_row is not None else 0) or 0)
            event_delivery_ratio = round((float(delivered_total) / float(total)), 4) if total > 0 else 0.0
            attempt_success_ratio = round((float(attempts_delivered) / float(attempts)), 4) if attempts > 0 else 0.0
            last_event_at = str(event_row["last_event_at"] or "") if event_row is not None else ""
            lag_minutes: Optional[float] = None
            parsed_last = _parse_iso_timestamp(last_event_at)
            if parsed_last is not None:
                if parsed_last.tzinfo is None:
                    parsed_last = parsed_last.replace(tzinfo=timezone.utc)
                lag_minutes = round((now_ts - parsed_last.timestamp()) / 60.0, 2)
            failed_attempt_ratio = round((float(attempts_failed) / float(attempts)), 4) if attempts > 0 else 0.0
            expected_min_events = int(expected_min_events_by_source.get(source_name) or 0)
            under_min_volume = total < expected_min_events
            stale = total == 0 or (lag_minutes is not None and lag_minutes > float(stale_threshold_minutes))
            high_failed_ratio = attempts > 0 and failed_attempt_ratio > float(tuned_max_failed_attempt_ratio)
            all_failed = attempts > 0 and attempts_failed == attempts
            dlq_exceeds = dlq_total > int(tuned_max_dlq_recent)
            status_value = "ok"
            if under_min_volume or stale:
                status_value = "stale"
            if attempts_failed > 0 or dlq_exceeds:
                status_value = "degraded"
            if all_failed or high_failed_ratio:
                status_value = "failing"
            adapter_state = (adapter_health_map.get(source_name) or {}).get("state")
            if not isinstance(adapter_state, dict):
                adapter_state = {}
            adapter_consecutive_failures = int(adapter_state.get("consecutive_failures") or 0)
            adapter_last_error = str(adapter_state.get("last_error") or "").strip()
            if adapter_consecutive_failures >= tuned_adapter_consecutive_failures:
                status_value = "failing"

            repair_hints: list[dict[str, Any]] = []
            if source_name == "youtube_channel_rss" and (under_min_volume or stale):
                repair_hints.append(
                    {
                        "code": "rss_source_stale_or_low_volume",
                        "severity": "warning",
                        "title": "RSS source volume below threshold",
                        "action": "Check RSS watchlist path and timer health; verify channel IDs are loading.",
                        "runbook_command": (
                            "systemctl status csi-ingester csi-rss-trend-report.timer csi-rss-telegram-digest.timer && "
                            "journalctl -u csi-ingester -n 120 --no-pager"
                        ),
                    }
                )
            if source_name == "reddit_discovery" and (under_min_volume or stale):
                repair_hints.append(
                    {
                        "code": "reddit_source_stale_or_low_volume",
                        "severity": "warning",
                        "title": "Reddit source volume below threshold",
                        "action": "Check reddit watchlist and endpoint reachability/fallback behavior.",
                        "runbook_command": (
                            "python3 /opt/universal_agent/CSI_Ingester/development/scripts/csi_reddit_probe.py "
                            "--watchlist-file /opt/universal_agent/CSI_Ingester/development/reddit_watchlist.json"
                        ),
                    }
                )
            if attempts_failed > 0 or high_failed_ratio:
                repair_hints.append(
                    {
                        "code": "delivery_failures_detected",
                        "severity": "critical" if status_value == "failing" else "warning",
                        "title": "CSI -> UA delivery failures detected",
                        "action": "Verify UA ingest endpoint auth and replay DLQ after repair.",
                        "runbook_command": (
                            "python3 /opt/universal_agent/CSI_Ingester/development/scripts/csi_replay_dlq.py "
                            "--db-path /opt/universal_agent/CSI_Ingester/development/var/csi.db --limit 100 --max-attempts 3"
                        ),
                    }
                )
            if dlq_exceeds:
                repair_hints.append(
                    {
                        "code": "dlq_backlog_exceeds_threshold",
                        "severity": "critical",
                        "title": "DLQ backlog exceeds threshold",
                        "action": "Inspect latest delivery errors and replay DLQ once root cause is fixed.",
                        "runbook_command": (
                            "sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db "
                            "\"select id,event_id,error_reason,created_at from dead_letter order by id desc limit 25;\""
                        ),
                    }
                )
            if adapter_consecutive_failures >= tuned_adapter_consecutive_failures:
                repair_hints.append(
                    {
                        "code": "adapter_consecutive_failures",
                        "severity": "critical",
                        "title": f"Adapter failing repeatedly ({adapter_consecutive_failures} consecutive failures)",
                        "action": "Inspect adapter errors and fix source-level connectivity/parsing.",
                        "runbook_command": "journalctl -u csi-ingester -n 200 --no-pager",
                        "detail": adapter_last_error,
                    }
                )

            source_rows.append(
                {
                    "source": source_name,
                    "status": status_value,
                    "window_hours": clamped_window,
                    "expected_min_events": expected_min_events,
                    "events_recent": total,
                    "delivered_recent": delivered_total,
                    "undelivered_recent": undelivered_total,
                    "event_delivery_ratio": event_delivery_ratio,
                    "delivery_attempts_recent": attempts,
                    "delivery_attempts_success": attempts_delivered,
                    "delivery_attempts_failed": attempts_failed,
                    "delivery_attempt_success_ratio": attempt_success_ratio,
                    "failed_attempt_ratio": failed_attempt_ratio,
                    "dlq_recent": dlq_total,
                    "last_event_at": last_event_at or None,
                    "last_delivered_at": str(event_row["last_delivered_at"] or "") if event_row is not None else None,
                    "last_attempt_at": str(attempts_row["last_attempt_at"] or "") if attempts_row is not None else None,
                    "last_error": {
                        "error_class": str(last_error_row["error_class"] or "") if last_error_row is not None else "",
                        "error_detail": str(last_error_row["error_detail"] or "") if last_error_row is not None else "",
                        "attempted_at": str(last_error_row["attempted_at"] or "") if last_error_row is not None else "",
                    },
                    "lag_minutes": lag_minutes,
                    "adapter_health": adapter_state,
                    "repair_hints": repair_hints,
                }
            )

        failing_sources = [row["source"] for row in source_rows if str(row.get("status") or "") == "failing"]
        stale_sources = [row["source"] for row in source_rows if str(row.get("status") or "") == "stale"]
        overall_status = "ok"
        if failing_sources:
            overall_status = "failing"
        elif stale_sources:
            overall_status = "degraded"
        elif any(str(row.get("status") or "") == "degraded" for row in source_rows):
            overall_status = "degraded"
        return {
            "status": "ok",
            "overall": {
                "status": overall_status,
                "window_hours": clamped_window,
                "stale_threshold_minutes": stale_threshold_minutes,
                "failing_sources": failing_sources,
                "stale_sources": stale_sources,
            },
            "tuning": {
                "max_failed_attempt_ratio": tuned_max_failed_attempt_ratio,
                "min_rss_events": tuned_min_rss_events,
                "min_reddit_events": tuned_min_reddit_events,
                "max_dlq_recent": tuned_max_dlq_recent,
                "adapter_consecutive_failures": tuned_adapter_consecutive_failures,
                "stale_threshold_minutes": stale_threshold_minutes,
                "window_hours": clamped_window,
            },
            "db_path": str(db_path),
            "sources": source_rows,
            "adapter_health": adapter_health,
        }
    except Exception as exc:
        return {"status": "error", "detail": f"Failed loading CSI delivery health: {exc}"}
    finally:
        if conn is not None:
            conn.close()


@app.get("/api/v1/dashboard/csi/reliability-slo")
async def dashboard_csi_reliability_slo():
    db_path = Path(os.getenv("CSI_DB_PATH", "/opt/universal_agent/CSI_Ingester/development/var/csi.db"))
    if not db_path.exists():
        return {"status": "unavailable", "detail": f"CSI database not found at {db_path}"}
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT source_key, state_json, updated_at
            FROM source_state
            WHERE source_key = 'runtime_canary:delivery_slo'
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return {
                "status": "ok",
                "slo": {
                    "status": "unknown",
                    "detail": "No daily reliability SLO evaluations recorded yet.",
                    "target_day_utc": None,
                    "last_checked_at": None,
                    "window_start_utc": None,
                    "window_end_utc": None,
                    "metrics": {},
                    "thresholds": {},
                    "top_root_causes": [],
                    "history": [],
                },
            }

        state = _activity_json_loads_obj(row["state_json"], default={})
        if not isinstance(state, dict):
            state = {}
        history = state.get("history") if isinstance(state.get("history"), list) else []
        top_root_causes = state.get("top_root_causes") if isinstance(state.get("top_root_causes"), list) else []
        return {
            "status": "ok",
            "slo": {
                "status": str(state.get("status") or "unknown"),
                "detail": str(state.get("last_transition_reason") or ""),
                "target_day_utc": str(state.get("target_day_utc") or ""),
                "last_checked_at": str(state.get("last_checked_at") or row["updated_at"] or ""),
                "window_start_utc": str(state.get("window_start_utc") or ""),
                "window_end_utc": str(state.get("window_end_utc") or ""),
                "metrics": state.get("metrics") if isinstance(state.get("metrics"), dict) else {},
                "thresholds": state.get("thresholds") if isinstance(state.get("thresholds"), dict) else {},
                "top_root_causes": [item for item in top_root_causes if isinstance(item, dict)][:3],
                "history": [item for item in history if isinstance(item, dict)][-30:],
            },
        }
    except Exception as exc:
        return {"status": "error", "detail": f"Failed loading CSI reliability SLO status: {exc}"}
    finally:
        if conn is not None:
            conn.close()


@app.get("/api/v1/dashboard/csi/opportunities")
async def dashboard_csi_opportunities(limit: int = 8):
    clamped_limit = max(1, min(int(limit), 50))
    db_path = Path(os.getenv("CSI_DB_PATH", "/opt/universal_agent/CSI_Ingester/development/var/csi.db"))
    if not db_path.exists():
        return {"status": "unavailable", "detail": f"CSI database not found at {db_path}", "bundles": [], "latest": None}
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        bundles: list[dict[str, Any]] = []
        table_row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='opportunity_bundles'"
        ).fetchone()
        if table_row is not None:
            rows = conn.execute(
                """
                SELECT
                    bundle_id,
                    report_key,
                    window_start_utc,
                    window_end_utc,
                    confidence_method,
                    quality_summary_json,
                    opportunities_json,
                    artifact_markdown_path,
                    artifact_json_path,
                    created_at
                FROM opportunity_bundles
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (clamped_limit,),
            ).fetchall()
            for row in rows:
                try:
                    quality_summary = json.loads(str(row["quality_summary_json"] or "{}"))
                    if not isinstance(quality_summary, dict):
                        quality_summary = {}
                except Exception:
                    quality_summary = {}
                try:
                    opportunities = json.loads(str(row["opportunities_json"] or "[]"))
                    if not isinstance(opportunities, list):
                        opportunities = []
                except Exception:
                    opportunities = []
                source_mix: dict[str, int] = {}
                for item in opportunities:
                    if not isinstance(item, dict):
                        continue
                    mix = item.get("source_mix")
                    if not isinstance(mix, dict):
                        continue
                    for key, value in mix.items():
                        k = str(key or "").strip() or "unknown"
                        source_mix[k] = int(source_mix.get(k) or 0) + int(value or 0)
                bundles.append(
                    {
                        "bundle_id": str(row["bundle_id"] or ""),
                        "report_key": str(row["report_key"] or ""),
                        "window_start_utc": str(row["window_start_utc"] or ""),
                        "window_end_utc": str(row["window_end_utc"] or ""),
                        "confidence_method": str(row["confidence_method"] or "heuristic"),
                        "quality_summary": quality_summary,
                        "opportunities": [item for item in opportunities if isinstance(item, dict)],
                        "source_mix": source_mix,
                        "artifact_paths": {
                            "markdown": str(row["artifact_markdown_path"] or ""),
                            "json": str(row["artifact_json_path"] or ""),
                        },
                        "created_at": _normalize_notification_timestamp(row["created_at"]),
                    }
                )
            return {
                "status": "ok",
                "source": "csi_db",
                "bundles": bundles,
                "latest": bundles[0] if bundles else None,
            }

        rows = conn.execute(
            """
            SELECT event_id, occurred_at, subject_json
            FROM events
            WHERE event_type = 'opportunity_bundle_ready'
            ORDER BY occurred_at DESC
            LIMIT ?
            """,
            (clamped_limit,),
        ).fetchall()
        for row in rows:
            subject = _activity_json_loads_obj(row["subject_json"], default={})
            if not isinstance(subject, dict):
                subject = {}
            opportunities = subject.get("opportunities")
            if not isinstance(opportunities, list):
                opportunities = []
            source_mix: dict[str, int] = {}
            for item in opportunities:
                if not isinstance(item, dict):
                    continue
                mix = item.get("source_mix")
                if not isinstance(mix, dict):
                    continue
                for key, value in mix.items():
                    k = str(key or "").strip() or "unknown"
                    source_mix[k] = int(source_mix.get(k) or 0) + int(value or 0)
            bundles.append(
                {
                    "bundle_id": str(subject.get("bundle_id") or row["event_id"] or ""),
                    "report_key": str(subject.get("report_key") or ""),
                    "window_start_utc": str(subject.get("window_start_utc") or ""),
                    "window_end_utc": str(subject.get("window_end_utc") or ""),
                    "confidence_method": str(subject.get("confidence_method") or "heuristic"),
                    "quality_summary": subject.get("quality_summary") if isinstance(subject.get("quality_summary"), dict) else {},
                    "opportunities": [item for item in opportunities if isinstance(item, dict)],
                    "source_mix": source_mix,
                    "artifact_paths": subject.get("artifact_paths") if isinstance(subject.get("artifact_paths"), dict) else {},
                    "created_at": _normalize_notification_timestamp(row["occurred_at"]),
                }
            )
        return {
            "status": "ok",
            "source": "events_fallback",
            "bundles": bundles,
            "latest": bundles[0] if bundles else None,
        }
    except Exception as exc:
        return {"status": "error", "detail": f"Failed loading CSI opportunities: {exc}", "bundles": [], "latest": None}
    finally:
        if conn is not None:
            conn.close()


@app.get("/api/v1/dashboard/csi/specialist-loops")
async def dashboard_csi_specialist_loops(limit: int = 50, status: Optional[str] = None):
    loops = _list_csi_specialist_loops(limit=limit, status_filter=status)
    return {"status": "ok", "loops": loops}


@app.post("/api/v1/dashboard/csi/specialist-loops/{topic_key}/action")
async def dashboard_csi_specialist_loop_action(
    topic_key: str,
    payload: CSISpecialistLoopActionRequest,
    request: Request,
):
    action = str(payload.action or "").strip().lower()
    if not action:
        raise HTTPException(status_code=400, detail="action is required")
    actor = _activity_actor_from_request(request)
    note = str(payload.note or "").strip() or None

    if action == "request_followup":
        result = await _csi_operator_request_followup(
            topic_key=topic_key,
            actor=actor,
            note=note,
            trigger="operator_manual_action",
        )
        loop = result.get("loop") if isinstance(result.get("loop"), dict) else {}
        if bool(result.get("ok")):
            _add_notification(
                kind="csi_specialist_followup_requested",
                title="CSI Specialist Follow-up Requested",
                message=f"Loop {loop.get('topic_label')} follow-up dispatch succeeded.",
                severity="info",
                requires_action=False,
                metadata={
                    "topic_key": loop.get("topic_key"),
                    "confidence_score": loop.get("confidence_score"),
                    "confidence_target": loop.get("confidence_target"),
                    "confidence_method": loop.get("confidence_method"),
                    "follow_up_budget_remaining": loop.get("follow_up_budget_remaining"),
                    "trigger": "operator_manual_action",
                },
            )
            return {"ok": True, "action": action, "loop": loop, "reason": result.get("reason")}
        _add_notification(
            kind="csi_specialist_followup_request_failed",
            title="CSI Specialist Follow-up Request Failed",
            message=f"Loop {loop.get('topic_label') or topic_key} follow-up dispatch failed: {result.get('reason')}",
            severity="warning",
            requires_action=True,
            metadata={"topic_key": topic_key, "reason": result.get("reason"), "trigger": "operator_manual_action"},
        )
        raise HTTPException(status_code=502, detail=str(result.get("reason") or "follow-up dispatch failed"))

    loop = _apply_csi_specialist_loop_action(
        topic_key=topic_key,
        action=action,
        actor=actor,
        note=note,
        follow_up_budget=payload.follow_up_budget,
    )
    return {"ok": True, "action": action, "loop": loop}


@app.post("/api/v1/dashboard/csi/specialist-loops/triage")
async def dashboard_csi_specialist_loop_triage(payload: CSISpecialistLoopTriageRequest, request: Request):
    max_items = max(1, min(int(payload.max_items or 50), 500))
    apply_changes = bool(payload.apply)
    request_followup = bool(payload.request_followup)
    actor = _activity_actor_from_request(request)
    note = str(payload.note or "").strip() or None

    loops = _list_csi_specialist_loops(limit=max_items)
    candidates: list[dict[str, Any]] = []
    for loop in loops:
        topic_key = str(loop.get("topic_key") or "").strip()
        status_value = str(loop.get("status") or "").strip().lower()
        confidence_score = float(loop.get("confidence_score") or 0.0)
        confidence_target = float(loop.get("confidence_target") or _csi_specialist_confidence_target)
        remaining = int(loop.get("follow_up_budget_remaining") or 0)
        suppressed_until = str(loop.get("suppressed_until") or "").strip()
        suppressed_expired = False
        if suppressed_until:
            suppressed_dt = _parse_iso_timestamp(suppressed_until)
            if suppressed_dt is not None:
                if suppressed_dt.tzinfo is None:
                    suppressed_dt = suppressed_dt.replace(tzinfo=timezone.utc)
                suppressed_expired = suppressed_dt.timestamp() <= time.time()
            else:
                suppressed_expired = True

        recommendation = ""
        reason = ""
        if status_value == "suppressed_low_signal" and suppressed_expired:
            recommendation = "unsuppress"
            reason = "suppression window expired"
        elif status_value == "budget_exhausted" and confidence_score < confidence_target:
            recommendation = "reset_budget"
            reason = "budget exhausted before target confidence"
        elif status_value == "open" and remaining <= 0 and confidence_score < confidence_target:
            recommendation = "reset_budget"
            reason = "open loop has zero budget"

        if not recommendation:
            continue
        candidates.append(
            {
                "topic_key": topic_key,
                "topic_label": loop.get("topic_label"),
                "before_status": status_value or "open",
                "recommendation": recommendation,
                "reason": reason,
            }
        )

    applied: list[dict[str, Any]] = []
    followups: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for item in candidates:
        topic_key = str(item.get("topic_key") or "")
        recommendation = str(item.get("recommendation") or "")
        try:
            if apply_changes:
                loop = _apply_csi_specialist_loop_action(
                    topic_key=topic_key,
                    action=recommendation,
                    actor=actor,
                    note=note or f"triage:{item.get('reason')}",
                )
            else:
                loop = _get_csi_specialist_loop(topic_key) or {}
            applied.append(
                {
                    **item,
                    "applied": apply_changes,
                    "after_status": str(loop.get("status") or item.get("before_status") or "open"),
                    "follow_up_budget_remaining": int(loop.get("follow_up_budget_remaining") or 0),
                }
            )
            can_follow = (
                apply_changes
                and request_followup
                and str(loop.get("status") or "").strip().lower() == "open"
                and int(loop.get("follow_up_budget_remaining") or 0) > 0
            )
            if can_follow:
                follow = await _csi_operator_request_followup(
                    topic_key=topic_key,
                    actor=actor,
                    note=note or f"triage-followup:{item.get('reason')}",
                    trigger="operator_triage",
                )
                followups.append(
                    {
                        "topic_key": topic_key,
                        "ok": bool(follow.get("ok")),
                        "reason": str(follow.get("reason") or ""),
                        "status": str((follow.get("loop") or {}).get("status") or loop.get("status") or "open"),
                    }
                )
        except Exception as exc:
            errors.append({"topic_key": topic_key, "error": str(exc)})

    if apply_changes:
        _add_notification(
            kind="csi_specialist_triage_run",
            title="CSI Specialist Triage Completed",
            message=(
                f"Triage reviewed {len(loops)} loops. "
                f"Applied {len(applied)} remediation action(s), "
                f"{len(followups)} follow-up dispatch(es), "
                f"{len(errors)} error(s)."
            ),
            severity="info" if not errors else "warning",
            requires_action=bool(errors),
            metadata={
                "total_loops_reviewed": len(loops),
                "candidates": len(candidates),
                "actions_applied": len(applied),
                "followups_requested": len(followups),
                "errors": errors[:10],
                "request_followup": request_followup,
            },
        )

    return {
        "status": "ok",
        "apply": apply_changes,
        "total_loops_reviewed": len(loops),
        "candidates": candidates,
        "applied": applied,
        "followups": followups,
        "errors": errors,
    }


@app.post("/api/v1/dashboard/csi/specialist-loops/cleanup")
async def dashboard_csi_specialist_loop_cleanup(payload: CSISpecialistLoopCleanupRequest, request: Request):
    apply_changes = bool(payload.apply)
    max_items = max(1, min(int(payload.max_items or 200), 1000))
    older_than_days = max(1, min(int(payload.older_than_days or 7), 365))
    actor = _activity_actor_from_request(request)
    note = str(payload.note or "").strip() or None

    loops = _list_csi_specialist_loops(limit=max_items)
    now_ts = time.time()
    cutoff_ts = now_ts - (older_than_days * 86400)
    candidates: list[dict[str, Any]] = []
    for loop in loops:
        status_value = str(loop.get("status") or "").strip().lower()
        if status_value not in {"closed", "budget_exhausted"}:
            continue
        updated_at = _parse_iso_timestamp(loop.get("updated_at"))
        if updated_at is None:
            continue
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        if updated_at.timestamp() > cutoff_ts:
            continue
        candidates.append(
            {
                "topic_key": str(loop.get("topic_key") or ""),
                "topic_label": str(loop.get("topic_label") or ""),
                "status": status_value,
                "updated_at": str(loop.get("updated_at") or ""),
            }
        )

    deleted_keys: list[str] = []
    errors: list[dict[str, Any]] = []
    if apply_changes and candidates:
        with _csi_specialist_loop_lock:
            conn = _activity_connect()
            try:
                _ensure_activity_schema(conn)
                for item in candidates:
                    topic_key = str(item.get("topic_key") or "").strip()
                    if not topic_key:
                        continue
                    try:
                        conn.execute(
                            "DELETE FROM csi_specialist_loops WHERE topic_key = ?",
                            (topic_key,),
                        )
                        deleted_keys.append(topic_key)
                        _record_activity_audit(
                            event_id=_csi_loop_audit_event_id(topic_key),
                            action="csi_loop_cleanup_delete",
                            actor=actor,
                            outcome="ok",
                            note=note,
                            metadata={"topic_key": topic_key, "older_than_days": older_than_days},
                        )
                    except Exception as exc:
                        errors.append({"topic_key": topic_key, "error": str(exc)})
                conn.commit()
            finally:
                conn.close()

    if apply_changes:
        _add_notification(
            kind="csi_specialist_cleanup_run",
            title="CSI Specialist Loop Cleanup Completed",
            message=(
                f"Loop cleanup identified {len(candidates)} stale loop(s) "
                f"older than {older_than_days} day(s); deleted {len(deleted_keys)}."
            ),
            severity="info" if not errors else "warning",
            requires_action=bool(errors),
            metadata={
                "older_than_days": older_than_days,
                "candidates": len(candidates),
                "deleted": len(deleted_keys),
                "errors": errors[:10],
            },
        )

    return {
        "status": "ok",
        "apply": apply_changes,
        "older_than_days": older_than_days,
        "candidates": candidates,
        "deleted": deleted_keys,
        "errors": errors,
    }


@app.get("/api/v1/dashboard/todolist/pipeline")
async def dashboard_todolist_pipeline():
    from universal_agent.services.todoist_service import TodoService
    try:
        todoist = TodoService()
        return {"status": "ok", "pipeline_summary": todoist.get_pipeline_summary()}
    except Exception as exc:
        logger.warning(f"Failed todolist pipeline summary: {exc}")
        return {"status": "error", "detail": str(exc)}


@app.get("/api/v1/dashboard/todolist/actionable")
async def dashboard_todolist_actionable():
    from universal_agent.services.todoist_service import TodoService
    try:
        todoist = TodoService()
        return {"status": "ok", "actionable_tasks": todoist.get_actionable_tasks()}
    except Exception as exc:
        logger.warning(f"Failed todolist actionable tasks: {exc}")
        return {"status": "error", "detail": str(exc)}


@app.get("/api/v1/dashboard/todolist/heartbeat")
async def dashboard_todolist_heartbeat():
    from universal_agent.services.todoist_service import TodoService
    try:
        todoist = TodoService()
        return {
            "status": "ok",
            "heartbeat_summary": todoist.heartbeat_summary(),
            "heartbeat_candidates": todoist.heartbeat_brainstorm_candidates()
        }
    except Exception as exc:
        logger.warning(f"Failed todolist heartbeat summary: {exc}")
        return {"status": "error", "detail": str(exc)}


@app.get("/api/v1/dashboard/notifications")
async def dashboard_notifications(
    limit: int = 100,
    status: Optional[str] = None,
    session_id: Optional[str] = None,
    kind: Optional[str] = None,
    source_domain: Optional[str] = None,
    pinned: Optional[bool] = None,
):
    _apply_notification_snooze_expiry()
    _apply_activity_snooze_expiry()
    limit = max(1, min(limit, 500))
    safe_session_id = None
    if session_id:
        safe_session_id = _sanitize_session_id_or_400(session_id)

    try:
        items = _query_activity_events(
            limit=limit,
            source_domain=source_domain,
            kind=kind,
            status_value=status,
            pinned=pinned,
            apply_default_window=False,
        )
        notifications: list[dict[str, Any]] = []
        for item in items:
            if str(item.get("event_class") or "") != "notification":
                continue
            if safe_session_id and str(item.get("session_id") or "") != safe_session_id:
                continue
            notifications.append(
                {
                    "id": str(item.get("id") or ""),
                    "kind": str(item.get("kind") or ""),
                    "title": str(item.get("title") or ""),
                    "message": str(item.get("full_message") or ""),
                    "summary": str(item.get("summary") or ""),
                    "full_message": str(item.get("full_message") or ""),
                    "session_id": item.get("session_id"),
                    "severity": str(item.get("severity") or "info"),
                    "requires_action": bool(item.get("requires_action")),
                    "status": str(item.get("status") or "new"),
                    "created_at": str(item.get("created_at_utc") or _utc_now_iso()),
                    "updated_at": str(item.get("updated_at_utc") or _utc_now_iso()),
                    "channels": ["dashboard"],
                    "email_targets": [],
                    "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                }
            )
        if notifications:
            return {"notifications": notifications}
    except Exception as exc:
        logger.debug("Falling back to in-memory notifications: %s", exc)

    items = list(_notifications)
    if status:
        status_norm = status.strip().lower()
        items = [item for item in items if str(item.get("status", "")).lower() == status_norm]
    if safe_session_id:
        items = [item for item in items if item.get("session_id") == safe_session_id]
    if kind:
        kind_norm = kind.strip().lower()
        items = [item for item in items if str(item.get("kind") or "").strip().lower() == kind_norm]
    if source_domain:
        domain_norm = source_domain.strip().lower()
        items = [
            item
            for item in items
            if _activity_source_domain(str(item.get("kind") or ""), item.get("metadata") if isinstance(item.get("metadata"), dict) else {})
            == domain_norm
        ]
    if pinned is not None:
        items = [item for item in items if bool((item.get("metadata") or {}).get("pinned")) == bool(pinned)]
    return {"notifications": items[-limit:][::-1]}


@app.get("/api/v1/dashboard/events")
async def dashboard_events(
    limit: int = 200,
    source_domain: Optional[str] = None,
    kind: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    requires_action: Optional[bool] = None,
    pinned: Optional[bool] = None,
    cursor: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
):
    limit = max(1, min(int(limit), 1000))
    events = _query_activity_events(
        limit=limit + 1,
        source_domain=source_domain,
        kind=kind,
        severity=severity,
        status_value=status,
        requires_action=requires_action,
        pinned=pinned,
        cursor=cursor,
        since=since,
        until=until,
    )
    has_more = len(events) > limit
    if has_more:
        events = events[:limit]
    next_cursor = None
    if has_more and events:
        tail = events[-1]
        next_cursor = _activity_cursor_encode(
            str(tail.get("created_at_utc") or ""),
            str(tail.get("id") or ""),
        )
    if events:
        return {
            "events": events,
            "source": "activity_store",
            "window_days_default": _activity_events_default_window_days,
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    fallback: list[dict[str, Any]] = []
    for item in reversed(_notifications):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        if pinned is not None and bool(metadata.get("pinned")) != bool(pinned):
            continue
        source = _activity_source_domain(str(item.get("kind") or ""), metadata)
        entity_ref = _activity_entity_ref(
            source_domain=source,
            session_id=str(item.get("session_id") or "") or None,
            metadata=metadata,
        )
        fallback.append(
            {
                "id": str(item.get("id") or ""),
                "event_class": "notification",
                "source_domain": source,
                "kind": str(item.get("kind") or ""),
                "title": str(item.get("title") or ""),
                "summary": str(item.get("summary") or _activity_summary_text(str(item.get("message") or ""))),
                "full_message": str(item.get("full_message") or item.get("message") or ""),
                "severity": str(item.get("severity") or "info"),
                "status": str(item.get("status") or "new"),
                "requires_action": bool(item.get("requires_action")),
                "session_id": str(item.get("session_id") or "") or None,
                "created_at_utc": _normalize_notification_timestamp(item.get("created_at")),
                "updated_at_utc": _normalize_notification_timestamp(item.get("updated_at")),
                "entity_ref": entity_ref,
                "actions": _activity_actions(
                    source_domain=source,
                    entity_ref=entity_ref,
                    requires_action=bool(item.get("requires_action")),
                    event_class="notification",
                    status=str(item.get("status") or "new"),
                    metadata=metadata,
                ),
                "metadata": metadata,
            }
        )
        if len(fallback) >= limit:
            break
    return {"events": fallback, "source": "in_memory", "next_cursor": None, "has_more": False}


@app.get("/api/v1/dashboard/events/stream")
async def dashboard_events_stream(
    request: Request,
    since_seq: int = 0,
    limit: int = 500,
    source_domain: Optional[str] = None,
    kind: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    requires_action: Optional[bool] = None,
    pinned: Optional[bool] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    heartbeat_seconds: int = 20,
    once: bool = False,
):
    if not _dashboard_events_sse_enabled:
        raise HTTPException(status_code=503, detail="Dashboard events SSE stream is disabled.")

    since_cursor = max(0, int(since_seq))
    max_items = max(1, min(int(limit), 5000))
    heartbeat_wait = max(2, min(int(heartbeat_seconds), 60))

    async def event_gen():
        nonlocal since_cursor
        _activity_counter_inc("events_sse_connects")
        emitted = 0
        try:
            if since_cursor <= 0:
                snapshot = _query_activity_events(
                    limit=max_items,
                    source_domain=source_domain,
                    kind=kind,
                    severity=severity,
                    status_value=status,
                    requires_action=requires_action,
                    pinned=pinned,
                    since=since,
                    until=until,
                )
                conn = _activity_connect()
                try:
                    _ensure_activity_schema(conn)
                    latest_seq = _activity_stream_latest_seq(conn)
                finally:
                    conn.close()
                since_cursor = max(0, int(latest_seq))
                payload = {
                    "kind": "snapshot",
                    "seq": since_cursor,
                    "events": snapshot,
                    "generated_at_utc": _utc_now_iso(),
                }
                _activity_counter_inc("events_sse_payloads")
                yield f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
                emitted += 1
                if once and emitted >= 1:
                    return

            last_heartbeat = time.time()
            while True:
                if await request.is_disconnected():
                    break
                rows, max_seq_seen = _activity_stream_read(
                    since_seq=since_cursor,
                    limit=max_items,
                    source_domain=source_domain,
                    kind=kind,
                    severity=severity,
                    status_value=status,
                    requires_action=requires_action,
                    pinned=pinned,
                    since=since,
                    until=until,
                )
                if max_seq_seen > since_cursor:
                    since_cursor = max_seq_seen
                if rows:
                    for row in rows:
                        seq = int(row.get("seq") or 0)
                        if seq > since_cursor:
                            since_cursor = seq
                        payload = {
                            "kind": "event",
                            "seq": since_cursor,
                            "op": str(row.get("op") or "upsert"),
                            "event": row.get("event") if isinstance(row.get("event"), dict) else {},
                            "generated_at_utc": _utc_now_iso(),
                        }
                        _activity_counter_inc("events_sse_payloads")
                        yield f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
                        emitted += 1
                        if once and emitted >= 1:
                            return
                    last_heartbeat = time.time()
                    continue

                now = time.time()
                if (now - last_heartbeat) >= heartbeat_wait:
                    heartbeat_payload = {
                        "kind": "heartbeat",
                        "seq": since_cursor,
                        "generated_at_utc": _utc_now_iso(),
                    }
                    _activity_counter_inc("events_sse_heartbeats")
                    yield f"data: {json.dumps(heartbeat_payload, separators=(',', ':'))}\n\n"
                    emitted += 1
                    last_heartbeat = now
                    if once and emitted >= 1:
                        return
                await asyncio.sleep(1.0)
        except Exception:
            _activity_counter_inc("events_sse_errors")
            raise
        finally:
            _activity_counter_inc("events_sse_disconnects")

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_gen(), media_type="text/event-stream", headers=headers)


@app.get("/api/v1/dashboard/events/counters")
async def dashboard_events_counters(
    source_domain: Optional[str] = None,
    kind: Optional[str] = None,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    pinned: Optional[bool] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
):
    counters = _query_activity_event_counters(
        source_domain=source_domain,
        kind=kind,
        severity=severity,
        status_value=status,
        pinned=pinned,
        since=since,
        until=until,
    )
    return {
        "generated_at_utc": _utc_now_iso(),
        "window_days_default": _activity_events_default_window_days,
        "totals": counters.get("totals") if isinstance(counters, dict) else {"unread": 0, "actionable": 0, "total": 0},
        "by_source": counters.get("by_source") if isinstance(counters, dict) else {},
    }


@app.get("/api/v1/dashboard/events/presets")
async def dashboard_events_presets(request: Request):
    owner = _dashboard_owner_from_request(request)
    return {"owner_id": owner, "presets": _list_event_filter_presets(owner)}


@app.post("/api/v1/dashboard/events/presets")
async def dashboard_events_presets_create(request: Request, payload: DashboardEventPresetCreateRequest):
    owner = _dashboard_owner_from_request(request)
    try:
        preset = _create_event_filter_preset(
            owner_id=owner,
            name=payload.name,
            filters=payload.filters,
            is_default=bool(payload.is_default),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"owner_id": owner, "preset": preset}


@app.patch("/api/v1/dashboard/events/presets/{preset_id}")
async def dashboard_events_presets_update(
    request: Request,
    preset_id: str,
    payload: DashboardEventPresetUpdateRequest,
):
    owner = _dashboard_owner_from_request(request)
    try:
        preset = _update_event_filter_preset(
            owner_id=owner,
            preset_id=preset_id,
            name=payload.name,
            filters=payload.filters,
            is_default=payload.is_default,
            mark_used=bool(payload.mark_used),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if preset is None:
        raise HTTPException(status_code=404, detail="Preset not found")
    return {"owner_id": owner, "preset": preset}


@app.delete("/api/v1/dashboard/events/presets/{preset_id}")
async def dashboard_events_presets_delete(request: Request, preset_id: str):
    owner = _dashboard_owner_from_request(request)
    deleted = _delete_event_filter_preset(owner_id=owner, preset_id=preset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Preset not found")
    return {"owner_id": owner, "deleted": True}


@app.get("/api/v1/dashboard/activity/{activity_id}/audit")
async def dashboard_activity_audit(activity_id: str, limit: int = 50):
    return {
        "event_id": activity_id,
        "audit": _list_activity_audit(event_id=activity_id, limit=max(1, min(int(limit), 200))),
    }


@app.post("/api/v1/dashboard/activity/{activity_id}/send-to-simone")
async def dashboard_activity_send_to_simone(activity_id: str, payload: ActivitySendToSimoneRequest, request: Request):
    instruction = str(payload.instruction or "").strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="instruction is required")
    activity = _get_activity_event(activity_id)
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity event not found")
    actor = _activity_actor_from_request(request)
    priority = str(payload.priority or "").strip()
    extra_context = payload.extra_context if isinstance(payload.extra_context, dict) else {}
    _record_activity_audit(
        event_id=activity_id,
        action="send_to_simone",
        actor=actor,
        outcome="requested",
        note=instruction[:500],
        metadata={
            "priority": priority or "normal",
            "extra_context": extra_context,
        },
    )
    if not _hooks_service:
        _record_activity_audit(
            event_id=activity_id,
            action="send_to_simone",
            actor=actor,
            outcome="failed",
            note="hooks service not initialized",
            metadata={},
        )
        raise HTTPException(status_code=503, detail="Hooks service not initialized")

    session_suffix = re.sub(r"[^A-Za-z0-9_.-]+", "_", activity_id).strip("._-")[-48:] or "activity"
    session_key = f"simone_handoff_{session_suffix}"
    metadata = activity.get("metadata") if isinstance(activity.get("metadata"), dict) else {}

    handoff_message = (
        "Manual handoff to Simone from Notifications & Events.\n"
        f"activity_id: {activity_id}\n"
        f"source_domain: {activity.get('source_domain')}\n"
        f"kind: {activity.get('kind')}\n"
        f"title: {activity.get('title')}\n"
        f"created_at_utc: {activity.get('created_at_utc')}\n"
        f"priority: {priority or 'normal'}\n"
        "instruction:\n"
        f"{instruction}\n\n"
        "activity_summary:\n"
        f"{activity.get('summary')}\n\n"
        "activity_full_message:\n"
        f"{activity.get('full_message')}\n\n"
        "activity_metadata_json:\n"
        f"{json.dumps(metadata, ensure_ascii=False, indent=2)[:12000]}"
    )

    requested = _add_notification(
        kind="simone_handoff_requested",
        title="Simone Handoff Requested",
        message=f"Manual handoff requested for activity {activity_id}.",
        severity="info",
        requires_action=False,
        metadata={
            "activity_id": activity_id,
            "session_key": session_key,
            "instruction": instruction,
            "priority": priority or "normal",
            "extra_context": extra_context,
        },
    )

    ok, reason = await _hooks_service.dispatch_internal_action(
        {
            "kind": "agent",
            "name": "ManualSimoneHandoff",
            "session_key": session_key,
            "message": handoff_message,
            "deliver": True,
            "timeout_seconds": 900,
        }
    )
    if not ok:
        _record_activity_audit(
            event_id=activity_id,
            action="send_to_simone",
            actor=actor,
            outcome="failed",
            note=str(reason or "dispatch failed"),
            metadata={"session_key": session_key},
        )
        failed = _add_notification(
            kind="simone_handoff_failed",
            title="Simone Handoff Failed",
            message=f"Handoff dispatch failed for activity {activity_id}: {reason}",
            severity="error",
            requires_action=True,
            metadata={"activity_id": activity_id, "session_key": session_key, "reason": reason},
        )
        return JSONResponse(
            status_code=502,
            content={
                "ok": False,
                "reason": reason,
                "requested_notification": requested,
                "failed_notification": failed,
            },
        )

    _record_activity_audit(
        event_id=activity_id,
        action="send_to_simone",
        actor=actor,
        outcome="completed",
        note=str(reason or "dispatched"),
        metadata={"session_key": session_key},
    )
    completed = _add_notification(
        kind="simone_handoff_completed",
        title="Simone Handoff Dispatched",
        message=f"Handoff dispatched to Simone session {session_key}.",
        severity="success",
        requires_action=False,
        metadata={"activity_id": activity_id, "session_key": session_key, "reason": reason},
    )
    return {"ok": True, "session_key": session_key, "requested_notification": requested, "completed_notification": completed}


@app.post("/api/v1/dashboard/activity/{activity_id}/action")
async def dashboard_activity_action(activity_id: str, payload: ActivityEventActionRequest, request: Request):
    action = str(payload.action or "").strip().lower()
    if not action:
        raise HTTPException(status_code=400, detail="action is required")
    event = _get_activity_event(activity_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Activity event not found")
    actor = _activity_actor_from_request(request)

    event_class = str(event.get("event_class") or "").strip().lower()
    if event_class != "notification" and action in {"mark_read", "snooze", "unsnooze"}:
        raise HTTPException(status_code=400, detail=f"Action '{action}' requires a notification event")

    updated_notification: Optional[dict[str, Any]] = None
    if event_class == "notification":
        record = {
            "id": str(event.get("id") or activity_id),
            "kind": str(event.get("kind") or ""),
            "title": str(event.get("title") or ""),
            "message": str(event.get("full_message") or ""),
            "summary": str(event.get("summary") or ""),
            "full_message": str(event.get("full_message") or ""),
            "session_id": event.get("session_id"),
            "severity": str(event.get("severity") or "info"),
            "requires_action": bool(event.get("requires_action")),
            "status": str(event.get("status") or "new"),
            "created_at": str(event.get("created_at_utc") or _utc_now_iso()),
            "updated_at": str(event.get("updated_at_utc") or _utc_now_iso()),
            "channels": ["dashboard"],
            "email_targets": [],
            "metadata": dict(event.get("metadata") if isinstance(event.get("metadata"), dict) else {}),
        }
        if action == "mark_read":
            _apply_notification_status(record, status_value="read", note=payload.note)
            updated_notification = record
        elif action == "snooze":
            _apply_notification_status(
                record,
                status_value="snoozed",
                note=payload.note,
                snooze_minutes=payload.snooze_minutes,
            )
            updated_notification = record
        elif action == "unsnooze":
            _apply_notification_status(record, status_value="new", note=payload.note)
            updated_notification = record
        elif action in {"pin", "unpin"}:
            metadata = record.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                record["metadata"] = metadata
            metadata["pinned"] = action == "pin"
            metadata["pinned_at"] = _utc_now_iso() if action == "pin" else None
            if action != "pin":
                metadata.pop("pinned_at", None)
            record["updated_at"] = _utc_now_iso()
            _persist_notification_activity(record)
            updated_notification = record
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported action '{action}'")
        _replace_notification_cache_record(updated_notification)
        refreshed = _get_activity_event(activity_id)
        _record_activity_audit(
            event_id=activity_id,
            action=action,
            actor=actor,
            outcome="ok",
            note=payload.note,
            metadata={"event_class": event_class, "snooze_minutes": payload.snooze_minutes},
        )
        return {"ok": True, "action": action, "event": refreshed or event}

    if action not in {"pin", "unpin"}:
        raise HTTPException(status_code=400, detail=f"Unsupported action '{action}' for event class '{event_class}'")

    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    if action == "pin":
        metadata["pinned"] = True
        metadata["pinned_at"] = _utc_now_iso()
    else:
        metadata["pinned"] = False
        metadata.pop("pinned_at", None)
    record = {
        "id": str(event.get("id") or ""),
        "event_class": str(event.get("event_class") or "event"),
        "source_domain": str(event.get("source_domain") or "system"),
        "kind": str(event.get("kind") or "event"),
        "title": str(event.get("title") or "Event"),
        "summary": str(event.get("summary") or ""),
        "full_message": str(event.get("full_message") or ""),
        "severity": str(event.get("severity") or "info"),
        "status": str(event.get("status") or "new"),
        "requires_action": bool(event.get("requires_action")),
        "session_id": str(event.get("session_id") or "") or None,
        "created_at": str(event.get("created_at_utc") or _utc_now_iso()),
        "updated_at": _utc_now_iso(),
        "entity_ref": event.get("entity_ref") if isinstance(event.get("entity_ref"), dict) else {},
        "actions": event.get("actions") if isinstance(event.get("actions"), list) else [],
        "metadata": metadata,
        "channels": ["dashboard"],
        "email_targets": [],
    }
    _activity_upsert_record(record)
    refreshed = _get_activity_event(activity_id)
    _record_activity_audit(
        event_id=activity_id,
        action=action,
        actor=actor,
        outcome="ok",
        note=payload.note,
        metadata={"event_class": event_class},
    )
    return {"ok": True, "action": action, "event": refreshed or event}


@app.patch("/api/v1/dashboard/notifications/{notification_id}")
async def dashboard_notification_update(notification_id: str, payload: NotificationUpdateRequest, request: Request):
    _apply_notification_snooze_expiry()
    _apply_activity_snooze_expiry()
    status_value = _normalize_notification_status(payload.status)
    if status_value not in {"new", "read", "acknowledged", "snoozed", "dismissed"}:
        raise HTTPException(status_code=400, detail="Invalid status")
    for item in reversed(_notifications):
        if item.get("id") == notification_id:
            _apply_notification_status(
                item,
                status_value=status_value,
                note=payload.note,
                snooze_minutes=payload.snooze_minutes,
            )
            _record_activity_audit(
                event_id=notification_id,
                action="set_status",
                actor=_activity_actor_from_request(request),
                outcome="ok",
                note=payload.note,
                metadata={"status": status_value, "snooze_minutes": payload.snooze_minutes},
            )
            return {"notification": item}
    event = _get_activity_event(notification_id)
    if event and str(event.get("event_class") or "") == "notification":
        reconstructed = {
            "id": str(event.get("id") or notification_id),
            "kind": str(event.get("kind") or ""),
            "title": str(event.get("title") or ""),
            "message": str(event.get("full_message") or ""),
            "summary": str(event.get("summary") or ""),
            "full_message": str(event.get("full_message") or ""),
            "session_id": event.get("session_id"),
            "severity": str(event.get("severity") or "info"),
            "requires_action": bool(event.get("requires_action")),
            "status": str(event.get("status") or "new"),
            "created_at": str(event.get("created_at_utc") or _utc_now_iso()),
            "updated_at": str(event.get("updated_at_utc") or _utc_now_iso()),
            "channels": ["dashboard"],
            "email_targets": [],
            "metadata": event.get("metadata") if isinstance(event.get("metadata"), dict) else {},
        }
        _apply_notification_status(
            reconstructed,
            status_value=status_value,
            note=payload.note,
            snooze_minutes=payload.snooze_minutes,
        )
        _record_activity_audit(
            event_id=notification_id,
            action="set_status",
            actor=_activity_actor_from_request(request),
            outcome="ok",
            note=payload.note,
            metadata={"status": status_value, "snooze_minutes": payload.snooze_minutes},
        )
        _replace_notification_cache_record(reconstructed)
        return {"notification": reconstructed}
    raise HTTPException(status_code=404, detail="Notification not found")


@app.post("/api/v1/dashboard/notifications/bulk")
async def dashboard_notification_bulk_update(payload: NotificationBulkUpdateRequest):
    status_value = _normalize_notification_status(payload.status)
    if status_value not in {"new", "read", "acknowledged", "snoozed", "dismissed"}:
        raise HTTPException(status_code=400, detail="Invalid status")

    _apply_notification_snooze_expiry()
    _apply_activity_snooze_expiry()
    kind_filter = str(payload.kind or "").strip().lower()
    current_status_filter = _normalize_notification_status(payload.current_status or "")
    limit = max(1, min(int(payload.limit or 200), 1000))
    updated = _bulk_update_activity_notifications(
        status_value=status_value,
        note=payload.note,
        kind=kind_filter or None,
        current_status=current_status_filter or None,
        snooze_minutes=payload.snooze_minutes,
        limit=limit,
    )

    return {
        "updated": len(updated),
        "status": status_value,
        "notifications": updated,
    }


@app.post("/api/v1/dashboard/notifications/purge")
async def dashboard_notification_purge(payload: NotificationPurgeRequest):
    _apply_notification_snooze_expiry()
    _apply_activity_snooze_expiry()
    kind_filter = str(payload.kind or "").strip().lower()
    status_filter = _normalize_notification_status(payload.current_status or "")
    older_than_hours = payload.older_than_hours
    if older_than_hours is not None:
        older_than_hours = max(1, min(int(older_than_hours), 24 * 365))
    apply_age_filter = older_than_hours is not None

    if not payload.clear_all and not kind_filter and not status_filter and not apply_age_filter:
        raise HTTPException(
            status_code=400,
            detail="Provide a purge filter (kind/current_status/older_than_hours) or set clear_all=true",
        )

    deleted_count = _purge_activity_notifications(
        clear_all=bool(payload.clear_all),
        kind=kind_filter or None,
        current_status=status_filter or None,
        older_than_hours=older_than_hours if apply_age_filter else None,
    )
    return {
        "deleted": deleted_count,
        "remaining": len(_notifications),
        "filters": {
            "clear_all": bool(payload.clear_all),
            "kind": kind_filter or None,
            "current_status": status_filter or None,
            "older_than_hours": older_than_hours,
        },
    }


@app.post("/api/v1/dashboard/system/commands")
async def dashboard_system_command(payload: DashboardSystemCommandRequest):
    text = str(payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    source_page = str(payload.source_page or "").strip()
    source_context = _normalize_source_context(payload.source_context)
    timezone_name = str(payload.timezone or "").strip() or "UTC"
    dry_run = bool(payload.dry_run)
    source_session_id = _system_context_session_id(source_context)
    task_description = _build_system_command_task_description(
        source_page=source_page,
        source_context=source_context,
    )

    try:
        from universal_agent.services.todoist_service import TodoService
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Todoist service unavailable: {exc}")

    try:
        todoist = TodoService()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Todoist not configured: {exc}")

    if _system_command_is_status_query(text):
        summary = todoist.heartbeat_summary()
        pipeline = todoist.get_pipeline_summary()
        return {
            "ok": True,
            "lane": "system",
            "intent": "status_query",
            "interpreted": {"query": text, "scope": "todoist"},
            "todoist": {
                "summary": summary,
                "pipeline": pipeline,
            },
            "dry_run": dry_run,
        }

    if _system_command_is_brainstorm_capture(text):
        content, schedule_text = _extract_system_command_content_and_schedule(text)
        if not content:
            content = text
        interpreted = {
            "content": content,
            "schedule_text": schedule_text,
            "priority": _system_command_priority_from_text(text),
            "source_page": source_page,
            "source_context": source_context,
        }
        if dry_run:
            return {
                "ok": True,
                "lane": "system",
                "intent": "capture_idea",
                "interpreted": interpreted,
                "dry_run": True,
            }
        task = todoist.record_idea(
            content=content,
            description=task_description,
            source_session_id=source_session_id,
            impact="M",
            effort="M",
        )
        _add_notification(
            kind="system_command_idea_recorded",
            title="Idea Captured",
            message=f"Todoist brainstorm idea captured: {content[:120]}",
            severity="info",
            metadata={
                "source": "system_command",
                "source_page": source_page,
                "todoist_task_id": str(task.get("id") or ""),
            },
        )
        return {
            "ok": True,
            "lane": "system",
            "intent": "capture_idea",
            "interpreted": interpreted,
            "todoist": {"task": task},
            "dry_run": False,
        }

    content, schedule_text = _extract_system_command_content_and_schedule(text)
    if not content:
        content = _strip_system_command_prefix(text) or text
    priority = _system_command_priority_from_text(text)
    section = "scheduled" if schedule_text else "background"
    interpreted = {
        "content": content,
        "schedule_text": schedule_text,
        "priority": priority,
        "section": section,
        "source_page": source_page,
        "source_context": source_context,
    }
    if dry_run:
        return {
            "ok": True,
            "lane": "system",
            "intent": "schedule_task" if schedule_text else "capture_task",
            "interpreted": interpreted,
            "dry_run": True,
        }

    task = todoist.create_task(
        content=content,
        description=task_description,
        priority=priority,
        section=section,
        due_string=schedule_text or None,
        labels=["agent-ready", "system-lane"],
    )

    cron_job: Optional[dict[str, Any]] = None
    cron_bridge_status: Optional[str] = None
    enable_cron_bridge = (
        os.getenv("UA_SYSTEM_COMMAND_ENABLE_CRON_BRIDGE", "1").strip().lower() in {"1", "true", "yes", "on"}
    )
    if schedule_text and _cron_service and enable_cron_bridge:
        try:
            task_id = str(task.get("id") or "").strip()
            repeat = _schedule_text_suggests_repeat(schedule_text)
            every_raw, cron_expr, run_at_ts, delete_after_run = _resolve_simplified_schedule_fields(
                schedule_time=schedule_text,
                repeat=repeat,
                timezone_name=timezone_name,
            )
            schedule_signature = _todoist_chron_schedule_signature(
                schedule_text=schedule_text,
                timezone_name=timezone_name,
                every_raw=every_raw,
                cron_expr=cron_expr,
                run_at_ts=run_at_ts,
                delete_after_run=delete_after_run,
            )
            run_command = _build_todoist_execution_cron_command(
                task_id=task_id,
                content=content,
            )
            job_metadata = {
                "source": "system_command",
                "autonomous": True,
                "todoist_task_id": task_id,
                "source_page": source_page,
                "source_context": source_context,
                "schedule_signature": schedule_signature,
            }

            existing_mapping = _todoist_chron_mapping_get(task_id) if task_id else None
            existing_job_id = str((existing_mapping or {}).get("cron_job_id") or "").strip()
            existing_job = _cron_service.get_job(existing_job_id) if existing_job_id else None

            if existing_job is not None:
                existing_signature = str((existing_mapping or {}).get("schedule_signature") or "").strip()
                if existing_signature == schedule_signature:
                    cron_bridge_status = "reused_existing"
                    cron_job = {
                        **existing_job.to_dict(),
                        "running": existing_job.job_id in _cron_service.running_jobs,
                    }
                else:
                    updated_job = _cron_service.update_job(
                        existing_job.job_id,
                        {
                            "command": run_command,
                            "enabled": True,
                            "every_seconds": every_raw if every_raw is not None else 0,
                            "cron_expr": cron_expr,
                            "timezone": timezone_name,
                            "run_at": run_at_ts,
                            "delete_after_run": delete_after_run,
                            "metadata": job_metadata,
                        },
                    )
                    cron_bridge_status = "updated_existing"
                    cron_job = {
                        **updated_job.to_dict(),
                        "running": updated_job.job_id in _cron_service.running_jobs,
                    }
                    todoist.add_comment(
                        task_id,
                        f"UA updated Chron job: {updated_job.job_id} ({schedule_text})",
                    )
            else:
                job = _cron_service.add_job(
                    user_id="cron_system",
                    workspace_dir=str(
                        WORKSPACES_DIR
                        / f"cron_todoist_{str(task.get('id') or 'task')[:8]}_{int(time.time())}"
                    ),
                    command=run_command,
                    every_raw=every_raw,
                    cron_expr=cron_expr,
                    timezone=timezone_name,
                    run_at=run_at_ts,
                    delete_after_run=delete_after_run,
                    enabled=True,
                    metadata=job_metadata,
                )
                cron_bridge_status = "created"
                cron_job = {**job.to_dict(), "running": job.job_id in _cron_service.running_jobs}
                todoist.add_comment(
                    task_id,
                    f"UA scheduled Chron job: {job.job_id} ({schedule_text})",
                )

            if cron_job and task_id:
                _todoist_chron_mapping_upsert(
                    task_id,
                    {
                        "cron_job_id": str(cron_job.get("job_id") or ""),
                        "schedule_text": schedule_text,
                        "schedule_signature": schedule_signature,
                        "timezone": timezone_name,
                        "bridge_status": cron_bridge_status or "unknown",
                        "source_page": source_page,
                        "source_context": source_context,
                    },
                )
        except Exception as exc:
            cron_bridge_status = "failed"
            todoist.add_comment(
                str(task.get("id") or ""),
                f"UA could not create Chron schedule automatically: {exc}",
            )

    _add_notification(
        kind="system_command_routed",
        title="System Command Captured",
        message=f"{content[:120]}",
        severity="info",
        metadata={
            "source": "system_command",
            "source_page": source_page,
            "todoist_task_id": str(task.get("id") or ""),
            "schedule_text": schedule_text or "",
            "cron_job_id": str((cron_job or {}).get("job_id") or ""),
            "cron_bridge_status": cron_bridge_status or "",
            "source_context": source_context,
        },
    )
    return {
        "ok": True,
        "lane": "system",
        "intent": "schedule_task" if schedule_text else "capture_task",
        "interpreted": interpreted,
        "todoist": {"task": task},
        "cron": {"job": cron_job, "status": cron_bridge_status} if cron_job or cron_bridge_status else None,
        "dry_run": False,
    }


@app.get("/api/v1/dashboard/tutorials/runs")
async def dashboard_tutorial_runs(limit: int = 100):
    clamped_limit = max(1, min(int(limit), 500))
    return {"runs": _list_tutorial_runs(limit=clamped_limit)}


@app.delete("/api/v1/dashboard/tutorials/runs")
async def dashboard_tutorial_run_delete(run_path: str):
    """Delete a tutorial run directory by its relative run_path."""
    import shutil
    run_path = run_path.strip().strip("/")
    if not run_path:
        raise HTTPException(status_code=400, detail="run_path is required")

    run_dir = _resolve_path_under_root(ARTIFACTS_DIR, run_path)
    rel = _artifact_rel_path(run_dir)
    if not _is_tutorial_run_rel_path(rel):
        raise HTTPException(
            status_code=400,
            detail="run_path must be under youtube-tutorial-creation/",
        )

    if not run_dir.exists() or not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="Tutorial run directory not found")

    try:
        shutil.rmtree(run_dir)
        logger.info(f"Deleted tutorial run directory: {run_dir}")
    except Exception as exc:
        logger.error(f"Failed to delete tutorial run directory {run_dir}: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to delete run: {exc}")

    return {"deleted": True, "run_path": rel}


_TUTORIAL_NOTIFICATION_KINDS = frozenset({
    "youtube_tutorial_started",
    "youtube_tutorial_ready",
    "youtube_tutorial_failed",
    "youtube_ingest_failed",
    "youtube_hook_recovery_queued",
    "tutorial_repo_bootstrap_queued",
    "tutorial_repo_bootstrap_ready",
    "tutorial_repo_bootstrap_failed",
})


@app.get("/api/v1/dashboard/tutorials/notifications")
async def dashboard_tutorial_notifications(limit: int = 50, include_dismissed: bool = False):
    """Return recent notifications relevant to the YouTube tutorial pipeline."""
    clamped = max(1, min(int(limit), 200))
    matching = [
        n for n in reversed(_notifications)
        if n.get("kind") in _TUTORIAL_NOTIFICATION_KINDS
        and (
            include_dismissed
            or _normalize_notification_status(n.get("status") or "new") != "dismissed"
        )
    ][:clamped]
    return {"notifications": matching}


@app.get("/api/v1/dashboard/tutorials/review-jobs")
async def dashboard_tutorial_review_jobs(limit: int = 50):
    clamped_limit = max(1, min(int(limit), 500))
    jobs = sorted(
        _tutorial_review_jobs.values(),
        key=lambda row: float(row.get("queued_at_epoch") or 0.0),
        reverse=True,
    )
    return {"jobs": jobs[:clamped_limit]}


@app.get("/api/v1/dashboard/tutorials/bootstrap-jobs")
async def dashboard_tutorial_bootstrap_jobs(limit: int = 100, run_path: str = ""):
    clamped_limit = max(1, min(int(limit), 500))
    jobs = _tutorial_bootstrap_list_jobs(limit=clamped_limit, run_path=run_path)
    return {"jobs": jobs}


@app.post("/api/v1/dashboard/tutorials/review")
async def dashboard_tutorial_review_dispatch(payload: TutorialReviewDispatchRequest):
    run_path = str(payload.run_path or "").strip().strip("/")
    if not run_path:
        raise HTTPException(status_code=400, detail="run_path is required")

    run_dir = _resolve_path_under_root(ARTIFACTS_DIR, run_path)
    if not run_dir.exists() or not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="Tutorial run directory not found")

    run_rel = _artifact_rel_path(run_dir)
    if not _is_tutorial_run_rel_path(run_rel):
        raise HTTPException(
            status_code=400,
            detail="run_path must be under youtube-tutorial-creation/",
        )

    manifest = _tutorial_manifest(run_dir)
    run_snapshot = {
        "run_path": run_rel,
        "run_dir": str(run_dir),
        "run_name": run_dir.name,
        "title": str(manifest.get("title") or run_dir.name),
        "video_id": str(manifest.get("video_id") or ""),
        "video_url": str(manifest.get("video_url") or ""),
        "status": str(manifest.get("status") or "unknown"),
        "files": _tutorial_key_files(run_dir),
    }

    now = datetime.now(timezone.utc)
    date_slug = now.strftime("%Y-%m-%d")
    hhmmss = now.strftime("%H%M%S")
    base_slug = _safe_slug_component(str(run_snapshot.get("title") or run_dir.name))
    review_rel = f"youtube-tutorial-reviews/{date_slug}/{base_slug}__{hhmmss}"

    job_id = f"trj_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    job = {
        "job_id": job_id,
        "status": "queued",
        "queued_at": now.isoformat(),
        "queued_at_epoch": time.time(),
        "tutorial_run_path": run_rel,
        "review_run_path": review_rel,
        "title": run_snapshot.get("title"),
        "video_url": run_snapshot.get("video_url"),
    }
    _remember_tutorial_review_job(job)

    _add_notification(
        kind="tutorial_review_queued",
        title="Tutorial Sent To Simone",
        message=f"Review queued for: {run_snapshot.get('title')}",
        severity="info",
        metadata={
            "job_id": job_id,
            "tutorial_run_path": run_rel,
            "review_run_path": review_rel,
            "tutorial_storage_href": _storage_explorer_href(scope="artifacts", path=run_rel),
            "review_storage_href": _storage_explorer_href(scope="artifacts", path=review_rel),
            "source": "dashboard_tutorial_dispatch",
        },
    )

    asyncio.create_task(
        _run_tutorial_review_job(
            job_id=job_id,
            run=run_snapshot,
            review_rel_path=review_rel,
            note=str(payload.note or "").strip(),
        )
    )
    return {
        "queued": True,
        "job_id": job_id,
        "tutorial_run_path": run_rel,
        "review_run_path": review_rel,
        "review_storage_href": _storage_explorer_href(scope="artifacts", path=review_rel),
    }


@app.post("/api/v1/dashboard/tutorials/bootstrap-repo")
async def dashboard_tutorial_bootstrap_repo(request: Request, payload: TutorialBootstrapRepoRequest):
    _require_delegation_publish_allowed()

    run_path = str(payload.run_path or "").strip().strip("/")
    if not run_path:
        raise HTTPException(status_code=400, detail="run_path is required")

    run_dir = _resolve_path_under_root(ARTIFACTS_DIR, run_path)
    if not run_dir.exists() or not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="Tutorial run directory not found")

    run_rel = _artifact_rel_path(run_dir)
    if not _is_tutorial_run_rel_path(run_rel):
        raise HTTPException(
            status_code=400,
            detail="run_path must be under youtube-tutorial-creation/",
        )

    implementation_dir = run_dir / "implementation"
    script_path = implementation_dir / "create_new_repo.sh"
    if not implementation_dir.exists() or not implementation_dir.is_dir():
        raise HTTPException(status_code=400, detail="implementation directory not found for tutorial run")
    if not script_path.exists() or not script_path.is_file():
        raise HTTPException(status_code=400, detail="create_new_repo.sh not found for tutorial run")

    manifest = _tutorial_manifest(run_dir)
    title = str(manifest.get("title") or run_dir.name).strip() or run_dir.name
    auto_repo_name = f"{_safe_slug_component(title)}__{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    repo_name = _sanitize_tutorial_repo_name(str(payload.repo_name or "").strip()) or auto_repo_name
    target_root = str(payload.target_root or "").strip() or _tutorial_bootstrap_target_root_default()
    python_version = str(payload.python_version or "").strip()
    timeout_seconds = max(30, min(int(payload.timeout_seconds or 900), 3600))
    execution_target = _normalize_tutorial_bootstrap_execution_target(payload.execution_target)
    if not execution_target:
        raise HTTPException(status_code=400, detail="execution_target must be local or server")

    if execution_target == "local":
        def _worker_hint_for(dispatch_backend: str) -> str:
            normalized = str(dispatch_backend or "http_queue").strip().lower()
            if normalized == "redis_stream":
                return (
                    "Run scripts/tutorial_local_bootstrap_worker.py on your local desktop "
                    "with --transport redis and matching UA_REDIS_* environment."
                )
            return (
                "Run scripts/tutorial_local_bootstrap_worker.py on your local desktop "
                "with --gateway-url and --ops-token to process queued jobs."
            )

        existing_job = _tutorial_bootstrap_find_active_job(run_path=run_rel, execution_target="local")
        if existing_job:
            existing_dispatch_backend = str(existing_job.get("dispatch_backend") or "http_queue").strip().lower()
            return {
                "queued": True,
                "job_id": str(existing_job.get("job_id") or ""),
                "status": str(existing_job.get("status") or "queued"),
                "execution_target": "local",
                "dispatch_backend": existing_dispatch_backend,
                "run_path": run_rel,
                "repo_name": str(existing_job.get("repo_name") or repo_name),
                "target_root": str(existing_job.get("target_root") or target_root),
                "job": existing_job,
                "existing_job_reused": True,
                "worker_hint": _worker_hint_for(existing_dispatch_backend),
            }
        now = datetime.now(timezone.utc)
        job_id = f"tbj_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        dispatch_backend = (
            "redis_stream"
            if (_delegation_mission_bus is not None and _FACTORY_POLICY.can_publish_delegations)
            else "http_queue"
        )
        job = {
            "job_id": job_id,
            "status": "queued",
            "queued_at": now.isoformat(),
            "queued_at_epoch": time.time(),
            "execution_target": "local",
            "tutorial_run_path": run_rel,
            "tutorial_title": title,
            "video_id": str(manifest.get("video_id") or ""),
            "video_url": str(manifest.get("video_url") or ""),
            "repo_name": repo_name,
            "target_root": target_root,
            "python_version": python_version,
            "timeout_seconds": timeout_seconds,
            "dispatch_backend": dispatch_backend,
        }
        saved = _remember_tutorial_bootstrap_job(job)
        dispatch_message_id = ""
        if dispatch_backend == "redis_stream":
            try:
                published, message_id = _publish_tutorial_bootstrap_mission(request=request, job=saved)
                if published:
                    dispatch_message_id = str(message_id or "")
                    if dispatch_message_id:
                        saved["delegation_message_id"] = dispatch_message_id
                        saved = _remember_tutorial_bootstrap_job(saved)
                else:
                    dispatch_backend = "http_queue"
                    saved["dispatch_backend"] = "http_queue"
                    saved["dispatch_fallback_reason"] = "redis_bus_unavailable"
                    saved = _remember_tutorial_bootstrap_job(saved)
            except Exception as exc:
                logger.warning("Failed publishing tutorial bootstrap mission to Redis: %s", exc)
                dispatch_backend = "http_queue"
                saved["dispatch_backend"] = "http_queue"
                saved["dispatch_fallback_reason"] = str(exc)
                saved = _remember_tutorial_bootstrap_job(saved)

        _add_notification(
            kind="tutorial_repo_bootstrap_queued",
            title="Tutorial Repo Bootstrap Queued",
            message=f"Queued local repo creation for: {title}",
            severity="info",
            metadata={
                "job_id": job_id,
                "tutorial_run_path": run_rel,
                "repo_name": repo_name,
                "target_root": target_root,
                "execution_target": "local",
                "dispatch_backend": dispatch_backend,
                "delegation_message_id": dispatch_message_id or None,
                "source": "dashboard_tutorial_bootstrap",
            },
        )
        return {
            "queued": True,
            "job_id": job_id,
            "status": "queued",
            "execution_target": "local",
            "dispatch_backend": dispatch_backend,
            "run_path": run_rel,
            "repo_name": repo_name,
            "target_root": target_root,
            "job": saved,
            "existing_job_reused": False,
            "worker_hint": _worker_hint_for(dispatch_backend),
        }

    args = ["bash", str(script_path), target_root, repo_name]
    if python_version:
        args.append(python_version)

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(implementation_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to launch bootstrap script: {exc}")

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.communicate()
        raise HTTPException(status_code=504, detail=f"Bootstrap script timed out after {timeout_seconds}s")

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    merged = "\n".join([stdout, stderr]).strip()
    repo_match = re.search(r"Repo ready:\s*(.+)", merged)
    repo_dir = repo_match.group(1).strip() if repo_match else str((Path(target_root) / repo_name).resolve())

    if proc.returncode != 0:
        detail = stderr.strip() or stdout.strip() or f"Bootstrap script failed with exit code {proc.returncode}"
        raise HTTPException(
            status_code=500,
            detail=detail[-1200:],
        )

    logger.info(
        "Tutorial repo bootstrap succeeded run_path=%s repo_dir=%s",
        run_rel,
        repo_dir,
    )
    repo_open_uri, repo_open_hint = _tutorial_bootstrap_open_metadata(repo_dir)
    return {
        "ok": True,
        "execution_target": "server",
        "run_path": run_rel,
        "repo_name": repo_name,
        "target_root": target_root,
        "repo_dir": repo_dir,
        "repo_open_uri": repo_open_uri,
        "repo_open_hint": repo_open_hint,
        "stdout": stdout[-4000:],
        "stderr": stderr[-2000:],
    }


@app.post("/api/v1/ops/tutorials/bootstrap-jobs/claim")
async def ops_tutorial_bootstrap_claim(request: Request, payload: TutorialBootstrapJobClaimRequest):
    _require_ops_auth(request)
    _require_delegation_consume_allowed()
    worker_id = str(payload.worker_id or "").strip() or f"worker-{uuid.uuid4().hex[:8]}"
    job = _tutorial_bootstrap_claim_next(worker_id=worker_id)
    return {"worker_id": worker_id, "job": job}


@app.post("/api/v1/ops/tutorials/bootstrap-jobs/{job_id}/start")
async def ops_tutorial_bootstrap_start(
    request: Request,
    job_id: str,
    payload: TutorialBootstrapJobClaimRequest,
):
    _require_ops_auth(request)
    _require_delegation_consume_allowed()
    worker_id = str(payload.worker_id or "").strip() or f"worker-{uuid.uuid4().hex[:8]}"
    job = _tutorial_bootstrap_mark_running(job_id=job_id, worker_id=worker_id)
    return {"worker_id": worker_id, "job": job}


@app.get("/api/v1/ops/tutorials/bootstrap-jobs/{job_id}/bundle")
async def ops_tutorial_bootstrap_bundle(request: Request, job_id: str):
    _require_ops_auth(request)
    _require_delegation_consume_allowed()
    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id:
        raise HTTPException(status_code=400, detail="job_id is required")

    with _tutorial_bootstrap_jobs_lock:
        job = dict(_tutorial_bootstrap_jobs.get(normalized_job_id) or {})
    if not job:
        raise HTTPException(status_code=404, detail="Bootstrap job not found")

    run_path = str(job.get("tutorial_run_path") or "").strip().strip("/")
    if not run_path:
        raise HTTPException(status_code=400, detail="Bootstrap job is missing tutorial_run_path")

    run_dir = _resolve_path_under_root(ARTIFACTS_DIR, run_path)
    run_rel = _artifact_rel_path(run_dir)
    if not _is_tutorial_run_rel_path(run_rel):
        raise HTTPException(status_code=400, detail="tutorial_run_path must be under youtube-tutorial-creation/")
    if not run_dir.exists() or not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="Tutorial run directory not found")

    implementation_dir = run_dir / "implementation"
    script_path = implementation_dir / "create_new_repo.sh"
    if not implementation_dir.exists() or not implementation_dir.is_dir():
        raise HTTPException(status_code=400, detail="implementation directory not found for tutorial run")
    if not script_path.exists() or not script_path.is_file():
        raise HTTPException(status_code=400, detail="create_new_repo.sh not found for tutorial run")

    tar_buffer = io.BytesIO()
    try:
        with tarfile.open(fileobj=tar_buffer, mode="w:gz") as archive:
            archive.add(implementation_dir, arcname="implementation")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to package bootstrap bundle: {exc}")
    tar_buffer.seek(0)
    filename = f"{normalized_job_id}_implementation.tgz"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Tutorial-Run-Path": run_rel,
    }
    return StreamingResponse(tar_buffer, media_type="application/gzip", headers=headers)


@app.post("/api/v1/ops/tutorials/bootstrap-jobs/{job_id}/result")
async def ops_tutorial_bootstrap_result(
    request: Request,
    job_id: str,
    payload: TutorialBootstrapJobResultRequest,
):
    _require_ops_auth(request)
    _require_delegation_consume_allowed()
    updated = _tutorial_bootstrap_update_result(
        job_id=job_id,
        worker_id=str(payload.worker_id or "").strip(),
        status=str(payload.status or ""),
        repo_dir=str(payload.repo_dir or ""),
        stdout=str(payload.stdout or ""),
        stderr=str(payload.stderr or ""),
        error=str(payload.error or ""),
    )

    is_success = str(updated.get("status") or "") == "completed"
    title = str(updated.get("tutorial_title") or updated.get("tutorial_run_path") or "tutorial")
    _add_notification(
        kind="tutorial_repo_bootstrap_ready" if is_success else "tutorial_repo_bootstrap_failed",
        title="Tutorial Repo Created" if is_success else "Tutorial Repo Bootstrap Failed",
        message=(
            f"Local repo created for: {title}"
            if is_success
            else f"Local repo bootstrap failed for: {title}"
        ),
        severity="success" if is_success else "error",
        requires_action=not is_success,
        metadata={
            "job_id": str(updated.get("job_id") or ""),
            "tutorial_run_path": str(updated.get("tutorial_run_path") or ""),
            "repo_name": str(updated.get("repo_name") or ""),
            "repo_dir": str(updated.get("repo_dir") or ""),
            "status": str(updated.get("status") or ""),
            "error": str(updated.get("error") or ""),
            "execution_target": "local",
            "source": "tutorial_bootstrap_worker",
        },
    )
    return {"job": updated}


@app.post("/api/v1/vision/describe")
async def vision_describe(request: VisionDescribeRequest):
    import httpx
    
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured for vision tasks")

    model = os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-3-5-sonnet-latest")

    try:
        base64_data = request.image_base64
        media_type = "image/png"

        if base64_data.startswith("data:"):
            header, content = base64_data.split(",", 1)
            media_type = header.split(";")[0].replace("data:", "")
            base64_data = content

        async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": model,
                    "max_tokens": 1024,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": media_type,
                                        "data": base64_data
                                    }
                                },
                                {
                                    "type": "text",
                                    "text": request.prompt
                                }
                            ]
                        }
                    ]
                }
            )
            response.raise_for_status()
            data = response.json()
            description = data.get("content", [{}])[0].get("text", "No description provided.")

            return {
                "ok": True,
                "description": description
            }
    except httpx.HTTPStatusError as e:
        logger.error(f"Vision API HTTP error: {e.response.text}")
        raise HTTPException(status_code=502, detail=f"Vision API returned an error: {e.response.status_code}")
    except Exception as e:
        logger.exception("Vision API error")
        raise HTTPException(status_code=502, detail=f"Vision API error: {str(e)}")


@app.post("/api/v1/heartbeat/wake")
async def wake_heartbeat(request: HeartbeatWakeRequest):
    if not _heartbeat_service:
        raise HTTPException(status_code=400, detail="Heartbeat service not available.")

    _scheduling_counter_inc("heartbeat_wake_requests")
    reason = request.reason or "wake"
    mode = (request.mode or "now").strip().lower()
    if request.session_id:
        session_id = _sanitize_session_id_or_400(request.session_id)
        if mode == "next":
            _heartbeat_service.request_heartbeat_next(session_id, reason=reason)
        else:
            _heartbeat_service.request_heartbeat_now(session_id, reason=reason)
        return {"status": "queued", "session_id": session_id, "reason": reason, "mode": mode}

    for session_id in list(_sessions.keys()):
        if mode == "next":
            _heartbeat_service.request_heartbeat_next(session_id, reason=reason)
        else:
            _heartbeat_service.request_heartbeat_now(session_id, reason=reason)
    return {"status": "queued", "count": len(_sessions), "reason": reason, "mode": mode}


@app.get("/api/v1/heartbeat/last")
async def get_last_heartbeat(session_id: Optional[str] = None):
    if not _heartbeat_service:
        raise HTTPException(status_code=400, detail="Heartbeat service not available.")

    _scheduling_counter_inc("heartbeat_last_requests")
    if session_id:
        session_id = _sanitize_session_id_or_400(session_id)
        session = get_session(session_id)
        if session:
            state = _read_heartbeat_state(session.workspace_dir) or {}
        else:
            # Allow heartbeat lookup for inactive sessions if workspace still exists.
            workspace_dir = WORKSPACES_DIR / session_id
            if not workspace_dir.exists():
                raise HTTPException(status_code=404, detail="Session not found.")
            state = _read_heartbeat_state(str(workspace_dir)) or {}
        busy = bool(_heartbeat_service and session_id in _heartbeat_service.busy_sessions)
        return {
            "session_id": session_id,
            "last_run": state.get("last_run"),
            "last_summary": state.get("last_summary"),
            "busy": busy,
        }

    payload: dict[str, dict] = {}
    for sid, session in _sessions.items():
        state = _read_heartbeat_state(session.workspace_dir) or {}
        if not state:
            continue
        busy = bool(_heartbeat_service and sid in _heartbeat_service.busy_sessions)
        payload[sid] = {
            "last_run": state.get("last_run"),
            "last_summary": state.get("last_summary"),
            "busy": busy,
        }
    return {"heartbeats": payload}


@app.post("/api/v1/system/event")
async def post_system_event(request: SystemEventRequest):
    event_type = (request.event_type or "system_event").strip() or "system_event"
    event = {
        "event_id": f"evt_{int(time.time() * 1000)}",
        "type": event_type,
        "payload": request.payload or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    target_sessions: list[str]
    if request.session_id:
        requested_session_id = _sanitize_session_id_or_400(request.session_id)
        if requested_session_id not in _sessions:
            raise HTTPException(status_code=404, detail="Session not found.")
        target_sessions = [requested_session_id]
    else:
        target_sessions = list(_sessions.keys())

    for sid in target_sessions:
        _enqueue_system_event(sid, event)
        if sid in manager.session_connections:
            _broadcast_system_event(sid, event)
        _persist_system_activity_event(event, session_id=sid)

    wake_flag = request.wake_heartbeat or request.wake_mode
    if wake_flag and _heartbeat_service and target_sessions:
        mode = "next"
        if isinstance(wake_flag, str):
            mode = wake_flag.strip().lower() or mode
        if mode not in {"now", "next"}:
            mode = "next"
        for sid in target_sessions:
            if mode == "next":
                _heartbeat_service.request_heartbeat_next(sid, reason=f"system_event:{event_type}")
            else:
                _heartbeat_service.request_heartbeat_now(sid, reason=f"system_event:{event_type}")

    return {"status": "queued", "count": len(target_sessions), "event": event}


@app.get("/api/v1/system/events")
async def list_system_events(session_id: str):
    session_id = _sanitize_session_id_or_400(session_id)
    # Ops/dashboard can request events for historical or external VP session ids.
    # Those are not always present in the in-memory live session registry.
    # Return a stable empty payload instead of 404 to avoid noisy polling errors.
    if session_id not in _sessions:
        return {
            "session_id": session_id,
            "events": [],
            "status": "session_not_loaded",
        }
    return {"session_id": session_id, "events": _system_events.get(session_id, [])}


@app.post("/api/v1/system/presence")
async def set_system_presence(request: SystemPresenceRequest):
    node_id = request.node_id or "gateway"
    presence = {
        "node_id": node_id,
        "status": request.status or "online",
        "reason": request.reason,
        "metadata": request.metadata or {},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _system_presence[node_id] = presence
    _broadcast_presence(presence)
    return {"status": "ok", "presence": presence}


@app.get("/api/v1/system/presence")
async def get_system_presence():
    return {"nodes": list(_system_presence.values())}


_SIMPLE_INTERVAL_RE = re.compile(
    r"^(?:in\s+)?(\d+)\s*(second|seconds|sec|secs|s|minute|minutes|min|mins|m|hour|hours|hr|hrs|h|day|days|d)$",
    re.IGNORECASE,
)

_SYSTEM_COMMAND_SCHEDULE_MARKERS = (
    " in ",
    " at ",
    " tomorrow",
    " today",
    " tonight",
    " every ",
    " daily",
    " weekly",
    " monthly",
    " weekday",
    " weekdays",
)


def _normalize_system_context_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 3:
        return None
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        compact = value.strip()
        if not compact:
            return None
        return compact[:500]
    if isinstance(value, list):
        out: list[Any] = []
        for item in value[:25]:
            normalized = _normalize_system_context_value(item, depth=depth + 1)
            if normalized is not None:
                out.append(normalized)
        return out or None
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key in sorted(value.keys(), key=lambda k: str(k))[:40]:
            key_text = str(key).strip()
            if not key_text:
                continue
            normalized = _normalize_system_context_value(value.get(key), depth=depth + 1)
            if normalized is not None:
                out[key_text[:120]] = normalized
        return out or None
    return str(value)[:500]


def _normalize_source_context(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    normalized = _normalize_system_context_value(payload, depth=0)
    if isinstance(normalized, dict):
        return normalized
    return {}


def _system_context_session_id(source_context: dict[str, Any]) -> Optional[str]:
    for key in ("session_id", "active_session_id", "chat_session_id"):
        value = source_context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    selection = source_context.get("selection")
    if isinstance(selection, dict):
        for key in ("session_id", "active_session_id"):
            value = selection.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _source_context_snippet(source_context: dict[str, Any], *, max_chars: int = 800) -> str:
    if not source_context:
        return ""
    try:
        snippet = json.dumps(source_context, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    except Exception:
        return ""
    if len(snippet) <= max_chars:
        return snippet
    return snippet[: max(0, max_chars - 3)] + "..."


def _build_system_command_task_description(*, source_page: str, source_context: dict[str, Any]) -> str:
    parts = [f"source_page: {source_page or 'unknown'}"]
    context_snippet = _source_context_snippet(source_context)
    if context_snippet:
        parts.append(f"source_context: {context_snippet}")
    return "\n".join(parts)


def _todoist_chron_mapping_store_path() -> Path:
    return WORKSPACES_DIR / TODOIST_CHRON_MAPPING_FILENAME


def _load_todoist_chron_mapping_store() -> dict[str, Any]:
    path = _todoist_chron_mapping_store_path()
    default_store = {
        "version": TODOIST_CHRON_MAPPING_STORE_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "task_map": {},
    }
    if not path.exists():
        return default_store
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_store
    if not isinstance(payload, dict):
        return default_store
    task_map = payload.get("task_map")
    if not isinstance(task_map, dict):
        task_map = {}
    return {
        "version": TODOIST_CHRON_MAPPING_STORE_VERSION,
        "updated_at": str(payload.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        "task_map": task_map,
    }


def _save_todoist_chron_mapping_store(store: dict[str, Any]) -> None:
    path = _todoist_chron_mapping_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    task_map = store.get("task_map")
    if not isinstance(task_map, dict):
        task_map = {}
    if len(task_map) > TODOIST_CHRON_MAPPING_MAX_ENTRIES:
        entries = sorted(
            (
                str(task_id),
                value if isinstance(value, dict) else {},
            )
            for task_id, value in task_map.items()
        )
        kept: dict[str, dict[str, Any]] = {}
        for task_id, row in entries[-TODOIST_CHRON_MAPPING_MAX_ENTRIES:]:
            kept[task_id] = row
        task_map = kept
    payload = {
        "version": TODOIST_CHRON_MAPPING_STORE_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "task_map": task_map,
    }
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _todoist_chron_mapping_get(task_id: str) -> Optional[dict[str, Any]]:
    clean_task_id = str(task_id or "").strip()
    if not clean_task_id:
        return None
    with _todoist_chron_mapping_lock:
        store = _load_todoist_chron_mapping_store()
        task_map = store.get("task_map")
        if not isinstance(task_map, dict):
            return None
        row = task_map.get(clean_task_id)
        if not isinstance(row, dict):
            return None
        return dict(row)


def _todoist_chron_mapping_upsert(task_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    clean_task_id = str(task_id or "").strip()
    if not clean_task_id:
        return {}
    now_iso = datetime.now(timezone.utc).isoformat()
    with _todoist_chron_mapping_lock:
        store = _load_todoist_chron_mapping_store()
        task_map = store.setdefault("task_map", {})
        if not isinstance(task_map, dict):
            task_map = {}
            store["task_map"] = task_map
        previous = task_map.get(clean_task_id)
        created_at = now_iso
        if isinstance(previous, dict) and str(previous.get("created_at") or "").strip():
            created_at = str(previous.get("created_at")).strip()
        merged = {
            **(previous if isinstance(previous, dict) else {}),
            **(entry if isinstance(entry, dict) else {}),
            "task_id": clean_task_id,
            "created_at": created_at,
            "updated_at": now_iso,
        }
        task_map[clean_task_id] = merged
        _save_todoist_chron_mapping_store(store)
        return dict(merged)


def _todoist_chron_mapping_reconciliation_metrics_update(
    *,
    result: dict[str, Any],
    duration_ms: float,
    error: Optional[str],
) -> None:
    bucket = _scheduling_runtime_metrics.setdefault("todoist_chron_reconciliation", {})
    if not isinstance(bucket, dict):
        bucket = {}
        _scheduling_runtime_metrics["todoist_chron_reconciliation"] = bucket
    bucket["runs"] = int(bucket.get("runs", 0) or 0) + 1
    bucket["last_run_at"] = datetime.now(timezone.utc).isoformat()
    bucket["last_duration_ms"] = round(max(0.0, duration_ms), 3)
    bucket["last_error"] = str(error or "") or None
    bucket["last_result"] = result if isinstance(result, dict) else {}


def _todoist_chron_task_index() -> dict[str, Any]:
    index: dict[str, Any] = {}
    if not _cron_service:
        return index
    jobs = sorted(
        list(_cron_service.list_jobs()),
        key=lambda job: float(getattr(job, "created_at", 0.0) or 0.0),
        reverse=True,
    )
    for job in jobs:
        metadata = getattr(job, "metadata", {}) or {}
        if not isinstance(metadata, dict):
            continue
        task_id = str(metadata.get("todoist_task_id") or "").strip()
        if not task_id or task_id in index:
            continue
        index[task_id] = job
    return index


def _reconcile_todoist_chron_mappings(*, remove_stale: bool = True, dry_run: bool = False) -> dict[str, Any]:
    started = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    stats: dict[str, Any] = {
        "ok": True,
        "remove_stale": bool(remove_stale),
        "dry_run": bool(dry_run),
        "started_at": now_iso,
        "inspected": 0,
        "unchanged": 0,
        "relinked": 0,
        "removed": 0,
        "stale_flagged": 0,
        "metadata_repairs": 0,
        "mapping_entries_before": 0,
        "mapping_entries_after": 0,
    }
    error_text: Optional[str] = None
    try:
        task_index = _todoist_chron_task_index()
        with _todoist_chron_mapping_lock:
            store = _load_todoist_chron_mapping_store()
            task_map = store.get("task_map")
            if not isinstance(task_map, dict):
                task_map = {}
                store["task_map"] = task_map
            stats["mapping_entries_before"] = len(task_map)
            changed = False

            for task_id in sorted(list(task_map.keys())):
                row = task_map.get(task_id)
                if not isinstance(row, dict):
                    if not dry_run:
                        task_map.pop(task_id, None)
                        changed = True
                    stats["removed"] = int(stats.get("removed", 0) or 0) + 1
                    continue
                stats["inspected"] = int(stats.get("inspected", 0) or 0) + 1
                mapped_job_id = str(row.get("cron_job_id") or "").strip()
                mapped_job = _cron_service.get_job(mapped_job_id) if _cron_service and mapped_job_id else None

                if mapped_job is not None:
                    metadata = getattr(mapped_job, "metadata", {}) or {}
                    if isinstance(metadata, dict) and str(metadata.get("todoist_task_id") or "").strip() != task_id:
                        if not dry_run and _cron_service:
                            try:
                                _cron_service.update_job(
                                    mapped_job.job_id,
                                    {"metadata": {"todoist_task_id": task_id}},
                                )
                                stats["metadata_repairs"] = int(stats.get("metadata_repairs", 0) or 0) + 1
                            except Exception:
                                logger.exception(
                                    "Failed repairing todoist_task_id metadata for cron job %s",
                                    mapped_job.job_id,
                                )
                    if row.get("stale"):
                        if not dry_run:
                            row.pop("stale", None)
                            row.pop("stale_detected_at", None)
                            row["updated_at"] = now_iso
                            changed = True
                    stats["unchanged"] = int(stats.get("unchanged", 0) or 0) + 1
                    continue

                candidate_job = task_index.get(task_id)
                if candidate_job is not None:
                    stats["relinked"] = int(stats.get("relinked", 0) or 0) + 1
                    if not dry_run:
                        row["cron_job_id"] = str(candidate_job.job_id)
                        row["relinked_at"] = now_iso
                        row["updated_at"] = now_iso
                        row.pop("stale", None)
                        row.pop("stale_detected_at", None)
                        changed = True
                    continue

                if remove_stale:
                    stats["removed"] = int(stats.get("removed", 0) or 0) + 1
                    if not dry_run:
                        task_map.pop(task_id, None)
                        changed = True
                else:
                    stats["stale_flagged"] = int(stats.get("stale_flagged", 0) or 0) + 1
                    if not dry_run and not row.get("stale"):
                        row["stale"] = True
                        row["stale_detected_at"] = now_iso
                        row["updated_at"] = now_iso
                        changed = True

            if changed and not dry_run:
                _save_todoist_chron_mapping_store(store)
            stats["mapping_entries_after"] = len(task_map)
    except Exception as exc:
        error_text = str(exc)
        stats["ok"] = False
        stats["error"] = error_text
        logger.exception("Todoist<->Chron mapping reconciliation failed")
    finally:
        duration_ms = (time.time() - started) * 1000.0
        _todoist_chron_mapping_reconciliation_metrics_update(
            result=stats,
            duration_ms=duration_ms,
            error=error_text,
        )
        stats["duration_ms"] = round(duration_ms, 3)
    return stats


async def _todoist_chron_reconcile_loop() -> None:
    stop_event = _todoist_chron_reconcile_stop_event or asyncio.Event()
    while not stop_event.is_set():
        try:
            _reconcile_todoist_chron_mappings(
                remove_stale=TODOIST_CHRON_RECONCILE_REMOVE_STALE,
                dry_run=False,
            )
        except Exception:
            logger.exception("Todoist<->Chron periodic reconciliation loop iteration failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=TODOIST_CHRON_RECONCILE_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue
        except Exception:
            await asyncio.sleep(1.0)


def _todoist_chron_schedule_signature(
    *,
    schedule_text: str,
    timezone_name: str,
    every_raw: Optional[str],
    cron_expr: Optional[str],
    run_at_ts: Optional[float],
    delete_after_run: bool,
) -> str:
    payload = {
        "schedule_text": str(schedule_text or "").strip().lower(),
        "timezone": str(timezone_name or "UTC").strip() or "UTC",
        "every": str(every_raw or "").strip().lower(),
        "cron_expr": str(cron_expr or "").strip(),
        "run_at": round(float(run_at_ts), 3) if run_at_ts is not None else None,
        "delete_after_run": bool(delete_after_run),
    }
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _autonomous_notification_timestamp(item: dict[str, Any]) -> Optional[float]:
    created = _parse_iso_datetime(item.get("created_at"))
    if created is not None:
        return created.timestamp()
    updated = _parse_iso_datetime(item.get("updated_at"))
    if updated is not None:
        return updated.timestamp()
    return None


def _artifact_links_for_path(path: Path) -> dict[str, str]:
    rel = _artifact_rel_path(path)
    api_url = _artifact_api_file_url(rel) if rel else ""
    storage_href = _storage_explorer_href(scope="artifacts", path=rel, preview=rel) if rel else ""
    return {
        "relative_path": rel,
        "api_url": api_url,
        "storage_href": storage_href,
    }


def _workspace_rel_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(WORKSPACES_DIR.resolve()).as_posix()
    except Exception:
        return ""


def _workspace_links_for_path(path: Path) -> dict[str, str]:
    rel = _workspace_rel_path(path)
    storage_href = _storage_explorer_href(scope="workspaces", path=rel, preview=rel) if rel else ""
    return {
        "relative_path": rel,
        "api_url": "",
        "storage_href": storage_href,
    }


def _heartbeat_artifact_links_from_payload(
    payload: dict[str, Any],
    *,
    max_files: int = 20,
) -> list[dict[str, str]]:
    session_id = str(payload.get("session_id") or "").strip()
    workspace_dir = _workspace_dir_for_session(session_id)
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        return []

    raw_paths: list[str] = []
    for key in ("writes", "work_products"):
        values = artifacts.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            text = str(value or "").strip()
            if text:
                raw_paths.append(text)

    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for raw in raw_paths:
        if len(out) >= max(1, max_files):
            break
        path_obj = Path(raw)
        candidate = path_obj if path_obj.is_absolute() else ((workspace_dir / path_obj) if workspace_dir else path_obj)
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate

        artifact_rel = _artifact_rel_path(resolved)
        if artifact_rel:
            key = ("artifacts", artifact_rel)
            if key in seen:
                continue
            seen.add(key)
            link = _artifact_links_for_path(resolved)
            if link.get("relative_path"):
                out.append(
                    {
                        "scope": "artifacts",
                        "relative_path": str(link.get("relative_path") or ""),
                        "api_url": str(link.get("api_url") or ""),
                        "storage_href": str(link.get("storage_href") or ""),
                        "source_path": raw,
                    }
                )
            continue

        workspace_rel = _workspace_rel_path(resolved)
        if workspace_rel:
            key = ("workspaces", workspace_rel)
            if key in seen:
                continue
            seen.add(key)
            link = _workspace_links_for_path(resolved)
            if link.get("relative_path"):
                out.append(
                    {
                        "scope": "workspaces",
                        "relative_path": str(link.get("relative_path") or ""),
                        "api_url": "",
                        "storage_href": str(link.get("storage_href") or ""),
                        "source_path": raw,
                    }
                )
    return out


def _autonomous_job_artifact_links(job_id: str, *, max_files: int = 6) -> list[dict[str, str]]:
    clean_job_id = str(job_id or "").strip()
    if not clean_job_id:
        return []
    job_root = ARTIFACTS_DIR / "cron" / clean_job_id
    if not job_root.exists() or not job_root.is_dir():
        return []
    files = [path for path in job_root.rglob("*") if path.is_file()]
    files.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0.0, reverse=True)
    links: list[dict[str, str]] = []
    for path in files[: max(1, max_files)]:
        link = _artifact_links_for_path(path)
        if not link.get("relative_path"):
            continue
        links.append(link)
    return links


def _strip_system_command_prefix(text: str) -> str:
    cleaned = (text or "").strip()
    patterns = [
        r"^(please\s+)?(add|schedule|queue|create|put)\s+",
        r"^(please\s+)?(remind|remind me)\s+to\s+",
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(
        r"\bto\s+(my\s+)?(todoist|to-?do\s+list|todo\s+list)\b",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    return cleaned.strip(" \t\n\r:;,-")


def _extract_system_command_content_and_schedule(text: str) -> tuple[str, Optional[str]]:
    raw = _strip_system_command_prefix(text)
    lowered = raw.lower()
    start_index: Optional[int] = None
    for marker in _SYSTEM_COMMAND_SCHEDULE_MARKERS:
        idx = lowered.find(marker)
        if idx <= 0:
            continue
        if start_index is None or idx < start_index:
            start_index = idx
    if start_index is None:
        return raw.strip(), None
    content = raw[:start_index].strip(" \t\n\r:;,-")
    schedule_text = raw[start_index:].strip(" \t\n\r:;,-")
    if not content:
        return raw.strip(), None
    if not schedule_text:
        return raw.strip(), None
    return content, schedule_text


def _system_command_priority_from_text(text: str) -> str:
    lowered = str(text or "").strip().lower()
    if re.search(r"\b(urgent|asap|immediately|immediate|critical)\b", lowered):
        return "urgent"
    if re.search(r"\b(high priority|high-priority|important|soon)\b", lowered):
        return "high"
    if re.search(r"\b(low priority|low-priority|whenever|someday|later)\b", lowered):
        return "low"
    return "medium"


def _system_command_is_status_query(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    has_query_verb = bool(re.search(r"\b(status|show|list|what|summary|summarize)\b", lowered))
    return has_query_verb and bool(re.search(r"\b(todo|todoist|to-?do)\b", lowered))


def _system_command_is_brainstorm_capture(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    return bool(re.search(r"\b(idea|brainstorm|backlog|capture this)\b", lowered))


def _build_todoist_execution_cron_command(*, task_id: str, content: str) -> str:
    safe_content = str(content or "").strip()
    if len(safe_content) > 200:
        safe_content = safe_content[:197] + "..."
    return "\n".join(
        [
            "Autonomous Todoist execution task.",
            f"todoist_task_id: {task_id}",
            f"todoist_task_content: {safe_content}",
            "Run the task now using available UA tools and produce concrete outputs when relevant.",
            "If completed, call internal Todoist task action to complete the task with a concise summary comment.",
            "If blocked, add a Todoist comment describing exactly what is missing.",
        ]
    )


def _build_autonomous_daily_briefing_command() -> str:
    return "\n".join(
        [
            "Generate the daily autonomous operations briefing for the last 24 hours.",
            "Focus only on work executed without direct user prompting (scheduled/proactive flows).",
            "Include:",
            "- tasks completed",
            "- tasks attempted and failed",
            "- links/paths to artifacts produced",
            "- items requiring user decisions",
            "Write a concise markdown report to UA_ARTIFACTS_DIR/autonomous-briefings/<today>/DAILY_BRIEFING.md.",
            "Then provide a short summary suitable for dashboard notification text.",
        ]
    )


def _autonomous_briefing_day_slug(now_ts: float) -> str:
    tz_name = (
        (os.getenv("UA_AUTONOMOUS_DAILY_BRIEFING_TIMEZONE") or "").strip()
        or AUTONOMOUS_DAILY_BRIEFING_DEFAULT_TIMEZONE
    )
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
    dt = datetime.fromtimestamp(now_ts, tz)
    return dt.date().isoformat()


def _collect_autonomous_runs_from_cron(
    *,
    window_start: float,
    window_end: float,
) -> dict[str, Any]:
    completed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "cron_runs_in_buffer": 0,
        "cron_runs_in_window": 0,
        "cron_runs_missing_job_metadata": 0,
        "cron_runs_non_autonomous": 0,
        "cron_runs_daily_briefing_excluded": 0,
        "cron_autonomous_runs_in_window": 0,
        "cron_autonomous_completed": 0,
        "cron_autonomous_failed": 0,
    }
    if not _cron_service:
        return {"completed": completed, "failed": failed, "diagnostics": diagnostics}

    try:
        runs = _cron_service.list_runs(limit=5000)
    except Exception:
        logger.exception("Failed loading cron runs for autonomous daily briefing backfill")
        return {"completed": completed, "failed": failed, "diagnostics": diagnostics}

    diagnostics["cron_runs_in_buffer"] = len(runs)
    if not runs:
        return {"completed": completed, "failed": failed, "diagnostics": diagnostics}

    jobs_by_id: dict[str, Any] = {}
    try:
        jobs_by_id = {
            str(job.job_id): job
            for job in _cron_service.list_jobs()
            if getattr(job, "job_id", None)
        }
    except Exception:
        logger.exception("Failed loading cron jobs for autonomous daily briefing backfill")

    for run in runs:
        if not isinstance(run, dict):
            continue
        raw_ts = run.get("finished_at") or run.get("started_at") or run.get("scheduled_at")
        try:
            event_ts = float(raw_ts)
        except Exception:
            event_ts = 0.0
        if event_ts <= 0.0 or event_ts < window_start or event_ts > (window_end + 5):
            continue
        diagnostics["cron_runs_in_window"] = int(diagnostics.get("cron_runs_in_window", 0) or 0) + 1

        job_id = str(run.get("job_id") or "").strip()
        job = jobs_by_id.get(job_id)
        if job is None:
            diagnostics["cron_runs_missing_job_metadata"] = (
                int(diagnostics.get("cron_runs_missing_job_metadata", 0) or 0) + 1
            )
            continue

        metadata = getattr(job, "metadata", None)
        if not isinstance(metadata, dict):
            metadata = {}
        system_job = str(metadata.get("system_job") or "").strip()
        is_autonomous = bool(metadata.get("autonomous")) or bool(metadata.get("briefing"))
        if not is_autonomous:
            diagnostics["cron_runs_non_autonomous"] = int(diagnostics.get("cron_runs_non_autonomous", 0) or 0) + 1
            continue
        if system_job == AUTONOMOUS_DAILY_BRIEFING_JOB_KEY:
            diagnostics["cron_runs_daily_briefing_excluded"] = (
                int(diagnostics.get("cron_runs_daily_briefing_excluded", 0) or 0) + 1
            )
            continue

        diagnostics["cron_autonomous_runs_in_window"] = (
            int(diagnostics.get("cron_autonomous_runs_in_window", 0) or 0) + 1
        )
        created_at = datetime.fromtimestamp(event_ts, timezone.utc).isoformat()
        status = str(run.get("status") or "").strip().lower()
        command = str(getattr(job, "command", "") or "").strip()
        if command and len(command) > 240:
            command = f"{command[:237]}..."
        output_preview = str(run.get("output_preview") or "").strip()
        error_text = str(run.get("error") or "").strip()
        if status in {"success"}:
            diagnostics["cron_autonomous_completed"] = int(diagnostics.get("cron_autonomous_completed", 0) or 0) + 1
            message = command or output_preview or f"Cron job {job_id} completed."
            completed.append(
                {
                    "id": "",
                    "kind": "autonomous_run_completed",
                    "title": "Autonomous Task Completed",
                    "message": message,
                    "created_at": created_at,
                    "job_id": job_id,
                    "run_id": str(run.get("run_id") or ""),
                    "todoist_task_id": str(metadata.get("todoist_task_id") or ""),
                    "error": "",
                    "system_job": system_job,
                    "report_api_url": "",
                    "report_storage_href": "",
                    "source": "cron_runs_backfill",
                }
            )
            continue

        diagnostics["cron_autonomous_failed"] = int(diagnostics.get("cron_autonomous_failed", 0) or 0) + 1
        if not error_text:
            error_text = f"Chron run status={status or 'unknown'}"
        message = command or output_preview or f"Cron job {job_id} failed."
        failed.append(
            {
                "id": "",
                "kind": "autonomous_run_failed",
                "title": "Autonomous Task Failed",
                "message": message,
                "created_at": created_at,
                "job_id": job_id,
                "run_id": str(run.get("run_id") or ""),
                "todoist_task_id": str(metadata.get("todoist_task_id") or ""),
                "error": error_text,
                "system_job": system_job,
                "report_api_url": "",
                "report_storage_href": "",
                "source": "cron_runs_backfill",
            }
        )

    for bucket in (completed, failed):
        bucket.sort(
            key=lambda row: _autonomous_notification_timestamp({"created_at": row.get("created_at")}) or 0.0,
            reverse=True,
        )
        del bucket[AUTONOMOUS_DAILY_BRIEFING_MAX_ITEMS :]

    return {
        "completed": completed,
        "failed": failed,
        "diagnostics": diagnostics,
    }


def _collect_autonomous_activity_rows(*, now_ts: Optional[float] = None) -> dict[str, Any]:
    if now_ts is None:
        now_ts = time.time()
    window_seconds = AUTONOMOUS_DAILY_BRIEFING_WINDOW_SECONDS
    window_start = float(now_ts) - float(window_seconds)

    completed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    heartbeat_rows: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {
        "notification_buffer_size": len(_notifications),
        "notification_events_in_window": 0,
        "notification_completed_in_window": 0,
        "notification_failed_in_window": 0,
        "notification_heartbeat_in_window": 0,
        "cron_backfill_applied": False,
        "signals_ingest_enabled": (
            str(os.getenv("UA_SIGNALS_INGEST_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}
        ),
        "todoist_credentials_present": bool(
            (os.getenv("TODOIST_API_TOKEN") or "").strip() or (os.getenv("TODOIST_API_KEY") or "").strip()
        ),
    }
    for item in _notifications:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind not in {"autonomous_run_completed", "autonomous_run_failed", "autonomous_heartbeat_completed"}:
            continue
        event_ts = _autonomous_notification_timestamp(item)
        if event_ts is None or event_ts < window_start:
            continue
        diagnostics["notification_events_in_window"] = int(diagnostics.get("notification_events_in_window", 0) or 0) + 1
        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        row = {
            "id": str(item.get("id") or ""),
            "kind": kind,
            "title": str(item.get("title") or ""),
            "message": str(item.get("message") or ""),
            "created_at": str(item.get("created_at") or ""),
            "job_id": str(metadata.get("job_id") or ""),
            "run_id": str(metadata.get("run_id") or ""),
            "todoist_task_id": str(metadata.get("todoist_task_id") or ""),
            "error": str(metadata.get("error") or ""),
            "system_job": str(metadata.get("system_job") or ""),
            "report_api_url": str(metadata.get("report_api_url") or ""),
            "report_storage_href": str(metadata.get("report_storage_href") or ""),
        }
        heartbeat_artifacts = metadata.get("heartbeat_artifacts")
        if isinstance(heartbeat_artifacts, list):
            row["heartbeat_artifacts"] = [
                dict(link)
                for link in heartbeat_artifacts
                if isinstance(link, dict)
            ][:50]
        else:
            row["heartbeat_artifacts"] = []
        if kind == "autonomous_run_completed":
            completed.append(row)
            diagnostics["notification_completed_in_window"] = (
                int(diagnostics.get("notification_completed_in_window", 0) or 0) + 1
            )
        elif kind == "autonomous_run_failed":
            failed.append(row)
            diagnostics["notification_failed_in_window"] = (
                int(diagnostics.get("notification_failed_in_window", 0) or 0) + 1
            )
        else:
            heartbeat_rows.append(row)
            diagnostics["notification_heartbeat_in_window"] = (
                int(diagnostics.get("notification_heartbeat_in_window", 0) or 0) + 1
            )

    # Keep newest first, bounded for artifact size safety.
    for bucket in (completed, failed, heartbeat_rows):
        bucket.sort(
            key=lambda row: _autonomous_notification_timestamp({"created_at": row.get("created_at")}) or 0.0,
            reverse=True,
        )
        del bucket[AUTONOMOUS_DAILY_BRIEFING_MAX_ITEMS :]

    cron_backfill = _collect_autonomous_runs_from_cron(window_start=window_start, window_end=float(now_ts))
    cron_diagnostics = cron_backfill.get("diagnostics")
    if isinstance(cron_diagnostics, dict):
        diagnostics.update(cron_diagnostics)

    if not completed and not failed:
        backfill_completed = list(cron_backfill.get("completed") or [])
        backfill_failed = list(cron_backfill.get("failed") or [])
        if backfill_completed or backfill_failed:
            completed = backfill_completed
            failed = backfill_failed
            diagnostics["cron_backfill_applied"] = True

    warnings: list[str] = []
    if diagnostics.get("cron_backfill_applied"):
        warnings.append(
            "Autonomous run events were backfilled from persisted cron runs because in-memory notifications were empty."
        )
    if int(diagnostics.get("cron_runs_daily_briefing_excluded", 0) or 0) > 0 and not completed and not failed:
        warnings.append(
            "Only the daily briefing cron job was observed in this window; no other autonomous cron tasks were recorded."
        )
    if not completed and not failed and not heartbeat_rows:
        warnings.append(
            "No autonomous activity events were available for this window. This usually indicates a runtime reset, "
            "upstream ingest being disabled, or no autonomous tasks being scheduled."
        )
    if int(diagnostics.get("cron_runs_missing_job_metadata", 0) or 0) > 0:
        warnings.append(
            "Some persisted cron runs could not be classified as autonomous because their job metadata was unavailable."
        )
    if not bool(diagnostics.get("signals_ingest_enabled")):
        warnings.append(
            "CSI signals ingest is disabled (`UA_SIGNALS_INGEST_ENABLED` is not enabled), so CSI-driven autonomous work may be absent."
        )
    if not bool(diagnostics.get("todoist_credentials_present")):
        warnings.append(
            "Todoist credentials are not configured in this runtime (`TODOIST_API_TOKEN`/`TODOIST_API_KEY`), so sync-backed task flows may fail."
        )

    return {
        "window_seconds": window_seconds,
        "window_started_at": datetime.fromtimestamp(window_start, timezone.utc).isoformat(),
        "window_ended_at": datetime.fromtimestamp(now_ts, timezone.utc).isoformat(),
        "completed": completed,
        "failed": failed,
        "heartbeat": heartbeat_rows,
        "source_diagnostics": diagnostics,
        "warnings": warnings,
    }


def _generate_autonomous_daily_briefing_artifact(*, now_ts: Optional[float] = None) -> dict[str, Any]:
    if now_ts is None:
        now_ts = time.time()
    rows = _collect_autonomous_activity_rows(now_ts=now_ts)
    completed = list(rows.get("completed") or [])
    failed = list(rows.get("failed") or [])
    heartbeat_rows = list(rows.get("heartbeat") or [])
    source_diagnostics = rows.get("source_diagnostics")
    if not isinstance(source_diagnostics, dict):
        source_diagnostics = {}
    warnings = [str(item).strip() for item in (rows.get("warnings") or []) if str(item).strip()]
    requires_decision = [row for row in failed if row.get("error")]
    non_cron_artifact_links: list[dict[str, str]] = []
    seen_artifact_refs: set[tuple[str, str]] = set()
    for row in heartbeat_rows:
        raw_links = row.get("heartbeat_artifacts")
        if not isinstance(raw_links, list):
            continue
        for raw in raw_links:
            if not isinstance(raw, dict):
                continue
            scope = str(raw.get("scope") or "").strip() or "workspaces"
            rel = str(raw.get("relative_path") or "").strip()
            href = str(raw.get("storage_href") or "").strip() or str(raw.get("api_url") or "").strip()
            if not rel or not href:
                continue
            key = (scope, rel)
            if key in seen_artifact_refs:
                continue
            seen_artifact_refs.add(key)
            non_cron_artifact_links.append(
                {
                    "scope": scope,
                    "relative_path": rel,
                    "storage_href": href,
                }
            )

    day_slug = _autonomous_briefing_day_slug(now_ts)
    out_dir = ARTIFACTS_DIR / "autonomous-briefings" / day_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "DAILY_BRIEFING.md"
    json_path = out_dir / "briefing.json"

    lines: list[str] = []
    lines.append("# Daily Autonomous Briefing")
    lines.append("")
    lines.append(f"- Generated: {datetime.fromtimestamp(now_ts, timezone.utc).isoformat()}")
    lines.append(f"- Window start (UTC): {rows.get('window_started_at')}")
    lines.append(f"- Window end (UTC): {rows.get('window_ended_at')}")
    lines.append(
        f"- Totals: completed={len(completed)}, failed={len(failed)}, heartbeat_events={len(heartbeat_rows)}"
    )
    lines.append(
        f"- Input health: notif_window={int(source_diagnostics.get('notification_events_in_window', 0) or 0)}, "
        f"cron_window={int(source_diagnostics.get('cron_runs_in_window', 0) or 0)}, "
        f"backfill_applied={'yes' if bool(source_diagnostics.get('cron_backfill_applied')) else 'no'}"
    )
    lines.append("")

    lines.append("## Briefing Input Diagnostics")
    lines.append(
        f"- Notification buffer size: {int(source_diagnostics.get('notification_buffer_size', 0) or 0)}"
    )
    lines.append(
        f"- Autonomous notifications in window: {int(source_diagnostics.get('notification_events_in_window', 0) or 0)}"
    )
    lines.append(f"- Persisted cron runs in window: {int(source_diagnostics.get('cron_runs_in_window', 0) or 0)}")
    lines.append(
        f"- Classified autonomous cron runs in window: {int(source_diagnostics.get('cron_autonomous_runs_in_window', 0) or 0)}"
    )
    lines.append(
        f"- Daily briefing self-runs excluded: {int(source_diagnostics.get('cron_runs_daily_briefing_excluded', 0) or 0)}"
    )
    lines.append(
        f"- CSI signals ingest enabled: {'yes' if bool(source_diagnostics.get('signals_ingest_enabled')) else 'no'}"
    )
    lines.append(
        f"- Todoist credentials present: {'yes' if bool(source_diagnostics.get('todoist_credentials_present')) else 'no'}"
    )
    lines.append(
        f"- Cron backfill applied: {'yes' if bool(source_diagnostics.get('cron_backfill_applied')) else 'no'}"
    )
    lines.append("")

    lines.append("## Data Quality Warnings")
    if warnings:
        for warning in warnings:
            lines.append(f"- {warning}")
    else:
        lines.append("- None.")
    lines.append("")

    lines.append("## Completed Autonomous Tasks")
    if completed:
        for row in completed:
            task_text = str(row.get("message") or "").strip() or str(row.get("title") or "Autonomous task")
            lines.append(f"- {task_text}")
            job_id = str(row.get("job_id") or "")
            if job_id:
                links = _autonomous_job_artifact_links(job_id)
                if links:
                    for link in links:
                        rel = str(link.get("relative_path") or "")
                        api_url = str(link.get("api_url") or "")
                        if rel and api_url:
                            lines.append(f"  - [Artifact: {rel}]({api_url})")
                else:
                    lines.append(f"  - No persisted artifact files found for job `{job_id}`.")
    else:
        lines.append("- None in the last window.")
    lines.append("")

    lines.append("## Attempted / Failed Autonomous Tasks")
    if failed:
        for row in failed:
            task_text = str(row.get("message") or "").strip() or str(row.get("title") or "Autonomous task")
            lines.append(f"- {task_text}")
            error_text = str(row.get("error") or "").strip()
            if error_text:
                lines.append(f"  - Error: `{error_text[:400]}`")
            job_id = str(row.get("job_id") or "")
            if job_id:
                links = _autonomous_job_artifact_links(job_id)
                for link in links:
                    rel = str(link.get("relative_path") or "")
                    api_url = str(link.get("api_url") or "")
                    if rel and api_url:
                        lines.append(f"  - [Artifact: {rel}]({api_url})")
    else:
        lines.append("- None in the last window.")
    lines.append("")

    lines.append("## Items Requiring User Decision")
    if requires_decision:
        for row in requires_decision:
            task_id = str(row.get("todoist_task_id") or "").strip()
            error_text = str(row.get("error") or "").strip()
            message = str(row.get("message") or "").strip()
            if task_id:
                lines.append(f"- Todoist task `{task_id}`: {message}")
            else:
                lines.append(f"- {message}")
            if error_text:
                lines.append(f"  - Reason: `{error_text[:400]}`")
    else:
        lines.append("- None.")
    lines.append("")

    lines.append("## Heartbeat Autonomous Activity")
    if heartbeat_rows:
        for row in heartbeat_rows:
            lines.append(f"- {str(row.get('created_at') or '')}: {str(row.get('message') or '')}")
    else:
        lines.append("- None in the last window.")
    lines.append("")

    lines.append("## Non-Cron Autonomous Artifact Outputs")
    if non_cron_artifact_links:
        for link in non_cron_artifact_links:
            scope = str(link.get("scope") or "workspaces")
            rel = str(link.get("relative_path") or "")
            href = str(link.get("storage_href") or "")
            lines.append(f"- [{scope}: {rel}]({href})")
    else:
        lines.append("- None discovered from heartbeat/autonomous workspace outputs.")
    lines.append("")

    markdown = "\n".join(lines).strip() + "\n"
    md_path.write_text(markdown, encoding="utf-8")

    payload = {
        "generated_at": datetime.fromtimestamp(now_ts, timezone.utc).isoformat(),
        "window_seconds": rows.get("window_seconds"),
        "window_started_at": rows.get("window_started_at"),
        "window_ended_at": rows.get("window_ended_at"),
        "source_diagnostics": source_diagnostics,
        "warnings": warnings,
        "counts": {
            "completed": len(completed),
            "failed": len(failed),
            "heartbeat": len(heartbeat_rows),
            "requires_decision": len(requires_decision),
            "non_cron_artifacts": len(non_cron_artifact_links),
        },
        "completed": completed,
        "failed": failed,
        "heartbeat": heartbeat_rows,
        "non_cron_artifacts": non_cron_artifact_links,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    md_links = _artifact_links_for_path(md_path)
    json_links = _artifact_links_for_path(json_path)
    summary_line = (
        f"Daily autonomous briefing ready: {len(completed)} completed, {len(failed)} failed, "
        f"{len(requires_decision)} need decisions."
    )
    return {
        "summary_line": summary_line,
        "markdown": md_links,
        "json": json_links,
        "counts": payload.get("counts", {}),
        "day_slug": day_slug,
    }


def _normalize_interval_from_text(text: str) -> Optional[str]:
    raw = (text or "").strip().lower()
    if not raw:
        return None
    compact = raw.replace(" ", "")
    if re.match(r"^\d+[smhd]$", compact):
        return compact
    match = _SIMPLE_INTERVAL_RE.match(raw)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith(("second", "sec")) or unit == "s":
        suffix = "s"
    elif unit.startswith(("minute", "min")) or unit == "m":
        suffix = "m"
    elif unit.startswith(("hour", "hr")) or unit == "h":
        suffix = "h"
    else:
        suffix = "d"
    return f"{amount}{suffix}"


def _parse_time_of_day_for_daily_cron(text: str) -> Optional[tuple[int, int]]:
    raw = (text or "").strip().lower()
    if not raw:
        return None
    for prefix in ("today ", "tomorrow ", "tonight "):
        if raw.startswith(prefix):
            raw = raw[len(prefix):].strip()
            break
    if raw.startswith("at "):
        raw = raw[3:].strip()
    match = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", raw)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridiem = (match.group(3) or "").lower()
    if minute < 0 or minute > 59:
        return None
    if meridiem:
        if hour < 1 or hour > 12:
            return None
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
    elif hour > 23:
        return None
    return hour, minute


def _resolve_simplified_schedule_fields(
    schedule_time: str,
    repeat: bool,
    timezone_name: str,
) -> tuple[Optional[str], Optional[str], Optional[float], bool]:
    text = (schedule_time or "").strip()
    if not text:
        raise ValueError("schedule_time is required when using simplified chron input.")

    if repeat:
        every = _normalize_interval_from_text(text)
        if every:
            return every, None, None, False
        tod = _parse_time_of_day_for_daily_cron(text)
        if tod:
            hour, minute = tod
            return None, f"{minute} {hour} * * *", None, False
        raise ValueError(
            "For repeating jobs, use a relative interval like 'in 30 minutes' or a clock time like '4:30 pm'."
        )

    run_at_ts = parse_run_at(text, timezone_name=timezone_name)
    if run_at_ts is None:
        raise ValueError(
            "Invalid schedule_time. Use natural text like 'in 20 minutes' or '4:30 pm'."
        )
    return None, None, run_at_ts, True


def _schedule_text_suggests_repeat(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    if re.search(r"\b(every|daily|weekly|weekday|weekdays|monthly|yearly)\b", lowered):
        return True
    if lowered.startswith("at "):
        return True
    return False


def _resolve_simplified_schedule_update_fields(
    *,
    schedule_time: str,
    repeat: Optional[bool],
    timezone_name: str,
    job: Any,
) -> tuple[Optional[str], Optional[str], Optional[float], bool]:
    if repeat is None:
        existing_repeat = bool(getattr(job, "cron_expr", None) or int(getattr(job, "every_seconds", 0) or 0) > 0)
        inferred_repeat = _schedule_text_suggests_repeat(schedule_time)
        repeat_mode = inferred_repeat or existing_repeat
    else:
        repeat_mode = bool(repeat)
    return _resolve_simplified_schedule_fields(
        schedule_time=schedule_time,
        repeat=repeat_mode,
        timezone_name=timezone_name,
    )


class _AgentScheduleInterpretation(BaseModel):
    status: str = "ok"
    every: Optional[str] = None
    cron_expr: Optional[str] = None
    run_at: Optional[Any] = None
    delete_after_run: Optional[bool] = None
    reason: Optional[str] = None
    confidence: Optional[str] = None


def _chron_job_schedule_snapshot(job: Any) -> dict[str, Any]:
    return {
        "job_id": str(getattr(job, "job_id", "") or ""),
        "every_seconds": int(getattr(job, "every_seconds", 0) or 0),
        "cron_expr": getattr(job, "cron_expr", None),
        "run_at_epoch": getattr(job, "run_at", None),
        "timezone": str(getattr(job, "timezone", "UTC") or "UTC"),
        "delete_after_run": bool(getattr(job, "delete_after_run", False)),
    }


def _normalize_agent_every_value(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw.startswith("every "):
        raw = raw[6:].strip()
    if raw.startswith("in "):
        raw = raw[3:].strip()
    normalized = _normalize_interval_from_text(raw)
    if normalized:
        return normalized
    if re.match(r"^\d+[smhd]$", raw):
        return raw
    if re.match(r"^\d+$", raw):
        return f"{int(raw)}s"
    return None


def _coerce_agent_schedule_to_update_fields(
    *,
    interpretation: _AgentScheduleInterpretation,
    repeat: Optional[bool],
    timezone_name: str,
) -> tuple[Optional[str], Optional[str], Optional[float], bool]:
    status = (interpretation.status or "ok").strip().lower()
    if status not in {"ok", "applied"}:
        reason = (interpretation.reason or "").strip() or (
            "System configuration agent could not safely interpret the schedule request."
        )
        raise HTTPException(status_code=400, detail=reason)

    every = _normalize_agent_every_value(interpretation.every)
    cron_expr = str(interpretation.cron_expr).strip() if interpretation.cron_expr else None
    run_at_ts: Optional[float] = None
    if interpretation.run_at not in (None, ""):
        run_at_ts = parse_run_at(interpretation.run_at, timezone_name=timezone_name)
        if run_at_ts is None:
            raise ValueError("System configuration agent returned an invalid run_at value.")

    selected = int(every is not None) + int(cron_expr is not None) + int(run_at_ts is not None)
    if selected != 1:
        raise ValueError("System configuration agent must return exactly one schedule mode.")

    if repeat is True and run_at_ts is not None:
        raise ValueError("repeat=true requires a repeating schedule (every or cron_expr).")
    if repeat is False and run_at_ts is None:
        raise ValueError("repeat=false requires a one-shot schedule (run_at).")

    if run_at_ts is not None:
        # Simplified one-shot updates are always modeled as run once, then delete.
        delete_after_run = True
    else:
        delete_after_run = False

    return every, cron_expr, run_at_ts, delete_after_run


async def _get_or_create_system_configuration_session() -> GatewaySession:
    gateway = get_gateway()
    try:
        return await gateway.resume_session(_SYSTEM_CONFIGURATION_AGENT_SESSION_ID)
    except Exception:
        workspace = str(WORKSPACES_DIR / _SYSTEM_CONFIGURATION_AGENT_SESSION_ID)
        return await gateway.create_session(
            user_id="ops:system-configuration-agent",
            workspace_dir=workspace,
        )


def _build_schedule_interpretation_prompt(
    *,
    schedule_time: str,
    repeat: Optional[bool],
    timezone_name: str,
    job: Any,
) -> str:
    context = {
        "instruction": schedule_time,
        "repeat_override": repeat,
        "timezone": timezone_name,
        "now_utc": datetime.now(timezone.utc).isoformat(),
        "job_schedule_before": _chron_job_schedule_snapshot(job),
    }
    return (
        "Internal system configuration task.\n"
        "Interpret a natural-language schedule update for a chron job.\n"
        "This is runtime configuration work; delegate to "
        "Task(subagent_type='system-configuration-agent', ...) if needed.\n"
        "Return ONLY one JSON object, no markdown and no extra text.\n"
        "Schema:\n"
        "{\n"
        '  "status": "ok|needs_clarification|cannot_comply",\n'
        '  "every": "30m|null",\n'
        '  "cron_expr": "5-field cron expr|null",\n'
        '  "run_at": "ISO-8601 timestamp with timezone|null",\n'
        '  "delete_after_run": true|false|null,\n'
        '  "reason": "short explanation",\n'
        '  "confidence": "low|medium|high"\n'
        "}\n"
        "Rules:\n"
        "- Exactly one of every, cron_expr, run_at must be non-null when status is ok.\n"
        "- If repeat_override=true, do not return run_at.\n"
        "- If repeat_override=false, return run_at and delete_after_run=true.\n"
        "- If repeat_override is null, infer intent from instruction and current schedule.\n"
        "- Keep timezone handling explicit.\n"
        "Context JSON:\n"
        f"{json.dumps(context, ensure_ascii=True, indent=2)}\n"
    )


async def _interpret_schedule_with_system_configuration_agent(
    *,
    schedule_time: str,
    repeat: Optional[bool],
    timezone_name: str,
    job: Any,
) -> _AgentScheduleInterpretation:
    session = await _get_or_create_system_configuration_session()
    gateway = get_gateway()
    prompt = _build_schedule_interpretation_prompt(
        schedule_time=schedule_time,
        repeat=repeat,
        timezone_name=timezone_name,
        job=job,
    )
    result = await gateway.run_query(
        session,
        GatewayRequest(
            user_input=prompt,
            force_complex=True,
            metadata={
                "source": "ops",
                "operation": "chron_schedule_interpretation",
                "subagent_type": "system-configuration-agent",
            },
        ),
    )
    payload = extract_json_payload(
        result.response_text,
        model=_AgentScheduleInterpretation,
        require_model=True,
    )
    if not isinstance(payload, _AgentScheduleInterpretation):
        raise ValueError("System configuration agent did not return a valid schedule payload.")
    return payload


async def _resolve_simplified_schedule_update_fields_with_agent(
    *,
    schedule_time: str,
    repeat: Optional[bool],
    timezone_name: str,
    job: Any,
) -> tuple[Optional[str], Optional[str], Optional[float], bool]:
    try:
        interpretation = await _interpret_schedule_with_system_configuration_agent(
            schedule_time=schedule_time,
            repeat=repeat,
            timezone_name=timezone_name,
            job=job,
        )
        return _coerce_agent_schedule_to_update_fields(
            interpretation=interpretation,
            repeat=repeat,
            timezone_name=timezone_name,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(
            "System configuration schedule interpretation failed for job %s, falling back to deterministic parser: %s",
            getattr(job, "job_id", "unknown"),
            exc,
        )
        return _resolve_simplified_schedule_update_fields(
            schedule_time=schedule_time,
            repeat=repeat,
            timezone_name=timezone_name,
            job=job,
        )


def _ensure_autonomous_daily_briefing_job() -> Optional[dict[str, Any]]:
    if not _cron_service:
        return None
    enabled = (os.getenv("UA_AUTONOMOUS_DAILY_BRIEFING_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"})
    cron_expr = (
        (os.getenv("UA_AUTONOMOUS_DAILY_BRIEFING_CRON") or "").strip()
        or AUTONOMOUS_DAILY_BRIEFING_DEFAULT_CRON
    )
    timezone_name = (
        (os.getenv("UA_AUTONOMOUS_DAILY_BRIEFING_TIMEZONE") or "").strip()
        or AUTONOMOUS_DAILY_BRIEFING_DEFAULT_TIMEZONE
    )
    command = _build_autonomous_daily_briefing_command()
    metadata = {
        "system_job": AUTONOMOUS_DAILY_BRIEFING_JOB_KEY,
        "autonomous": True,
        "briefing": True,
        "source": "system",
        "session_id": "autonomous_daily_briefing",
    }

    existing = None
    for job in _cron_service.list_jobs():
        job_metadata = getattr(job, "metadata", {}) or {}
        if not isinstance(job_metadata, dict):
            continue
        if str(job_metadata.get("system_job") or "").strip() == AUTONOMOUS_DAILY_BRIEFING_JOB_KEY:
            existing = job
            break

    if existing is None:
        job = _cron_service.add_job(
            user_id="cron_system",
            workspace_dir=str(WORKSPACES_DIR / "cron_autonomous_daily_briefing"),
            command=command,
            cron_expr=cron_expr,
            timezone=timezone_name,
            delete_after_run=False,
            enabled=enabled,
            metadata=metadata,
        )
        logger.info(
            "⏰ Created autonomous daily briefing chron job id=%s cron=%s tz=%s enabled=%s",
            job.job_id,
            cron_expr,
            timezone_name,
            enabled,
        )
        return {**job.to_dict(), "running": job.job_id in _cron_service.running_jobs}

    updates: dict[str, Any] = {
        "command": command,
        "cron_expr": cron_expr,
        "timezone": timezone_name,
        "enabled": enabled,
        "metadata": metadata,
    }
    updated = _cron_service.update_job(existing.job_id, updates)
    logger.info(
        "⏰ Updated autonomous daily briefing chron job id=%s cron=%s tz=%s enabled=%s",
        updated.job_id,
        cron_expr,
        timezone_name,
        enabled,
    )
    return {**updated.to_dict(), "running": updated.job_id in _cron_service.running_jobs}


@app.get("/api/v1/cron/jobs")
async def list_cron_jobs():
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Chron service not available.")
    return {
        "jobs": [
            {**job.to_dict(), "running": job.job_id in _cron_service.running_jobs}
            for job in _cron_service.list_jobs()
        ]
    }


@app.post("/api/v1/cron/jobs")
async def create_cron_job(request: CronJobCreateRequest):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Chron service not available.")
    try:
        # Parse run_at (relative, ISO, or natural text in the request timezone)
        run_at_ts = parse_run_at(request.run_at, timezone_name=request.timezone) if request.run_at else None
        every_raw = request.every
        cron_expr = request.cron_expr
        delete_after_run = request.delete_after_run

        if request.schedule_time is not None:
            every_raw, cron_expr, run_at_ts, delete_after_run = _resolve_simplified_schedule_fields(
                schedule_time=request.schedule_time,
                repeat=bool(request.repeat),
                timezone_name=request.timezone,
            )
        elif request.repeat is not None and not (request.every or request.cron_expr or request.run_at):
            raise ValueError("repeat requires schedule_time when legacy fields are not provided.")
        
        job = _cron_service.add_job(
            user_id=request.user_id or "cron",
            workspace_dir=_sanitize_workspace_dir_or_400(request.workspace_dir),
            command=request.command,
            every_raw=every_raw,
            cron_expr=cron_expr,
            timezone=request.timezone,
            run_at=run_at_ts,
            delete_after_run=delete_after_run,
            model=request.model,
            timeout_seconds=request.timeout_seconds,
            enabled=request.enabled,
            metadata=request.metadata or {},
        )
        return {"job": {**job.to_dict(), "running": job.job_id in _cron_service.running_jobs}}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/v1/cron/jobs/{job_id}")
async def get_cron_job(job_id: str):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Chron service not available.")
    job = _cron_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Chron job not found")
    return {"job": {**job.to_dict(), "running": job.job_id in _cron_service.running_jobs}}


@app.put("/api/v1/cron/jobs/{job_id}")
async def update_cron_job(job_id: str, request: CronJobUpdateRequest):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Chron service not available.")
    job = _cron_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Chron job not found")
    
    # Build updates dict, only including non-None values
    if request.repeat is not None and request.schedule_time is None:
        raise HTTPException(status_code=400, detail="repeat requires schedule_time for simplified updates.")
    updates: dict = {}
    effective_tz = request.timezone if request.timezone is not None else job.timezone
    if request.command is not None:
        updates["command"] = request.command
    if request.schedule_time is not None:
        every_raw, cron_expr, run_at_ts, delete_after_run = await _resolve_simplified_schedule_update_fields_with_agent(
            schedule_time=request.schedule_time,
            repeat=request.repeat,
            timezone_name=effective_tz,
            job=job,
        )
        # Scheduling modes are mutually exclusive.
        updates["every_seconds"] = 0
        updates["cron_expr"] = None
        updates["run_at"] = None
        if every_raw is not None:
            updates["every"] = every_raw
        if cron_expr is not None:
            updates["cron_expr"] = cron_expr
        if run_at_ts is not None:
            updates["run_at"] = run_at_ts
        updates["delete_after_run"] = delete_after_run
    if request.timeout_seconds is not None:
        updates["timeout_seconds"] = request.timeout_seconds
    if request.every is not None:
        updates["every"] = request.every
    if request.cron_expr is not None:
        updates["cron_expr"] = request.cron_expr
    if request.timezone is not None:
        updates["timezone"] = request.timezone
    if request.run_at is not None:
        updates["run_at"] = parse_run_at(request.run_at, timezone_name=effective_tz)
    if request.delete_after_run is not None:
        updates["delete_after_run"] = request.delete_after_run
    if request.model is not None:
        updates["model"] = request.model
    if request.enabled is not None:
        updates["enabled"] = request.enabled
    if request.workspace_dir is not None:
        updates["workspace_dir"] = _sanitize_workspace_dir_or_400(request.workspace_dir)
    if request.user_id is not None:
        updates["user_id"] = request.user_id
    if request.metadata is not None:
        updates["metadata"] = request.metadata
    
    try:
        job = _cron_service.update_job(job_id, updates)
        return {"job": {**job.to_dict(), "running": job.job_id in _cron_service.running_jobs}}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/v1/cron/jobs/{job_id}")
async def delete_cron_job(job_id: str):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Chron service not available.")
    job = _cron_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Chron job not found")
    _cron_service.delete_job(job_id)
    return {"status": "deleted", "job_id": job_id}


@app.post("/api/v1/cron/jobs/{job_id}/run")
async def run_cron_job(job_id: str):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Chron service not available.")
    job = _cron_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Chron job not found")
    record = await _cron_service.run_job_now(job_id, reason="manual")
    return {"run": record.to_dict()}


@app.get("/api/v1/cron/jobs/{job_id}/runs")
async def list_cron_job_runs(job_id: str, limit: int = 200):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Chron service not available.")
    return {"runs": _cron_service.list_runs(job_id=job_id, limit=limit)}


@app.get("/api/v1/cron/runs")
async def list_cron_runs(limit: int = 200):
    if not _cron_service:
        raise HTTPException(status_code=400, detail="Chron service not available.")
    return {"runs": _cron_service.list_runs(limit=limit)}


@app.get("/api/v1/sessions/{session_id}")
async def get_session_info(session_id: str, request: Request):
    _require_session_api_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    session = get_session(session_id)
    if not session:
        _increment_metric("resume_attempts")
        gateway = get_gateway()
        try:
            session = await gateway.resume_session(session_id)
            session.metadata["user_id"] = session.user_id
            policy = _session_policy(session)
            session.metadata["policy"] = _policy_metadata_snapshot(policy)
            store_session(session)
            _increment_metric("resume_successes")
            if _heartbeat_service:
                _heartbeat_service.register_session(session)
        except ValueError:
            _increment_metric("resume_failures")
            raise HTTPException(status_code=404, detail="Session not found")
            
    # Allowlist check for resume (optional, but good practice)
    if not is_user_allowed(session.user_id):
        raise HTTPException(status_code=403, detail="Access denied: User not allowed.")

    session.metadata.setdefault("user_id", session.user_id)

    return CreateSessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        workspace_dir=session.workspace_dir,
        metadata=session.metadata,
    )


@app.get("/api/v1/sessions/{session_id}/policy")
async def get_session_policy(session_id: str, request: Request):
    _require_session_api_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    policy = _session_policy(session)
    return {"session_id": session_id, "policy": policy}


@app.patch("/api/v1/sessions/{session_id}/policy")
async def patch_session_policy(session_id: str, payload: SessionPolicyPatchRequest, request: Request):
    _require_session_api_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    updated = update_session_policy(
        session.workspace_dir,
        payload.patch or {},
        session_id=session.session_id,
        user_id=session.user_id,
    )
    session.metadata["policy"] = _policy_metadata_snapshot(updated)
    return {"session_id": session_id, "policy": updated}


@app.get("/api/v1/sessions/{session_id}/pending")
async def get_pending_gate(session_id: str, request: Request):
    _require_session_api_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    pending = _pending_gated_requests.get(session_id)
    return {"session_id": session_id, "pending": pending}


@app.post("/api/v1/sessions/{session_id}/resume")
async def resume_gated_request(session_id: str, payload: ResumeRequest, request: Request):
    _require_session_api_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    pending = _pending_gated_requests.get(session_id)
    if not pending:
        raise HTTPException(status_code=404, detail="No pending gated request")

    approval_id = pending.get("approval_id")
    if payload.approval_id and approval_id and payload.approval_id != approval_id:
        raise HTTPException(status_code=400, detail="approval_id mismatch")

    approval_record = None
    if approval_id:
        approval_record = update_approval(
            approval_id,
            {
                "status": "approved",
                "notes": payload.reason or "Approved via resume endpoint",
                "metadata": {"resumed_at": datetime.now(timezone.utc).isoformat()},
            },
        )
    pending["status"] = "approved"
    pending["updated_at"] = datetime.now(timezone.utc).isoformat()
    return {"session_id": session_id, "pending": pending, "approval": approval_record}


@app.delete("/api/v1/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    _require_session_api_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    session = _sessions.get(session_id)
    if session:
        try:
            workspace = Path(session.workspace_dir)
            shared_root = resolve_shared_memory_workspace(str(workspace))
            broker = get_memory_orchestrator(workspace_dir=shared_root)
            broker.capture_session_rollover(
                session_id=session_id,
                trigger="api_delete",
                transcript_path=str(workspace / "transcript.md"),
                run_log_path=str(workspace / "run.log"),
            )
        except Exception as exc:
            logger.warning("Session delete memory capture failed (%s): %s", session_id, exc)
    _mark_session_terminal(session_id, "deleted")
    _sessions.pop(session_id, None)
    _pending_gated_requests.pop(session_id, None)
    _session_turn_state.pop(session_id, None)
    _session_turn_locks.pop(session_id, None)
    gateway = get_gateway()
    await gateway.close_session(session_id)
    _session_runtime.pop(session_id, None)
    if _heartbeat_service:
        _heartbeat_service.unregister_session(session_id)
    return {"status": "deleted", "session_id": session_id}


# =============================================================================
# Ops / Control Plane Endpoints
# =============================================================================


@app.post("/api/v1/ops/notifications")
async def ops_create_notification(request: Request, payload: OpsNotificationCreateRequest):
    _require_ops_auth(request)
    session_id = None
    if payload.session_id:
        session_id = _sanitize_session_id_or_400(payload.session_id)
    severity = str(payload.severity or "warning").strip().lower()
    if severity not in {"info", "warning", "error", "success"}:
        severity = "warning"
    record = _add_notification(
        kind=str(payload.kind or "system_event").strip() or "system_event",
        title=str(payload.title or "System Event").strip() or "System Event",
        message=str(payload.message or "").strip() or "No details provided.",
        session_id=session_id,
        severity=severity,
        requires_action=bool(payload.requires_action),
        metadata=payload.metadata if isinstance(payload.metadata, dict) else None,
    )
    return {"notification": record}


@app.get("/api/v1/ops/sessions")
async def ops_list_sessions(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    status: str = "all",
    source: str = "all",
    owner: Optional[str] = None,
    memory_mode: str = "all",
):
    _require_ops_auth(request)
    try:
        if not _ops_service:
            raise HTTPException(status_code=503, detail="Ops service not initialized")
        summaries = _ops_service.list_sessions(
            status_filter=status,
            source_filter=source,
            owner_filter=owner,
            memory_mode_filter=memory_mode,
        )
        return {
            "sessions": summaries[offset : offset + limit],
            "total": len(summaries),
            "limit": limit,
            "offset": offset,
        }
    except HTTPException:
        raise
    except Exception:
        logger.exception("CRITICAL: Failed to list sessions")
        raise HTTPException(status_code=500, detail="Internal Server Error: check gateway logs")


@app.post("/api/v1/ops/sessions/cancel")
async def ops_cancel_outstanding_sessions(request: Request, payload: OpsSessionCancelRequest):
    _require_ops_auth(request)
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")

    reason = (payload.reason or "Cancelled from ops bulk session controls").strip()
    if not reason:
        reason = "Cancelled from ops bulk session controls"

    sessions = _ops_service.list_sessions(status_filter="all")
    candidates: list[dict[str, Any]] = []
    for item in sessions:
        status = str(item.get("status", "")).lower()
        active_runs = int(item.get("active_runs") or 0)
        if status in {"running", "active"} or active_runs > 0:
            candidates.append(item)

    cancelled: list[str] = []
    for item in candidates:
        session_id = str(item.get("session_id", "")).strip()
        if not session_id:
            continue
        await _cancel_session_execution(session_id, reason)
        cancelled.append(session_id)

    return {
        "status": "cancel_requested",
        "reason": reason,
        "sessions_considered": len(candidates),
        "sessions_cancelled": cancelled,
    }


@app.post("/api/v1/ops/sessions/csi/purge")
async def ops_purge_csi_sessions(request: Request, payload: OpsCsiSessionPurgeRequest):
    _require_ops_auth(request)
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")

    keep_latest = max(0, min(int(payload.keep_latest or 0), 200))
    older_than_minutes = max(0, min(int(payload.older_than_minutes or 0), 60 * 24 * 30))
    older_than_seconds = older_than_minutes * 60
    include_active = bool(payload.include_active)

    sessions = _ops_service.list_sessions(status_filter="all")
    csi_sessions = [
        item
        for item in sessions
        if str(item.get("session_id") or "").strip().lower().startswith("session_hook_csi_")
    ]

    def _activity_epoch(item: dict[str, Any]) -> Optional[float]:
        dt = _parse_iso_timestamp(item.get("last_activity")) or _parse_iso_timestamp(item.get("last_modified"))
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()

    csi_sessions.sort(
        key=lambda item: (_activity_epoch(item) or 0.0, str(item.get("session_id") or "")),
        reverse=True,
    )

    protected_ids = {
        str(item.get("session_id") or "").strip()
        for item in csi_sessions[:keep_latest]
        if str(item.get("session_id") or "").strip()
    }

    now_ts = time.time()
    candidates: list[str] = []
    skipped_recent: list[str] = []
    skipped_active: list[str] = []
    skipped_protected: list[str] = []

    for item in csi_sessions:
        session_id = str(item.get("session_id") or "").strip()
        if not session_id:
            continue
        if session_id in protected_ids:
            skipped_protected.append(session_id)
            continue

        status = str(item.get("status") or "").strip().lower()
        active_runs = int(item.get("active_runs") or 0)
        is_active = status in {"running", "active"} or active_runs > 0
        if is_active and not include_active:
            skipped_active.append(session_id)
            continue

        if older_than_seconds > 0:
            activity_ts = _activity_epoch(item)
            if activity_ts is None:
                skipped_recent.append(session_id)
                continue
            if (now_ts - activity_ts) < older_than_seconds:
                skipped_recent.append(session_id)
                continue

        candidates.append(session_id)

    if payload.dry_run:
        return {
            "status": "dry_run",
            "total_csi_sessions": len(csi_sessions),
            "keep_latest": keep_latest,
            "older_than_minutes": older_than_minutes,
            "include_active": include_active,
            "candidates": candidates,
            "skipped": {
                "protected": skipped_protected,
                "active": skipped_active,
                "recent": skipped_recent,
            },
        }

    deleted: list[str] = []
    failed: dict[str, str] = {}
    for session_id in candidates:
        try:
            if include_active:
                try:
                    await _cancel_session_execution(session_id, "CSI session purge requested")
                except Exception:
                    logger.debug("CSI purge cancel skipped for session=%s", session_id, exc_info=True)
            removed = await _ops_service.delete_session(session_id)
            if removed:
                deleted.append(session_id)
            else:
                failed[session_id] = "not_found_or_not_deleted"
        except Exception as exc:
            failed[session_id] = str(exc)

    return {
        "status": "ok",
        "total_csi_sessions": len(csi_sessions),
        "keep_latest": keep_latest,
        "older_than_minutes": older_than_minutes,
        "include_active": include_active,
        "deleted": deleted,
        "failed": failed,
        "skipped": {
            "protected": skipped_protected,
            "active": skipped_active,
            "recent": skipped_recent,
        },
    }


@app.get("/api/v1/ops/calendar/events")
async def ops_calendar_events(
    request: Request,
    start: Optional[str] = None,
    end: Optional[str] = None,
    view: str = "week",
    source: str = "all",
    owner: Optional[str] = None,
    timezone_name: Optional[str] = None,
):
    _require_ops_auth(request)
    _scheduling_counter_inc("calendar_events_requests")
    started = time.perf_counter()
    tz_name = _calendar_timezone_or_default(timezone_name)
    start_ts, end_ts = _calendar_normalize_window(
        start=start,
        end=end,
        view=view,
        timezone_name=tz_name,
    )
    _calendar_cleanup_state()
    feed = _calendar_build_feed(
        start_ts=start_ts,
        end_ts=end_ts,
        timezone_name=tz_name,
        source_filter=source,
        owner=owner,
    )
    stasis_items = [item for item in _calendar_missed_events.values() if item.get("status") == "pending"]
    stasis_items.sort(key=lambda item: str(item.get("created_at") or ""))
    _scheduling_record_projection_sample(
        duration_ms=(time.perf_counter() - started) * 1000.0,
        events=feed["events"],
        always_running=feed["always_running"],
        stasis_count=len(stasis_items),
    )
    return {
        "timezone": tz_name,
        "view": view,
        "start_utc": datetime.fromtimestamp(start_ts, timezone.utc).isoformat(),
        "end_utc": datetime.fromtimestamp(end_ts, timezone.utc).isoformat(),
        "start_local": _calendar_local_iso(start_ts, tz_name),
        "end_local": _calendar_local_iso(end_ts, tz_name),
        "events": feed["events"],
        "always_running": feed["always_running"],
        "stasis_queue": stasis_items,
        "legend": {
            "heartbeat": "sky",
            "cron": "blue",
            "missed": "amber",
            "success": "emerald",
            "failed": "rose",
            "disabled": "slate",
        },
    }


@app.post("/api/v1/ops/calendar/events/{event_id}/action")
async def ops_calendar_event_action(
    request: Request,
    event_id: str,
    payload: CalendarEventActionRequest,
):
    _require_ops_auth(request)
    _scheduling_counter_inc("calendar_action_requests")
    source, source_ref, _scheduled_at = _calendar_parse_event_id(event_id)
    tz_name = _calendar_timezone_or_default(payload.timezone)
    result = await _calendar_apply_event_action(
        source=source,
        source_ref=source_ref,
        action=payload.action,
        event_id=event_id,
        run_at=payload.run_at,
        timezone_name=tz_name,
    )
    return {
        "event_id": event_id,
        "source": source,
        "source_ref": source_ref,
        **result,
    }


@app.post("/api/v1/ops/calendar/events/{event_id}/change-request")
async def ops_calendar_event_change_request(
    request: Request,
    event_id: str,
    payload: CalendarEventChangeRequest,
):
    _require_ops_auth(request)
    _scheduling_counter_inc("calendar_change_request_requests")
    tz_name = _calendar_timezone_or_default(payload.timezone)
    proposal = _calendar_create_change_proposal(
        event_id=event_id,
        instruction=payload.instruction,
        timezone_name=tz_name,
    )
    return {"proposal": proposal}


@app.post("/api/v1/ops/calendar/events/{event_id}/change-request/confirm")
async def ops_calendar_event_change_confirm(
    request: Request,
    event_id: str,
    payload: CalendarEventChangeConfirmRequest,
):
    _require_ops_auth(request)
    _scheduling_counter_inc("calendar_change_confirm_requests")
    proposal = _calendar_change_proposals.get(payload.proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if str(proposal.get("event_id")) != event_id:
        raise HTTPException(status_code=400, detail="Proposal does not match event")
    if not payload.approve:
        proposal["status"] = "rejected"
        proposal["resolved_at"] = datetime.now(timezone.utc).isoformat()
        return {"status": "rejected", "proposal": proposal}

    operation = proposal.get("operation") or {}
    op_type = str(operation.get("type") or "").strip().lower()
    source = str(proposal.get("source") or "")
    source_ref = str(proposal.get("source_ref") or "")
    result: dict[str, Any]
    if op_type == "cron_set_enabled":
        if not _cron_service:
            raise HTTPException(status_code=503, detail="Chron service not available")
        updated = _cron_service.update_job(source_ref, {"enabled": bool(operation.get("enabled"))})
        result = {"job": updated.to_dict()}
    elif op_type == "cron_run_now":
        if not _cron_service:
            raise HTTPException(status_code=503, detail="Chron service not available")
        run = await _cron_service.run_job_now(source_ref, reason="calendar_change_request")
        result = {"run": run.to_dict()}
    elif op_type == "cron_set_interval":
        if not _cron_service:
            raise HTTPException(status_code=503, detail="Chron service not available")
        every_seconds = int(operation.get("every_seconds") or 0)
        if every_seconds <= 0:
            raise HTTPException(status_code=400, detail="Invalid interval")
        updated = _cron_service.update_job(source_ref, {"every_seconds": every_seconds, "run_at": None})
        result = {"job": updated.to_dict()}
    elif op_type == "cron_backfill_schedule":
        if not _cron_service:
            raise HTTPException(status_code=503, detail="Chron service not available")
        job = _cron_service.get_job(source_ref)
        if not job:
            raise HTTPException(status_code=404, detail="Chron job not found")
        run_at = float(operation.get("run_at") or 0.0)
        if run_at <= 0:
            raise HTTPException(status_code=400, detail="Invalid run_at")
        # Keep one-shot jobs on the same job_id when rescheduling so
        # calendar actions and run history stay correlated to the job
        # the user actually edited.
        if job.run_at is not None and bool(job.delete_after_run):
            updated = _cron_service.update_job(
                source_ref,
                {
                    "run_at": run_at,
                    "enabled": True,
                },
            )
            result = {
                "job": updated.to_dict(),
                "mode": "updated_existing_one_shot",
            }
        else:
            created = _cron_service.add_job(
                user_id=job.user_id,
                workspace_dir=job.workspace_dir,
                command=job.command,
                run_at=run_at,
                delete_after_run=True,
                enabled=True,
                metadata={**(job.metadata or {}), "backfill_for_job_id": source_ref},
            )
            result = {
                "job": created.to_dict(),
                "mode": "created_backfill_job",
            }
    elif op_type == "heartbeat_set_delivery":
        mode = str(operation.get("mode") or "last").strip().lower()
        if mode not in {"none", "last"}:
            raise HTTPException(status_code=400, detail="Unsupported heartbeat mode")
        applied = _calendar_apply_heartbeat_delivery_mode(source_ref, mode)
        result = {"heartbeat": applied}
    elif op_type == "heartbeat_run_now":
        if not _heartbeat_service:
            raise HTTPException(status_code=503, detail="Heartbeat service not available")
        _heartbeat_service.request_heartbeat_now(source_ref, reason="calendar_change_request")
        result = {"heartbeat": {"session_id": source_ref, "action": "run_now"}}
    elif op_type == "heartbeat_set_interval":
        every_seconds = int(operation.get("every_seconds") or 0)
        if every_seconds <= 0:
            raise HTTPException(status_code=400, detail="Invalid heartbeat interval")
        applied = _calendar_apply_heartbeat_interval(source_ref, every_seconds)
        result = {"heartbeat": applied}
    else:
        raise HTTPException(status_code=400, detail="Proposal operation cannot be applied")

    proposal["status"] = "applied"
    proposal["resolved_at"] = datetime.now(timezone.utc).isoformat()
    proposal["result"] = result
    return {
        "status": "applied",
        "event_id": event_id,
        "source": source,
        "source_ref": source_ref,
        "proposal": proposal,
        "result": result,
    }


@app.get("/api/v1/ops/metrics/session-continuity")
async def ops_session_continuity_metrics(request: Request):
    _require_ops_auth(request)
    return {"metrics": _observability_metrics_snapshot()}


@app.get("/api/v1/ops/metrics/scheduling-runtime")
async def ops_scheduling_runtime_metrics(request: Request):
    _require_ops_auth(request)
    return {"metrics": _scheduling_runtime_metrics_snapshot()}


@app.get("/api/v1/ops/metrics/activity-events")
async def ops_activity_events_metrics(request: Request):
    _require_ops_auth(request)
    return {"metrics": _activity_runtime_metrics_snapshot()}


@app.post("/api/v1/ops/reconcile/todoist-chron")
async def ops_reconcile_todoist_chron(
    request: Request,
    dry_run: bool = False,
    remove_stale: Optional[bool] = None,
):
    _require_ops_auth(request)
    effective_remove_stale = (
        TODOIST_CHRON_RECONCILE_REMOVE_STALE if remove_stale is None else bool(remove_stale)
    )
    result = _reconcile_todoist_chron_mappings(
        remove_stale=effective_remove_stale,
        dry_run=bool(dry_run),
    )
    metrics = _scheduling_runtime_metrics_snapshot().get("todoist_chron_reconciliation", {})
    return {"status": "ok", "reconciliation": result, "metrics": metrics}


@app.get("/api/v1/ops/metrics/vp-bridge")
async def ops_vp_bridge_metrics(request: Request):
    _require_ops_auth(request)
    return {"metrics": _vp_event_bridge_snapshot()}


@app.post("/api/v1/ops/vp/bridge/cursor")
async def ops_vp_bridge_cursor_update(
    request: Request,
    body: OpsVpBridgeCursorUpdateRequest,
):
    _require_ops_auth(request)
    update = _vp_event_bridge_control_cursor(action=body.action, requested_rowid=body.rowid)
    return {"status": "ok", "update": update, "metrics": _vp_event_bridge_snapshot()}


@app.get("/api/v1/ops/metrics/vp")
async def ops_vp_metrics(
    request: Request,
    vp_id: str,
    mission_limit: int = 50,
    event_limit: int = 200,
):
    _require_ops_auth(request)
    vp_identifier = (vp_id or "").strip()
    if not vp_identifier:
        raise HTTPException(status_code=400, detail="vp_id is required")
    try:
        return _vp_metrics_snapshot(
            vp_id=vp_identifier,
            mission_limit=max(1, min(int(mission_limit), 500)),
            event_limit=max(1, min(int(event_limit), 1000)),
            storage_lane="external",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _vp_api_error("metrics_external", exc) from exc


@app.get("/api/v1/ops/vp/sessions")
async def ops_vp_sessions(
    request: Request,
    status: str = "all",
    limit: int = 100,
):
    _require_ops_auth(request)
    gateway = get_gateway()
    conn = _external_vp_conn(gateway)
    statuses = None
    if status.strip().lower() != "all":
        statuses = [part.strip().lower() for part in status.split(",") if part.strip()]
    try:
        rows = list_vp_sessions(conn, statuses=statuses, limit=max(1, min(int(limit), 500)))
        runtime_conn = getattr(gateway, "_runtime_db_conn", None)
        if not rows and runtime_conn is not None and runtime_conn is not conn:
            rows = list_vp_sessions(runtime_conn, statuses=statuses, limit=max(1, min(int(limit), 500)))
        return {"sessions": [_vp_session_to_dict(row) for row in rows]}
    except HTTPException:
        raise
    except Exception as exc:
        raise _vp_api_error("list_sessions", exc) from exc


@app.get("/api/v1/ops/vp/missions")
async def ops_vp_missions(
    request: Request,
    vp_id: str = "",
    status: str = "all",
    limit: int = 100,
):
    _require_ops_auth(request)
    gateway = get_gateway()
    conn = _external_vp_conn(gateway)
    statuses = None
    if status.strip().lower() != "all":
        statuses = [part.strip().lower() for part in status.split(",") if part.strip()]
    vp_identifier = vp_id.strip() or None
    try:
        rows = list_vp_missions(
            conn,
            vp_id=vp_identifier,
            statuses=statuses,
            limit=max(1, min(int(limit), 500)),
        )
        runtime_conn = getattr(gateway, "_runtime_db_conn", None)
        if not rows and runtime_conn is not None and runtime_conn is not conn:
            rows = list_vp_missions(
                runtime_conn,
                vp_id=vp_identifier,
                statuses=statuses,
                limit=max(1, min(int(limit), 500)),
            )
        return {"missions": [_vp_mission_to_dict(row) for row in rows]}
    except HTTPException:
        raise
    except Exception as exc:
        raise _vp_api_error("list_missions", exc) from exc


@app.post("/api/v1/ops/vp/missions/dispatch")
async def ops_vp_dispatch_mission(
    request: Request,
    body: VpMissionDispatchRequest,
):
    _require_ops_auth(request)
    gateway = get_gateway()
    conn = _external_vp_conn(gateway)
    vp_identifier = body.vp_id.strip()
    if not vp_identifier:
        raise HTTPException(status_code=400, detail="vp_id is required")
    objective = body.objective.strip()
    if not objective:
        raise HTTPException(status_code=400, detail="objective is required")

    try:
        mission = dispatch_mission_with_retry(
            conn=conn,
            request=MissionDispatchRequest(
                vp_id=vp_identifier,
                mission_type=body.mission_type.strip() or "task",
                objective=objective,
                constraints=dict(body.constraints or {}),
                budget=dict(body.budget or {}),
                idempotency_key=(body.idempotency_key or "").strip(),
                source_session_id=(body.source_session_id or "ops.dispatch").strip(),
                source_turn_id=(body.source_turn_id or "").strip(),
                reply_mode=(body.reply_mode or "async").strip() or "async",
                priority=int(body.priority or 100),
                run_id=(body.run_id or "").strip() or None,
            ),
            workspace_base=WORKSPACES_DIR,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise _vp_api_error("dispatch_mission", exc) from exc
    return {"mission": _vp_mission_to_dict(mission)}


@app.post("/api/v1/ops/vp/missions/{mission_id}/cancel")
async def ops_vp_cancel_mission(
    request: Request,
    mission_id: str,
    body: VpMissionCancelRequest,
):
    _require_ops_auth(request)
    gateway = get_gateway()
    conn = _external_vp_conn(gateway)
    if not mission_id.strip():
        raise HTTPException(status_code=400, detail="mission_id is required")
    try:
        cancelled = cancel_mission(conn, mission_id.strip(), reason=(body.reason or "cancel_requested"))
        if not cancelled:
            raise HTTPException(status_code=404, detail="Mission not found or not cancellable")
        mission = get_vp_mission(conn, mission_id.strip())
        return {"status": "cancel_requested", "mission": _vp_mission_to_dict(mission)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _vp_api_error("cancel_mission", exc) from exc


@app.get("/api/v1/ops/metrics/coder-vp")
async def ops_coder_vp_metrics(
    request: Request,
    vp_id: str = "vp.coder.primary",
    mission_limit: int = 50,
    event_limit: int = 200,
):
    _require_ops_auth(request)
    vp_identifier = (vp_id or "").strip()
    if not vp_identifier:
        raise HTTPException(status_code=400, detail="vp_id is required")

    clamped_mission_limit = max(1, min(int(mission_limit), 500))
    clamped_event_limit = max(1, min(int(event_limit), 1000))
    try:
        return _vp_metrics_snapshot(
            vp_id=vp_identifier,
            mission_limit=clamped_mission_limit,
            event_limit=clamped_event_limit,
            storage_lane="coder",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _vp_api_error("metrics_coder", exc) from exc


@app.get("/api/v1/ops/scheduling/events")
async def ops_scheduling_events(
    request: Request,
    since_seq: int = 0,
    limit: int = 500,
):
    _require_ops_auth(request)
    _scheduling_counter_inc("push_replay_requests")
    events = _scheduling_event_bus.snapshot(since_seq=max(0, int(since_seq)), limit=max(1, min(int(limit), 5000)))
    metrics = _scheduling_runtime_metrics_snapshot()
    projection_state = metrics.get("projection_state", {}) if isinstance(metrics, dict) else {}
    return {
        "events": events,
        "projection_version": int(projection_state.get("version", 0) or 0),
        "projection_last_event_seq": int(projection_state.get("last_event_seq", 0) or 0),
        "event_bus_seq": int((_scheduling_event_bus.info() or {}).get("seq", 0) or 0),
    }


@app.get("/api/v1/ops/scheduling/stream")
async def ops_scheduling_stream(
    request: Request,
    since_seq: int = 0,
    heartbeat_seconds: int = 20,
    limit: int = 500,
    once: bool = False,
    ops_token: Optional[str] = None,
):
    _require_ops_auth(request, token_override=ops_token)
    if not SCHED_PUSH_ENABLED:
        raise HTTPException(status_code=503, detail="Scheduling push stream disabled.")
    since = max(0, int(since_seq))
    max_items = max(1, min(int(limit), 5000))
    heartbeat_wait = max(2, min(int(heartbeat_seconds), 60))

    async def event_gen():
        nonlocal since
        _scheduling_counter_inc("push_stream_connects")
        emitted = 0
        try:
            while True:
                if await request.is_disconnected():
                    break
                events = await _scheduling_event_bus.wait_for_events(
                    since_seq=since,
                    timeout_seconds=float(heartbeat_wait),
                    limit=max_items,
                )
                metrics = _scheduling_runtime_metrics_snapshot()
                projection_state = metrics.get("projection_state", {}) if isinstance(metrics, dict) else {}
                projection_version = int(projection_state.get("version", 0) or 0)
                projection_last_event_seq = int(projection_state.get("last_event_seq", 0) or 0)
                if events:
                    for event in events:
                        seq = int(event.get("seq", 0) or 0)
                        if seq > since:
                            since = seq
                        payload = {
                            "kind": "event",
                            "event": event,
                            "projection_version": projection_version,
                            "projection_last_event_seq": projection_last_event_seq,
                        }
                        _scheduling_counter_inc("push_stream_event_payloads")
                        yield f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
                        emitted += 1
                        if once and emitted >= 1:
                            return
                else:
                    keepalive = {
                        "kind": "heartbeat",
                        "seq": since,
                        "projection_version": projection_version,
                        "projection_last_event_seq": projection_last_event_seq,
                    }
                    _scheduling_counter_inc("push_stream_keepalives")
                    yield f"data: {json.dumps(keepalive, separators=(',', ':'))}\n\n"
                    emitted += 1
                    if once and emitted >= 1:
                        return
        finally:
            _scheduling_counter_inc("push_stream_disconnects")

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_gen(), media_type="text/event-stream", headers=headers)


@app.get("/api/v1/ops/sessions/{session_id}")
async def ops_get_session(request: Request, session_id: str):
    _require_ops_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")
    details = _ops_service.get_session_details(session_id)
    if not details:
        raise HTTPException(status_code=404, detail="Session not found")
    return details


@app.get("/api/v1/ops/sessions/{session_id}/preview")
async def ops_session_preview(
    request: Request, session_id: str, limit: int = 200, max_bytes: int = 200_000
):
    _require_ops_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")
    result = _ops_service.tail_file(session_id, "activity_journal.log", limit=limit, max_bytes=max_bytes)
    return {"session_id": session_id, **result}


@app.post("/api/v1/ops/sessions/{session_id}/reset")
async def ops_session_reset(
    request: Request, session_id: str, payload: OpsSessionResetRequest
):
    _require_ops_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")
    result = _ops_service.reset_session(
        session_id,
        clear_logs=payload.clear_logs,
        clear_memory=payload.clear_memory,
        clear_work_products=payload.clear_work_products,
    )
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.post("/api/v1/ops/sessions/{session_id}/archive")
async def ops_session_archive(
    request: Request, session_id: str, payload: OpsSessionArchiveRequest
):
    _require_ops_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")
    result = _ops_service.archive_session(
        session_id,
        clear_memory=payload.clear_memory,
        clear_work_products=payload.clear_work_products,
    )
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.post("/api/v1/ops/sessions/{session_id}/cancel")
async def ops_session_cancel(
    request: Request, session_id: str, payload: OpsSessionCancelRequest
):
    _require_ops_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")
    if not _ops_service.get_session_details(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    reason = (payload.reason or "Cancelled from ops panel").strip()
    if not reason:
        reason = "Cancelled from ops panel"
    return await _cancel_session_execution(session_id, reason)


@app.post("/api/v1/ops/sessions/{session_id}/compact")
async def ops_session_compact(
    request: Request, session_id: str, payload: OpsSessionCompactRequest
):
    _require_ops_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")
    result = _ops_service.compact_session(session_id, payload.max_lines, payload.max_bytes)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.delete("/api/v1/ops/sessions/{session_id}")
async def ops_delete_session(request: Request, session_id: str, confirm: bool = False):
    _require_ops_auth(request)
    session_id = _sanitize_session_id_or_400(session_id)
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true is required")
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")
    
    deleted = await _ops_service.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted", "session_id": session_id}


@app.get("/api/v1/ops/logs/tail")
async def ops_logs_tail(
    request: Request,
    session_id: Optional[str] = None,
    path: Optional[str] = None,
    cursor: Optional[int] = None,
    limit: int = 200,
    max_bytes: int = 250_000,
):
    _require_ops_auth(request)
    if not _ops_service:
        raise HTTPException(status_code=503, detail="Ops service not initialized")

    if session_id:
        session_id = _sanitize_session_id_or_400(session_id)
        result = _ops_service.tail_file(session_id, "run.log", cursor=cursor, limit=limit, max_bytes=max_bytes)
        file_path = str(_ops_service.workspaces_dir / session_id / "run.log")
        return {"file": file_path, **result}
    elif path:
        try:
            candidate = resolve_ops_log_path(WORKSPACES_DIR, path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result = _ops_service.read_log_slice(candidate, cursor=cursor, limit=limit, max_bytes=max_bytes)
        return {"file": str(candidate), **result}
    else:
        raise HTTPException(status_code=400, detail="session_id or path required")


@app.get("/api/v1/ops/skills")
async def ops_skills_status(request: Request):
    _require_ops_auth(request)
    try:
        return {"skills": _load_skill_catalog()}
    except Exception:
        logger.exception("CRITICAL: Failed to list skills")
        raise HTTPException(status_code=500, detail="Internal Server Error: check gateway logs")


@app.patch("/api/v1/ops/skills/{skill_key}")
async def ops_skill_update(request: Request, skill_key: str, payload: OpsSkillUpdateRequest):
    _require_ops_auth(request)
    config = load_ops_config()
    skills_cfg = config.get("skills", {})
    entries = skills_cfg.get("entries", {})
    if not isinstance(entries, dict):
        entries = {}
    normalized = skill_key.strip().lower()
    entry = entries.get(normalized, {})
    if not isinstance(entry, dict):
        entry = {}
    if payload.enabled is not None:
        entry["enabled"] = payload.enabled
    entries[normalized] = entry
    skills_cfg["entries"] = entries
    config["skills"] = skills_cfg
    write_ops_config(config)
    return {"status": "updated", "skill": normalized, "config": entry}


@app.get("/api/v1/ops/skills/{skill_key}/doc")
async def ops_skill_doc(request: Request, skill_key: str):
    _require_ops_auth(request)
    catalog = _load_skill_catalog()
    normalized = skill_key.strip().lower()
    logger.info(f"Docs requested for skill: '{skill_key}' (normalized: '{normalized}')")
    
    for s in catalog:
        name_norm = str(s.get("name", "")).strip().lower()
        if name_norm == normalized:
            path = s.get("path")
            logger.info(f"Found match: {s.get('name')} at path: {path}")
            if path and os.path.exists(path):
                return {"skill": s.get("name"), "content": Path(path).read_text(encoding="utf-8")}
            else:
                logger.warning(f"Skill path does not exist: {path}")
                # Fallback: check if path is relative to repo root?
                # The path should be absolute from _load_skill_catalog but let's be sure
                return {"skill": s.get("name"), "content": f"Use locally at: {path}\n\n(File not found at server runtime)"}
                
    logger.warning(f"Skill not found in catalog: {normalized}. Available: {[s.get('name') for s in catalog]}")
    raise HTTPException(status_code=404, detail="Skill documentation not found")


@app.get("/api/v1/ops/channels")
async def ops_channels_status(request: Request):
    _require_ops_auth(request)
    return {"channels": _load_channel_status()}


@app.post("/api/v1/ops/channels/{channel_id}/probe")
async def ops_channels_probe(request: Request, channel_id: str, timeout: float = 4.0):
    _require_ops_auth(request)
    result = await _probe_channel(channel_id, timeout=timeout)
    _channel_probe_results[channel_id.strip().lower()] = result
    return {"probe": result}


@app.post("/api/v1/ops/channels/{channel_id}/logout")
async def ops_channels_logout(request: Request, channel_id: str):
    _require_ops_auth(request)
    config = load_ops_config()
    channels_cfg = config.get("channels", {})
    entries = channels_cfg.get("entries", {})
    if not isinstance(entries, dict):
        entries = {}
    normalized = channel_id.strip().lower()
    entry = entries.get(normalized, {})
    if not isinstance(entry, dict):
        entry = {}
    entry["enabled"] = False
    entries[normalized] = entry
    channels_cfg["entries"] = entries
    config["channels"] = channels_cfg
    write_ops_config(config)
    return {"status": "disabled", "channel": normalized}


@app.get("/api/v1/ops/config")
async def ops_config_get(request: Request):
    _require_ops_auth(request)
    config = load_ops_config()
    return {"config": config, "base_hash": ops_config_hash(config)}


@app.get("/api/v1/ops/config/schema")
async def ops_config_schema_get(request: Request):
    _require_ops_auth(request)
    return {"schema": ops_config_schema()}


def _remote_sync_enabled(config: dict[str, Any]) -> bool:
    section = config.get("remote_debug", {})
    if not isinstance(section, dict):
        return False
    return bool(section.get("local_workspace_sync_enabled", False))


@app.get("/api/v1/ops/deployment/profile")
async def ops_deployment_profile_get(request: Request):
    _require_ops_auth(request)
    return {"deployment_profile": _deployment_profile_defaults()}


@app.post("/api/v1/ops/config")
async def ops_config_set(request: Request, payload: OpsConfigRequest):
    _require_ops_auth(request)
    current = load_ops_config()
    if payload.base_hash and payload.base_hash != ops_config_hash(current):
        raise HTTPException(status_code=409, detail="Config changed; reload and retry")
    write_ops_config(payload.config or {})
    updated = load_ops_config()
    return {"config": updated, "base_hash": ops_config_hash(updated)}


@app.patch("/api/v1/ops/config")
async def ops_config_patch(request: Request, payload: OpsConfigPatchRequest):
    _require_ops_auth(request)
    current = load_ops_config()
    if payload.base_hash and payload.base_hash != ops_config_hash(current):
        raise HTTPException(status_code=409, detail="Config changed; reload and retry")
    updated = apply_merge_patch(current, payload.patch or {})
    write_ops_config(updated)
    return {"config": updated, "base_hash": ops_config_hash(updated)}


@app.get("/api/v1/ops/remote-sync")
async def ops_remote_sync_get(request: Request):
    _require_ops_auth(request)
    config = load_ops_config()
    return {
        "enabled": _remote_sync_enabled(config),
        "default_enabled": False,
        "config_key": "remote_debug.local_workspace_sync_enabled",
        "base_hash": ops_config_hash(config),
    }


@app.post("/api/v1/ops/remote-sync")
async def ops_remote_sync_set(request: Request, payload: OpsRemoteSyncUpdateRequest):
    _require_ops_auth(request)
    config = load_ops_config()
    section = config.get("remote_debug", {})
    if not isinstance(section, dict):
        section = {}
    section["local_workspace_sync_enabled"] = bool(payload.enabled)
    config["remote_debug"] = section
    write_ops_config(config)
    updated = load_ops_config()
    return {
        "enabled": _remote_sync_enabled(updated),
        "default_enabled": False,
        "config_key": "remote_debug.local_workspace_sync_enabled",
        "base_hash": ops_config_hash(updated),
    }


@app.post("/api/v1/ops/workspaces/purge")
async def ops_workspaces_purge(request: Request, confirm: bool = False):
    """
    Purge all session workspaces and artifacts from the VPS filesystem.
    This frees up disk space and prevents old data from syncing to local environments.
    """
    _require_ops_auth(request)
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Confirmation required. Pass ?confirm=true to purge all workspaces and artifacts.",
        )

    deleted_workspaces_count = 0
    deleted_artifacts_count = 0
    errors = []

    # 1. Purge Workspaces
    if WORKSPACES_DIR.exists():
        for item in WORKSPACES_DIR.iterdir():
            if item.is_dir():
                # Safety check: simplistic heuristic to avoid deleting non-session dirs if any exist
                # But "Purge All" implies all. We'll trust the WORKSPACES_DIR is dedicated.
                try:
                    shutil.rmtree(item)
                    deleted_workspaces_count += 1
                except Exception as e:
                    errors.append(f"Failed to delete workspace {item.name}: {e}")

    # 2. Purge Artifacts
    artifacts_dir = resolve_artifacts_dir()
    if artifacts_dir.exists():
        for item in artifacts_dir.iterdir():
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                deleted_artifacts_count += 1
            except Exception as e:
                errors.append(f"Failed to delete artifact item {item.name}: {e}")

    logger.warning(
        f"🧹 PURGE COMPLETE: Deleted {deleted_workspaces_count} workspaces and {deleted_artifacts_count} artifact items."
    )

    return {
        "deleted_workspaces": deleted_workspaces_count,
        "deleted_artifacts_items": deleted_artifacts_count,
        "errors": errors,
        "status": "completed" if not errors else "completed_with_errors",
    }


@app.get("/api/v1/ops/approvals")
async def ops_approvals_list(request: Request, status: Optional[str] = None):
    _require_ops_auth(request)
    return {"approvals": list_approvals(status=status)}


@app.post("/api/v1/ops/approvals")
async def ops_approvals_create(request: Request, payload: OpsApprovalCreateRequest):
    _require_ops_auth(request)
    record = upsert_approval(payload.model_dump(exclude_none=True))
    return {"approval": record}


@app.patch("/api/v1/ops/approvals/{approval_id}")
async def ops_approvals_update(
    request: Request, approval_id: str, payload: OpsApprovalUpdateRequest
):
    _require_ops_auth(request)
    record = update_approval(approval_id, payload.model_dump(exclude_none=True))
    if record is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    for pending in _pending_gated_requests.values():
        if pending.get("approval_id") == approval_id:
            pending["status"] = record.get("status")
            pending["updated_at"] = datetime.now(timezone.utc).isoformat()
    return {"approval": record}


@app.get("/api/v1/ops/work-threads")
async def ops_work_threads_list(
    request: Request,
    status: Optional[str] = None,
    session_id: Optional[str] = None,
):
    _require_ops_auth(request)
    return {"threads": list_work_threads(status=status, session_id=session_id)}


@app.post("/api/v1/ops/work-threads")
async def ops_work_threads_upsert(request: Request, payload: OpsWorkThreadUpsertRequest):
    _require_ops_auth(request)
    try:
        record = upsert_work_thread(payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"thread": record}


@app.post("/api/v1/ops/work-threads/decide")
async def ops_work_threads_decide(request: Request, payload: OpsWorkThreadDecisionRequest):
    _require_ops_auth(request)
    decided_by = (
        str(request.headers.get("x-ua-dashboard-owner") or "").strip()
        or "dashboard_operator"
    )
    try:
        record = append_work_thread_decision(
            session_id=payload.session_id,
            decision=payload.decision,
            note=payload.note,
            decided_by=decided_by,
            metadata=payload.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"thread": record}


@app.patch("/api/v1/ops/work-threads/{thread_id}")
async def ops_work_threads_update(
    request: Request,
    thread_id: str,
    payload: OpsWorkThreadUpdateRequest,
):
    _require_ops_auth(request)
    record = update_work_thread(thread_id, payload.model_dump(exclude_none=True))
    if record is None:
        raise HTTPException(status_code=404, detail="Work thread not found")
    return {"thread": record}


@app.get("/api/v1/ops/models")
async def ops_models_list(request: Request):
    _require_ops_auth(request)
    models = []
    sonnet = os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL")
    haiku = os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL")
    if sonnet:
        models.append({"id": sonnet, "label": "default-sonnet"})
    if haiku:
        models.append({"id": haiku, "label": "default-haiku"})
    return {"models": models}


# =============================================================================
# WebSocket Streaming Endpoint
# =============================================================================


def agent_event_to_wire(event: AgentEvent) -> dict:
    return {
        "type": event.type.value if hasattr(event.type, "value") else str(event.type),
        "data": event.data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "time_offset": event.data.get("time_offset") if isinstance(event.data, dict) else None,
    }


@app.websocket("/api/v1/sessions/{session_id}/stream")
async def websocket_stream(websocket: WebSocket, session_id: str):
    if _FACTORY_POLICY.gateway_mode == "health_only":
        await websocket.close(code=4403, reason="WebSocket API disabled for LOCAL_WORKER role")
        return
    if not await _require_session_ws_auth(websocket):
        return
    try:
        session_id = validate_session_id(session_id)
    except ValueError:
        await websocket.close(code=4000, reason="Invalid session id format")
        return
    connection_id = f"gw_{session_id}_{time.time()}"
    # Register connection with session_id
    await manager.connect(connection_id, websocket, session_id)
    
    gateway = get_gateway()
    session = get_session(session_id)

    if not session:
        try:
            session = await gateway.resume_session(session_id)
            # Success - count metrics
            _increment_metric("resume_attempts")
            _increment_metric("resume_successes")
            _increment_metric("ws_attach_attempts")
            
            session.metadata["user_id"] = session.user_id
            store_session(session)
            if _heartbeat_service:
                _heartbeat_service.register_session(session)
        except ValueError:
            # Session Not Found - do NOT count as continuity failure
            await websocket.close(code=4004, reason="Session not found")
            manager.disconnect(connection_id, session_id)
            return
        except Exception:
            # System error - count as failure
            _increment_metric("resume_attempts")
            _increment_metric("resume_failures")
            _increment_metric("ws_attach_attempts")
            _increment_metric("ws_attach_failures")
            logger.exception(f"Failed to resume session {session_id}")
            await websocket.close(code=1011, reason="Internal Error")
            manager.disconnect(connection_id, session_id)
            return
    else:
        # Session exists - count attempt
        _increment_metric("ws_attach_attempts")

    session.metadata.setdefault("user_id", session.user_id)

    # 1. Enforce Allowlist for WebSocket
    if not is_user_allowed(session.user_id):
        logger.warning(f"⛔ Access Denied (WS): User '{session.user_id}' not in allowlist.")
        _increment_metric("ws_attach_failures")
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        session_id=session_id,
    )
    _increment_metric("ws_attach_successes")

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
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            },
                            session_id=session_id,
                        )
                        continue

                    raw_data = msg.get("data", {}) or {}
                    client_turn_id = _normalize_client_turn_id(raw_data.get("client_turn_id"))
                    metadata = raw_data.get("metadata", {}) or {}
                    if not isinstance(metadata, dict):
                        metadata = {"raw": metadata}
                    system_events = _drain_system_events(session_id)
                    if system_events:
                        metadata = {**metadata, "system_events": system_events}
                    policy = _session_policy(session)
                    memory_policy = normalize_memory_policy(policy.get("memory"))

                    resume_requested = user_input.strip().lower() in {"resume", "continue", "/resume"}
                    pending_gate = _pending_gated_requests.get(session_id)
                    clear_pending_gate_on_success = False
                    if resume_requested and pending_gate:
                        if not _pending_gate_is_approved(session_id):
                            await manager.send_json(
                                connection_id,
                                {
                                    "type": "error",
                                    "data": {
                                        "message": "Pending request is not approved yet. Approve it first, then resume."
                                    },
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                                session_id=session_id,
                            )
                            continue
                        saved_request = pending_gate.get("request", {})
                        if not isinstance(saved_request, dict):
                            saved_request = {}
                        user_input = str(saved_request.get("user_input") or user_input)
                        force_complex = bool(saved_request.get("force_complex", raw_data.get("force_complex", False)))
                        saved_metadata = saved_request.get("metadata", {})
                        if not isinstance(saved_metadata, dict):
                            saved_metadata = {}
                        metadata = {
                            **saved_metadata,
                            "resumed": True,
                            "identity_mode": pending_gate.get("identity_mode") or saved_metadata.get("identity_mode", "persona"),
                            "autonomy_mode": policy.get("autonomy_mode", "yolo"),
                            "memory_policy": memory_policy,
                            "memory_enabled": memory_policy.get("enabled"),
                            "memory_session_enabled": memory_policy.get("sessionMemory"),
                            "memory_sources": memory_policy.get("sources", []),
                            "memory_scope": memory_policy.get("scope"),
                        }
                        clear_pending_gate_on_success = True
                    else:
                        metadata = {
                            **metadata,
                            "identity_mode": policy.get("identity_mode", "persona"),
                            "autonomy_mode": policy.get("autonomy_mode", "yolo"),
                            "memory_policy": memory_policy,
                            "memory_enabled": memory_policy.get("enabled"),
                            "memory_session_enabled": memory_policy.get("sessionMemory"),
                            "memory_sources": memory_policy.get("sources", []),
                            "memory_scope": memory_policy.get("scope"),
                        }
                        evaluation = evaluate_request_against_policy(
                            policy,
                            user_input=user_input,
                            metadata=metadata,
                        )
                        decision = str(evaluation.get("decision", "allow")).lower()
                        categories = evaluation.get("categories") or []
                        reasons = evaluation.get("reasons") or []

                        if decision == "deny":
                            reason_text = "; ".join(str(reason) for reason in reasons) or "Policy denied request."
                            _add_notification(
                                kind="policy_denied",
                                title="Request Blocked",
                                message=reason_text,
                                session_id=session_id,
                                severity="warning",
                                requires_action=True,
                                metadata={"categories": categories},
                            )
                            await manager.send_json(
                                connection_id,
                                {
                                    "type": "error",
                                    "data": {"message": reason_text, "categories": categories},
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                                session_id=session_id,
                            )
                            continue

                        if decision == "require_approval":
                            approval = upsert_approval(
                                {
                                    "phase_id": f"session_gate_{session_id}",
                                    "status": "pending",
                                    "summary": f"Approval required for request categories: {', '.join(categories) or 'unknown'}",
                                    "requested_by": session.user_id,
                                    "metadata": {
                                        "session_id": session_id,
                                        "categories": categories,
                                        "reasons": reasons,
                                        "user_input": user_input,
                                    },
                                }
                            )
                            _pending_gated_requests[session_id] = {
                                "session_id": session_id,
                                "approval_id": approval.get("approval_id"),
                                "status": "pending",
                                "categories": categories,
                                "reasons": reasons,
                                "created_at": datetime.now(timezone.utc).isoformat(),
                                "request": {
                                    "user_input": user_input,
                                    "force_complex": raw_data.get("force_complex", False),
                                    "metadata": metadata,
                                },
                                "identity_mode": policy.get("identity_mode", "persona"),
                            }
                            _add_notification(
                                kind="approval_required",
                                title="Approval Required",
                                message=f"Session {session_id} is waiting for approval.",
                                session_id=session_id,
                                severity="warning",
                                requires_action=True,
                                metadata={
                                    "approval_id": approval.get("approval_id"),
                                    "categories": categories,
                                },
                            )
                            await manager.send_json(
                                connection_id,
                                {
                                    "type": "status",
                                    "data": {
                                        "status": "approval_required",
                                        "approval_id": approval.get("approval_id"),
                                        "categories": categories,
                                        "reasons": reasons,
                                    },
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                                session_id=session_id,
                            )
                            continue

                        force_complex = raw_data.get("force_complex", False)

                    async with _session_turn_lock(session_id):
                        admission = _admit_turn(
                            session_id=session_id,
                            connection_id=connection_id,
                            user_input=user_input,
                            force_complex=bool(force_complex),
                            metadata=metadata,
                            client_turn_id=client_turn_id,
                        )

                    decision = str(admission.get("decision", "accepted"))
                    admitted_turn_id = str(admission.get("turn_id") or "")
                    if decision == "busy":
                        _increment_metric("turn_busy_rejected")
                        await manager.send_json(
                            connection_id,
                            {
                                "type": "status",
                                "data": {
                                    "status": "turn_rejected_busy",
                                    "active_turn_id": admitted_turn_id,
                                    "message": "Another turn is currently running for this session.",
                                },
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            },
                            session_id=session_id,
                        )
                        continue
                    if decision == "duplicate_in_progress":
                        _increment_metric("turn_duplicate_in_progress")
                        await manager.send_json(
                            connection_id,
                            {
                                "type": "status",
                                "data": {
                                    "status": "turn_in_progress",
                                    "turn_id": admitted_turn_id,
                                    "message": "This turn is already in progress.",
                                },
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            },
                            session_id=session_id,
                        )
                        continue
                    if decision == "duplicate_completed":
                        _increment_metric("turn_duplicate_completed")
                        await manager.send_json(
                            connection_id,
                            {
                                "type": "status",
                                "data": {
                                    "status": "duplicate_turn_ignored",
                                    "turn_id": admitted_turn_id,
                                    "message": "Duplicate turn ignored; request already processed.",
                                },
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            },
                            session_id=session_id,
                        )
                        await manager.send_json(
                            connection_id,
                            {
                                "type": "query_complete",
                                "data": {"turn_id": admitted_turn_id},
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            },
                            session_id=session_id,
                        )
                        continue

                    request_metadata = {
                        **metadata,
                        "turn_id": admitted_turn_id,
                    }
                    if client_turn_id:
                        request_metadata["client_turn_id"] = client_turn_id
                    request_source = _normalize_run_source(request_metadata.get("source"))
                    request_metadata["source"] = request_source

                    request = GatewayRequest(
                        user_input=user_input,
                        force_complex=force_complex,
                        metadata=request_metadata,
                    )
                    logger.info(
                        "WS execute start (session=%s, user_id=%s, len=%s)",
                        session_id,
                        session.user_id,
                        len(user_input),
                    )
                    mission_tracker = MissionGuardrailTracker(build_mission_contract(user_input))
                    _increment_session_active_runs(session_id, run_source=request_source)

                    async def run_execution(turn_id: str):
                        saw_streaming_text = False
                        tool_call_count = 0
                        execution_duration_seconds = 0.0
                        execution_start_ts = time.time()
                        if _heartbeat_service:
                            _heartbeat_service.busy_sessions.add(session.session_id)
                        try:
                            # Execute the request and stream to all attached clients for this session.
                            async for event in gateway.execute(session, request):
                                if event.type == EventType.TOOL_CALL:
                                    tool_call_count += 1
                                    if isinstance(event.data, dict):
                                        mission_tracker.record_tool_call(
                                            str(event.data.get("name") or ""),
                                            tool_input=event.data.get("input"),
                                        )
                                elif event.type == EventType.ITERATION_END and isinstance(event.data, dict):
                                    execution_duration_seconds = float(
                                        event.data.get("duration_seconds") or execution_duration_seconds
                                    )
                                    # Prefer engine-provided count if available
                                    if isinstance(event.data.get("tool_calls"), int):
                                        tool_call_count = int(event.data["tool_calls"])
                                if (
                                    event.type == EventType.TEXT
                                    and isinstance(event.data, dict)
                                    and event.data.get("final") is True
                                    and saw_streaming_text
                                ):
                                    continue
                                if (
                                    event.type == EventType.TEXT
                                    and isinstance(event.data, dict)
                                    and event.data.get("time_offset") is not None
                                ):
                                    saw_streaming_text = True
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
                                await manager.broadcast(session_id, agent_event_to_wire(event))

                            if execution_duration_seconds <= 0:
                                execution_duration_seconds = round(time.time() - execution_start_ts, 3)
                            goal_satisfaction = mission_tracker.evaluate()
                            completion_summary = {
                                "tool_calls": tool_call_count,
                                "duration_seconds": execution_duration_seconds,
                                "goal_satisfaction": goal_satisfaction,
                            }

                            # Generate checkpoint for next session/follow-up
                            try:
                                from universal_agent.session_checkpoint import SessionCheckpointGenerator
                                workspace_path = Path(session.workspace_dir)
                                generator = SessionCheckpointGenerator(workspace_path)
                                checkpoint_result = SimpleNamespace(
                                    tool_calls=tool_call_count,
                                    execution_time_seconds=execution_duration_seconds,
                                    goal_satisfaction=goal_satisfaction,
                                )
                                checkpoint = generator.generate_from_result(
                                    session_id=session.session_id,
                                    original_request=user_input,
                                    result=checkpoint_result,
                                )
                                generator.save(checkpoint)
                                logger.info(f"✅ Saved session checkpoint: {workspace_path / 'session_checkpoint.json'}")
                            except Exception as ckpt_err:
                                logger.warning(f"⚠️ Failed to save checkpoint: {ckpt_err}")

                            if not bool(goal_satisfaction.get("passed")):
                                missing_items = goal_satisfaction.get("missing")
                                goal_message = "Mission requirements were not satisfied."
                                if isinstance(missing_items, list) and missing_items:
                                    first_missing = missing_items[0] if isinstance(missing_items[0], dict) else {}
                                    missing_message = str(first_missing.get("message") or "").strip()
                                    missing_requirement = str(first_missing.get("requirement") or "").strip()
                                    if missing_message:
                                        goal_message = f"{goal_message} {missing_message}"
                                    elif missing_requirement:
                                        goal_message = (
                                            f"{goal_message} Missing requirement: {missing_requirement}."
                                        )
                                _add_notification(
                                    kind="assistance_needed",
                                    title="Mission Guardrail Blocked Completion",
                                    message=goal_message,
                                    session_id=session.session_id,
                                    severity="error",
                                    requires_action=True,
                                    metadata={"goal_satisfaction": goal_satisfaction},
                                )
                                await manager.broadcast(
                                    session_id,
                                    {
                                        "type": "status",
                                        "data": {
                                            "status": "goal_satisfaction_failed",
                                            "turn_id": turn_id,
                                            "goal_satisfaction": goal_satisfaction,
                                        },
                                        "timestamp": datetime.now(timezone.utc).isoformat(),
                                    },
                                )
                                await manager.broadcast(
                                    session_id,
                                    {
                                        "type": "error",
                                        "data": {
                                            "message": goal_message,
                                            "goal_satisfaction": goal_satisfaction,
                                        },
                                        "timestamp": datetime.now(timezone.utc).isoformat(),
                                    },
                                )
                                await manager.broadcast(
                                    session_id,
                                    {
                                        "type": "query_complete",
                                        "data": {
                                            "turn_id": turn_id,
                                            "goal_satisfaction": goal_satisfaction,
                                            "completed": False,
                                        },
                                        "timestamp": datetime.now(timezone.utc).isoformat(),
                                    },
                                )
                                async with _session_turn_lock(session_id):
                                    _finalize_turn(
                                        session_id,
                                        turn_id,
                                        TURN_STATUS_FAILED,
                                        error_message=goal_message,
                                        completion=completion_summary,
                                    )
                                return

                            _add_notification(
                                kind="mission_complete",
                                title="Mission Completed",
                                message="Session completed execution successfully.",
                                session_id=session.session_id,
                                severity="info",
                                metadata={
                                    "tool_calls": tool_call_count,
                                    "duration_seconds": execution_duration_seconds,
                                    "goal_satisfaction": goal_satisfaction,
                                },
                            )

                            await manager.broadcast(
                                session_id,
                                {
                                    "type": "query_complete",
                                    "data": {
                                        "turn_id": turn_id,
                                        "goal_satisfaction": goal_satisfaction,
                                        "completed": True,
                                    },
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                            )

                            await manager.broadcast(
                                session_id,
                                {"type": "pong", "data": {}, "timestamp": datetime.now(timezone.utc).isoformat()},
                            )
                            logger.info("WS execute complete (session=%s)", session_id)
                            if clear_pending_gate_on_success:
                                _pending_gated_requests.pop(session_id, None)
                            async with _session_turn_lock(session_id):
                                _finalize_turn(
                                    session_id,
                                    turn_id,
                                    TURN_STATUS_COMPLETED,
                                    completion=completion_summary,
                                )
                        except asyncio.CancelledError:
                            logger.warning("Execution cancelled for session %s turn %s", session_id, turn_id)
                            await manager.broadcast(
                                session_id,
                                {
                                    "type": "status",
                                    "data": {
                                        "status": "turn_cancelled",
                                        "turn_id": turn_id,
                                        "message": "Execution cancelled.",
                                    },
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                            )
                            await manager.broadcast(
                                session_id,
                                {
                                    "type": "query_complete",
                                    "data": {"turn_id": turn_id, "cancelled": True},
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                            )
                            async with _session_turn_lock(session_id):
                                _finalize_turn(
                                    session_id,
                                    turn_id,
                                    TURN_STATUS_CANCELLED,
                                    error_message="cancelled",
                                    completion={
                                        "tool_calls": tool_call_count,
                                        "duration_seconds": round(time.time() - execution_start_ts, 3),
                                    },
                                )
                            raise
                        except Exception as e:
                            logger.error("Execution error for session %s: %s", session_id, e, exc_info=True)
                            _add_notification(
                                kind="assistance_needed",
                                title="Session Failed",
                                message=str(e),
                                session_id=session.session_id,
                                severity="error",
                                requires_action=True,
                            )
                            await manager.broadcast(
                                session_id,
                                {
                                    "type": "error",
                                    "data": {"message": str(e)},
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                },
                            )
                            async with _session_turn_lock(session_id):
                                _finalize_turn(
                                    session_id,
                                    turn_id,
                                    TURN_STATUS_FAILED,
                                    error_message=str(e),
                                    completion={
                                        "tool_calls": tool_call_count,
                                        "duration_seconds": round(time.time() - execution_start_ts, 3),
                                    },
                                )
                        finally:
                            _decrement_session_active_runs(session_id, run_source=request_source)
                            if _heartbeat_service:
                                _heartbeat_service.busy_sessions.discard(session.session_id)

                    execution_task = asyncio.create_task(run_execution(admitted_turn_id))
                    _register_execution_task(session_id, execution_task)
                
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
                         "timestamp": datetime.now(timezone.utc).isoformat()
                     }
                     # Broadcast to ALL connections for this session
                     await manager.broadcast(session_id, payload)

                elif msg_type == "cancel":
                    # User requested to stop the current agent run
                    reason = msg.get("data", {}).get("reason", "User requested stop")
                    run_id = session.metadata.get("run_id")
                    logger.info("Cancel request received (session=%s, run=%s, reason=%s)", session_id, run_id, reason)
                    await _cancel_session_execution(session_id, reason, run_id=run_id)

                else:
                    await manager.send_json(
                        connection_id,
                        {
                            "type": "error",
                            "data": {"message": f"Unknown message type: {msg_type}"},
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        },
                        session_id=session_id,
                    )

            except json.JSONDecodeError:
                await manager.send_json(
                    connection_id,
                    {
                        "type": "error",
                        "data": {"message": "Invalid JSON"},
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    session_id=session_id,
                )
            except Exception as e:
                logger.error(f"Error handling message: {e}")
                await manager.send_json(
                    connection_id,
                    {
                        "type": "error",
                        "data": {"message": str(e)},
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    session_id=session_id,
                )

    except WebSocketDisconnect as exc:
        _record_ws_close(
            getattr(exc, "code", None),
            getattr(exc, "reason", None),
            endpoint="gateway_session_stream",
        )
        manager.disconnect(connection_id, session_id)
        logger.info(f"Gateway WebSocket disconnected: {connection_id}")
    except Exception as e:
        _record_ws_close(None, str(e), endpoint="gateway_session_stream")
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

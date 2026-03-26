
import asyncio
import base64
import functools
import hashlib
import hmac
import importlib.util
import json
import logging
import os
import random
import re
import shlex
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import Request, Response
from pydantic import BaseModel, Field

from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import get_run, get_run_attempt
from universal_agent.gateway import InProcessGateway, GatewayRequest
from universal_agent.heartbeat_mediation import sanitize_heartbeat_recommendation_text
from universal_agent.ops_config import load_ops_config, resolve_ops_config_path
from universal_agent.run_catalog import RunCatalogService
from universal_agent.workflow_admission import (
    WorkflowAdmissionDecision,
    WorkflowAdmissionService,
    WorkflowTrigger,
)
from universal_agent.youtube_ingest import normalize_video_target

logger = logging.getLogger(__name__)

_RUNTIME_DB_LOCK_RETRY_ATTEMPTS = 4
_RUNTIME_DB_LOCK_RETRY_BASE_SECONDS = 0.25


def _is_sqlite_lock_error(exc: sqlite3.OperationalError) -> bool:
    detail = str(exc or "").strip().lower()
    return "database is locked" in detail or "database table is locked" in detail

DEFAULT_HOOKS_PATH = "/hooks"
DEFAULT_HOOKS_MAX_BODY_BYTES = 256 * 1024
DEFAULT_BOOTSTRAP_HOOKS_MAX_BODY_BYTES = 1024 * 1024
DEFAULT_BOOTSTRAP_TRANSFORMS_DIR = "../webhook_transforms"
HOOK_SESSION_ID_PREFIX = "session_hook_"
MAX_SESSION_ID_LEN = 128
SESSION_ID_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
DEFAULT_SYNC_READY_MARKER_FILENAME = "sync_ready.json"
SYNC_READY_MARKER_VERSION = 1
YOUTUBE_AGENT_CANONICAL = "youtube-expert"
YOUTUBE_AGENT_LEGACY_ALIAS = "youtube-explainer-expert"
YOUTUBE_AGENT_ROUTE_ALIASES = {YOUTUBE_AGENT_CANONICAL, YOUTUBE_AGENT_LEGACY_ALIAS}
YOUTUBE_TUTORIAL_ARTIFACT_DIR_CANONICAL = "youtube-tutorial-creation"
YOUTUBE_TUTORIAL_BOOTSTRAP_SCRIPT_NAMES = {"create_new_repo.sh", "deletethisrepo.sh"}
YOUTUBE_TUTORIAL_CODE_HINT_KEYWORDS = {
    "code",
    "coding",
    "programming",
    "python",
    "javascript",
    "typescript",
    "react",
    "nextjs",
    "next.js",
    "mcp",
    "api",
    "sdk",
    "cli",
    "sql",
    "database",
    "docker",
    "kubernetes",
    "repo",
    "github",
    "automation",
    "agent",
}
YOUTUBE_TUTORIAL_NON_CODE_HINT_KEYWORDS = {
    "recipe",
    "cooking",
    "cook",
    "food",
    "kitchen",
    "grill",
    "charcoal",
    "souvlaki",
    "baking",
    "travel",
    "vlog",
    "music",
    "song",
    "workout",
    "fitness",
}
DEFAULT_TUTORIAL_BOOTSTRAP_REPO_ROOT_LOCAL = "/home/kjdragan/YoutubeCodeExamples"
DEFAULT_TUTORIAL_BOOTSTRAP_REPO_ROOT_VPS = str((resolve_artifacts_dir() / "tutorial_repos").resolve())
YOUTUBE_PROXY_ALERT_FAILURE_CLASSES = {
    "proxy_quota_or_billing",
    "proxy_pool_unallocated",
    "proxy_auth_failed",
    "proxy_not_configured",
    "proxy_connect_failed",
}
YOUTUBE_INGEST_NON_RETRYABLE_FAILURE_CLASSES = {
    "invalid_video_target",
    "video_unavailable",
    "api_unavailable",
    "transcript_unavailable",
    "empty_or_low_quality_transcript",
    "proxy_not_configured",
    "proxy_auth_failed",
    "proxy_quota_or_billing",
    "proxy_pool_unallocated",
}
YOUTUBE_INGEST_DEGRADABLE_FAILURE_CLASSES = {
    "api_unavailable",
    "request_blocked",
    "transcript_unavailable",
    "empty_or_low_quality_transcript",
}
YOUTUBE_DISPATCH_INTERRUPTION_ERROR_TOKENS = (
    # ── Process-level signals (deployment restarts) ──
    "exit code -15",
    "exit code: -15",
    "terminated process",
    "cannot write to terminated process",
    "sigterm",
    "sigkill",
    "signal 15",
    "killed",
    # ── Transient network / API errors (recoverable) ──
    "connection reset",
    "connectionreseterror",
    "connection refused",
    "connectionrefusederror",
    "broken pipe",
    "brokenpipeerror",
    "timeout",
    "timed out",
    "timedout",
    "deadline exceeded",
    "503",
    "service unavailable",
    "502",
    "bad gateway",
    "429",
    "rate limit",
    "rate_limit",
    "quota exceeded",
    "resource exhausted",
    "capacity",
    "no capacity available",
    "temporarily unavailable",
    "server overloaded",
    "overloaded",
    # ── LLM / model-provider transient errors ──
    "model overloaded",
    "model_overloaded",
    "request failed",
    "request_failed",
    "invalid response",
    "invalid_response",
    "api error",
    "api_error",
    "internal server error",
    "internal_server_error",
    "500",
    "server error",
    "iteration_status:error",
    "iteration_status:failed",
)


class HookReportedTimeout(RuntimeError):
    """Raised when agent execution reports a timeout via stream output."""


class HookIdleTimeout(RuntimeError):
    """Raised when agent execution makes no observable progress for too long."""


class HookMatchConfig(BaseModel):
    path: Optional[str] = None
    source: Optional[str] = None
    headers: Optional[Dict[str, str]] = None

class HookTransformConfig(BaseModel):
    module: str
    export: Optional[str] = None

class HookAuthConfig(BaseModel):
    strategy: str = "token"  # token | none
    secret_env: Optional[str] = None
    timestamp_tolerance_seconds: int = 300
    replay_window_seconds: int = 600

class HookMappingConfig(BaseModel):
    id: Optional[str] = None
    match: Optional[HookMatchConfig] = None
    action: str = "agent"  # "wake" or "agent"
    wake_mode: str = "now"
    transform: Optional[HookTransformConfig] = None
    auth: Optional[HookAuthConfig] = None
    message_template: Optional[str] = None
    text_template: Optional[str] = None
    name: Optional[str] = None
    session_key: Optional[str] = None
    deliver: bool = True
    allow_unsafe_external_content: bool = False
    to: Optional[str] = None
    model: Optional[str] = None
    thinking: Optional[str] = None
    timeout_seconds: Optional[int] = None

class HooksConfig(BaseModel):
    enabled: bool = False
    token: Optional[str] = None
    base_path: str = DEFAULT_HOOKS_PATH
    max_body_bytes: int = DEFAULT_HOOKS_MAX_BODY_BYTES
    transforms_dir: Optional[str] = None
    mappings: List[HookMappingConfig] = Field(default_factory=list)

class HookAction(BaseModel):
    kind: str  # "wake" or "agent"
    text: Optional[str] = None
    message: Optional[str] = None
    mode: str = "now" # wake mode
    name: Optional[str] = None
    session_key: Optional[str] = None
    deliver: bool = True
    allow_unsafe_external_content: bool = False
    to: Optional[str] = None
    model: Optional[str] = None
    thinking: Optional[str] = None
    timeout_seconds: Optional[int] = None


def _is_youtube_agent_route(route: str | None) -> bool:
    return (route or "").strip().lower() in YOUTUBE_AGENT_ROUTE_ALIASES


def _manual_youtube_safe_segment(value: str | None, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    safe = SESSION_ID_SANITIZE_RE.sub("_", text).strip("._-")
    return safe or fallback


def _manual_youtube_normalize_mode(raw_mode: Any) -> str:
    mode = str(raw_mode or "").strip().lower()
    if not mode:
        return "explainer_only"
    if mode in {"auto", "detect", "auto_detect"}:
        return "auto"
    if mode in {"explainer_only", "explain", "explanation", "explainer"}:
        return "explainer_only"
    if mode in {
        "explainer_plus_code",
        "plus_code",
        "code",
        "with_code",
        "explainer_with_code",
        "explain_and_code",
    }:
        return "explainer_plus_code"
    return "explainer_only"


def _manual_youtube_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _manual_youtube_probably_code(*parts: Any) -> bool:
    tokens = " ".join(str(part or "") for part in parts).strip().lower()
    if not tokens:
        return False
    has_code = any(keyword in tokens for keyword in YOUTUBE_TUTORIAL_CODE_HINT_KEYWORDS)
    has_non_code = any(keyword in tokens for keyword in YOUTUBE_TUTORIAL_NON_CODE_HINT_KEYWORDS)
    if has_non_code and not has_code:
        return False
    return has_code


def build_manual_youtube_action(
    payload: dict[str, Any],
    *,
    name: str = "ManualYouTubeWebhook",
) -> Optional[dict[str, Any]]:
    if not isinstance(payload, dict):
        return None

    video_url, video_id = normalize_video_target(
        payload.get("video_url"),
        payload.get("video_id"),
    )
    if not video_url:
        return None

    channel_id = str(payload.get("channel_id") or "").strip()
    title = str(payload.get("title") or "").strip()
    mode = _manual_youtube_normalize_mode(payload.get("mode"))
    if mode == "auto":
        mode = (
            "explainer_plus_code"
            if _manual_youtube_probably_code(title, channel_id, video_url)
            else "explainer_only"
        )
    learning_mode = (
        "concept_plus_implementation"
        if mode == "explainer_plus_code"
        else "concept_only"
    )
    allow_degraded = _manual_youtube_bool(
        payload.get("allow_degraded_transcript_only"),
        default=True,
    )
    try:
        artifacts_root = str(resolve_artifacts_dir())
    except Exception:
        artifacts_root = "artifacts"

    channel_seg = _manual_youtube_safe_segment(channel_id, "manual")
    if video_id:
        video_seg = _manual_youtube_safe_segment(video_id, "manual")
    else:
        video_seg = hashlib.sha256(video_url.encode("utf-8", errors="replace")).hexdigest()[:12]
    session_key = f"yt_{channel_seg}__{video_seg}"

    lines = [
        "Manual YouTube URL ingestion event received.",
        "Route this run to the YouTube specialist.",
        f"target_subagent: {YOUTUBE_AGENT_CANONICAL}",
        "Ingestion first: use youtube-transcript-metadata skill for transcript+metadata.",
        "Then use youtube-tutorial-creation for durable tutorial artifacts.",
        "Produce durable learning artifacts in UA_ARTIFACTS_DIR.",
        f"resolved_artifacts_root: {artifacts_root}",
        "Path rule: do not use a literal UA_ARTIFACTS_DIR folder segment in file paths.",
        "Invalid paths: /opt/universal_agent/UA_ARTIFACTS_DIR/... and UA_ARTIFACTS_DIR/...",
        f"Use this absolute durable base path: {artifacts_root}/youtube-tutorial-creation/...",
        "Required baseline artifacts: README.md, CONCEPT.md, manifest.json.",
        "If learning_mode is concept_plus_implementation, also create IMPLEMENTATION.md and implementation/ with runnable code.",
        "If learning_mode is concept_only, keep implementation procedural (no repo bootstrap scripts).",
        "Create required artifacts first and keep them even if extraction fails.",
        "On extraction failure, set manifest status to degraded_transcript_only or failed (never leave empty run dirs).",
        f"video_url: {video_url}",
        f"video_id: {video_id or ''}",
        f"channel_id: {channel_id}",
        f"title: {title}",
        f"mode: {mode}",
        f"learning_mode: {learning_mode}",
        f"allow_degraded_transcript_only: {str(allow_degraded).lower()}",
        "Set implementation_required=true only when transcript+metadata confirm software/coding content.",
        "If learning_mode is concept_plus_implementation, include runnable code in implementation/ and explain how to run it.",
        "Transcript path: youtube-transcript-api is source of truth. yt-dlp is metadata-only.",
        "Video analysis path: use Gemini multimodal video understanding with the YouTube URL directly when available.",
        "Use visual analysis when possible. Continue with transcript-only mode when visual processing is unavailable.",
    ]
    return {
        "kind": "agent",
        "name": str(name or "ManualYouTubeWebhook"),
        "session_key": session_key,
        "to": YOUTUBE_AGENT_CANONICAL,
        "message": "\n".join(lines),
        "deliver": True,
    }


class HooksService:
    @staticmethod
    def _runtime_db_connect():
        conn = connect_runtime_db(get_runtime_db_path())
        ensure_schema(conn)
        return conn

    @staticmethod
    def _run_with_runtime_db_retry(operation: Callable[[Any], Any]) -> Any:
        attempts = max(1, int(_RUNTIME_DB_LOCK_RETRY_ATTEMPTS))
        base_delay = max(0.0, float(_RUNTIME_DB_LOCK_RETRY_BASE_SECONDS))
        last_exc: Optional[sqlite3.OperationalError] = None
        for attempt in range(1, attempts + 1):
            conn = HooksService._runtime_db_connect()
            try:
                return operation(conn)
            except sqlite3.OperationalError as exc:
                last_exc = exc
                try:
                    conn.rollback()
                except Exception:
                    pass
                if not _is_sqlite_lock_error(exc) or attempt >= attempts:
                    raise
                time.sleep(base_delay * attempt)
            finally:
                conn.close()
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("runtime DB retry exhausted without result")

    @staticmethod
    def _workflow_admission_service() -> WorkflowAdmissionService:
        return WorkflowAdmissionService()

    def _admit_workflow_once(
        self,
        *,
        workflow_service: WorkflowAdmissionService,
        trigger: WorkflowTrigger,
        entrypoint: str,
        workspace_dir: str,
        retryable_failure: bool,
        max_attempts: int,
    ) -> tuple[WorkflowAdmissionDecision, Optional[str], Optional[str], Optional[int], str]:
        workflow_decision = workflow_service.admit(
            trigger,
            entrypoint=entrypoint,
            workspace_dir=workspace_dir,
            retryable_failure=retryable_failure,
            max_attempts=max_attempts,
        )
        workflow_run_id = workflow_decision.run_id
        workflow_attempt_id = workflow_decision.attempt_id
        workflow_attempt_number: Optional[int] = None
        effective_workspace_dir = workspace_dir
        attempt_context = self._workflow_attempt_context_safe(
            run_id=workflow_run_id,
            attempt_id=workflow_attempt_id,
        )
        workflow_attempt_number = int(attempt_context.get("attempt_number") or 0) or None
        effective_workspace_dir = str(
            attempt_context.get("workspace_dir") or effective_workspace_dir or ""
        ) or effective_workspace_dir
        return (
            workflow_decision,
            workflow_run_id,
            workflow_attempt_id,
            workflow_attempt_number,
            effective_workspace_dir,
        )

    async def _admit_workflow_with_retry(
        self,
        *,
        action: HookAction,
        session_key: str,
        workflow_profile: dict[str, Any],
    ) -> dict[str, Any]:
        workflow_service = self._workflow_admission_service()
        workflow_workspace_dir = str(workflow_profile.get("workspace_dir") or "")
        base_delay = float(self._workflow_admission_retry_base_seconds)
        max_delay = float(self._workflow_admission_retry_max_delay_seconds)
        ceiling = float(self._workflow_admission_retry_ceiling_seconds)
        admit_attempt = 0
        admit_elapsed = 0.0
        action_name = str(action.name or action.to or action.kind or "unknown").strip()
        while admit_elapsed < ceiling:
            admit_attempt += 1
            try:
                admit_fn = functools.partial(
                    self._admit_workflow_once,
                    workflow_service=workflow_service,
                    trigger=workflow_profile["trigger"],
                    entrypoint=str(
                        workflow_profile.get("entrypoint") or "hooks_service.generic_hook"
                    ),
                    workspace_dir=workflow_workspace_dir,
                    retryable_failure=bool(workflow_profile.get("retryable_failure")),
                    max_attempts=max(1, int(workflow_profile.get("max_attempts") or 1)),
                )
                loop = asyncio.get_running_loop()
                # Serialize the DB admission call itself, but do not hold
                # the lock while sleeping between retries.
                async with self._workflow_admission_lock:
                    (
                        workflow_decision,
                        workflow_run_id,
                        workflow_attempt_id,
                        workflow_attempt_number,
                        workflow_workspace_dir,
                    ) = await loop.run_in_executor(None, admit_fn)
                return {
                    "decision": "admitted",
                    "workflow_decision": workflow_decision,
                    "run_id": workflow_run_id,
                    "attempt_id": workflow_attempt_id,
                    "attempt_number": workflow_attempt_number,
                    "workspace_dir": workflow_workspace_dir,
                }
            except sqlite3.OperationalError as exc:
                if not _is_sqlite_lock_error(exc):
                    raise
                delay = min(base_delay * (2 ** (admit_attempt - 1)), max_delay)
                logger.warning(
                    "Hook admission DB contention (attempt %d, %.0fs elapsed) action=%s session_key=%s: %s — retrying in %.0fs",
                    admit_attempt,
                    admit_elapsed,
                    action_name,
                    session_key,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                admit_elapsed += delay
                continue
        logger.error(
            "Hook admission DB locked after %.0fs, action=%s session_key=%s",
            admit_elapsed,
            action_name,
            session_key,
        )
        return {
            "decision": "failed",
            "reason": "runtime_db_locked",
            "retryable": True,
        }

    def __init__(
        self,
        gateway: InProcessGateway,
        *,
        turn_admitter: Optional[Callable[[str, GatewayRequest], Awaitable[dict[str, Any]]]] = None,
        turn_finalizer: Optional[
            Callable[[str, str, str, Optional[str], Optional[dict[str, Any]]], Awaitable[None]]
        ] = None,
        run_counter_start: Optional[Callable[[str, str], None]] = None,
        run_counter_finish: Optional[Callable[..., None]] = None,
        notification_sink: Optional[Callable[[dict[str, Any]], None]] = None,
    ):
        self.gateway = gateway
        self._turn_admitter = turn_admitter
        self._turn_finalizer = turn_finalizer
        self._run_counter_start = run_counter_start
        self._run_counter_finish = run_counter_finish
        self._notification_sink = notification_sink
        self._agent_dispatch_state_lock = asyncio.Lock()
        self._workflow_admission_lock = asyncio.Lock()
        self._workflow_admission_retry_base_seconds = max(
            0.1,
            self._safe_float_env("UA_HOOKS_WORKFLOW_ADMISSION_RETRY_BASE_SECONDS", 5.0),
        )
        self._workflow_admission_retry_max_delay_seconds = max(
            self._workflow_admission_retry_base_seconds,
            self._safe_float_env("UA_HOOKS_WORKFLOW_ADMISSION_RETRY_MAX_DELAY_SECONDS", 30.0),
        )
        self._workflow_admission_retry_ceiling_seconds = max(
            self._workflow_admission_retry_base_seconds,
            self._safe_float_env("UA_HOOKS_WORKFLOW_ADMISSION_RETRY_CEILING_SECONDS", 300.0),
        )
        configured_dispatch_concurrency = max(
            1,
            self._safe_int_env("UA_HOOKS_AGENT_DISPATCH_CONCURRENCY", 1),
        )
        # Keep dispatch concurrency intentionally tight; higher values can
        # overwhelm host memory during bursty webhook traffic.
        self._agent_dispatch_concurrency = min(4, configured_dispatch_concurrency)
        self._agent_dispatch_gate = asyncio.Semaphore(self._agent_dispatch_concurrency)
        self._agent_dispatch_queue_limit = max(
            self._agent_dispatch_concurrency,
            self._safe_int_env("UA_HOOKS_AGENT_DISPATCH_QUEUE_LIMIT", 40),
        )
        self._agent_dispatch_pending_count = 0
        self._dispatch_overflow_notification_cooldown_seconds = max(
            0,
            self._safe_int_env("UA_HOOKS_AGENT_DISPATCH_OVERFLOW_NOTIFICATION_COOLDOWN_SECONDS", 120),
        )
        self._dispatch_overflow_state: Dict[str, dict[str, Any]] = {}
        self.config = self._load_config()
        self.transform_cache = {}
        self._seen_webhook_ids: Dict[str, float] = {}

        self._deployment_profile = (os.getenv("UA_DEPLOYMENT_PROFILE") or "local_workstation").strip().lower()
        if self._deployment_profile not in {"local_workstation", "standalone_node", "vps"}:
            self._deployment_profile = "local_workstation"
        self._youtube_ingest_mode = (os.getenv("UA_HOOKS_YOUTUBE_INGEST_MODE") or "").strip().lower()
        default_ingest_url = ""
        self._youtube_ingest_url = (
            os.getenv("UA_HOOKS_YOUTUBE_INGEST_URL", default_ingest_url).strip()
        )
        ingest_urls_raw = (os.getenv("UA_HOOKS_YOUTUBE_INGEST_URLS") or "").strip()
        self._youtube_ingest_urls = self._parse_endpoint_list(
            ingest_urls_raw,
            fallback=self._youtube_ingest_url,
        )
        self._youtube_ingest_urls = self._normalize_youtube_ingest_urls(self._youtube_ingest_urls)
        if self._youtube_ingest_urls:
            self._youtube_ingest_url = self._youtube_ingest_urls[0]
        self._youtube_ingest_token = (
            os.getenv("UA_HOOKS_YOUTUBE_INGEST_TOKEN") or ""
        ).strip()
        self._youtube_ingest_timeout_seconds = self._safe_int_env(
            "UA_HOOKS_YOUTUBE_INGEST_TIMEOUT_SECONDS", 120
        )
        configured_retries = self._safe_int_env(
            "UA_HOOKS_YOUTUBE_INGEST_RETRY_ATTEMPTS",
            10,
        )
        self._youtube_ingest_retries = min(10, max(1, configured_retries))
        self._youtube_ingest_retry_delay_seconds = max(
            0.0, self._safe_float_env("UA_HOOKS_YOUTUBE_INGEST_RETRY_DELAY_SECONDS", 20.0)
        )
        self._tutorial_bootstrap_repo_root = (
            (os.getenv("UA_TUTORIAL_BOOTSTRAP_TARGET_ROOT") or "").strip()
            or (os.getenv("UA_TUTORIAL_BOOTSTRAP_REPO_ROOT") or "").strip()
            or self._default_tutorial_bootstrap_repo_root()
        )
        self._youtube_ingest_retry_max_delay_seconds = max(
            self._youtube_ingest_retry_delay_seconds,
            self._safe_float_env("UA_HOOKS_YOUTUBE_INGEST_RETRY_MAX_DELAY_SECONDS", 90.0),
        )
        self._youtube_ingest_retry_jitter_seconds = max(
            0.0, self._safe_float_env("UA_HOOKS_YOUTUBE_INGEST_RETRY_JITTER_SECONDS", 3.0)
        )
        self._youtube_ingest_min_chars = max(
            20, min(self._safe_int_env("UA_HOOKS_YOUTUBE_INGEST_MIN_CHARS", 160), 5000)
        )
        self._youtube_ingest_cooldown_seconds = max(
            0, self._safe_int_env("UA_HOOKS_YOUTUBE_INGEST_COOLDOWN_SECONDS", 600)
        )
        self._youtube_ingest_inflight_ttl_seconds = max(
            30, self._safe_int_env("UA_HOOKS_YOUTUBE_INGEST_INFLIGHT_TTL_SECONDS", 900)
        )
        self._youtube_ingest_inflight: Dict[str, float] = {}
        self._youtube_ingest_inflight_owners: Dict[str, dict[str, str]] = {}
        self._youtube_ingest_cooldowns: Dict[str, dict[str, Any]] = {}
        self._youtube_ingest_fail_open = self._safe_bool_env(
            "UA_HOOKS_YOUTUBE_INGEST_FAIL_OPEN", False
        )
        # ── Configurable dispatch retry policies ──────────────────────
        # JSON dict keyed by failure reason, e.g.
        # {"hook_dispatch_failed": {"max_retries": 3, "delay_seconds": 60, "backoff_factor": 2.0}}
        self._dispatch_retry_policies: dict[str, dict[str, Any]] = self._parse_dispatch_retry_policies()
        self._default_hook_timeout_seconds = max(
            0, self._safe_int_env("UA_HOOKS_DEFAULT_TIMEOUT_SECONDS", 0)
        )
        self._youtube_hook_timeout_seconds = max(
            60, self._safe_int_env("UA_HOOKS_YOUTUBE_TIMEOUT_SECONDS", 1800)
        )
        self._youtube_hook_idle_timeout_seconds = self._optional_int_env(
            "UA_HOOKS_YOUTUBE_IDLE_TIMEOUT_SECONDS",
            default=900,
            minimum=60,
        )
        self._sync_ready_marker_enabled = self._safe_bool_env(
            "UA_HOOKS_SYNC_READY_MARKER_ENABLED", True
        )
        self._sync_ready_marker_filename = (
            (os.getenv("UA_HOOKS_SYNC_READY_MARKER_FILENAME") or "").strip()
            or DEFAULT_SYNC_READY_MARKER_FILENAME
        )
        self._startup_recovery_enabled = self._safe_bool_env(
            "UA_HOOKS_STARTUP_RECOVERY_ENABLED", True
        )
        self._startup_recovery_max_sessions = max(
            1,
            self._safe_int_env("UA_HOOKS_STARTUP_RECOVERY_MAX_SESSIONS", 3),
        )
        self._startup_recovery_min_age_seconds = max(
            30,
            self._safe_int_env("UA_HOOKS_STARTUP_RECOVERY_MIN_AGE_SECONDS", 120),
        )
        self._startup_recovery_cooldown_seconds = max(
            300,
            self._safe_int_env("UA_HOOKS_STARTUP_RECOVERY_COOLDOWN_SECONDS", 1800),
        )
        self._startup_warmup_delay_seconds = max(
            0,
            self._safe_int_env("UA_HOOKS_STARTUP_WARMUP_DELAY_SECONDS", 15),
        )
        # Legacy marker GC: old pending recovery markers created before
        # workflow-run binding can otherwise survive forever and requeue stale
        # videos on every restart.
        self._legacy_pending_marker_max_age_seconds = max(
            0,
            self._safe_int_env("UA_HOOKS_LEGACY_PENDING_MARKER_MAX_AGE_SECONDS", 21600),
        )
        # Video-level dispatch dedup: prevents the same YouTube video from being
        # processed concurrently by multiple dispatch sources (e.g. playlist watcher
        # + any other webhook source).  Key = video_id, value = start timestamp.
        self._youtube_video_dispatch_inflight: Dict[str, float] = {}
        self._youtube_video_dispatch_lock = asyncio.Lock()
        self._youtube_video_dispatch_dedup_ttl_seconds = max(
            60,
            self._safe_int_env("UA_HOOKS_YOUTUBE_DISPATCH_DEDUP_TTL_SECONDS", 3600),
        )

    def _load_config(self) -> HooksConfig:
        ops_config = load_ops_config()
        hooks_data = ops_config.get("hooks", {})
        if not isinstance(hooks_data, dict):
            hooks_data = {}
        hooks_data = dict(hooks_data)

        hooks_data = self._maybe_bootstrap_youtube_hooks(hooks_data)

        # Env var overrides
        hooks_enabled_raw = (os.getenv("UA_HOOKS_ENABLED") or "").strip().lower()
        if hooks_enabled_raw in {"1", "true", "yes", "on"}:
            hooks_data["enabled"] = True
        elif hooks_enabled_raw in {"0", "false", "no", "off"}:
            hooks_data["enabled"] = False
        if token := os.getenv("UA_HOOKS_TOKEN"):
            hooks_data["token"] = token
            
        return HooksConfig(**hooks_data)

    def _maybe_bootstrap_youtube_hooks(self, hooks_data: dict[str, Any]) -> dict[str, Any]:
        """
        Auto-bootstrap YouTube mappings when hooks config is absent.

        This prevents local stacks from silently disabling hook ingress when
        `ops_config.json` has not been initialized yet.
        """
        auto_bootstrap_raw = (os.getenv("UA_HOOKS_AUTO_BOOTSTRAP") or "").strip().lower()
        if auto_bootstrap_raw in {"0", "false", "no", "off"}:
            return hooks_data

        existing_mappings = hooks_data.get("mappings")
        if isinstance(existing_mappings, list) and existing_mappings:
            return hooks_data

        transforms_dir = str(hooks_data.get("transforms_dir") or "").strip() or DEFAULT_BOOTSTRAP_TRANSFORMS_DIR
        ops_dir = resolve_ops_config_path().parent
        transforms_root = (ops_dir / transforms_dir).resolve()
        composio_transform_path = transforms_root / "composio_youtube_transform.py"
        manual_transform_path = transforms_root / "manual_youtube_transform.py"

        token_configured = bool((hooks_data.get("token") or "").strip() or (os.getenv("UA_HOOKS_TOKEN") or "").strip())
        composio_secret_configured = bool((os.getenv("COMPOSIO_WEBHOOK_SECRET") or "").strip())

        bootstrap_mappings: list[dict[str, Any]] = []

        if composio_transform_path.exists() and composio_secret_configured:
            bootstrap_mappings.append(
                {
                    "id": "composio-youtube-trigger",
                    "match": {"path": "composio"},
                    "action": "agent",
                    "auth": {"strategy": "composio_hmac"},
                    "transform": {
                        "module": "composio_youtube_transform.py",
                        "export": "transform",
                    },
                }
            )

        # Never expose manual ingestion without token auth.
        if manual_transform_path.exists() and token_configured:
            bootstrap_mappings.append(
                {
                    "id": "youtube-manual-url",
                    "match": {"path": "youtube/manual"},
                    "action": "agent",
                    "auth": {"strategy": "token"},
                    "transform": {
                        "module": "manual_youtube_transform.py",
                        "export": "transform",
                    },
                }
            )

        if not bootstrap_mappings:
            return hooks_data

        hooks_data["mappings"] = bootstrap_mappings
        hooks_data.setdefault("transforms_dir", transforms_dir)
        hooks_data.setdefault("max_body_bytes", DEFAULT_BOOTSTRAP_HOOKS_MAX_BODY_BYTES)
        if "enabled" not in hooks_data:
            hooks_data["enabled"] = True
        logger.info(
            "Hook auto-bootstrap enabled mappings=%s transforms_dir=%s",
            [str(item.get("id") or "") for item in bootstrap_mappings],
            hooks_data.get("transforms_dir"),
        )
        return hooks_data

    def is_enabled(self) -> bool:
        return self.config.enabled

    def readiness_status(self) -> dict[str, Any]:
        mapping_ids: list[str] = []
        try:
            for mapping in self.config.mappings:
                mapping_id = str(mapping.id or "").strip()
                if mapping_id:
                    mapping_ids.append(mapping_id)
        except Exception:
            mapping_ids = []

        return {
            "ready": bool(self.config.enabled),
            "hooks_enabled": bool(self.config.enabled),
            "base_path": str(self.config.base_path or "").strip() or "/hooks",
            "max_body_bytes": int(self.config.max_body_bytes),
            "mapping_count": len(mapping_ids),
            "mapping_ids": mapping_ids,
            "youtube_ingest_mode": self._youtube_ingest_mode or "disabled",
            "youtube_ingest_url_configured": bool(self._youtube_ingest_urls),
            "youtube_ingest_url_count": len(self._youtube_ingest_urls),
            "youtube_ingest_fail_open": bool(self._youtube_ingest_fail_open),
            "youtube_ingest_min_chars": int(self._youtube_ingest_min_chars),
            "youtube_ingest_retry_attempts": int(self._youtube_ingest_retries),
            "youtube_ingest_retry_delay_seconds": float(self._youtube_ingest_retry_delay_seconds),
            "youtube_ingest_retry_jitter_seconds": float(self._youtube_ingest_retry_jitter_seconds),
            "youtube_ingest_cooldown_seconds": int(self._youtube_ingest_cooldown_seconds),
            "dispatch_retry_policies": self._dispatch_retry_policies,
            "hook_default_timeout_seconds": int(self._default_hook_timeout_seconds or 0),
            "youtube_hook_timeout_seconds": int(self._youtube_hook_timeout_seconds),
            "youtube_hook_idle_timeout_seconds": int(self._youtube_hook_idle_timeout_seconds or 0),
            "sync_ready_marker_enabled": bool(self._sync_ready_marker_enabled),
            "sync_ready_marker_filename": str(self._sync_ready_marker_filename),
            "agent_dispatch_concurrency": int(self._agent_dispatch_concurrency),
            "agent_dispatch_queue_limit": int(self._agent_dispatch_queue_limit),
            "agent_dispatch_pending_count": int(self._agent_dispatch_pending_count),
            "startup_recovery_enabled": bool(self._startup_recovery_enabled),
            "startup_recovery_max_sessions": int(self._startup_recovery_max_sessions),
            "startup_recovery_min_age_seconds": int(self._startup_recovery_min_age_seconds),
            "startup_recovery_cooldown_seconds": int(self._startup_recovery_cooldown_seconds),
            "startup_warmup_delay_seconds": int(self._startup_warmup_delay_seconds),
        }

    @staticmethod
    def _session_key_from_session_id(session_id: str) -> str:
        sid = str(session_id or "").strip()
        if sid.startswith(HOOK_SESSION_ID_PREFIX):
            return sid[len(HOOK_SESSION_ID_PREFIX) :]
        return sid

    @staticmethod
    def _session_id_from_workspace_dir(session_dir: Path) -> Optional[str]:
        name = str(getattr(session_dir, "name", "") or "").strip()
        if not name:
            return None
        if name.startswith(HOOK_SESSION_ID_PREFIX):
            return name
        if name.startswith("run_"):
            candidate = name[len("run_") :].strip()
            if candidate.startswith(HOOK_SESSION_ID_PREFIX):
                return candidate

        # Migration backstop: prefer explicit marker/session payload when the
        # directory name is no longer enough to recover the live hook session id.
        for marker_name in ("pending_hook_recovery.json", "pending_local_ingest.json", ".hook_startup_recovery.json"):
            marker_path = session_dir / marker_name
            if not marker_path.is_file():
                continue
            try:
                payload = json.loads(marker_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            candidate = str(payload.get("session_id") or "").strip()
            if candidate.startswith(HOOK_SESSION_ID_PREFIX):
                return candidate
        return None

    def _iter_hook_workspace_candidates(
        self,
        workspace_root: Path,
        *,
        session_key_prefix: str,
    ) -> list[tuple[float, Path, str]]:
        root = Path(str(workspace_root or "")).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            return []

        candidates: list[tuple[float, Path, str]] = []
        seen_dirs: set[Path] = set()
        glob_patterns = (
            f"{HOOK_SESSION_ID_PREFIX}{session_key_prefix}*",
            f"run_{HOOK_SESSION_ID_PREFIX}{session_key_prefix}*",
        )
        for pattern in glob_patterns:
            for session_dir in root.glob(pattern):
                try:
                    resolved_dir = session_dir.resolve()
                except Exception:
                    resolved_dir = session_dir
                if resolved_dir in seen_dirs or not session_dir.is_dir():
                    continue
                session_id = self._session_id_from_workspace_dir(session_dir)
                if not session_id or not session_id.startswith(f"{HOOK_SESSION_ID_PREFIX}{session_key_prefix}"):
                    continue
                try:
                    mtime = float(session_dir.stat().st_mtime)
                except Exception:
                    mtime = 0.0
                candidates.append((mtime, session_dir, session_id))
                seen_dirs.add(resolved_dir)
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates

    @staticmethod
    def _youtube_parts_from_session_key(session_key: str) -> tuple[str, str]:
        raw = str(session_key or "").strip()
        if not raw.startswith("yt_"):
            return "", ""
        body = raw[len("yt_") :]
        # New format uses __ (double underscore) as the delimiter between
        # channel and video segments.  YouTube video IDs are base64url
        # ([A-Za-z0-9_-]) and can contain single underscores, but never
        # two consecutive underscores, so __ is unambiguous.
        if "__" in body:
            channel_key, video_id = body.split("__", 1)
            return channel_key.strip(), video_id.strip()
        # Legacy format fallback: single underscore delimiter.  Safe only
        # when the video ID itself does not contain an underscore.
        if "_" not in body:
            return "", ""
        channel_key, video_id = body.rsplit("_", 1)
        return channel_key.strip(), video_id.strip()

    @staticmethod
    def _parse_turn_start_and_finalized(turn_file: Path) -> tuple[Optional[str], bool]:
        started_at: Optional[str] = None
        finalized = False
        try:
            for line in turn_file.read_text(encoding="utf-8").splitlines():
                raw = line.strip()
                if not raw:
                    continue
                event = json.loads(raw)
                if not isinstance(event, dict):
                    continue
                event_kind = str(event.get("event") or "").strip()
                if event_kind == "turn_started" and not started_at:
                    started_at = str(event.get("timestamp") or "").strip() or None
                elif event_kind == "turn_finalized":
                    finalized = True
        except Exception:
            return None, False
        return started_at, finalized

    @staticmethod
    def _parse_iso_epoch(raw: str) -> Optional[float]:
        value = str(raw or "").strip()
        if not value:
            return None
        try:
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            from datetime import datetime

            return float(datetime.fromisoformat(value).timestamp())
        except Exception:
            return None

    def _startup_recovery_marker_path(self, session_dir: Path) -> Path:
        return session_dir / ".hook_startup_recovery.json"

    def _pending_hook_recovery_marker_path(self, session_dir: Path) -> Path:
        return session_dir / "pending_hook_recovery.json"

    def _startup_recovery_allowed(self, session_dir: Path) -> bool:
        marker = self._startup_recovery_marker_path(session_dir)
        payload = self._safe_json(marker)
        attempted_at_epoch = self._parse_iso_epoch(str(payload.get("last_attempt_at") or ""))
        if attempted_at_epoch is None:
            return True
        return (time.time() - attempted_at_epoch) >= float(self._startup_recovery_cooldown_seconds)

    def _record_startup_recovery_attempt(self, session_dir: Path, *, session_id: str) -> None:
        marker = self._startup_recovery_marker_path(session_dir)
        payload = self._safe_json(marker)
        attempts = int(payload.get("attempts") or 0) + 1
        out = {
            "session_id": session_id,
            "attempts": attempts,
            "last_attempt_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        try:
            marker.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
        except Exception:
            logger.warning("Failed writing startup recovery marker session_id=%s", session_id)

    def _write_pending_hook_recovery_marker(
        self,
        session_dir: Path,
        *,
        session_id: str,
        reason: str,
        expected_video_id: str,
        retry_count: int = 0,
        retry_status: str = "dispatch_interrupted",
    ) -> Path:
        marker = self._pending_hook_recovery_marker_path(session_dir)
        out = {
            "status": retry_status,
            "session_id": session_id,
            "video_id": str(expected_video_id or "").strip(),
            "reason": str(reason or "hook_dispatch_interrupted").strip(),
            "created_at_epoch": time.time(),
            "retry_count": retry_count,
        }
        try:
            marker.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
        except Exception:
            logger.warning("Failed writing pending hook recovery marker session_id=%s", session_id)
        return marker

    def _read_pending_hook_recovery_marker(self, session_dir: Path) -> dict[str, Any]:
        marker = self._pending_hook_recovery_marker_path(session_dir)
        return self._safe_json(marker)

    def _clear_pending_hook_recovery_marker(self, session_dir: Path) -> None:
        marker = self._pending_hook_recovery_marker_path(session_dir)
        if not marker.exists():
            return
        try:
            marker.unlink()
        except Exception:
            logger.warning("Failed removing pending hook recovery marker path=%s", marker)

    def _pending_local_ingest_marker_path(self, session_dir: Path) -> Path:
        return session_dir / "pending_local_ingest.json"

    def _clear_pending_local_ingest_marker(self, session_dir: Path) -> None:
        marker = self._pending_local_ingest_marker_path(session_dir)
        if not marker.exists():
            return
        try:
            marker.unlink()
        except Exception:
            logger.warning("Failed removing pending local ingest marker path=%s", marker)

    @staticmethod
    def _pending_marker_created_epoch(payload: dict[str, Any]) -> float:
        try:
            return float(payload.get("created_at_epoch") or 0.0)
        except Exception:
            return 0.0

    @staticmethod
    def _pending_marker_has_workflow_identity(payload: dict[str, Any]) -> bool:
        run_id = str(payload.get("run_id") or "").strip()
        attempt_id = str(payload.get("attempt_id") or "").strip()
        return bool(run_id or attempt_id)

    def _legacy_pending_marker_is_stale(
        self,
        payload: dict[str, Any],
        *,
        now_epoch: float,
    ) -> bool:
        max_age_seconds = float(self._legacy_pending_marker_max_age_seconds)
        if max_age_seconds <= 0:
            return False
        if self._pending_marker_has_workflow_identity(payload):
            return False
        created_epoch = self._pending_marker_created_epoch(payload)
        if created_epoch <= 0:
            return False
        return (now_epoch - created_epoch) >= max_age_seconds

    def _finalize_startup_recovery_if_already_processed(
        self,
        *,
        video_id: str,
        run_id: Optional[str] = None,
        attempt_id: Optional[str] = None,
        session_dir: Optional[Path] = None,
    ) -> bool:
        clean_video_id = str(video_id or "").strip()
        if not clean_video_id:
            return False
        try:
            artifact_validation = self._validate_youtube_tutorial_artifacts(
                video_id=clean_video_id,
                started_at_epoch=0.0,
            )
        except Exception:
            return False

        run_path = str(artifact_validation.get("run_rel_path") or "").strip()
        logger.info(
            "Startup recovery skipped for already-processed tutorial video_id=%s run_id=%s run_path=%s",
            clean_video_id,
            run_id or "",
            run_path,
        )

        if run_id:
            try:
                self._workflow_admission_service().mark_completed(
                    run_id,
                    attempt_id=attempt_id,
                    summary={
                        "status": "completed",
                        "video_id": clean_video_id,
                        "reason": "startup_recovery_artifacts_already_ready",
                        "tutorial_run_path": run_path,
                    },
                )
            except Exception as exc:
                logger.warning(
                    "Startup recovery failed to mark run completed run_id=%s video_id=%s: %s",
                    run_id,
                    clean_video_id,
                    exc,
                )

        if session_dir is not None:
            self._clear_pending_hook_recovery_marker(session_dir)
            self._clear_pending_local_ingest_marker(session_dir)
        return True

    def _parse_dispatch_retry_policies(self) -> dict[str, dict[str, Any]]:
        """Parse UA_HOOKS_DISPATCH_RETRY_POLICIES env var (JSON dict).

        Falls back to a sensible default: hook_dispatch_failed gets 2 retries
        with 120s delay and 2.0 backoff factor.
        """
        default_policies: dict[str, dict[str, Any]] = {
            "hook_dispatch_failed": {
                "max_retries": 2,
                "delay_seconds": 120,
                "backoff_factor": 2.0,
            },
        }
        raw = (os.getenv("UA_HOOKS_DISPATCH_RETRY_POLICIES") or "").strip()
        if not raw:
            return default_policies
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                # Validate and normalise each entry
                result: dict[str, dict[str, Any]] = {}
                for key, val in parsed.items():
                    if isinstance(val, dict):
                        result[str(key)] = {
                            "max_retries": int(val.get("max_retries", 0)),
                            "delay_seconds": float(val.get("delay_seconds", 60)),
                            "backoff_factor": float(val.get("backoff_factor", 1.0)),
                        }
                return result
        except Exception:
            logger.warning("Invalid UA_HOOKS_DISPATCH_RETRY_POLICIES, falling back to defaults")
        return default_policies

    def _get_current_retry_count(self, session_dir: Optional[Path]) -> int:
        """Read retry_count from an existing pending_hook_recovery.json marker, or 0."""
        if session_dir is None:
            return 0
        marker = self._pending_hook_recovery_marker_path(session_dir)
        if not marker.is_file():
            return 0
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return int(payload.get("retry_count", 0))
        except Exception:
            pass
        return 0

    @staticmethod
    def _is_dispatch_interruption_error(reason: str) -> bool:
        lowered = str(reason or "").strip().lower()
        if not lowered:
            return False
        return any(token in lowered for token in YOUTUBE_DISPATCH_INTERRUPTION_ERROR_TOKENS)

    def _dispatch_failure_reason(self, exc: Exception, execution_summary: Optional[dict[str, Any]]) -> str:
        parts: list[str] = []
        if isinstance(exc, Exception):
            parts.append(str(exc))
        if isinstance(execution_summary, dict):
            parts.append(str(execution_summary.get("reported_error_message") or ""))
            parts.append(str(execution_summary.get("iteration_status") or ""))
        detail = " ".join(part for part in parts if part).strip()
        if self._is_dispatch_interruption_error(detail):
            return "hook_dispatch_interrupted"
        return "hook_dispatch_failed"

    @staticmethod
    def _is_retryable_youtube_dispatch_failure(reason: str) -> bool:
        normalized = str(reason or "").strip().lower()
        if not normalized:
            return False
        if normalized in {
            "hook_dispatch_interrupted",
            "hook_dispatch_failed",
            "hook_timeout",
            "hook_idle_timeout",
            "proxy_connect_failed",
        }:
            return True
        return normalized.startswith("hook_timeout_") or normalized.startswith("hook_idle_timeout_")

    @staticmethod
    def _hook_workflow_workspace_dir(session_key: str) -> str:
        safe = SESSION_ID_SANITIZE_RE.sub("_", str(session_key or "").strip()).strip("._-")
        if not safe:
            safe = hashlib.sha256(str(session_key or "youtube").encode("utf-8", errors="replace")).hexdigest()[:16]
        return str((Path("AGENT_RUN_WORKSPACES") / f"run_session_hook_{safe}").resolve())

    @staticmethod
    def _youtube_workflow_workspace_dir(session_key: str) -> str:
        return HooksService._hook_workflow_workspace_dir(session_key)

    def _build_youtube_workflow_trigger(
        self,
        *,
        action: HookAction,
        session_key: str,
        session_id: str,
        expected_video_id: str,
    ) -> WorkflowTrigger:
        video_url = self._extract_action_field(action.message or "", "video_url")
        normalized_url, normalized_video_id = normalize_video_target(video_url, expected_video_id)
        dedup_key = normalized_video_id or normalized_url or session_key
        payload = {
            "session_key": session_key,
            "session_id": session_id,
            "hook_name": action.name or "Hook",
            "route_to": action.to or "",
            "message": action.message or "",
            "model": action.model,
            "thinking": action.thinking,
            "timeout_seconds": action.timeout_seconds,
            "video_id": normalized_video_id or expected_video_id or "",
            "video_url": normalized_url or "",
            "video_title": self._extract_action_field(action.message or "", "title"),
            "workspace_dir": self._youtube_workflow_workspace_dir(session_key),
        }
        return WorkflowTrigger(
            run_kind="youtube_tutorial_hook",
            trigger_source="webhook",
            dedup_key=dedup_key,
            payload_json=json.dumps(payload, ensure_ascii=True, sort_keys=True),
            priority=100,
            run_policy="automation_ephemeral",
            interrupt_policy="attach_if_same_dedup_key",
            external_origin="youtube_webhook",
            external_origin_id=normalized_video_id or expected_video_id or session_id,
            external_correlation_id=session_id,
        )

    @staticmethod
    def _is_retryable_generic_hook_failure(reason: str) -> bool:
        normalized = str(reason or "").strip().lower()
        if not normalized:
            return False
        if normalized in {
            "hook_dispatch_interrupted",
            "hook_dispatch_failed",
            "hook_timeout",
            "hook_idle_timeout",
            "agent_reported_timeout",
        }:
            return True
        return normalized.startswith("hook_timeout_") or normalized.startswith("hook_idle_timeout_")

    def _build_generic_hook_workflow_profile(
        self,
        *,
        action: HookAction,
        session_key: str,
        session_id: str,
    ) -> Optional[dict[str, Any]]:
        hook_name = str(action.name or "").strip()
        workspace_dir = self._hook_workflow_workspace_dir(session_key)
        payload = {
            "session_key": session_key,
            "session_id": session_id,
            "hook_name": hook_name or "Hook",
            "route_to": action.to or "",
            "message": action.message or "",
            "model": action.model,
            "thinking": action.thinking,
            "timeout_seconds": action.timeout_seconds,
            "workspace_dir": workspace_dir,
        }

        if hook_name == "AutoHeartbeatInvestigation":
            activity_id = self._extract_action_field(action.message or "", "activity_id")
            payload["activity_id"] = activity_id
            trigger = WorkflowTrigger(
                run_kind="heartbeat_investigation_hook",
                trigger_source="heartbeat",
                dedup_key=activity_id or session_key,
                payload_json=json.dumps(payload, ensure_ascii=True, sort_keys=True),
                priority=90,
                run_policy="automation_ephemeral",
                interrupt_policy="attach_if_same_dedup_key",
                external_origin="heartbeat_mediation",
                external_origin_id=activity_id or session_id,
                external_correlation_id=activity_id or session_key,
            )
            return {
                "trigger": trigger,
                "workspace_dir": workspace_dir,
                "entrypoint": "hooks_service.heartbeat_investigation",
                "max_attempts": 3,
                "retryable_failure": True,
            }

        if hook_name == "ManualSimoneHandoff":
            activity_id = self._extract_action_field(action.message or "", "activity_id")
            payload["activity_id"] = activity_id
            trigger = WorkflowTrigger(
                run_kind="simone_handoff_hook",
                trigger_source="dashboard",
                dedup_key=activity_id or session_key,
                payload_json=json.dumps(payload, ensure_ascii=True, sort_keys=True),
                priority=80,
                run_policy="automation_ephemeral",
                interrupt_policy="attach_if_same_dedup_key",
                external_origin="dashboard_activity",
                external_origin_id=activity_id or session_id,
                external_correlation_id=activity_id or session_key,
            )
            return {
                "trigger": trigger,
                "workspace_dir": workspace_dir,
                "entrypoint": "hooks_service.simone_handoff",
                "max_attempts": 3,
                "retryable_failure": True,
            }

        if (
            hook_name in {"AgentMailInbound", "AgentMailWebhook"}
            or str(action.to or "").strip().lower() == "email-handler"
            or session_key.startswith("agentmail_")
        ):
            thread_id = self._extract_action_field(action.message or "", "thread_id")
            message_id = self._extract_action_field(action.message or "", "message_id")
            sender_email = self._extract_action_field(action.message or "", "sender_email")
            payload.update(
                {
                    "thread_id": thread_id,
                    "message_id": message_id,
                    "sender_email": sender_email,
                }
            )
            trigger = WorkflowTrigger(
                run_kind="agentmail_inbound_hook",
                trigger_source="agentmail",
                dedup_key=message_id or thread_id or session_key,
                payload_json=json.dumps(payload, ensure_ascii=True, sort_keys=True),
                priority=85,
                run_policy="automation_ephemeral",
                interrupt_policy="attach_if_same_dedup_key",
                external_origin="agentmail",
                external_origin_id=message_id or thread_id or session_id,
                external_correlation_id=thread_id or session_key,
            )
            return {
                "trigger": trigger,
                "workspace_dir": workspace_dir,
                "entrypoint": "hooks_service.agentmail_inbound",
                "max_attempts": 3,
                "retryable_failure": True,
            }

        return None

    def _workflow_profile_for_action(
        self,
        *,
        action: HookAction,
        session_key: str,
        session_id: str,
        expected_video_id: str,
    ) -> Optional[dict[str, Any]]:
        if _is_youtube_agent_route(action.to):
            return {
                "trigger": self._build_youtube_workflow_trigger(
                    action=action,
                    session_key=session_key,
                    session_id=session_id,
                    expected_video_id=expected_video_id,
                ),
                "workspace_dir": self._youtube_workflow_workspace_dir(session_key),
                "entrypoint": "hooks_service.youtube_tutorial_hook",
                "max_attempts": 3,
                "retryable_failure": True,
            }
        return self._build_generic_hook_workflow_profile(
            action=action,
            session_key=session_key,
            session_id=session_id,
        )

    def _schedule_generic_hook_retry_attempt(
        self,
        *,
        action: HookAction,
        run_id: str,
        attempt_id: str,
        workspace_dir: str,
    ) -> None:
        asyncio.create_task(
            self._dispatch_action(
                action,
                workflow_run_id=run_id,
                workflow_attempt_id=attempt_id,
                workflow_workspace_dir=workspace_dir,
                skip_workflow_admission=True,
            )
        )

    def _queue_or_finalize_generic_hook_attempt(
        self,
        *,
        action: HookAction,
        hook_name: str,
        reason: str,
        failure_class: str,
        session_workspace: Optional[Path],
        workflow_profile: Optional[dict[str, Any]],
        workflow_run_id: Optional[str],
        workflow_attempt_id: Optional[str],
    ) -> None:
        if (
            workflow_profile is None
            or not workflow_run_id
            or not workflow_attempt_id
        ):
            return
        workflow_service = self._workflow_admission_service()
        workspace_dir = str(session_workspace) if session_workspace is not None else str(
            workflow_profile.get("workspace_dir") or ""
        )
        max_attempts = max(1, int(workflow_profile.get("max_attempts") or 1))
        trigger = workflow_profile.get("trigger")
        entrypoint = str(workflow_profile.get("entrypoint") or "hooks_service.generic_hook")
        retryable_failure = bool(workflow_profile.get("retryable_failure"))

        if retryable_failure and self._is_retryable_generic_hook_failure(reason) and isinstance(trigger, WorkflowTrigger):
            retry_decision = workflow_service.queue_retry(
                trigger,
                entrypoint=entrypoint,
                run_id=workflow_run_id,
                attempt_id=workflow_attempt_id,
                workspace_dir=workspace_dir,
                failure_reason=reason,
                failure_class=failure_class,
                max_attempts=max_attempts,
            )
            if retry_decision.action == "start_new_attempt" and retry_decision.attempt_id:
                self._schedule_generic_hook_retry_attempt(
                    action=action,
                    run_id=workflow_run_id,
                    attempt_id=retry_decision.attempt_id,
                    workspace_dir=workspace_dir,
                )
                return

        workflow_service.mark_needs_review(
            workflow_run_id,
            attempt_id=workflow_attempt_id,
            reason=reason,
            failure_class=failure_class,
            summary={
                "hook_name": hook_name,
                "status": "needs_review",
            },
        )

    def _workflow_attempt_context(
        self,
        *,
        run_id: Optional[str],
        attempt_id: Optional[str],
    ) -> dict[str, Any]:
        if not run_id and not attempt_id:
            return {}
        def _operation(conn: Any) -> dict[str, Any]:
            run_row = get_run(conn, run_id) if run_id else None
            attempt_row = get_run_attempt(conn, attempt_id) if attempt_id else None
            context: dict[str, Any] = {}
            if run_row is not None:
                context["run_id"] = str(run_row["run_id"] or "")
                context["run_status"] = str(run_row["status"] or "")
                context["workspace_dir"] = str(run_row["workspace_dir"] or "")
                context["attempt_count"] = int(run_row["attempt_count"] or 0)
            if attempt_row is not None:
                context["attempt_id"] = str(attempt_row["attempt_id"] or "")
                context["attempt_number"] = int(attempt_row["attempt_number"] or 0)
                context["attempt_status"] = str(attempt_row["status"] or "")
                context["provider_session_id"] = str(attempt_row["provider_session_id"] or "")
            return context
        return self._run_with_runtime_db_retry(_operation)

    def _workflow_attempt_context_safe(
        self,
        *,
        run_id: Optional[str],
        attempt_id: Optional[str],
    ) -> dict[str, Any]:
        try:
            return self._workflow_attempt_context(
                run_id=run_id,
                attempt_id=attempt_id,
            )
        except sqlite3.OperationalError as exc:
            if not _is_sqlite_lock_error(exc):
                raise
            logger.warning(
                "Workflow attempt context lookup locked run_id=%s attempt_id=%s: %s",
                run_id,
                attempt_id,
                exc,
            )
            return {}

    @staticmethod
    def _merge_workflow_metadata(
        metadata: dict[str, Any],
        *,
        run_id: Optional[str],
        attempt_id: Optional[str],
        attempt_number: Optional[int],
        workspace_dir: Optional[str],
        provider_session_id: Optional[str],
        retry_count: Optional[int] = None,
        max_attempts: Optional[int] = None,
        recovered_after_retry: Optional[bool] = None,
        recovered_from_reason: Optional[str] = None,
        dispatch_issue_resolved: Optional[bool] = None,
    ) -> dict[str, Any]:
        enriched = dict(metadata)
        if run_id:
            enriched["run_id"] = run_id
        if attempt_id:
            enriched["attempt_id"] = attempt_id
        if attempt_number:
            enriched["attempt_number"] = int(attempt_number)
        if workspace_dir:
            enriched["workspace_dir"] = workspace_dir
        if provider_session_id:
            enriched["provider_session_id"] = provider_session_id
        if retry_count is not None:
            if "retry_count" in enriched:
                enriched["workflow_retry_count"] = int(retry_count)
            else:
                enriched["retry_count"] = int(retry_count)
        if max_attempts is not None:
            if "max_attempts" in enriched:
                enriched["workflow_max_attempts"] = int(max_attempts)
            else:
                enriched["max_attempts"] = int(max_attempts)
        if recovered_after_retry is not None:
            enriched["recovered_after_retry"] = bool(recovered_after_retry)
        if recovered_from_reason:
            enriched["recovered_from_reason"] = recovered_from_reason
        if dispatch_issue_resolved is not None:
            enriched["dispatch_issue_resolved"] = bool(dispatch_issue_resolved)
        return enriched

    def _build_youtube_action_from_run_spec(self, run_spec: dict[str, Any]) -> Optional[HookAction]:
        if not isinstance(run_spec, dict):
            return None
        payload_json = run_spec.get("payload_json")
        payload: dict[str, Any] = {}
        if isinstance(payload_json, str) and payload_json.strip():
            try:
                parsed = json.loads(payload_json)
                if isinstance(parsed, dict):
                    payload = parsed
            except Exception:
                payload = {}
        if not payload:
            payload = dict(run_spec)
        session_key = str(payload.get("session_key") or "").strip()
        message = str(payload.get("message") or "").strip()
        if not session_key or not message:
            return None
        timeout_value = payload.get("timeout_seconds")
        timeout_seconds = int(timeout_value) if isinstance(timeout_value, (int, float)) and int(timeout_value) > 0 else None
        return HookAction(
            kind="agent",
            name=str(payload.get("hook_name") or "RecoveredYouTubeWebhook"),
            session_key=session_key,
            to=str(payload.get("route_to") or YOUTUBE_AGENT_CANONICAL),
            message=message,
            deliver=True,
            model=str(payload.get("model") or "") or None,
            thinking=str(payload.get("thinking") or "") or None,
            timeout_seconds=timeout_seconds,
        )

    def _emit_youtube_retry_queued_notification(
        self,
        *,
        session_id: str,
        session_key: str,
        hook_name: str,
        expected_video_id: str,
        current_attempt_number: int,
        next_attempt_number: int,
        max_attempts: int,
        reason: str,
        run_id: Optional[str],
        attempt_id: Optional[str],
        workspace_dir: Optional[str],
    ) -> None:
        self._emit_notification(
            kind="youtube_tutorial_interrupted",
            title="YouTube Tutorial Interrupted",
            message=(
                f"Tutorial pipeline attempt {current_attempt_number} was interrupted. "
                f"Automatic retry attempt {next_attempt_number}/{max_attempts} has been queued."
            ),
            session_id=session_id,
            severity="warning",
            requires_action=True,
            metadata=self._merge_workflow_metadata(
                {
                    "source": "hooks",
                    "hook_name": hook_name,
                    "hook_session_key": session_key,
                    "video_id": expected_video_id or "",
                    "reason": reason,
                },
                run_id=run_id,
                attempt_id=attempt_id,
                attempt_number=current_attempt_number,
                workspace_dir=workspace_dir,
                provider_session_id=session_id,
                retry_count=max(0, next_attempt_number - 1),
                max_attempts=max_attempts,
            ),
        )

    def _schedule_youtube_retry_attempt(
        self,
        *,
        action: HookAction,
        run_id: str,
        attempt_id: str,
        workspace_dir: str,
    ) -> None:
        asyncio.create_task(
            self._dispatch_action(
                action,
                workflow_run_id=run_id,
                workflow_attempt_id=attempt_id,
                workflow_workspace_dir=workspace_dir,
                skip_workflow_admission=True,
            )
        )

    def _queue_or_finalize_youtube_attempt(
        self,
        *,
        action: HookAction,
        session_id: str,
        session_key: str,
        hook_name: str,
        expected_video_id: str,
        reason: str,
        failure_class: str,
        session_workspace: Optional[Path],
        started_at_epoch: Optional[float],
        workflow_run_id: Optional[str],
        workflow_attempt_id: Optional[str],
        workflow_attempt_number: Optional[int],
    ) -> None:
        if not workflow_run_id or not workflow_attempt_id:
            self._emit_youtube_tutorial_failure_notification(
                session_id=session_id,
                session_key=session_key,
                hook_name=hook_name,
                expected_video_id=expected_video_id,
                reason=reason,
                started_at_epoch=started_at_epoch,
                workspace_dir=str(session_workspace) if session_workspace is not None else None,
                provider_session_id=session_id,
            )
            return

        workflow_service = self._workflow_admission_service()
        workspace_dir = str(session_workspace) if session_workspace is not None else self._youtube_workflow_workspace_dir(session_key)
        max_attempts = 3
        if self._is_retryable_youtube_dispatch_failure(reason):
            retry_decision = workflow_service.queue_retry(
                self._build_youtube_workflow_trigger(
                    action=action,
                    session_key=session_key,
                    session_id=session_id,
                    expected_video_id=expected_video_id,
                ),
                entrypoint="hooks_service.youtube_tutorial_hook",
                run_id=workflow_run_id,
                attempt_id=workflow_attempt_id,
                workspace_dir=workspace_dir,
                failure_reason=reason,
                failure_class=failure_class,
                max_attempts=max_attempts,
            )
            if retry_decision.action == "start_new_attempt" and retry_decision.attempt_id:
                retry_context = self._workflow_attempt_context_safe(
                    run_id=workflow_run_id,
                    attempt_id=retry_decision.attempt_id,
                )
                self._emit_youtube_retry_queued_notification(
                    session_id=session_id,
                    session_key=session_key,
                    hook_name=hook_name,
                    expected_video_id=expected_video_id,
                    current_attempt_number=workflow_attempt_number or 1,
                    next_attempt_number=int(retry_context.get("attempt_number") or (workflow_attempt_number or 1) + 1),
                    max_attempts=max_attempts,
                    reason=reason,
                    run_id=workflow_run_id,
                    attempt_id=workflow_attempt_id,
                    workspace_dir=workspace_dir,
                )
                self._schedule_youtube_retry_attempt(
                    action=action,
                    run_id=workflow_run_id,
                    attempt_id=retry_decision.attempt_id,
                    workspace_dir=workspace_dir,
                )
                return

        workflow_service.mark_needs_review(
            workflow_run_id,
            attempt_id=workflow_attempt_id,
            reason=reason,
            failure_class=failure_class,
            summary={
                "video_id": expected_video_id or "",
                "hook_name": hook_name,
                "workspace_dir": workspace_dir,
            },
        )
        self._emit_youtube_tutorial_failure_notification(
            session_id=session_id,
            session_key=session_key,
            hook_name=hook_name,
            expected_video_id=expected_video_id,
            reason=reason,
            started_at_epoch=started_at_epoch,
            run_id=workflow_run_id,
            attempt_id=workflow_attempt_id,
            attempt_number=workflow_attempt_number,
            workspace_dir=workspace_dir,
            provider_session_id=session_id,
            max_attempts=max_attempts,
        )

    @staticmethod
    def _pending_local_ingest_failure_class(pending_payload: dict[str, Any]) -> str:
        if not isinstance(pending_payload, dict):
            return ""
        last_result = pending_payload.get("last_result")
        if isinstance(last_result, dict):
            failure_class = str(last_result.get("failure_class") or "").strip().lower()
            if failure_class:
                return failure_class
        attempts = pending_payload.get("attempts")
        if isinstance(attempts, list):
            for attempt in reversed(attempts):
                if not isinstance(attempt, dict):
                    continue
                failure_class = str(attempt.get("failure_class") or "").strip().lower()
                if failure_class:
                    return failure_class
        return str(pending_payload.get("failure_class") or "").strip().lower()

    def _load_existing_ingest_context(self, workspace_root: Path) -> dict[str, Any]:
        candidates = (
            workspace_root / "local_ingest_result.json",
            workspace_root / "pending_local_ingest.json",
            workspace_root / "ingestion" / "youtube_local_ingest_result.json",
        )
        for path in candidates:
            if not path.is_file():
                continue
            payload = self._safe_json(path)
            if not isinstance(payload, dict):
                continue
            failure_class = self._pending_local_ingest_failure_class(payload)
            if not failure_class:
                continue
            last_result = payload.get("last_result") if isinstance(payload.get("last_result"), dict) else {}
            attempts_used = int(payload.get("attempt_count") or len(payload.get("attempts") or []))
            max_attempts = int(payload.get("max_attempts") or self._youtube_ingest_retries)
            error = str(last_result.get("error") or payload.get("error") or "").strip()
            reason = self._format_ingest_failure_reason(
                error=error or "local_ingest_failed",
                failure_class=failure_class,
                attempts=attempts_used,
                max_attempts=max_attempts,
            )
            return {
                "root_error": error or "local_ingest_failed",
                "root_failure_class": failure_class,
                "root_reason": reason,
                "root_result_file": str(path),
                "root_attempts": attempts_used,
                "root_max_attempts": max_attempts,
            }
        return {}

    def _current_inflight_ingest_context(self, video_key: str) -> dict[str, Any]:
        owner = self._youtube_ingest_inflight_owners.get(video_key)
        if not isinstance(owner, dict):
            return {}
        workspace_raw = str(owner.get("workspace_root") or "").strip()
        if not workspace_raw:
            return {}
        context = self._load_existing_ingest_context(Path(workspace_raw))
        if context:
            context.setdefault("owner_session_id", str(owner.get("session_id") or "").strip())
        return context

    def _build_youtube_recovery_action(self, *, session_id: str) -> Optional[HookAction]:
        session_key = self._session_key_from_session_id(session_id)
        channel_key, video_id = self._youtube_parts_from_session_key(session_key)
        if not video_id:
            return None
        channel_id = channel_key.replace("_", "-") if channel_key else ""
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        recovery_message = "\n".join(
            [
                "Recovered interrupted YouTube webhook run after service restart/OOM.",
                f"video_url: {video_url}",
                f"video_id: {video_id}",
                f"channel_id: {channel_id}",
                "mode: auto",
                "learning_mode: auto",
                "allow_degraded_transcript_only: true",
                "Resume this tutorial run and complete artifact generation.",
            ]
        )
        return HookAction(
            kind="agent",
            name="RecoveredYouTubeWebhook",
            session_key=session_key,
            to=YOUTUBE_AGENT_CANONICAL,
            message=recovery_message,
            deliver=True,
        )

    def _build_youtube_recovery_action_from_pending(
        self,
        *,
        session_id: str,
        pending_payload: dict[str, Any],
    ) -> Optional[HookAction]:
        session_key = self._session_key_from_session_id(session_id)
        _, key_video_id = self._youtube_parts_from_session_key(session_key)
        video_url = str(pending_payload.get("video_url") or "").strip()
        video_id = str(pending_payload.get("video_id") or "").strip() or key_video_id
        if not video_url and video_id:
            video_url = f"https://www.youtube.com/watch?v={video_id}"
        if not video_url:
            return None
        message_lines = [
            "Recovered failed local ingest run during startup backfill.",
            f"video_url: {video_url}",
            f"video_id: {video_id}",
            "mode: auto",
            "learning_mode: auto",
            "allow_degraded_transcript_only: true",
            "Resume this tutorial run and complete artifact generation.",
        ]
        return HookAction(
            kind="agent",
            name="RecoveredPendingLocalIngest",
            session_key=session_key,
            to=YOUTUBE_AGENT_CANONICAL,
            message="\n".join(message_lines),
            deliver=True,
        )

    def _build_youtube_recovery_action_from_pending_interrupt(
        self,
        *,
        session_id: str,
        pending_payload: dict[str, Any],
    ) -> Optional[HookAction]:
        # Prefer the canonical session-key derived recovery action because it
        # keeps routing deterministic after restarts.
        action = self._build_youtube_recovery_action(session_id=session_id)
        if action is not None:
            return action

        video_id = str(pending_payload.get("video_id") or "").strip()
        if not video_id:
            return None
        session_key = self._session_key_from_session_id(session_id)
        message_lines = [
            "Recovered interrupted YouTube webhook run during startup backfill.",
            f"video_url: https://www.youtube.com/watch?v={video_id}",
            f"video_id: {video_id}",
            "mode: auto",
            "learning_mode: auto",
            "allow_degraded_transcript_only: true",
            "Resume this tutorial run and complete artifact generation.",
        ]
        return HookAction(
            kind="agent",
            name="RecoveredInterruptedHookDispatch",
            session_key=session_key,
            to=YOUTUBE_AGENT_CANONICAL,
            message="\n".join(message_lines),
            deliver=True,
        )

    async def recover_interrupted_youtube_sessions(self, workspace_root: Path) -> int:
        if not self._startup_recovery_enabled:
            return 0
        # Warmup delay: give LLM adapters time to initialise after deploy/restart
        if self._startup_warmup_delay_seconds > 0:
            logger.info(
                "Startup recovery: waiting %ds for LLM adapters to warm up",
                self._startup_warmup_delay_seconds,
            )
            await asyncio.sleep(self._startup_warmup_delay_seconds)
        root = Path(str(workspace_root or "")).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            return 0
        candidates = self._iter_hook_workspace_candidates(root, session_key_prefix="yt_")

        recovered: int = 0
        now_epoch = time.time()
        conn = self._runtime_db_connect()
        try:
            runtime_rows = conn.execute(
                """
                SELECT r.run_id,
                       r.run_spec_json,
                       r.workspace_dir,
                       r.provider_session_id,
                       r.latest_attempt_id,
                       a.status AS attempt_status,
                       a.attempt_number,
                       a.failure_reason
                FROM runs r
                LEFT JOIN run_attempts a ON a.attempt_id = r.latest_attempt_id
                WHERE r.run_kind = 'youtube_tutorial_hook'
                ORDER BY r.updated_at DESC, r.created_at DESC
                """
            ).fetchall()
        finally:
            conn.close()

        for row in runtime_rows:
            if recovered >= self._startup_recovery_max_sessions:
                break
            attempt_status = str(row["attempt_status"] or "").strip().lower()
            if attempt_status not in {"queued", "blocked", "running"}:
                continue
            try:
                run_spec = json.loads(str(row["run_spec_json"] or "{}"))
            except Exception:
                run_spec = {}
            action = self._build_youtube_action_from_run_spec(run_spec if isinstance(run_spec, dict) else {})
            if action is None:
                continue
            run_id = str(row["run_id"] or "").strip()
            latest_attempt_id = str(row["latest_attempt_id"] or "").strip()
            expected_video_id = self._extract_action_field(action.message or "", "video_id")
            provider_session_id = str(row["provider_session_id"] or "").strip() or self._session_id_from_key(action.session_key or "")
            workspace_dir = str(row["workspace_dir"] or self._youtube_workflow_workspace_dir(action.session_key or "")).strip()
            workspace_path: Optional[Path] = None
            if workspace_dir:
                try:
                    workspace_path = Path(workspace_dir).expanduser().resolve()
                except Exception:
                    workspace_path = Path(workspace_dir)
            if expected_video_id and self._finalize_startup_recovery_if_already_processed(
                video_id=expected_video_id,
                run_id=run_id or None,
                attempt_id=latest_attempt_id or None,
                session_dir=workspace_path,
            ):
                continue
            if attempt_status == "running":
                try:
                    await self.gateway.resume_session(provider_session_id)
                    continue
                except Exception:
                    retry_decision = self._workflow_admission_service().queue_retry(
                        self._build_youtube_workflow_trigger(
                            action=action,
                            session_key=str(action.session_key or ""),
                            session_id=provider_session_id,
                            expected_video_id=expected_video_id,
                        ),
                        entrypoint="hooks_service.youtube_tutorial_hook",
                        run_id=run_id,
                        attempt_id=latest_attempt_id,
                        workspace_dir=workspace_dir,
                        failure_reason="startup_interrupted",
                        failure_class="startup_interrupted",
                        max_attempts=3,
                    )
                    if retry_decision.action != "start_new_attempt" or not retry_decision.attempt_id:
                        continue
                    asyncio.create_task(
                        self._dispatch_action(
                            action,
                            workflow_run_id=run_id,
                            workflow_attempt_id=retry_decision.attempt_id,
                            workflow_workspace_dir=workspace_dir,
                            skip_workflow_admission=True,
                        )
                    )
                    recovered += 1
                    continue

            asyncio.create_task(
                self._dispatch_action(
                    action,
                    workflow_run_id=run_id,
                    workflow_attempt_id=latest_attempt_id,
                    workflow_workspace_dir=workspace_dir,
                    skip_workflow_admission=True,
                )
            )
            recovered += 1
            self._emit_notification(
                kind="youtube_hook_recovery_queued",
                title="Recovered Interrupted YouTube Hook",
                message=f"Queued recovery run for session {provider_session_id}",
                session_id=provider_session_id,
                severity="warning",
                metadata=self._merge_workflow_metadata(
                    {
                        "source": "hooks",
                        "reason": "runtime_db_recovery",
                        "video_id": expected_video_id,
                    },
                    run_id=run_id,
                    attempt_id=latest_attempt_id,
                    attempt_number=int(row["attempt_number"] or 0) or None,
                    workspace_dir=workspace_dir,
                    provider_session_id=provider_session_id,
                ),
            )

        for _, session_dir, session_id in candidates:
            if recovered >= self._startup_recovery_max_sessions:
                break
            turns_dir = session_dir / "turns"
            if not turns_dir.exists() or not turns_dir.is_dir():
                continue
            turn_files = sorted(
                turns_dir.glob("*.jsonl"),
                key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
                reverse=True,
            )
            if not turn_files:
                continue
            started_at, finalized = self._parse_turn_start_and_finalized(turn_files[0])
            if finalized or not started_at:
                continue
            started_epoch = self._parse_iso_epoch(started_at)
            if started_epoch is not None and (now_epoch - started_epoch) < float(self._startup_recovery_min_age_seconds):
                continue
            if not self._startup_recovery_allowed(session_dir):
                continue
            action = self._build_youtube_recovery_action(session_id=session_id)
            if action is None:
                continue
            expected_video_id = self._extract_action_field(action.message or "", "video_id")
            if expected_video_id and self._finalize_startup_recovery_if_already_processed(
                video_id=expected_video_id,
                session_dir=session_dir,
            ):
                continue
            self._record_startup_recovery_attempt(session_dir, session_id=session_id)
            asyncio.create_task(self._dispatch_action(action))
            recovered += 1
            logger.warning(
                "Queued startup recovery for interrupted youtube hook session_id=%s",
                session_id,
            )
            self._emit_notification(
                kind="youtube_hook_recovery_queued",
                title="Recovered Interrupted YouTube Hook",
                message=f"Queued recovery run for session {session_id}",
                session_id=session_id,
                severity="warning",
                metadata={"source": "hooks", "reason": "startup_recovery"},
            )

        for _, session_dir, session_id in candidates:
            if recovered >= self._startup_recovery_max_sessions:
                break
            pending_dispatch_path = self._pending_hook_recovery_marker_path(session_dir)
            if not pending_dispatch_path.is_file():
                continue
            pending_payload = self._safe_json(pending_dispatch_path)
            if not isinstance(pending_payload, dict):
                continue
            pending_status = str(pending_payload.get("status") or "").strip().lower()
            if pending_status == "recovered":
                self._clear_pending_hook_recovery_marker(session_dir)
                continue
            if pending_status not in {"dispatch_interrupted", "dispatch_retry_pending"}:
                continue
            if self._legacy_pending_marker_is_stale(pending_payload, now_epoch=now_epoch):
                marker_video_id = str(pending_payload.get("video_id") or "").strip()
                created_epoch = self._pending_marker_created_epoch(pending_payload)
                marker_age_seconds = max(0, int(now_epoch - created_epoch)) if created_epoch > 0 else None
                logger.info(
                    "Clearing stale legacy pending dispatch marker session_id=%s video_id=%s age_seconds=%s",
                    session_id,
                    marker_video_id,
                    marker_age_seconds if marker_age_seconds is not None else "unknown",
                )
                self._clear_pending_hook_recovery_marker(session_dir)
                continue
            marker_video_id = str(pending_payload.get("video_id") or "").strip()
            if marker_video_id and self._finalize_startup_recovery_if_already_processed(
                video_id=marker_video_id,
                session_dir=session_dir,
            ):
                continue
            # ── Retry limit check for retry-pending markers ──────────
            if pending_status == "dispatch_retry_pending":
                retry_count = int(pending_payload.get("retry_count", 0))
                failure_reason = str(pending_payload.get("reason") or "hook_dispatch_failed").strip()
                retry_policy = self._dispatch_retry_policies.get(failure_reason, {})
                max_retries = int(retry_policy.get("max_retries", 0))
                if retry_count >= max_retries:
                    # Exceeded max retries — emit permanent failure and remove marker
                    self._clear_pending_hook_recovery_marker(session_dir)
                    video_id = str(pending_payload.get("video_id") or "").strip()
                    self._emit_notification(
                        kind="youtube_tutorial_failed",
                        title="YouTube Tutorial Failed — Retries Exhausted",
                        message=(
                            f"{video_id or session_id}: {failure_reason}; "
                            f"all {max_retries} retries exhausted."
                        ),
                        session_id=session_id,
                        severity="error",
                        requires_action=True,
                        metadata={
                            "source": "hooks",
                            "video_id": video_id,
                            "reason": failure_reason,
                            "retry_count": retry_count,
                            "max_retries": max_retries,
                            "retries_exhausted": True,
                        },
                    )
                    logger.warning(
                        "Retry limit exhausted for session_id=%s reason=%s (%d/%d)",
                        session_id, failure_reason, retry_count, max_retries,
                    )
                    continue
            created_epoch = float(pending_payload.get("created_at_epoch") or 0.0)
            if created_epoch > 0 and (now_epoch - created_epoch) < float(self._startup_recovery_min_age_seconds):
                continue
            if not self._startup_recovery_allowed(session_dir):
                continue
            action = self._build_youtube_recovery_action_from_pending_interrupt(
                session_id=session_id,
                pending_payload=pending_payload,
            )
            if action is None:
                continue
            self._record_startup_recovery_attempt(session_dir, session_id=session_id)
            asyncio.create_task(self._dispatch_action(action))
            recovered += 1
            logger.warning(
                "Queued startup recovery for interrupted youtube dispatch session_id=%s",
                session_id,
            )
            self._emit_notification(
                kind="youtube_hook_recovery_queued",
                title="Recovered Interrupted YouTube Dispatch",
                message=f"Queued recovery run for session {session_id}",
                session_id=session_id,
                severity="warning",
                metadata={"source": "hooks", "reason": "startup_dispatch_interrupted_backfill"},
            )

        for _, session_dir, session_id in candidates:
            if recovered >= self._startup_recovery_max_sessions:
                break
            pending_path = session_dir / "pending_local_ingest.json"
            if not pending_path.is_file():
                continue
            pending_payload = self._safe_json(pending_path)
            if not isinstance(pending_payload, dict):
                continue
            pending_status = str(pending_payload.get("status") or "").strip().lower()
            if pending_status not in {"failed_local_ingest", "pending_local_ingest"}:
                continue
            if self._legacy_pending_marker_is_stale(pending_payload, now_epoch=now_epoch):
                marker_video_id = str(pending_payload.get("video_id") or "").strip()
                created_epoch = self._pending_marker_created_epoch(pending_payload)
                marker_age_seconds = max(0, int(now_epoch - created_epoch)) if created_epoch > 0 else None
                logger.info(
                    "Clearing stale legacy pending local ingest marker session_id=%s video_id=%s age_seconds=%s",
                    session_id,
                    marker_video_id,
                    marker_age_seconds if marker_age_seconds is not None else "unknown",
                )
                self._clear_pending_local_ingest_marker(session_dir)
                continue
            marker_video_id = str(pending_payload.get("video_id") or "").strip()
            if marker_video_id and self._finalize_startup_recovery_if_already_processed(
                video_id=marker_video_id,
                session_dir=session_dir,
            ):
                continue
            created_epoch = float(pending_payload.get("created_at_epoch") or 0.0)
            if created_epoch > 0 and (now_epoch - created_epoch) < float(self._startup_recovery_min_age_seconds):
                continue
            failure_class = self._pending_local_ingest_failure_class(pending_payload)
            if failure_class in YOUTUBE_INGEST_NON_RETRYABLE_FAILURE_CLASSES:
                logger.info(
                    "Skipping startup backfill for non-retryable youtube ingest session_id=%s failure_class=%s",
                    session_id,
                    failure_class,
                )
                continue
            if not self._startup_recovery_allowed(session_dir):
                continue
            action = self._build_youtube_recovery_action_from_pending(
                session_id=session_id,
                pending_payload=pending_payload,
            )
            if action is None:
                continue
            self._record_startup_recovery_attempt(session_dir, session_id=session_id)
            asyncio.create_task(self._dispatch_action(action))
            recovered += 1
            logger.warning(
                "Queued startup backfill for pending local ingest session_id=%s",
                session_id,
            )
            self._emit_notification(
                kind="youtube_hook_recovery_queued",
                title="Recovered Failed YouTube Ingest",
                message=f"Queued degraded recovery for session {session_id}",
                session_id=session_id,
                severity="warning",
                metadata={"source": "hooks", "reason": "startup_pending_local_ingest_backfill"},
            )
        return recovered

    def _emit_notification(
        self,
        *,
        kind: str,
        title: str,
        message: str,
        session_id: Optional[str],
        severity: str,
        requires_action: bool = False,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        if not self._notification_sink:
            return
        payload = {
            "kind": str(kind or "").strip() or "hook_event",
            "title": str(title or "").strip() or "Hook Event",
            "message": str(message or "").strip() or "Hook event",
            "session_id": str(session_id or "").strip() or None,
            "severity": str(severity or "").strip() or "info",
            "requires_action": bool(requires_action),
            "metadata": dict(metadata or {}),
        }
        try:
            self._notification_sink(payload)
        except Exception:
            logger.exception("Failed emitting hook notification payload=%s", payload)

    @staticmethod
    def _read_json_file(path: Path) -> dict[str, Any]:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _emit_heartbeat_investigation_completion(
        self,
        *,
        session_id: str,
        session_key: str,
        workspace_root: Optional[Path],
    ) -> None:
        if workspace_root is None:
            return
        work_products = workspace_root / "work_products"
        json_path = work_products / "heartbeat_investigation_summary.json"
        md_path = work_products / "heartbeat_investigation_summary.md"
        payload = self._read_json_file(json_path) if json_path.exists() else {}
        summary_text = ""
        if md_path.exists():
            try:
                summary_text = md_path.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                summary_text = ""
        source_notification_id = str(payload.get("source_notification_id") or "").strip()
        classification = str(payload.get("classification") or "unknown_issue").strip() or "unknown_issue"
        operator_review_required = bool(payload.get("operator_review_required"))
        recommended_next_step = sanitize_heartbeat_recommendation_text(
            str(payload.get("recommended_next_step") or "").strip(),
            field="next_step",
        )
        email_summary = sanitize_heartbeat_recommendation_text(
            str(payload.get("email_summary") or "").strip(),
            field="summary",
        )
        metadata: dict[str, Any] = {
            "source": "heartbeat",
            "session_key": session_key,
            "hook_session_key": session_key,
            "hook_session_id": session_id,
            "source_notification_id": source_notification_id,
            "classification": classification,
            "operator_review_required": operator_review_required,
            "recommended_next_step": recommended_next_step,
            "email_summary": email_summary,
            "proposed_changes": payload.get("proposed_changes") if isinstance(payload.get("proposed_changes"), list) else [],
            "unknown_rule_count": int(payload.get("unknown_rule_count") or 0),
        }
        if md_path.exists():
            try:
                metadata["heartbeat_investigation_summary_workspace_relpath"] = (
                    md_path.resolve().relative_to(workspace_root.parent.resolve()).as_posix()
                )
            except Exception:
                metadata["heartbeat_investigation_summary_workspace_relpath"] = ""
        if json_path.exists():
            try:
                metadata["heartbeat_investigation_summary_json_workspace_relpath"] = (
                    json_path.resolve().relative_to(workspace_root.parent.resolve()).as_posix()
                )
            except Exception:
                metadata["heartbeat_investigation_summary_json_workspace_relpath"] = ""
        self._emit_notification(
            kind="heartbeat_investigation_completed",
            title="Heartbeat Investigation Completed",
            message=summary_text or "Simone completed heartbeat investigation.",
            session_id=session_id,
            severity="info",
            requires_action=bool(operator_review_required),
            metadata=metadata,
        )

    def _tutorial_run_rel_path(self, run_dir: Path) -> str:
        try:
            artifacts_root = Path(str(resolve_artifacts_dir())).resolve()
            run_rel = run_dir.resolve().relative_to(artifacts_root).as_posix().strip("/")
            return run_rel
        except Exception:
            return ""

    @staticmethod
    def _tutorial_manifest_probably_code(manifest_payload: dict[str, Any]) -> bool:
        values = [
            manifest_payload.get("title"),
            manifest_payload.get("description"),
            manifest_payload.get("summary"),
            manifest_payload.get("channel"),
            manifest_payload.get("channel_name"),
        ]
        tokens = " ".join(str(value or "") for value in values).strip().lower()
        if not tokens:
            return False
        has_code = any(keyword in tokens for keyword in YOUTUBE_TUTORIAL_CODE_HINT_KEYWORDS)
        has_non_code = any(keyword in tokens for keyword in YOUTUBE_TUTORIAL_NON_CODE_HINT_KEYWORDS)
        return has_code and not (has_non_code and not has_code)

    @staticmethod
    def _tutorial_manifest_explicitly_non_code(manifest_payload: dict[str, Any]) -> bool:
        values = [
            manifest_payload.get("title"),
            manifest_payload.get("description"),
            manifest_payload.get("summary"),
            manifest_payload.get("channel"),
            manifest_payload.get("channel_name"),
        ]
        tokens = " ".join(str(value or "") for value in values).strip().lower()
        if not tokens:
            return False
        has_code = any(keyword in tokens for keyword in YOUTUBE_TUTORIAL_CODE_HINT_KEYWORDS)
        has_non_code = any(keyword in tokens for keyword in YOUTUBE_TUTORIAL_NON_CODE_HINT_KEYWORDS)
        return has_non_code and not has_code

    def _tutorial_key_files_for_notification(
        self,
        *,
        run_dir: Path,
        run_rel_path: str,
        implementation_required: bool = False,
    ) -> list[dict[str, str]]:
        files: list[dict[str, str]] = []
        primary_files = [
            ("README", "README.md"),
            ("Concept", "CONCEPT.md"),
            ("Implementation", "IMPLEMENTATION.md"),
            ("Manifest", "manifest.json"),
        ]
        for label, name in primary_files:
            path = run_dir / name
            if not path.is_file():
                continue
            rel_path = f"{run_rel_path}/{name}" if run_rel_path else ""
            files.append(
                {
                    "label": label,
                    "name": name,
                    "path": str(path),
                    "rel_path": rel_path,
                }
            )
        implementation_dir = run_dir / "implementation"
        if implementation_dir.is_dir():
            if implementation_required:
                implementation_files = sorted(
                    [
                        node
                        for node in implementation_dir.rglob("*")
                        if node.is_file()
                        and node.suffix.lower() in {".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".sh", ".ipynb", ".sql"}
                        and node.name.strip().lower() not in YOUTUBE_TUTORIAL_BOOTSTRAP_SCRIPT_NAMES
                    ]
                )[:8]
            else:
                implementation_files = sorted(
                    [
                        node
                        for node in implementation_dir.rglob("*")
                        if node.is_file()
                        and node.suffix.lower() in {".md", ".txt"}
                        and node.name.strip().lower() not in YOUTUBE_TUTORIAL_BOOTSTRAP_SCRIPT_NAMES
                    ]
                )[:8]
            for node in implementation_files:
                rel_under_run = node.relative_to(run_dir).as_posix()
                rel_path = f"{run_rel_path}/{rel_under_run}" if run_rel_path else ""
                files.append(
                    {
                        "label": (f"Code: {node.name}" if implementation_required else f"Procedure: {node.name}"),
                        "name": node.name,
                        "path": str(node),
                        "rel_path": rel_path,
                    }
                )
        return files

    def _tutorial_repo_bootstrap_script(self) -> str:
        repo_root = self._tutorial_bootstrap_repo_root
        repo_root_quoted = shlex.quote(repo_root)
        return (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n\n"
            "SCRIPT_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
            f"TARGET_ROOT=\"${{1:-{repo_root_quoted}}}\"\n"
            "REPO_NAME=\"${2:-yt_tutorial_impl_$(date +%Y%m%d_%H%M%S)}\"\n"
            "PYTHON_VERSION=\"${3:-}\"\n\n"
            "if ! command -v uv >/dev/null 2>&1; then\n"
            "  echo \"uv is required but not found on PATH.\" >&2\n"
            "  exit 1\n"
            "fi\n\n"
            "mkdir -p \"$TARGET_ROOT\"\n"
            "TARGET_DIR=\"$TARGET_ROOT/$REPO_NAME\"\n"
            "if [[ -e \"$TARGET_DIR\" ]]; then\n"
            "  echo \"Target repo already exists: $TARGET_DIR\" >&2\n"
            "  exit 1\n"
            "fi\n"
            "mkdir -p \"$TARGET_DIR\"\n\n"
            "cd \"$TARGET_DIR\"\n"
            "if [[ -n \"$PYTHON_VERSION\" ]]; then\n"
            "  uv init --python \"$PYTHON_VERSION\"\n"
            "else\n"
            "  uv init\n"
            "fi\n\n"
            "shopt -s dotglob nullglob\n"
            "for item in \"$SCRIPT_DIR\"/*; do\n"
            "  base=\"$(basename \"$item\")\"\n"
            "  if [[ \"$base\" == \"create_new_repo.sh\" || \"$base\" == \"deletethisrepo.sh\" ]]; then\n"
            "    continue\n"
            "  fi\n"
            "  cp -a \"$item\" \"$TARGET_DIR/\"\n"
            "done\n"
            "shopt -u dotglob nullglob\n\n"
            "cat > \"$TARGET_DIR/deletethisrepo.sh\" <<'EOF'\n"
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "THIS_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
            "PARENT_DIR=\"$(dirname \"$THIS_DIR\")\"\n"
            "read -r -p \"Delete repo at $THIS_DIR ? [y/N] \" REPLY\n"
            "if [[ ! \"$REPLY\" =~ ^[Yy]$ ]]; then\n"
            "  echo \"Cancelled\"\n"
            "  exit 0\n"
            "fi\n"
            "cd \"$PARENT_DIR\"\n"
            "rm -rf \"$THIS_DIR\"\n"
            "echo \"Deleted $THIS_DIR\"\n"
            "EOF\n"
            "chmod +x \"$TARGET_DIR/deletethisrepo.sh\"\n\n"
            "cd \"$TARGET_DIR\"\n"
            "if [[ -f \"requirements.txt\" ]]; then\n"
            "  uv add -r requirements.txt\n"
            "else\n"
            "  echo \"requirements.txt not found; skipping uv add -r requirements.txt.\"\n"
            "fi\n\n"
            "uv sync\n\n"
            "echo \"Repo ready: $TARGET_DIR\"\n"
            "echo \"Use 'uv run app.py' (or your entrypoint) to run inside the managed env.\"\n"
            "echo \"Run ./deletethisrepo.sh inside that repo to remove it.\"\n"
        )

    @staticmethod
    def _tutorial_repo_delete_script() -> str:
        return (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n\n"
            "THIS_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
            "PARENT_DIR=\"$(dirname \"$THIS_DIR\")\"\n"
            "read -r -p \"Delete repo at $THIS_DIR ? [y/N] \" REPLY\n"
            "if [[ ! \"$REPLY\" =~ ^[Yy]$ ]]; then\n"
            "  echo \"Cancelled\"\n"
            "  exit 0\n"
            "fi\n"
            "cd \"$PARENT_DIR\"\n"
            "rm -rf \"$THIS_DIR\"\n"
            "echo \"Deleted $THIS_DIR\"\n"
        )

    def _ensure_tutorial_bootstrap_scripts(self, implementation_dir: Path) -> list[str]:
        scripts: list[str] = []
        create_script = implementation_dir / "create_new_repo.sh"
        delete_script = implementation_dir / "deletethisrepo.sh"
        self._write_text_file(create_script, self._tutorial_repo_bootstrap_script())
        self._write_text_file(delete_script, self._tutorial_repo_delete_script())
        try:
            os.chmod(create_script, 0o755)
            os.chmod(delete_script, 0o755)
        except Exception:
            logger.warning("Failed to set executable bit on tutorial bootstrap scripts in %s", implementation_dir)
        scripts.append(str(create_script))
        scripts.append(str(delete_script))
        return scripts

    def _emit_youtube_tutorial_failure_notification(
        self,
        *,
        session_id: str,
        session_key: str,
        hook_name: str,
        expected_video_id: str,
        reason: str,
        started_at_epoch: Optional[float],
        run_id: Optional[str] = None,
        attempt_id: Optional[str] = None,
        attempt_number: Optional[int] = None,
        workspace_dir: Optional[str] = None,
        provider_session_id: Optional[str] = None,
        max_attempts: Optional[int] = None,
    ) -> None:
        metadata: dict[str, Any] = {
            "source": "hooks",
            "hook_name": hook_name,
            "hook_session_key": session_key,
            "video_id": expected_video_id or "",
            "reason": reason,
        }
        metadata = self._merge_workflow_metadata(
            metadata,
            run_id=run_id,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            workspace_dir=workspace_dir,
            provider_session_id=provider_session_id,
            max_attempts=max_attempts,
        )
        tutorial_title = expected_video_id or session_key
        if expected_video_id and started_at_epoch is not None:
            manifest_path = self._find_recent_tutorial_manifest(
                video_id=expected_video_id,
                started_at_epoch=float(started_at_epoch),
            )
            if manifest_path is not None:
                manifest_payload = self._safe_json(manifest_path)
                run_dir = manifest_path.parent
                run_rel_path = self._tutorial_run_rel_path(run_dir)
                key_files = self._tutorial_key_files_for_notification(
                    run_dir=run_dir,
                    run_rel_path=run_rel_path,
                )
                metadata.update(
                    {
                        "tutorial_run_path": run_rel_path,
                        "tutorial_manifest_path": (
                            f"{run_rel_path}/manifest.json" if run_rel_path else ""
                        ),
                        "tutorial_key_files": key_files,
                    }
                )
                tutorial_title = str(manifest_payload.get("title") or tutorial_title or "")
        self._emit_notification(
            kind="youtube_tutorial_failed",
            title="YouTube Tutorial Processing Failed",
            message=(
                f"{tutorial_title}: {reason}"
                f"{f' (video_id: {expected_video_id})' if expected_video_id else ''}"
            ),
            session_id=session_id,
            severity="error",
            requires_action=True,
            metadata=metadata,
        )

    @staticmethod
    def _tutorial_manifest_video_id(manifest_payload: dict[str, Any]) -> str:
        if not isinstance(manifest_payload, dict):
            return ""
        top_level = str(manifest_payload.get("video_id") or "").strip()
        if top_level:
            return top_level
        video_block = manifest_payload.get("video")
        if isinstance(video_block, dict):
            nested = str(video_block.get("video_id") or "").strip()
            if nested:
                return nested
        return ""

    @staticmethod
    def _tutorial_manifest_title(manifest_payload: dict[str, Any], fallback: str) -> str:
        if not isinstance(manifest_payload, dict):
            return fallback
        top_level = str(manifest_payload.get("title") or "").strip()
        if top_level:
            return top_level
        video_block = manifest_payload.get("video")
        if isinstance(video_block, dict):
            nested = str(video_block.get("title") or "").strip()
            if nested:
                return nested
        return fallback

    @staticmethod
    def _tutorial_recovery_metadata(pending_payload: Optional[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(pending_payload, dict):
            return {}
        retry_count = int(pending_payload.get("retry_count") or 0)
        max_retries = int(pending_payload.get("max_retries") or 0)
        if retry_count <= 0:
            return {}
        total_attempts_allowed = max(1, max_retries + 1) if max_retries > 0 else retry_count + 1
        return {
            "recovered_after_retry": True,
            "retry_count": retry_count,
            "max_retries": max_retries,
            "attempt_number": retry_count + 1,
            "total_attempts_allowed": total_attempts_allowed,
        }

    def _emit_youtube_tutorial_ready_notification(
        self,
        *,
        session_id: str,
        session_key: str,
        hook_name: str,
        expected_video_id: str,
        ingest_status: str,
        artifact_validation: dict[str, Any],
        pending_recovery_payload: Optional[dict[str, Any]] = None,
        recovered_from_reason: Optional[str] = None,
        run_id: Optional[str] = None,
        attempt_id: Optional[str] = None,
        attempt_number: Optional[int] = None,
        workspace_dir: Optional[str] = None,
        provider_session_id: Optional[str] = None,
        max_attempts: Optional[int] = None,
    ) -> None:
        tutorial_title = str(artifact_validation.get("title") or expected_video_id or session_key)
        run_rel_path = str(artifact_validation.get("run_rel_path") or "").strip()
        tutorial_key_files = artifact_validation.get("key_files")
        recovery_metadata = self._tutorial_recovery_metadata(pending_recovery_payload)
        if not recovery_metadata and attempt_number and attempt_number > 1:
            total_attempts_allowed = max(int(max_attempts or 0), int(attempt_number))
            recovery_metadata = {
                "recovered_after_retry": True,
                "retry_count": max(0, int(attempt_number) - 1),
                "attempt_number": int(attempt_number),
                "total_attempts_allowed": total_attempts_allowed or int(attempt_number),
            }
        if recovery_metadata:
            attempt_number = int(recovery_metadata.get("attempt_number") or 0)
            total_attempts_allowed = int(recovery_metadata.get("total_attempts_allowed") or 0)
            message = (
                f"{tutorial_title} artifacts are ready after automatic recovery on "
                f"attempt {attempt_number}/{total_attempts_allowed}."
                f"{f' (video_id: {expected_video_id})' if expected_video_id else ''}"
            )
        elif recovered_from_reason:
            message = (
                f"{tutorial_title} artifacts are ready and the output package validated "
                f"successfully after an earlier dispatch hiccup."
                f"{f' (video_id: {expected_video_id})' if expected_video_id else ''}"
            )
        else:
            message = (
                f"{tutorial_title} artifacts are ready for review."
                f"{f' (video_id: {expected_video_id})' if expected_video_id else ''}"
            )

        metadata: dict[str, Any] = {
            "source": "hooks",
            "hook_name": hook_name,
            "hook_session_key": session_key,
            "video_id": expected_video_id,
            "tutorial_status": str(artifact_validation.get("status") or "full"),
            "tutorial_run_path": run_rel_path,
            "tutorial_manifest_path": (
                f"{run_rel_path}/manifest.json" if run_rel_path else ""
            ),
            "tutorial_key_files": tutorial_key_files
            if isinstance(tutorial_key_files, list)
            else [],
            "ingest_status": ingest_status,
        }
        metadata = self._merge_workflow_metadata(
            metadata,
            run_id=run_id,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            workspace_dir=workspace_dir,
            provider_session_id=provider_session_id,
            max_attempts=max_attempts,
        )
        if recovery_metadata:
            metadata.update(recovery_metadata)
        if recovered_from_reason:
            metadata["dispatch_issue_resolved"] = True
            metadata["recovered_from_reason"] = str(recovered_from_reason)

        self._emit_notification(
            kind="youtube_tutorial_ready",
            title="YouTube Tutorial Artifacts Ready",
            message=message,
            session_id=session_id,
            severity="success",
            requires_action=True,
            metadata=metadata,
        )

    @staticmethod
    def _format_ingest_failure_reason(
        *,
        error: str,
        failure_class: str,
        attempts: int,
        max_attempts: int,
    ) -> str:
        err = str(error or "local_ingest_failed").strip()
        cls = str(failure_class or "unknown").strip().lower()
        if cls == "proxy_quota_or_billing":
            return (
                f"local ingest failed after {int(attempts)}/{int(max_attempts)} attempts "
                f"(error={err}, failure_class={cls}). "
                "PROXY ALERT: Webshare quota/billing appears exhausted; verify account credits/bandwidth and retry."
            )
        if cls == "proxy_pool_unallocated":
            return (
                f"local ingest failed after {int(attempts)}/{int(max_attempts)} attempts "
                f"(error={err}, failure_class={cls}). "
                "PROXY ALERT: Webshare reported no allocated proxies for the configured endpoint/username. "
                "Refresh your Webshare proxy list/rotation username and update Infisical secrets."
            )
        if cls == "proxy_auth_failed":
            return (
                f"local ingest failed after {int(attempts)}/{int(max_attempts)} attempts "
                f"(error={err}, failure_class={cls}). "
                "PROXY ALERT: Webshare credentials appear invalid; verify PROXY_USERNAME/PROXY_PASSWORD secrets."
            )
        if cls == "proxy_connect_failed":
            return (
                f"local ingest failed after {int(attempts)}/{int(max_attempts)} attempts "
                f"(error={err}, failure_class={cls}). "
                "PROXY ALERT: Residential proxy CONNECT failed; verify Webshare host/port overrides, "
                "proxy credentials in Infisical, and upstream proxy availability."
            )
        if cls == "proxy_not_configured":
            return (
                f"YouTube ingest BLOCKED — residential proxy is NOT CONFIGURED "
                f"(error={err}, failure_class={cls}). "
                "PROXY ALERT: PROXY_USERNAME and PROXY_PASSWORD env vars are missing. "
                "Without a residential proxy, YouTube WILL ban this server's datacenter IP. "
                "Add Webshare credentials to Infisical and redeploy."
            )
        if cls == "video_unavailable":
            return (
                f"video is unavailable (error={err}, failure_class={cls}). "
                "Likely deleted/private/region-restricted; skipping retries."
            )
        if cls == "transcript_unavailable":
            return (
                f"transcript is unavailable (error={err}, failure_class={cls}). "
                "Captions are missing/disabled for this video; skipping retries."
            )
        return (
            f"local ingest failed after {int(attempts)}/{int(max_attempts)} attempts "
            f"(error={err}, failure_class={cls})"
        )

    async def handle_request(self, request: Request, subpath: str) -> Response:
        if not self.config.enabled:
            return Response("Hooks disabled", status_code=404)
        
        # Read body
        try:
            body_bytes = await request.body()
            logger.info("Hook ingress received path=%s bytes=%d", subpath, len(body_bytes))
            if len(body_bytes) > self.config.max_body_bytes:
                logger.warning("Hook ingress rejected path=%s reason=payload_too_large", subpath)
                return Response("Payload too large", status_code=413)
            
            payload = {}
            if body_bytes:
                try:
                    payload = json.loads(body_bytes)
                except json.JSONDecodeError:
                    logger.warning("Hook ingress rejected path=%s reason=invalid_json", subpath)
                    return Response("Invalid JSON", status_code=400)
        except Exception as e:
            logger.exception("Hook ingress rejected path=%s reason=body_read_error", subpath)
            return Response(f"Error reading body: {str(e)}", status_code=400)

        # Context for matching/templating
        headers = {k.lower(): v for k, v in request.headers.items()}
        context = {
            "payload": payload,
            "headers": headers,
            "path": subpath,
            "query": dict(request.query_params),
            "raw_body": body_bytes,
            "raw_body_text": body_bytes.decode("utf-8", errors="replace"),
        }

        # Match and dispatch
        try:
            matched = False
            auth_failed = False
            for mapping in self.config.mappings:
                if not self._mapping_matches(mapping, context):
                    continue

                matched = True
                mapping_id = mapping.id or "<unlabeled>"
                if not self._authenticate_request(mapping, request, context):
                    auth_failed = True
                    logger.warning(
                        "Hook ingress auth failed path=%s mapping=%s strategy=%s",
                        subpath,
                        mapping_id,
                        (mapping.auth.strategy if mapping.auth else "token"),
                    )
                    continue

                auth_outcome = context.get("_hook_auth_outcome")
                if isinstance(auth_outcome, dict) and auth_outcome.get("deduped"):
                    logger.info(
                        "Hook ingress deduped path=%s mapping=%s webhook_id=%s",
                        subpath,
                        mapping_id,
                        auth_outcome.get("webhook_id"),
                    )
                    return Response(
                        json.dumps({"ok": True, "deduped": True}),
                        media_type="application/json",
                        status_code=200,
                    )

                action = await self._build_action(mapping, context)
                if action is None:
                    logger.info("Hook ingress skipped path=%s mapping=%s", subpath, mapping_id)
                    return Response(
                        json.dumps({"ok": True, "skipped": True}),
                        media_type="application/json",
                        status_code=200,
                    )

                asyncio.create_task(self._dispatch_action(action))

                logger.info(
                    "Hook ingress accepted path=%s mapping=%s action=%s",
                    subpath,
                    mapping_id,
                    action.kind,
                )
                return Response(
                    json.dumps({"ok": True, "action": action.kind}),
                    media_type="application/json",
                    status_code=200,
                )
            
            if matched and auth_failed:
                logger.warning("Hook ingress unauthorized path=%s", subpath)
                return Response("Unauthorized", status_code=401)
            logger.info("Hook ingress no_match path=%s", subpath)
            return Response("No matching hook found", status_code=404)
        except Exception as e:
            logger.exception("Error processing hook")
            return Response(json.dumps({"ok": False, "error": str(e)}), status_code=500, media_type="application/json")

    async def dispatch_internal_payload(
        self,
        *,
        subpath: str,
        payload: dict[str, Any],
        headers: Optional[dict[str, str]] = None,
    ) -> tuple[bool, str]:
        """
        Dispatch an internal payload through hook mappings without external auth checks.

        Intended for trusted in-process producers (for example CSI ingest path)
        that need to reuse existing hook transforms and action routing.
        """
        if not self.config.enabled:
            return False, "hooks_disabled"

        effective_headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        body_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        context = {
            "payload": payload,
            "headers": effective_headers,
            "path": subpath,
            "query": {},
            "raw_body": body_bytes,
            "raw_body_text": body_bytes.decode("utf-8", errors="replace"),
        }

        for mapping in self.config.mappings:
            if not self._mapping_matches(mapping, context):
                continue
            action = await self._build_action(mapping, context)
            if action is None:
                return True, "skipped"
            if str(action.kind or "").strip().lower() == "agent":
                result = await self.dispatch_internal_action_background_with_admission(
                    action.model_dump()
                )
                if str(result.get("decision") or "").strip().lower() == "failed":
                    return False, str(result.get("reason") or "dispatch_failed")
                return True, action.kind
            asyncio.create_task(self._dispatch_action(action))
            return True, action.kind
        return False, "no_match"

    async def dispatch_internal_action(self, action_payload: dict[str, Any]) -> tuple[bool, str]:
        """
        Dispatch a trusted in-process hook action directly.

        This bypasses mapping resolution and auth because the caller is already
        trusted (for example UA's CSI ingest route).
        """
        if not self.config.enabled:
            return False, "hooks_disabled"
        try:
            action = HookAction.model_validate(action_payload)
        except Exception:
            return False, "invalid_action"
        if str(action.kind or "").strip().lower() == "agent":
            result = await self.dispatch_internal_action_background_with_admission(
                action.model_dump()
            )
            if str(result.get("decision") or "").strip().lower() == "failed":
                return False, str(result.get("reason") or "dispatch_failed")
            return True, action.kind
        asyncio.create_task(self._dispatch_action(action))
        return True, action.kind

    async def dispatch_internal_action_with_admission(
        self,
        action_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Dispatch a trusted in-process hook action and return admission/execution result."""
        if not self.config.enabled:
            return {"decision": "failed", "reason": "hooks_disabled", "error": "hooks_disabled"}
        try:
            action = HookAction.model_validate(action_payload)
        except Exception:
            return {"decision": "failed", "reason": "invalid_action", "error": "invalid_action"}
        return await self._dispatch_action(action)

    async def dispatch_internal_action_background_with_admission(
        self,
        action_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Admit a trusted in-process hook action and dispatch it in the background."""
        if not self.config.enabled:
            return {"decision": "failed", "reason": "hooks_disabled", "error": "hooks_disabled"}
        try:
            action = HookAction.model_validate(action_payload)
        except Exception:
            return {"decision": "failed", "reason": "invalid_action", "error": "invalid_action"}

        if str(action.kind or "").strip().lower() != "agent":
            return await self._dispatch_action(action)

        session_key = str(action.session_key or "").strip()
        if not session_key:
            return await self._dispatch_action(action)

        session_id = self._session_id_from_key(session_key)
        expected_video_id = self._extract_action_field(action.message or "", "video_id")
        workflow_profile = self._workflow_profile_for_action(
            action=action,
            session_key=session_key,
            session_id=session_id,
            expected_video_id=expected_video_id,
        )
        if workflow_profile is None:
            asyncio.create_task(self._dispatch_action(action))
            return {
                "decision": "accepted",
                "reason": "background_dispatched",
                "session_id": session_id,
            }

        admission_result = await self._admit_workflow_with_retry(
            action=action,
            session_key=session_key,
            workflow_profile=workflow_profile,
        )
        if str(admission_result.get("decision") or "").strip().lower() == "failed":
            return {
                "decision": "failed",
                "reason": "runtime_db_locked",
                "retryable": True,
                "session_id": session_id,
            }
        workflow_decision = admission_result["workflow_decision"]
        workflow_run_id = str(admission_result.get("run_id") or "").strip() or None
        workflow_attempt_id = str(admission_result.get("attempt_id") or "").strip() or None
        workflow_attempt_number = int(admission_result.get("attempt_number") or 0) or None
        workflow_workspace_dir = str(admission_result.get("workspace_dir") or "").strip()

        if workflow_decision.action in {"attach_to_existing_run", "defer", "skip_duplicate"}:
            return {
                "decision": "skipped",
                "reason": workflow_decision.reason,
                "session_id": session_id,
                "run_id": workflow_run_id,
                "attempt_id": workflow_attempt_id,
                "attempt_number": workflow_attempt_number,
                "workspace_dir": workflow_workspace_dir,
            }
        if workflow_decision.action == "escalate_review":
            return {
                "decision": "failed",
                "reason": workflow_decision.reason,
                "session_id": session_id,
                "run_id": workflow_run_id,
                "attempt_id": workflow_attempt_id,
                "attempt_number": workflow_attempt_number,
                "workspace_dir": workflow_workspace_dir,
            }

        asyncio.create_task(
            self._dispatch_action(
                action,
                workflow_run_id=workflow_run_id,
                workflow_attempt_id=workflow_attempt_id,
                workflow_workspace_dir=workflow_workspace_dir,
                skip_workflow_admission=True,
            )
        )
        return {
            "decision": "accepted",
            "reason": "dispatched",
            "session_id": session_id,
            "run_id": workflow_run_id,
            "attempt_id": workflow_attempt_id,
            "attempt_number": workflow_attempt_number,
            "workspace_dir": workflow_workspace_dir,
        }

    def _extract_action_field(self, message: str, key: str) -> str:
        if not message:
            return ""
        prefix = f"{key}:"
        for line in message.splitlines():
            stripped = line.strip()
            if not stripped.lower().startswith(prefix.lower()):
                continue
            return stripped.split(":", 1)[1].strip()
        return ""

    @staticmethod
    def _parse_bool_text(raw: str, *, default: bool = False) -> bool:
        value = str(raw or "").strip().lower()
        if not value:
            return bool(default)
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
        return bool(default)

    def _allow_degraded_transcript_only(self, action: HookAction) -> bool:
        raw = self._extract_action_field(action.message or "", "allow_degraded_transcript_only")
        if not raw:
            return False
        return self._parse_bool_text(raw, default=False)

    def _should_fail_open_ingest(self, *, action: HookAction, failure_class: str) -> bool:
        if self._youtube_ingest_fail_open:
            return True
        normalized = str(failure_class or "").strip().lower()
        if normalized not in YOUTUBE_INGEST_DEGRADABLE_FAILURE_CLASSES:
            return False
        return self._allow_degraded_transcript_only(action)


    def _safe_int_env(self, name: str, default: int) -> int:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return int(default)
        try:
            return int(raw)
        except Exception:
            return int(default)

    def _safe_float_env(self, name: str, default: float) -> float:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            return float(default)
        try:
            return float(raw)
        except Exception:
            return float(default)

    def _safe_bool_env(self, name: str, default: bool) -> bool:
        raw = (os.getenv(name) or "").strip().lower()
        if not raw:
            return bool(default)
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        return bool(default)

    def _optional_int_env(
        self,
        name: str,
        *,
        default: int,
        minimum: int = 0,
    ) -> Optional[int]:
        raw = (os.getenv(name) or "").strip()
        value = default
        if raw:
            try:
                value = int(raw)
            except Exception:
                value = default
        if value <= 0:
            return None
        if minimum > 0:
            value = max(minimum, value)
        return value

    @staticmethod
    def _safe_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _parse_endpoint_list(self, raw: str, *, fallback: str = "") -> list[str]:
        endpoints: list[str] = []
        for item in (raw or "").split(","):
            value = item.strip()
            if not value:
                continue
            if not value.startswith("http://") and not value.startswith("https://"):
                continue
            endpoints.append(value)
        if not endpoints and fallback.strip():
            endpoints.append(fallback.strip())
        deduped: list[str] = []
        for endpoint in endpoints:
            if endpoint not in deduped:
                deduped.append(endpoint)
        return deduped

    def _default_tutorial_bootstrap_repo_root(self) -> str:
        if self._deployment_profile == "vps":
            return DEFAULT_TUTORIAL_BOOTSTRAP_REPO_ROOT_VPS
        return DEFAULT_TUTORIAL_BOOTSTRAP_REPO_ROOT_LOCAL

    @staticmethod
    def _is_loopback_endpoint(endpoint: str) -> bool:
        host = (urlparse(str(endpoint or "")).hostname or "").strip().lower()
        return host in {"127.0.0.1", "localhost"}

    def _normalize_youtube_ingest_urls(self, endpoints: list[str]) -> list[str]:
        deduped: list[str] = []
        for endpoint in endpoints:
            value = str(endpoint or "").strip()
            if value and value not in deduped:
                deduped.append(value)
        if not deduped:
            return []
        loopback = [endpoint for endpoint in deduped if self._is_loopback_endpoint(endpoint)]
        non_loopback = [endpoint for endpoint in deduped if endpoint not in loopback]
        if self._deployment_profile == "vps":
            return loopback or deduped
        if self._deployment_profile == "local_workstation":
            return loopback + non_loopback if loopback else deduped
        return deduped

    def _is_youtube_local_ingest_target(self, action: HookAction) -> bool:
        return (
            (action.kind or "").strip().lower() == "agent"
            and _is_youtube_agent_route(action.to)
            and bool((action.message or "").strip())
            and self._youtube_ingest_mode == "local_worker"
        )

    def _append_message_lines(self, message: str, extra_lines: list[str]) -> str:
        base = (message or "").strip()
        cleaned_lines = [line for line in extra_lines if isinstance(line, str) and line.strip()]
        if not cleaned_lines:
            return base
        if not base:
            return "\n".join(cleaned_lines)
        return base + "\n\n" + "\n".join(cleaned_lines)

    def _youtube_ingest_video_key(self, video_url: str, video_id: str) -> str:
        normalized_url, normalized_id = normalize_video_target(video_url, video_id)
        if normalized_id:
            return normalized_id
        if normalized_url:
            return f"url:{normalized_url.strip().lower()}"
        seed = f"{video_url}|{video_id}".encode("utf-8", errors="replace")
        return f"unknown:{hashlib.sha256(seed).hexdigest()[:16]}"

    def _cleanup_youtube_ingest_state(self, now_epoch: float) -> None:
        expired_inflight = [key for key, until_epoch in self._youtube_ingest_inflight.items() if until_epoch <= now_epoch]
        for key in expired_inflight:
            self._youtube_ingest_inflight.pop(key, None)
            self._youtube_ingest_inflight_owners.pop(key, None)

        expired_cooldowns = []
        for key, entry in self._youtube_ingest_cooldowns.items():
            until_epoch = float(entry.get("until_epoch") or 0.0)
            if until_epoch <= now_epoch:
                expired_cooldowns.append(key)
        for key in expired_cooldowns:
            self._youtube_ingest_cooldowns.pop(key, None)

    def _youtube_ingest_retry_delay(self, attempt_index: int) -> float:
        base_delay = self._youtube_ingest_retry_delay_seconds
        if base_delay <= 0:
            return 0.0
        factor = float(2 ** max(0, attempt_index))
        delay = min(self._youtube_ingest_retry_max_delay_seconds, base_delay * factor)
        if self._youtube_ingest_retry_jitter_seconds > 0:
            delay += random.uniform(0.0, self._youtube_ingest_retry_jitter_seconds)
        return max(0.0, delay)

    def _set_youtube_ingest_cooldown(
        self,
        *,
        video_key: str,
        failure_class: str,
        error: str,
        now_epoch: float,
    ) -> None:
        if self._youtube_ingest_cooldown_seconds <= 0:
            return
        cooldown_seconds = float(self._youtube_ingest_cooldown_seconds)
        if failure_class == "request_blocked":
            cooldown_seconds = max(cooldown_seconds, cooldown_seconds * 2.0)
        if cooldown_seconds <= 0:
            return
        self._youtube_ingest_cooldowns[video_key] = {
            "until_epoch": now_epoch + cooldown_seconds,
            "failure_class": failure_class,
            "error": error,
        }

    async def _call_local_youtube_ingest_worker(
        self,
        *,
        video_url: str,
        video_id: str,
        session_id: str,
        min_chars: int,
    ) -> dict[str, Any]:
        if not self._youtube_ingest_urls:
            return {"ok": False, "status": "failed", "error": "missing_ingest_url"}

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._youtube_ingest_token:
            headers["Authorization"] = f"Bearer {self._youtube_ingest_token}"

        payload = {
            "video_url": video_url,
            "video_id": video_id,
            "language": "en",
            "timeout_seconds": self._youtube_ingest_timeout_seconds,
            "request_id": session_id,
            "min_chars": max(20, min(int(min_chars or 0), 5000)),
        }

        timeout = httpx.Timeout(timeout=float(max(5, self._youtube_ingest_timeout_seconds + 10)))
        attempts: list[dict[str, Any]] = []
        last_result: dict[str, Any] = {"ok": False, "status": "failed", "error": "ingest_request_error"}

        for endpoint in self._youtube_ingest_urls:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(endpoint, headers=headers, json=payload)
                if 200 <= resp.status_code < 300:
                    try:
                        data = resp.json()
                    except Exception:
                        data = {"ok": False, "status": "failed", "error": "invalid_json_response"}
                    if isinstance(data, dict):
                        data.setdefault("http_status", resp.status_code)
                        data["ingest_endpoint"] = endpoint
                        attempts.append(
                            {
                                "endpoint": endpoint,
                                "ok": bool(data.get("ok")),
                                "http_status": int(resp.status_code),
                                "error": str(data.get("error") or ""),
                                "failure_class": str(data.get("failure_class") or ""),
                            }
                        )
                        if bool(data.get("ok")):
                            data["ingest_endpoint_attempts"] = attempts
                            return data
                        last_result = data
                        continue
                    last_result = {"ok": False, "status": "failed", "error": "invalid_response_shape"}
                    attempts.append(
                        {
                            "endpoint": endpoint,
                            "ok": False,
                            "http_status": int(resp.status_code),
                            "error": "invalid_response_shape",
                            "failure_class": "",
                        }
                    )
                    continue

                last_result = {
                    "ok": False,
                    "status": "failed",
                    "error": "ingest_http_error",
                    "http_status": resp.status_code,
                    "detail": (resp.text or "")[:2000],
                }
                attempts.append(
                    {
                        "endpoint": endpoint,
                        "ok": False,
                        "http_status": int(resp.status_code),
                        "error": "ingest_http_error",
                        "failure_class": "",
                    }
                )
            except Exception as exc:
                last_result = {
                    "ok": False,
                    "status": "failed",
                    "error": "ingest_request_error",
                    "detail": str(exc),
                }
                attempts.append(
                    {
                        "endpoint": endpoint,
                        "ok": False,
                        "http_status": 0,
                        "error": "ingest_request_error",
                        "failure_class": "",
                    }
                )

        out = dict(last_result)
        out["ingest_endpoint_attempts"] = attempts
        return out

    def _write_text_file(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _sync_ready_marker_path(self, workspace_root: Path) -> Path:
        marker_name = self._sync_ready_marker_filename.strip() or DEFAULT_SYNC_READY_MARKER_FILENAME
        return workspace_root / marker_name

    def _find_recent_tutorial_manifest(
        self,
        *,
        video_id: str,
        started_at_epoch: float,
    ) -> Optional[Path]:
        artifacts_root = Path(str(resolve_artifacts_dir())).resolve()

        best_path: Optional[Path] = None
        best_mtime = 0.0
        threshold = float(started_at_epoch or 0.0) - 60.0

        tutorials_root = artifacts_root / YOUTUBE_TUTORIAL_ARTIFACT_DIR_CANONICAL
        if not tutorials_root.exists():
            return None
        for manifest_path in tutorials_root.rglob("manifest.json"):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if self._tutorial_manifest_video_id(payload) != video_id:
                continue
            try:
                manifest_mtime = manifest_path.stat().st_mtime
            except Exception:
                continue
            if manifest_mtime < threshold:
                continue
            if manifest_mtime >= best_mtime:
                best_mtime = manifest_mtime
                best_path = manifest_path
        return best_path

    def _validate_youtube_tutorial_artifacts(
        self,
        *,
        video_id: str,
        started_at_epoch: float,
    ) -> dict[str, Any]:
        manifest_path = self._find_recent_tutorial_manifest(
            video_id=video_id,
            started_at_epoch=started_at_epoch,
        )
        if manifest_path is None:
            raise RuntimeError("youtube_artifacts_missing_manifest")

        run_dir = manifest_path.parent
        manifest_payload = self._safe_json(manifest_path)
        learning_mode = str(manifest_payload.get("learning_mode") or "").strip().lower()
        mode = str(manifest_payload.get("mode") or "").strip().lower()
        artifacts = manifest_payload.get("artifacts") if isinstance(manifest_payload, dict) else None
        artifacts_map = artifacts if isinstance(artifacts, dict) else {}
        implementation_dir_hint = str(artifacts_map.get("implementation_dir") or "").strip()
        hinted_dir = run_dir / implementation_dir_hint if implementation_dir_hint else run_dir / "implementation"
        has_substantive_code = False
        if hinted_dir.exists() and hinted_dir.is_dir():
            has_substantive_code = any(
                node.is_file()
                and node.name.strip().lower() not in YOUTUBE_TUTORIAL_BOOTSTRAP_SCRIPT_NAMES
                and node.suffix.lower()
                in {
                    ".py",
                    ".ts",
                    ".tsx",
                    ".js",
                    ".jsx",
                    ".sh",
                    ".ipynb",
                    ".gs",
                    ".html",
                    ".css",
                    ".sql",
                    ".java",
                    ".go",
                    ".rs",
                }
                for node in hinted_dir.rglob("*")
            )
        manifest_flag = manifest_payload.get("implementation_required")
        if isinstance(manifest_flag, bool):
            if not manifest_flag:
                implementation_required = False
            elif has_substantive_code:
                implementation_required = True
            else:
                implementation_required = not self._tutorial_manifest_explicitly_non_code(
                    manifest_payload
                )
        elif learning_mode in {"concept_only"} or mode in {"explainer_only"}:
            implementation_required = False
        elif learning_mode in {"concept_plus_implementation", "implementation", "code_only"} or mode in {
            "explainer_plus_code",
            "implementation",
            "code_only",
        }:
            implementation_required = has_substantive_code or not self._tutorial_manifest_explicitly_non_code(
                manifest_payload
            )
        else:
            implementation_required = has_substantive_code

        missing: list[str] = []
        required_files = ["README.md", "CONCEPT.md"]
        if implementation_required:
            required_files.append("IMPLEMENTATION.md")
        for required_name in required_files:
            if not (run_dir / required_name).is_file():
                missing.append(required_name)
        if implementation_required:
            implementation_dir = run_dir / "implementation"
            if not implementation_dir.is_dir():
                missing.append("implementation/")
            else:
                has_impl_file = any(
                    node.is_file() and node.name.strip().lower() not in YOUTUBE_TUTORIAL_BOOTSTRAP_SCRIPT_NAMES
                    for node in implementation_dir.rglob("*")
                )
                if not has_impl_file:
                    missing.append("implementation/*")
        if missing:
            raise RuntimeError(f"youtube_artifacts_incomplete:{','.join(missing)}")

        bootstrap_scripts: list[str] = []
        implementation_dir = run_dir / "implementation"
        if implementation_dir.is_dir():
            has_impl_file = any(
                node.is_file() and node.name.strip().lower() not in YOUTUBE_TUTORIAL_BOOTSTRAP_SCRIPT_NAMES
                for node in implementation_dir.rglob("*")
            )
            if implementation_required and has_impl_file:
                bootstrap_scripts = self._ensure_tutorial_bootstrap_scripts(implementation_dir)

        run_rel_path = self._tutorial_run_rel_path(run_dir)

        return {
            "video_id": video_id,
            "run_dir": str(run_dir),
            "manifest_path": str(manifest_path),
            "run_rel_path": run_rel_path,
            "title": self._tutorial_manifest_title(manifest_payload, run_dir.name),
            "status": str(manifest_payload.get("status") or "full"),
            "implementation_required": implementation_required,
            "bootstrap_scripts": bootstrap_scripts,
            "key_files": self._tutorial_key_files_for_notification(
                run_dir=run_dir,
                run_rel_path=run_rel_path,
                implementation_required=implementation_required,
            ),
        }

    def _write_sync_ready_marker(
        self,
        *,
        session_id: str,
        workspace_root: Path,
        state: str,
        ready: bool,
        hook_name: str,
        run_source: str,
        started_at_epoch: Optional[float] = None,
        completed_at_epoch: Optional[float] = None,
        error: Optional[str] = None,
        execution_summary: Optional[dict[str, Any]] = None,
    ) -> None:
        if not self._sync_ready_marker_enabled:
            return

        now_epoch = time.time()
        payload: dict[str, Any] = {
            "version": SYNC_READY_MARKER_VERSION,
            "session_id": session_id,
            "state": str(state or "").strip().lower(),
            "ready": bool(ready),
            "hook_name": str(hook_name or "Hook"),
            "run_source": str(run_source or "webhook"),
            "updated_at_epoch": now_epoch,
        }
        if started_at_epoch is not None:
            payload["started_at_epoch"] = float(started_at_epoch)
        if completed_at_epoch is not None:
            payload["completed_at_epoch"] = float(completed_at_epoch)
        if error:
            payload["error"] = str(error)
        if execution_summary:
            payload["execution_summary"] = execution_summary

        marker_path = self._sync_ready_marker_path(workspace_root)
        try:
            self._write_text_file(marker_path, json.dumps(payload, indent=2))
        except Exception as exc:
            logger.warning(
                "Failed writing sync ready marker session_id=%s path=%s err=%s",
                session_id,
                marker_path,
                exc,
            )

    async def _prepare_local_youtube_ingest(
        self,
        *,
        action: HookAction,
        session_id: str,
        session_workspace: str,
    ) -> tuple[HookAction, dict[str, Any], bool]:
        if not self._is_youtube_local_ingest_target(action):
            return action, {}, False

        video_url = self._extract_action_field(action.message or "", "video_url")
        video_id = self._extract_action_field(action.message or "", "video_id")
        if not video_url:
            return action, {"hook_youtube_ingest_status": "skipped_no_video_url"}, False

        video_key = self._youtube_ingest_video_key(video_url, video_id)
        now = time.time()
        self._cleanup_youtube_ingest_state(now)

        errors: list[dict[str, Any]] = []
        ingest_result: dict[str, Any] = {}
        cooldown_entry = self._youtube_ingest_cooldowns.get(video_key)
        cooldown_until = float(cooldown_entry.get("until_epoch") or 0.0) if isinstance(cooldown_entry, dict) else 0.0
        if cooldown_entry and cooldown_until > now:
            ingest_result = {
                "ok": False,
                "status": "failed",
                "error": "ingest_cooldown_active",
                "failure_class": str(cooldown_entry.get("failure_class") or "cooldown_active"),
                "detail": f"cooldown_active_seconds={int(cooldown_until - now)}",
                "video_key": video_key,
            }
        elif self._youtube_ingest_inflight.get(video_key, 0.0) > now:
            duplicate_context = self._current_inflight_ingest_context(video_key)
            ingest_result = {
                "ok": False,
                "status": "failed",
                "error": "ingest_inflight_deduped",
                "failure_class": "inflight_duplicate",
                "detail": f"video_key={video_key}",
                "video_key": video_key,
            }
            if duplicate_context:
                ingest_result.update(duplicate_context)
        else:
            self._youtube_ingest_inflight[video_key] = now + float(self._youtube_ingest_inflight_ttl_seconds)
            self._youtube_ingest_inflight_owners[video_key] = {
                "session_id": session_id,
                "workspace_root": str(Path(session_workspace).resolve()),
            }
            try:
                for attempt_index in range(self._youtube_ingest_retries):
                    ingest_result = await self._call_local_youtube_ingest_worker(
                        video_url=video_url,
                        video_id=video_id,
                        session_id=session_id,
                        min_chars=self._youtube_ingest_min_chars,
                    )
                    if ingest_result.get("ok") and str(ingest_result.get("status") or "").lower() == "succeeded":
                        self._youtube_ingest_cooldowns.pop(video_key, None)
                        break
                    errors.append(
                        {
                            "attempt": attempt_index + 1,
                            "error": str(ingest_result.get("error") or "unknown"),
                            "failure_class": str(ingest_result.get("failure_class") or ""),
                            "detail": str(ingest_result.get("detail") or "")[:2000],
                        }
                    )
                    failure_class = str(ingest_result.get("failure_class") or "").strip().lower()
                    if failure_class in YOUTUBE_INGEST_NON_RETRYABLE_FAILURE_CLASSES:
                        break
                    if attempt_index < self._youtube_ingest_retries - 1:
                        delay_seconds = self._youtube_ingest_retry_delay(attempt_index)
                        if delay_seconds > 0:
                            await asyncio.sleep(delay_seconds)
            finally:
                self._youtube_ingest_inflight.pop(video_key, None)
                self._youtube_ingest_inflight_owners.pop(video_key, None)

        if not (ingest_result.get("ok") and str(ingest_result.get("status") or "").lower() == "succeeded"):
            failure_class = str(ingest_result.get("failure_class") or "").strip().lower()
            if failure_class in {"request_blocked", "api_unavailable", *YOUTUBE_PROXY_ALERT_FAILURE_CLASSES}:
                self._set_youtube_ingest_cooldown(
                    video_key=video_key,
                    failure_class=failure_class,
                    error=str(ingest_result.get("error") or "local_ingest_failed"),
                    now_epoch=time.time(),
                )

        workspace_root = Path(session_workspace).resolve()
        ingestion_dir = workspace_root / "ingestion"
        meta_path = ingestion_dir / "youtube_local_ingest_result.json"
        terminal_result_path = workspace_root / "local_ingest_result.json"

        if ingest_result.get("ok") and str(ingest_result.get("status") or "").lower() == "succeeded":
            transcript_text = str(ingest_result.get("transcript_text") or "")
            transcript_path = ingestion_dir / "youtube_transcript.local.txt"
            try:
                self._write_text_file(transcript_path, transcript_text)
                self._write_text_file(meta_path, json.dumps(ingest_result, indent=2))
            except Exception as exc:
                logger.warning("Failed persisting local ingest transcript session_id=%s err=%s", session_id, exc)

            result_lines = [
                "local_youtube_ingest_mode: local_worker",
                "local_youtube_ingest_status: succeeded",
                f"local_youtube_ingest_source: {str(ingest_result.get('source') or 'unknown')}",
                f"local_youtube_ingest_endpoint: {str(ingest_result.get('ingest_endpoint') or '')}",
                f"local_youtube_ingest_transcript_chars: {int(ingest_result.get('transcript_chars') or 0)}",
                f"local_youtube_ingest_transcript_file: {transcript_path}",
                f"local_youtube_ingest_metadata_file: {meta_path}",
                f"local_youtube_ingest_metadata_status: {str(ingest_result.get('metadata_status') or '')}",
                f"local_youtube_ingest_metadata_source: {str(ingest_result.get('metadata_source') or '')}",
                "Use the local_youtube_ingest_transcript_file as primary transcript input for this run.",
            ]
            action = action.model_copy(
                update={"message": self._append_message_lines(action.message or "", result_lines)}
            )
            metadata = {
                "hook_youtube_ingest_mode": "local_worker",
                "hook_youtube_ingest_status": "succeeded",
                "hook_youtube_ingest_source": str(ingest_result.get("source") or "unknown"),
                "hook_youtube_ingest_endpoint": str(ingest_result.get("ingest_endpoint") or ""),
                "hook_youtube_ingest_transcript_file": str(transcript_path),
                "hook_youtube_ingest_metadata_file": str(meta_path),
                "hook_youtube_ingest_metadata_status": str(ingest_result.get("metadata_status") or ""),
                "hook_youtube_ingest_metadata_source": str(ingest_result.get("metadata_source") or ""),
                "hook_youtube_ingest_metadata_error": str(ingest_result.get("metadata_error") or ""),
                "hook_youtube_ingest_metadata_failure_class": str(ingest_result.get("metadata_failure_class") or ""),
                "hook_youtube_ingest_video_key": video_key,
            }
            return action, metadata, False

        pending_payload = {
            "status": "failed_local_ingest",
            "session_id": session_id,
            "video_url": video_url,
            "video_id": video_id,
            "video_key": video_key,
            "ingest_url": self._youtube_ingest_urls[0] if self._youtube_ingest_urls else "",
            "ingest_urls": list(self._youtube_ingest_urls),
            "min_chars": int(self._youtube_ingest_min_chars),
            "attempts": errors,
            "attempt_results": errors,
            "attempt_count": len(errors),
            "max_attempts": int(self._youtube_ingest_retries),
            "last_result": ingest_result,
            "created_at_epoch": time.time(),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        failed_ingest_result_path = ingestion_dir / "youtube_local_ingest_result.json"
        pending_path = workspace_root / "pending_local_ingest.json"
        attempts_used = len(errors)
        max_attempts = int(self._youtube_ingest_retries)
        failure_error = str(ingest_result.get("error") or "local_ingest_failed")
        failure_class = str(ingest_result.get("failure_class") or "")
        duplicate_root_reason = str(ingest_result.get("root_reason") or "").strip()
        duplicate_root_class = str(ingest_result.get("root_failure_class") or "").strip().lower()
        duplicate_root_error = str(ingest_result.get("root_error") or "").strip()
        duplicate_root_result_file = str(ingest_result.get("root_result_file") or "").strip()
        duplicate_owner_session_id = str(ingest_result.get("owner_session_id") or "").strip()
        preserve_existing_duplicate_state = failure_class == "inflight_duplicate" and bool(
            duplicate_root_result_file
        )
        if not preserve_existing_duplicate_state:
            try:
                self._write_text_file(failed_ingest_result_path, json.dumps(pending_payload, indent=2))
                self._write_text_file(terminal_result_path, json.dumps(pending_payload, indent=2))
            except Exception:
                logger.warning(
                    "Failed writing local ingest result file session_id=%s path=%s",
                    session_id,
                    failed_ingest_result_path,
                )
        if failure_class == "inflight_duplicate":
            failure_reason = (
                "duplicate ingest request suppressed because another run for this video is already in progress"
            )
            if duplicate_root_class:
                failure_reason += (
                    f". Existing root cause: {duplicate_root_reason or duplicate_root_class}"
                )
            elif duplicate_owner_session_id:
                failure_reason += f" (active session_id={duplicate_owner_session_id})"
        else:
            failure_reason = self._format_ingest_failure_reason(
                error=failure_error,
                failure_class=failure_class,
                attempts=attempts_used,
                max_attempts=max_attempts,
            )
        logger.error(
            "Local youtube ingest failed session_id=%s video_id=%s reason=%s pending_file=%s",
            session_id,
            str(video_id or ""),
            failure_reason,
            pending_path,
        )

        metadata = {
            "hook_youtube_ingest_mode": "local_worker",
            "hook_youtube_ingest_status": "failed_local_ingest",
            "hook_youtube_ingest_pending_file": "" if preserve_existing_duplicate_state else str(pending_path),
            "hook_youtube_ingest_result_file": "" if preserve_existing_duplicate_state else str(failed_ingest_result_path),
            "hook_youtube_ingest_terminal_result_file": "" if preserve_existing_duplicate_state else str(terminal_result_path),
            "hook_youtube_ingest_error": failure_error,
            "hook_youtube_ingest_failure_class": failure_class,
            "hook_youtube_ingest_attempts": attempts_used,
            "hook_youtube_ingest_max_attempts": max_attempts,
            "hook_youtube_ingest_reason": failure_reason,
            "hook_youtube_ingest_video_key": video_key,
            "hook_youtube_ingest_root_error": duplicate_root_error,
            "hook_youtube_ingest_root_failure_class": duplicate_root_class,
            "hook_youtube_ingest_root_reason": duplicate_root_reason,
            "hook_youtube_ingest_root_result_file": duplicate_root_result_file,
            "hook_youtube_ingest_owner_session_id": duplicate_owner_session_id,
        }

        should_fail_open = self._should_fail_open_ingest(
            action=action,
            failure_class=failure_class,
        )
        if should_fail_open:
            metadata["hook_youtube_ingest_status"] = "failed_fail_open"
            metadata["hook_youtube_ingest_fail_open_reason"] = (
                "env_fail_open"
                if self._youtube_ingest_fail_open
                else "allow_degraded_transcript_only"
            )
            metadata["hook_youtube_ingest_pending_file"] = ""
            fail_open_lines = [
                "local_youtube_ingest_mode: local_worker",
                "local_youtube_ingest_status: failed_fail_open",
                f"local_youtube_ingest_result_file: {terminal_result_path}",
                f"local_youtube_ingest_error: {failure_error}",
                f"local_youtube_ingest_failure_class: {failure_class}",
                f"local_youtube_ingest_reason: {failure_reason}",
                "Local transcript ingestion failed; proceed in degraded mode and record this in manifest.",
            ]
            action = action.model_copy(
                update={"message": self._append_message_lines(action.message or "", fail_open_lines)}
            )
            return action, metadata, False

        if not preserve_existing_duplicate_state:
            try:
                self._write_text_file(pending_path, json.dumps(pending_payload, indent=2))
            except Exception:
                logger.warning("Failed writing pending_local_ingest marker session_id=%s", session_id)

        logger.warning(
            "Deferring youtube dispatch session_id=%s status=pending_local_ingest pending_file=%s",
            session_id,
            pending_path,
        )
        return action, metadata, True

    def _extract_token(self, request: Request) -> Optional[str]:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return request.headers.get("X-UA-Hooks-Token")

    def _mapping_matches(self, mapping: HookMappingConfig, context: Dict) -> bool:
        if mapping.match:
            if mapping.match.path and mapping.match.path != context["path"]:
                return False
            if mapping.match.source:
                payload_source = context["payload"].get("source")
                if payload_source != mapping.match.source:
                    return False

            if mapping.match.headers:
                headers = context.get("headers", {})
                for expected_header, expected_value in mapping.match.headers.items():
                    actual_value = headers.get(expected_header.lower())
                    if actual_value is None:
                        return False
                    if str(actual_value) != str(expected_value):
                        return False
        return True

    def _authenticate_request(self, mapping: HookMappingConfig, request: Request, context: Dict) -> bool:
        auth = mapping.auth or HookAuthConfig()
        strategy = (auth.strategy or "token").strip().lower()
        context.pop("_hook_auth_outcome", None)

        if strategy == "none":
            return True
        if strategy == "composio_hmac":
            secret = str(os.getenv("COMPOSIO_WEBHOOK_SECRET") or "").strip()
            if not secret:
                return False

            webhook_id = str(request.headers.get("webhook-id") or "").strip()
            webhook_timestamp = str(request.headers.get("webhook-timestamp") or "").strip()
            webhook_signature = str(request.headers.get("webhook-signature") or "").strip()
            raw_body = context.get("raw_body") or b""
            if not isinstance(raw_body, (bytes, bytearray)):
                raw_body = str(raw_body).encode("utf-8", errors="replace")

            if not webhook_id or not webhook_timestamp or not webhook_signature:
                return False
            try:
                timestamp_epoch = int(webhook_timestamp)
            except (TypeError, ValueError):
                return False

            now_epoch = int(time.time())
            if abs(now_epoch - timestamp_epoch) > int(auth.timestamp_tolerance_seconds or 300):
                return False

            signing_string = f"{webhook_id}.{webhook_timestamp}.{raw_body.decode('utf-8', errors='replace')}"
            expected_digest = hmac.new(
                secret.encode("utf-8"),
                signing_string.encode("utf-8"),
                hashlib.sha256,
            ).digest()
            expected_signature = base64.b64encode(expected_digest).decode("utf-8")
            provided_signatures = [part.strip() for part in webhook_signature.split() if part.strip()]
            if not provided_signatures:
                provided_signatures = [part.strip() for part in webhook_signature.split(",") if part.strip()]

            valid_signature = False
            for candidate in provided_signatures:
                if "," in candidate:
                    _, _, candidate = candidate.partition(",")
                    candidate = candidate.strip()
                if candidate and hmac.compare_digest(candidate, expected_signature):
                    valid_signature = True
                    break
            if not valid_signature:
                return False

            replay_window = int(auth.replay_window_seconds or 600)
            if replay_window > 0:
                self._cleanup_seen_webhook_ids(now_epoch)
                seen_key = f"composio:{webhook_id}"
                existing_expiry = self._seen_webhook_ids.get(seen_key)
                if existing_expiry and existing_expiry > now_epoch:
                    context["_hook_auth_outcome"] = {"deduped": True, "webhook_id": webhook_id}
                    return True
                self._seen_webhook_ids[seen_key] = float(now_epoch + replay_window)
            return True
        # default: token strategy
        if not self.config.token:
            # Explicitly allow open webhook mappings when no token is configured.
            return True
        token = self._extract_token(request)
        return bool(token and token == self.config.token)

    def _cleanup_seen_webhook_ids(self, now_epoch: int) -> None:
        expired = [wid for wid, exp in self._seen_webhook_ids.items() if exp <= now_epoch]
        for wid in expired:
            self._seen_webhook_ids.pop(wid, None)

    async def _build_action(self, mapping: HookMappingConfig, context: Dict) -> Optional[HookAction]:
        # base action
        base_action = self._create_base_action(mapping, context)
        
        # Apply transform
        if mapping.transform:
            transform_fn = self._load_transform(mapping.transform)
            if transform_fn:
                try:
                    if asyncio.iscoroutinefunction(transform_fn):
                        override = await transform_fn(context)
                    else:
                        override = transform_fn(context)

                    if override is None:
                         # Transform indicated skip
                         return None
                    # Merge logic could go here, for now simpler override
                    # If transform returns a dict, merge it into base_action
                    if isinstance(override, dict):
                         updated_data = base_action.model_dump()
                         updated_data.update(override)
                         base_action = HookAction(**updated_data)
                except Exception as e:
                    logger.error(f"Transform failed: {e}")
                    raise e

        return base_action

    def _create_base_action(self, mapping: HookMappingConfig, context: Dict) -> HookAction:
        if mapping.action == "wake":
            text = self._render_template(mapping.text_template or "", context)
            return HookAction(kind="wake", text=text, mode=mapping.wake_mode)
        else:
            message = self._render_template(mapping.message_template or "", context)
            return HookAction(
                kind="agent",
                message=message,
                name=self._render_template(mapping.name or "Hook", context),
                deliver=mapping.deliver,
                session_key=self._render_template(mapping.session_key or "", context),
                mode=mapping.wake_mode,
                allow_unsafe_external_content=mapping.allow_unsafe_external_content,
                to=mapping.to,
                model=mapping.model,
                thinking=mapping.thinking,
                timeout_seconds=mapping.timeout_seconds,
            )

    def _load_transform(self, transform_config: HookTransformConfig):
        # Resolve path
        config_path = resolve_ops_config_path()
        config_dir = config_path.parent
        
        # Use transforms_dir if set, else relative to config file
        base_dir = config_dir
        if self.config.transforms_dir:
             base_dir = (config_dir / self.config.transforms_dir).resolve()
             
        module_path = (base_dir / transform_config.module).resolve()
        
        # Check cache
        cache_key = str(module_path)
        if cache_key in self.transform_cache:
            return self.transform_cache[cache_key]

        if not module_path.exists():
            raise FileNotFoundError(f"Transform module not found: {module_path}")

        spec = importlib.util.spec_from_file_location("hook_transform", module_path)
        if not spec or not spec.loader:
             raise ImportError(f"Could not load spec for {module_path}")
        
        module = importlib.util.module_from_spec(spec)
        sys.modules["hook_transform_temp"] = module
        spec.loader.exec_module(module)
        
        export_name = transform_config.export or "transform"
        if not hasattr(module, export_name):
             raise ImportError(f"Module {module_path} does not export '{export_name}'")
             
        fn = getattr(module, export_name)
        self.transform_cache[cache_key] = fn
        return fn

    def _render_template(self, template: str, context: Dict) -> str:
        # Simple templating: {{ payload.x }} {{ headers.y }}
        # Can rely on python's str.format or a simple regex replacer
        # Clawdbot uses a custom replacer. Let's do a simple one for now.
        # Supporting dot notation is key.
        import re
        
        def getattr_deep(obj, path):
            parts = path.split('.')
            curr = obj
            for p in parts:
                if isinstance(curr, dict):
                    curr = curr.get(p)
                else:
                    return None
                if curr is None: return None
            return curr

        def replacer(match):
            expr = match.group(1).strip()
            val = getattr_deep(context, expr)
            return str(val) if val is not None else ""

        return re.sub(r'\{\{\s*([^}]+)\s*\}\}', replacer, template)

    async def _dispatch_action(
        self,
        action: HookAction,
        *,
        workflow_run_id: Optional[str] = None,
        workflow_attempt_id: Optional[str] = None,
        workflow_workspace_dir: Optional[str] = None,
        skip_workflow_admission: bool = False,
    ) -> dict[str, Any]:
        logger.info("Dispatching hook action kind=%s", action.kind)
        if action.kind == "wake":
            logger.info("Wake hook action is not implemented yet; dropping action")
            return {"decision": "failed", "reason": "wake_not_supported", "error": "wake_not_supported"}
        if action.kind != "agent":
            logger.warning("Unsupported hook action kind=%s", action.kind)
            return {"decision": "failed", "reason": "unsupported_action_kind", "error": "unsupported_action_kind"}

        session_key = (action.session_key or "").strip()
        if not session_key:
            logger.warning("Hook agent action missing session_key")
            return {"decision": "failed", "reason": "missing_session_key", "error": "missing_session_key"}

        session_id = self._session_id_from_key(session_key)
        timeout_seconds = (
            int(action.timeout_seconds)
            if action.timeout_seconds is not None
            else (self._default_hook_timeout_seconds or None)
        )
        is_youtube_tutorial = _is_youtube_agent_route(action.to)
        expected_video_id = self._extract_action_field(action.message or "", "video_id")
        expected_video_title = self._extract_action_field(action.message or "", "title")
        workflow_attempt_number: Optional[int] = None
        workflow_profile = self._workflow_profile_for_action(
            action=action,
            session_key=session_key,
            session_id=session_id,
            expected_video_id=expected_video_id,
        )
        workflow_service = self._workflow_admission_service() if workflow_profile is not None else None
        if workflow_profile is not None and not skip_workflow_admission:
            admission_result = await self._admit_workflow_with_retry(
                action=action,
                session_key=session_key,
                workflow_profile=workflow_profile,
            )
            if str(admission_result.get("decision") or "").strip().lower() == "failed":
                return {
                    "decision": "failed",
                    "reason": "runtime_db_locked",
                    "retryable": True,
                    "session_id": session_id,
                }
            workflow_decision = admission_result["workflow_decision"]
            workflow_run_id = str(admission_result.get("run_id") or "").strip() or None
            workflow_attempt_id = str(admission_result.get("attempt_id") or "").strip() or None
            workflow_attempt_number = int(admission_result.get("attempt_number") or 0) or None
            workflow_workspace_dir = str(admission_result.get("workspace_dir") or "").strip()
            if workflow_decision.action in {
                "attach_to_existing_run",
                "defer",
                "skip_duplicate",
            }:
                return {
                    "decision": "skipped",
                    "reason": workflow_decision.reason,
                    "session_id": session_id,
                    "run_id": workflow_run_id,
                    "attempt_id": workflow_attempt_id,
                    "workspace_dir": workflow_workspace_dir,
                }
            if workflow_decision.action == "escalate_review":
                self._emit_youtube_tutorial_failure_notification(
                    session_id=session_id,
                    session_key=session_key,
                    hook_name=action.name or "Hook",
                    expected_video_id=expected_video_id,
                    reason="needs_review",
                    started_at_epoch=None,
                    run_id=workflow_run_id,
                    attempt_id=workflow_attempt_id,
                    attempt_number=workflow_attempt_number,
                    workspace_dir=workflow_workspace_dir,
                    provider_session_id=session_id,
                )
                return {
                    "decision": "failed",
                    "reason": workflow_decision.reason,
                    "session_id": session_id,
                    "run_id": workflow_run_id,
                    "attempt_id": workflow_attempt_id,
                    "workspace_dir": workflow_workspace_dir,
                }

        # --- Video-level dispatch dedup guard ---
        if is_youtube_tutorial and expected_video_id:
            async with self._youtube_video_dispatch_lock:
                self._evict_stale_video_dispatch_entries()
                existing_ts = self._youtube_video_dispatch_inflight.get(expected_video_id)
                if existing_ts is not None:
                    age = time.time() - existing_ts
                    logger.info(
                        "Duplicate YouTube dispatch rejected video_id=%s "
                        "session_key=%s hook=%s inflight_age=%.0fs",
                        expected_video_id,
                        session_key,
                        action.name or "Hook",
                        age,
                    )
                    return {
                        "decision": "skipped",
                        "reason": "duplicate_video_dispatch",
                        "video_id": expected_video_id,
                        "inflight_age_seconds": int(age),
                    }
                self._youtube_video_dispatch_inflight[expected_video_id] = time.time()

        if is_youtube_tutorial:
            if timeout_seconds is None:
                timeout_seconds = self._youtube_hook_timeout_seconds
            else:
                timeout_seconds = max(timeout_seconds, self._youtube_hook_timeout_seconds)
        if timeout_seconds is not None:
            timeout_seconds = max(1, timeout_seconds)

        metadata: Dict[str, Any] = {
            "source": "webhook",
            "hook_name": action.name or "Hook",
            "hook_session_key": session_key,
            "hook_session_id": session_id,
        }
        if action.to:
            metadata["hook_route_to"] = action.to
        if action.model:
            metadata["hook_model"] = action.model
        if expected_video_title:
            metadata["tutorial_title"] = expected_video_title
        if action.thinking:
            metadata["hook_thinking"] = action.thinking
        if timeout_seconds is not None:
            metadata["hook_timeout_seconds"] = timeout_seconds
        if workflow_profile is not None and workflow_run_id:
            if workflow_attempt_number is None:
                attempt_context = self._workflow_attempt_context_safe(
                    run_id=workflow_run_id,
                    attempt_id=workflow_attempt_id,
                )
                workflow_attempt_number = (
                    int(attempt_context.get("attempt_number") or 0) or None
                )
                workflow_workspace_dir = str(
                    attempt_context.get("workspace_dir") or workflow_workspace_dir or ""
                ) or workflow_workspace_dir
            metadata = self._merge_workflow_metadata(
                metadata,
                run_id=workflow_run_id,
                attempt_id=workflow_attempt_id,
                attempt_number=workflow_attempt_number,
                workspace_dir=workflow_workspace_dir,
                provider_session_id=session_id,
            )
        run_source = str(metadata.get("source") or "webhook").strip().lower() or "webhook"
        hook_name = action.name or "Hook"
        session_workspace: Optional[Path] = None
        admitted_turn_id: Optional[str] = None
        start_ts: Optional[float] = None
        execution_summary: dict[str, Any] = {}
        terminal_reason: Optional[str] = None
        pending_recovery_payload: dict[str, Any] = {}
        dispatch_gate_acquired = False
        dispatch_wait_started = time.time()
        pending_admitted = False
        async with self._agent_dispatch_state_lock:
            candidate_pending = self._agent_dispatch_pending_count + 1
            if candidate_pending > self._agent_dispatch_queue_limit:
                logger.error(
                    "Hook action dropped session_id=%s reason=dispatch_queue_overflow pending=%s limit=%s",
                    session_id,
                    candidate_pending,
                    self._agent_dispatch_queue_limit,
                )
                now_ts = time.time()
                cooldown = int(self._dispatch_overflow_notification_cooldown_seconds)
                state = self._dispatch_overflow_state.get(session_id) or {}
                last_emit_ts = float(state.get("last_emit_ts") or 0.0)
                suppressed_count = int(state.get("suppressed_count") or 0)
                if cooldown > 0 and (now_ts - last_emit_ts) < cooldown:
                    state["suppressed_count"] = suppressed_count + 1
                    state["last_pending"] = candidate_pending
                    self._dispatch_overflow_state[session_id] = state
                    return {"decision": "failed", "reason": "dispatch_queue_overflow", "error": "dispatch_queue_overflow"}

                note_suffix = ""
                if suppressed_count > 0:
                    note_suffix = f"; suppressed {suppressed_count} duplicate overflow alert(s)"
                self._emit_notification(
                    kind="hook_dispatch_queue_overflow",
                    title="Hook Dispatch Queue Overflow",
                    message=(
                        f"Dropped hook action for {session_id} "
                        f"(pending={candidate_pending}, limit={self._agent_dispatch_queue_limit}{note_suffix})"
                    ),
                    session_id=session_id,
                    severity="error",
                    metadata={
                        "source": "hooks",
                        "pending": candidate_pending,
                        "limit": int(self._agent_dispatch_queue_limit),
                        "suppressed_duplicates": suppressed_count,
                        "cooldown_seconds": cooldown,
                    },
                )
                self._dispatch_overflow_state[session_id] = {
                    "last_emit_ts": now_ts,
                    "suppressed_count": 0,
                    "last_pending": candidate_pending,
                }
                return {"decision": "failed", "reason": "dispatch_queue_overflow", "error": "dispatch_queue_overflow"}
            self._agent_dispatch_pending_count = candidate_pending
            pending_admitted = True
        await self._agent_dispatch_gate.acquire()
        dispatch_gate_acquired = True
        dispatch_queue_wait_seconds = max(0.0, time.time() - dispatch_wait_started)
        if dispatch_queue_wait_seconds > 0:
            metadata["hook_dispatch_queue_wait_seconds"] = round(
                dispatch_queue_wait_seconds,
                3,
            )
        if dispatch_queue_wait_seconds >= 1.0:
            logger.info(
                "Hook action queued session_id=%s wait_seconds=%.3f",
                session_id,
                dispatch_queue_wait_seconds,
            )

        try:
            resolved_workspace_dir = workflow_workspace_dir or self._hook_workflow_workspace_dir(session_key)
            session = await self._resolve_or_create_webhook_session(session_id, resolved_workspace_dir)
            session_workspace = Path(str(session.workspace_dir)).resolve()
            if workflow_profile is not None and workflow_run_id and workflow_attempt_id and workflow_service is not None:
                workflow_service.mark_running(
                    workflow_run_id,
                    attempt_id=workflow_attempt_id,
                    provider_session_id=session_id,
                    summary={
                        "hook_name": hook_name,
                        "workspace_dir": str(session_workspace),
                        **({"video_id": expected_video_id or ""} if expected_video_id else {}),
                    },
                )
                attempt_context = self._workflow_attempt_context_safe(
                    run_id=workflow_run_id,
                    attempt_id=workflow_attempt_id,
                )
                workflow_attempt_number = int(attempt_context.get("attempt_number") or 0) or workflow_attempt_number
                metadata = self._merge_workflow_metadata(
                    metadata,
                    run_id=workflow_run_id,
                    attempt_id=workflow_attempt_id,
                    attempt_number=workflow_attempt_number,
                    workspace_dir=str(session_workspace),
                    provider_session_id=session_id,
                )
            pending_recovery_payload = self._read_pending_hook_recovery_marker(session_workspace)
            self._clear_pending_hook_recovery_marker(session_workspace)
            action, ingest_metadata, should_skip_dispatch = await self._prepare_local_youtube_ingest(
                action=action,
                session_id=session_id,
                session_workspace=str(session.workspace_dir),
            )
            metadata.update(ingest_metadata)
            if should_skip_dispatch:
                ingest_status = str(metadata.get("hook_youtube_ingest_status") or "").strip().lower()
                failed_local_ingest = ingest_status == "failed_local_ingest"
                marker_state = "failed_local_ingest" if failed_local_ingest else "pending_local_ingest"
                marker_error = (
                    str(metadata.get("hook_youtube_ingest_reason") or metadata.get("hook_youtube_ingest_error") or "")
                    if failed_local_ingest
                    else None
                )
                ingest_failure_class = str(metadata.get("hook_youtube_ingest_failure_class") or "").strip().lower()
                if is_youtube_tutorial and workflow_run_id and workflow_attempt_id and workflow_service is not None:
                    if failed_local_ingest:
                        reason = str(metadata.get("hook_youtube_ingest_reason") or "local_ingest_failed").strip()
                        retryable_ingest_failure = (
                            ingest_failure_class
                            and ingest_failure_class not in YOUTUBE_INGEST_NON_RETRYABLE_FAILURE_CLASSES
                            and ingest_failure_class != "inflight_duplicate"
                        )
                        if retryable_ingest_failure:
                            retry_decision = workflow_service.queue_retry(
                                self._build_youtube_workflow_trigger(
                                    action=action,
                                    session_key=session_key,
                                    session_id=session_id,
                                    expected_video_id=expected_video_id,
                                ),
                                entrypoint="hooks_service.youtube_tutorial_hook",
                                run_id=workflow_run_id,
                                attempt_id=workflow_attempt_id,
                                workspace_dir=str(session_workspace),
                                failure_reason=reason,
                                failure_class=ingest_failure_class or "youtube_ingest_failed",
                                max_attempts=3,
                            )
                            retry_context = self._workflow_attempt_context_safe(
                                run_id=workflow_run_id,
                                attempt_id=retry_decision.attempt_id,
                            )
                            next_attempt_number = int(retry_context.get("attempt_number") or 0)
                            self._emit_youtube_retry_queued_notification(
                                session_id=session_id,
                                session_key=session_key,
                                hook_name=hook_name,
                                expected_video_id=expected_video_id,
                                current_attempt_number=workflow_attempt_number or 1,
                                next_attempt_number=next_attempt_number,
                                max_attempts=3,
                                reason=reason,
                                run_id=workflow_run_id,
                                attempt_id=workflow_attempt_id,
                                workspace_dir=str(session_workspace),
                            )
                            if retry_decision.action == "start_new_attempt" and retry_decision.attempt_id:
                                self._schedule_youtube_retry_attempt(
                                    action=action,
                                    run_id=workflow_run_id,
                                    attempt_id=retry_decision.attempt_id,
                                    workspace_dir=str(session_workspace),
                                )
                        elif ingest_failure_class == "inflight_duplicate":
                            workflow_service.mark_blocked(
                                workflow_run_id,
                                attempt_id=workflow_attempt_id,
                                reason=reason,
                                summary={
                                    "video_id": expected_video_id or "",
                                    "ingest_status": ingest_status,
                                    "failure_class": ingest_failure_class,
                                },
                            )
                        else:
                            workflow_service.mark_needs_review(
                                workflow_run_id,
                                attempt_id=workflow_attempt_id,
                                reason=reason,
                                failure_class=ingest_failure_class or "youtube_ingest_failed",
                                summary={
                                    "video_id": expected_video_id or "",
                                    "ingest_status": ingest_status,
                                    "failure_class": ingest_failure_class,
                                },
                            )
                    else:
                        workflow_service.mark_blocked(
                            workflow_run_id,
                            attempt_id=workflow_attempt_id,
                            reason="pending_local_ingest",
                            summary={
                                "video_id": expected_video_id or "",
                                "ingest_status": ingest_status,
                            },
                        )
                if session_workspace is not None:
                    self._write_sync_ready_marker(
                        session_id=session_id,
                        workspace_root=session_workspace,
                        state=marker_state,
                        ready=False,
                        hook_name=hook_name,
                        run_source=run_source,
                        error=marker_error,
                    )
                if failed_local_ingest:
                    reason = str(metadata.get("hook_youtube_ingest_reason") or "local_ingest_failed").strip()
                    failure_class = str(metadata.get("hook_youtube_ingest_failure_class") or "").strip().lower()
                    is_duplicate = failure_class == "inflight_duplicate"
                    logger.error(
                        "Hook action failed pre-dispatch session_id=%s hook=%s reason=%s",
                        session_id,
                        hook_name,
                        reason,
                    )
                    self._emit_notification(
                        kind="youtube_ingest_failed",
                        title="YouTube Ingest Duplicate Suppressed" if is_duplicate else "YouTube Ingest Failed",
                        message=reason,
                        session_id=session_id,
                        severity="warning" if is_duplicate else "error",
                        requires_action=not is_duplicate,
                        metadata=self._merge_workflow_metadata(
                            {
                                "source": "hooks",
                                "hook_name": hook_name,
                                "hook_session_key": session_key,
                                "video_key": str(metadata.get("hook_youtube_ingest_video_key") or ""),
                                "error": str(metadata.get("hook_youtube_ingest_error") or ""),
                                "failure_class": str(metadata.get("hook_youtube_ingest_failure_class") or ""),
                                "root_error": str(metadata.get("hook_youtube_ingest_root_error") or ""),
                                "root_failure_class": str(metadata.get("hook_youtube_ingest_root_failure_class") or ""),
                                "root_reason": str(metadata.get("hook_youtube_ingest_root_reason") or ""),
                                "owner_session_id": str(metadata.get("hook_youtube_ingest_owner_session_id") or ""),
                                "attempts": int(metadata.get("hook_youtube_ingest_attempts") or 0),
                                "max_attempts": int(metadata.get("hook_youtube_ingest_max_attempts") or 0),
                                "pending_file": str(metadata.get("hook_youtube_ingest_pending_file") or ""),
                                "result_file": str(metadata.get("hook_youtube_ingest_terminal_result_file") or metadata.get("hook_youtube_ingest_result_file") or ""),
                                "root_result_file": str(metadata.get("hook_youtube_ingest_root_result_file") or ""),
                            },
                            run_id=workflow_run_id,
                            attempt_id=workflow_attempt_id,
                            attempt_number=workflow_attempt_number,
                            workspace_dir=str(session_workspace) if session_workspace is not None else workflow_workspace_dir,
                            provider_session_id=session_id,
                            max_attempts=3,
                        ),
                    )
                    if failure_class in YOUTUBE_PROXY_ALERT_FAILURE_CLASSES:
                        if failure_class == "proxy_not_configured":
                            _proxy_alert_msg = (
                                "CRITICAL: YouTube ingest BLOCKED — residential proxy is NOT CONFIGURED. "
                                "PROXY_USERNAME/PROXY_PASSWORD env vars are missing. "
                                "All YouTube transcript requests will fail until proxy credentials are added."
                            )
                        elif failure_class == "proxy_connect_failed":
                            _proxy_alert_msg = (
                                "YouTube ingest failed because the residential proxy CONNECT path is broken. "
                                "Check Webshare host/port overrides, proxy credentials in Infisical, "
                                "and upstream proxy availability."
                            )
                        elif failure_class == "proxy_pool_unallocated":
                            _proxy_alert_msg = (
                                "YouTube ingest failed because Webshare reported no proxies allocated for the "
                                "configured endpoint/username. Refresh the Webshare proxy list/rotation username "
                                "and update Infisical proxy secrets."
                            )
                        else:
                            _proxy_alert_msg = (
                                "YouTube ingest failed due to proxy billing/quota or proxy credentials. "
                                "Check Webshare account status and proxy secrets."
                            )
                        self._emit_notification(
                            kind="youtube_ingest_proxy_alert",
                            title="YouTube Proxy Alert",
                            message=_proxy_alert_msg,
                            session_id=session_id,
                            severity="error",
                            requires_action=True,
                            metadata=self._merge_workflow_metadata(
                                {
                                    "source": "hooks",
                                    "hook_name": hook_name,
                                    "hook_session_key": session_key,
                                    "failure_class": failure_class,
                                    "error": str(metadata.get("hook_youtube_ingest_error") or ""),
                                    "reason": reason,
                                    "video_key": str(metadata.get("hook_youtube_ingest_video_key") or ""),
                                    "result_file": str(metadata.get("hook_youtube_ingest_terminal_result_file") or metadata.get("hook_youtube_ingest_result_file") or ""),
                                },
                                run_id=workflow_run_id,
                                attempt_id=workflow_attempt_id,
                                attempt_number=workflow_attempt_number,
                                workspace_dir=str(session_workspace) if session_workspace is not None else workflow_workspace_dir,
                                provider_session_id=session_id,
                                max_attempts=3,
                            ),
                        )
                else:
                    logger.info(
                        "Hook action deferred session_id=%s hook=%s reason=pending_local_ingest",
                        session_id,
                        hook_name,
                    )
                return {
                    "decision": "failed" if failed_local_ingest else "blocked",
                    "reason": str(metadata.get("hook_youtube_ingest_reason") or "pre_dispatch_deferred"),
                    "error": str(metadata.get("hook_youtube_ingest_error") or metadata.get("hook_youtube_ingest_reason") or "pre_dispatch_deferred"),
                    "failure_class": str(metadata.get("hook_youtube_ingest_failure_class") or ""),
                    "run_id": workflow_run_id,
                    "attempt_id": workflow_attempt_id,
                    "workspace_dir": str(session_workspace) if session_workspace is not None else workflow_workspace_dir,
                }

            user_input = self._build_agent_user_input(action)
            if not user_input:
                if session_workspace is not None:
                    self._write_sync_ready_marker(
                        session_id=session_id,
                        workspace_root=session_workspace,
                        state="failed_pre_dispatch",
                        ready=True,
                        hook_name=hook_name,
                        run_source=run_source,
                        completed_at_epoch=time.time(),
                        error="missing_message",
                    )
                logger.warning("Hook agent action missing message session_key=%s", session_key)
                return {"decision": "failed", "reason": "missing_message", "error": "missing_message"}

            request = GatewayRequest(user_input=user_input, metadata=metadata)
            if self._turn_admitter:
                admission = await self._turn_admitter(session_id, request)
                decision = str(admission.get("decision", "accepted"))
                admitted_turn_id = str(admission.get("turn_id") or "") or None
                if decision != "accepted":
                    logger.info(
                        "Hook action skipped session_id=%s decision=%s turn_id=%s",
                        session_id,
                        decision,
                        admitted_turn_id or "",
                    )
                    return {
                        "decision": decision,
                        "turn_id": admitted_turn_id,
                        "reason": decision,
                        "session_id": session_id,
                    }

            if self._run_counter_start:
                self._run_counter_start(session_id, run_source)

            start_ts = time.time()
            if session_workspace is not None:
                self._write_sync_ready_marker(
                    session_id=session_id,
                    workspace_root=session_workspace,
                    state="in_progress",
                    ready=False,
                    hook_name=hook_name,
                    run_source=run_source,
                    started_at_epoch=start_ts,
                )
            if is_youtube_tutorial:
                tutorial_title = str(metadata.get("tutorial_title") or "").strip()
                ingest_status = str(metadata.get("hook_youtube_ingest_status") or "").strip().lower()
                ingest_reason = str(metadata.get("hook_youtube_ingest_reason") or "").strip()
                degraded_fail_open = ingest_status == "failed_fail_open"
                if tutorial_title and expected_video_id:
                    processing_label = f"{tutorial_title} ({expected_video_id})"
                elif tutorial_title:
                    processing_label = tutorial_title
                else:
                    processing_label = expected_video_id or session_key
                self._emit_notification(
                    kind="youtube_tutorial_started",
                    title="YouTube Tutorial Pipeline Started",
                    message=f"Processing tutorial pipeline attempt {workflow_attempt_number or 1}.",
                    session_id=session_id,
                    severity="info",
                    metadata=self._merge_workflow_metadata(
                        {
                            "source": "hooks",
                            "hook_name": hook_name,
                            "hook_session_key": session_key,
                            "video_id": expected_video_id or "",
                            "tutorial_title": tutorial_title,
                            "processing_label": processing_label,
                            "ingest_status": ingest_status,
                            "ingest_reason": ingest_reason,
                            "ingest_failure_class": str(metadata.get("hook_youtube_ingest_failure_class") or ""),
                        },
                        run_id=workflow_run_id,
                        attempt_id=workflow_attempt_id,
                        attempt_number=workflow_attempt_number,
                        workspace_dir=str(session_workspace) if session_workspace is not None else workflow_workspace_dir,
                        provider_session_id=session_id,
                        max_attempts=3,
                    ),
                )
                if degraded_fail_open:
                    self._emit_notification(
                        kind="youtube_tutorial_progress",
                        title="YouTube Tutorial Running In Degraded Mode",
                        message=(
                            f"{processing_label}: local ingest failed, continuing without pre-fetched transcript."
                            f"{f' {ingest_reason}' if ingest_reason else ''}"
                        ),
                        session_id=session_id,
                        severity="warning",
                        metadata=self._merge_workflow_metadata(
                            {
                                "source": "hooks",
                                "hook_name": hook_name,
                                "hook_session_key": session_key,
                                "video_id": expected_video_id or "",
                                "tutorial_title": tutorial_title,
                                "ingest_status": ingest_status,
                                "ingest_reason": ingest_reason,
                                "ingest_failure_class": str(metadata.get("hook_youtube_ingest_failure_class") or ""),
                            },
                            run_id=workflow_run_id,
                            attempt_id=workflow_attempt_id,
                            attempt_number=workflow_attempt_number,
                            workspace_dir=str(session_workspace) if session_workspace is not None else workflow_workspace_dir,
                            provider_session_id=session_id,
                            max_attempts=3,
                        ),
                    )
            idle_timeout_seconds = (
                int(self._youtube_hook_idle_timeout_seconds)
                if is_youtube_tutorial and self._youtube_hook_idle_timeout_seconds
                else None
            )
            execution_summary = await self._run_gateway_execute_with_watchdogs(
                session=session,
                request=request,
                workspace_root=session_workspace,
                total_timeout_seconds=timeout_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
            )
            if execution_summary.get("reported_timeout"):
                raise HookReportedTimeout(
                    str(execution_summary.get("reported_timeout_message") or "agent_reported_timeout")
                )
            if execution_summary.get("reported_error"):
                raise RuntimeError(
                    str(execution_summary.get("reported_error_message") or "agent_reported_error")
                )
            if is_youtube_tutorial and expected_video_id:
                execution_summary["artifact_validation"] = self._validate_youtube_tutorial_artifacts(
                    video_id=expected_video_id,
                    started_at_epoch=float(start_ts or 0.0),
                )
                artifact_validation = execution_summary.get("artifact_validation") or {}
                if isinstance(artifact_validation, dict):
                    self._emit_youtube_tutorial_ready_notification(
                        session_id=session_id,
                        session_key=session_key,
                        hook_name=hook_name,
                        expected_video_id=expected_video_id,
                        ingest_status=ingest_status,
                        artifact_validation=artifact_validation,
                        pending_recovery_payload=pending_recovery_payload,
                        run_id=workflow_run_id,
                        attempt_id=workflow_attempt_id,
                        attempt_number=workflow_attempt_number,
                        workspace_dir=str(session_workspace) if session_workspace is not None else workflow_workspace_dir,
                        provider_session_id=session_id,
                        max_attempts=3,
                    )
            if workflow_profile is not None and workflow_run_id and workflow_service is not None:
                workflow_service.mark_completed(
                    workflow_run_id,
                    attempt_id=workflow_attempt_id,
                    summary={
                        "hook_name": hook_name,
                        "status": "completed",
                        **({"video_id": expected_video_id or ""} if expected_video_id else {}),
                    },
                )
            if admitted_turn_id and self._turn_finalizer:
                await self._turn_finalizer(
                    session_id,
                    admitted_turn_id,
                    "completed",
                    None,
                    execution_summary,
                )
            if session_workspace is not None:
                self._write_sync_ready_marker(
                    session_id=session_id,
                    workspace_root=session_workspace,
                    state="completed",
                    ready=True,
                    hook_name=hook_name,
                    run_source=run_source,
                    started_at_epoch=start_ts,
                    completed_at_epoch=time.time(),
                    execution_summary=execution_summary,
                )
            if str(action.name or "").strip() == "AutoHeartbeatInvestigation":
                self._emit_heartbeat_investigation_completion(
                    session_id=session_id,
                    session_key=session_key,
                    workspace_root=session_workspace,
                )
            logger.info("Hook action dispatched session_id=%s hook=%s", session_id, hook_name)
            terminal_reason = "completed"
            return {
                "decision": "accepted",
                "turn_id": admitted_turn_id,
                "status": "completed",
                "session_id": session_id,
                "execution_summary": execution_summary,
                "run_id": workflow_run_id,
                "attempt_id": workflow_attempt_id,
            }
        except HookReportedTimeout as exc:
            terminal_reason = "agent_reported_timeout"
            logger.error(
                "Hook action reported timeout session_key=%s session_id=%s detail=%s",
                session_key,
                session_id,
                exc,
            )
            if session_workspace is not None:
                state = {
                    "tool_calls": 0,
                    "duration_seconds": round(max(0.0, time.time() - (start_ts or time.time())), 3),
                }
                self._write_sync_ready_marker(
                    session_id=session_id,
                    workspace_root=session_workspace,
                    state="timed_out",
                    ready=True,
                    hook_name=hook_name,
                    run_source=run_source,
                    started_at_epoch=start_ts,
                    completed_at_epoch=time.time(),
                    error="agent_reported_timeout",
                    execution_summary=state,
                )
            if self._turn_finalizer:
                try:
                    state = {
                        "tool_calls": 0,
                        "duration_seconds": round(max(0.0, time.time() - (start_ts or time.time())), 3),
                    }
                    if admitted_turn_id:
                        await self._turn_finalizer(
                            session_id,
                            admitted_turn_id,
                            "failed",
                            "agent_reported_timeout",
                            state,
                        )
                except Exception:
                    logger.exception("Failed finalizing timeout-reported hook turn session_id=%s", session_id)
            if is_youtube_tutorial:
                self._queue_or_finalize_youtube_attempt(
                    action=action,
                    session_id=session_id,
                    session_key=session_key,
                    hook_name=hook_name,
                    expected_video_id=expected_video_id,
                    reason="agent_reported_timeout",
                    failure_class="hook_timeout",
                    session_workspace=session_workspace,
                    started_at_epoch=start_ts,
                    workflow_run_id=workflow_run_id,
                    workflow_attempt_id=workflow_attempt_id,
                    workflow_attempt_number=workflow_attempt_number,
                )
            elif workflow_profile is not None:
                self._queue_or_finalize_generic_hook_attempt(
                    action=action,
                    hook_name=hook_name,
                    reason="agent_reported_timeout",
                    failure_class="hook_timeout",
                    session_workspace=session_workspace,
                    workflow_profile=workflow_profile,
                    workflow_run_id=workflow_run_id,
                    workflow_attempt_id=workflow_attempt_id,
                )
            return {
                "decision": "failed",
                "turn_id": admitted_turn_id,
                "reason": "agent_reported_timeout",
                "error": str(exc),
                "session_id": session_id,
                "run_id": workflow_run_id,
                "attempt_id": workflow_attempt_id,
            }
        except HookIdleTimeout as exc:
            terminal_reason = f"hook_idle_timeout_{int(self._youtube_hook_idle_timeout_seconds or 0)}s"
            logger.error(
                "Hook action idle timed out session_key=%s session_id=%s detail=%s",
                session_key,
                session_id,
                exc,
            )
            await self._abort_active_session_execution(session_id, reason=str(exc))
            if session_workspace is not None:
                state = {
                    "tool_calls": 0,
                    "duration_seconds": round(max(0.0, time.time() - (start_ts or time.time())), 3),
                }
                idle_seconds = int(self._youtube_hook_idle_timeout_seconds or 0)
                self._write_sync_ready_marker(
                    session_id=session_id,
                    workspace_root=session_workspace,
                    state="timed_out",
                    ready=True,
                    hook_name=hook_name,
                    run_source=run_source,
                    started_at_epoch=start_ts,
                    completed_at_epoch=time.time(),
                    error=f"hook_idle_timeout_{idle_seconds}s",
                    execution_summary=state,
                )
            if self._turn_finalizer:
                try:
                    state = {
                        "tool_calls": 0,
                        "duration_seconds": round(max(0.0, time.time() - (start_ts or time.time())), 3),
                    }
                    if admitted_turn_id:
                        idle_seconds = int(self._youtube_hook_idle_timeout_seconds or 0)
                        await self._turn_finalizer(
                            session_id,
                            admitted_turn_id,
                            "failed",
                            f"hook_idle_timeout_{idle_seconds}s",
                            state,
                        )
                except Exception:
                    logger.exception("Failed finalizing idle-timeout hook turn session_id=%s", session_id)
            if is_youtube_tutorial:
                idle_seconds = int(self._youtube_hook_idle_timeout_seconds or 0)
                self._queue_or_finalize_youtube_attempt(
                    action=action,
                    session_id=session_id,
                    session_key=session_key,
                    hook_name=hook_name,
                    expected_video_id=expected_video_id,
                    reason=f"hook_idle_timeout_{idle_seconds}s",
                    failure_class="hook_idle_timeout",
                    session_workspace=session_workspace,
                    started_at_epoch=start_ts,
                    workflow_run_id=workflow_run_id,
                    workflow_attempt_id=workflow_attempt_id,
                    workflow_attempt_number=workflow_attempt_number,
                )
            elif workflow_profile is not None:
                idle_seconds = int(self._youtube_hook_idle_timeout_seconds or 0)
                self._queue_or_finalize_generic_hook_attempt(
                    action=action,
                    hook_name=hook_name,
                    reason=f"hook_idle_timeout_{idle_seconds}s",
                    failure_class="hook_idle_timeout",
                    session_workspace=session_workspace,
                    workflow_profile=workflow_profile,
                    workflow_run_id=workflow_run_id,
                    workflow_attempt_id=workflow_attempt_id,
                )
            return {
                "decision": "failed",
                "turn_id": admitted_turn_id,
                "reason": f"hook_idle_timeout_{int(self._youtube_hook_idle_timeout_seconds or 0)}s",
                "error": str(exc),
                "session_id": session_id,
                "run_id": workflow_run_id,
                "attempt_id": workflow_attempt_id,
            }
        except asyncio.TimeoutError:
            terminal_reason = f"hook_timeout_{timeout_seconds}s"
            logger.error(
                "Hook action timed out session_key=%s session_id=%s timeout_seconds=%s",
                session_key,
                session_id,
                timeout_seconds,
            )
            await self._abort_active_session_execution(
                session_id,
                reason=f"hook_timeout_{timeout_seconds}s",
            )
            if session_workspace is not None:
                state = {
                    "tool_calls": 0,
                    "duration_seconds": round(max(0.0, time.time() - (start_ts or time.time())), 3),
                }
                self._write_sync_ready_marker(
                    session_id=session_id,
                    workspace_root=session_workspace,
                    state="timed_out",
                    ready=True,
                    hook_name=hook_name,
                    run_source=run_source,
                    started_at_epoch=start_ts,
                    completed_at_epoch=time.time(),
                    error=f"hook_timeout_{timeout_seconds}s",
                    execution_summary=state,
                )
            if self._turn_finalizer:
                try:
                    state = {
                        "tool_calls": 0,
                        "duration_seconds": round(max(0.0, time.time() - (start_ts or time.time())), 3),
                    }
                    if admitted_turn_id:
                        await self._turn_finalizer(
                            session_id,
                            admitted_turn_id,
                            "failed",
                            f"hook_timeout_{timeout_seconds}s",
                            state,
                        )
                except Exception:
                    logger.exception("Failed finalizing timed-out hook turn session_id=%s", session_id)
            if is_youtube_tutorial:
                self._queue_or_finalize_youtube_attempt(
                    action=action,
                    session_id=session_id,
                    session_key=session_key,
                    hook_name=hook_name,
                    expected_video_id=expected_video_id,
                    reason=f"hook_timeout_{timeout_seconds}s",
                    failure_class="hook_timeout",
                    session_workspace=session_workspace,
                    started_at_epoch=start_ts,
                    workflow_run_id=workflow_run_id,
                    workflow_attempt_id=workflow_attempt_id,
                    workflow_attempt_number=workflow_attempt_number,
                )
            elif workflow_profile is not None:
                self._queue_or_finalize_generic_hook_attempt(
                    action=action,
                    hook_name=hook_name,
                    reason=f"hook_timeout_{timeout_seconds}s",
                    failure_class="hook_timeout",
                    session_workspace=session_workspace,
                    workflow_profile=workflow_profile,
                    workflow_run_id=workflow_run_id,
                    workflow_attempt_id=workflow_attempt_id,
                )
            return {
                "decision": "failed",
                "turn_id": admitted_turn_id,
                "reason": f"hook_timeout_{timeout_seconds}s",
                "error": f"hook_timeout_{timeout_seconds}s",
                "session_id": session_id,
                "run_id": workflow_run_id,
                "attempt_id": workflow_attempt_id,
            }
        except Exception as exc:
            dispatch_failure_reason = self._dispatch_failure_reason(exc, execution_summary)
            terminal_reason = dispatch_failure_reason
            is_interrupted_dispatch = dispatch_failure_reason == "hook_dispatch_interrupted"
            recovered_artifact_validation: Optional[dict[str, Any]] = None
            if (
                is_youtube_tutorial
                and expected_video_id
                and not is_interrupted_dispatch
                and start_ts is not None
            ):
                try:
                    recovered_artifact_validation = self._validate_youtube_tutorial_artifacts(
                        video_id=expected_video_id,
                        started_at_epoch=float(start_ts or 0.0),
                    )
                except Exception:
                    recovered_artifact_validation = None
            if recovered_artifact_validation:
                logger.warning(
                    "Hook dispatch raised %s after validated tutorial artifacts existed; "
                    "treating session_id=%s video_id=%s as completed",
                    dispatch_failure_reason,
                    session_id,
                    expected_video_id,
                )
                execution_summary["artifact_validation"] = recovered_artifact_validation
                ingest_status = str(metadata.get("hook_youtube_ingest_status") or "").strip().lower()
                self._emit_youtube_tutorial_ready_notification(
                    session_id=session_id,
                    session_key=session_key,
                    hook_name=hook_name,
                    expected_video_id=expected_video_id,
                    ingest_status=ingest_status,
                    artifact_validation=recovered_artifact_validation,
                    pending_recovery_payload=pending_recovery_payload,
                    recovered_from_reason=dispatch_failure_reason,
                    run_id=workflow_run_id,
                    attempt_id=workflow_attempt_id,
                    attempt_number=workflow_attempt_number,
                    workspace_dir=str(session_workspace) if session_workspace is not None else workflow_workspace_dir,
                    provider_session_id=session_id,
                    max_attempts=3,
                )
                if workflow_run_id and workflow_service is not None:
                    workflow_service.mark_completed(
                        workflow_run_id,
                        attempt_id=workflow_attempt_id,
                        summary={
                            "hook_name": hook_name,
                            "status": "completed",
                            "recovered_from_reason": dispatch_failure_reason,
                            **({"video_id": expected_video_id or ""} if expected_video_id else {}),
                        },
                    )
                if admitted_turn_id and self._turn_finalizer:
                    try:
                        await self._turn_finalizer(
                            session_id,
                            admitted_turn_id,
                            "completed",
                            None,
                            execution_summary,
                        )
                    except Exception:
                        logger.exception(
                            "Failed finalizing recovered hook turn session_id=%s",
                            session_id,
                        )
                if session_workspace is not None:
                    self._write_sync_ready_marker(
                        session_id=session_id,
                        workspace_root=session_workspace,
                        state="completed",
                        ready=True,
                        hook_name=hook_name,
                        run_source=run_source,
                        started_at_epoch=start_ts,
                        completed_at_epoch=time.time(),
                        execution_summary=execution_summary,
                    )
                terminal_reason = "completed"
                return {
                    "decision": "accepted",
                    "turn_id": admitted_turn_id,
                    "status": "completed",
                    "session_id": session_id,
                    "execution_summary": execution_summary,
                    "recovered_from_reason": dispatch_failure_reason,
                    "run_id": workflow_run_id,
                    "attempt_id": workflow_attempt_id,
                }
            logger.exception(
                "Failed dispatching hook action session_key=%s session_id=%s reason=%s",
                session_key,
                session_id,
                dispatch_failure_reason,
            )
            if session_workspace is not None:
                state = {
                    "tool_calls": 0,
                    "duration_seconds": round(max(0.0, time.time() - (start_ts or time.time())), 3),
                }
                self._write_sync_ready_marker(
                    session_id=session_id,
                    workspace_root=session_workspace,
                    state="dispatch_interrupted" if is_interrupted_dispatch else "dispatch_failed",
                    ready=True,
                    hook_name=hook_name,
                    run_source=run_source,
                    started_at_epoch=start_ts,
                    completed_at_epoch=time.time(),
                    error=dispatch_failure_reason,
                    execution_summary=state,
                )
            if self._turn_finalizer:
                try:
                    state = {
                        "tool_calls": 0,
                        "duration_seconds": round(max(0.0, time.time() - (start_ts or time.time())), 3),
                    }
                    if admitted_turn_id:
                        await self._turn_finalizer(
                            session_id,
                            admitted_turn_id,
                            "failed",
                            dispatch_failure_reason,
                            state,
                        )
                except Exception:
                    logger.exception("Failed finalizing errored hook turn session_id=%s", session_id)
            if is_youtube_tutorial:
                self._queue_or_finalize_youtube_attempt(
                    action=action,
                    session_id=session_id,
                    session_key=session_key,
                    hook_name=hook_name,
                    expected_video_id=expected_video_id,
                    reason=dispatch_failure_reason,
                    failure_class=dispatch_failure_reason,
                    session_workspace=session_workspace,
                    started_at_epoch=start_ts,
                    workflow_run_id=workflow_run_id,
                    workflow_attempt_id=workflow_attempt_id,
                    workflow_attempt_number=workflow_attempt_number,
                )
            elif workflow_profile is not None:
                self._queue_or_finalize_generic_hook_attempt(
                    action=action,
                    hook_name=hook_name,
                    reason=dispatch_failure_reason,
                    failure_class=dispatch_failure_reason,
                    session_workspace=session_workspace,
                    workflow_profile=workflow_profile,
                    workflow_run_id=workflow_run_id,
                    workflow_attempt_id=workflow_attempt_id,
                )
            return {
                "decision": "failed",
                "turn_id": admitted_turn_id,
                "reason": dispatch_failure_reason,
                "error": str(exc),
                "session_id": session_id,
                "run_id": workflow_run_id,
                "attempt_id": workflow_attempt_id,
            }
        finally:
            if self._run_counter_finish:
                try:
                    try:
                        self._run_counter_finish(session_id, run_source, terminal_reason)
                    except TypeError:
                        self._run_counter_finish(session_id, run_source)
                except Exception:
                    logger.exception("Failed finishing hook run counter session_id=%s", session_id)
            if dispatch_gate_acquired:
                self._agent_dispatch_gate.release()
            if pending_admitted:
                async with self._agent_dispatch_state_lock:
                    self._agent_dispatch_pending_count = max(
                        0,
                        self._agent_dispatch_pending_count - 1,
                    )
            # Release video-level dispatch dedup guard
            if is_youtube_tutorial and expected_video_id:
                self._youtube_video_dispatch_inflight.pop(expected_video_id, None)

    @staticmethod
    def _run_log_progress_marker(run_log_path: Path) -> tuple[int, float]:
        try:
            stat = run_log_path.stat()
            return int(stat.st_size), float(stat.st_mtime)
        except Exception:
            return (0, 0.0)

    async def _run_gateway_execute_with_watchdogs(
        self,
        *,
        session,
        request: GatewayRequest,
        workspace_root: Optional[Path],
        total_timeout_seconds: Optional[int],
        idle_timeout_seconds: Optional[int],
    ) -> dict[str, Any]:
        consume_task = asyncio.create_task(self._consume_gateway_execute(session, request, workspace_root=workspace_root))
        started = time.monotonic()
        last_progress = started
        run_log_path = (
            (workspace_root / "run.log").resolve() if workspace_root is not None else None
        )
        last_marker = (
            self._run_log_progress_marker(run_log_path)
            if run_log_path is not None
            else (0, 0.0)
        )
        poll_seconds = 5.0

        try:
            while True:
                now = time.monotonic()
                if total_timeout_seconds is not None:
                    elapsed = now - started
                    remaining = float(total_timeout_seconds) - elapsed
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    wait_seconds = min(poll_seconds, remaining)
                else:
                    wait_seconds = poll_seconds

                try:
                    return await asyncio.wait_for(asyncio.shield(consume_task), timeout=wait_seconds)
                except asyncio.TimeoutError:
                    now = time.monotonic()
                    if total_timeout_seconds is not None and (now - started) >= float(total_timeout_seconds):
                        raise
                    if idle_timeout_seconds is not None and run_log_path is not None:
                        marker = self._run_log_progress_marker(run_log_path)
                        if marker != last_marker:
                            last_marker = marker
                            last_progress = now
                            continue
                        if (now - last_progress) >= float(idle_timeout_seconds):
                            raise HookIdleTimeout(
                                f"idle_no_progress_{int(idle_timeout_seconds)}s"
                            )
                    continue
        finally:
            if not consume_task.done():
                consume_task.cancel()
                try:
                    await consume_task
                except BaseException:
                    pass

    async def _consume_gateway_execute(
        self, session, request: GatewayRequest, *, workspace_root: Optional[Path] = None,
    ) -> dict[str, Any]:
        tool_calls: int = 0
        duration_seconds = 0.0
        started = time.time()
        reported_error_message: Optional[str] = None
        reported_timeout_message: Optional[str] = None
        iteration_status: str = ""
        text_tail: list[str] = []

        # Open run.log for append so hook-dispatched sessions can be rehydrated
        _rl_handle = None
        if workspace_root is not None:
            try:
                _rl_path = workspace_root / "run.log"
                _rl_handle = open(_rl_path, "a", encoding="utf-8")
                _ts0 = time.strftime("%H:%M:%S", time.gmtime())
                user_text = (request.user_input or "")[:500]
                _rl_handle.write(f"[{_ts0}] \U0001f464 USER: {user_text}\n")
                _rl_handle.flush()
            except Exception:
                _rl_handle = None

        def _rl_write(line: str) -> None:
            if _rl_handle:
                try:
                    _rl_handle.write(line + "\n")
                    _rl_handle.flush()
                except Exception:
                    pass

        try:
            async for event in self.gateway.execute(session, request):
                event_type = getattr(event, "type", None)
                event_name = event_type.value if hasattr(event_type, "value") else str(event_type)
                if event_name == "tool_call":
                    tool_calls += 1
                    _rl_ts = time.strftime("%H:%M:%S", time.gmtime())
                    tool_name = ""
                    if isinstance(getattr(event, "data", None), dict):
                        tool_name = str(event.data.get("tool_name") or event.data.get("name") or "")
                    _rl_write(f"[{_rl_ts}] \U0001f527 TOOL CALL: {tool_name}" if tool_name else f"[{_rl_ts}] \U0001f527 TOOL CALL")
                elif event_name == "text" and isinstance(getattr(event, "data", None), dict):
                    text = str((event.data or {}).get("text") or "").strip()
                    if text:
                        text_tail.append(text)
                        if len(text_tail) > 8:
                            text_tail = text_tail[-8:]
                elif event_name == "error" and isinstance(getattr(event, "data", None), dict):
                    message = str((event.data or {}).get("message") or "").strip()
                    detail = str((event.data or {}).get("detail") or "").strip()
                    if message or detail:
                        reported_error_message = f"{message} {detail}".strip()
                        _rl_ts = time.strftime("%H:%M:%S", time.gmtime())
                        _rl_write(f"[{_rl_ts}] ERROR: {reported_error_message[:300]}")
                elif event_name == "iteration_end" and isinstance(getattr(event, "data", None), dict):
                    data = getattr(event, "data", {}) or {}
                    duration_seconds = float(data.get("duration_seconds") or duration_seconds)
                    if isinstance(data.get("tool_calls"), int):
                        tool_calls = int(data.get("tool_calls"))
                    iteration_status = str(data.get("status") or "").strip().lower()
                    # R2: Log non-success iteration status to run.log for debugging
                    if iteration_status and iteration_status not in {"complete", "completed", "success"}:
                        _rl_ts = time.strftime("%H:%M:%S", time.gmtime())
                        _rl_write(f"[{_rl_ts}] \u26a0\ufe0f ITERATION STATUS: {iteration_status}")
                elif event_name == "status" and isinstance(getattr(event, "data", None), dict):
                    status_msg = str((event.data or {}).get("message") or "").strip()
                    if status_msg:
                        _rl_ts = time.strftime("%H:%M:%S", time.gmtime())
                        _rl_write(f"[{_rl_ts}] INFO: {status_msg[:300]}")
        finally:
            if _rl_handle:
                try:
                    _rl_ts_end = time.strftime("%H:%M:%S", time.gmtime())
                    _rl_handle.write(f"[{_rl_ts_end}] === Turn completed ({tool_calls} tool calls) ===\n")
                    # R3: Structured warning for 0-tool-call completions
                    if tool_calls == 0 and (time.time() - started) < 30:
                        _zero_tc_dur = round(time.time() - started, 1)
                        _rl_handle.write(
                            f"[{_rl_ts_end}] \u26a0\ufe0f ZERO TOOL CALLS in {_zero_tc_dur}s — "
                            f"status={iteration_status or 'unknown'} "
                            f"error={reported_error_message or 'none'}\n"
                        )
                    _rl_handle.close()
                except Exception:
                    pass
        if duration_seconds <= 0:
            duration_seconds = round(max(0.0, time.time() - started), 3)
        # R3: Structured logging for 0-tool-call completions
        if tool_calls == 0 and duration_seconds < 30:
            logger.warning(
                "Hook dispatch completed with zero tool calls in %.1fs "
                "iteration_status=%s reported_error=%s",
                duration_seconds,
                iteration_status or "unknown",
                reported_error_message or "none",
            )
        text_window = "\n".join(text_tail).lower()
        if "request timed out" in text_window or "execution timed out" in text_window:
            reported_timeout_message = "agent_response_timeout_text"
        if iteration_status and iteration_status not in {"complete", "completed", "success"}:
            reported_error_message = reported_error_message or f"iteration_status:{iteration_status}"

        summary: dict[str, Any] = {"tool_calls": tool_calls, "duration_seconds": duration_seconds}
        if iteration_status:
            summary["iteration_status"] = iteration_status
        if reported_timeout_message:
            summary["reported_timeout"] = True
            summary["reported_timeout_message"] = reported_timeout_message
        if reported_error_message:
            summary["reported_error"] = True
            summary["reported_error_message"] = reported_error_message[:500]
        return summary

    async def _abort_active_session_execution(self, session_id: str, *, reason: str) -> None:
        """Best-effort hard stop when hooks timeout/idle-timeout a running turn."""
        try:
            adapters = getattr(self.gateway, "_adapters", None)
            if not isinstance(adapters, dict):
                return
            adapter = adapters.get(session_id)
            if adapter is None:
                return
            reset_fn = getattr(adapter, "reset", None)
            if reset_fn is None:
                return
            await reset_fn()
            logger.info(
                "Hook forced adapter reset session_id=%s reason=%s",
                session_id,
                reason,
            )
        except Exception:
            logger.exception(
                "Hook failed adapter reset session_id=%s reason=%s",
                session_id,
                reason,
            )

    async def _resolve_or_create_webhook_session(self, session_id: str, workspace_dir: str):
        try:
            return await self.gateway.resume_session(session_id)
        except ValueError:
            resolved_workspace = Path(str(workspace_dir or "")).resolve()
            logger.info("Creating webhook session session_id=%s workspace=%s", session_id, resolved_workspace)
            return await self.gateway.create_session(
                user_id="webhook",
                workspace_dir=str(resolved_workspace),
                session_id=session_id,
            )

    def _evict_stale_video_dispatch_entries(self) -> None:
        """Remove expired entries from the video dispatch inflight dict."""
        now = time.time()
        ttl = float(self._youtube_video_dispatch_dedup_ttl_seconds)
        stale = [
            vid for vid, ts in self._youtube_video_dispatch_inflight.items()
            if (now - ts) >= ttl
        ]
        for vid in stale:
            self._youtube_video_dispatch_inflight.pop(vid, None)

    def _session_id_from_key(self, session_key: str) -> str:
        raw = session_key.strip()
        if not raw:
            digest = hashlib.sha256(str(time.time()).encode("utf-8")).hexdigest()[:12]
            return f"{HOOK_SESSION_ID_PREFIX}{digest}"

        safe = SESSION_ID_SANITIZE_RE.sub("_", raw)
        safe = safe.strip("._-")
        if not safe:
            safe = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]

        session_id = f"{HOOK_SESSION_ID_PREFIX}{safe}"
        if len(session_id) <= MAX_SESSION_ID_LEN:
            return session_id

        suffix = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:12]
        keep = MAX_SESSION_ID_LEN - len(HOOK_SESSION_ID_PREFIX) - len(suffix) - 1
        keep = max(8, keep)
        trimmed = safe[:keep].rstrip("._-") or safe[:8]
        return f"{HOOK_SESSION_ID_PREFIX}{trimmed}_{suffix}"

    def _build_agent_user_input(self, action: HookAction) -> str:
        message = (action.message or "").strip()
        if not message:
            return ""
        if not action.to:
            return message

        route = (action.to or "").strip().lower()
        extra_lines: list[str] = []
        if _is_youtube_agent_route(route):
            try:
                artifacts_root = str(resolve_artifacts_dir())
            except Exception:
                artifacts_root = "<resolve_artifacts_dir_failed>"
            payload_video_id = self._extract_action_field(message, "video_id")
            payload_video_url = self._extract_action_field(message, "video_url")
            extra_lines = [
                f"Resolved artifacts root (absolute): {artifacts_root}",
                "Path rule: never use a literal UA_ARTIFACTS_DIR folder name in paths.",
                "Invalid examples: /opt/universal_agent/UA_ARTIFACTS_DIR/... and UA_ARTIFACTS_DIR/...",
                f"Durable writes must use this root: {artifacts_root}/youtube-tutorial-creation/...",
                "Create required baseline artifacts first (manifest.json, README.md, CONCEPT.md).",
                "Only create runnable implementation artifacts when transcript+metadata confirm software/coding content.",
                "If transcript/video extraction fails, keep those files and set manifest status to degraded_transcript_only or failed.",
            ]
            if payload_video_id or payload_video_url:
                extra_lines.extend(
                    [
                        "Webhook payload values below are authoritative for this run.",
                        "Do not substitute video_id/video_url from memory, previous runs, or examples.",
                        f"authoritative_video_id: {payload_video_id or ''}",
                        f"authoritative_video_url: {payload_video_url or ''}",
                        "Before finalizing, ensure manifest.json uses the same authoritative video_id/video_url.",
                    ]
                )

        routing_lines = [
            f"Webhook route target: {action.to}",
            "Mandatory: delegate this run to the target subagent using Task.",
            f"Use Task(subagent_type='{action.to}', prompt='Use the webhook payload below and complete the run end-to-end.').",
            "",
            *extra_lines,
            "" if extra_lines else "",
            message,
        ]
        return "\n".join(routing_lines)

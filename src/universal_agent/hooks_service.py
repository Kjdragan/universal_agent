
import asyncio
import base64
import hashlib
import hmac
import importlib.util
import json
import logging
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx
from fastapi import Request, Response
from pydantic import BaseModel, Field

from universal_agent.artifacts import resolve_artifacts_dir
from universal_agent.gateway import InProcessGateway, GatewayRequest
from universal_agent.ops_config import load_ops_config, resolve_ops_config_path
from universal_agent.youtube_ingest import normalize_video_target

logger = logging.getLogger(__name__)

DEFAULT_HOOKS_PATH = "/hooks"
DEFAULT_HOOKS_MAX_BODY_BYTES = 256 * 1024
DEFAULT_BOOTSTRAP_HOOKS_MAX_BODY_BYTES = 1024 * 1024
DEFAULT_BOOTSTRAP_TRANSFORMS_DIR = "../webhook_transforms"
HOOK_SESSION_ID_PREFIX = "session_hook_"
MAX_SESSION_ID_LEN = 128
SESSION_ID_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
DEFAULT_SYNC_READY_MARKER_FILENAME = "sync_ready.json"
SYNC_READY_MARKER_VERSION = 1


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
    strategy: str = "token"  # token | composio_hmac | none
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


class HooksService:
    def __init__(
        self,
        gateway: InProcessGateway,
        *,
        turn_admitter: Optional[Callable[[str, GatewayRequest], Awaitable[dict[str, Any]]]] = None,
        turn_finalizer: Optional[
            Callable[[str, str, str, Optional[str], Optional[dict[str, Any]]], Awaitable[None]]
        ] = None,
        run_counter_start: Optional[Callable[[str, str], None]] = None,
        run_counter_finish: Optional[Callable[[str, str], None]] = None,
        notification_sink: Optional[Callable[[dict[str, Any]], None]] = None,
    ):
        self.gateway = gateway
        self._turn_admitter = turn_admitter
        self._turn_finalizer = turn_finalizer
        self._run_counter_start = run_counter_start
        self._run_counter_finish = run_counter_finish
        self._notification_sink = notification_sink
        self._agent_dispatch_state_lock = asyncio.Lock()
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
        self.config = self._load_config()
        self.transform_cache = {}
        self._seen_webhook_ids: Dict[str, float] = {}
        self._forward_youtube_manual_url = (os.getenv("UA_HOOKS_FORWARD_YOUTUBE_MANUAL_URL") or "").strip()
        self._forward_youtube_token = (os.getenv("UA_HOOKS_FORWARD_YOUTUBE_TOKEN") or "").strip()
        # Best-effort forwarding must not degrade primary hook handling when the
        # local stack is offline. Use a simple cooldown to avoid log spam.
        self._forward_failures = 0
        self._forward_disabled_until_ts = 0.0
        self._youtube_ingest_mode = (os.getenv("UA_HOOKS_YOUTUBE_INGEST_MODE") or "").strip().lower()
        default_ingest_url = ""
        if self._forward_youtube_manual_url.endswith("/api/v1/hooks/youtube/manual"):
            default_ingest_url = self._forward_youtube_manual_url.replace(
                "/api/v1/hooks/youtube/manual",
                "/api/v1/youtube/ingest",
            )
        self._youtube_ingest_url = (
            os.getenv("UA_HOOKS_YOUTUBE_INGEST_URL", default_ingest_url).strip()
        )
        ingest_urls_raw = (os.getenv("UA_HOOKS_YOUTUBE_INGEST_URLS") or "").strip()
        self._youtube_ingest_urls = self._parse_endpoint_list(
            ingest_urls_raw,
            fallback=self._youtube_ingest_url,
        )
        self._youtube_ingest_token = (
            os.getenv("UA_HOOKS_YOUTUBE_INGEST_TOKEN") or self._forward_youtube_token
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
        self._youtube_ingest_cooldowns: Dict[str, dict[str, Any]] = {}
        self._youtube_ingest_fail_open = self._safe_bool_env(
            "UA_HOOKS_YOUTUBE_INGEST_FAIL_OPEN", False
        )
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
                    "auth": {
                        "strategy": "composio_hmac",
                        "secret_env": "COMPOSIO_WEBHOOK_SECRET",
                        "timestamp_tolerance_seconds": 300,
                        "replay_window_seconds": 600,
                    },
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
        }

    @staticmethod
    def _session_key_from_session_id(session_id: str) -> str:
        sid = str(session_id or "").strip()
        if sid.startswith(HOOK_SESSION_ID_PREFIX):
            return sid[len(HOOK_SESSION_ID_PREFIX) :]
        return sid

    @staticmethod
    def _youtube_parts_from_session_key(session_key: str) -> tuple[str, str]:
        raw = str(session_key or "").strip()
        if not raw.startswith("yt_"):
            return "", ""
        body = raw[len("yt_") :]
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
                "mode: explainer_plus_code",
                "learning_mode: concept_plus_implementation",
                "allow_degraded_transcript_only: true",
                "Resume this tutorial run and complete artifact generation.",
            ]
        )
        return HookAction(
            kind="agent",
            name="RecoveredYouTubeWebhook",
            session_key=session_key,
            to="youtube-explainer-expert",
            message=recovery_message,
            deliver=True,
        )

    async def recover_interrupted_youtube_sessions(self, workspace_root: Path) -> int:
        if not self._startup_recovery_enabled:
            return 0
        root = Path(str(workspace_root or "")).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            return 0
        candidates: list[tuple[float, Path]] = []
        for session_dir in root.glob(f"{HOOK_SESSION_ID_PREFIX}yt_*"):
            if not session_dir.is_dir():
                continue
            try:
                mtime = float(session_dir.stat().st_mtime)
            except Exception:
                mtime = 0.0
            candidates.append((mtime, session_dir))
        candidates.sort(key=lambda item: item[0], reverse=True)

        recovered = 0
        now_epoch = time.time()
        for _, session_dir in candidates:
            if recovered >= self._startup_recovery_max_sessions:
                break
            session_id = session_dir.name
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

    def _tutorial_run_rel_path(self, run_dir: Path) -> str:
        try:
            artifacts_root = Path(str(resolve_artifacts_dir())).resolve()
            run_rel = run_dir.resolve().relative_to(artifacts_root).as_posix().strip("/")
            return run_rel
        except Exception:
            return ""

    def _tutorial_key_files_for_notification(
        self,
        *,
        run_dir: Path,
        run_rel_path: str,
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
            implementation_files = sorted(
                [
                    node
                    for node in implementation_dir.rglob("*")
                    if node.is_file() and node.suffix.lower() in {".py", ".ts", ".tsx", ".js", ".jsx", ".md"}
                ]
            )[:8]
            for node in implementation_files:
                rel_under_run = node.relative_to(run_dir).as_posix()
                rel_path = f"{run_rel_path}/{rel_under_run}" if run_rel_path else ""
                files.append(
                    {
                        "label": f"Code: {node.name}",
                        "name": node.name,
                        "path": str(node),
                        "rel_path": rel_path,
                    }
                )
        return files

    def _emit_youtube_tutorial_failure_notification(
        self,
        *,
        session_id: str,
        session_key: str,
        hook_name: str,
        expected_video_id: str,
        reason: str,
        started_at_epoch: Optional[float],
    ) -> None:
        metadata: dict[str, Any] = {
            "source": "hooks",
            "hook_name": hook_name,
            "hook_session_key": session_key,
            "video_id": expected_video_id or "",
            "reason": reason,
        }
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
            message=f"{tutorial_title}: {reason}",
            session_id=session_id,
            severity="error",
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
        cls = str(failure_class or "unknown").strip()
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
                    if context.get("_composio_replay_detected"):
                        logger.info(
                            "Hook ingress deduped replay path=%s mapping=%s",
                            subpath,
                            mapping_id,
                        )
                        return Response(
                            json.dumps({"ok": True, "deduped": True}),
                            media_type="application/json",
                            status_code=200,
                        )
                    auth_failed = True
                    logger.warning(
                        "Hook ingress auth failed path=%s mapping=%s strategy=%s",
                        subpath,
                        mapping_id,
                        (mapping.auth.strategy if mapping.auth else "token"),
                    )
                    continue

                action = await self._build_action(mapping, context)
                if action is None:
                    logger.info("Hook ingress skipped path=%s mapping=%s", subpath, mapping_id)
                    return Response(
                        json.dumps({"ok": True, "skipped": True}),
                        media_type="application/json",
                        status_code=200,
                    )

                asyncio.create_task(self._dispatch_action(action))
                asyncio.create_task(self._maybe_forward_youtube_manual(mapping_id, action))
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
        asyncio.create_task(self._dispatch_action(action))
        return True, action.kind

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

    async def _maybe_forward_youtube_manual(self, mapping_id: str, action: HookAction) -> None:
        """
        Optional YouTube hook mirroring:

        If this gateway receives a Composio YouTube playlist webhook (mapping id
        'composio-youtube-trigger'), optionally forward a normalized payload to a
        secondary UA gateway running elsewhere (typically a local dev stack).

        This is disabled unless `UA_HOOKS_FORWARD_YOUTUBE_MANUAL_URL` is set.
        """
        url = self._forward_youtube_manual_url
        if not url:
            return
        now = time.time()
        if self._forward_disabled_until_ts and now < self._forward_disabled_until_ts:
            return
        if (mapping_id or "").strip().lower() != "composio-youtube-trigger":
            return
        if action.kind != "agent" or not action.message:
            return

        video_url = self._extract_action_field(action.message, "video_url")
        if not video_url:
            return
        video_id = self._extract_action_field(action.message, "video_id")
        mode = self._extract_action_field(action.message, "mode")
        allow_degraded_raw = self._extract_action_field(action.message, "allow_degraded_transcript_only")
        allow_degraded = True
        if allow_degraded_raw:
            allow_degraded = allow_degraded_raw.strip().lower() in {"1", "true", "yes", "on"}

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._forward_youtube_token:
            headers["Authorization"] = f"Bearer {self._forward_youtube_token}"
        payload = {
            "video_url": video_url,
            "video_id": video_id,
            "mode": mode or "explainer_plus_code",
            "allow_degraded_transcript_only": allow_degraded,
        }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
            if 200 <= resp.status_code < 300:
                self._forward_failures = 0
                self._forward_disabled_until_ts = 0.0
                logger.info("Hook forward ok mapping=%s url=%s status=%s", mapping_id, url, resp.status_code)
            else:
                self._forward_failures += 1
                if self._forward_failures >= 3:
                    self._forward_disabled_until_ts = now + 300.0
                logger.warning(
                    "Hook forward failed mapping=%s url=%s status=%s body=%s",
                    mapping_id,
                    url,
                    resp.status_code,
                    (resp.text or "")[:200],
                )
        except Exception as exc:
            self._forward_failures += 1
            if self._forward_failures >= 3:
                self._forward_disabled_until_ts = now + 300.0
            logger.warning("Hook forward error mapping=%s url=%s err=%s", mapping_id, url, exc)

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

    def _is_youtube_local_ingest_target(self, action: HookAction) -> bool:
        return (
            (action.kind or "").strip().lower() == "agent"
            and (action.to or "").strip().lower() == "youtube-explainer-expert"
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
        tutorials_root = artifacts_root / "youtube-tutorial-learning"
        if not tutorials_root.exists():
            return None

        best_path: Optional[Path] = None
        best_mtime = 0.0
        threshold = float(started_at_epoch or 0.0) - 60.0

        for manifest_path in tutorials_root.rglob("manifest.json"):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(payload.get("video_id") or "").strip() != video_id:
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
        implementation_required = bool(manifest_payload.get("implementation_required")) or (
            learning_mode in {"concept_plus_implementation", "implementation", "code_only"}
            or mode in {"explainer_plus_code", "implementation", "code_only"}
            or bool(implementation_dir_hint)
        )

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
                has_impl_file = any(node.is_file() for node in implementation_dir.rglob("*"))
                if not has_impl_file:
                    missing.append("implementation/*")
        if missing:
            raise RuntimeError(f"youtube_artifacts_incomplete:{','.join(missing)}")

        run_rel_path = self._tutorial_run_rel_path(run_dir)

        return {
            "video_id": video_id,
            "run_dir": str(run_dir),
            "manifest_path": str(manifest_path),
            "run_rel_path": run_rel_path,
            "title": str(manifest_payload.get("title") or run_dir.name),
            "status": str(manifest_payload.get("status") or "full"),
            "implementation_required": implementation_required,
            "key_files": self._tutorial_key_files_for_notification(
                run_dir=run_dir,
                run_rel_path=run_rel_path,
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
            ingest_result = {
                "ok": False,
                "status": "failed",
                "error": "ingest_inflight_deduped",
                "failure_class": "inflight_duplicate",
                "detail": f"video_key={video_key}",
                "video_key": video_key,
            }
        else:
            self._youtube_ingest_inflight[video_key] = now + float(self._youtube_ingest_inflight_ttl_seconds)
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
                    if attempt_index < self._youtube_ingest_retries - 1:
                        delay_seconds = self._youtube_ingest_retry_delay(attempt_index)
                        if delay_seconds > 0:
                            await asyncio.sleep(delay_seconds)
            finally:
                self._youtube_ingest_inflight.pop(video_key, None)

        if not (ingest_result.get("ok") and str(ingest_result.get("status") or "").lower() == "succeeded"):
            failure_class = str(ingest_result.get("failure_class") or "").strip().lower()
            if failure_class in {"request_blocked", "api_unavailable"}:
                self._set_youtube_ingest_cooldown(
                    video_key=video_key,
                    failure_class=failure_class,
                    error=str(ingest_result.get("error") or "local_ingest_failed"),
                    now_epoch=time.time(),
                )

        workspace_root = Path(session_workspace).resolve()
        ingestion_dir = workspace_root / "ingestion"
        meta_path = ingestion_dir / "youtube_local_ingest_result.json"

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
            "last_result": ingest_result,
            "created_at_epoch": time.time(),
        }
        pending_path = workspace_root / "pending_local_ingest.json"
        try:
            self._write_text_file(pending_path, json.dumps(pending_payload, indent=2))
        except Exception:
            logger.warning("Failed writing pending_local_ingest marker session_id=%s", session_id)

        attempts_used = len(errors)
        max_attempts = int(self._youtube_ingest_retries)
        failure_error = str(ingest_result.get("error") or "local_ingest_failed")
        failure_class = str(ingest_result.get("failure_class") or "")
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
            "hook_youtube_ingest_pending_file": str(pending_path),
            "hook_youtube_ingest_error": failure_error,
            "hook_youtube_ingest_failure_class": failure_class,
            "hook_youtube_ingest_attempts": attempts_used,
            "hook_youtube_ingest_max_attempts": max_attempts,
            "hook_youtube_ingest_reason": failure_reason,
            "hook_youtube_ingest_video_key": video_key,
        }

        if self._youtube_ingest_fail_open:
            fail_open_lines = [
                "local_youtube_ingest_mode: local_worker",
                "local_youtube_ingest_status: failed_fail_open",
                f"local_youtube_ingest_pending_file: {pending_path}",
                f"local_youtube_ingest_error: {failure_error}",
                f"local_youtube_ingest_failure_class: {failure_class}",
                f"local_youtube_ingest_reason: {failure_reason}",
                "Local transcript ingestion failed; proceed in degraded mode and record this in manifest.",
            ]
            action = action.model_copy(
                update={"message": self._append_message_lines(action.message or "", fail_open_lines)}
            )
            return action, metadata, False

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

        if strategy == "none":
            return True
        if strategy == "composio_hmac":
            return self._verify_composio_hmac(context, auth)

        # default: token strategy
        if not self.config.token:
            # Explicitly allow open webhook mappings when no token is configured.
            return True
        token = self._extract_token(request)
        return bool(token and token == self.config.token)

    def _verify_composio_hmac(self, context: Dict, auth: HookAuthConfig) -> bool:
        headers = context.get("headers", {})
        signature = headers.get("webhook-signature") or headers.get("x-composio-signature")
        webhook_id = headers.get("webhook-id")
        webhook_timestamp_raw = headers.get("webhook-timestamp")
        secret_env = auth.secret_env or "COMPOSIO_WEBHOOK_SECRET"
        secret = os.getenv(secret_env)

        if not signature or not webhook_id or not webhook_timestamp_raw or not secret:
            return False

        received_sig = signature.strip()
        if received_sig.lower().startswith("v1,"):
            received_sig = received_sig.split(",", 1)[1].strip()
        if not received_sig:
            return False

        try:
            webhook_timestamp = int(webhook_timestamp_raw)
        except (TypeError, ValueError):
            return False

        now = int(time.time())
        if abs(now - webhook_timestamp) > auth.timestamp_tolerance_seconds:
            return False

        raw_body_text = context.get("raw_body_text", "")
        signing_string = f"{webhook_id}.{webhook_timestamp_raw}.{raw_body_text}"
        expected_sig = base64.b64encode(
            hmac.new(
                secret.encode("utf-8"),
                signing_string.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")

        if not hmac.compare_digest(received_sig, expected_sig):
            return False

        self._cleanup_seen_webhook_ids(now)
        if webhook_id in self._seen_webhook_ids:
            context["_composio_replay_detected"] = True
            return False

        self._seen_webhook_ids[webhook_id] = float(now + auth.replay_window_seconds)
        return True

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

    async def _dispatch_action(self, action: HookAction):
        logger.info("Dispatching hook action kind=%s", action.kind)
        if action.kind == "wake":
            logger.info("Wake hook action is not implemented yet; dropping action")
            return
        if action.kind != "agent":
            logger.warning("Unsupported hook action kind=%s", action.kind)
            return

        session_key = (action.session_key or "").strip()
        if not session_key:
            logger.warning("Hook agent action missing session_key")
            return

        session_id = self._session_id_from_key(session_key)
        timeout_seconds = (
            int(action.timeout_seconds)
            if action.timeout_seconds is not None
            else (self._default_hook_timeout_seconds or None)
        )
        is_youtube_tutorial = (action.to or "").strip().lower() == "youtube-explainer-expert"
        expected_video_id = self._extract_action_field(action.message or "", "video_id")
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
        if action.thinking:
            metadata["hook_thinking"] = action.thinking
        if timeout_seconds is not None:
            metadata["hook_timeout_seconds"] = timeout_seconds
        run_source = str(metadata.get("source") or "webhook").strip().lower() or "webhook"
        hook_name = action.name or "Hook"
        session_workspace: Optional[Path] = None
        admitted_turn_id: Optional[str] = None
        start_ts: Optional[float] = None
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
                self._emit_notification(
                    kind="hook_dispatch_queue_overflow",
                    title="Hook Dispatch Queue Overflow",
                    message=(
                        f"Dropped hook action for {session_id} "
                        f"(pending={candidate_pending}, limit={self._agent_dispatch_queue_limit})"
                    ),
                    session_id=session_id,
                    severity="error",
                    metadata={
                        "source": "hooks",
                        "pending": candidate_pending,
                        "limit": int(self._agent_dispatch_queue_limit),
                    },
                )
                return
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
            session = await self._resolve_or_create_webhook_session(session_id)
            session_workspace = Path(str(session.workspace_dir)).resolve()
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
                    logger.error(
                        "Hook action failed pre-dispatch session_id=%s hook=%s reason=%s",
                        session_id,
                        hook_name,
                        reason,
                    )
                    self._emit_notification(
                        kind="youtube_ingest_failed",
                        title="YouTube Ingest Failed",
                        message=reason,
                        session_id=session_id,
                        severity="error",
                        requires_action=True,
                        metadata={
                            "source": "hooks",
                            "hook_name": hook_name,
                            "hook_session_key": session_key,
                            "video_key": str(metadata.get("hook_youtube_ingest_video_key") or ""),
                            "error": str(metadata.get("hook_youtube_ingest_error") or ""),
                            "failure_class": str(metadata.get("hook_youtube_ingest_failure_class") or ""),
                            "attempts": int(metadata.get("hook_youtube_ingest_attempts") or 0),
                            "max_attempts": int(metadata.get("hook_youtube_ingest_max_attempts") or 0),
                            "pending_file": str(metadata.get("hook_youtube_ingest_pending_file") or ""),
                        },
                    )
                else:
                    logger.info(
                        "Hook action deferred session_id=%s hook=%s reason=pending_local_ingest",
                        session_id,
                        hook_name,
                    )
                return

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
                return

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
                    return

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
                tutorial_title = str(metadata.get("tutorial_title") or expected_video_id or session_key)
                self._emit_notification(
                    kind="youtube_tutorial_started",
                    title="YouTube Tutorial Pipeline Started",
                    message=f"Processing: {tutorial_title}",
                    session_id=session_id,
                    severity="info",
                    metadata={
                        "source": "hooks",
                        "hook_name": hook_name,
                        "hook_session_key": session_key,
                        "video_id": expected_video_id or "",
                    },
                )
            execution_summary: dict[str, Any] = {}
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
                    tutorial_title = str(artifact_validation.get("title") or expected_video_id or session_key)
                    run_rel_path = str(artifact_validation.get("run_rel_path") or "").strip()
                    tutorial_key_files = artifact_validation.get("key_files")
                    self._emit_notification(
                        kind="youtube_tutorial_ready",
                        title="YouTube Tutorial Artifacts Ready",
                        message=f"{tutorial_title} artifacts are ready for review.",
                        session_id=session_id,
                        severity="success",
                        requires_action=True,
                        metadata={
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
            logger.info("Hook action dispatched session_id=%s hook=%s", session_id, hook_name)
        except HookReportedTimeout as exc:
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
                self._emit_youtube_tutorial_failure_notification(
                    session_id=session_id,
                    session_key=session_key,
                    hook_name=hook_name,
                    expected_video_id=expected_video_id,
                    reason="agent_reported_timeout",
                    started_at_epoch=start_ts,
                )
        except HookIdleTimeout as exc:
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
                self._emit_youtube_tutorial_failure_notification(
                    session_id=session_id,
                    session_key=session_key,
                    hook_name=hook_name,
                    expected_video_id=expected_video_id,
                    reason=f"hook_idle_timeout_{idle_seconds}s",
                    started_at_epoch=start_ts,
                )
        except asyncio.TimeoutError:
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
                self._emit_youtube_tutorial_failure_notification(
                    session_id=session_id,
                    session_key=session_key,
                    hook_name=hook_name,
                    expected_video_id=expected_video_id,
                    reason=f"hook_timeout_{timeout_seconds}s",
                    started_at_epoch=start_ts,
                )
        except Exception:
            logger.exception(
                "Failed dispatching hook action session_key=%s session_id=%s",
                session_key,
                session_id,
            )
            if session_workspace is not None:
                state = {
                    "tool_calls": 0,
                    "duration_seconds": round(max(0.0, time.time() - (start_ts or time.time())), 3),
                }
                self._write_sync_ready_marker(
                    session_id=session_id,
                    workspace_root=session_workspace,
                    state="dispatch_failed",
                    ready=True,
                    hook_name=hook_name,
                    run_source=run_source,
                    started_at_epoch=start_ts,
                    completed_at_epoch=time.time(),
                    error="hook_dispatch_failed",
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
                            "hook_dispatch_failed",
                            state,
                        )
                except Exception:
                    logger.exception("Failed finalizing errored hook turn session_id=%s", session_id)
            if is_youtube_tutorial:
                self._emit_youtube_tutorial_failure_notification(
                    session_id=session_id,
                    session_key=session_key,
                    hook_name=hook_name,
                    expected_video_id=expected_video_id,
                    reason="hook_dispatch_failed",
                    started_at_epoch=start_ts,
                )
        finally:
            if self._run_counter_finish:
                try:
                    self._run_counter_finish(session_id, run_source)
                except Exception:
                    logger.exception("Failed decrementing hook run counter session_id=%s", session_id)
            if dispatch_gate_acquired:
                self._agent_dispatch_gate.release()
            if pending_admitted:
                async with self._agent_dispatch_state_lock:
                    self._agent_dispatch_pending_count = max(
                        0,
                        self._agent_dispatch_pending_count - 1,
                    )

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
        consume_task = asyncio.create_task(self._consume_gateway_execute(session, request))
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

    async def _consume_gateway_execute(self, session, request: GatewayRequest) -> dict[str, Any]:
        tool_calls = 0
        duration_seconds = 0.0
        started = time.time()
        reported_error_message: Optional[str] = None
        reported_timeout_message: Optional[str] = None
        iteration_status: str = ""
        text_tail: list[str] = []
        async for event in self.gateway.execute(session, request):
            event_type = getattr(event, "type", None)
            event_name = event_type.value if hasattr(event_type, "value") else str(event_type)
            if event_name == "tool_call":
                tool_calls += 1
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
            elif event_name == "iteration_end" and isinstance(getattr(event, "data", None), dict):
                data = getattr(event, "data", {}) or {}
                duration_seconds = float(data.get("duration_seconds") or duration_seconds)
                if isinstance(data.get("tool_calls"), int):
                    tool_calls = int(data.get("tool_calls"))
                iteration_status = str(data.get("status") or "").strip().lower()
        if duration_seconds <= 0:
            duration_seconds = round(max(0.0, time.time() - started), 3)
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

    async def _resolve_or_create_webhook_session(self, session_id: str):
        try:
            return await self.gateway.resume_session(session_id)
        except ValueError:
            workspace_dir = Path("AGENT_RUN_WORKSPACES") / session_id
            logger.info("Creating webhook session session_id=%s workspace=%s", session_id, workspace_dir)
            return await self.gateway.create_session(user_id="webhook", workspace_dir=str(workspace_dir))

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
        if route == "youtube-explainer-expert":
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
                f"Durable writes must use this root: {artifacts_root}/youtube-tutorial-learning/...",
                "Create required artifacts first (manifest.json, README.md, CONCEPT.md, IMPLEMENTATION.md, implementation/) before retrieval.",
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

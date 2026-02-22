
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
    ):
        self.gateway = gateway
        self._turn_admitter = turn_admitter
        self._turn_finalizer = turn_finalizer
        self._run_counter_start = run_counter_start
        self._run_counter_finish = run_counter_finish
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
        self._youtube_ingest_token = (
            os.getenv("UA_HOOKS_YOUTUBE_INGEST_TOKEN") or self._forward_youtube_token
        ).strip()
        self._youtube_ingest_timeout_seconds = self._safe_int_env(
            "UA_HOOKS_YOUTUBE_INGEST_TIMEOUT_SECONDS", 120
        )
        self._youtube_ingest_retries = max(
            1, self._safe_int_env("UA_HOOKS_YOUTUBE_INGEST_RETRY_ATTEMPTS", 3)
        )
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
        self._sync_ready_marker_enabled = self._safe_bool_env(
            "UA_HOOKS_SYNC_READY_MARKER_ENABLED", True
        )
        self._sync_ready_marker_filename = (
            (os.getenv("UA_HOOKS_SYNC_READY_MARKER_FILENAME") or "").strip()
            or DEFAULT_SYNC_READY_MARKER_FILENAME
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
            "youtube_ingest_url_configured": bool(self._youtube_ingest_url),
            "youtube_ingest_fail_open": bool(self._youtube_ingest_fail_open),
            "youtube_ingest_min_chars": int(self._youtube_ingest_min_chars),
            "youtube_ingest_retry_attempts": int(self._youtube_ingest_retries),
            "youtube_ingest_retry_delay_seconds": float(self._youtube_ingest_retry_delay_seconds),
            "youtube_ingest_retry_jitter_seconds": float(self._youtube_ingest_retry_jitter_seconds),
            "youtube_ingest_cooldown_seconds": int(self._youtube_ingest_cooldown_seconds),
            "hook_default_timeout_seconds": int(self._default_hook_timeout_seconds or 0),
            "sync_ready_marker_enabled": bool(self._sync_ready_marker_enabled),
            "sync_ready_marker_filename": str(self._sync_ready_marker_filename),
        }

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
        if not self._youtube_ingest_url:
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

        try:
            timeout = httpx.Timeout(timeout=float(max(5, self._youtube_ingest_timeout_seconds + 10)))
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(self._youtube_ingest_url, headers=headers, json=payload)
            if 200 <= resp.status_code < 300:
                try:
                    data = resp.json()
                except Exception:
                    data = {"ok": False, "status": "failed", "error": "invalid_json_response"}
                if isinstance(data, dict):
                    data.setdefault("http_status", resp.status_code)
                    return data
                return {"ok": False, "status": "failed", "error": "invalid_response_shape"}
            return {
                "ok": False,
                "status": "failed",
                "error": "ingest_http_error",
                "http_status": resp.status_code,
                "detail": (resp.text or "")[:2000],
            }
        except Exception as exc:
            return {"ok": False, "status": "failed", "error": "ingest_request_error", "detail": str(exc)}

    def _write_text_file(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _sync_ready_marker_path(self, workspace_root: Path) -> Path:
        marker_name = self._sync_ready_marker_filename.strip() or DEFAULT_SYNC_READY_MARKER_FILENAME
        return workspace_root / marker_name

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
                "hook_youtube_ingest_transcript_file": str(transcript_path),
                "hook_youtube_ingest_video_key": video_key,
            }
            return action, metadata, False

        pending_payload = {
            "status": "pending_local_ingest",
            "session_id": session_id,
            "video_url": video_url,
            "video_id": video_id,
            "video_key": video_key,
            "ingest_url": self._youtube_ingest_url,
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

        metadata = {
            "hook_youtube_ingest_mode": "local_worker",
            "hook_youtube_ingest_status": "pending_local_ingest",
            "hook_youtube_ingest_pending_file": str(pending_path),
            "hook_youtube_ingest_error": str(ingest_result.get("error") or "local_ingest_failed"),
            "hook_youtube_ingest_failure_class": str(ingest_result.get("failure_class") or ""),
            "hook_youtube_ingest_video_key": video_key,
        }

        if self._youtube_ingest_fail_open:
            fail_open_lines = [
                "local_youtube_ingest_mode: local_worker",
                "local_youtube_ingest_status: failed_fail_open",
                f"local_youtube_ingest_pending_file: {pending_path}",
                f"local_youtube_ingest_error: {str(ingest_result.get('error') or 'local_ingest_failed')}",
                f"local_youtube_ingest_failure_class: {str(ingest_result.get('failure_class') or '')}",
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
                if session_workspace is not None:
                    self._write_sync_ready_marker(
                        session_id=session_id,
                        workspace_root=session_workspace,
                        state="pending_local_ingest",
                        ready=False,
                        hook_name=hook_name,
                        run_source=run_source,
                    )
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
            execution_summary: dict[str, Any] = {}
            if timeout_seconds is None:
                execution_summary = await self._consume_gateway_execute(session, request)
            else:
                execution_summary = await asyncio.wait_for(
                    self._consume_gateway_execute(session, request),
                    timeout=float(timeout_seconds),
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
        except asyncio.TimeoutError:
            logger.error(
                "Hook action timed out session_key=%s session_id=%s timeout_seconds=%s",
                session_key,
                session_id,
                timeout_seconds,
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
        finally:
            if self._run_counter_finish:
                try:
                    self._run_counter_finish(session_id, run_source)
                except Exception:
                    logger.exception("Failed decrementing hook run counter session_id=%s", session_id)

    async def _consume_gateway_execute(self, session, request: GatewayRequest) -> dict[str, Any]:
        tool_calls = 0
        duration_seconds = 0.0
        started = time.time()
        async for event in self.gateway.execute(session, request):
            event_type = getattr(event, "type", None)
            event_name = event_type.value if hasattr(event_type, "value") else str(event_type)
            if event_name == "tool_call":
                tool_calls += 1
            elif event_name == "iteration_end" and isinstance(getattr(event, "data", None), dict):
                data = getattr(event, "data", {}) or {}
                duration_seconds = float(data.get("duration_seconds") or duration_seconds)
                if isinstance(data.get("tool_calls"), int):
                    tool_calls = int(data.get("tool_calls"))
        if duration_seconds <= 0:
            duration_seconds = round(max(0.0, time.time() - started), 3)
        return {"tool_calls": tool_calls, "duration_seconds": duration_seconds}

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

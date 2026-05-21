"""Universal ZAI HTTP observability.

P7 (2026-05-21): closes the instrumentation gap from P4 (#402). The
ZAIRateLimiter only catches calls that go through `with_rate_limit_retry`
— ~2 files in the codebase. At least 8+ other files call ZAI directly
via httpx, bypassing the rate-limiter entirely. Confirmed via two ZAI
429s on prod at 2026-05-21 06:10:53 UTC that did NOT update the P4
snapshot.

This module monkey-patches `httpx.Client.__init__` and
`httpx.AsyncClient.__init__` at process start so every outbound HTTP
request automatically gets an event hook installed. The hook filters
by host — only requests to `api.z.ai` (and friends) are captured —
keeping the events file focused.

Captures per request:
- Timestamp, method, URL path, status code
- Response-time milliseconds
- Rate-limit headers: `retry-after`, `x-ratelimit-remaining`, `x-ratelimit-limit`
- Body snippet on non-2xx (first 500 bytes)
- Caller attribution via stack walk (which UA module issued the call)
- Category: ok / rate_limited_429 / fup_signal / server_error_5xx /
  client_error_4xx

Rolling JSONL buffer at `AGENT_RUN_WORKSPACES/zai_inference_events.jsonl`
with `UA_ZAI_EVENTS_MAX_LINES` cap (default 10000 — ~3 days at current
load levels). Periodic trim keeps file size bounded.

Install once at process start via `install_zai_observability()`. Called
from `infisical_loader.initialize_runtime_secrets()` so every UA process
(gateway, cron subprocess, heartbeat daemon, briefings_agent) is
instrumented without per-file changes.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
import traceback
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# ── Configuration ──────────────────────────────────────────────────────────

ZAI_HOSTS = frozenset({
    "api.z.ai",
    "open.bigmodel.cn",
})


EVENT_CATEGORY_OK = "ok"
EVENT_CATEGORY_RATE_LIMITED = "rate_limited_429"
EVENT_CATEGORY_FUP = "fup_signal"
EVENT_CATEGORY_SERVER_ERROR = "server_error_5xx"
EVENT_CATEGORY_CLIENT_ERROR = "client_error_4xx"


def _events_max_lines() -> int:
    try:
        return max(100, int(os.getenv("UA_ZAI_EVENTS_MAX_LINES", "10000")))
    except ValueError:
        return 10000


def _trim_threshold_ratio() -> float:
    try:
        return max(0.05, float(os.getenv("UA_ZAI_EVENTS_TRIM_RATIO", "0.25")))
    except ValueError:
        return 0.25


def _events_path() -> Path:
    env_override = os.getenv("UA_ZAI_EVENTS_PATH")
    if env_override:
        return Path(env_override)
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "AGENT_RUN_WORKSPACES" / "zai_inference_events.jsonl"


# ── Classification ─────────────────────────────────────────────────────────

def _fup_keywords() -> frozenset[str]:
    try:
        from universal_agent.rate_limiter import FUP_KEYWORDS
        return FUP_KEYWORDS
    except Exception:  # noqa: BLE001
        return frozenset({
            "fair use",
            "fair-use",
            "fup",
            "policy violation",
            "policy-violation",
            "abuse",
            "concurrency limit",
            "weekly limit",
            "account suspended",
            "account flagged",
            "1313",
        })


def _is_fup_body(body: str) -> bool:
    if not body:
        return False
    lower = body.lower()
    return any(kw in lower for kw in _fup_keywords())


def _classify_response(status: int, body_snippet: str, reason_phrase: str = "") -> str:
    if _is_fup_body(body_snippet):
        return EVENT_CATEGORY_FUP
    if status == 429:
        return EVENT_CATEGORY_RATE_LIMITED
    if 500 <= status < 600:
        return EVENT_CATEGORY_SERVER_ERROR
    if 400 <= status < 500:
        return EVENT_CATEGORY_CLIENT_ERROR
    return EVENT_CATEGORY_OK


def _is_zai_url(url: str) -> bool:
    if not url:
        return False
    return any(h in url for h in ZAI_HOSTS)


# ── Caller attribution ────────────────────────────────────────────────────

_SKIP_FRAME_FRAGMENTS = (
    "/httpx/",
    "/anyio/",
    "/asyncio/",
    "zai_observability.py",
)


def _identify_caller() -> str:
    try:
        stack = traceback.extract_stack()
        for frame in reversed(stack):
            filename = frame.filename
            if any(skip in filename for skip in _SKIP_FRAME_FRAGMENTS):
                continue
            if "universal_agent" in filename:
                parts = filename.split("universal_agent/")
                if len(parts) > 1:
                    return f"universal_agent/{parts[-1]}"
            if "site-packages" in filename:
                continue
            parts = filename.split("/")
            if len(parts) >= 2:
                return "/".join(parts[-2:])
            return filename
    except Exception:  # noqa: BLE001
        return "unknown"
    return "unknown"


# ── Event persistence ─────────────────────────────────────────────────────

def _trim_events_file(path: Path, max_lines: int) -> None:
    try:
        if not path.exists():
            return
        with open(path) as f:
            lines = f.readlines()
        if len(lines) <= max_lines:
            return
        keep = lines[-max_lines:]
        with open(path, "w") as f:
            f.writelines(keep)
    except Exception as exc:  # noqa: BLE001
        logger.debug("zai_observability trim failed: %s", exc)


def _append_event(event: dict) -> None:
    try:
        path = _events_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")
        if random.random() < 0.02:
            max_lines = _events_max_lines()
            ratio = _trim_threshold_ratio()
            size_estimate = path.stat().st_size
            if size_estimate > max_lines * 500 * (1 + ratio):
                _trim_events_file(path, max_lines)
    except Exception as exc:  # noqa: BLE001
        logger.debug("zai_observability append failed: %s", exc)


# ── httpx hooks ───────────────────────────────────────────────────────────

_REQUEST_START_EXT_KEY = "zai_obs_started_at"


def _on_request_sync(request: httpx.Request) -> None:
    request.extensions[_REQUEST_START_EXT_KEY] = time.monotonic()


async def _on_request_async(request: httpx.Request) -> None:
    request.extensions[_REQUEST_START_EXT_KEY] = time.monotonic()


def _capture(request: httpx.Request, response: httpx.Response) -> None:
    if not _is_zai_url(str(request.url)):
        return
    started_at = request.extensions.get(_REQUEST_START_EXT_KEY)
    response_time_ms: Optional[float] = None
    if started_at is not None:
        response_time_ms = round((time.monotonic() - started_at) * 1000.0, 1)

    body_snippet = ""
    if response.status_code >= 400:
        try:
            raw = response.text
            body_snippet = raw[:500] if raw else ""
        except Exception:  # noqa: BLE001
            body_snippet = ""

    event = {
        "ts": time.time(),
        "method": request.method,
        "url_path": request.url.path,
        "host": request.url.host,
        "status": response.status_code,
        "response_time_ms": response_time_ms,
        "retry_after": response.headers.get("retry-after"),
        "ratelimit_remaining": response.headers.get("x-ratelimit-remaining"),
        "ratelimit_limit": response.headers.get("x-ratelimit-limit"),
        "category": _classify_response(response.status_code, body_snippet, response.reason_phrase or ""),
        "caller": _identify_caller(),
    }
    if body_snippet:
        event["body_snippet"] = body_snippet

    _append_event(event)


def _on_response_sync(response: httpx.Response) -> None:
    try:
        _capture(response.request, response)
    except Exception as exc:  # noqa: BLE001
        logger.debug("zai_observability sync capture failed: %s", exc)


async def _on_response_async(response: httpx.Response) -> None:
    try:
        _capture(response.request, response)
    except Exception as exc:  # noqa: BLE001
        logger.debug("zai_observability async capture failed: %s", exc)


# ── Install hooks ─────────────────────────────────────────────────────────

# Capture the ORIGINAL httpx __init__ once at module-import time. If
# install_zai_observability() is called multiple times (e.g. tests that
# reset the _INSTALLED flag), the patched init always calls into the
# pristine original — no recursion.
_ORIG_SYNC_INIT = httpx.Client.__init__
_ORIG_ASYNC_INIT = httpx.AsyncClient.__init__

_INSTALLED = False


def _patched_sync_init(self, *args, **kwargs):
    hooks = dict(kwargs.pop("event_hooks", None) or {})
    request_hooks = list(hooks.get("request") or [])
    response_hooks = list(hooks.get("response") or [])
    if _on_request_sync not in request_hooks:
        request_hooks.append(_on_request_sync)
    if _on_response_sync not in response_hooks:
        response_hooks.append(_on_response_sync)
    hooks["request"] = request_hooks
    hooks["response"] = response_hooks
    kwargs["event_hooks"] = hooks
    _ORIG_SYNC_INIT(self, *args, **kwargs)


def _patched_async_init(self, *args, **kwargs):
    hooks = dict(kwargs.pop("event_hooks", None) or {})
    request_hooks = list(hooks.get("request") or [])
    response_hooks = list(hooks.get("response") or [])
    if _on_request_async not in request_hooks:
        request_hooks.append(_on_request_async)
    if _on_response_async not in response_hooks:
        response_hooks.append(_on_response_async)
    hooks["request"] = request_hooks
    hooks["response"] = response_hooks
    kwargs["event_hooks"] = hooks
    _ORIG_ASYNC_INIT(self, *args, **kwargs)


def install_zai_observability() -> bool:
    """Monkey-patch httpx.Client/AsyncClient __init__ so every new client
    automatically carries the observability hooks. Idempotent across
    process lifetime — first call installs, subsequent calls are no-op."""
    global _INSTALLED
    if _INSTALLED:
        return False
    httpx.Client.__init__ = _patched_sync_init
    httpx.AsyncClient.__init__ = _patched_async_init
    _INSTALLED = True
    logger.info("zai_observability: hooks installed on httpx.Client + httpx.AsyncClient")
    return True

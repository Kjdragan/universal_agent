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
- Body snippet on non-2xx (first 500 bytes). At response-hook time with a
  real transport the body is NOT yet read, so `response.text` raises
  `httpx.ResponseNotRead`. The hook therefore force-reads the body
  (`response.read()` / `await response.aread()`) for status >= 400 before
  capturing — without this the snippet was empty on EVERY production event,
  which silently blinded both the `fup_texted` flag and the `fup_signal`
  category. Streaming success responses are never force-read.
- Model id parsed from the request JSON body (the Anthropic-compatible POST
  payload). `"unknown"` when the body is absent / non-JSON / unread.
- Caller attribution via stack walk (which UA module issued the call)
- Category: ok / rate_limited_429 / fup_signal / server_error_5xx /
  client_error_4xx. NOTE: ZAI's ordinary throttle is a 429 whose body carries
  code `[1313]` + Fair-Usage-Policy text (verified prod 2026-06-11, 1058/1058
  over 12h), so a 1313-texted 429 is `rate_limited_429` (gradient), NOT
  `fup_signal`. `fup_signal` (cliff) is reserved for FUP-keyword bodies on
  NON-429 responses (e.g. a 403 suspension).
- `fup_texted` (bool): true when the body matches the FUP keyword set whatever
  the status — preserves 1313-throttle visibility orthogonal to `category`.

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
from pathlib import Path
import random
import time
import traceback
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
    """Classify a ZAI response into an event category.

    IMPORTANT (verified on prod 2026-06-11, journalctl 12h sample, 1058/1058):
    ZAI's ORDINARY throttle response IS a 429 whose body carries code ``[1313]``
    and the Fair-Usage-Policy text — there is no separate gentle-429 vs
    harsh-FUP wire signal. So a 1313-texted 429 is the rate-limit GRADIENT, NOT
    the cliff: a status==429 always classifies as ``rate_limited_429`` even when
    the body matches FUP keywords. ``fup_signal`` (the genuine cliff — back off
    is not enough, the account is in trouble) is reserved for FUP-keyword bodies
    on NON-429 responses, e.g. a 403 suspension text. The orthogonal
    ``fup_texted`` event field (set in ``_capture``) preserves visibility into
    1313-texted throttle without conflating it with the cliff category.
    """
    if status == 429:
        return EVENT_CATEGORY_RATE_LIMITED
    if _is_fup_body(body_snippet):
        return EVENT_CATEGORY_FUP
    if 500 <= status < 600:
        return EVENT_CATEGORY_SERVER_ERROR
    if 400 <= status < 500:
        return EVENT_CATEGORY_CLIENT_ERROR
    return EVENT_CATEGORY_OK


def _is_zai_url(url: str) -> bool:
    if not url:
        return False
    return any(h in url for h in ZAI_HOSTS)


def _model_from_request(request: httpx.Request) -> str:
    """Parse the model id out of the request's JSON body.

    The Anthropic-compatible POST payload is JSON with a top-level
    ``"model"`` key. We use full ``json.loads`` rather than a regex —
    prompts inside the body can mention model names, and the parse cost
    is low-ms riding on a multi-second LLM call. Fail-soft: ANY problem
    (non-JSON, empty, ``httpx.RequestNotRead`` from a streaming request)
    yields ``"unknown"`` so the event is still captured.
    """
    try:
        raw = request.content  # bytes; raises RequestNotRead for streaming bodies
        if not raw:
            return "unknown"
        parsed = json.loads(raw)
        model = parsed.get("model") if isinstance(parsed, dict) else None
        if isinstance(model, str) and model.strip():
            return model.strip()
        return "unknown"
    except Exception:  # noqa: BLE001 — fail-soft, never break the hook
        return "unknown"


# ── Caller attribution ────────────────────────────────────────────────────

# Frame-path fragments to skip when walking the stack. These are framework
# / SDK internals that we want to walk PAST to find the actual UA caller
# that issued the ZAI request.
_SKIP_FRAME_FRAGMENTS = (
    "/httpx/",
    "/anyio/",
    "/asyncio/",
    "/.venv/",                 # any virtualenv path
    "/site-packages/",         # any pip-installed dependency
    "/anthropic/",             # Anthropic SDK (used for ZAI's Anthropic-compatible endpoint)
    "/openai/",                # OpenAI SDK (used for ZAI's OpenAI-compatible endpoint)
    "/google/genai/",          # google-genai SDK
    "/google/generativeai/",   # legacy google-generativeai
    "zai_observability.py",
)


def _identify_caller() -> str:
    """Walk the stack to find the first frame OUTSIDE framework/SDK code.
    Returns a path relative to universal_agent/ if the caller is a UA
    source file. Best-effort — never raises.

    Note: the universal_agent project tree contains `.venv/` so naive
    `"universal_agent" in filename` checks match SDK frames inside the
    venv. The skip list above filters those out so we land on the
    actual UA module that issued the call (e.g. cody_implementation,
    mission_control_tier1, briefings_agent).
    """
    try:
        stack = traceback.extract_stack()
        for frame in reversed(stack):
            filename = frame.filename
            if any(skip in filename for skip in _SKIP_FRAME_FRAGMENTS):
                continue
            # Only treat as a UA-source frame if it's actually under the
            # source tree, not the venv. The skip list already excludes
            # .venv/ and site-packages, so any frame matching
            # "universal_agent/" here is a real UA-source file.
            if "/universal_agent/" in filename:
                parts = filename.split("/universal_agent/")
                if len(parts) > 1:
                    return f"universal_agent/{parts[-1]}"
            # Non-UA, non-framework frame (e.g. user script, REPL).
            parts = filename.split("/")
            if len(parts) >= 2:
                return "/".join(parts[-2:])
            return filename
    except Exception:  # noqa: BLE001
        return "unknown"
    return "unknown"


# ── Event persistence ─────────────────────────────────────────────────────

def _trim_events_file(path: Path, max_lines: int) -> None:
    """Trim the events file to its last ``max_lines`` lines, atomically.

    Writes the kept lines to a sibling ``.tmp`` file then ``os.replace``s it
    over the original — mirroring ``rate_limiter.py::_persist_snapshot`` so a
    concurrent reader/appender never sees a truncated or half-written file.

    Best-effort semantics still apply: appends from other processes that land
    BETWEEN the read and the replace are lost (they go to the old inode which
    the replace discards). That is the accepted trade-off — the alternative
    in-place rewrite could leave the file corrupt mid-write, which is worse.
    """
    try:
        if not path.exists():
            return
        with open(path) as f:
            lines = f.readlines()
        if len(lines) <= max_lines:
            return
        keep = lines[-max_lines:]
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w") as f:
            f.writelines(keep)
        os.replace(tmp, path)
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


class ZAIGloballyPausedError(httpx.RequestError):
    """Raised from the request hook to ABORT an outbound ``api.z.ai`` request
    while the operator control plane (``services/zai_control``) has a global
    pause engaged. An ``httpx.RequestError`` subclass so it surfaces to callers
    as an ordinary request failure (handled / retried-as-non-429 / fallen-back),
    not an unexpected crash. This is the 100%-coverage emergency stop — it fires
    in the request hook installed on EVERY httpx client, so it catches the
    direct-httpx ZAI callers the rate limiter never sees."""

    def __init__(self, message: str, *, request: httpx.Request | None = None) -> None:
        # httpx.RequestError requires a request; pass it when we have one.
        if request is not None:
            super().__init__(message, request=request)
        else:
            super().__init__(message)


def _global_pause_active() -> bool:
    """True iff the control plane has a global pause engaged. FAILS OPEN
    (False) on any error — a control-read problem must never block traffic."""
    try:
        from universal_agent.services import zai_control

        paused, _info = zai_control.is_globally_paused()
        return bool(paused)
    except Exception:  # noqa: BLE001 — fail open
        return False


def _enforce_global_pause(request: httpx.Request) -> None:
    """Abort the request if it targets ZAI and a global pause is active.
    Only ZAI-host requests are ever affected; all other traffic is untouched."""
    try:
        if not _is_zai_url(str(request.url)):
            return
    except Exception:  # noqa: BLE001 — URL parse issue: do not block
        return
    if _global_pause_active():
        raise ZAIGloballyPausedError(
            "ZAI requests globally paused by operator control plane "
            "(services/zai_control). Clear the pause or wait for its TTL.",
            request=request,
        )


def _on_request_sync(request: httpx.Request) -> None:
    _enforce_global_pause(request)
    request.extensions[_REQUEST_START_EXT_KEY] = time.monotonic()


async def _on_request_async(request: httpx.Request) -> None:
    _enforce_global_pause(request)
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

    model = _model_from_request(request)

    # `fup_texted` is orthogonal to `category`: it marks ANY response whose body
    # matches the FUP keyword set (incl. ZAI's standard 1313-texted 429 throttle),
    # while `category` reserves `fup_signal` for the NON-429 cliff. Lets the
    # monitor/watchdog see 1313 throttle pressure without it counting as a cliff.
    fup_texted = _is_fup_body(body_snippet)

    event = {
        "ts": time.time(),
        "method": request.method,
        "url_path": request.url.path,
        "host": request.url.host,
        "status": response.status_code,
        "model": model,
        "response_time_ms": response_time_ms,
        "retry_after": response.headers.get("retry-after"),
        "ratelimit_remaining": response.headers.get("x-ratelimit-remaining"),
        "ratelimit_limit": response.headers.get("x-ratelimit-limit"),
        "category": _classify_response(response.status_code, body_snippet, response.reason_phrase or ""),
        "fup_texted": fup_texted,
        "caller": _identify_caller(),
    }
    if body_snippet:
        event["body_snippet"] = body_snippet

    _append_event(event)


def _on_response_sync(response: httpx.Response) -> None:
    try:
        # At response-hook time with a real transport the body is not yet
        # read, so `_capture`'s `response.text` raises `httpx.ResponseNotRead`
        # and the snippet comes back empty — which darkens FUP classification.
        # Force-read error bodies here (httpx caches content, so downstream
        # consumption is unaffected). Never force-read streaming successes.
        if response.status_code >= 400:
            try:
                response.read()
            except Exception:  # noqa: BLE001 — fail-soft; capture still runs
                pass
        _capture(response.request, response)
    except Exception as exc:  # noqa: BLE001
        logger.debug("zai_observability sync capture failed: %s", exc)


async def _on_response_async(response: httpx.Response) -> None:
    try:
        # See `_on_response_sync` — same error-body force-read, async variant.
        if response.status_code >= 400:
            try:
                await response.aread()
            except Exception:  # noqa: BLE001 — fail-soft; capture still runs
                pass
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

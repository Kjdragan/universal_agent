"""
Fetcher strategy: selects and escalates across Scrapling fetcher tiers.

Tier 1 — Fetcher (basic HTTP + TLS fingerprint spoofing)
Tier 2 — DynamicFetcher (full Playwright Chromium, JS rendering)
Tier 3 — StealthyFetcher (Camoufox stealth browser, Cloudflare bypass)

The strategy starts at the lowest tier and automatically escalates when
bot-detection signals are detected in the response.
"""

from __future__ import annotations

from copy import deepcopy
import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class FetcherLevel(IntEnum):
    BASIC = 1       # Fetcher — fast HTTP with browser TLS impersonation
    DYNAMIC = 2     # DynamicFetcher — Playwright Chromium
    STEALTHY = 3    # StealthyFetcher — Camoufox, Cloudflare Turnstile solver


# HTML fragments that signal a bot-detection wall
_BOT_SIGNALS = [
    "cf-browser-verification",
    "cf_chl_opt",
    "challenge-form",
    "captcha",
    "just a moment",
    "checking your browser",
    "ddos-guard",
    "ray id",
    "under ddos protection",
    "please enable javascript",
    "enable cookies",
    "blocked",
    "access denied",
    "__cf_bm",
    "turnstile",
    "_cf_",
]

_BOT_DETECTION_STATUS_CODES = {403, 429, 503, 520, 521, 522, 523, 524, 525, 526, 527}


@dataclass
class ScrapeRequest:
    """Single URL scrape request with per-URL overrides."""
    url: str
    # Force a minimum fetcher level (skip tiers below this)
    min_level: FetcherLevel = FetcherLevel.BASIC
    # Force a specific level (no escalation)
    force_level: Optional[FetcherLevel] = None
    # StealthyFetcher: attempt to solve Cloudflare Turnstile
    solve_cloudflare: bool = True
    # Browser fetchers: run headless
    headless: bool = True
    # Wait for network idle before parsing
    network_idle: bool = True
    # Max seconds to wait per page
    timeout: float = 30.0
    # Optional CSS selector to wait for before parsing
    wait_selector: Optional[str] = None
    # Optional proxy string e.g. "http://user:pass@host:port"
    proxy: Optional[str] = None
    # Extra headers to send
    extra_headers: Optional[dict[str, str]] = None
    # Additional kwargs forwarded to the fetcher
    extra_kwargs: dict[str, Any] = field(default_factory=dict)


def _is_bot_blocked(page: Any) -> bool:
    """Return True if the page response indicates bot-detection."""
    try:
        status = getattr(page, "status", 200)
        if status in _BOT_DETECTION_STATUS_CODES:
            return True
        body = ""
        try:
            body = (page.body or b"").decode("utf-8", errors="ignore").lower()
        except Exception:
            try:
                body = str(page.text or "").lower()
            except Exception as fallback_exc:
                logger.debug(
                    "Could not extract body text for bot detection on %s: %s",
                    getattr(page, "url", "unknown URL"),
                    fallback_exc,
                )
        return any(sig in body for sig in _BOT_SIGNALS)
    except Exception as e:
        logger.warning("Error during bot detection for %s: %s. Assuming blocked.", getattr(page, 'url', 'unknown URL'), e)
        return True


def _safe_fetch_basic(req: ScrapeRequest) -> Any:
    """Tier 1: Fetcher — fast HTTP with TLS fingerprint impersonation via curl_cffi."""
    from scrapling.fetchers import Fetcher

    kwargs: dict[str, Any] = {
        # Impersonate a real Chrome browser TLS + headers fingerprint
        "impersonate": "chrome",
        # Auto-generate realistic browser headers + Google referer spoof
        "stealthy_headers": True,
        # Timeout is in seconds for Fetcher
        "timeout": req.timeout,
        "retries": 2,
        "retry_delay": 1,
    }
    if req.extra_headers:
        kwargs["headers"] = req.extra_headers
    if req.proxy:
        kwargs["proxy"] = req.proxy
    kwargs.update(req.extra_kwargs)

    logger.debug("[BASIC] GET %s", req.url)
    page = Fetcher.get(req.url, **kwargs)
    return page


def _safe_fetch_dynamic(req: ScrapeRequest) -> Any:
    """Tier 2: DynamicFetcher — full Playwright Chromium, JS rendering."""
    from scrapling.fetchers import DynamicFetcher

    # NOTE: DynamicFetcher timeout is in *milliseconds*
    timeout_ms = int(req.timeout * 1000)

    kwargs: dict[str, Any] = {
        "headless": req.headless,
        "network_idle": req.network_idle,
        "timeout": timeout_ms,
        "disable_resources": True,   # block images/fonts/media for speed
        "google_search": True,       # set Google referer for each request
        "retries": 2,
        "retry_delay": 2,
    }
    if req.wait_selector:
        kwargs["wait_selector"] = req.wait_selector
        kwargs["wait_selector_state"] = "visible"
    if req.proxy:
        kwargs["proxy"] = req.proxy
    if req.extra_headers:
        kwargs["extra_headers"] = req.extra_headers
    kwargs.update(req.extra_kwargs)

    logger.debug("[DYNAMIC] fetch %s", req.url)
    page = DynamicFetcher.fetch(req.url, **kwargs)
    return page


def _safe_fetch_stealthy(req: ScrapeRequest) -> Any:
    """Tier 3: StealthyFetcher — Patchright + full Cloudflare Turnstile solver."""
    from scrapling.fetchers import StealthyFetcher

    # NOTE: StealthyFetcher timeout is also in *milliseconds*
    timeout_ms = int(req.timeout * 1000)

    kwargs: dict[str, Any] = {
        "headless": req.headless,
        "network_idle": req.network_idle,
        "timeout": timeout_ms,
        # Cloudflare Turnstile/Interstitial auto-solve
        "solve_cloudflare": req.solve_cloudflare,
        # Anti-fingerprinting measures
        "block_webrtc": True,        # prevent local IP leak via WebRTC
        "hide_canvas": True,         # random noise on canvas ops
        "allow_webgl": True,         # keep WebGL — disabling is detectable
        "disable_resources": True,
        "google_search": True,
        "retries": 3,
        "retry_delay": 3,
    }
    if req.wait_selector:
        kwargs["wait_selector"] = req.wait_selector
        kwargs["wait_selector_state"] = "visible"
    if req.proxy:
        kwargs["proxy"] = req.proxy
    if req.extra_headers:
        kwargs["extra_headers"] = req.extra_headers
    kwargs.update(req.extra_kwargs)

    logger.debug("[STEALTHY] fetch %s", req.url)
    page = StealthyFetcher.fetch(req.url, **kwargs)
    return page


class FetcherStrategy:
    """
    Escalating fetcher strategy.

    Usage::

        strategy = FetcherStrategy()
        page, level = strategy.fetch(ScrapeRequest(url="https://example.com"))
    """

    def __init__(self, escalation_delay: float = 2.0) -> None:
        """
        Args:
            escalation_delay: Seconds to wait before retrying at a higher tier.
        """
        self.escalation_delay = escalation_delay
        self._last_fetch_trace: dict[str, Any] = {}

    @property
    def last_fetch_trace(self) -> dict[str, Any]:
        """Telemetry for the most recent `fetch` call."""
        return deepcopy(self._last_fetch_trace)

    @staticmethod
    def _coerce_level(value: Any) -> Optional[FetcherLevel]:
        if isinstance(value, FetcherLevel):
            return value
        if isinstance(value, IntEnum):
            try:
                return FetcherLevel(int(value))
            except Exception:
                return None
        if isinstance(value, int):
            try:
                return FetcherLevel(value)
            except Exception:
                return None
        if isinstance(value, str):
            candidate = value.strip().upper()
            if not candidate:
                return None
            try:
                return FetcherLevel[candidate]
            except Exception:
                try:
                    return FetcherLevel(int(candidate))
                except Exception:
                    return None
        return None

    @classmethod
    def _extract_level(cls, obj: Any) -> Optional[FetcherLevel]:
        if obj is None:
            return None
        for attr in ("fetcher_level", "level", "tier", "fetcher_tier"):
            coerced = cls._coerce_level(getattr(obj, attr, None))
            if coerced is not None:
                return coerced
        return None

    def fetch(self, req: ScrapeRequest) -> tuple[Any, FetcherLevel]:
        """
        Fetch *req.url* using the appropriate fetcher tier.

        Returns:
            (page, level_used) — Scrapling Response and the tier that succeeded.

        Raises:
            RuntimeError: if all tiers fail.
        """
        start_level = req.force_level or req.min_level
        tiers: list[tuple[FetcherLevel, Any]] = [
            (FetcherLevel.BASIC, _safe_fetch_basic),
            (FetcherLevel.DYNAMIC, _safe_fetch_dynamic),
            (FetcherLevel.STEALTHY, _safe_fetch_stealthy),
        ]
        allowed_tiers = [
            (level, fetch_fn)
            for level, fetch_fn in tiers
            if level >= start_level and (req.force_level is None or level == req.force_level)
        ]

        last_exc: Optional[Exception] = None
        last_page: Any = None
        last_page_level: Optional[FetcherLevel] = None
        trace: dict[str, Any] = {
            "url": req.url,
            "requested_min_level": req.min_level.name,
            "forced_level": req.force_level.name if req.force_level else None,
            "attempts": [],
            "escalations": [],
            "result": "unknown",
            "final_level": None,
            "total_fetch_ms": 0.0,
        }
        fetch_started = time.perf_counter()

        for idx, (level, fetch_fn) in enumerate(allowed_tiers):
            attempt_started = time.perf_counter()
            attempt: dict[str, Any] = {
                "tier": level.name,
                "status_code": None,
                "outcome": "unknown",
                "duration_ms": 0.0,
                "error_type": None,
                "error_message": None,
            }
            try:
                page = fetch_fn(req)
                attempt["status_code"] = getattr(page, "status", None)
                if _is_bot_blocked(page):
                    attempt["outcome"] = "blocked"
                    logger.info(
                        "[%s] Bot-detection detected for %s — escalating",
                        level.name, req.url,
                    )
                    last_page = page
                    last_page_level = level
                    if idx < len(allowed_tiers) - 1:
                        next_level = allowed_tiers[idx + 1][0]
                        trace["escalations"].append(
                            {
                                "from_tier": level.name,
                                "to_tier": next_level.name,
                                "reason": "bot_blocked",
                                "delay_s": self.escalation_delay,
                            }
                        )
                        attempt["duration_ms"] = round((time.perf_counter() - attempt_started) * 1000, 3)
                        trace["attempts"].append(attempt)
                        time.sleep(self.escalation_delay)
                    else:
                        attempt["duration_ms"] = round((time.perf_counter() - attempt_started) * 1000, 3)
                        trace["attempts"].append(attempt)
                    continue
            except Exception as exc:
                attempt["outcome"] = "error"
                attempt["error_type"] = exc.__class__.__name__
                attempt["error_message"] = str(exc)
                logger.warning(
                    "[%s] Error fetching %s: %s — escalating",
                    level.name, req.url, exc,
                )
                last_exc = exc
                if idx < len(allowed_tiers) - 1:
                    next_level = allowed_tiers[idx + 1][0]
                    trace["escalations"].append(
                        {
                            "from_tier": level.name,
                            "to_tier": next_level.name,
                            "reason": "fetch_error",
                            "delay_s": self.escalation_delay,
                        }
                    )
                    attempt["duration_ms"] = round((time.perf_counter() - attempt_started) * 1000, 3)
                    trace["attempts"].append(attempt)
                    time.sleep(self.escalation_delay)
                else:
                    attempt["duration_ms"] = round((time.perf_counter() - attempt_started) * 1000, 3)
                    trace["attempts"].append(attempt)
            else:
                attempt["outcome"] = "success"
                attempt["duration_ms"] = round((time.perf_counter() - attempt_started) * 1000, 3)
                trace["attempts"].append(attempt)
                trace["result"] = "success"
                trace["final_level"] = level.name
                trace["total_fetch_ms"] = round((time.perf_counter() - fetch_started) * 1000, 3)
                self._last_fetch_trace = trace
                logger.info("[%s] Successfully fetched %s", level.name, req.url)
                return page, level

        # All tiers exhausted — return best partial result or raise
        if last_page is not None:
            level_used = (
                req.force_level
                or self._extract_level(last_page)
                or last_page_level
                or self._extract_level(last_exc)
                or FetcherLevel.STEALTHY
            )
            trace["result"] = "partial_blocked"
            trace["final_level"] = level_used.name
            trace["total_fetch_ms"] = round((time.perf_counter() - fetch_started) * 1000, 3)
            self._last_fetch_trace = trace
            logger.warning("All tiers blocked for %s; returning partial result", req.url)
            return last_page, level_used
        trace["result"] = "failed"
        trace["final_level"] = None
        trace["total_fetch_ms"] = round((time.perf_counter() - fetch_started) * 1000, 3)
        if last_exc is not None:
            trace["final_error_type"] = last_exc.__class__.__name__
            trace["final_error_message"] = str(last_exc)
        self._last_fetch_trace = trace
        raise RuntimeError(
            f"All fetcher tiers failed for {req.url}"
        ) from last_exc

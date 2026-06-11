"""
Centralized rate limiter for ZAI API calls.

This module provides a singleton rate limiter that:
- Enforces global concurrency limits across all components
- Uses adaptive backoff that adjusts based on 429 frequency
- Adds jitter to prevent thundering herd
- Logs rate limit events to logfire for monitoring

Configuration (environment variables):
    ZAI_MAX_CONCURRENT: Max parallel requests (default: 2)
    ZAI_INITIAL_BACKOFF: Initial backoff floor in seconds (default: 1.0)
    ZAI_MAX_BACKOFF: Maximum backoff cap in seconds (default: 30.0)
    ZAI_MIN_INTERVAL: Minimum seconds between request starts (default: 0.5)
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import random
import threading
import time
from typing import Any, TypeVar

try:
    import logfire
except ImportError:
    logfire = None

T = TypeVar("T")

logger = logging.getLogger(__name__)


# Fair-Use-Policy / concurrency-violation keywords that bump a generic 429
# or 4xx into the CRITICAL "respond NOW" tier per operator 2026-05-20.
# Lowercase matching. Refine after first real FUP response from ZAI.
FUP_KEYWORDS = frozenset({
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


def _is_fup_error(error_str: str) -> bool:
    if not error_str:
        return False
    lower = error_str.lower()
    return any(kw in lower for kw in FUP_KEYWORDS)


class _LoopPrimitives:
    """The asyncio primitives the limiter needs, bound to ONE event loop.

    CPython 3.10+ asyncio primitives bind (lazily, on first contended use)
    to the loop that uses them; using them from another loop raises
    ``RuntimeError: ... is bound to a different event loop``. The limiter
    is a process-global singleton shared by code running on more than one
    loop — the convergence subprocess creates a fresh ``asyncio.run()``
    loop per LLM call, and the gateway can run sync background work on a
    Starlette threadpool thread (its own loops) while the main loop
    serves. So primitives live in a per-loop bundle instead of on the
    singleton directly.

    NOTE: this module must stay free of universal_agent imports at call
    time — it is imported by low-level seams (llm_classifier) and any
    UA import here risks cycles.
    """

    __slots__ = ("semaphore", "request_lock")

    def __init__(self, max_concurrent: int) -> None:
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.request_lock = asyncio.Lock()


def _get_state_path() -> Path:
    """Where the rate-limiter persists its snapshot.

    Daemon subprocesses (heartbeat, csi-ingester) get a freshly-imported
    `rate_limiter` module with a fresh singleton instance — they need to
    read state from disk, not memory. The watchdog probe lives in a
    subprocess too.
    """
    env = os.getenv("UA_ZAI_INFERENCE_STATE_PATH")
    if env:
        return Path(env)
    # rate_limiter.py is at src/universal_agent/rate_limiter.py;
    # repo root is parents[2].
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "AGENT_RUN_WORKSPACES" / "zai_inference_state.json"


class ZAIRateLimiter:
    """
    Centralized rate limiter for ZAI API calls.

    Features:
    - Global concurrency limit (default 3)
    - Adaptive backoff that increases floor after repeated 429s
    - Staggered release with jitter to prevent thundering herd
    - Shared state across all callers
    - Logfire instrumentation for monitoring
    """

    _instance: "ZAIRateLimiter | None" = None
    _lock: "asyncio.Lock | None" = None

    def __init__(self, max_concurrent: int | None = None) -> None:
        # Config from environment
        # Default to 2 concurrent - ZAI rate limits are strict
        self._max_concurrent = max_concurrent or int(os.getenv("ZAI_MAX_CONCURRENT", "2"))
        self._initial_backoff = float(os.getenv("ZAI_INITIAL_BACKOFF", "1.0"))
        self._max_backoff = float(os.getenv("ZAI_MAX_BACKOFF", "30.0"))

        # State
        self._backoff_floor = self._initial_backoff
        self._last_429_time = 0.0
        self._last_success_time = 0.0
        self._consecutive_429s = 0
        self._total_429s = 0
        self._total_requests = 0
        # FUP tracking (P4 2026-05-20): critical-immediate tier for
        # fair-use-policy / concurrency-violation signals from ZAI.
        self._total_fup_events = 0
        self._last_fup_time = 0.0
        self._last_fup_snippet = ""
        self._last_fup_context = ""

        # Minimum inter-request spacing to avoid burst rate limits
        self._min_request_interval = float(os.getenv("ZAI_MIN_INTERVAL", "0.5"))
        self._last_request_time = 0.0

        # Per-loop asyncio primitives (see _LoopPrimitives). Guarded by a
        # threading.Lock because bundles can be created from different
        # threads (gateway main loop vs Starlette threadpool loops).
        self._loop_primitives: dict[asyncio.AbstractEventLoop, _LoopPrimitives] = {}
        self._primitives_guard = threading.Lock()
        # Diagnostic: counts the times a second LIVE loop used the limiter
        # concurrently (cross-thread pattern — caps then hold per-loop, not
        # globally; loud-log so the source pattern gets fixed).
        self._cross_loop_conflicts = 0

        # Counters/floors are mutated only in synchronous critical sections
        # (no awaits), so a threading.Lock is both sufficient and immune to
        # event-loop binding — and it protects cross-thread use, which an
        # asyncio.Lock never did.
        self._state_lock = threading.Lock()

        if logfire:
            logfire.info(
                "zai_rate_limiter_initialized",
                max_concurrent=self._max_concurrent,
                initial_backoff=self._initial_backoff,
                max_backoff=self._max_backoff,
            )

    @classmethod
    def get_instance(cls: type["ZAIRateLimiter"], max_concurrent: int | None = None) -> "ZAIRateLimiter":
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls(max_concurrent)
        return cls._instance

    @classmethod
    def reset_instance(cls: type["ZAIRateLimiter"]) -> None:
        """Reset the singleton (useful for testing)."""
        cls._instance = None

    def _get_loop_primitives(self) -> _LoopPrimitives:
        """Return the asyncio-primitive bundle for the RUNNING loop,
        creating it on first use.

        Why per-loop: the singleton is shared across loops — sequentially
        in the convergence subprocess (a fresh ``asyncio.run()`` loop per
        LLM call via `proactive_convergence.py`'s sync→async bridges) and
        potentially CONCURRENTLY in the gateway (a Starlette threadpool
        thread running ``asyncio.run()`` while the main loop serves).
        In-place primitive swaps would hand out fresh full-cap semaphores
        on every loop change (cap silently unenforced under exactly the
        concurrent pattern); per-loop bundles keep each loop's cap intact.

        Trade-off, on purpose: when two loops are live at once, each
        enforces ``max_concurrent`` independently (process-wide admission
        is bounded by ``cap × live loops``, not ``cap``). That pattern is
        a bug at the call site — we count it (``cross_loop_conflicts`` in
        the snapshot) and loud-log it so it gets fixed at the source.

        Closed loops are pruned on the next bundle creation, so one-shot
        ``asyncio.run()`` loops don't accumulate. Must be called from a
        running loop.
        """
        loop = asyncio.get_running_loop()
        with self._primitives_guard:
            prims = self._loop_primitives.get(loop)
            if prims is None:
                live = [lp for lp in self._loop_primitives if not lp.is_closed()]
                for stale in [lp for lp in self._loop_primitives if lp.is_closed()]:
                    del self._loop_primitives[stale]
                if live:
                    self._cross_loop_conflicts += 1
                    msg = (
                        "ZAIRateLimiter: a second LIVE event loop is using the "
                        "limiter concurrently (%d conflict(s) so far) — caps now "
                        "enforce per-loop, not process-wide. Fix the caller: move "
                        "LLM-bearing work off threadpool asyncio.run() bridges."
                    )
                    logger.warning(msg, self._cross_loop_conflicts)
                    if logfire:
                        logfire.warn(
                            "zai_rate_limiter_cross_loop_conflict",
                            conflicts=self._cross_loop_conflicts,
                            live_loops=len(live) + 1,
                        )
                prims = _LoopPrimitives(self._max_concurrent)
                self._loop_primitives[loop] = prims
            return prims

    async def record_429(self, context: str = "") -> None:
        """
        Called when a 429 is received. Adjusts adaptive backoff.

        Args:
            context: Optional context string for logging (e.g., section name)
        """
        with self._state_lock:
            now = time.time()
            self._total_429s += 1

            # If 429s are happening within 10s of each other, they're related
            if now - self._last_429_time < 10:
                self._consecutive_429s += 1
                # Raise floor after repeated 429s (max 8s floor)
                self._backoff_floor = min(8.0, self._initial_backoff * (1.5 ** self._consecutive_429s))
            else:
                self._consecutive_429s = 1
                self._backoff_floor = self._initial_backoff

            self._last_429_time = now

            if logfire:
                logfire.warn(
                    "zai_rate_limit_hit",
                    context=context,
                    consecutive_429s=self._consecutive_429s,
                    total_429s=self._total_429s,
                    backoff_floor=self._backoff_floor,
                )
            self._persist_snapshot()

    async def record_success(self) -> None:
        """Called on successful request. Gradually lowers backoff floor."""
        with self._state_lock:
            self._total_requests += 1
            self._last_success_time = time.time()
            # Slowly decay the floor back to initial
            self._backoff_floor = max(self._initial_backoff, self._backoff_floor * 0.9)
            self._consecutive_429s = max(0, self._consecutive_429s - 1)
            self._persist_snapshot()

    async def record_fup_signal(self, context: str = "", error_snippet: str = "") -> None:
        """Called when a Fair-Use-Policy / concurrency-violation signal is
        detected from ZAI. Distinct from 429 because the response is to
        STOP, not retry. The watchdog escalates this as CRITICAL with
        no grace period.
        """
        with self._state_lock:
            now = time.time()
            self._total_fup_events += 1
            self._last_fup_time = now
            self._last_fup_snippet = (error_snippet or "")[:500]
            self._last_fup_context = (context or "")[:200]
            if logfire:
                logfire.error(
                    "zai_fup_signal",
                    context=context,
                    total_fup_events=self._total_fup_events,
                    error_snippet=self._last_fup_snippet,
                )
            logger.error(
                "ZAI FUP signal detected — context=%s snippet=%r total=%d",
                context,
                self._last_fup_snippet,
                self._total_fup_events,
            )
            self._persist_snapshot()

    def _persist_snapshot(self) -> None:
        """Atomic write of current state to the snapshot JSON.

        Called under `_state_lock` from each `record_*` method. Sync I/O
        on a ~1 KB file is microsecond-scale; no event-loop concern.
        Atomic via temp-file + os.replace so the watchdog never reads a
        half-written file.
        """
        path = _get_state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "max_concurrent": self._max_concurrent,
                "backoff_floor": self._backoff_floor,
                "consecutive_429s": self._consecutive_429s,
                "total_429s": self._total_429s,
                "total_requests": self._total_requests,
                "total_fup_events": self._total_fup_events,
                "last_429_at": self._last_429_time or None,
                "last_success_at": self._last_success_time or None,
                "last_fup_at": self._last_fup_time or None,
                "last_fup_snippet": self._last_fup_snippet,
                "last_fup_context": self._last_fup_context,
                "cross_loop_conflicts": self._cross_loop_conflicts,
                "snapshot_written_at": time.time(),
            }
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload))
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001 — never crash the rate limiter over a snapshot write
            logger.warning("rate_limiter snapshot persist failed", exc_info=True)

    def get_backoff(self, attempt: int) -> float:
        """
        Calculate backoff with jitter. Uses adaptive floor.

        Args:
            attempt: Zero-indexed attempt number

        Returns:
            Backoff duration in seconds
        """
        base = self._backoff_floor * (2 ** attempt)
        jitter = random.uniform(0.1, 0.5) * base
        return min(base + jitter, self._max_backoff)

    @asynccontextmanager
    async def acquire(self, context: str = "") -> AsyncIterator[None]:
        """
        Acquire a slot for an API call.
        Enforces both concurrent limit AND minimum inter-request spacing.

        Args:
            context: Optional context string for logging
        """
        # The bundle is captured locally so release always pairs with the
        # semaphore actually acquired — never a swapped attribute.
        prims = self._get_loop_primitives()
        await prims.semaphore.acquire()
        try:
            # Enforce minimum spacing between ALL requests (not just concurrent)
            # This prevents burst rate limits from sliding window quotas.
            # (`request_lock` is per-loop; `_last_request_time` is shared, so
            # spacing still coordinates across loops — a lost-update race
            # between loops costs only slightly-off jittered spacing.)
            async with prims.request_lock:
                now = time.time()
                elapsed = now - self._last_request_time
                if elapsed < self._min_request_interval:
                    wait_time = self._min_request_interval - elapsed
                    # Add small jitter to prevent exact synchronization
                    wait_time += random.uniform(0.05, 0.15)
                    await asyncio.sleep(wait_time)
                self._last_request_time = time.time()
            yield
        finally:
            prims.semaphore.release()

    def get_stats(self) -> dict[str, float]:
        """Return current rate limiter statistics."""
        return {
            "max_concurrent": self._max_concurrent,
            "backoff_floor": self._backoff_floor,
            "consecutive_429s": self._consecutive_429s,
            "total_429s": self._total_429s,
            "total_requests": self._total_requests,
        }


async def with_rate_limit_retry(
    func: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = 5,
    context: str = "",
    **kwargs: Any,
) -> T:
    """
    Execute an async function with rate limit handling.

    This is a convenience wrapper that:
    1. Acquires a slot from the rate limiter
    2. Executes the function
    3. Handles 429 errors with adaptive backoff
    4. Records success/failure for adaptive tuning

    Args:
        func: Async function to execute
        *args: Positional arguments for func
        max_retries: Maximum retry attempts (default: 5)
        context: Context string for logging
        **kwargs: Keyword arguments for func

    Returns:
        Result of func

    Raises:
        Last exception if all retries exhausted
    """
    limiter = ZAIRateLimiter.get_instance()
    last_error = None

    for attempt in range(max_retries):
        async with limiter.acquire(context):
            try:
                result = await func(*args, **kwargs)
                await limiter.record_success()
                return result
            except Exception as e:
                error_str = str(e)
                error_lower = error_str.lower()

                # P4 (2026-05-20): FUP detection takes precedence over generic
                # 429 handling. FUP = stop, do NOT retry — retrying makes the
                # ban risk worse. Watchdog escalates as critical.
                if _is_fup_error(error_lower):
                    await limiter.record_fup_signal(context, error_str)
                    raise

                is_rate_limit = "429" in error_lower or "too many requests" in error_lower

                if is_rate_limit:
                    await limiter.record_429(context)
                    last_error = e

                    if attempt < max_retries - 1:
                        delay = limiter.get_backoff(attempt)
                        print(f"  ⚠️ [429] Rate limited ({context}). Backoff: {delay:.1f}s (Attempt {attempt+1}/{max_retries})")
                        await asyncio.sleep(delay)
                        continue
                else:
                    # Non-rate-limit error, don't retry
                    raise

    # All retries exhausted
    if last_error:
        raise last_error
    raise RuntimeError(f"Rate limit retries exhausted for {context}")

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
import collections
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import random
import sys
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


def _is_429_error(error_str: str) -> bool:
    """Is this error a rate-limit (429) response?

    Checked BEFORE the FUP keywords on purpose: ZAI delivers its STANDARD
    throttle as a 429 whose body carries code 1313 + the Fair-Usage-Policy
    text (verified 2026-06-11: 1058/1058 journald 429s over 12h carried
    '1313'). A 1313-texted 429 is therefore the rate-limit GRADIENT (back
    off, retry, shrink the tier cap) — NOT the account-level cliff. The
    cliff is FUP text on a non-429 response (suspension etc.) or gradient
    saturation (429s persisting at minimum caps).
    """
    if not error_str:
        return False
    lower = error_str.lower()
    return "429" in lower or "too many requests" in lower


class ZAIFupPauseError(RuntimeError):
    """Raised by ``ZAIRateLimiter.acquire`` while the account-level FUP
    acquire-pause is active. Fail-fast on purpose: callers already fail
    closed per item (idempotent re-runs), and sleeping here would wedge
    cron budgets while holding nothing useful."""


# ── Per-tier adaptive concurrency (AIMD) ───────────────────────────────────
#
# One dynamic concurrency cap per model tier (opus/sonnet/mid/haiku),
# TCP-congestion-control style: seed conservative, additive increase on a
# sustained clean streak, multiplicative decrease (halve) at most once per
# congestion EVENT (i.e. only if the tier has seen a success since its last
# decrease — a wall-clock cooldown would serially halve through one retry
# saga's spread-out 429s), and an account-level slam+pause on a genuine
# FUP/cliff signal. Throughput is the SUM of the per-tier caps; cheap tiers
# get more concurrency by design.
TIERS = ("opus", "sonnet", "mid", "haiku")

# tier -> (start cap, min cap, max cap). Overridable per tier via
# ZAI_TIER_CAP_<TIER> / ZAI_TIER_CAP_MIN_<TIER> / ZAI_TIER_CAP_MAX_<TIER>.
# Code defaults are the durable config (the VPS .env is wiped on deploy).
_TIER_CAP_DEFAULTS: dict[str, tuple[int, int, int]] = {
    "opus": (1, 1, 3),
    "sonnet": (2, 1, 5),
    "mid": (3, 1, 5),
    "haiku": (4, 1, 6),
}


def _tier_env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


class _AdaptiveGate:
    """An asyncio counting gate whose capacity is read LIVE on every
    admission decision — ``holders < cap_fn()`` — so cap changes are plain
    int writes with no debt bookkeeping (the adversarially-reviewed
    debt-counter design could deadlock a tier when a decrease landed
    before the semaphore was lazily created).

    Wake semantics mirror ``asyncio.Semaphore`` permit-transfer: the waker
    counts the woken waiter as a holder before setting its future, so a
    woken waiter returns without re-checking (no fast-path barging past
    parked waiters, no re-park livelock). A cap DECREASE simply stops new
    admissions until holders drain below the new cap; a cap INCREASE takes
    effect on the next release/acquire (cross-loop waiter notification is
    deliberately not attempted — futures must only be touched from their
    own loop, and increases follow successes, which release a slot
    microseconds later anyway).

    Loop-bound like any asyncio primitive: instances live in the per-loop
    ``_LoopPrimitives`` bundle.
    """

    __slots__ = ("_cap_fn", "_holders", "_waiters")

    def __init__(self, cap_fn: Callable[[], int]) -> None:
        self._cap_fn = cap_fn
        self._holders = 0
        self._waiters: collections.deque[asyncio.Future[bool]] = collections.deque()

    def _effective_cap(self) -> int:
        # Never below 1: a zero/negative cap would wedge the tier forever.
        return max(1, int(self._cap_fn()))

    def locked(self) -> bool:
        return self._holders >= self._effective_cap() or any(
            not f.done() for f in self._waiters
        )

    async def acquire(self) -> bool:
        if not self.locked():
            self._holders += 1
            return True
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        try:
            try:
                await fut
            finally:
                self._waiters.remove(fut)
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                # Granted a slot, then cancelled before resuming: give the
                # slot back and pass the wake along.
                self._holders -= 1
                self._wake_next()
            raise
        return True

    def release(self) -> None:
        self._holders -= 1
        self._wake_next()

    def _wake_next(self) -> None:
        cap = self._effective_cap()
        for fut in self._waiters:
            if self._holders >= cap:
                return
            if not fut.done():
                self._holders += 1  # permit transferred to the woken waiter
                fut.set_result(True)


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

    __slots__ = ("semaphore", "request_lock", "tier_gates")

    def __init__(self, max_concurrent: int) -> None:
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.request_lock = asyncio.Lock()
        # tier -> _AdaptiveGate, created lazily on first tier acquire in
        # this loop. The gate reads the limiter's live tier cap.
        self.tier_gates: dict[str, _AdaptiveGate] = {}

    def get_tier_gate(self, tier: str, limiter: "ZAIRateLimiter") -> _AdaptiveGate:
        gate = self.tier_gates.get(tier)
        if gate is None:
            gate = _AdaptiveGate(lambda: limiter._effective_tier_cap(tier))
            self.tier_gates[tier] = gate
        return gate


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
        self._last_cross_loop_time = 0.0

        # Counters/floors are mutated only in synchronous critical sections
        # (no awaits), so a threading.Lock is both sufficient and immune to
        # event-loop binding — and it protects cross-thread use, which an
        # asyncio.Lock never did.
        self._state_lock = threading.Lock()

        # ── Per-tier AIMD state ────────────────────────────────────────
        self._tier_cap: dict[str, int] = {}
        self._tier_min: dict[str, int] = {}
        self._tier_max: dict[str, int] = {}
        for tier, (start, lo, hi) in _TIER_CAP_DEFAULTS.items():
            t_up = tier.upper()
            self._tier_min[tier] = _tier_env_int(f"ZAI_TIER_CAP_MIN_{t_up}", lo)
            self._tier_max[tier] = max(
                self._tier_min[tier], _tier_env_int(f"ZAI_TIER_CAP_MAX_{t_up}", hi)
            )
            self._tier_cap[tier] = min(
                self._tier_max[tier],
                max(self._tier_min[tier], _tier_env_int(f"ZAI_TIER_CAP_{t_up}", start)),
            )
        self._tier_backoff_floor = {t: self._initial_backoff for t in TIERS}
        self._tier_consecutive_429s = dict.fromkeys(TIERS, 0)
        self._tier_last_429 = dict.fromkeys(TIERS, 0.0)
        self._tier_clean_streak = dict.fromkeys(TIERS, 0)
        self._tier_last_increase = dict.fromkeys(TIERS, 0.0)
        self._tier_last_decrease = dict.fromkeys(TIERS, 0.0)
        # One multiplicative decrease per congestion EVENT: halve only if
        # the tier has succeeded since its last decrease (classic AIMD —
        # a wall-clock cooldown would serially halve through one retry
        # saga's spread-out 429s). Seeded True so the first-ever 429 falls.
        self._tier_success_since_decrease = dict.fromkeys(TIERS, True)
        self._tier_increase_count = dict.fromkeys(TIERS, 0)
        self._tier_decrease_count = dict.fromkeys(TIERS, 0)
        self._tier_total_429s = dict.fromkeys(TIERS, 0)
        self._tier_total_requests = dict.fromkeys(TIERS, 0)

        # AIMD knobs (code defaults are the durable config).
        self._increase_streak = _tier_env_int("ZAI_TIER_INCREASE_STREAK", 20)
        self._increase_quiet_s = float(os.getenv("ZAI_TIER_INCREASE_QUIET_SECONDS", "60"))
        self._increase_cooldown_s = float(os.getenv("ZAI_TIER_INCREASE_COOLDOWN_SECONDS", "120"))
        self._fup_freeze_s = float(os.getenv("ZAI_FUP_FREEZE_SECONDS", "1800"))
        self._fup_pause_s = float(os.getenv("ZAI_FUP_ACQUIRE_PAUSE_SECONDS", "180"))
        self._saturation_429s = _tier_env_int("ZAI_TIER_SATURATION_429S", 6)
        # Saturation only escalates to the cliff when a logical call has
        # actually EXHAUSTED its retries recently — raw consecutive-429
        # counts alone are reachable in ~11s by two overlapping retry sagas
        # interleaving through a cap-1 gate (their wire 429s land <10s
        # apart), which is routine throttle, not the cliff.
        self._saturation_exhaustion_window_s = float(
            os.getenv("ZAI_TIER_SATURATION_EXHAUSTION_WINDOW_SECONDS", "120")
        )

        # Account-level cliff state: freeze gates additive INCREASES;
        # pause fail-fasts new acquires (the actual brake). Consecutive
        # cliffs (within an hour of each other) grow the pause
        # exponentially up to ZAI_FUP_FREEZE_SECONDS — under SUSTAINED
        # saturation a fixed 180s pause would thrash (pause → fail → page)
        # many times per hour.
        self._freeze_until = 0.0
        self._acquire_pause_until = 0.0
        self._consecutive_cliffs = 0
        self._last_cliff_time = 0.0
        # Logical-outcome counters (wire 429s amplify under retries; the
        # watchdog/monitor need outcomes, not raw wire counts).
        self._total_429s_exhausted = 0
        self._total_succeeded_after_retry = 0
        self._last_exhausted_time = 0.0
        self._created_at = time.time()

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
                    self._last_cross_loop_time = time.time()
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

    def _effective_tier_cap(self, tier: str) -> int:
        """The cap a tier gate enforces right now: an operator control-plane
        override (``services/zai_control``) wins over the AIMD-managed cap;
        otherwise the AIMD cap stands. Fails OPEN to the AIMD cap on any
        control-read error."""
        ai_cap = self._tier_cap[tier]
        try:
            from universal_agent.services import zai_control

            return zai_control.effective_tier_cap(tier, ai_cap, self._tier_max[tier])
        except Exception:  # noqa: BLE001 — fail open to the autotuner's cap
            return ai_cap

    def _normalize_tier(self, model_tier: str | None) -> str | None:
        if model_tier is None:
            return None
        tier = str(model_tier).strip().lower()
        return tier if tier in TIERS else "sonnet"

    def _set_tier_cap(self, tier: str, new_cap: int, reason: str) -> None:
        """Apply a tier-cap change with the transition loud-logged.

        journald/logfire transition lines are the PRIMARY channel for
        verifying the controller in production — the snapshot file is
        last-writer-wins across processes and cannot be trusted alone.
        Must be called under ``_state_lock``.
        """
        old = self._tier_cap[tier]
        new_cap = min(self._tier_max[tier], max(self._tier_min[tier], int(new_cap)))
        if new_cap == old:
            return
        self._tier_cap[tier] = new_cap
        if new_cap > old:
            self._tier_increase_count[tier] += 1
            self._tier_last_increase[tier] = time.time()
        else:
            self._tier_decrease_count[tier] += 1
            self._tier_last_decrease[tier] = time.time()
        logger.info(
            "zai_tier_cap_change tier=%s old=%d new=%d reason=%s pid=%d",
            tier, old, new_cap, reason, os.getpid(),
        )
        if logfire:
            logfire.info(
                "zai_tier_cap_change",
                tier=tier, old=old, new=new_cap, reason=reason, pid=os.getpid(),
            )

    async def record_429(self, context: str = "", model_tier: str | None = None) -> None:
        """
        Called when a 429 is received. Adjusts adaptive backoff, and — when
        ``model_tier`` is given — drives that tier's AIMD multiplicative
        decrease (at most once per congestion event) plus the saturation
        escalation (429s persisting at minimum cap ⇒ account-level cliff).

        Args:
            context: Optional context string for logging (e.g., section name)
            model_tier: Tier bucket of the model on the wire (see
                ``utils/model_resolution.py::model_id_to_tier``)
        """
        tier = self._normalize_tier(model_tier)
        escalate_cliff = False
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

            if tier is not None:
                self._tier_total_429s[tier] += 1
                if now - self._tier_last_429[tier] < 10:
                    self._tier_consecutive_429s[tier] += 1
                    self._tier_backoff_floor[tier] = min(
                        8.0,
                        self._initial_backoff * (1.5 ** self._tier_consecutive_429s[tier]),
                    )
                else:
                    self._tier_consecutive_429s[tier] = 1
                    self._tier_backoff_floor[tier] = self._initial_backoff
                self._tier_last_429[tier] = now
                self._tier_clean_streak[tier] = 0

                # Multiplicative decrease — once per congestion event.
                if (
                    self._tier_success_since_decrease[tier]
                    and self._tier_cap[tier] > self._tier_min[tier]
                ):
                    self._set_tier_cap(tier, self._tier_cap[tier] // 2, "429_halve")
                    self._tier_success_since_decrease[tier] = False

                # Gradient saturation: still being rejected at minimum cap
                # despite backoff AND logical calls are actually failing
                # (recent retry exhaustion) ⇒ behave like the cliff
                # (slam + pause). The exhaustion requirement keeps a short
                # burst of overlapping retry sagas (which can rack up 6
                # consecutive wire 429s in seconds while every logical call
                # still eventually succeeds) from tripping an account-wide
                # pause.
                if (
                    self._tier_cap[tier] <= self._tier_min[tier]
                    and self._tier_consecutive_429s[tier] >= self._saturation_429s
                    and now - self._last_exhausted_time <= self._saturation_exhaustion_window_s
                    and now > self._acquire_pause_until
                ):
                    escalate_cliff = True

            if logfire:
                logfire.warn(
                    "zai_rate_limit_hit",
                    context=context,
                    model_tier=tier,
                    consecutive_429s=self._consecutive_429s,
                    total_429s=self._total_429s,
                    backoff_floor=self._backoff_floor,
                )
            self._persist_snapshot()
        if escalate_cliff:
            await self.record_fup_signal(
                context=f"gradient_saturated:{tier}:{context}",
                error_snippet=(
                    f"429s persisted at min cap on tier {tier} "
                    f"({self._saturation_429s}+ consecutive) — escalating to cliff"
                ),
            )

    async def record_success(self, model_tier: str | None = None) -> None:
        """Called on successful request. Gradually lowers backoff floor and
        — when ``model_tier`` is given — feeds that tier's clean streak,
        possibly earning an additive (+1) cap increase."""
        tier = self._normalize_tier(model_tier)
        with self._state_lock:
            now = time.time()
            self._total_requests += 1
            self._last_success_time = now
            # Slowly decay the floor back to initial
            self._backoff_floor = max(self._initial_backoff, self._backoff_floor * 0.9)
            self._consecutive_429s = max(0, self._consecutive_429s - 1)

            if tier is not None:
                self._tier_total_requests[tier] += 1
                self._tier_backoff_floor[tier] = max(
                    self._initial_backoff, self._tier_backoff_floor[tier] * 0.9
                )
                self._tier_consecutive_429s[tier] = max(
                    0, self._tier_consecutive_429s[tier] - 1
                )
                self._tier_success_since_decrease[tier] = True
                self._tier_clean_streak[tier] += 1
                # Additive increase: sustained clean streak, quiet since the
                # last 429, not too soon after the last increase, no FUP
                # freeze, below the max bound.
                if (
                    self._tier_clean_streak[tier] >= self._increase_streak
                    and now - self._tier_last_429[tier] >= self._increase_quiet_s
                    and now - self._tier_last_increase[tier] >= self._increase_cooldown_s
                    and now >= self._freeze_until
                    and self._tier_cap[tier] < self._tier_max[tier]
                ):
                    self._set_tier_cap(tier, self._tier_cap[tier] + 1, "clean_increase")
                    self._tier_clean_streak[tier] = 0
            self._persist_snapshot()

    async def record_fup_signal(self, context: str = "", error_snippet: str = "") -> None:
        """Called on an account-level CLIFF signal: FUP text on a NON-429
        response (suspension etc.), or gradient saturation (429s persisting
        at minimum caps — see ``record_429``). The response is to STOP, not
        retry: every tier slams to its minimum cap, additive increases
        freeze for ``ZAI_FUP_FREEZE_SECONDS``, and new acquires fail fast
        for ``ZAI_FUP_ACQUIRE_PAUSE_SECONDS``. The watchdog escalates this
        as CRITICAL with no grace period.

        NOTE (verified 2026-06-11): ZAI's ORDINARY throttle is a 429 whose
        body carries code 1313 + FUP text. Those route through
        ``record_429`` (the gradient), NOT here — calling this for every
        1313-texted 429 would page CRITICAL continuously and turn routine
        throttle into account-wide pauses.
        """
        with self._state_lock:
            now = time.time()
            self._total_fup_events += 1
            self._last_fup_time = now
            self._last_fup_snippet = (error_snippet or "")[:500]
            self._last_fup_context = (context or "")[:200]
            # Cliff: all tiers to minimum, freeze increases, pause acquires.
            for tier in TIERS:
                self._set_tier_cap(tier, self._tier_min[tier], "fup_slam")
                self._tier_clean_streak[tier] = 0
                self._tier_success_since_decrease[tier] = False
            # Cliffs arriving within ~5s while the pause is already armed are
            # the SAME event reported twice (e.g. two concurrent record_429
            # saturation escalations from different loops) — don't double the
            # pause for them.
            same_event = (
                now - self._last_cliff_time < 5.0 and self._acquire_pause_until > now
            )
            if not same_event:
                if now - self._last_cliff_time < 3600:
                    self._consecutive_cliffs += 1
                else:
                    self._consecutive_cliffs = 1
                self._last_cliff_time = now
                pause_s = min(
                    self._fup_freeze_s,
                    self._fup_pause_s * (2 ** (self._consecutive_cliffs - 1)),
                )
                self._freeze_until = now + self._fup_freeze_s
                self._acquire_pause_until = now + pause_s
            else:
                pause_s = max(0.0, self._acquire_pause_until - now)
            if logfire:
                logfire.error(
                    "zai_fup_signal",
                    context=context,
                    total_fup_events=self._total_fup_events,
                    error_snippet=self._last_fup_snippet,
                    pause_seconds=pause_s,
                    consecutive_cliffs=self._consecutive_cliffs,
                    freeze_seconds=self._fup_freeze_s,
                )
            logger.error(
                "ZAI FUP signal detected — context=%s snippet=%r total=%d "
                "(tiers slammed to min; acquires paused %.0fs; cliff #%d this hour; "
                "increases frozen %.0fs)",
                context,
                self._last_fup_snippet,
                self._total_fup_events,
                pause_s,
                self._consecutive_cliffs,
                self._fup_freeze_s,
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
            # Merge-on-write for cross-process timestamp fields: every UA
            # process holds its own singleton and overwrites this single
            # file last-writer-wins, so without the merge a gateway
            # record_success would erase a cron's last_exhausted_at and the
            # watchdog's outcome alarms could never be trusted. Timestamps
            # are wall-clock comparable; counters stay per-writer (an
            # acknowledged attribution limitation — journald cap-change
            # lines are the primary cross-process channel).
            try:
                existing = json.loads(path.read_text())
                if not isinstance(existing, dict):
                    existing = {}
            except Exception:  # noqa: BLE001 — absent/corrupt file: no merge
                existing = {}

            def _merged_ts(key: str, own: float) -> float | None:
                try:
                    prior = float(existing.get(key) or 0.0)
                except (TypeError, ValueError):
                    prior = 0.0
                merged = max(prior, float(own or 0.0))
                return merged or None

            payload = {
                "max_concurrent": self._max_concurrent,
                "backoff_floor": self._backoff_floor,
                "consecutive_429s": self._consecutive_429s,
                "total_429s": self._total_429s,
                "total_requests": self._total_requests,
                "total_fup_events": self._total_fup_events,
                "total_429s_exhausted": self._total_429s_exhausted,
                "total_succeeded_after_retry": self._total_succeeded_after_retry,
                "last_exhausted_at": _merged_ts("last_exhausted_at", self._last_exhausted_time),
                "last_429_at": _merged_ts("last_429_at", self._last_429_time),
                "last_success_at": _merged_ts("last_success_at", self._last_success_time),
                "last_fup_at": _merged_ts("last_fup_at", self._last_fup_time),
                "last_fup_snippet": self._last_fup_snippet,
                "last_fup_context": self._last_fup_context,
                "cross_loop_conflicts": self._cross_loop_conflicts,
                "last_cross_loop_conflict_at": _merged_ts(
                    "last_cross_loop_conflict_at", self._last_cross_loop_time
                ),
                "freeze_until": _merged_ts("freeze_until", self._freeze_until),
                "acquire_pause_until": _merged_ts(
                    "acquire_pause_until", self._acquire_pause_until
                ),
                # Process identity: the snapshot is single-file,
                # last-writer-wins across UA processes (each has its own
                # singleton) — readings are meaningless without attribution.
                # zai_tier_cap_change journald lines are the primary
                # verification channel; this blob is the quick glance.
                "pid": os.getpid(),
                "process_name": (sys.argv[0] or "").rsplit("/", 1)[-1][:80],
                "singleton_created_at": self._created_at,
                "tiers": {
                    t: {
                        "cap": self._tier_cap[t],
                        "min": self._tier_min[t],
                        "max": self._tier_max[t],
                        "floor": round(self._tier_backoff_floor[t], 3),
                        "consecutive_429s": self._tier_consecutive_429s[t],
                        "clean_streak": self._tier_clean_streak[t],
                        "total_429s": self._tier_total_429s[t],
                        "total_requests": self._tier_total_requests[t],
                        "increase_count": self._tier_increase_count[t],
                        "decrease_count": self._tier_decrease_count[t],
                        "last_429_at": self._tier_last_429[t] or None,
                        "last_increase_at": self._tier_last_increase[t] or None,
                        "last_decrease_at": self._tier_last_decrease[t] or None,
                    }
                    for t in TIERS
                },
                "snapshot_written_at": time.time(),
            }
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload))
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001 — never crash the rate limiter over a snapshot write
            logger.warning("rate_limiter snapshot persist failed", exc_info=True)

    def get_backoff(self, attempt: int, model_tier: str | None = None) -> float:
        """
        Calculate backoff with jitter. Uses the adaptive floor — the
        TIER's floor when ``model_tier`` is given, else the global one.

        Args:
            attempt: Zero-indexed attempt number
            model_tier: Optional tier bucket for per-tier floors

        Returns:
            Backoff duration in seconds
        """
        tier = self._normalize_tier(model_tier)
        floor = self._backoff_floor if tier is None else self._tier_backoff_floor[tier]
        base = floor * (2 ** attempt)
        jitter = random.uniform(0.1, 0.5) * base
        return min(base + jitter, self._max_backoff)

    @asynccontextmanager
    async def acquire(self, context: str = "", model_tier: str | None = None) -> AsyncIterator[None]:
        """
        Acquire a slot for an API call.
        Enforces both concurrent limit AND minimum inter-request spacing.

        With ``model_tier`` given, admission goes through that tier's
        dynamic AIMD gate (tiers run additively — throughput is the sum of
        the per-tier caps); without it, the legacy global semaphore is used
        (existing callers see identical behavior). The min-interval spacing
        stays GLOBAL either way — it is the account-wide thundering-herd
        guard.

        Raises ``ZAIFupPauseError`` (fail-fast) while the account-level FUP
        acquire-pause is active, OR while the operator control plane
        (``services/zai_control``) has a global pause or this tier's hard-stop
        engaged. The control-plane checks fail OPEN — any read error lets the
        acquire proceed.

        Args:
            context: Optional context string for logging
            model_tier: Tier bucket of the model on the wire
        """
        tier = self._normalize_tier(model_tier)
        # Operator control-plane pause (fail-open). Global pause covers every
        # caller; tier pause is the per-tier hard-stop from the lever ladder.
        try:
            from universal_agent.services import zai_control

            paused, _info = zai_control.is_globally_paused()
            if paused:
                raise ZAIFupPauseError(
                    f"ZAI globally paused by operator control plane "
                    f"(context={context!r}) — do not retry"
                )
            if tier is not None and zai_control.is_tier_paused(tier):
                raise ZAIFupPauseError(
                    f"ZAI tier {tier!r} paused by operator control plane "
                    f"(context={context!r}) — do not retry"
                )
        except ZAIFupPauseError:
            raise
        except Exception:  # noqa: BLE001 — control read fails OPEN
            pass

        pause_remaining = self._acquire_pause_until - time.time()
        if pause_remaining > 0:
            raise ZAIFupPauseError(
                f"ZAI FUP acquire-pause active for another {pause_remaining:.0f}s "
                f"(context={context!r}) — account-level cliff signal; do not retry"
            )
        # `tier` resolved at the top of this method.
        # The bundle is captured locally so release always pairs with the
        # gate actually acquired — never a swapped attribute.
        prims = self._get_loop_primitives()
        gate = prims.semaphore if tier is None else prims.get_tier_gate(tier, self)
        await gate.acquire()
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
            gate.release()

    def note_retry_exhausted(self) -> None:
        """A logical call gave up after exhausting its 429 retries — the
        outcome the watchdog should alarm on (wire 429 counts amplify
        under retries and no longer mean failure)."""
        with self._state_lock:
            self._total_429s_exhausted += 1
            self._last_exhausted_time = time.time()
            self._persist_snapshot()

    def note_succeeded_after_retry(self) -> None:
        """A logical call succeeded after one or more 429 retries —
        limiter-managed throttle working as intended."""
        with self._state_lock:
            self._total_succeeded_after_retry += 1
            self._persist_snapshot()

    def get_stats(self) -> dict[str, Any]:
        """Return current rate limiter statistics."""
        return {
            "max_concurrent": self._max_concurrent,
            "backoff_floor": self._backoff_floor,
            "consecutive_429s": self._consecutive_429s,
            "total_429s": self._total_429s,
            "total_requests": self._total_requests,
            "total_fup_events": self._total_fup_events,
            "total_429s_exhausted": self._total_429s_exhausted,
            "total_succeeded_after_retry": self._total_succeeded_after_retry,
            "tier_caps": dict(self._tier_cap),
            "freeze_until": self._freeze_until,
            "acquire_pause_until": self._acquire_pause_until,
        }


async def with_rate_limit_retry(
    func: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = 5,
    context: str = "",
    model_tier: str | None = None,
    max_total_seconds: float | None = None,
    **kwargs: Any,
) -> T:
    """
    Execute an async function with rate limit handling.

    This is a convenience wrapper that:
    1. Acquires a slot from the rate limiter (the TIER's AIMD gate when
       ``model_tier`` is given)
    2. Executes the function
    3. Handles 429 errors with adaptive backoff — the slot is RELEASED
       during backoff sleeps, so a cap-1 tier is never monopolized by one
       failing call's retry saga
    4. Records success/failure for adaptive tuning

    Error dispatch (verified against live ZAI behavior 2026-06-11 — its
    STANDARD throttle is a 429 whose body carries code 1313 + Fair-Usage
    text):
        429-shaped (even FUP-texted) → gradient: record_429 + backoff + retry
        FUP text on a non-429        → cliff:    record_fup_signal + raise
        ZAIFupPauseError from acquire → propagate (account paused, fail fast)
        anything else                 → raise (not a rate-limit problem)

    Args:
        func: Async function to execute
        *args: Positional arguments for func
        max_retries: Maximum retry attempts (default: 5)
        context: Context string for logging
        model_tier: Tier bucket of the model on the wire
            (``utils/model_resolution.py::model_id_to_tier``)
        max_total_seconds: Optional wall-clock budget for the WHOLE retry
            saga; when the next backoff sleep would cross it, the last 429
            is raised instead (callers with cron budgets thread theirs in)
        **kwargs: Keyword arguments for func. NOTE: because this wrapper
            consumes ``max_retries``/``context``/``model_tier``/
            ``max_total_seconds``, the wrapped func can never receive
            kwargs by those names.

    Returns:
        Result of func

    Raises:
        Last exception if all retries exhausted
    """
    limiter = ZAIRateLimiter.get_instance()
    last_error: Exception | None = None
    started = time.monotonic()
    had_429 = False

    for attempt in range(max_retries):
        delay = 0.0
        async with limiter.acquire(context, model_tier=model_tier):
            try:
                result = await func(*args, **kwargs)
                await limiter.record_success(model_tier=model_tier)
                if had_429:
                    limiter.note_succeeded_after_retry()
                return result
            except Exception as e:
                error_str = str(e)

                if _is_429_error(error_str):
                    await limiter.record_429(context, model_tier=model_tier)
                    had_429 = True
                    last_error = e
                    if attempt >= max_retries - 1:
                        break  # exhausted — handled below
                    delay = limiter.get_backoff(attempt, model_tier=model_tier)
                elif _is_fup_error(error_str):
                    # Account-level cliff (non-429 FUP signal): stop, do NOT
                    # retry — retrying makes the ban risk worse.
                    await limiter.record_fup_signal(context, error_str)
                    raise
                else:
                    # Non-rate-limit error, don't retry
                    raise

        # Slot released here — back off without holding tier capacity.
        if delay > 0:
            if (
                max_total_seconds is not None
                and (time.monotonic() - started) + delay > max_total_seconds
            ):
                break  # budget would be crossed — surface the last 429
            print(f"  ⚠️ [429] Rate limited ({context}). Backoff: {delay:.1f}s (Attempt {attempt+1}/{max_retries})")
            await asyncio.sleep(delay)

    # All retries exhausted (or the total budget crossed)
    if last_error:
        limiter.note_retry_exhausted()
        raise last_error
    raise RuntimeError(f"Rate limit retries exhausted for {context}")

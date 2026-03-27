"""
Capacity Governor — system-level rate limiting for agent dispatch.

Sits between the dispatch pipeline and agent execution to prevent
overloading the LLM provider API. Tracks:
  - Active concurrent agent sessions
  - Recent 429/overloaded errors (from any component)
  - Adaptive backoff that reduces dispatch throughput under pressure

The governor is consulted before each heartbeat dispatch sweep:
  - If capacity is available → proceed normally
  - If at capacity → skip dispatch, schedule deferred wake
  - If in backoff → skip dispatch, wait for backoff to expire

The governor is a singleton (shared across heartbeat service, auto-refinement
loop, and any future dispatch entrypoints).

Integration points:
  - HeartbeatService._run_heartbeat: check before dispatch_sweep
  - auto_refinement_loop: check before refine_with_llm / decompose_with_llm
  - refinement_agent / decomposition_agent: report 429s

Configuration (environment variables):
    UA_CAPACITY_MAX_CONCURRENT: Max parallel agent executions (default: 2)
    UA_CAPACITY_BACKOFF_BASE_SECONDS: Initial backoff on 429 (default: 30)
    UA_CAPACITY_BACKOFF_MAX_SECONDS: Maximum backoff cap (default: 300)
    UA_CAPACITY_COOLDOWN_AFTER_429_SECONDS: Mandatory cooldown after a 429 (default: 60)
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_MAX_CONCURRENT = 2
DEFAULT_BACKOFF_BASE_SECONDS = 30.0
DEFAULT_BACKOFF_MAX_SECONDS = 300.0
DEFAULT_COOLDOWN_AFTER_429_SECONDS = 60.0


# ---------------------------------------------------------------------------
# Capacity state
# ---------------------------------------------------------------------------

@dataclass
class CapacitySnapshot:
    """Point-in-time view of capacity governor state."""
    max_concurrent: int
    active_slots: int
    available_slots: int
    in_backoff: bool
    backoff_remaining_seconds: float
    consecutive_429s: int
    total_429s: int
    total_requests: int
    total_shed: int  # requests shed (denied) by governor
    last_429_at: Optional[str]


class CapacityGovernor:
    """System-level capacity governor for agent dispatch.

    Thread-safe singleton. All public methods are sync or async as needed.
    Uses an asyncio Semaphore for concurrency and time-based backoff for 429s.
    """

    _instance: Optional["CapacityGovernor"] = None

    def __init__(
        self,
        max_concurrent: Optional[int] = None,
        backoff_base: Optional[float] = None,
        backoff_max: Optional[float] = None,
        cooldown_after_429: Optional[float] = None,
    ):
        self._max_concurrent = max_concurrent or int(
            os.getenv("UA_CAPACITY_MAX_CONCURRENT", str(DEFAULT_MAX_CONCURRENT))
        )
        self._backoff_base = backoff_base or float(
            os.getenv("UA_CAPACITY_BACKOFF_BASE_SECONDS", str(DEFAULT_BACKOFF_BASE_SECONDS))
        )
        self._backoff_max = backoff_max or float(
            os.getenv("UA_CAPACITY_BACKOFF_MAX_SECONDS", str(DEFAULT_BACKOFF_MAX_SECONDS))
        )
        self._cooldown_after_429 = cooldown_after_429 or float(
            os.getenv("UA_CAPACITY_COOLDOWN_AFTER_429_SECONDS", str(DEFAULT_COOLDOWN_AFTER_429_SECONDS))
        )

        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._active_slots = 0
        self._lock = asyncio.Lock()

        # 429 tracking
        self._consecutive_429s = 0
        self._total_429s = 0
        self._total_requests = 0
        self._total_shed = 0
        self._last_429_at: float = 0.0
        self._backoff_until: float = 0.0  # Unix timestamp

        logger.info(
            "CapacityGovernor initialized: max_concurrent=%d, backoff_base=%.1fs, backoff_max=%.1fs, cooldown=%.1fs",
            self._max_concurrent,
            self._backoff_base,
            self._backoff_max,
            self._cooldown_after_429,
        )

    @classmethod
    def get_instance(cls, **kwargs) -> "CapacityGovernor":
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (testing)."""
        cls._instance = None

    # ------------------------------------------------------------------
    # Pre-flight check (called before dispatch)
    # ------------------------------------------------------------------

    def can_dispatch(self) -> tuple[bool, str]:
        """Synchronous pre-flight check. Returns (allowed, reason).

        This is the primary gate for heartbeat and auto-refinement dispatch.
        Does NOT acquire a slot — use `acquire_slot()` for that.
        """
        now = time.time()

        # Check 1: Are we in mandatory backoff?
        if now < self._backoff_until:
            remaining = self._backoff_until - now
            reason = (
                f"capacity_backoff: {remaining:.0f}s remaining "
                f"(consecutive_429s={self._consecutive_429s})"
            )
            self._total_shed += 1
            return False, reason

        # Check 2: Are all slots taken?
        if self._active_slots >= self._max_concurrent:
            reason = (
                f"capacity_full: {self._active_slots}/{self._max_concurrent} "
                f"slots in use"
            )
            self._total_shed += 1
            return False, reason

        return True, "capacity_available"

    # ------------------------------------------------------------------
    # Slot acquisition (wraps semaphore + tracking)
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def acquire_slot(self, context: str = ""):
        """Acquire a capacity slot for an agent execution.

        Usage:
            async with governor.acquire_slot("heartbeat:session_simone"):
                await run_agent(...)

        Raises asyncio.TimeoutError after 5s if no slot is available
        (prevents deadlocks — caller should treat as capacity_full).
        """
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=5.0)
        except asyncio.TimeoutError:
            self._total_shed += 1
            raise

        async with self._lock:
            self._active_slots += 1
            self._total_requests += 1

        logger.debug(
            "CapacityGovernor slot acquired: %s (active=%d/%d)",
            context, self._active_slots, self._max_concurrent,
        )

        try:
            yield
        finally:
            self._semaphore.release()
            async with self._lock:
                self._active_slots = max(0, self._active_slots - 1)
            logger.debug(
                "CapacityGovernor slot released: %s (active=%d/%d)",
                context, self._active_slots, self._max_concurrent,
            )

    # ------------------------------------------------------------------
    # 429 / error reporting
    # ------------------------------------------------------------------

    async def report_rate_limit(self, context: str = "", error: Optional[Exception] = None) -> float:
        """Report a 429 / overloaded error. Returns the backoff duration applied.

        Call this from any component that hits a provider rate limit.
        The governor will enter a mandatory backoff period to let the
        provider recover before dispatching more work.
        """
        async with self._lock:
            now = time.time()
            self._total_429s += 1

            # Track consecutive 429s (within 120s of each other = same incident)
            if now - self._last_429_at < 120:
                self._consecutive_429s += 1
            else:
                self._consecutive_429s = 1

            self._last_429_at = now

            # Calculate backoff: exponential with jitter, capped
            backoff = min(
                self._backoff_base * (2 ** (self._consecutive_429s - 1)),
                self._backoff_max,
            )
            jitter = random.uniform(0.1, 0.3) * backoff
            total_backoff = backoff + jitter

            # At minimum, apply the cooldown period
            total_backoff = max(total_backoff, self._cooldown_after_429)

            self._backoff_until = now + total_backoff

            logger.warning(
                "CapacityGovernor: 429 reported by %s (consecutive=%d, total=%d). "
                "Backoff for %.0fs until %s. Error: %s",
                context,
                self._consecutive_429s,
                self._total_429s,
                total_backoff,
                time.strftime("%H:%M:%S", time.localtime(self._backoff_until)),
                str(error)[:200] if error else "N/A",
            )

            return total_backoff

    async def report_success(self, context: str = "") -> None:
        """Report a successful execution. Gradually decays backoff pressure."""
        async with self._lock:
            self._total_requests += 1
            # Successful runs decay the consecutive counter
            self._consecutive_429s = max(0, self._consecutive_429s - 1)
            # If we've had enough successes, clear backoff early
            if self._consecutive_429s == 0 and self._backoff_until > 0:
                self._backoff_until = 0.0
                logger.info("CapacityGovernor: backoff cleared after success by %s", context)

    # ------------------------------------------------------------------
    # Snapshot (for dashboards, logging, health checks)
    # ------------------------------------------------------------------

    def snapshot(self) -> CapacitySnapshot:
        """Return a point-in-time snapshot of governor state."""
        now = time.time()
        remaining = max(0.0, self._backoff_until - now)
        return CapacitySnapshot(
            max_concurrent=self._max_concurrent,
            active_slots=self._active_slots,
            available_slots=max(0, self._max_concurrent - self._active_slots),
            in_backoff=now < self._backoff_until,
            backoff_remaining_seconds=remaining,
            consecutive_429s=self._consecutive_429s,
            total_429s=self._total_429s,
            total_requests=self._total_requests,
            total_shed=self._total_shed,
            last_429_at=(
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._last_429_at))
                if self._last_429_at > 0 else None
            ),
        )

    def snapshot_dict(self) -> dict[str, Any]:
        """Return snapshot as a plain dict (for JSON serialization)."""
        s = self.snapshot()
        return {
            "max_concurrent": s.max_concurrent,
            "active_slots": s.active_slots,
            "available_slots": s.available_slots,
            "in_backoff": s.in_backoff,
            "backoff_remaining_seconds": round(s.backoff_remaining_seconds, 1),
            "consecutive_429s": s.consecutive_429s,
            "total_429s": s.total_429s,
            "total_requests": s.total_requests,
            "total_shed": s.total_shed,
            "last_429_at": s.last_429_at,
        }


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def check_capacity() -> tuple[bool, str]:
    """Quick check — can we dispatch right now?"""
    return CapacityGovernor.get_instance().can_dispatch()


def capacity_snapshot() -> dict[str, Any]:
    """Get current capacity state as dict."""
    return CapacityGovernor.get_instance().snapshot_dict()
"""End of capacity_governor.py — system-level rate limiting for agent dispatch."""

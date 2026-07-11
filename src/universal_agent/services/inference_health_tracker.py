"""Inference-health circuit breaker for VP dispatch.

A small, in-process, thread-safe tracker of recent inference outcomes over a
rolling window. When too many inference failures (5xx / timeout /
429-overloaded) land inside the window, the tracker trips a **dispatch hold**:
the VP claim path stops claiming new missions (which would be predestined to
fail against a degraded provider) until the hold window clears, then
auto-resumes. Exactly ONE alert is emitted per degraded *episode* so a sustained
outage doesn't spam findings.

Contrast with ``capacity_governor`` (exponential backoff on consecutive 429s,
concurrency slots) and ``zai_control`` (an operator-set manual pause): this is an
**automatic, windowed, provider-health breaker** that gates only *new* VP
dispatch. Held work stays QUEUED — nothing is lost — and dispatch resumes on its
own once the failure window drains.

Design guarantees (all deliberately conservative and fail-SAFE):

- **Enabled by default** (``UA_INFERENCE_DEGRADE_ENABLED``); a kill-switch to
  ``false`` makes ``should_hold_dispatch`` always return "don't hold" so behavior
  is byte-for-byte the legacy path.
- **Nothing lost:** the breaker only SKIPS a claim; the mission row stays open
  and is claimed on a later tick once the hold clears.
- **One alert per episode:** the alert fires only on the *transition* into a
  hold; subsequent held ticks return no alert. A fresh trip after the hold
  auto-clears is a new episode and alerts again.
- **Cheap + thread-safe:** a deque of recent failure timestamps behind a plain
  lock; touchable from sync or async contexts.

Configuration (environment, read live so the kill-switch is honored instantly):

    UA_INFERENCE_DEGRADE_ENABLED         default true  (breaker armed)
    UA_INFERENCE_DEGRADE_FAIL_COUNT      default 5     (failures within window)
    UA_INFERENCE_DEGRADE_WINDOW_MINUTES  default 10
    UA_INFERENCE_DEGRADE_HOLD_MINUTES    default 10
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging
import os
import threading
import time
from typing import Any, Optional

from universal_agent.utils.env_utils import env_int as _env_int

logger = logging.getLogger(__name__)

# ── Conservative defaults (operator-chosen; enabled out of the box) ──────────
DEFAULT_ENABLED = True
DEFAULT_FAIL_COUNT = 5
DEFAULT_WINDOW_MINUTES = 10.0
DEFAULT_HOLD_MINUTES = 10.0


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(minimum, float(str(raw).strip()))
    except (TypeError, ValueError):
        return default


@dataclass
class HoldDecision:
    """Result of a ``should_hold_dispatch`` check.

    ``hold`` — True means the caller must SKIP claiming new dispatch this tick.
    ``alert`` — populated ONLY on the tick that transitions into a new hold
    episode (so the caller emits exactly one signal per episode); None otherwise.
    """

    hold: bool
    alert: Optional[dict[str, Any]] = None
    fail_count: int = 0
    hold_remaining_seconds: float = 0.0


class InferenceHealthTracker:
    """Rolling-window breaker that holds VP dispatch when inference is degraded.

    Thread-safe singleton. ``record_failure`` / ``record_success`` feed the
    window; ``should_hold_dispatch`` is the gate the dispatch claim path reads.
    """

    _instance: Optional["InferenceHealthTracker"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        *,
        fail_count: Optional[int] = None,
        window_minutes: Optional[float] = None,
        hold_minutes: Optional[float] = None,
    ) -> None:
        self._fail_count = (
            fail_count if fail_count is not None
            else _env_int("UA_INFERENCE_DEGRADE_FAIL_COUNT", DEFAULT_FAIL_COUNT, minimum=1)
        )
        self._window_seconds = (
            (window_minutes if window_minutes is not None
             else _env_float("UA_INFERENCE_DEGRADE_WINDOW_MINUTES", DEFAULT_WINDOW_MINUTES))
            * 60.0
        )
        self._hold_seconds = (
            (hold_minutes if hold_minutes is not None
             else _env_float("UA_INFERENCE_DEGRADE_HOLD_MINUTES", DEFAULT_HOLD_MINUTES))
            * 60.0
        )

        self._lock = threading.RLock()
        self._failures: deque[float] = deque()  # timestamps of recent failures
        self._hold_until: float = 0.0
        self._episode_alerted: bool = False

        # Observability counters (do not affect the gate).
        self._total_failures = 0
        self._total_successes = 0
        self._total_holds = 0  # episodes tripped

        logger.info(
            "InferenceHealthTracker initialized: fail_count=%d window=%.0fs hold=%.0fs",
            self._fail_count, self._window_seconds, self._hold_seconds,
        )

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls, **kwargs: Any) -> "InferenceHealthTracker":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(**kwargs)
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Drop the singleton (tests + explicit re-config)."""
        with cls._instance_lock:
            cls._instance = None

    # ------------------------------------------------------------------
    # Runtime enablement (read live so the kill-switch takes effect instantly)
    # ------------------------------------------------------------------

    @staticmethod
    def enabled() -> bool:
        return _env_flag("UA_INFERENCE_DEGRADE_ENABLED", DEFAULT_ENABLED)

    # ------------------------------------------------------------------
    # Recording inference outcomes
    # ------------------------------------------------------------------

    def _prune(self, now: float) -> None:
        cutoff = now - self._window_seconds
        failures = self._failures
        while failures and failures[0] < cutoff:
            failures.popleft()

    def record_failure(self, reason: str = "", *, now: Optional[float] = None) -> None:
        """Record an inference-degradation signal (5xx / timeout / 429-overload).

        Cheap and never raises. Recording a failure does NOT itself alert or hold
        — the gate (``should_hold_dispatch``) evaluates the window.
        """
        now = time.time() if now is None else now
        with self._lock:
            self._prune(now)
            self._failures.append(now)
            self._total_failures += 1
        logger.debug("InferenceHealthTracker: failure recorded (%s)", reason or "unspecified")

    def record_success(self, *, now: Optional[float] = None) -> None:
        """Record a healthy inference outcome (observability only).

        A success does not retroactively erase failures already inside the
        window — the window itself drains them by time — so a real outage still
        trips even if interleaved with a few successes. Kept for counters and a
        future decay policy.
        """
        with self._lock:
            self._total_successes += 1

    # ------------------------------------------------------------------
    # The gate (read by the VP dispatch claim path)
    # ------------------------------------------------------------------

    def should_hold_dispatch(self, *, now: Optional[float] = None) -> HoldDecision:
        """Decide whether to HOLD new VP dispatch this tick.

        Returns a :class:`HoldDecision`. When ``hold`` is True the caller must
        skip claiming a new mission. ``alert`` is set only on the transition into
        a new degraded episode (emit exactly one signal). Auto-resumes: once the
        hold window elapses the breaker clears itself and the next trip is a
        fresh episode.

        Disabled (``UA_INFERENCE_DEGRADE_ENABLED=false``) → always
        ``hold=False`` (legacy behavior, no state change).
        """
        if not self.enabled():
            return HoldDecision(hold=False)

        now = time.time() if now is None else now
        with self._lock:
            # Auto-resume: a hold that has elapsed clears the episode so a fresh
            # breach can trip (and re-alert) as a new episode.
            if self._hold_until and now >= self._hold_until:
                self._hold_until = 0.0
                self._episode_alerted = False
                logger.info("InferenceHealthTracker: hold window cleared — dispatch resumed")

            self._prune(now)
            fail_count = len(self._failures)

            # Already holding within an active episode → keep holding, no re-alert.
            if self._hold_until and now < self._hold_until:
                return HoldDecision(
                    hold=True,
                    alert=None,
                    fail_count=fail_count,
                    hold_remaining_seconds=max(0.0, self._hold_until - now),
                )

            # Not currently holding — do the recent failures breach the threshold?
            if fail_count >= self._fail_count:
                self._hold_until = now + self._hold_seconds
                self._total_holds += 1
                alert: Optional[dict[str, Any]] = None
                if not self._episode_alerted:
                    self._episode_alerted = True
                    alert = {
                        "reason": "inference_degraded",
                        "fail_count": fail_count,
                        "threshold": self._fail_count,
                        "window_seconds": self._window_seconds,
                        "hold_seconds": self._hold_seconds,
                        "hold_until": self._hold_until,
                        "message": (
                            f"Inference provider degraded: {fail_count} failures in the "
                            f"last {self._window_seconds / 60:.0f}m (threshold "
                            f"{self._fail_count}). Holding new VP dispatch for "
                            f"{self._hold_seconds / 60:.0f}m; queued work is preserved."
                        ),
                    }
                    logger.warning("InferenceHealthTracker: %s", alert["message"])
                return HoldDecision(
                    hold=True,
                    alert=alert,
                    fail_count=fail_count,
                    hold_remaining_seconds=self._hold_seconds,
                )

            return HoldDecision(hold=False, fail_count=fail_count)

    # ------------------------------------------------------------------
    # Snapshot (dashboards / logging)
    # ------------------------------------------------------------------

    def snapshot(self, *, now: Optional[float] = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        with self._lock:
            self._prune(now)
            holding = bool(self._hold_until and now < self._hold_until)
            return {
                "enabled": self.enabled(),
                "fail_count_threshold": self._fail_count,
                "window_seconds": self._window_seconds,
                "hold_seconds": self._hold_seconds,
                "failures_in_window": len(self._failures),
                "holding": holding,
                "hold_remaining_seconds": max(0.0, self._hold_until - now) if holding else 0.0,
                "total_failures": self._total_failures,
                "total_successes": self._total_successes,
                "total_holds": self._total_holds,
            }


def record_inference_failure(reason: str = "") -> None:
    """Module-level convenience: record a degradation signal on the singleton."""
    try:
        InferenceHealthTracker.get_instance().record_failure(reason)
    except Exception:  # noqa: BLE001 — breaker recording must never break a caller
        logger.debug("record_inference_failure no-op (tracker unavailable)", exc_info=True)


def record_inference_success() -> None:
    """Module-level convenience: record a healthy outcome on the singleton."""
    try:
        InferenceHealthTracker.get_instance().record_success()
    except Exception:  # noqa: BLE001
        logger.debug("record_inference_success no-op (tracker unavailable)", exc_info=True)

"""System Load Guard — blocks dispatch when VPS resources are exhausted.

This module provides a lightweight system health check that the idle dispatch
loop consults before waking any session.  It checks:

  1. **Process count** — total processes owned by the current user.
     When above threshold, the system is in a process explosion scenario.

  2. **Swap usage** — percentage of swap space consumed.
     High swap indicates memory pressure that will degrade all running tasks.

Design principle: **Notify-first, kill-last.**
When thresholds are exceeded, the guard returns structured information
(SystemHealthStatus) including a notification_message for Simone to
investigate.  It does NOT kill processes — that's the reaper's job.

Configuration via environment variables:
  - UA_MAX_PROCESS_COUNT (default: 100) — per-user process count ceiling
  - UA_MAX_SWAP_PCT (default: 85.0) — swap usage percentage ceiling
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Defaults ─────────────────────────────────────────────────────────────────

DEFAULT_MAX_PROCESS_COUNT = 100
DEFAULT_MAX_SWAP_PCT = 85.0


# ── Data Model ───────────────────────────────────────────────────────────────

@dataclass
class SystemHealthStatus:
    """Structured health check result.

    Includes actionable notification content when unhealthy.
    """

    healthy: bool
    reason: str
    process_count: int = 0
    swap_pct: float = 0.0
    notification_message: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "healthy": self.healthy,
            "reason": self.reason,
            "process_count": self.process_count,
            "swap_pct": round(self.swap_pct, 1),
            "notification_message": self.notification_message,
        }


# ── Platform Probes ──────────────────────────────────────────────────────────
# Separated into functions for easy mocking in tests.

def _count_user_processes() -> int:
    """Count processes owned by the current user.

    Uses /proc on Linux for efficiency. Falls back to psutil if available.
    """
    try:
        uid = os.getuid()
        count = 0
        proc_path = "/proc"
        if os.path.isdir(proc_path):
            for entry in os.listdir(proc_path):
                if not entry.isdigit():
                    continue
                try:
                    status_file = os.path.join(proc_path, entry, "status")
                    with open(status_file, "r") as f:
                        for line in f:
                            if line.startswith("Uid:"):
                                real_uid = int(line.split()[1])
                                if real_uid == uid:
                                    count += 1
                                break
                except (OSError, ValueError, IndexError):
                    continue
            return count
    except Exception:
        pass

    # Fallback: try psutil
    try:
        import psutil
        username = os.getenv("USER", "")
        return len([p for p in psutil.process_iter(["username"])
                     if p.info.get("username") == username])
    except Exception:
        pass

    # Cannot determine — assume healthy
    return 0


def _get_swap_percent() -> float:
    """Get swap usage percentage.

    Reads /proc/meminfo on Linux. Falls back to psutil.
    """
    try:
        with open("/proc/meminfo", "r") as f:
            swap_total = 0
            swap_free = 0
            for line in f:
                if line.startswith("SwapTotal:"):
                    swap_total = int(line.split()[1])
                elif line.startswith("SwapFree:"):
                    swap_free = int(line.split()[1])
            if swap_total > 0:
                return ((swap_total - swap_free) / swap_total) * 100.0
            return 0.0  # No swap configured
    except Exception:
        pass

    # Fallback: try psutil
    try:
        import psutil
        swap = psutil.swap_memory()
        return swap.percent
    except Exception:
        pass

    # Cannot determine — assume healthy
    return 0.0


# ── Core Guard ───────────────────────────────────────────────────────────────

def is_system_healthy(
    *,
    max_process_count: int = 0,
    max_swap_pct: float = 0.0,
) -> SystemHealthStatus:
    """Check if system resources allow dispatching more work.

    Parameters
    ----------
    max_process_count : int
        Override for max process count. 0 = use env UA_MAX_PROCESS_COUNT
        or DEFAULT_MAX_PROCESS_COUNT.
    max_swap_pct : float
        Override for max swap percent. 0 = use env UA_MAX_SWAP_PCT
        or DEFAULT_MAX_SWAP_PCT.

    Returns
    -------
    SystemHealthStatus with healthy=True/False and reason.
    """
    # Resolve thresholds
    if max_process_count <= 0:
        max_process_count = int(
            os.getenv("UA_MAX_PROCESS_COUNT", str(DEFAULT_MAX_PROCESS_COUNT))
        )
    if max_swap_pct <= 0.0:
        max_swap_pct = float(
            os.getenv("UA_MAX_SWAP_PCT", str(DEFAULT_MAX_SWAP_PCT))
        )

    proc_count = _count_user_processes()
    swap_pct = _get_swap_percent()

    # Check process count — strictly greater than threshold
    if proc_count > max_process_count:
        msg = (
            f"🚨 System overload detected: {proc_count} processes "
            f"(threshold: {max_process_count}), swap at {swap_pct:.1f}%. "
            f"Dispatch paused. Investigate for runaway processes or stuck runs."
        )
        logger.warning(msg)
        return SystemHealthStatus(
            healthy=False,
            reason=f"process_count_exceeded:{proc_count}>{max_process_count}",
            process_count=proc_count,
            swap_pct=swap_pct,
            notification_message=msg,
        )

    # Check swap usage — strictly greater than threshold
    if swap_pct > max_swap_pct:
        msg = (
            f"🚨 System memory pressure: swap at {swap_pct:.1f}% "
            f"(threshold: {max_swap_pct:.0f}%), {proc_count} processes. "
            f"Dispatch paused. Investigate memory-hungry processes."
        )
        logger.warning(msg)
        return SystemHealthStatus(
            healthy=False,
            reason=f"swap_exceeded:{swap_pct:.1f}%>{max_swap_pct:.0f}%",
            process_count=proc_count,
            swap_pct=swap_pct,
            notification_message=msg,
        )

    return SystemHealthStatus(
        healthy=True,
        reason="healthy",
        process_count=proc_count,
        swap_pct=swap_pct,
        notification_message=None,
    )

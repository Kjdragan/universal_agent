"""ZAI inference health invariant — P4 of the watchdog restoration.

Reads the persistent ZAIRateLimiter snapshot (written by `record_*` calls in
`rate_limiter.py`) and counts UA Python processes to detect three failure
modes that threaten throughput or — worse — the subscription itself:

1. Sustained 429s (≥3 consecutive) → CRITICAL. Throttling that kills
   throughput.
2. ZAI Fair-Use-Policy / concurrency-violation signal in the last 30 min →
   CRITICAL with no grace period. Ban risk — respond NOW.
3. Adaptive backoff_floor at the max cap → CRITICAL. The rate limiter is
   sustained-throttled.
4. UA Python process count above soft limit → WARN. Correlates with the
   above; flags the operator that we're self-induced-choking.

One invariant emits one finding listing every triggered condition in
`observed_value.triggered_conditions` so the operator gets the full picture
per alert. FUP wins severity if present (critical); otherwise 429-tier
conditions are critical; process-count alone is warn.

Why one invariant instead of three: framework emits at most one finding
per invariant. A real bad day would trigger all three simultaneously
(FUP + 429s + high process count are correlated). Splitting would spam
3 emails and 3 Task Hub rows.

Cost: one ~1 KB JSON read + one `pgrep` subprocess call per heartbeat.
No AI inference. No DB write. No HTTP.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from universal_agent.rate_limiter import _get_state_path
from universal_agent.services.pipeline_invariants import invariant

logger = logging.getLogger(__name__)


# Thresholds — strict per operator direction 2026-05-20.
CONSECUTIVE_429_CRITICAL = int(os.getenv("UA_ZAI_CONSECUTIVE_429_CRITICAL", "3"))
FUP_DETECT_WINDOW_SECONDS = int(os.getenv("UA_ZAI_FUP_DETECT_WINDOW_SECONDS", "1800"))  # 30 min
PROCESS_COUNT_SOFT_LIMIT = int(os.getenv("UA_PYTHON_PROC_SOFT_LIMIT", "30"))
# Match the rate_limiter's max backoff floor cap (record_429 caps at 8s).
BACKOFF_FLOOR_MAX_THRESHOLD = float(os.getenv("UA_ZAI_BACKOFF_FLOOR_MAX", "8.0"))


def _read_snapshot() -> Optional[Dict[str, Any]]:
    """Return the snapshot dict, or None if missing / corrupt. Never raise."""
    path = _get_state_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("zai_inference_health: snapshot read failed: %s", exc)
        return None


def _count_ua_processes() -> int:
    """Count UA-related Python processes via pgrep.

    Pattern matches universal_agent OR csi_ingester invocations. Falls back
    to 0 on any error — the watchdog never crashes over a process query.
    Wrapped so tests can patch it.
    """
    if shutil.which("pgrep") is None:
        return 0
    try:
        result = subprocess.run(
            ["pgrep", "-cf", "universal_agent|csi_ingester"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        # pgrep exit 1 means "no matches" — that's a valid 0 count.
        if result.returncode in (0, 1):
            return int((result.stdout or "0").strip() or "0")
        return 0
    except (subprocess.SubprocessError, ValueError, OSError) as exc:
        logger.debug("zai_inference_health: pgrep failed: %s", exc)
        return 0


@invariant(
    id="zai_inference_health",
    title="ZAI inference pressure within safe operating envelope",
    description=(
        "Watches the persistent ZAIRateLimiter snapshot for sustained 429s "
        "(throttling) and FUP signals (ban risk), plus the count of UA "
        "Python processes (concurrency we may be inflicting on ourselves). "
        "Strict thresholds: 3+ consecutive 429s or any FUP event in the "
        "last 30 min fires critical."
    ),
    severity="critical",
    runbook_command=(
        "cat /opt/universal_agent/AGENT_RUN_WORKSPACES/zai_inference_state.json; "
        "pgrep -af 'universal_agent|csi_ingester' | head -50; "
        "journalctl -u universal-agent-gateway --since '30 min ago' --no-pager | grep -iE 'zai_rate_limit|zai_fup|429'"
    ),
    metadata={
        "pipeline": "zai_inference",
        "snapshot_path": "AGENT_RUN_WORKSPACES/zai_inference_state.json",
        "design_note": (
            "P4 (2026-05-20): one probe covering 429 pressure + FUP risk + "
            "self-induced process count. FUP gets critical-immediate with a "
            "30 min detection window so the alert clears quickly once the "
            "situation has cooled. Splitting per-condition would spam 3 "
            "alerts on a correlated bad day."
        ),
        "thresholds": {
            "consecutive_429_critical": CONSECUTIVE_429_CRITICAL,
            "fup_detect_window_seconds": FUP_DETECT_WINDOW_SECONDS,
            "process_count_soft_limit": PROCESS_COUNT_SOFT_LIMIT,
            "backoff_floor_max": BACKOFF_FLOOR_MAX_THRESHOLD,
        },
    },
)
def zai_inference_health(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    snapshot = _read_snapshot()
    process_count = _count_ua_processes()

    # If there's no snapshot AND process count is fine, stay quiet (fresh deploy).
    if snapshot is None and process_count <= PROCESS_COUNT_SOFT_LIMIT:
        return None

    triggered: list[str] = []
    severity = "warn"
    now = time.time()

    snapshot = snapshot or {}

    # Condition 1: FUP signal in the last 30 min — CRITICAL.
    last_fup_at = snapshot.get("last_fup_at")
    fup_active = False
    if last_fup_at is not None:
        try:
            if (now - float(last_fup_at)) <= FUP_DETECT_WINDOW_SECONDS:
                fup_active = True
                triggered.append("fup_active")
                severity = "critical"
        except (TypeError, ValueError):
            pass

    # Condition 2: sustained 429s — CRITICAL.
    consecutive_429s = int(snapshot.get("consecutive_429s") or 0)
    if consecutive_429s >= CONSECUTIVE_429_CRITICAL:
        triggered.append("consecutive_429s")
        severity = "critical"

    # Condition 3: backoff_floor saturated — CRITICAL.
    backoff_floor = float(snapshot.get("backoff_floor") or 0.0)
    backoff_at_max = backoff_floor >= BACKOFF_FLOOR_MAX_THRESHOLD
    if backoff_at_max:
        triggered.append("backoff_at_max")
        severity = "critical"

    # Condition 4: too many UA Python processes — WARN.
    high_process_count = process_count > PROCESS_COUNT_SOFT_LIMIT
    if high_process_count:
        triggered.append("high_process_count")
        # Keep severity as warn UNLESS another critical condition raised it.

    if not triggered:
        return None

    observed_value = {
        "triggered_conditions": triggered,
        "fup_active": fup_active,
        "consecutive_429s": consecutive_429s,
        "total_429s": int(snapshot.get("total_429s") or 0),
        "total_fup_events": int(snapshot.get("total_fup_events") or 0),
        "backoff_floor": backoff_floor,
        "backoff_at_max": backoff_at_max,
        "process_count": process_count,
        "process_soft_limit": PROCESS_COUNT_SOFT_LIMIT,
        "last_429_at": snapshot.get("last_429_at"),
        "last_fup_at": last_fup_at,
        "last_fup_snippet": snapshot.get("last_fup_snippet") or "",
        "last_fup_context": snapshot.get("last_fup_context") or "",
    }

    # Build a human-readable message that names the worst-cause first.
    if fup_active:
        headline = (
            "ZAI FUP signal active — fair-use / concurrency violation in last "
            f"{FUP_DETECT_WINDOW_SECONDS // 60} min. Snippet: "
            f"{(snapshot.get('last_fup_snippet') or '')[:120]!r}. STOP "
            "concurrent inference now and investigate before resuming."
        )
    elif consecutive_429s >= CONSECUTIVE_429_CRITICAL:
        headline = (
            f"ZAI rate limit sustained — {consecutive_429s} consecutive 429s. "
            "Throughput is being throttled. Lower ZAI_MAX_CONCURRENT or "
            "reduce in-flight workers."
        )
    elif backoff_at_max:
        headline = (
            f"ZAI adaptive backoff saturated (floor={backoff_floor:.1f}s at "
            "cap). Rate limiter is in sustained-throttle mode."
        )
    else:
        headline = (
            f"UA Python process count {process_count} above soft limit "
            f"{PROCESS_COUNT_SOFT_LIMIT}. Risk of self-induced ZAI rate "
            "pressure — reduce concurrent workers."
        )

    return {
        "observed_value": observed_value,
        "threshold_text": (
            f"consecutive_429s < {CONSECUTIVE_429_CRITICAL} AND "
            f"no FUP event in last {FUP_DETECT_WINDOW_SECONDS // 60} min AND "
            f"backoff_floor < {BACKOFF_FLOOR_MAX_THRESHOLD} AND "
            f"UA python proc count ≤ {PROCESS_COUNT_SOFT_LIMIT}"
        ),
        "message": headline,
        "severity_override": severity,
    }

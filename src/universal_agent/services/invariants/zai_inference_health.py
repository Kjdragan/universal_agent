"""ZAI inference health invariant — P4 of the watchdog restoration.

Reads the persistent ZAIRateLimiter snapshot (written by `record_*` calls in
`rate_limiter.py`) AND tails the universal P7 events JSONL
(`zai_inference_events.jsonl`, populated by `zai_observability.py`'s httpx
hook), then counts UA Python processes to detect failure modes that threaten
throughput or — worse — the subscription itself:

1. Sustained 429s (≥3 consecutive) → CRITICAL. Throttling that kills
   throughput. Detected from the snapshot (in-band callers wrapped by
   `with_rate_limit_retry`).
2. Burst of 429s seen by the universal httpx hook in a rolling window →
   CRITICAL. Catches direct-httpx callers that bypass `with_rate_limit_retry`
   — the gap that hid the 2026-05-21 session_dossier 49-burst.
3. ZAI Fair-Use-Policy / concurrency-violation signal in the last 30 min,
   from snapshot OR events file → CRITICAL with no grace period. Ban risk —
   respond NOW.
4. Adaptive backoff_floor at the max cap → CRITICAL. The rate limiter is
   sustained-throttled.
5. UA Python process count above soft limit → WARN. Correlates with the
   above; flags the operator that we're self-induced-choking.

One invariant emits one finding listing every triggered condition in
`observed_value.triggered_conditions` so the operator gets the full picture
per alert. FUP wins severity if present (critical); otherwise 429-tier
conditions are critical; process-count alone is warn.

Why one invariant instead of several: framework emits at most one finding
per invariant. A real bad day would trigger all of these simultaneously
(FUP + 429s + high process count are correlated). Splitting would spam
multiple emails and Task Hub rows for the same root cause.

Cost: one ~1 KB JSON read + tail of an N-line JSONL (capped by file size
budget; we read the full file but the trimmer in zai_observability keeps it
≤10k lines) + one `pgrep` subprocess call per heartbeat. No AI inference.
No DB write. No HTTP.
"""

from __future__ import annotations

from collections import Counter
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Dict, Optional

from universal_agent.rate_limiter import _get_state_path
from universal_agent.services.pipeline_invariants import invariant

logger = logging.getLogger(__name__)


# Thresholds — strict per operator direction 2026-05-20.
CONSECUTIVE_429_CRITICAL = int(os.getenv("UA_ZAI_CONSECUTIVE_429_CRITICAL", "3"))
FUP_DETECT_WINDOW_SECONDS = int(
    os.getenv("UA_ZAI_FUP_DETECT_WINDOW_SECONDS", "1800")
)  # 30 min
PROCESS_COUNT_SOFT_LIMIT = int(os.getenv("UA_PYTHON_PROC_SOFT_LIMIT", "30"))
# Match the rate_limiter's max backoff floor cap (record_429 caps at 8s).
BACKOFF_FLOOR_MAX_THRESHOLD = float(os.getenv("UA_ZAI_BACKOFF_FLOOR_MAX", "8.0"))

# JSONL-events thresholds (the P7 augmentation — catches direct-httpx callers
# that bypass `with_rate_limit_retry`).
EVENTS_429_WINDOW_SECONDS = int(
    os.getenv("UA_ZAI_EVENTS_429_WINDOW_SECONDS", "600")
)  # 10 min
EVENTS_429_CRITICAL_COUNT = int(os.getenv("UA_ZAI_EVENTS_429_CRITICAL_COUNT", "3"))
EVENTS_FUP_WINDOW_SECONDS = int(
    os.getenv("UA_ZAI_EVENTS_FUP_WINDOW_SECONDS", "1800")
)  # 30 min
# Cap on how many events we'll read at the tail. JSONL file is line-trimmed by
# zai_observability so this is a soft belt-and-suspenders bound.
EVENTS_MAX_READ = int(os.getenv("UA_ZAI_EVENTS_MAX_READ", "5000"))


def _events_path() -> Path:
    env_override = os.getenv("UA_ZAI_EVENTS_PATH")
    if env_override:
        return Path(env_override)
    # Match zai_observability._events_path(): four levels up from this file
    # gets us to the repo root, then AGENT_RUN_WORKSPACES/.
    # this file: src/universal_agent/services/invariants/zai_inference_health.py
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "AGENT_RUN_WORKSPACES" / "zai_inference_events.jsonl"


def _scan_recent_events(now: float) -> Dict[str, Any]:
    """Tail the JSONL events file and bucket recent 429s/FUPs by caller.

    Returns a dict with counts and caller attribution over the configured
    rolling windows. Always returns a populated structure (zeros if file
    missing / unreadable) — never raises. The watchdog never crashes on
    bad upstream data.
    """
    out: Dict[str, Any] = {
        "events_429_count": 0,
        "events_429_top_callers": [],
        "events_fup_count": 0,
        "events_fup_top_callers": [],
        "events_last_429_caller": None,
        "events_last_fup_caller": None,
        "events_last_fup_snippet": "",
    }
    path = _events_path()
    if not path.exists():
        return out

    cutoff_429 = now - EVENTS_429_WINDOW_SECONDS
    cutoff_fup = now - EVENTS_FUP_WINDOW_SECONDS
    callers_429: Counter[str] = Counter()
    callers_fup: Counter[str] = Counter()
    last_429: Optional[Dict[str, Any]] = None
    last_fup: Optional[Dict[str, Any]] = None

    try:
        # Read the whole file — it's trimmed by zai_observability (default
        # 10k lines, ~5 MB worst case). For an extra safety net we cap at
        # EVENTS_MAX_READ via tail.
        with open(path) as f:
            lines = f.readlines()
        if len(lines) > EVENTS_MAX_READ:
            lines = lines[-EVENTS_MAX_READ:]
        for raw in lines:
            try:
                event = json.loads(raw)
            except (ValueError, TypeError):
                continue
            ts = event.get("ts")
            if not isinstance(ts, (int, float)):
                continue
            category = event.get("category")
            caller = str(event.get("caller") or "unknown")
            if category == "rate_limited_429" and ts >= cutoff_429:
                callers_429[caller] += 1
                last_429 = event
            elif category == "fup_signal" and ts >= cutoff_fup:
                callers_fup[caller] += 1
                last_fup = event
    except OSError as exc:
        logger.debug("zai_inference_health: events read failed: %s", exc)
        return out

    out["events_429_count"] = sum(callers_429.values())
    out["events_429_top_callers"] = [
        {"caller": c, "count": n} for c, n in callers_429.most_common(5)
    ]
    out["events_fup_count"] = sum(callers_fup.values())
    out["events_fup_top_callers"] = [
        {"caller": c, "count": n} for c, n in callers_fup.most_common(5)
    ]
    if last_429:
        out["events_last_429_caller"] = str(last_429.get("caller") or "unknown")
    if last_fup:
        out["events_last_fup_caller"] = str(last_fup.get("caller") or "unknown")
        out["events_last_fup_snippet"] = str(last_fup.get("body_snippet") or "")[:200]
    return out


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
        "Watches the persistent ZAIRateLimiter snapshot AND the universal P7 "
        "httpx-hook events file (zai_inference_events.jsonl) for sustained "
        "429s (throttling) and FUP signals (ban risk), plus the count of UA "
        "Python processes (concurrency we may be inflicting on ourselves). "
        "Strict thresholds: 3+ consecutive 429s (snapshot), 3+ 429s in last "
        "10 min (events), or any FUP signal in last 30 min fires critical."
    ),
    severity="critical",
    runbook_command=(
        "cat /opt/universal_agent/AGENT_RUN_WORKSPACES/zai_inference_state.json; "
        "tail -200 /opt/universal_agent/AGENT_RUN_WORKSPACES/zai_inference_events.jsonl "
        '| grep -E \'"category":"(rate_limited_429|fup_signal)"\' | tail -50; '
        "pgrep -af 'universal_agent|csi_ingester' | head -50; "
        "journalctl -u universal-agent-gateway --since '30 min ago' --no-pager "
        "| grep -iE 'zai_rate_limit|zai_fup|429'"
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
            "events_429_window_seconds": EVENTS_429_WINDOW_SECONDS,
            "events_429_critical_count": EVENTS_429_CRITICAL_COUNT,
            "events_fup_window_seconds": EVENTS_FUP_WINDOW_SECONDS,
        },
    },
)
def zai_inference_health(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Flag ZAI inference pressure that risks throttling or a FUP ban.

    Combines the persistent ``ZAIRateLimiter`` snapshot, the httpx-hook events
    file (sustained 429s and FUP signals), and the live UA Python process count
    (self-inflicted concurrency). Fires critical on 3+ consecutive 429s, 3+ 429s
    in the recent window, or any FUP signal in the detection window; lesser
    conditions may emit a downgraded ``severity_override``. ``ctx`` is unused —
    all inputs are read from disk/process state. Returns None when healthy.
    """
    snapshot = _read_snapshot()
    process_count = _count_ua_processes()
    now = time.time()
    events_scan = _scan_recent_events(now)

    # If nothing upstream has any data AND process count is fine, stay quiet.
    if (
        snapshot is None
        and events_scan["events_429_count"] == 0
        and events_scan["events_fup_count"] == 0
        and process_count <= PROCESS_COUNT_SOFT_LIMIT
    ):
        return None

    triggered: list[str] = []
    severity = "warn"

    snapshot = snapshot or {}

    # Condition 1: FUP signal in the last 30 min — CRITICAL.
    # Snapshot-side (in-band callers).
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

    # Condition 1b: FUP signal from the universal httpx hook — catches
    # direct-httpx callers that never touch `with_rate_limit_retry`.
    events_fup_active = events_scan["events_fup_count"] > 0
    if events_fup_active and not fup_active:
        triggered.append("fup_active")
        fup_active = True
        severity = "critical"
    elif events_fup_active:
        # Both signals fired; same condition name, don't double-list.
        pass

    # Condition 2: sustained 429s — CRITICAL.
    consecutive_429s = int(snapshot.get("consecutive_429s") or 0)
    if consecutive_429s >= CONSECUTIVE_429_CRITICAL:
        triggered.append("consecutive_429s")
        severity = "critical"

    # Condition 2b: burst of 429s in JSONL rolling window — CRITICAL.
    # This is the gap that hid the 2026-05-21 session_dossier burst from the
    # watchdog: the rate-limiter snapshot saw 0 because the caller didn't go
    # through `with_rate_limit_retry`. The P7 events file did see them.
    events_429_burst = events_scan["events_429_count"] >= EVENTS_429_CRITICAL_COUNT
    if events_429_burst:
        triggered.append("events_429_burst")
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
        # P7 forensic — direct-httpx callers attribution.
        "events_429_count": events_scan["events_429_count"],
        "events_429_window_seconds": EVENTS_429_WINDOW_SECONDS,
        "events_429_top_callers": events_scan["events_429_top_callers"],
        "events_fup_count": events_scan["events_fup_count"],
        "events_fup_window_seconds": EVENTS_FUP_WINDOW_SECONDS,
        "events_fup_top_callers": events_scan["events_fup_top_callers"],
        "events_last_429_caller": events_scan["events_last_429_caller"],
        "events_last_fup_caller": events_scan["events_last_fup_caller"],
        "events_last_fup_snippet": events_scan["events_last_fup_snippet"],
    }

    # Build a human-readable message that names the worst-cause first.
    if fup_active:
        # Prefer the events-side caller attribution if we have it.
        if events_fup_active:
            top = events_scan["events_fup_top_callers"]
            caller_str = (
                top[0]["caller"]
                if top
                else (events_scan["events_last_fup_caller"] or "?")
            )
            snippet = events_scan["events_last_fup_snippet"][:120]
            headline = (
                f"ZAI FUP signal active — {events_scan['events_fup_count']} event(s) "
                f"in last {EVENTS_FUP_WINDOW_SECONDS // 60} min from `{caller_str}`. "
                f"Snippet: {snippet!r}. STOP concurrent inference now and investigate."
            )
        else:
            headline = (
                "ZAI FUP signal active — fair-use / concurrency violation in last "
                f"{FUP_DETECT_WINDOW_SECONDS // 60} min. Snippet: "
                f"{(snapshot.get('last_fup_snippet') or '')[:120]!r}. STOP "
                "concurrent inference now and investigate before resuming."
            )
    elif events_429_burst:
        top = events_scan["events_429_top_callers"]
        caller_str = (
            ", ".join(f"{t['caller']}×{t['count']}" for t in top[:3]) if top else "?"
        )
        headline = (
            f"ZAI 429 burst — {events_scan['events_429_count']} responses in last "
            f"{EVENTS_429_WINDOW_SECONDS // 60} min (top callers: {caller_str}). "
            "Direct-httpx caller bypassing with_rate_limit_retry — wrap it, "
            "lower its concurrency, or move it off China-peak hours."
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
            f"UA python proc count ≤ {PROCESS_COUNT_SOFT_LIMIT} AND "
            f"events-file 429s < {EVENTS_429_CRITICAL_COUNT} in last "
            f"{EVENTS_429_WINDOW_SECONDS // 60} min AND "
            f"events-file FUP count = 0 in last {EVENTS_FUP_WINDOW_SECONDS // 60} min"
        ),
        "message": headline,
        "severity_override": severity,
    }

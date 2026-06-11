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
        "events_429_by_model": [],
        "events_429_fup_texted_count": 0,
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
    models_429: Counter[str] = Counter()
    fup_texted_429 = 0
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
                # Events predating this PR's deploy have no "model" field.
                models_429[str(event.get("model") or "unknown")] += 1
                if event.get("fup_texted"):
                    fup_texted_429 += 1
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
    out["events_429_by_model"] = [
        {"model": m, "count": n} for m, n in models_429.most_common(8)
    ]
    out["events_429_fup_texted_count"] = fup_texted_429
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

    # ── Limiter-managed-throttle discriminator (2026-06-11) ──────────────
    # With UA_LLM_CLASSIFIER_LIMITER_ENABLED routing the hot `_call_llm` seam
    # through the limiter, every burst legitimately ramps consecutive_429s
    # and saturates the backoff floor WHILE the limiter retries and succeeds.
    # Those are managed states, not incidents — alarm on OUTCOMES instead:
    # retries exhausted recently, the FUP acquire-pause being active, or a
    # genuine cliff (fup_active above). A 429 burst in the events file with
    # NO recent snapshot 429 activity still means an un-limited bypasser is
    # hammering ZAI — that stays CRITICAL (the original 2026-05-21 gap).
    def _recent(ts_value: object, window: float) -> bool:
        try:
            return ts_value is not None and (now - float(ts_value)) <= window
        except (TypeError, ValueError):
            return False

    acquire_pause_until = float(snapshot.get("acquire_pause_until") or 0.0)
    pause_active = acquire_pause_until > now
    exhausted_recent = _recent(snapshot.get("last_exhausted_at"), FUP_DETECT_WINDOW_SECONDS)
    snapshot_429_recent = _recent(snapshot.get("last_429_at"), EVENTS_429_WINDOW_SECONDS)
    # The managed DEMOTION only applies while the seam-routing flag is ON:
    # legacy direct-record callers mostly don't increment the exhaustion
    # counters, so without the flag the demotion would mask genuine
    # sustained throttle. The OUTCOME conditions (1c fup_pause_active,
    # 1d retries_exhausted, 3b cross_loop_conflicts) and the recency gates
    # on conditions 2/3 are always-on — they alarm on states that simply
    # did not exist pre-AIMD.
    from universal_agent.feature_flags import _is_truthy

    limiter_routing_enabled = _is_truthy(os.getenv("UA_LLM_CLASSIFIER_LIMITER_ENABLED"))
    limiter_managing = (
        limiter_routing_enabled
        and snapshot_429_recent
        and not exhausted_recent
        and not pause_active
    )

    # Condition 1c: the account-level FUP acquire-pause is in force — CRITICAL.
    if pause_active:
        triggered.append("fup_pause_active")
        severity = "critical"

    # Condition 1d: logical calls ran out of 429 retries recently — CRITICAL.
    # (Wire 429 counts amplify under limiter retries; exhaustion is the
    # outcome that means real work is failing.)
    if exhausted_recent:
        triggered.append("retries_exhausted")
        severity = "critical"

    # Condition 2: sustained 429s — CRITICAL, demoted to a managed WARN when
    # the limiter is actively handling them with no bad outcomes. Gated on
    # snapshot RECENCY in all flag states: consecutive_429s/backoff_floor
    # only decay via record_success, so after a cron subprocess exits (or a
    # managed burst ends) the counters freeze — a streak whose last_429_at
    # is >10 min old is history, not live pressure (the events-burst
    # condition 2b independently covers anything active).
    consecutive_429s = int(snapshot.get("consecutive_429s") or 0)
    if consecutive_429s >= CONSECUTIVE_429_CRITICAL and snapshot_429_recent:
        if limiter_managing:
            triggered.append("consecutive_429s_managed")
        else:
            triggered.append("consecutive_429s")
            severity = "critical"

    # Condition 2b: burst of 429s in JSONL rolling window. CRITICAL when the
    # snapshot shows no recent in-band 429s (= an un-limited bypasser, the gap
    # that hid the 2026-05-21 session_dossier burst); managed WARN otherwise.
    events_429_burst = events_scan["events_429_count"] >= EVENTS_429_CRITICAL_COUNT
    if events_429_burst:
        if limiter_managing:
            triggered.append("events_429_burst_managed")
        else:
            triggered.append("events_429_burst")
            severity = "critical"

    # Condition 3: backoff_floor saturated — same managed demotion, same
    # recency gate (a frozen floor from an exited process is not pressure).
    backoff_floor = float(snapshot.get("backoff_floor") or 0.0)
    backoff_at_max = backoff_floor >= BACKOFF_FLOOR_MAX_THRESHOLD
    if backoff_at_max and snapshot_429_recent:
        if limiter_managing:
            triggered.append("backoff_at_max_managed")
        else:
            triggered.append("backoff_at_max")
            severity = "critical"

    # Condition 3b: two live event loops used the limiter concurrently — the
    # per-loop-primitives guard fired (caps held per-loop, not process-wide).
    # WARN: fix the calling pattern (threadpool asyncio.run bridges). The
    # counter is lifetime-monotonic, so only alert on RECENT conflicts
    # (within the last hour) — otherwise one old conflict re-warns forever.
    cross_loop_conflicts = int(snapshot.get("cross_loop_conflicts") or 0)
    cross_loop_recent = _recent(snapshot.get("last_cross_loop_conflict_at"), 3600.0)
    if cross_loop_conflicts > 0 and cross_loop_recent:
        triggered.append("cross_loop_conflicts")

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
        "fup_pause_active": pause_active,
        "acquire_pause_until": acquire_pause_until or None,
        "retries_exhausted_recent": exhausted_recent,
        "total_429s_exhausted": int(snapshot.get("total_429s_exhausted") or 0),
        "total_succeeded_after_retry": int(snapshot.get("total_succeeded_after_retry") or 0),
        "limiter_managing": limiter_managing,
        "cross_loop_conflicts": cross_loop_conflicts,
        "tier_caps": {
            t: (d or {}).get("cap")
            for t, d in (snapshot.get("tiers") or {}).items()
        },
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
        "events_429_by_model": events_scan["events_429_by_model"],
        "events_429_fup_texted_count": events_scan["events_429_fup_texted_count"],
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
    elif pause_active:
        headline = (
            f"ZAI FUP acquire-pause ACTIVE — the limiter hit the account-level "
            f"cliff and is failing acquires fast for another "
            f"{max(0.0, acquire_pause_until - now):.0f}s. All-tier caps slammed "
            "to minimum; inference resumes automatically when the pause expires."
        )
    elif exhausted_recent:
        headline = (
            f"ZAI logical calls EXHAUSTED their 429 retries recently "
            f"(total_429s_exhausted={observed_value['total_429s_exhausted']}, "
            f"succeeded_after_retry={observed_value['total_succeeded_after_retry']}). "
            "The limiter is retrying but real work is failing — pressure exceeds "
            "what the per-tier caps can absorb."
        )
    elif events_429_burst and not limiter_managing:
        # Neutral reporting: name the top caller(s) by 429 count WITHOUT
        # asserting they bypass the limiter. The ZAI 429 ceiling is
        # account-level, so a fully-compliant limiter user can be throttled
        # as COLLATERAL — the 2026-06-10 incident falsely accused
        # mission_control_chief_of_staff.py and sent the operator chasing the
        # wrong file. Per-model 429 breakdown (when events carry the new
        # `model` field) points at the actual pressure source.
        top = events_scan["events_429_top_callers"]
        caller_str = (
            ", ".join(f"{t['caller']}×{t['count']}" for t in top[:3]) if top else "?"
        )
        by_model = events_scan.get("events_429_by_model") or []
        model_str = (
            "; 429s by model: "
            + ", ".join(f"{m['model']}×{m['count']}" for m in by_model[:5])
            if by_model
            else ""
        )
        # ZAI's standard throttle is a 1313/FUP-texted 429 (gradient, not the
        # cliff). Surfacing how many of these 429s carry that text shows the
        # operator the throttle is the ordinary one, not an account suspension.
        fup_texted = events_scan.get("events_429_fup_texted_count") or 0
        fup_texted_str = (
            f"; {fup_texted} of these carry the 1313/Fair-Usage throttle text "
            "(ordinary gradient, not a cliff)"
            if fup_texted
            else ""
        )
        headline = (
            f"ZAI 429 burst — {events_scan['events_429_count']} responses in last "
            f"{EVENTS_429_WINDOW_SECONDS // 60} min (top callers by 429 count: "
            f"{caller_str}{model_str}{fup_texted_str}). Note: the ZAI 429 limit "
            "is account-level, so a named caller may be throttled as collateral "
            "rather than the cause — use the per-model breakdown and concurrency "
            "settings to find the real pressure source."
        )
    elif limiter_managing and (
        events_429_burst or consecutive_429s >= CONSECUTIVE_429_CRITICAL or backoff_at_max
    ):
        headline = (
            f"ZAI throttle, limiter-managed (WARN) — "
            f"{events_scan['events_429_count']} wire 429s in last "
            f"{EVENTS_429_WINDOW_SECONDS // 60} min, consecutive={consecutive_429s}, "
            f"floor={backoff_floor:.1f}s; retries succeeding "
            f"(succeeded_after_retry={observed_value['total_succeeded_after_retry']}, "
            "exhausted=0 recently). No action needed unless this escalates to "
            "retries_exhausted / fup_pause_active."
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
    elif cross_loop_conflicts > 0 and not high_process_count:
        headline = (
            f"ZAI limiter cross-loop conflicts detected ({cross_loop_conflicts}) — "
            "two live event loops used the limiter concurrently (caps enforced "
            "per-loop, not process-wide). Find and fix the threadpool "
            "asyncio.run() call path (see 10_zai_rate_limiter.md §4.6)."
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

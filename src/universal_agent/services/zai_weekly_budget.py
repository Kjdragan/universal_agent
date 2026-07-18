"""Self-calibrating weekly ZAI budget meter + auto-escalation (R3).

R1 (`services/zai_control.py::handle_weekly_exhaustion`, 2026-07-18) taught the
stack to *react* to ZAI's weekly/monthly quota wall (error 1310) after the fact
— stop retries, auto-pause until the stated reset. This module adds the
*leading* signal: track week-to-date ZAI spend against a learned weekly cap and
proactively trim inference (via `zai_control.apply_level`) BEFORE the account
gets hard-blocked, instead of only reacting once it already has.

**No fixed cap number is hardcoded.** ``observed_cap`` is *learned*:

- Seeded from ``UA_ZAI_WEEKLY_CAP_SEED_TOKENS`` (default 400,000,000 — the
  2026-07 exhausted-week estimate) with ``calibrated_from="seed_estimate"``.
- Replaced automatically the next time a real 1310 lands: when the control
  file's ``weekly_exhaustion`` stamp is newer than what we last calibrated
  from, ``observed_cap`` becomes the week-to-date total AT THAT MOMENT (the
  actual observed wall). The FIRST real 1310 (row still on
  ``"seed_estimate"``) sets this DIRECTLY, no floor — the seed is a rough
  a-priori guess, not an observation, so flooring against it would let a
  too-high seed overshoot the learned cap forever. From the second real 1310
  onward (``calibrated_from`` already ``"1310@..."``), the reading IS floored
  at the prior ``observed_cap`` so a transient under-count (see the
  httpx-retention note below) can never silently LOWER an already-learned
  cap. ``calibrated_from`` becomes ``"1310@<iso last_seen_at>"``.

**Week boundaries** are anchored to ZAI's observed weekly reset instant
(``UA_ZAI_WEEK_RESET_ANCHOR_EPOCH``, default the epoch of 2026-07-19 00:54:25
Asia/Shanghai = 1784393665.0 — computed via ``zoneinfo``, verified at
implementation time) and rolled forward in 7-day increments in code (handles
arbitrary multi-week gaps between meter runs in O(1), no iteration). When a
FRESHER real reset timestamp is available (the control file's
``weekly_exhaustion.reset_at_epoch``), that supersedes the seeded anchor —
self-correcting if ZAI ever shifts its reset schedule.

**Week-to-date** is computed by fanning out across the SAME four capture lanes
`zai_status.py::build_token_usage` uses — `token_consolidation.
analyze_sink_token_usage` (in-process SDK), `analyze_cody_token_usage`
(claude --print subprocess), `read_csi_token_usage` (CSI), and
`zai_status.analyze_token_usage` (httpx JSONL) — then `token_consolidation.
consolidate()`. Cache-INCLUSIVE throughout (cache_read dominates real spend;
see `token_consolidation` module docstring). KNOWN LIMITATION, accepted: the
httpx JSONL lane only retains ~6 days of events, so early in a fresh 7-day
window it can under-count that lane's contribution (~17% of total spend per
the 2026-07 baseline) — the tables involved are small (hundreds of rows/week),
so this is recomputed from scratch (not incrementally accumulated) on every
run: idempotent and self-healing as soon as the window narrows to ≤6 days.

**Escalation** is one-directional: L1 (≥70% of observed_cap), L2 (≥85%), L3
(≥95%) via `zai_control.apply_level(level, by="auto:weekly-budget")` — but
ONLY when the computed target level is STRICTLY GREATER than the control
plane's CURRENT intervention level, and NEVER while a global pause (real
1310 auto-pause, or an operator pause) is active — `maybe_escalate` returns
`None` immediately in that case, before writing anything, because
`apply_level`'s wholesale preset-replace would otherwise silently cancel an
in-flight pause. Escalation never downgrades and never touches an
operator-set level (a human at L2 stays at L2 even if the budget pct says
L1). On a successful escalation, `_stamp_auto_escalation` merge-writes an
`auto_escalation` marker (level / baseline_level / baseline_updated_by /
set_at) recording what the control plane looked like right before our write.

**Release is STATELESS**, evaluated fresh on every `run_meter` pass via
`maybe_release_stale_escalation` — no "is this a new week" gate, no reliance
on in-process or DB state surviving between runs. Every pass: if the
`auto_escalation` stamp is present, the control plane hasn't since been
raised further by anything else, and the CURRENT run's budget-driven target
level has dropped below the stamp's level, and no global pause is active —
restore `apply_level(stamp.baseline_level, by="auto:weekly-budget")` and
clear the stamp. If the operator raised the level further in the meantime,
the stamp is cleared without touching the level (their state wins). This
design is self-healing across week rollover, a crashed/missed run (caught on
the very next pass), and any later writer changing `updated_by` — none of
which the original "release only at the top of a new week, gated on
`updated_by`" design tolerated.

Host: called every ~10 min by `proactive_health_timer_main.py::_run` via
`run_meter(conn)` (fail-soft, log-only on error — never blocks the health
digest). Alerting: `services/invariants/zai_inference_health.py` extends its
existing single-finding probe with `weekly_budget_high` (WARN ≥85%) and
`weekly_budget_critical` (CRITICAL ≥95%), reading this module's persisted
state fail-open — composes with, never duplicates, the existing
`weekly_limit_exhausted` (real-1310) condition. API: `zai_status.py::
build_status` exposes a top-level `weekly_budget` key (fail-soft
``{"available": False}``). Dashboard: `web-ui/app/dashboard/zai-control/
page.tsx` "Weekly budget" card.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
import os
import sqlite3
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

WEEK_SECONDS = 7 * 24 * 3600

# 2026-07-19 00:54:25 Asia/Shanghai (fixed UTC+8, no DST) — the observed ZAI
# weekly reset instant. Verified via:
#   datetime.strptime("2026-07-19 00:54:25", "%Y-%m-%d %H:%M:%S")
#       .replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
#   == 1784393665.0
DEFAULT_ANCHOR_EPOCH = 1784393665.0

DEFAULT_SEED_CAP_TOKENS = 400_000_000

# Escalation / alert thresholds as a fraction of observed_cap (0..1).
LEVEL_1_PCT_DEFAULT = 0.70
LEVEL_2_PCT_DEFAULT = 0.85
LEVEL_3_PCT_DEFAULT = 0.95

TABLE_NAME = "zai_weekly_budget_state"


def _float_env(key: str, default: float) -> float:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _int_env(key: str, default: int) -> int:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default


def _anchor_epoch_base() -> float:
    return _float_env("UA_ZAI_WEEK_RESET_ANCHOR_EPOCH", DEFAULT_ANCHOR_EPOCH)


def _seed_cap_tokens() -> int:
    return _int_env("UA_ZAI_WEEKLY_CAP_SEED_TOKENS", DEFAULT_SEED_CAP_TOKENS)


def _level_thresholds() -> dict[int, float]:
    return {
        1: _float_env("UA_ZAI_WEEKLY_BUDGET_L1_PCT", LEVEL_1_PCT_DEFAULT),
        2: _float_env("UA_ZAI_WEEKLY_BUDGET_L2_PCT", LEVEL_2_PCT_DEFAULT),
        3: _float_env("UA_ZAI_WEEKLY_BUDGET_L3_PCT", LEVEL_3_PCT_DEFAULT),
    }


def target_level(pct: float) -> int:
    """Map a budget-consumed fraction (0..1) to an escalation level 0..3."""
    thresholds = _level_thresholds()
    if pct >= thresholds[3]:
        return 3
    if pct >= thresholds[2]:
        return 2
    if pct >= thresholds[1]:
        return 1
    return 0


# ── Control-file read (fresher reset stamp + calibration source) ──────────


def _control_weekly_exhaustion() -> Optional[dict[str, Any]]:
    """Fail-open read of the control file's `weekly_exhaustion` stamp (written
    by `zai_control.handle_weekly_exhaustion` on a real 1310 detection).
    Never raises."""
    try:
        from universal_agent.services import zai_control

        data = zai_control.read_control() or {}
        weekly = data.get("weekly_exhaustion")
        return weekly if isinstance(weekly, dict) else None
    except Exception:  # noqa: BLE001 — fail open, mirrors zai_control's own contract
        return None


def _calibration_key(weekly: dict[str, Any]) -> Optional[str]:
    """`"1310@<iso last_seen_at>"` — a stable, idempotent key for a given 1310
    sighting so re-running the meter against the SAME stamp doesn't re-fire
    calibration. Returns None if last_seen_at is missing/unparseable."""
    last_seen_at = weekly.get("last_seen_at")
    if last_seen_at is None:
        return None
    try:
        iso = datetime.fromtimestamp(float(last_seen_at), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return f"1310@{iso}"


def resolve_anchor_epoch() -> float:
    """The anchor epoch used for week-boundary math: the env/seed default,
    superseded by a FRESHER real reset timestamp from the control file's
    `weekly_exhaustion` stamp (self-correcting if ZAI shifts its schedule).
    Fails open to the base default on any control-read error."""
    base = _anchor_epoch_base()
    weekly = _control_weekly_exhaustion()
    if weekly:
        try:
            reset_epoch = float(weekly.get("reset_at_epoch"))
        except (TypeError, ValueError):
            reset_epoch = None
        if reset_epoch is not None and reset_epoch > base:
            # int()-truncated (still returned as a float) so week rows keyed
            # on this value never drift across runs from a fractional-second
            # artifact of whatever wrote the control-file stamp.
            return float(int(reset_epoch))
    return base


def current_week_start(now: float, anchor_epoch: Optional[float] = None) -> float:
    """The start-of-week epoch (a reset instant) covering `now`: the anchor
    rolled forward (or, if `now` precedes the anchor entirely, backward) by
    whole 7-day increments. O(1) — handles arbitrary multi-week gaps without
    iterating."""
    anchor = resolve_anchor_epoch() if anchor_epoch is None else anchor_epoch
    if now < anchor:
        k = math.ceil((anchor - now) / WEEK_SECONDS)
        return anchor - k * WEEK_SECONDS
    k = math.floor((now - anchor) / WEEK_SECONDS)
    return anchor + k * WEEK_SECONDS


# ── Week-to-date compute (cache-inclusive, 4-lane fan-out) ─────────────────


def compute_week_to_date(now: float, week_start: float, top_n: int = 5) -> dict[str, Any]:
    """Cache-inclusive week-to-date token total across every ZAI capture lane.
    Fail-soft per-lane: one lane erroring never blocks the others. Returns
    ``{"week_to_date_tokens": int, "window_seconds": int, "totals": {...}}``.
    """
    from universal_agent.services import token_consolidation as tc, zai_status

    window_seconds = max(0, int(now - week_start))
    sources: list[dict[str, Any]] = []

    for reader in (
        tc.analyze_sink_token_usage,
        tc.analyze_cody_token_usage,
        tc.read_csi_token_usage,
    ):
        try:
            sources.append(reader(now, window_seconds, top_n=top_n))
        except Exception as exc:  # noqa: BLE001 — one lane must never break the meter
            logger.debug(
                "zai_weekly_budget: lane reader %s failed: %s", reader.__name__, exc
            )

    try:
        httpx_src = zai_status.analyze_token_usage(now, window_seconds, top_n=top_n)
        zai_status._make_cache_inclusive(httpx_src)
        sources.append(httpx_src)
    except Exception as exc:  # noqa: BLE001
        logger.debug("zai_weekly_budget: httpx lane failed: %s", exc)

    consolidated = tc.consolidate(sources, top_n=top_n)
    return {
        "week_to_date_tokens": int(consolidated["totals"].get("total_tokens") or 0),
        "window_seconds": window_seconds,
        "totals": consolidated["totals"],
    }


# ── Persisted state (task_hub.py::ensure_schema table) ─────────────────────


def _latest_row(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        f"SELECT * FROM {TABLE_NAME} ORDER BY week_anchor_epoch DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def _get_or_create_week_row(conn: sqlite3.Connection, week_start: float) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        f"SELECT * FROM {TABLE_NAME} WHERE week_anchor_epoch = ?", (week_start,)
    ).fetchone()
    if row:
        return dict(row)

    # New week: carry forward calibration from the most recent PRIOR week (the
    # learned cap outlives the week it was calibrated in) rather than
    # resetting to the seed every week.
    prev = _latest_row(conn)
    observed_cap: Optional[int] = None
    calibrated_from: Optional[str] = None
    if prev and float(prev.get("week_anchor_epoch") or 0.0) < week_start:
        observed_cap = prev.get("observed_cap")
        calibrated_from = prev.get("calibrated_from")
    if observed_cap is None:
        observed_cap = _seed_cap_tokens()
        calibrated_from = "seed_estimate"

    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        f"""INSERT INTO {TABLE_NAME}
            (week_anchor_epoch, observed_cap, week_to_date_tokens, last_computed_at,
             last_escalation_level, last_escalated_at, calibrated_from, updated_at)
            VALUES (?, ?, 0, ?, 0, NULL, ?, ?)""",
        (week_start, int(observed_cap), time.time(), calibrated_from, now_iso),
    )
    conn.commit()
    row = conn.execute(
        f"SELECT * FROM {TABLE_NAME} WHERE week_anchor_epoch = ?", (week_start,)
    ).fetchone()
    return dict(row)


def maybe_calibrate(row: dict[str, Any], week_to_date_tokens: int) -> Optional[dict[str, Any]]:
    """If the control file carries a `weekly_exhaustion` stamp NEWER than what
    this row was last calibrated from, return the calibration update
    ``{"observed_cap", "calibrated_from"}`` — else None (no-op, most calls).

    The floor-against-prior-value guard (`max(week_to_date_tokens now, prior
    observed_cap)`, protecting against the httpx-retention undershoot — see
    module docstring) applies ONLY once a REAL 1310 has already calibrated
    this row (``calibrated_from`` starts with ``"1310@"``). The FIRST real
    1310 calibration — when the row is still on ``"seed_estimate"`` (or has
    no ``calibrated_from`` at all) — sets ``observed_cap`` to the week-to-date
    total DIRECTLY, with no floor: the seed is a rough a-priori guess, not an
    observation, so flooring a real wall reading against it would let a
    too-high seed permanently overshoot the learned cap forever.
    """
    weekly = _control_weekly_exhaustion()
    if not weekly:
        return None
    key = _calibration_key(weekly)
    if key is None or key == row.get("calibrated_from"):
        return None
    prior_calibrated_from = row.get("calibrated_from")
    if prior_calibrated_from and str(prior_calibrated_from).startswith("1310@"):
        prior_cap = int(row.get("observed_cap") or 0)
        new_cap = max(int(week_to_date_tokens), prior_cap)
    else:
        new_cap = int(week_to_date_tokens)
    return {"observed_cap": new_cap, "calibrated_from": key}


# ── Escalation (never-downgrade; new-week release) ─────────────────────────


def _is_globally_paused_fail_open() -> bool:
    """(bool) whether a global pause is currently active. A control-read
    failure is treated as NOT paused (fail-open, matching every other
    `zai_control` read) — the caller is responsible for its own additional
    guards; this helper only isolates the read from the caller's try/except
    so a single control hiccup can't be conflated with other failure modes."""
    try:
        from universal_agent.services import zai_control

        paused, _ = zai_control.is_globally_paused()
        return bool(paused)
    except Exception:  # noqa: BLE001 — fail open: treat a read failure as not-paused
        return False


def _stamp_auto_escalation(level: int, baseline_level: int, baseline_updated_by: Any) -> None:
    """Merge-write the `auto_escalation` marker onto the control file AFTER a
    successful `apply_level` call — read/mutate/write, the same pattern
    `zai_control.handle_weekly_exhaustion` uses for its own `weekly_exhaustion`
    stamp. Records the PRE-escalation baseline (level + updated_by) so a later
    run can restore it exactly, even across an escalation-on-top-of-an-
    operator-baseline (e.g. operator at L1, we escalate to L2 — baseline_level
    stays 1, so release lands back at L1, not L0). Fail-soft: a write failure
    here never raises — the escalation itself already succeeded; only the
    stateless-release bookkeeping is at risk, and the next run's read of a
    missing stamp is a safe no-op."""
    try:
        from universal_agent.services import zai_control

        data = zai_control.read_control()
        data["auto_escalation"] = {
            "level": level,
            "baseline_level": baseline_level,
            "baseline_updated_by": baseline_updated_by,
            "set_at": time.time(),
        }
        zai_control.write_control(data)
    except Exception:  # noqa: BLE001 — fail-soft, never raise
        logger.warning("zai_weekly_budget: failed to stamp auto_escalation", exc_info=True)


def maybe_escalate(pct: float) -> Optional[int]:
    """Apply the target intervention level via `zai_control.apply_level` ONLY
    when it is STRICTLY GREATER than the control plane's current level.
    Never downgrades; never touches an operator- or 1310-set level unless our
    own target is higher.

    Guarded against an ACTIVE global pause (real 1310 auto-pause, or an
    operator-set global pause): `apply_level` writes a FULL preset for the
    target level, which has no `global_pause.active` of its own at L1-L3 —
    writing over an active pause would silently cancel it mid-exhaustion.
    So this returns None immediately whenever a global pause is active,
    before computing or writing anything. A control-read failure during that
    check is treated as NOT paused (fail-open, mirrors every other
    `zai_control` read) rather than blocking escalation.

    On a successful escalation, also stamps an `auto_escalation` marker
    (`_stamp_auto_escalation`) recording the pre-escalation baseline so a
    later `run_meter` pass can release it statelessly (see
    `maybe_release_stale_escalation`) — no in-process or "new week" state is
    needed for the release to fire correctly.

    Returns the level actually applied, or None.
    """
    if _is_globally_paused_fail_open():
        return None

    target = target_level(pct)
    if target <= 0:
        return None
    try:
        from universal_agent.services import zai_control

        current = zai_control.current_state()
        current_level = int(current.get("intervention_level") or 0)
        if target <= current_level:
            return None
        baseline_level = current_level
        baseline_updated_by = current.get("updated_by")
        zai_control.apply_level(
            target,
            reason=f"weekly_budget {pct * 100:.0f}% of observed cap",
            by="auto:weekly-budget",
        )
        _stamp_auto_escalation(target, baseline_level, baseline_updated_by)
        return target
    except Exception:  # noqa: BLE001 — fail-soft, mirrors zai_control's contract
        logger.warning("zai_weekly_budget: maybe_escalate failed", exc_info=True)
        return None


def maybe_release_stale_escalation(target: int) -> bool:
    """Stateless release of our own prior escalation — evaluated fresh on
    EVERY `run_meter` pass (not gated on "is this a new week"), so it's
    self-healing across week rollover, a crashed/missed run, and any later
    writer changing `updated_by`.

    Logic:
    - No `auto_escalation` stamp on the control file → no-op (nothing to do).
    - An active global pause → no-op entirely (neither restore nor clear —
      wait for the pause to clear before touching anything).
    - The control plane's CURRENT `intervention_level` is already above the
      stamp's `level` (an operator raised it further since we escalated) →
      the operator's higher state wins: clear the stamp and stand down,
      WITHOUT downgrading anything.
    - Otherwise, if `target` (this run's freshly computed budget-driven
      level) is STILL >= the stamp's `level`, there's nothing to release yet
      — leave the stamp in place.
    - Otherwise (`target` has dropped below the stamp's level and nothing
      has raised it further) — restore the pre-escalation `baseline_level`
      via `apply_level` and remove the stamp.

    Returns True iff a restore (`apply_level` to the baseline) was applied.
    Never raises — fail-soft, mirrors every other function in this module.
    """
    try:
        from universal_agent.services import zai_control

        if _is_globally_paused_fail_open():
            return False

        data = zai_control.read_control()
        stamp = data.get("auto_escalation")
        if not isinstance(stamp, dict):
            return False

        stamp_level = int(stamp.get("level") or 0)
        current_level = int(zai_control.current_state().get("intervention_level") or 0)

        if current_level > stamp_level:
            # The operator (or something else) raised the level further since
            # we escalated — their higher state wins. Stand down: clear our
            # stamp, do not touch the level.
            data.pop("auto_escalation", None)
            zai_control.write_control(data)
            return False

        if target >= stamp_level:
            # Budget-driven level is still at/above what we escalated to —
            # nothing to release yet; leave the stamp in place.
            return False

        baseline_level = int(stamp.get("baseline_level") or 0)
        zai_control.apply_level(
            baseline_level,
            reason="weekly_budget stale-escalation release",
            by="auto:weekly-budget",
        )
        # apply_level carries over the still-present auto_escalation stamp
        # (it's in the _APPLY_LEVEL_STAMP_KEYS allowlist) — a second
        # read/mutate/write clears it now that the restore has landed.
        data2 = zai_control.read_control()
        data2.pop("auto_escalation", None)
        zai_control.write_control(data2)
        return True
    except Exception:  # noqa: BLE001 — fail-soft
        logger.warning(
            "zai_weekly_budget: maybe_release_stale_escalation failed", exc_info=True
        )
        return False


# ── Host entrypoint ─────────────────────────────────────────────────────────


def run_meter(conn: sqlite3.Connection, *, now: Optional[float] = None) -> dict[str, Any]:
    """Compute → calibrate → persist → maybe-escalate. Called every ~10 min
    by `proactive_health_timer_main.py::_run`. Fail-soft: never raises;
    returns ``{"available": False, "error": ...}`` on any internal failure so
    the health timer's guard is a pure belt-and-suspenders backstop."""
    now = time.time() if now is None else now
    try:
        from universal_agent import task_hub

        task_hub.ensure_schema(conn)

        week_start = current_week_start(now)
        prev_latest = _latest_row(conn)
        is_new_week = (
            prev_latest is not None
            and float(prev_latest.get("week_anchor_epoch") or 0.0) < week_start
        )
        row = _get_or_create_week_row(conn, week_start)

        wtd = compute_week_to_date(now, week_start)
        week_to_date_tokens = int(wtd["week_to_date_tokens"])

        # NOTE: `or _seed_cap_tokens()` would be wrong here — since fix 5, a
        # first real 1310 calibration can legitimately persist observed_cap=0
        # (a degenerate but valid reading), and 0 is falsy in Python. Use an
        # explicit None-check so a real zero is never silently replaced by
        # the seed default.
        _stored_cap = row.get("observed_cap")
        observed_cap = int(_stored_cap) if _stored_cap is not None else _seed_cap_tokens()
        calibrated_from = row.get("calibrated_from") or "seed_estimate"
        calibration = maybe_calibrate(row, week_to_date_tokens)
        if calibration:
            observed_cap = int(calibration["observed_cap"])
            calibrated_from = calibration["calibrated_from"]

        pct = (week_to_date_tokens / observed_cap) if observed_cap > 0 else 0.0
        target = target_level(pct)

        # Stateless release: evaluated every pass (not gated on "is this a
        # new week"), so week rollover, a missed/crashed run, and any later
        # writer changing updated_by are all self-healing — see
        # `maybe_release_stale_escalation`'s docstring.
        released = maybe_release_stale_escalation(target)

        applied_level = maybe_escalate(pct)

        last_escalation_level = int(row.get("last_escalation_level") or 0)
        last_escalated_at = row.get("last_escalated_at")
        if applied_level is not None:
            last_escalation_level = applied_level
            last_escalated_at = now
        elif released:
            last_escalation_level = 0

        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            f"""UPDATE {TABLE_NAME}
                SET observed_cap = ?, week_to_date_tokens = ?, last_computed_at = ?,
                    last_escalation_level = ?, last_escalated_at = ?, calibrated_from = ?,
                    updated_at = ?
                WHERE week_anchor_epoch = ?""",
            (
                observed_cap,
                week_to_date_tokens,
                now,
                last_escalation_level,
                last_escalated_at,
                calibrated_from,
                now_iso,
                week_start,
            ),
        )
        conn.commit()

        return {
            "available": True,
            "week_anchor_epoch": week_start,
            "reset_at_epoch": week_start + WEEK_SECONDS,
            "observed_cap": observed_cap,
            "week_to_date_tokens": week_to_date_tokens,
            "pct": pct,
            "calibrated_from": calibrated_from,
            "last_escalation_level": last_escalation_level,
            "last_escalated_at": last_escalated_at,
            "applied_level": applied_level,
            "is_new_week": is_new_week,
            "released_stale_escalation": released,
        }
    except Exception as exc:  # noqa: BLE001 — fail-soft, never raise (host contract)
        logger.warning("zai_weekly_budget: run_meter failed", exc_info=True)
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}


# ── Read-only snapshot (invariant + status endpoint) ────────────────────────


def _open_activity_conn() -> Optional[sqlite3.Connection]:
    """Read-only connection to activity_state.db (where the state table
    lives). Returns None if the DB file doesn't exist yet — never raises."""
    try:
        from universal_agent.durable.db import get_activity_db_path

        path = get_activity_db_path()
    except Exception:  # noqa: BLE001
        return None
    if not path or not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro&uri=true", uri=True, timeout=2.0)
        conn.execute("PRAGMA busy_timeout=2000;")
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as exc:  # noqa: BLE001
        logger.debug("zai_weekly_budget: activity_state.db ro open failed: %s", exc)
        return None


def read_latest_state(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
    """Read-only snapshot of the most recent week's row. Fail-soft None."""
    try:
        row = conn.execute(
            f"SELECT * FROM {TABLE_NAME} ORDER BY week_anchor_epoch DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    except Exception as exc:  # noqa: BLE001 — e.g. table doesn't exist yet
        logger.debug("zai_weekly_budget: read_latest_state failed: %s", exc)
        return None


def get_status_snapshot() -> dict[str, Any]:
    """Public, read-only snapshot for `zai_status.py::build_status` and the
    `zai_inference_health` invariant. Opens its own read-only connection
    (never writes). Fails soft to ``{"available": False}`` — missing DB,
    missing table (meter never ran), or any error, never raises."""
    conn = _open_activity_conn()
    if conn is None:
        return {"available": False}
    try:
        row = read_latest_state(conn)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    if not row:
        return {"available": False}

    observed_cap = int(row.get("observed_cap") or 0)
    week_to_date_tokens = int(row.get("week_to_date_tokens") or 0)
    pct = (week_to_date_tokens / observed_cap) if observed_cap > 0 else 0.0
    week_anchor_epoch = row.get("week_anchor_epoch")
    reset_at_epoch = (
        float(week_anchor_epoch) + WEEK_SECONDS if week_anchor_epoch is not None else None
    )

    return {
        "available": True,
        "week_anchor_epoch": week_anchor_epoch,
        "reset_at_epoch": reset_at_epoch,
        "observed_cap": observed_cap,
        "week_to_date_tokens": week_to_date_tokens,
        "pct": round(pct, 4),
        "calibrated_from": row.get("calibrated_from"),
        "last_escalation_level": int(row.get("last_escalation_level") or 0),
        "last_escalated_at": row.get("last_escalated_at"),
        "last_computed_at": row.get("last_computed_at"),
        "updated_at": row.get("updated_at"),
    }

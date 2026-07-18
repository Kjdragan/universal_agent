"""Tests for the self-calibrating weekly ZAI budget meter (R3,
services/zai_weekly_budget.py).

Covers: anchor rolling (incl. multi-week gaps + control-stamp supersession),
week-to-date cache-inclusive compute from seeded activity-DB rows, seed-cap
fallback, calibration overwrite on a fresh 1310 stamp, threshold→level
mapping, never-downgrade escalation, new-week release (only for our own
`updated_by`), and fail-soft behavior on a missing csi.db / corrupt control
file.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import time
from zoneinfo import ZoneInfo

import pytest

from universal_agent.services import zai_weekly_budget as zwb

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def activity_db(tmp_path, monkeypatch):
    """Real temp activity_state.db with schema, reusing the
    test_token_consolidation.py fixture pattern."""
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(tmp_path / "activity.db"))
    from universal_agent.durable.db import connect_runtime_db, get_activity_db_path
    from universal_agent.task_hub import ensure_schema

    conn = connect_runtime_db(get_activity_db_path())
    ensure_schema(conn)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def isolated_control(tmp_path, monkeypatch):
    """Isolated zai_control control file, reusing the test_zai_control.py
    fixture pattern. Autouse so every test gets a clean control file."""
    from universal_agent.services import zai_control

    monkeypatch.setenv("UA_ZAI_CONTROL_PATH", str(tmp_path / "zai_control.json"))
    zai_control._invalidate_cache()
    yield
    zai_control._invalidate_cache()


@pytest.fixture(autouse=True)
def no_csi(tmp_path, monkeypatch):
    """Point CSI at a nonexistent path by default so that lane fails soft
    (available=False) instead of touching a real CSI DB."""
    monkeypatch.setenv("CSI_DB_PATH", str(tmp_path / "no_such_csi.db"))
    yield


def _insert_sink(conn, *, principal, model, input_t, output_t, cache_read, ts):
    conn.execute(
        """INSERT INTO token_usage_events
           (ts, recorded_at, source, principal, model, caller, caller_fn, status,
            input_tokens, output_tokens, cache_creation_input_tokens,
            cache_read_input_tokens, total_cost_usd, num_turns)
           VALUES (?,?, 'cli-in-process', ?,?,?,?, 'ok', ?,?,0,?, 0.5, 1)""",
        (ts, "2026-07-18T00:00:00", principal, model, principal, f"{principal}::turn",
         input_t, output_t, cache_read),
    )
    conn.commit()


def _future_1310_body(hours_ahead: float = 48.0) -> str:
    target = datetime.now(tz=ZoneInfo("Asia/Shanghai")) + timedelta(hours=hours_ahead)
    ts_str = target.strftime("%Y-%m-%d %H:%M:%S")
    return (
        "[1310][Weekly/Monthly Limit Exhausted. Your limit will reset at "
        f"{ts_str}][20260718191643db4f685336574732]"
    )


# ── Anchor epoch literal ─────────────────────────────────────────────────────


def test_anchor_epoch_literal_matches_verified_value():
    """2026-07-19 00:54:25 Asia/Shanghai == 1784393665.0 (verified via
    zoneinfo at implementation time)."""
    naive = datetime.strptime("2026-07-19 00:54:25", "%Y-%m-%d %H:%M:%S")
    aware = naive.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    assert aware.timestamp() == zwb.DEFAULT_ANCHOR_EPOCH == 1784393665.0


# ── Anchor rolling ───────────────────────────────────────────────────────────


def test_week_start_rolls_forward_one_week():
    anchor = zwb.DEFAULT_ANCHOR_EPOCH
    now = anchor + 10 * 86400  # 10 days after anchor
    assert zwb.current_week_start(now, anchor_epoch=anchor) == anchor + zwb.WEEK_SECONDS


def test_week_start_rolls_forward_multi_week_gap():
    """A multi-week gap between meter runs must be handled in O(1), not by
    iterating week by week."""
    anchor = zwb.DEFAULT_ANCHOR_EPOCH
    now = anchor + 25 * 86400  # ~3.57 weeks after anchor -> k=3
    assert zwb.current_week_start(now, anchor_epoch=anchor) == anchor + 3 * zwb.WEEK_SECONDS


def test_week_start_at_exact_boundary():
    anchor = zwb.DEFAULT_ANCHOR_EPOCH
    now = anchor + 2 * zwb.WEEK_SECONDS  # exactly on a reset instant
    assert zwb.current_week_start(now, anchor_epoch=anchor) == anchor + 2 * zwb.WEEK_SECONDS


def test_week_start_before_anchor_rolls_backward():
    anchor = zwb.DEFAULT_ANCHOR_EPOCH
    now = anchor - 3 * 86400  # 3 days before the anchor
    assert zwb.current_week_start(now, anchor_epoch=anchor) == anchor - zwb.WEEK_SECONDS


def test_week_start_far_before_anchor_multi_week_gap():
    anchor = zwb.DEFAULT_ANCHOR_EPOCH
    now = anchor - 16 * 86400  # >2 weeks before -> k=3 (ceil(16/7))
    assert zwb.current_week_start(now, anchor_epoch=anchor) == anchor - 3 * zwb.WEEK_SECONDS


def test_anchor_superseded_by_fresher_control_stamp():
    """A real observed reset (control file's weekly_exhaustion.reset_at_epoch)
    newer than the seeded anchor supersedes it — self-correcting."""
    from universal_agent.services import zai_control

    fresher = zwb.DEFAULT_ANCHOR_EPOCH + 2 * 86400
    zai_control.write_control(
        {"weekly_exhaustion": {"last_seen_at": time.time(), "reset_at_epoch": fresher, "source": "t"}}
    )
    zai_control._invalidate_cache()

    assert zwb.resolve_anchor_epoch() == fresher
    now = fresher + 100
    assert zwb.current_week_start(now) == fresher


def test_resolve_anchor_epoch_int_truncates_adopted_stamp():
    """A fresher control-stamp reset_at_epoch with a fractional part must be
    int()-truncated on adoption so week rows keyed on this value never drift
    across runs from a fractional-second artifact of whatever wrote it."""
    from universal_agent.services import zai_control

    fresher = zwb.DEFAULT_ANCHOR_EPOCH + 2 * 86400 + 0.987654
    zai_control.write_control(
        {"weekly_exhaustion": {"last_seen_at": time.time(), "reset_at_epoch": fresher, "source": "t"}}
    )
    zai_control._invalidate_cache()

    resolved = zwb.resolve_anchor_epoch()
    assert resolved == float(int(fresher))
    assert resolved == resolved // 1  # integer-valued float


def test_stale_control_stamp_does_not_supersede_anchor():
    """A control stamp OLDER than the seeded anchor must not override it."""
    from universal_agent.services import zai_control

    older = zwb.DEFAULT_ANCHOR_EPOCH - 86400
    zai_control.write_control(
        {"weekly_exhaustion": {"last_seen_at": time.time(), "reset_at_epoch": older, "source": "t"}}
    )
    zai_control._invalidate_cache()

    assert zwb.resolve_anchor_epoch() == zwb.DEFAULT_ANCHOR_EPOCH


def test_resolve_anchor_epoch_fails_open_on_control_error(monkeypatch):
    from universal_agent.services import zai_control

    def boom(*a, **k):
        raise RuntimeError("control broken")

    monkeypatch.setattr(zai_control, "read_control", boom)
    assert zwb.resolve_anchor_epoch() == zwb.DEFAULT_ANCHOR_EPOCH


# ── Week-to-date compute (cache-inclusive) ──────────────────────────────────


def test_compute_week_to_date_cache_inclusive(activity_db):
    now = time.time()
    week_start = now - 3600  # 1h ago
    _insert_sink(
        activity_db, principal="simone", model="glm-5.1",
        input_t=100_000, output_t=5_000, cache_read=900_000, ts=now - 1800,
    )
    result = zwb.compute_week_to_date(now, week_start)
    assert result["week_to_date_tokens"] == 100_000 + 5_000 + 900_000
    assert result["window_seconds"] == 3600


def test_compute_week_to_date_excludes_rows_outside_window(activity_db):
    now = time.time()
    week_start = now - 3600
    _insert_sink(
        activity_db, principal="simone", model="glm-5.1",
        input_t=100, output_t=10, cache_read=1000, ts=now - 1800,
    )
    _insert_sink(
        activity_db, principal="simone", model="glm-5.1",
        input_t=99999, output_t=9999, cache_read=99999, ts=now - 100_000,  # older than window
    )
    result = zwb.compute_week_to_date(now, week_start)
    assert result["week_to_date_tokens"] == 1110


def test_compute_week_to_date_fails_soft_missing_activity_db(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(tmp_path / "does_not_exist.db"))
    now = time.time()
    result = zwb.compute_week_to_date(now, now - 3600)
    assert result["week_to_date_tokens"] == 0


# ── Seed cap when no 1310 ────────────────────────────────────────────────────


def test_run_meter_seeds_cap_when_no_1310(activity_db, monkeypatch):
    monkeypatch.setenv("UA_ZAI_WEEKLY_CAP_SEED_TOKENS", "123456")
    now = time.time()
    result = zwb.run_meter(activity_db, now=now)
    assert result["available"] is True
    assert result["observed_cap"] == 123456
    assert result["calibrated_from"] == "seed_estimate"


def test_run_meter_default_seed_cap(activity_db):
    result = zwb.run_meter(activity_db, now=time.time())
    assert result["observed_cap"] == zwb.DEFAULT_SEED_CAP_TOKENS
    assert result["calibrated_from"] == "seed_estimate"


def test_run_meter_persists_row(activity_db):
    now = time.time()
    zwb.run_meter(activity_db, now=now)
    row = zwb.read_latest_state(activity_db)
    assert row is not None
    assert row["calibrated_from"] == "seed_estimate"


# ── Calibration overwrite on a fresh 1310 stamp ──────────────────────────────


def test_calibration_overwrites_seed_on_fresh_1310_stamp(activity_db):
    from universal_agent.services import zai_control

    now = time.time()
    _insert_sink(
        activity_db, principal="simone", model="glm-5.1",
        input_t=1_000_000, output_t=0, cache_read=0, ts=now - 10,
    )
    r1 = zwb.run_meter(activity_db, now=now)
    assert r1["calibrated_from"] == "seed_estimate"

    zai_control.handle_weekly_exhaustion(_future_1310_body(), source="test")
    r2 = zwb.run_meter(activity_db, now=now + 5)
    assert r2["calibrated_from"].startswith("1310@")
    assert r2["observed_cap"] >= 1_000_000


def test_calibration_is_idempotent_for_same_stamp(activity_db):
    """Re-running the meter against the SAME 1310 stamp must not re-trigger
    calibration (calibrated_from key already matches)."""
    from universal_agent.services import zai_control

    now = time.time()
    zai_control.handle_weekly_exhaustion(_future_1310_body(), source="test")
    r1 = zwb.run_meter(activity_db, now=now)
    cap_after_first = r1["observed_cap"]

    # A new (larger) row would change observed_cap if calibration re-fired.
    _insert_sink(
        activity_db, principal="simone", model="glm-5.1",
        input_t=999_999_999, output_t=0, cache_read=0, ts=now - 1,
    )
    r2 = zwb.run_meter(activity_db, now=now + 1)
    assert r2["calibrated_from"] == r1["calibrated_from"]
    assert r2["observed_cap"] == cap_after_first  # unchanged — no re-calibration


def test_calibration_never_lowers_observed_cap(activity_db):
    """The floor-at-prior-value guard: a fresh 1310 stamp with a LOWER
    week-to-date reading (e.g. httpx-retention undershoot) must not shrink
    observed_cap below what it already learned. This guard applies once the
    row is already on a REAL prior calibration (`1310@...`) — see
    `test_maybe_calibrate_first_real_1310_overrides_seed_overshoot` for the
    seed_estimate case, which is deliberately NOT floored."""
    row = {"observed_cap": 5_000_000, "calibrated_from": "1310@2026-07-01T00:00:00+00:00"}
    from universal_agent.services import zai_control

    zai_control.handle_weekly_exhaustion(_future_1310_body(), source="test")
    calibration = zwb.maybe_calibrate(row, week_to_date_tokens=100)  # much lower
    assert calibration is not None
    assert calibration["observed_cap"] == 5_000_000  # floored at prior value


def test_maybe_calibrate_first_real_1310_overrides_seed_overshoot():
    """The FIRST real 1310 calibration off a seed_estimate row must trust the
    observed wall DIRECTLY, with no floor against the seed — otherwise a
    too-high seed permanently overshoots the learned cap forever."""
    row = {"observed_cap": 400_000_000, "calibrated_from": "seed_estimate"}
    from universal_agent.services import zai_control

    zai_control.handle_weekly_exhaustion(_future_1310_body(), source="test")
    calibration = zwb.maybe_calibrate(row, week_to_date_tokens=250_000_000)
    assert calibration is not None
    assert calibration["observed_cap"] == 250_000_000  # NOT floored at the seed


def test_maybe_calibrate_first_real_1310_with_null_calibrated_from():
    """Same no-floor behavior when calibrated_from is missing/None entirely
    (not just the string "seed_estimate")."""
    row = {"observed_cap": 400_000_000, "calibrated_from": None}
    from universal_agent.services import zai_control

    zai_control.handle_weekly_exhaustion(_future_1310_body(), source="test")
    calibration = zwb.maybe_calibrate(row, week_to_date_tokens=250_000_000)
    assert calibration is not None
    assert calibration["observed_cap"] == 250_000_000


def test_maybe_calibrate_second_real_1310_still_floors_at_prior_cap():
    """Once a row is already on a REAL calibration (`1310@...`), the
    floor-at-prior-value guard still applies — a transient httpx-retention
    undershoot on a LATER 1310 must not shrink an already-learned cap."""
    row = {"observed_cap": 250_000_000, "calibrated_from": "1310@2026-07-01T00:00:00+00:00"}
    from universal_agent.services import zai_control

    zai_control.handle_weekly_exhaustion(_future_1310_body(), source="test")
    calibration = zwb.maybe_calibrate(row, week_to_date_tokens=100)  # much lower
    assert calibration is not None
    assert calibration["observed_cap"] == 250_000_000  # floored — unchanged


def test_maybe_calibrate_noop_when_no_1310():
    row = {"observed_cap": 1000, "calibrated_from": "seed_estimate"}
    assert zwb.maybe_calibrate(row, week_to_date_tokens=500) is None


# ── Threshold boundary mapping ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "pct,expected",
    [
        (0.0, 0),
        (0.69, 0),
        (0.70, 1),
        (0.84, 1),
        (0.85, 2),
        (0.94, 2),
        (0.95, 3),
        (1.5, 3),
    ],
)
def test_target_level_thresholds(pct, expected):
    assert zwb.target_level(pct) == expected


def test_target_level_thresholds_env_overridable(monkeypatch):
    monkeypatch.setenv("UA_ZAI_WEEKLY_BUDGET_L1_PCT", "0.5")
    monkeypatch.setenv("UA_ZAI_WEEKLY_BUDGET_L2_PCT", "0.6")
    monkeypatch.setenv("UA_ZAI_WEEKLY_BUDGET_L3_PCT", "0.7")
    assert zwb.target_level(0.55) == 1
    assert zwb.target_level(0.65) == 2
    assert zwb.target_level(0.75) == 3


# ── Never-downgrade escalation ───────────────────────────────────────────────


def test_maybe_escalate_applies_when_target_above_current():
    from universal_agent.services import zai_control

    applied = zwb.maybe_escalate(pct=0.80)  # target level 1
    assert applied == 1
    assert zai_control.current_state()["intervention_level"] == 1
    assert zai_control.current_state()["updated_by"] == "auto:weekly-budget"


def test_maybe_escalate_never_downgrades():
    from universal_agent.services import zai_control

    zai_control.apply_level(2, reason="operator manual", by="dashboard")
    applied = zwb.maybe_escalate(pct=0.75)  # target level 1 < current 2
    assert applied is None
    assert zai_control.current_state()["intervention_level"] == 2
    assert zai_control.current_state()["updated_by"] == "dashboard"


def test_maybe_escalate_noop_below_l1_threshold():
    from universal_agent.services import zai_control

    applied = zwb.maybe_escalate(pct=0.10)
    assert applied is None
    assert zai_control.current_state()["intervention_level"] == 0


def test_maybe_escalate_fails_soft_on_control_error(monkeypatch):
    from universal_agent.services import zai_control

    def boom(*a, **k):
        raise RuntimeError("control broken")

    monkeypatch.setattr(zai_control, "current_state", boom)
    assert zwb.maybe_escalate(pct=0.99) is None  # must not raise


def test_maybe_escalate_never_cancels_active_global_pause():
    """BLOCKER regression: an active global pause (e.g. the R1 1310 auto-pause,
    or an operator pause) must never be clobbered by an escalation write.
    apply_level(1..3) writes a preset with no global_pause.active of its
    own — writing over an active pause would silently cancel it mid-
    exhaustion. maybe_escalate must return None immediately, before writing
    anything, and the pause must be untouched."""
    from universal_agent.services import zai_control

    zai_control.set_global_pause(True, ttl_seconds=3600, reason="zai_1310_weekly_limit_exhausted")
    zai_control._invalidate_cache()
    before = zai_control.read_control()

    applied = zwb.maybe_escalate(pct=1.0)  # 100% — would target L3 if not guarded

    assert applied is None
    zai_control._invalidate_cache()
    after = zai_control.read_control()
    assert zai_control.is_globally_paused()[0] is True
    assert after.get("global_pause") == before.get("global_pause")
    assert after.get("intervention_level") == before.get("intervention_level")


def test_is_globally_paused_fail_open_helper_treats_error_as_not_paused(monkeypatch):
    """The pause-guard helper itself must fail OPEN (not paused) on a
    control-read error — a transient read glitch at this ONE check must
    never masquerade as "always paused" and permanently block legitimate
    escalation. Verified in isolation: `zai_control.current_state()` (called
    later in `maybe_escalate`) ALSO depends on `is_globally_paused`, so a
    fully-broken control subsystem legitimately fails the whole call soft to
    None (see `test_maybe_escalate_fails_soft_on_control_error`) — this test
    pins the guard's own fail-open contract independent of that."""
    from universal_agent.services import zai_control

    def boom(*a, **k):
        raise RuntimeError("control broken")

    monkeypatch.setattr(zai_control, "is_globally_paused", boom)
    assert zwb._is_globally_paused_fail_open() is False


# ── Stateless release (R3 fix 3+4) ──────────────────────────────────────────
# Replaces the old updated_by-keyed, new-week-gated one-shot release: every
# run_meter pass evaluates the auto_escalation stamp fresh, so week rollover,
# a crashed/missed run, and any later writer changing updated_by are all
# self-healing.


def _stamp(level, baseline_level, baseline_updated_by=None, set_at=None):
    """Write an auto_escalation stamp directly (bypassing maybe_escalate) so
    unit tests can drive maybe_release_stale_escalation in isolation."""
    from universal_agent.services import zai_control

    data = zai_control.read_control()
    data["auto_escalation"] = {
        "level": level,
        "baseline_level": baseline_level,
        "baseline_updated_by": baseline_updated_by,
        "set_at": set_at if set_at is not None else time.time(),
    }
    zai_control.write_control(data)
    zai_control._invalidate_cache()


def test_release_noop_when_no_stamp():
    assert zwb.maybe_release_stale_escalation(target=0) is False


def test_release_restores_baseline_when_target_drops_below_stamp():
    from universal_agent.services import zai_control

    zai_control.apply_level(2, reason="auto weekly", by="auto:weekly-budget")
    _stamp(level=2, baseline_level=0)

    released = zwb.maybe_release_stale_escalation(target=0)  # budget dropped
    assert released is True
    assert zai_control.current_state()["intervention_level"] == 0
    assert zai_control.read_control().get("auto_escalation") is None


def test_release_restores_operator_baseline_not_l0():
    """Escalating from an operator baseline (e.g. L1 -> L2) must release back
    to L1, not L0 — the stamp's baseline_level carries the pre-escalation
    state, not a hardcoded zero."""
    from universal_agent.services import zai_control

    zai_control.apply_level(1, reason="operator manual", by="dashboard")
    zai_control.apply_level(2, reason="auto weekly", by="auto:weekly-budget")
    _stamp(level=2, baseline_level=1, baseline_updated_by="dashboard")

    released = zwb.maybe_release_stale_escalation(target=0)
    assert released is True
    assert zai_control.current_state()["intervention_level"] == 1


def test_release_skipped_when_target_still_at_or_above_stamp_level():
    from universal_agent.services import zai_control

    zai_control.apply_level(2, reason="auto weekly", by="auto:weekly-budget")
    _stamp(level=2, baseline_level=0)

    released = zwb.maybe_release_stale_escalation(target=2)  # budget unchanged
    assert released is False
    assert zai_control.current_state()["intervention_level"] == 2
    assert zai_control.read_control().get("auto_escalation") is not None


def test_release_stands_down_when_operator_raised_level_further():
    """If the operator raised the level ABOVE our stamped level since we
    escalated, their higher state wins: clear the stamp, do NOT downgrade."""
    from universal_agent.services import zai_control

    zai_control.apply_level(1, reason="auto weekly", by="auto:weekly-budget")
    _stamp(level=1, baseline_level=0)
    zai_control.apply_level(3, reason="operator override", by="dashboard")

    released = zwb.maybe_release_stale_escalation(target=0)
    assert released is False
    assert zai_control.current_state()["intervention_level"] == 3  # untouched
    assert zai_control.read_control().get("auto_escalation") is None  # cleared


def test_release_skipped_while_global_pause_active():
    """A real 1310 (or operator) global pause in force must block the
    release entirely — neither restoring nor clearing the stamp — until the
    pause clears."""
    from universal_agent.services import zai_control

    zai_control.apply_level(2, reason="auto weekly", by="auto:weekly-budget")
    _stamp(level=2, baseline_level=0)
    zai_control.set_global_pause(True, ttl_seconds=3600, reason="zai_1310_weekly_limit_exhausted")
    zai_control._invalidate_cache()

    released = zwb.maybe_release_stale_escalation(target=0)
    assert released is False
    assert zai_control.is_globally_paused()[0] is True
    assert zai_control.read_control().get("auto_escalation") is not None  # untouched


def test_maybe_escalate_stamps_auto_escalation_with_baseline():
    """maybe_escalate itself must stamp the pre-escalation baseline (level +
    updated_by) so a later release restores the RIGHT state."""
    from universal_agent.services import zai_control

    zai_control.apply_level(1, reason="operator manual", by="dashboard")
    applied = zwb.maybe_escalate(pct=0.90)  # target level 2 > current 1
    assert applied == 2
    stamp = zai_control.read_control().get("auto_escalation")
    assert stamp is not None
    assert stamp["level"] == 2
    assert stamp["baseline_level"] == 1
    assert stamp["baseline_updated_by"] == "dashboard"


def test_run_meter_rollover_release_restores_baseline(activity_db):
    """End-to-end: run_meter at week N escalates us to L1 off a hot week;
    week N+1 starts near 0% — the very next run_meter call (now firmly in
    week N+1) must release back to L0 via the stateless mechanism, with no
    reliance on an "is this a new week" gate."""
    from universal_agent.services import zai_control

    anchor = zwb.DEFAULT_ANCHOR_EPOCH
    week1_now = anchor + 100  # early in week 1 (starts at anchor)
    _insert_sink(
        activity_db, principal="simone", model="glm-5.1",
        input_t=zwb.DEFAULT_SEED_CAP_TOKENS, output_t=0, cache_read=0, ts=week1_now - 10,
    )
    r1 = zwb.run_meter(activity_db, now=week1_now)
    assert r1["applied_level"] is not None and r1["applied_level"] >= 1
    assert zai_control.current_state()["updated_by"] == "auto:weekly-budget"

    week2_now = anchor + zwb.WEEK_SECONDS + 100  # into week 2, no new spend yet
    r2 = zwb.run_meter(activity_db, now=week2_now)
    assert r2["is_new_week"] is True
    assert r2["released_stale_escalation"] is True
    assert zai_control.current_state()["intervention_level"] == 0
    assert zai_control.read_control().get("auto_escalation") is None


def test_run_meter_crash_simulated_missed_release_still_releases_next_run(activity_db):
    """Simulate a crash/missed run_meter pass: the stamp from week N's
    escalation survives untouched across a run_meter call that (for
    whatever reason — a skipped tick, a crash) never got the chance to
    release it. The VERY NEXT run must still release, statelessly — no
    reliance on catching the exact rollover tick."""
    from universal_agent.services import zai_control

    anchor = zwb.DEFAULT_ANCHOR_EPOCH
    week1_now = anchor + 100
    _insert_sink(
        activity_db, principal="simone", model="glm-5.1",
        input_t=zwb.DEFAULT_SEED_CAP_TOKENS, output_t=0, cache_read=0, ts=week1_now - 10,
    )
    r1 = zwb.run_meter(activity_db, now=week1_now)
    assert r1["applied_level"] is not None and r1["applied_level"] >= 1
    assert zai_control.read_control().get("auto_escalation") is not None

    # Simulate the "missed" tick at the exact rollover instant by jumping
    # straight to well INTO week 2 instead of running right at the boundary —
    # the stamp from week 1 is still sitting there, untouched, days later.
    week2_later_now = anchor + zwb.WEEK_SECONDS + (2 * 86400)
    r2 = zwb.run_meter(activity_db, now=week2_later_now)
    assert r2["released_stale_escalation"] is True
    assert zai_control.current_state()["intervention_level"] == 0
    assert zai_control.read_control().get("auto_escalation") is None


# ── Fail-soft: missing csi.db / corrupt control file ─────────────────────────


def test_run_meter_fails_soft_on_corrupt_control_file(activity_db, isolated_control, tmp_path):
    control_path = tmp_path / "zai_control.json"
    control_path.write_text("{not valid json")
    from universal_agent.services import zai_control

    zai_control._invalidate_cache()

    result = zwb.run_meter(activity_db, now=time.time())
    # zai_control fails open to {} — run_meter must still succeed with the seed cap.
    assert result["available"] is True
    assert result["calibrated_from"] == "seed_estimate"


def test_get_status_snapshot_fails_soft_missing_db(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(tmp_path / "nope.db"))
    assert zwb.get_status_snapshot() == {"available": False}


def test_get_status_snapshot_fails_soft_no_table(tmp_path, monkeypatch):
    """A DB file exists but the meter never ran (table absent / empty)."""
    import sqlite3

    path = tmp_path / "activity.db"
    sqlite3.connect(str(path)).close()  # create empty file, no schema
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(path))
    assert zwb.get_status_snapshot() == {"available": False}


def test_get_status_snapshot_shape_when_available(activity_db):
    now = time.time()
    zwb.run_meter(activity_db, now=now)
    snap = zwb.get_status_snapshot()
    assert snap["available"] is True
    for key in (
        "week_anchor_epoch", "reset_at_epoch", "observed_cap",
        "week_to_date_tokens", "pct", "calibrated_from",
        "last_escalation_level",
    ):
        assert key in snap

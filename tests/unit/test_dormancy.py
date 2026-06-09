"""Direct unit coverage for ``services.dormancy``.

These tests pin the canonical Houston active window (06:00 inclusive ..
22:00 exclusive, America/Chicago) and the permissive-on-error /
opt-in-declared-mode contracts. Every time-dependent assertion uses a
FIXED, tz-explicit input (epoch seconds or a tz-aware datetime) so the
results are deterministic regardless of the machine's local timezone --
``is_active_window()`` with the default ``None`` (current wall clock) is
deliberately never asserted on.

Date choices are DST-stable on purpose:
  * 2026-01-15 is winter -> Chicago is CST (UTC-6),
  * 2026-06-15 is summer -> Chicago is CDT (UTC-5),
neither sits near a spring-forward / fall-back transition, so the
Chicago-local hour of each constructed instant is unambiguous.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from universal_agent.services import dormancy
from universal_agent.services.dormancy import (
    ACTIVE_END_HOUR,
    ACTIVE_START_HOUR,
    HOUSTON_TZ,
    active_window_desc,
    is_active_window,
    is_dormant,
    should_run,
)

CHICAGO = ZoneInfo(HOUSTON_TZ)

# Two DST-stable reference dates with known, fixed UTC offsets.
WINTER_DATE = (2026, 1, 15)  # CST, UTC-6
SUMMER_DATE = (2026, 6, 15)  # CDT, UTC-5


def _chicago_dt(date: tuple[int, int, int], hour: int) -> datetime:
    """Tz-aware Chicago-local instant at ``hour`` on ``date``."""
    y, m, d = date
    return datetime(y, m, d, hour, 0, tzinfo=CHICAGO)


def _epoch_for_chicago(date: tuple[int, int, int], hour: int) -> float:
    """UNIX epoch (UTC seconds) for the same Chicago-local instant."""
    return _chicago_dt(date, hour).timestamp()


# ---------------------------------------------------------------------------
# Boundary behaviour: 05 dormant, 06 active (inclusive start), 21 active,
# 22 dormant (exclusive end). Asserted via BOTH input shapes used by the
# real call sites -- epoch ints/floats and tz-aware America/Chicago
# datetimes -- to prove they agree.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("date", [WINTER_DATE, SUMMER_DATE])
@pytest.mark.parametrize(
    ("hour", "expected_active"),
    [
        (5, False),   # 05:00 -> dormant (before inclusive start)
        (6, True),    # 06:00 -> active (inclusive start)
        (21, True),   # 21:00 -> active (last active hour)
        (22, False),  # 22:00 -> dormant (exclusive end)
    ],
)
def test_boundaries_epoch_and_datetime_agree(date, hour, expected_active):
    aware = _chicago_dt(date, hour)
    epoch = _epoch_for_chicago(date, hour)

    # tz-aware datetime input (proactive_pipeline_invariants._now_houston).
    assert is_active_window(aware) is expected_active
    assert is_dormant(aware) is (not expected_active)

    # epoch input (cron_artifact_reminders, gateway_server).
    assert is_active_window(epoch) is expected_active
    assert is_dormant(epoch) is (not expected_active)


def test_boundary_known_offset_guard():
    """Lock the constructed instants to a known offset so a boundary test
    cannot silently pass for the wrong reason (e.g. a tz-database shift)."""
    assert _chicago_dt(WINTER_DATE, 6).utcoffset().total_seconds() == -6 * 3600
    assert _chicago_dt(SUMMER_DATE, 6).utcoffset().total_seconds() == -5 * 3600


# ---------------------------------------------------------------------------
# Full 24-hour equivalence: prove no drift from the historical
# ``6 <= hour <= 21`` / ``6 <= hour < 22`` expressions.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("date", [WINTER_DATE, SUMMER_DATE])
def test_full_24h_equivalence(date):
    for h in range(24):
        expected = ACTIVE_START_HOUR <= h < ACTIVE_END_HOUR
        assert expected == (6 <= h < 22)  # canonical == historical
        aware = _chicago_dt(date, h)
        assert is_active_window(aware) is expected, f"hour={h} aware mismatch"
        assert is_active_window(_epoch_for_chicago(date, h)) is expected, (
            f"hour={h} epoch mismatch"
        )


# ---------------------------------------------------------------------------
# Naive datetime == UTC contract (dormancy._to_houston_hour lines 98-101).
# A naive datetime is treated as UTC, so it must match the equivalent
# tz-aware UTC datetime and the equivalent epoch.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "utc_hour",
    list(range(0, 24, 3)),  # sample across the day
)
def test_naive_datetime_treated_as_utc(utc_hour):
    naive = datetime(2026, 6, 15, utc_hour, 0)  # no tzinfo
    aware_utc = naive.replace(tzinfo=timezone.utc)
    epoch = aware_utc.timestamp()

    assert is_active_window(naive) == is_active_window(aware_utc)
    assert is_active_window(naive) == is_active_window(epoch)


# ---------------------------------------------------------------------------
# Permissive-on-error: if the timezone lookup raises, is_active_window
# returns True (and is_dormant returns False) so alerts are never dropped.
# ---------------------------------------------------------------------------
def test_permissive_on_error(monkeypatch):
    def _boom(_now):
        raise RuntimeError("tzdata unavailable")

    monkeypatch.setattr(dormancy, "_to_houston_hour", _boom)

    # Use a fixed dormant instant; permissiveness must override it.
    dormant_epoch = _epoch_for_chicago(WINTER_DATE, 3)
    assert is_active_window(dormant_epoch) is True
    assert is_dormant(dormant_epoch) is False


# ---------------------------------------------------------------------------
# should_run: declared-mode gate.
# ---------------------------------------------------------------------------
def test_should_run_always_ignores_window():
    # 'always' runs even at a dormant hour.
    dormant_epoch = _epoch_for_chicago(WINTER_DATE, 3)
    assert should_run("always", now=dormant_epoch) is True


def test_should_run_dormancy_aware_follows_window():
    active_epoch = _epoch_for_chicago(WINTER_DATE, 12)
    dormant_epoch = _epoch_for_chicago(WINTER_DATE, 3)
    assert should_run("dormancy_aware", now=active_epoch) is True
    assert should_run("dormancy_aware", now=dormant_epoch) is False


def test_should_run_unrecognized_mode_fails_open():
    dormant_epoch = _epoch_for_chicago(WINTER_DATE, 3)
    assert should_run("nonsense", now=dormant_epoch) is True


@pytest.mark.parametrize("truthy", ["1", "true", "YES", "on", "On", "TRUE"])
def test_should_run_env_override_truthy_forces_dormancy_aware(truthy):
    dormant_epoch = _epoch_for_chicago(WINTER_DATE, 3)
    active_epoch = _epoch_for_chicago(WINTER_DATE, 12)
    env = {"UA_DORMANCY": truthy}
    # Declared 'always' but env forces dormancy_aware -> follows window.
    assert should_run(
        "always", env_var="UA_DORMANCY", env=env, now=dormant_epoch
    ) is False
    assert should_run(
        "always", env_var="UA_DORMANCY", env=env, now=active_epoch
    ) is True


@pytest.mark.parametrize("falsy", ["0", "false", "no", "off", "", "garbage"])
def test_should_run_env_override_falsy_forces_always(falsy):
    dormant_epoch = _epoch_for_chicago(WINTER_DATE, 3)
    env = {"UA_DORMANCY": falsy}
    # Declared 'dormancy_aware' but env forces 'always' -> runs anyway.
    assert should_run(
        "dormancy_aware", env_var="UA_DORMANCY", env=env, now=dormant_epoch
    ) is True


def test_should_run_env_var_absent_keeps_mode():
    dormant_epoch = _epoch_for_chicago(WINTER_DATE, 3)
    env: dict[str, str] = {}
    # env_var named but not present -> mode used unchanged.
    assert should_run(
        "dormancy_aware", env_var="UA_DORMANCY", env=env, now=dormant_epoch
    ) is False
    assert should_run(
        "always", env_var="UA_DORMANCY", env=env, now=dormant_epoch
    ) is True


def test_should_run_does_not_mutate_passed_env():
    env = {"UA_DORMANCY": "1"}
    snapshot = dict(env)
    should_run(
        "always", env_var="UA_DORMANCY", env=env,
        now=_epoch_for_chicago(WINTER_DATE, 3),
    )
    assert env == snapshot


# ---------------------------------------------------------------------------
# active_window_desc: cosmetic but part of the public API.
# ---------------------------------------------------------------------------
def test_active_window_desc_mentions_bounds_and_tz():
    desc = active_window_desc()
    assert "06:00-22:00" in desc
    assert HOUSTON_TZ in desc
    assert "dormant" in desc

"""Drift guard: scheduled-job windows stay pinned to the single source of truth.

``services/dormancy.py`` owns the 6 AM-10 PM Houston active window. Windowed
in-process cron registrations build their hour field from
``dormancy.cron_hour_field()`` (so they cannot drift), and the static systemd
``deployment/systemd/*.timer`` OnCalendar specs — which are plain text that
cannot import Python and only take effect on reinstall — are pinned here so a
hand-edit cannot silently diverge from the constants.

Phase 2 of the dormancy single-source migration (Phase 1 consolidated the
runtime checks into ``dormancy.is_active_window``).
"""

from __future__ import annotations

from pathlib import Path
import re

from universal_agent.services import dormancy

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SYSTEMD_DIR = _REPO_ROOT / "deployment" / "systemd"

# The "06..21" hour-range token inside an OnCalendar value such as
# "*-*-* 06..21:00:00 America/Chicago".
_HOUR_RANGE_RE = re.compile(r"(\d{2}\.\.\d{2})")


def test_cron_hour_field_derives_from_constants() -> None:
    assert dormancy.cron_hour_field() == (
        f"{dormancy.ACTIVE_START_HOUR}-{dormancy.ACTIVE_END_HOUR - 1}"
    )
    assert dormancy.cron_hour_field() == "6-21"


def test_systemd_hour_range_derives_from_constants() -> None:
    assert dormancy.systemd_hour_range() == (
        f"{dormancy.ACTIVE_START_HOUR:02d}..{dormancy.ACTIVE_END_HOUR - 1:02d}"
    )
    assert dormancy.systemd_hour_range() == "06..21"


def _windowed_oncalendar_entries() -> list[tuple[str, str]]:
    """Return ``(timer_filename, hour_range)`` for every committed ``.timer``
    whose OnCalendar uses an ``H..H`` hour range (i.e. a dormancy window)."""
    entries: list[tuple[str, str]] = []
    for timer in sorted(_SYSTEMD_DIR.glob("*.timer")):
        for line in timer.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped.startswith("OnCalendar="):
                continue
            match = _HOUR_RANGE_RE.search(stripped[len("OnCalendar="):])
            if match:
                entries.append((timer.name, match.group(1)))
    return entries


def test_systemd_dir_exists() -> None:
    assert _SYSTEMD_DIR.is_dir(), f"missing systemd unit dir: {_SYSTEMD_DIR}"


def test_windowed_timers_match_dormancy_window() -> None:
    windowed = _windowed_oncalendar_entries()
    assert windowed, "expected at least one windowed systemd timer (an H..H range)"
    expected = dormancy.systemd_hour_range()
    mismatches = [(name, rng) for name, rng in windowed if rng != expected]
    assert not mismatches, (
        "systemd timer OnCalendar hour-range(s) diverged from "
        f"dormancy.systemd_hour_range() ({expected!r}): {mismatches}. "
        "Update deployment/systemd/*.timer (and reinstall on the VPS) or the "
        "dormancy constants together."
    )


def test_known_windowed_timers_present() -> None:
    names = {name for name, _ in _windowed_oncalendar_entries()}
    for expected in (
        "universal-agent-hourly-intel-digest.timer",
        "universal-agent-csi-convergence-sync.timer",
        "universal-agent-artifact-reminders-sweep.timer",
    ):
        assert expected in names, (
            f"{expected} is no longer a windowed (H..H) timer — confirm the "
            "schedule change was intentional before updating this guard."
        )

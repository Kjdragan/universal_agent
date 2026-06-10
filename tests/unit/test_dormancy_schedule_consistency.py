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

# Timers intentionally widened to a full 24h OnCalendar range that gate
# execution at RUNTIME via dormancy.should_run, controlled by a UA_<JOB>_24_7
# env var inside their ExecStart script, rather than via the schedule. Default
# behaviour stays windowed (the env var is the opt-IN-to-24/7 lever); the
# schedule fires every hour so the per-run gate can decide. These are exempt
# from the strict 06..21 match below but pinned to the full-day range so a
# hand-edit to some other partial window is still caught.
_FULL_DAY_RANGE = "00..23"
_RUNTIME_GATED_TIMERS = {
    "universal-agent-hourly-intel-digest.timer",
    "universal-agent-csi-convergence-sync.timer",
}

# ExecStart script (relative to repo root) -> the UA_<JOB>_24_7 env var its
# runtime gate reads (truthy -> run 24/7; unset -> windowed). A widened 24/7
# timer is only safe if its script actually gates at runtime, so this pairs
# every runtime-gated timer with its gate.
_RUNTIME_GATED_SCRIPTS = {
    "src/universal_agent/scripts/hourly_intel_digest_cron.py": "UA_INTEL_DIGEST_24_7",
    "src/universal_agent/scripts/csi_convergence_sync.py": "UA_CSI_CONVERGENCE_SYNC_24_7",
}


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
    mismatches = [
        (name, rng)
        for name, rng in windowed
        if name not in _RUNTIME_GATED_TIMERS and rng != expected
    ]
    assert not mismatches, (
        "systemd timer OnCalendar hour-range(s) diverged from "
        f"dormancy.systemd_hour_range() ({expected!r}): {mismatches}. "
        "Update deployment/systemd/*.timer (and reinstall on the VPS) or the "
        "dormancy constants together. (Timers that intentionally run 24/7 and "
        "gate at runtime belong in _RUNTIME_GATED_TIMERS, not here.)"
    )


def test_runtime_gated_timers_are_full_day() -> None:
    """Runtime-gated timers must use the full-day OnCalendar range.

    Their dormancy decision lives in the ExecStart script via
    ``dormancy.should_run(env_var="UA_<JOB>_DORMANCY")``, so the schedule is
    deliberately 24/7. Pinning to ``00..23`` keeps a hand-edit to some other
    partial window from silently re-narrowing the schedule (which would defeat
    the runtime opt-out: a flipped env var could never fire overnight).
    """
    by_name = dict(_windowed_oncalendar_entries())
    for name in sorted(_RUNTIME_GATED_TIMERS):
        assert by_name.get(name) == _FULL_DAY_RANGE, (
            f"{name} is a runtime-gated 24/7 timer and must use OnCalendar "
            f"hour-range {_FULL_DAY_RANGE!r} (it gates at runtime via "
            f"should_run); found {by_name.get(name)!r}."
        )


def test_known_windowed_timers_present() -> None:
    names = {name for name, _ in _windowed_oncalendar_entries()}
    for expected in (
        "universal-agent-artifact-reminders-sweep.timer",
    ):
        assert expected in names, (
            f"{expected} is no longer a windowed (H..H) timer — confirm the "
            "schedule change was intentional before updating this guard."
        )


def test_runtime_gated_scripts_call_should_run() -> None:
    """Every runtime-gated job's ExecStart script must gate at runtime.

    A widened 24/7 timer with no ``should_run`` gate would run the job
    overnight unconditionally — the opposite of the opt-OUT default. Pin that
    each script imports/calls ``dormancy.should_run`` with the matching
    ``UA_<JOB>_DORMANCY`` env var and the windowed-by-default mode, so a
    schedule widening can never ship without its gate.
    """
    for rel, env_var in sorted(_RUNTIME_GATED_SCRIPTS.items()):
        body = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "should_run(" in body, f"{rel} must call dormancy.should_run()"
        assert env_var in body, f"{rel} must gate on env var {env_var!r}"
        assert '"dormancy_aware"' in body, (
            f"{rel} should_run gate must default to dormancy_aware (windowed) so "
            f"the default (env var unset) stays windowed; only a truthy "
            f"{env_var} flips it to mode='always' (24/7)."
        )

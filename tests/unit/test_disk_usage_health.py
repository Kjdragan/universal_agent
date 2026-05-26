"""Tests for the disk_usage_health invariant.

Background: Simone's 2026-05-20 morning digest flagged disk at 70% with a
"climbing" trend and recommended an invariant for proactive detection. P5
adds one. Same lightweight pattern as the P4 zai_inference_health probe:
no DB, no AI inference, single fast syscall + small pure-logic block.

Thresholds (operator-strict per pattern set in P4):
- WARN: any monitored mount above 75%
- CRITICAL: any monitored mount above 90%
- Severity is the worst-of across monitored mounts
"""

from __future__ import annotations

from collections import namedtuple
import importlib
from pathlib import Path
from unittest.mock import patch

import pytest

from universal_agent.services import pipeline_invariants as pi
from universal_agent.services.pipeline_invariants import (
    clear_registry_for_tests,
    run_invariants,
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    clear_registry_for_tests()
    from universal_agent.services.invariants import disk_usage_health
    importlib.reload(disk_usage_health)
    yield
    clear_registry_for_tests()


DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])


def _gb(n: float) -> int:
    return int(n * 1024 * 1024 * 1024)


def _mock_disk_usage(mounts: dict[str, DiskUsage]):
    """Patch shutil.disk_usage to return controlled values per path."""
    def _impl(path):
        path_str = str(path)
        # Walk up looking for a known mount
        for mount, usage in mounts.items():
            if path_str.startswith(mount):
                return usage
        return mounts.get("/", DiskUsage(_gb(100), _gb(10), _gb(90)))
    return patch(
        "universal_agent.services.invariants.disk_usage_health.shutil.disk_usage",
        side_effect=_impl,
    )


def test_registers_on_import():
    ids = {inv.id for inv in pi.get_registered_invariants()}
    assert "disk_usage_health" in ids


def test_all_mounts_healthy_emits_nothing():
    """Every mount well below the warn threshold (75%) → no finding."""
    with _mock_disk_usage({
        "/": DiskUsage(_gb(200), _gb(40), _gb(160)),       # 20%
        "/opt": DiskUsage(_gb(100), _gb(30), _gb(70)),     # 30%
        "/var/lib": DiskUsage(_gb(100), _gb(50), _gb(50)), # 50%
    }):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "disk_usage_health"]
    assert matches == []


def test_seventy_five_percent_does_not_fire():
    """At the exact threshold (75%) we don't fire — the gate is strictly above."""
    with _mock_disk_usage({
        "/": DiskUsage(_gb(100), _gb(75), _gb(25)),  # exactly 75%
    }):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "disk_usage_health"]
    assert matches == []


def test_above_seventy_five_percent_fires_warn():
    """Disk above 75% → warn. Simone's 70%-and-climbing case crossing the gate."""
    with _mock_disk_usage({
        "/": DiskUsage(_gb(100), _gb(80), _gb(20)),  # 80%
    }):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "disk_usage_health"]
    assert len(matches) == 1
    assert matches[0].severity == "warn"
    obs = matches[0].observed_value or {}
    pressured = obs.get("pressured_mounts") or []
    assert any(m.get("mount") == "/" and m.get("used_pct") >= 75 for m in pressured)


def test_above_ninety_percent_fires_critical():
    """Disk above 90% is catastrophic-class — critical severity."""
    with _mock_disk_usage({
        "/": DiskUsage(_gb(100), _gb(92), _gb(8)),  # 92%
    }):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "disk_usage_health"]
    assert len(matches) == 1
    assert matches[0].severity == "critical"


def test_worst_mount_drives_severity():
    """If /opt is fine but / is critical, finding is critical. Worst-of."""
    with _mock_disk_usage({
        "/": DiskUsage(_gb(100), _gb(95), _gb(5)),     # 95% critical
        "/opt": DiskUsage(_gb(100), _gb(30), _gb(70)), # 30% healthy
    }):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "disk_usage_health"]
    assert len(matches) == 1
    assert matches[0].severity == "critical"


def test_multiple_pressured_mounts_listed_in_one_finding():
    """Both root and /opt above 75% → ONE finding listing both."""
    with _mock_disk_usage({
        "/": DiskUsage(_gb(100), _gb(82), _gb(18)),    # 82%
        "/opt": DiskUsage(_gb(100), _gb(78), _gb(22)), # 78%
    }):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "disk_usage_health"]
    assert len(matches) == 1
    obs = matches[0].observed_value or {}
    pressured = {m["mount"] for m in obs.get("pressured_mounts") or []}
    # At least the two pressured mounts present (others may be there if monitored too)
    assert "/" in pressured
    assert "/opt" in pressured


def test_disk_usage_oserror_silent():
    """If a mount path doesn't exist on the host (dev box without /var/lib),
    skip that mount gracefully — don't crash the watchdog."""
    def _raise(path):
        raise FileNotFoundError(path)
    with patch(
        "universal_agent.services.invariants.disk_usage_health.shutil.disk_usage",
        side_effect=_raise,
    ):
        findings = run_invariants({})
    # Probe handled the error; either no finding, or a finding with empty
    # pressured_mounts. Critically: no probe_error finding was emitted.
    probe_errors = [f for f in findings if "probe_error" in (f.metric_key or "")]
    assert probe_errors == []


def test_finding_includes_runbook_command():
    with _mock_disk_usage({
        "/": DiskUsage(_gb(100), _gb(85), _gb(15)),
    }):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "disk_usage_health"]
    assert len(matches) == 1
    runbook = matches[0].runbook_command or ""
    # Useful runbook mentions df + AGENT_RUN_WORKSPACES (most likely cleanup target)
    assert "df" in runbook or "AGENT_RUN_WORKSPACES" in runbook


def test_observed_value_includes_gb_used_and_free():
    """Operator should be able to size the problem from the finding alone."""
    with _mock_disk_usage({
        "/": DiskUsage(_gb(200), _gb(160), _gb(40)),  # 80%
    }):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "disk_usage_health"]
    assert len(matches) == 1
    obs = matches[0].observed_value or {}
    pressured = obs.get("pressured_mounts") or []
    root = next((m for m in pressured if m["mount"] == "/"), None)
    assert root is not None
    assert root.get("used_gb") and root.get("free_gb") and root.get("total_gb")

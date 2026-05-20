"""Tests for the zai_inference_health invariant.

Watchdog probe that reads the ZAIRateLimiter snapshot + counts UA Python
processes. Strict severity per operator direction 2026-05-20:
- 3+ consecutive 429s → CRITICAL (throttling kills throughput)
- ANY FUP event in last 24h → CRITICAL (ban risk; respond NOW)
- backoff_floor at max → CRITICAL (sustained throttle)
- Python process count > soft limit → WARN (correlates with the above)
"""

from __future__ import annotations

import importlib
import json
import time
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
    from universal_agent.services.invariants import zai_inference_health
    importlib.reload(zai_inference_health)
    yield
    clear_registry_for_tests()


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    state_file = tmp_path / "zai_inference_state.json"
    monkeypatch.setenv("UA_ZAI_INFERENCE_STATE_PATH", str(state_file))
    yield state_file


def _write_snapshot(path: Path, **fields) -> None:
    base = {
        "max_concurrent": 2,
        "backoff_floor": 1.0,
        "consecutive_429s": 0,
        "total_429s": 0,
        "total_requests": 100,
        "total_fup_events": 0,
        "last_429_at": None,
        "last_success_at": time.time(),
        "last_fup_at": None,
        "last_fup_snippet": "",
        "last_fup_context": "",
    }
    base.update(fields)
    path.write_text(json.dumps(base))


def _mock_process_count(count: int):
    """Patch the pgrep-equivalent so tests don't depend on host process state."""
    return patch(
        "universal_agent.services.invariants.zai_inference_health._count_ua_processes",
        return_value=count,
    )


def test_registers_on_import():
    ids = {inv.id for inv in pi.get_registered_invariants()}
    assert "zai_inference_health" in ids


def test_no_snapshot_is_silent(isolated_state):
    """No state file (fresh deploy) → no finding. Watchdog never crashes on
    missing upstream."""
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert matches == []


def test_healthy_snapshot_emits_nothing(isolated_state):
    _write_snapshot(isolated_state)  # all zeros
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert matches == []


def test_three_consecutive_429s_fires_critical(isolated_state):
    """Per operator: sustained 429s = throttling that kills throughput.
    Threshold strict at 3 (not 5)."""
    _write_snapshot(
        isolated_state,
        consecutive_429s=3,
        total_429s=15,
        last_429_at=time.time(),
    )
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert len(matches) == 1
    assert matches[0].severity == "critical"
    obs = matches[0].observed_value or {}
    assert "consecutive_429s" in obs


def test_one_429_does_not_fire(isolated_state):
    """A single 429 is normal — we have retry logic. Don't fire below 3."""
    _write_snapshot(
        isolated_state,
        consecutive_429s=1,
        total_429s=1,
        last_429_at=time.time(),
    )
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert matches == []


def test_recent_fup_event_in_30min_fires_critical(isolated_state):
    """Per operator 2026-05-20: FUP = immediate response, sharp window.
    ANY event in the rolling 30 min window fires critical. Outside that
    window the situation is presumed cooled and we let the alert clear."""
    _write_snapshot(
        isolated_state,
        total_fup_events=1,
        last_fup_at=time.time() - 600,  # 10 min ago — well inside 30 min window
        last_fup_snippet="HTTP 403: fair use policy violation",
        last_fup_context="csi_brief",
    )
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert len(matches) == 1
    assert matches[0].severity == "critical"
    obs = matches[0].observed_value or {}
    assert obs["fup_active"] is True
    assert "fair use" in obs.get("last_fup_snippet", "").lower()


def test_fup_event_older_than_30min_does_not_fire(isolated_state):
    """FUP from over 30 min ago that hasn't recurred — situation cooled,
    let the alert clear. If it recurs, the new event will fire again."""
    _write_snapshot(
        isolated_state,
        total_fup_events=1,
        last_fup_at=time.time() - 45 * 60,  # 45 min ago — outside 30 min window
        last_fup_snippet="cooled fup",
    )
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert matches == []


def test_backoff_floor_at_max_fires_critical(isolated_state):
    """If the rate limiter's adaptive backoff has saturated, we're being
    sustained-throttled — also critical."""
    _write_snapshot(
        isolated_state,
        backoff_floor=8.0,  # the cap in record_429
        consecutive_429s=2,
        total_429s=10,
        last_429_at=time.time(),
    )
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert len(matches) == 1
    assert matches[0].severity == "critical"
    obs = matches[0].observed_value or {}
    assert obs.get("backoff_at_max") is True


def test_high_process_count_fires_warn(isolated_state):
    """Too many UA Python processes = correlated risk for the above. Warn."""
    _write_snapshot(isolated_state)  # healthy snapshot
    with _mock_process_count(50):  # well above the 30 default
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert len(matches) == 1
    assert matches[0].severity == "warn"


def test_multiple_conditions_in_one_finding(isolated_state):
    """FUP + high process count → ONE finding listing both, critical (FUP wins).
    Operator gets full picture from one alert."""
    _write_snapshot(
        isolated_state,
        consecutive_429s=4,
        total_429s=20,
        last_429_at=time.time(),
        total_fup_events=1,
        last_fup_at=time.time() - 60,  # 1 min ago — inside 30 min window
        last_fup_snippet="fup hit",
    )
    with _mock_process_count(50):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert len(matches) == 1
    assert matches[0].severity == "critical"
    obs = matches[0].observed_value or {}
    triggered = obs.get("triggered_conditions") or []
    assert "fup_active" in triggered
    assert "consecutive_429s" in triggered
    assert "high_process_count" in triggered


def test_malformed_snapshot_silent(isolated_state):
    isolated_state.write_text("not json")
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert matches == []


def test_runbook_command_mentions_zai_state(isolated_state):
    _write_snapshot(
        isolated_state,
        consecutive_429s=5,
        total_429s=20,
        last_429_at=time.time(),
    )
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert len(matches) == 1
    runbook = matches[0].runbook_command or ""
    assert "zai_inference_state.json" in runbook or "pgrep" in runbook

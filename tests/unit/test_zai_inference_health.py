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
from pathlib import Path
import time
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
    events_file = tmp_path / "zai_inference_events.jsonl"
    monkeypatch.setenv("UA_ZAI_INFERENCE_STATE_PATH", str(state_file))
    monkeypatch.setenv("UA_ZAI_EVENTS_PATH", str(events_file))
    # Tighten the events windows so tests don't need to fake huge timespans.
    monkeypatch.setenv("UA_ZAI_EVENTS_429_WINDOW_SECONDS", "600")
    monkeypatch.setenv("UA_ZAI_EVENTS_429_CRITICAL_COUNT", "3")
    monkeypatch.setenv("UA_ZAI_EVENTS_FUP_WINDOW_SECONDS", "1800")
    yield state_file


def _write_events(events_path: Path, events: list[dict]) -> None:
    """Write a list of event dicts to the JSONL events file (one per line)."""
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with open(events_path, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _event(
    category: str,
    ts_offset: float,
    caller: str = "universal_agent/foo.py",
    status: int = 429,
    body: str = "",
) -> dict:
    """Build a single event dict; ts_offset is seconds RELATIVE to now (negative = past)."""
    return {
        "ts": time.time() + ts_offset,
        "method": "POST",
        "url_path": "/api/anthropic/v1/messages",
        "host": "api.z.ai",
        "status": status,
        "response_time_ms": 200.0,
        "category": category,
        "caller": caller,
        "body_snippet": body,
    }


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


# --- JSONL events-file augmentation (P7 gap closer) ------------------------


def test_events_429_burst_fires_critical(isolated_state):
    """Direct-httpx caller bypasses with_rate_limit_retry → snapshot stays
    clean BUT the universal P7 hook captures the 429s in the JSONL events
    file. Invariant should fire critical from the events alone."""
    _write_snapshot(isolated_state)  # snapshot reports all-zero
    events_path = isolated_state.parent / "zai_inference_events.jsonl"
    _write_events(
        events_path,
        [
            _event(
                "rate_limited_429",
                -60,
                caller="universal_agent/services/session_dossier.py",
            ),
            _event(
                "rate_limited_429",
                -55,
                caller="universal_agent/services/session_dossier.py",
            ),
            _event(
                "rate_limited_429",
                -50,
                caller="universal_agent/services/session_dossier.py",
            ),
        ],
    )
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert len(matches) == 1
    assert matches[0].severity == "critical"
    obs = matches[0].observed_value or {}
    assert "events_429_burst" in (obs.get("triggered_conditions") or [])
    assert obs.get("events_429_count") == 3
    callers = obs.get("events_429_top_callers") or []
    assert callers and callers[0]["caller"].endswith("session_dossier.py")
    assert callers[0]["count"] == 3
    # Headline should name the caller so the operator gets attribution.
    assert "session_dossier" in (matches[0].recommendation or "")


def test_events_429_under_threshold_does_not_fire(isolated_state):
    """2 × 429s in window is below the 3-event critical threshold — quiet."""
    _write_snapshot(isolated_state)
    events_path = isolated_state.parent / "zai_inference_events.jsonl"
    _write_events(
        events_path,
        [
            _event("rate_limited_429", -60),
            _event("rate_limited_429", -55),
        ],
    )
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert matches == []


def test_events_429_outside_window_is_ignored(isolated_state):
    """429s from 30 min ago are outside the 10 min rolling window — quiet."""
    _write_snapshot(isolated_state)
    events_path = isolated_state.parent / "zai_inference_events.jsonl"
    _write_events(
        events_path,
        [
            _event("rate_limited_429", -1800),
            _event("rate_limited_429", -1700),
            _event("rate_limited_429", -1600),
        ],
    )
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert matches == []


def test_events_fup_signal_fires_critical(isolated_state):
    """A single fup_signal in the rolling window fires critical even when
    the snapshot has no FUP recorded (direct-httpx caller)."""
    _write_snapshot(isolated_state)
    events_path = isolated_state.parent / "zai_inference_events.jsonl"
    _write_events(
        events_path,
        [
            _event(
                "fup_signal",
                -300,
                caller="universal_agent/services/some_caller.py",
                status=403,
                body='{"error":"fair use policy violation, code 1313"}',
            ),
        ],
    )
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert len(matches) == 1
    assert matches[0].severity == "critical"
    obs = matches[0].observed_value or {}
    assert obs.get("fup_active") is True
    assert obs.get("events_fup_count") == 1
    assert "some_caller.py" in (obs.get("events_last_fup_caller") or "")
    # Headline should be the FUP one, not 429 burst.
    msg = matches[0].recommendation or ""
    assert "FUP signal active" in msg


def test_events_ok_only_stays_silent(isolated_state):
    """A healthy event stream with only ok events doesn't fire."""
    _write_snapshot(isolated_state)
    events_path = isolated_state.parent / "zai_inference_events.jsonl"
    _write_events(
        events_path,
        [
            _event("ok", -120, status=200),
            _event("ok", -60, status=200),
        ],
    )
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert matches == []


def test_events_missing_file_is_silent(isolated_state):
    """No events file at all → invariant treats events-side as zeros and
    falls through to snapshot/process-count logic."""
    _write_snapshot(isolated_state)  # healthy snapshot
    # Don't write events_path.
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert matches == []


def test_events_malformed_lines_are_skipped(isolated_state):
    """Garbage lines mixed with valid events: valid ones still counted."""
    _write_snapshot(isolated_state)
    events_path = isolated_state.parent / "zai_inference_events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with open(events_path, "w") as f:
        f.write("this is not json\n")
        f.write(json.dumps(_event("rate_limited_429", -60)) + "\n")
        f.write("{broken json\n")
        f.write(json.dumps(_event("rate_limited_429", -50)) + "\n")
        f.write(json.dumps(_event("rate_limited_429", -40)) + "\n")
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert len(matches) == 1
    assert matches[0].severity == "critical"
    obs = matches[0].observed_value or {}
    assert obs.get("events_429_count") == 3


def test_runbook_command_mentions_events_jsonl(isolated_state):
    """The runbook should point operators at the new events file too."""
    _write_snapshot(isolated_state)
    events_path = isolated_state.parent / "zai_inference_events.jsonl"
    _write_events(
        events_path,
        [
            _event("rate_limited_429", -60),
            _event("rate_limited_429", -55),
            _event("rate_limited_429", -50),
        ],
    )
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert len(matches) == 1
    runbook = matches[0].runbook_command or ""
    assert "zai_inference_events.jsonl" in runbook


# ── Limiter-managed throttle semantics (2026-06-11, AIMD seam routing) ──────
# These activate ONLY while UA_LLM_CLASSIFIER_LIMITER_ENABLED is on: with the
# hot `_call_llm` seam routed through the limiter, in-band 429 streaks and a
# saturated floor are NORMAL managed states (the limiter retries and wins);
# the alarm-worthy outcomes are retries_exhausted and the FUP acquire-pause.
# Flag off (all tests above) keeps the pre-AIMD alarm behavior byte-identical.


def test_flag_on_managed_throttle_demotes_to_warn(isolated_state, monkeypatch):
    monkeypatch.setenv("UA_LLM_CLASSIFIER_LIMITER_ENABLED", "1")
    _write_snapshot(
        isolated_state,
        consecutive_429s=5,
        backoff_floor=8.0,
        total_429s=40,
        last_429_at=time.time(),  # limiter actively recording
        total_429s_exhausted=0,
        total_succeeded_after_retry=12,
    )
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert len(matches) == 1
    assert matches[0].severity == "warn"
    obs = matches[0].observed_value or {}
    triggered = obs.get("triggered_conditions") or []
    assert "consecutive_429s_managed" in triggered
    assert "backoff_at_max_managed" in triggered
    assert "consecutive_429s" not in triggered
    assert obs.get("limiter_managing") is True
    assert "limiter-managed" in (matches[0].recommendation or "")


def test_flag_on_retries_exhausted_fires_critical(isolated_state, monkeypatch):
    monkeypatch.setenv("UA_LLM_CLASSIFIER_LIMITER_ENABLED", "1")
    _write_snapshot(
        isolated_state,
        consecutive_429s=5,
        last_429_at=time.time(),
        total_429s_exhausted=3,
        last_exhausted_at=time.time() - 120,  # 2 min ago — inside the window
    )
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert len(matches) == 1
    assert matches[0].severity == "critical"
    triggered = (matches[0].observed_value or {}).get("triggered_conditions") or []
    assert "retries_exhausted" in triggered
    # Exhaustion breaks the managed discriminator: full criticals return.
    assert "consecutive_429s" in triggered


def test_flag_on_fup_pause_active_fires_critical(isolated_state, monkeypatch):
    monkeypatch.setenv("UA_LLM_CLASSIFIER_LIMITER_ENABLED", "1")
    _write_snapshot(
        isolated_state,
        acquire_pause_until=time.time() + 120,  # pause in force
        last_429_at=time.time(),
    )
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert len(matches) == 1
    assert matches[0].severity == "critical"
    obs = matches[0].observed_value or {}
    assert "fup_pause_active" in (obs.get("triggered_conditions") or [])
    assert obs.get("fup_pause_active") is True
    assert "acquire-pause" in (matches[0].recommendation or "")


def test_cross_loop_conflicts_fires_warn(isolated_state):
    _write_snapshot(isolated_state, cross_loop_conflicts=2)
    with _mock_process_count(10):
        findings = run_invariants({})
    matches = [f for f in findings if f.metric_key == "zai_inference_health"]
    assert len(matches) == 1
    assert matches[0].severity == "warn"
    obs = matches[0].observed_value or {}
    assert "cross_loop_conflicts" in (obs.get("triggered_conditions") or [])
    assert obs.get("cross_loop_conflicts") == 2

from __future__ import annotations

from datetime import timedelta
import importlib.util
from pathlib import Path


def _load_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "csi_threads_rollout_verify.py"
    spec = importlib.util.spec_from_file_location("csi_threads_rollout_verify", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load csi_threads_rollout_verify module")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_seeded_probe_result_count_sums_term_results():
    mod = _load_module()
    seeded_probe = {
        "terms": [
            {"term": "ai", "results": 3},
            {"term": "robots", "results": 0},
            {"term": "markets", "results": 2},
        ]
    }
    assert mod._seeded_probe_result_count(seeded_probe) == 5


def test_seeded_no_event_signal_rate_limited_is_non_blocking_constraint():
    mod = _load_module()
    signal, is_failure = mod._seeded_no_event_signal(
        seeded_rows=0,
        seeded_probe_ok=1,
        seeded_probe_results=0,
        seeded_polled_recently=True,
        seeded_cycle_hits=0,
        seeded_cycle_new_hits=0,
        seeded_cycle_rate_limited=True,
        seeded_cycle_timeout_aborted=False,
        require_seeded_events=True,
    )
    assert signal == "seeded_poll_constrained_recently"
    assert is_failure is False


def test_seeded_no_event_signal_hits_but_no_new_events_warning():
    mod = _load_module()
    signal, is_failure = mod._seeded_no_event_signal(
        seeded_rows=0,
        seeded_probe_ok=1,
        seeded_probe_results=4,
        seeded_polled_recently=True,
        seeded_cycle_hits=4,
        seeded_cycle_new_hits=0,
        seeded_cycle_rate_limited=False,
        seeded_cycle_timeout_aborted=False,
        require_seeded_events=False,
    )
    assert signal == "seeded_live_but_no_new_events"
    assert is_failure is False


def test_seeded_no_event_signal_suppresses_noisy_warning_when_analysis_present():
    mod = _load_module()
    signal, is_failure = mod._seeded_no_event_signal(
        seeded_rows=12,
        seeded_probe_ok=1,
        seeded_probe_results=8,
        seeded_polled_recently=True,
        seeded_cycle_hits=8,
        seeded_cycle_new_hits=0,
        seeded_cycle_rate_limited=False,
        seeded_cycle_timeout_aborted=False,
        require_seeded_events=False,
    )
    assert signal == ""
    assert is_failure is False


def test_seeded_no_event_signal_can_fail_when_strict_required_and_idle():
    mod = _load_module()
    signal, is_failure = mod._seeded_no_event_signal(
        seeded_rows=0,
        seeded_probe_ok=0,
        seeded_probe_results=0,
        seeded_polled_recently=False,
        seeded_cycle_hits=0,
        seeded_cycle_new_hits=0,
        seeded_cycle_rate_limited=False,
        seeded_cycle_timeout_aborted=False,
        require_seeded_events=True,
    )
    assert signal == "no_seeded_events_in_lookback"
    assert is_failure is True


def test_webhook_activity_signal_ignored_when_disabled():
    mod = _load_module()
    signal, is_failure = mod._webhook_activity_signal(
        webhook_enabled=False,
        webhook_last_ingested=None,
        lookback_hours=24,
        require_webhook_activity=True,
    )
    assert signal == ""
    assert is_failure is False


def test_webhook_activity_signal_passes_when_recent():
    mod = _load_module()
    signal, is_failure = mod._webhook_activity_signal(
        webhook_enabled=True,
        webhook_last_ingested=mod._utc_now() - timedelta(hours=1),
        lookback_hours=24,
        require_webhook_activity=True,
    )
    assert signal == ""
    assert is_failure is False


def test_webhook_activity_signal_fails_when_required_and_stale():
    mod = _load_module()
    signal, is_failure = mod._webhook_activity_signal(
        webhook_enabled=True,
        webhook_last_ingested=mod._utc_now() - timedelta(hours=72),
        lookback_hours=24,
        require_webhook_activity=True,
    )
    assert signal == "webhook_enabled_but_no_ingest_in_lookback"
    assert is_failure is True

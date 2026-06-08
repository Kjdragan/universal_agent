"""Tests for the extracted systemd-migrated-jobs registry + its Mission Control
read-path consumer (the Chief-of-Staff migrated-cron-relic filter).

The registry was extracted from gateway_server.py (2026-06-06) so the readout can
detect stale ``cron:<job>`` Task Hub relics of timer-served jobs WITHOUT importing
the gateway app. The relic filter stops migrated-then-stale cron rows from driving
a false "N crons parked / <job> failed" stagnation narrative in the readout.
"""

from __future__ import annotations

from universal_agent import gateway_server
from universal_agent.services import mission_control_chief_of_staff as cos
from universal_agent.systemd_migrated_jobs import (
    SYSTEMD_MIGRATED_SYSTEM_JOBS,
    is_migrated_to_systemd,
)


def test_registry_has_all_migrated_jobs() -> None:
    # 20 Phase A jobs + cron_artifact_reminders_sweep (migrated 2026-06-08).
    assert len(SYSTEMD_MIGRATED_SYSTEM_JOBS) == 21
    for job in (
        "scratch_pruning",               # batch 1
        "codie_proactive_cleanup",       # batch 2 (bespoke gate)
        "hourly_intel_digest",           # batch 3
        "csi_convergence_sync",          # batch 3 (bespoke gate)
        "morning_briefing",              # batch A4
        "csi_demo_triage_rank",          # batch A4
        "cron_artifact_reminders_sweep", # post-Phase-A (2026-06-08)
    ):
        assert job in SYSTEMD_MIGRATED_SYSTEM_JOBS


def test_gateway_reexports_the_same_registry_and_gate() -> None:
    # gateway_server re-exports the leaf module's objects unchanged, so its 40+
    # internal call sites and the Phase-A batch-timer tests keep working.
    assert gateway_server._SYSTEMD_MIGRATED_SYSTEM_JOBS is SYSTEMD_MIGRATED_SYSTEM_JOBS
    assert gateway_server._is_migrated_to_systemd is is_migrated_to_systemd


def test_is_migrated_member_vs_nonmember() -> None:
    assert is_migrated_to_systemd("hourly_intel_digest") is True
    # still in-process (NOT migrated): the one remaining daily prompt job + minute loops
    assert is_migrated_to_systemd("paper_to_podcast_daily") is False
    assert is_migrated_to_systemd("simone_chat_auto_complete") is False
    # disabled by #734 but NOT migrated to a timer
    assert is_migrated_to_systemd("hackernews_snapshot") is False


def test_env_escape_hatch_restores_in_process(monkeypatch) -> None:
    monkeypatch.setenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", "1")
    assert is_migrated_to_systemd("hourly_intel_digest") is False
    monkeypatch.setenv("UA_SYSTEMD_TIMER_MIGRATION_DISABLED", "0")
    assert is_migrated_to_systemd("hourly_intel_digest") is True


def test_chief_of_staff_relic_filter_targets_only_migrated_crons() -> None:
    # migrated cron jobs → stale relic, filtered from the readout
    assert cos._is_migrated_cron_relic({"task_id": "cron:hourly_intel_digest"}) is True
    assert cos._is_migrated_cron_relic({"task_id": " cron:morning_briefing "}) is True
    # non-migrated, still-in-process cron → NOT a relic (paper_to_podcast is healthy)
    assert cos._is_migrated_cron_relic({"task_id": "cron:paper_to_podcast_daily"}) is False
    assert cos._is_migrated_cron_relic({"task_id": "cron:simone_chat_auto_complete"}) is False
    # non-cron tasks → never a relic
    assert cos._is_migrated_cron_relic({"task_id": "mission:abc"}) is False
    assert cos._is_migrated_cron_relic({"task_id": ""}) is False
    assert cos._is_migrated_cron_relic({}) is False

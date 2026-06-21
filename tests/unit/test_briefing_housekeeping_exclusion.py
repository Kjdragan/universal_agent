"""Tests for the autonomous-briefing telemetry fix (Issue 2).

ROOT A — housekeeping-cron inflation: the per-minute `simone_chat_auto_complete`
and quarter-hourly `vp_mission_pr_reconciler` crons get stamped
`metadata.autonomous=True` by `_register_system_cron_job`, so every successful
tick emits a `kind="autonomous_run_completed"` notification. Before the fix
`_collect_autonomous_activity_rows` tallied each one into "completed", inflating
the count by 1000s/24h. The fix excludes the `HOUSEKEEPING_SYSTEM_JOBS` set from
the tally.

ROOT B — cron_runs_in_window:0 under split runtime: when `_cron_service` is None
(briefing composes in a process that doesn't host the cron service), the in-memory
run buffer is empty so the count reported 0 despite thousands of durable runs.
`_count_cron_runs_in_window_from_jsonl` now reads the durable cron_runs.jsonl.
"""

from __future__ import annotations

import json
import time


def _make_completed_notification(*, system_job: str, created_at_ts: float) -> dict:
    return {
        "id": f"notif-{system_job}-{int(created_at_ts)}",
        "kind": "autonomous_run_completed",
        "title": "Autonomous Task Completed",
        "message": f"!script {system_job}",
        "created_at": time.strftime(
            "%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(created_at_ts)
        ),
        "metadata": {
            "job_id": f"job-{system_job}",
            "system_job": system_job,
            "autonomous": True,
        },
    }


def test_housekeeping_completion_excluded_from_tally() -> None:
    from universal_agent import gateway_server as gs

    now = time.time()
    # One housekeeping cron completion + one genuine proactive completion,
    # both inside the 24h window.
    housekeeping = _make_completed_notification(
        system_job="simone_chat_auto_complete", created_at_ts=now - 60
    )
    real = _make_completed_notification(
        system_job="morning_briefing", created_at_ts=now - 120
    )

    saved = list(gs._notifications)
    saved_cron_service = gs._cron_service
    try:
        gs._notifications.clear()
        gs._notifications.extend([housekeeping, real])
        # Force the split-aware / no-backfill path so this test only exercises
        # the in-memory notification filter (ROOT A).
        gs._cron_service = None
        result = gs._collect_autonomous_activity_rows(now_ts=now)
    finally:
        gs._notifications.clear()
        gs._notifications.extend(saved)
        gs._cron_service = saved_cron_service

    completed_jobs = {row["system_job"] for row in result["completed"]}
    assert "simone_chat_auto_complete" not in completed_jobs, (
        "housekeeping cron must be excluded from the completed tally"
    )
    assert "morning_briefing" in completed_jobs, (
        "genuine proactive completions must still be counted"
    )
    assert len(result["completed"]) == 1
    diag = result["source_diagnostics"]
    assert diag["housekeeping_completions_excluded"] == 1


def test_cron_runs_jsonl_window_count(tmp_path, monkeypatch) -> None:
    from universal_agent import gateway_server as gs

    now = time.time()
    window_start = now - 86400
    runs_path = tmp_path / "cron_runs.jsonl"

    records = [
        # in-window: finished 1h ago
        {"run_id": "r1", "job_id": "j1", "status": "success", "finished_at": now - 3600},
        # in-window: only started_at present, 2h ago
        {"run_id": "r2", "job_id": "j2", "status": "success", "started_at": now - 7200},
        # in-window: only scheduled_at present, 30m ago
        {"run_id": "r3", "job_id": "j3", "status": "failed", "scheduled_at": now - 1800},
        # OUT of window: finished 2 days ago
        {"run_id": "r4", "job_id": "j4", "status": "success", "finished_at": now - 172800},
        # OUT of window (future beyond +5s tolerance)
        {"run_id": "r5", "job_id": "j5", "status": "success", "finished_at": now + 600},
        # malformed line tolerance: no usable timestamp
        {"run_id": "r6", "job_id": "j6", "status": "success"},
    ]
    with runs_path.open("w", encoding="utf-8") as handle:
        for rec in records:
            handle.write(json.dumps(rec) + "\n")
        handle.write("not-json-line\n")  # parser must skip, not raise

    monkeypatch.setattr(gs, "WORKSPACES_DIR", tmp_path)
    count = gs._count_cron_runs_in_window_from_jsonl(
        window_start=window_start, window_end=now
    )
    assert count == 3, f"expected 3 in-window runs, got {count}"


def test_cron_runs_in_window_split_aware_when_service_none(tmp_path, monkeypatch) -> None:
    """When _cron_service is None, cron_runs_in_window must come from the jsonl."""
    from universal_agent import gateway_server as gs

    now = time.time()
    runs_path = tmp_path / "cron_runs.jsonl"
    with runs_path.open("w", encoding="utf-8") as handle:
        for i in range(4):
            handle.write(
                json.dumps(
                    {"run_id": f"r{i}", "job_id": "j", "status": "success", "finished_at": now - 100 * i}
                )
                + "\n"
            )

    monkeypatch.setattr(gs, "WORKSPACES_DIR", tmp_path)
    saved_cron_service = gs._cron_service
    try:
        gs._cron_service = None
        out = gs._collect_autonomous_runs_from_cron(
            window_start=now - 86400, window_end=now
        )
    finally:
        gs._cron_service = saved_cron_service

    assert out["diagnostics"]["cron_runs_in_window"] == 4
    assert out["diagnostics"].get("cron_runs_source") == "cron_runs_jsonl"
    # No job metadata available from jsonl → no backfill rows.
    assert out["completed"] == []
    assert out["failed"] == []

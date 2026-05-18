"""Unit tests for the proactive_health aggregator payload builder."""

from __future__ import annotations

import importlib
from pathlib import Path
import sqlite3

import pytest

from universal_agent.services import pipeline_invariants as pi
from universal_agent.services.pipeline_invariants import clear_registry_for_tests
from universal_agent.services.proactive_health import build_proactive_health_payload

TASK_HUB_SCHEMA = """
CREATE TABLE task_hub_items (
    task_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

CSI_SCHEMA = """
CREATE TABLE rss_event_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL DEFAULT 'youtube_channel_rss',
    transcript_status TEXT NOT NULL DEFAULT 'missing',
    analyzed_at TEXT DEFAULT (datetime('now'))
);
"""


@pytest.fixture(autouse=True)
def _clean_registry_with_youtube():
    """Each test starts with only the YouTube invariant registered."""
    clear_registry_for_tests()
    from universal_agent.services.invariants import youtube_invariants

    importlib.reload(youtube_invariants)
    yield
    clear_registry_for_tests()


@pytest.fixture
def activity_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(TASK_HUB_SCHEMA)
    conn.commit()
    return conn


def _insert_task(
    conn: sqlite3.Connection,
    task_id: str,
    status: str,
    age_minutes: int,
) -> None:
    conn.execute(
        "INSERT INTO task_hub_items (task_id, source_kind, title, status, created_at, updated_at) "
        "VALUES (?, 'test', ?, ?, datetime('now'), datetime('now', ?))",
        (task_id, f"task {task_id}", status, f"-{age_minutes} minutes"),
    )
    conn.commit()


class _FakeJob:
    def __init__(self, job_id: str, enabled: bool = True, last_outcome: str = "ok"):
        self._d = {
            "job_id": job_id,
            "enabled": enabled,
            "cron_expr": "0 * * * *",
            "last_run_at": "2026-05-18T00:00:00Z",
            "last_outcome": last_outcome,
            "next_run_at": "2026-05-18T01:00:00Z",
        }

    def to_dict(self):
        return dict(self._d)


def test_empty_state_is_ok(activity_conn: sqlite3.Connection) -> None:
    payload = build_proactive_health_payload(
        activity_conn=activity_conn,
        cron_jobs=[],
        csi_db_path=None,
    )
    assert payload["overall_status"] == "ok"
    assert payload["crons"] == []
    assert payload["stale_tasks"]["count"] == 0
    assert payload["parked_tasks"]["count"] == 0
    assert payload["invariants"] == []
    assert "generated_at_utc" in payload


def test_parked_task_yields_warn(activity_conn: sqlite3.Connection) -> None:
    _insert_task(activity_conn, "t1", "needs_review", age_minutes=5)
    payload = build_proactive_health_payload(
        activity_conn=activity_conn,
        cron_jobs=[],
        csi_db_path=None,
    )
    assert payload["overall_status"] == "warn"
    assert payload["parked_tasks"]["count"] == 1
    sample = payload["parked_tasks"]["samples"][0]
    assert sample["task_id"] == "t1"


def test_single_stale_task_yields_warn(activity_conn: sqlite3.Connection) -> None:
    _insert_task(activity_conn, "t1", "in_progress", age_minutes=300)  # past 180
    payload = build_proactive_health_payload(
        activity_conn=activity_conn,
        cron_jobs=[],
        csi_db_path=None,
    )
    assert payload["overall_status"] == "warn"
    assert payload["stale_tasks"]["count"] == 1


def test_three_stale_tasks_yields_critical(activity_conn: sqlite3.Connection) -> None:
    for i in range(3):
        _insert_task(activity_conn, f"t{i}", "in_progress", age_minutes=300)
    payload = build_proactive_health_payload(
        activity_conn=activity_conn,
        cron_jobs=[],
        csi_db_path=None,
    )
    assert payload["overall_status"] == "critical"
    assert payload["stale_tasks"]["count"] == 3


def test_fresh_in_progress_task_not_marked_stale(activity_conn: sqlite3.Connection) -> None:
    _insert_task(activity_conn, "fresh", "in_progress", age_minutes=10)
    payload = build_proactive_health_payload(
        activity_conn=activity_conn,
        cron_jobs=[],
        csi_db_path=None,
    )
    assert payload["overall_status"] == "ok"
    assert payload["stale_tasks"]["count"] == 0


def test_youtube_invariant_failure_drives_critical(
    activity_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    csi_db = tmp_path / "csi.db"
    conn = sqlite3.connect(str(csi_db))
    try:
        conn.executescript(CSI_SCHEMA)
        for i in range(10):
            conn.execute(
                "INSERT INTO rss_event_analysis (event_id, source, transcript_status) "
                "VALUES (?, 'youtube_channel_rss', 'missing')",
                (f"e{i}",),
            )
        conn.commit()
    finally:
        conn.close()

    payload = build_proactive_health_payload(
        activity_conn=activity_conn,
        cron_jobs=[],
        csi_db_path=csi_db,
    )
    assert payload["overall_status"] == "critical"
    invariants = payload["invariants"]
    assert len(invariants) == 1
    assert invariants[0]["metric_key"] == "youtube_transcript_coverage"
    assert invariants[0]["severity"] == "critical"


def test_cron_jobs_serialized(activity_conn: sqlite3.Connection) -> None:
    payload = build_proactive_health_payload(
        activity_conn=activity_conn,
        cron_jobs=[_FakeJob("job_a"), _FakeJob("job_b", enabled=False)],
        csi_db_path=None,
    )
    assert len(payload["crons"]) == 2
    ids = [c["job_id"] for c in payload["crons"]]
    assert "job_a" in ids and "job_b" in ids
    job_b = next(c for c in payload["crons"] if c["job_id"] == "job_b")
    assert job_b["enabled"] is False


def test_cron_job_serialization_errors_are_skipped(activity_conn: sqlite3.Connection) -> None:
    class _Bad:
        def to_dict(self):
            raise RuntimeError("nope")

    payload = build_proactive_health_payload(
        activity_conn=activity_conn,
        cron_jobs=[_FakeJob("job_a"), _Bad()],
        csi_db_path=None,
    )
    # Good job survived; bad job skipped silently.
    assert len(payload["crons"]) == 1
    assert payload["crons"][0]["job_id"] == "job_a"


def test_no_activity_conn_still_returns_payload(tmp_path: Path) -> None:
    payload = build_proactive_health_payload(
        activity_conn=None,
        cron_jobs=[],
        csi_db_path=None,
    )
    assert payload["overall_status"] == "ok"
    assert payload["stale_tasks"]["count"] == 0
    assert payload["parked_tasks"]["count"] == 0

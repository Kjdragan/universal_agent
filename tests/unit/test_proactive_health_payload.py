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
    source_kind: str = "test",
) -> None:
    conn.execute(
        "INSERT INTO task_hub_items (task_id, source_kind, title, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, datetime('now'), datetime('now', ?))",
        (task_id, source_kind, f"task {task_id}", status, f"-{age_minutes} minutes"),
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


def test_proactive_health_parked_row_excluded_from_count(
    activity_conn: sqlite3.Connection,
) -> None:
    """A `source_kind='proactive_health'` parked row must NOT count toward
    parked_tasks.count, and must not by itself force overall_status to 'warn'.

    Regression for the self-inflation loop: the watchdog used to write its own
    `needs_review` rows into Task Hub, and then `_query_parked_tasks` counted
    them, driving `_derive_overall_status` -> 'warn'. Those rows no longer get
    written, and the query now filters them out as a belt-and-suspenders guard.
    """
    _insert_task(
        activity_conn,
        "proactive_health:youtube_enrichment_coverage",
        "needs_review",
        age_minutes=5,
        source_kind="proactive_health",
    )
    payload = build_proactive_health_payload(
        activity_conn=activity_conn,
        cron_jobs=[],
        csi_db_path=None,
    )
    # The watchdog-authored row is invisible to the parked count.
    assert payload["parked_tasks"]["count"] == 0
    assert payload["parked_tasks"]["samples"] == []
    # No critical/warn invariants seeded → that lone row can't force 'warn'.
    assert payload["overall_status"] == "ok"


def test_proactive_health_parked_row_does_not_mask_real_parked_tasks(
    activity_conn: sqlite3.Connection,
) -> None:
    """A genuine parked task (non-proactive_health) is still counted even when
    a proactive_health row sits alongside it — the filter is surgical."""
    _insert_task(
        activity_conn,
        "proactive_health:some_finding",
        "needs_review",
        age_minutes=5,
        source_kind="proactive_health",
    )
    _insert_task(activity_conn, "real_parked", "needs_review", age_minutes=5)
    payload = build_proactive_health_payload(
        activity_conn=activity_conn,
        cron_jobs=[],
        csi_db_path=None,
    )
    assert payload["parked_tasks"]["count"] == 1
    assert payload["parked_tasks"]["samples"][0]["task_id"] == "real_parked"
    assert payload["overall_status"] == "warn"


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


# ─── Persistence-file fallback for cron_jobs ─────────────────────────────────
# Background: Simone heartbeat daemons run with a freshly imported
# `gateway_server` module whose `_cron_service` module-level var is None.
# That made `cron_jobs=[]` the only thing the aggregator ever saw from the
# heartbeat path — Layer-1 (cron last-run staleness) was invisible.
# Fix: aggregator accepts an optional `cron_persistence_path` and reads
# `cron_jobs.json` directly when no in-memory jobs are supplied.

def _write_cron_jobs_json(path: Path, jobs: list[dict]) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"jobs": jobs}, indent=2))


def test_payload_reads_crons_from_persistence_when_no_inmemory_jobs(
    activity_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    cron_path = tmp_path / "cron_jobs.json"
    _write_cron_jobs_json(
        cron_path,
        [
            {
                "job_id": "morning_briefing",
                "user_id": "system",
                "workspace_dir": None,
                "command": "echo hi",
                "description": "",
                "cron_expr": "30 6 * * *",
                "every_seconds": 0,
                "timezone": "UTC",
                "run_at": None,
                "enabled": True,
                "delete_after_run": False,
                "model": None,
                "timeout_seconds": None,
                "catch_up_on_restart": False,
                "metadata": {},
                "last_run_at": 1779278400.0,
                "last_outcome": "success",
                "next_run_at": None,
            },
            {
                "job_id": "atlas_direct_dispatch",
                "user_id": "system",
                "workspace_dir": None,
                "command": "echo hi",
                "description": "",
                "cron_expr": "*/1 * * * *",
                "every_seconds": 0,
                "timezone": "UTC",
                "run_at": None,
                "enabled": True,
                "delete_after_run": False,
                "model": None,
                "timeout_seconds": None,
                "catch_up_on_restart": False,
                "metadata": {},
                "last_run_at": 1779299100.0,
                "last_outcome": "success",
                "next_run_at": None,
            },
        ],
    )

    payload = build_proactive_health_payload(
        activity_conn=activity_conn,
        cron_jobs=None,
        csi_db_path=None,
        cron_persistence_path=cron_path,
    )
    job_ids = [c["job_id"] for c in payload["crons"]]
    assert "morning_briefing" in job_ids
    assert "atlas_direct_dispatch" in job_ids
    assert len(payload["crons"]) == 2


def test_in_memory_cron_jobs_take_precedence_over_persistence(
    activity_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    cron_path = tmp_path / "cron_jobs.json"
    _write_cron_jobs_json(
        cron_path,
        [
            {
                "job_id": "from_disk",
                "user_id": "system",
                "workspace_dir": None,
                "command": "echo hi",
                "description": "",
                "cron_expr": "0 * * * *",
                "every_seconds": 0,
                "timezone": "UTC",
                "run_at": None,
                "enabled": True,
                "delete_after_run": False,
                "model": None,
                "timeout_seconds": None,
                "catch_up_on_restart": False,
                "metadata": {},
                "last_run_at": None,
                "last_outcome": None,
                "next_run_at": None,
            }
        ],
    )

    payload = build_proactive_health_payload(
        activity_conn=activity_conn,
        cron_jobs=[_FakeJob("from_memory")],
        csi_db_path=None,
        cron_persistence_path=cron_path,
    )
    job_ids = [c["job_id"] for c in payload["crons"]]
    assert job_ids == ["from_memory"]
    assert "from_disk" not in job_ids


def test_missing_cron_persistence_path_is_silent(
    activity_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    payload = build_proactive_health_payload(
        activity_conn=activity_conn,
        cron_jobs=None,
        csi_db_path=None,
        cron_persistence_path=tmp_path / "does_not_exist.json",
    )
    assert payload["crons"] == []
    assert payload["overall_status"] == "ok"


# ─── P0b: invariants read proactive_* tables from activity_conn ──────────────
# Background: PR #392 wired the aggregator to open runtime_state.db and pass
# it as `runtime_conn` to invariants. But the WRITERS (`_activity_connect()`
# in gateway_server.py) write the proactive_artifacts / proactive_artifact_emails
# / proactive_intelligence_reports tables into activity_state.db. As a result
# 4 invariants (proactive_artifact_digest_delivery, proactive_reports_daily_trio,
# csi_demo_triage_rank_artifact, paper_to_podcast_email_delivery) were silently
# no-op'ing because they queried an empty DB. Fix: invariants use activity_conn
# (already in context for task_hub_items queries — same DB has the proactive
# tables too), aggregator stops opening the dead runtime_state.db connection.

def test_proactive_artifact_digest_invariant_reads_from_activity_conn(
    tmp_path: Path,
) -> None:
    """When the activity_conn DB has proactive_artifact_emails with a fresh
    delivery, the proactive_artifact_digest_delivery invariant should NOT fire
    (a healthy state). Before P0b, the invariant read from runtime_conn which
    was empty, so this test would have passed for the wrong reason — the
    invariant silently no-op'd whether the email was sent or not.

    The post-fix behavior: invariant queries activity_conn and finds the
    fresh row, so it stays quiet. A *missing* row (separate test below) is
    what should fire it. Either way, we prove it's reading the right DB."""
    from datetime import datetime, timedelta, timezone
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # task_hub_items so the activity_conn path doesn't blow up
    conn.executescript(TASK_HUB_SCHEMA)
    # proactive_artifact_emails — same DB, different table (real prod layout)
    conn.executescript(
        """
        CREATE TABLE proactive_artifact_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artifact_id TEXT NOT NULL,
            message_id TEXT NOT NULL DEFAULT '',
            thread_id TEXT NOT NULL DEFAULT '',
            subject TEXT NOT NULL DEFAULT '',
            recipient TEXT NOT NULL DEFAULT '',
            sent_at TEXT NOT NULL,
            delivery_state TEXT NOT NULL DEFAULT 'emailed',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    fresh = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn.execute(
        "INSERT INTO proactive_artifact_emails (artifact_id, subject, recipient, sent_at) "
        "VALUES ('a1', 'Proactive Digest 2026-05-20', 'kevinjdragan@gmail.com', ?)",
        (fresh,),
    )
    conn.commit()

    payload = build_proactive_health_payload(
        activity_conn=conn,
        cron_jobs=[],
        csi_db_path=None,
    )
    digest_findings = [
        f for f in payload["invariants"]
        if f.get("metric_key") == "proactive_artifact_digest_delivery"
    ]
    # Fresh row → invariant should not fire. The key proof is that the
    # aggregator + invariant path saw our seeded row.
    assert digest_findings == [], (
        f"Invariant fired despite fresh email row: {digest_findings}"
    )


def test_proactive_artifact_digest_invariant_fires_when_emails_stale(
    tmp_path: Path, monkeypatch
) -> None:
    """The bug-reproduction case: aggregator should DETECT that no digest
    email has been sent in 26h+. Before P0b, the invariant queried the empty
    runtime_state.db and silently passed — it could never fire on real data.

    P7 (2026-05-21): test was time-bombed. It (a) hard-coded fixed_now to
    2026-05-20 18:00 UTC which is now in the past, (b) reloaded the invariants
    module AFTER the monkeypatch, undoing it. CI runs after that date hit the
    invariant's `now.hour < 9` hour-gate and got [] back. Fix: reload FIRST,
    then patch, and use a clock-relative fixed_now so the test is durable."""
    from datetime import datetime, timedelta, timezone

    # Reload BEFORE the monkeypatch — reload would otherwise re-create
    # _now_houston and undo the patch.
    import importlib

    from universal_agent.services.invariants import proactive_pipeline_invariants as ppi
    importlib.reload(ppi)

    # Force _now_houston to a fixed hour past the invariant's 9 AM probe
    # gate. Use 18:00 UTC ("hour" alone is what the gate checks).
    real_now = datetime.now(timezone.utc)
    fixed_now = real_now.replace(hour=18, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(ppi, "_now_houston", lambda: fixed_now)
    # The invariant also computes age_hours via real `datetime.now(timezone.utc)`
    # internally — we can't patch that easily, so seed `stale` relative to
    # REAL now (35h ago) so age_hours always exceeds the 30h threshold.

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(TASK_HUB_SCHEMA)
    conn.executescript(
        """
        CREATE TABLE proactive_artifact_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artifact_id TEXT NOT NULL,
            message_id TEXT NOT NULL DEFAULT '',
            thread_id TEXT NOT NULL DEFAULT '',
            subject TEXT NOT NULL DEFAULT '',
            recipient TEXT NOT NULL DEFAULT '',
            sent_at TEXT NOT NULL,
            delivery_state TEXT NOT NULL DEFAULT 'emailed',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    # Seed a row 35h before REAL now so it exceeds the invariant's internal
    # 30h threshold (which uses datetime.now(timezone.utc), not _now_houston).
    stale = (real_now - timedelta(hours=35)).isoformat()
    conn.execute(
        "INSERT INTO proactive_artifact_emails (artifact_id, subject, recipient, sent_at) "
        "VALUES ('a1', 'Old Digest', 'kevinjdragan@gmail.com', ?)",
        (stale,),
    )
    conn.commit()

    payload = build_proactive_health_payload(
        activity_conn=conn,
        cron_jobs=[],
        csi_db_path=None,
    )
    digest_findings = [
        f for f in payload["invariants"]
        if f.get("metric_key") == "proactive_artifact_digest_delivery"
    ]
    # Proof of fix: the invariant FOUND the stale row and flagged it. Before
    # P0b, this list would be empty because the invariant read from an empty
    # runtime_state.db connection.
    assert len(digest_findings) >= 1, (
        f"Expected stale-digest finding but got: {payload['invariants']}"
    )

"""Hermetic tests for the proactive activity inventory.

No real systemctl, no real DBs — systemctl is monkeypatched to a canned
``list-timers`` table, the in-app cron registry is a tmp ``cron_jobs.json`` +
``cron_runs.jsonl``, and lanes run against an in-memory sqlite. This exercises
the status-classification logic that makes the report legible.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
import subprocess

import pytest

from universal_agent.services import proactive_activity_report as par


def _ts(dt: datetime) -> str:
    return dt.strftime("%a %Y-%m-%d %H:%M:%S UTC")


def _fake_list_timers(now: datetime) -> str:
    """Build a canned ``systemctl list-timers --all`` table.

    - proactive-health: ~5min cadence, last ran 2 min ago    → healthy
    - hourly-intel-digest: hourly cadence, last ran 6h ago    → stale (degraded)
    - nightly-wiki: daily cadence, last ran 30h ago           → healthy (≤2× day)
    """
    recent = now - timedelta(minutes=2)
    overdue = now - timedelta(hours=6)
    daily_ok = now - timedelta(hours=30)
    nxt = now + timedelta(minutes=3)
    return "\n".join(
        [
            "NEXT                        LEFT       LAST                        PASSED      UNIT                                          ACTIVATES",
            f"{_ts(nxt)}      3min     {_ts(recent)}      2min ago    universal-agent-proactive-health.timer        universal-agent-proactive-health.service",
            f"{_ts(now + timedelta(hours=1))}      54min    {_ts(overdue)}      6h ago      universal-agent-hourly-intel-digest.timer     universal-agent-hourly-intel-digest.service",
            f"{_ts(now + timedelta(hours=18))}     18h      {_ts(daily_ok)}      30h ago     universal-agent-nightly-wiki.timer            universal-agent-nightly-wiki.service",
        ]
    )


@pytest.fixture()
def patched_systemctl(monkeypatch):
    now = datetime.now(timezone.utc)
    canned = _fake_list_timers(now)

    def fake_run(cmd, *args, **kwargs):  # noqa: ANN001
        assert cmd[:2] == ["systemctl", "list-timers"]
        return subprocess.CompletedProcess(cmd, 0, stdout=canned, stderr="")

    monkeypatch.setattr(par.subprocess, "run", fake_run)
    return now


@pytest.fixture()
def workspaces(tmp_path, monkeypatch):
    """A tmp AGENT_RUN_WORKSPACES with cron_jobs.json + cron_runs.jsonl."""
    now = datetime.now(timezone.utc)
    jobs = {
        "jobs": [
            {
                "job_id": "abc123",
                "user_id": "system",
                "workspace_dir": "/tmp/x",
                "command": "noop",
                "enabled": True,
                "every_seconds": 60,
                "cron_expr": None,
                "last_run_at": now.timestamp() - 30,
                "metadata": {"system_job": "simone_chat_auto_complete"},
            },
            {
                "job_id": "ccintel",
                "user_id": "system",
                "workspace_dir": "/tmp/x",
                "command": "noop",
                "enabled": False,  # operator-paused
                "every_seconds": 0,
                "cron_expr": "0 8,16,22 * * *",
                "last_run_at": now.timestamp() - 9 * 3600,
                "metadata": {"system_job": "claude_code_intel_sync"},
            },
        ]
    }
    (tmp_path / "cron_jobs.json").write_text(json.dumps(jobs))
    runs = [
        {"run_id": "r1", "job_id": "abc123", "status": "success", "started_at": now.timestamp() - 30},
    ]
    (tmp_path / "cron_runs.jsonl").write_text("\n".join(json.dumps(r) for r in runs) + "\n")
    monkeypatch.setenv("AGENT_RUN_WORKSPACES_DIR", str(tmp_path))
    return tmp_path


def _make_conn(*, with_proactive_artifacts: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE convergence_candidates (created_at TEXT)")
    conn.execute(
        "INSERT INTO convergence_candidates (created_at) VALUES (?)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    if with_proactive_artifacts:
        # proactive_artifacts lives in the runtime activity_state.db — the report's
        # own conn — not csi.db. A fresh row must classify healthy.
        conn.execute("CREATE TABLE proactive_artifacts (created_at TEXT)")
        conn.execute(
            "INSERT INTO proactive_artifacts (created_at) VALUES (?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
    return conn


def test_systemd_status_classification(patched_systemctl):
    acts = par._collect_systemd_activities()
    by_name = {a["name"]: a for a in acts}

    assert by_name["proactive-health"]["status"] == par.STATUS_HEALTHY
    # 6h-old hourly job is well past 2× its hourly cadence → degraded/stale.
    assert by_name["hourly-intel-digest"]["status"] == par.STATUS_DEGRADED
    # 30h-old daily job is within 2× a day → still healthy.
    assert by_name["nightly-wiki"]["status"] == par.STATUS_HEALTHY


def test_inapp_paused_and_healthy(workspaces):
    acts = par._collect_inapp_cron_activities(str(workspaces))
    by_name = {a["name"]: a for a in acts}

    # Disabled-by-design claude_code_intel_sync → paused (NOT broken).
    cc = by_name["Claude Code intel sync"]
    assert cc["status"] == par.STATUS_PAUSED
    assert "operator-paused" in cc["detail"]

    # Recent */60s job → healthy.
    assert by_name["Simone chat auto-complete"]["status"] == par.STATUS_HEALTHY


def test_build_inventory_and_render(patched_systemctl, workspaces):
    conn = _make_conn()
    inv = par.build_activity_inventory(conn)

    assert inv["summary"]["total"] == len(inv["activities"]) > 0
    # Every activity carries the required shape.
    for a in inv["activities"]:
        assert set(a) >= {
            "name",
            "category",
            "scheduler",
            "cadence",
            "last_run_iso",
            "status",
            "detail",
        }

    section = par.render_activity_section(inv)
    # The paused activity is rendered with the ⏸️ icon.
    assert "⏸️" in section
    assert "Claude Code intel sync" in section
    # Summary header present.
    assert "healthy" in section and "paused/parked" in section


def test_render_includes_paused_icon_minimal():
    inv = {
        "generated_at": "2026-06-21T00:00:00+00:00",
        "activities": [
            {
                "name": "Claude Code intel sync",
                "category": "Intelligence",
                "scheduler": "in-app cron",
                "cadence": "0 8,16,22 * * *",
                "last_run_iso": None,
                "status": par.STATUS_PAUSED,
                "detail": "X API credits depleted (operator-paused)",
            }
        ],
        "summary": {"healthy": 0, "degraded": 0, "paused": 1, "dark": 0, "unknown": 0, "total": 1},
    }
    section = par.render_activity_section(inv)
    assert "⏸️ Claude Code intel sync" in section
    assert "operator-paused" in section


def test_build_inventory_never_raises_without_systemctl(monkeypatch, tmp_path):
    """On a dev box (no systemctl, no registry) the inventory degrades, not raises."""
    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise FileNotFoundError("systemctl")

    monkeypatch.setattr(par.subprocess, "run", boom)
    monkeypatch.setenv("AGENT_RUN_WORKSPACES_DIR", str(tmp_path))
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    inv = par.build_activity_inventory(conn)
    assert "activities" in inv and "summary" in inv
    # systemd source degrades to a single 'unknown' placeholder.
    assert any(a["status"] == par.STATUS_UNKNOWN for a in inv["activities"])


def test_proactive_artifacts_reads_activity_db_and_is_healthy():
    """The proactive_artifacts lane must read the report's own conn (activity DB),
    NOT csi.db. A fresh row classifies healthy."""
    conn = _make_conn(with_proactive_artifacts=True)
    acts = par._collect_lane_activities(conn)
    by_name = {a["name"]: a for a in acts}
    assert "Proactive artifacts" in by_name
    assert by_name["Proactive artifacts"]["status"] == par.STATUS_HEALTHY


def test_proactive_artifacts_missing_table_degrades_not_raises():
    """A conn without the table degrades to 'unknown', never raises."""
    conn = _make_conn(with_proactive_artifacts=False)
    acts = par._collect_lane_activities(conn)
    by_name = {a["name"]: a for a in acts}
    assert by_name["Proactive artifacts"]["status"] == par.STATUS_UNKNOWN


def _make_csi_db(tmp_path, rows):
    """Build a tmp csi.db `events` table with (source, occurred_at) rows."""
    db_path = tmp_path / "csi.db"
    cx = sqlite3.connect(str(db_path))
    cx.execute("CREATE TABLE events (source TEXT, occurred_at TEXT)")
    cx.executemany("INSERT INTO events (source, occurred_at) VALUES (?, ?)", rows)
    cx.commit()
    cx.close()
    return db_path


def test_retired_csi_source_is_omitted_not_dark(monkeypatch, tmp_path):
    """A decommissioned producer (csi_analytics) leaves stale csi.db rows; it must
    be OMITTED from the inventory (with a retired-count note), NOT reported dark."""
    now = datetime.now(timezone.utc)
    fresh = now.isoformat()
    stale = (now - timedelta(days=20)).isoformat()
    db_path = _make_csi_db(
        tmp_path,
        [
            ("youtube_channel_rss", fresh),
            ("csi_analytics", stale),  # retired producer, very stale
            ("threads_owned", stale),  # retired producer, very stale
        ],
    )
    import universal_agent.services.transcript_corpus as tc

    monkeypatch.setattr(tc, "resolve_csi_db_path", lambda: str(db_path))

    conn = _make_conn()
    acts = par._collect_lane_activities(conn)
    names = [a["name"] for a in acts]

    # The retired sources are NOT listed as their own CSI lane rows.
    assert "CSI · csi_analytics" not in names
    assert "CSI · threads_owned" not in names
    # The live source IS listed and healthy.
    assert "CSI · youtube_channel_rss" in names
    live = next(a for a in acts if a["name"] == "CSI · youtube_channel_rss")
    assert live["status"] == par.STATUS_HEALTHY
    # A retired-count note is emitted (parked, not dark) covering the 2 retired rows.
    retired_note = next((a for a in acts if a["name"] == "CSI · retired lanes"), None)
    assert retired_note is not None
    assert retired_note["status"] == par.STATUS_PARKED
    assert "2 retired" in retired_note["detail"]
    # No retired source appears as a dark entry anywhere.
    dark_names = [a["name"] for a in acts if a["status"] == par.STATUS_DARK]
    assert not any("csi_analytics" in n or "threads" in n for n in dark_names)

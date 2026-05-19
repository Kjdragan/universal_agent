"""Tests for the 5 proactive-pipeline invariants.

Each test sets a fixed Houston time via patching `_now_houston` and seeds
either a tmp_path artifacts tree or an in-memory sqlite activity DB. The
goal is to lock the "stay quiet when fresh" / "fire when stale" boundary
without depending on real production data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib
import os
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo

import pytest

from universal_agent.services import pipeline_invariants as pi
from universal_agent.services.invariants import proactive_pipeline_invariants as ppi
from universal_agent.services.pipeline_invariants import (
    clear_registry_for_tests,
    run_invariants,
)

HOUSTON = ZoneInfo("America/Chicago")
UTC = timezone.utc


@pytest.fixture(autouse=True)
def _fresh_registry():
    """Each test starts with ONLY the proactive_pipeline_invariants
    registered — no YouTube probes so tests don't cross-talk."""
    clear_registry_for_tests()
    importlib.reload(ppi)
    yield
    clear_registry_for_tests()


def _set_now(monkeypatch, dt_houston: datetime) -> None:
    """Force `_now_houston` to return a specific Houston-tz datetime."""
    monkeypatch.setattr(ppi, "_now_houston", lambda: dt_houston)
    today_str = dt_houston.strftime("%Y-%m-%d")
    monkeypatch.setattr(ppi, "_today_houston", lambda: today_str)


def _seeded_activity_conn() -> sqlite3.Connection:
    """In-memory activity DB with the two proactive tables this PR queries."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
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
        CREATE TABLE proactive_convergence_events (
            event_id TEXT PRIMARY KEY,
            primary_topic TEXT NOT NULL,
            video_ids_json TEXT NOT NULL DEFAULT '[]',
            channel_names_json TEXT NOT NULL DEFAULT '[]',
            brief_task_id TEXT NOT NULL DEFAULT '',
            artifact_id TEXT NOT NULL DEFAULT '',
            feedback_score INTEGER,
            detected_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    conn.commit()
    return conn


def _only(findings, metric_key):
    return [f for f in findings if f.metric_key == metric_key]


# === All 5 register on import ===


def test_all_five_invariants_register_on_import() -> None:
    ids = {inv.id for inv in pi.get_registered_invariants()}
    assert {
        "morning_briefing_freshness",
        "proactive_artifact_digest_delivery",
        "hackernews_snapshot_cadence",
        "csi_convergence_sync_freshness",
        "nightly_wiki_daily_output",
    }.issubset(ids)


# === 1. morning_briefing_freshness ===


def test_morning_briefing_fresh_emits_nothing(tmp_path: Path, monkeypatch) -> None:
    today_dt = datetime(2026, 5, 19, 9, 0, tzinfo=HOUSTON)
    _set_now(monkeypatch, today_dt)
    base = tmp_path / "artifacts"
    target = base / "autonomous-briefings" / today_dt.strftime("%Y-%m-%d") / "DAILY_BRIEFING.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# briefing")
    # Align file mtime to fake "now" so freshness math doesn't depend on
    # real-world clock when the test runs.
    fresh_mtime = today_dt.timestamp() - 60  # 1 min before fake now
    os.utime(target, (fresh_mtime, fresh_mtime))
    findings = run_invariants({"artifacts_dir": base})
    assert _only(findings, "morning_briefing_freshness") == []


def test_morning_briefing_missing_emits_warn(tmp_path: Path, monkeypatch) -> None:
    today_dt = datetime(2026, 5, 19, 9, 0, tzinfo=HOUSTON)
    _set_now(monkeypatch, today_dt)
    base = tmp_path / "artifacts"
    base.mkdir()
    (base / "autonomous-briefings").mkdir()
    findings = run_invariants({"artifacts_dir": base})
    matches = _only(findings, "morning_briefing_freshness")
    assert len(matches) == 1
    assert matches[0].severity == "warn"
    assert "No DAILY_BRIEFING.md" in (matches[0].recommendation or "")


def test_morning_briefing_stale_emits_warn(tmp_path: Path, monkeypatch) -> None:
    today_dt = datetime(2026, 5, 19, 11, 0, tzinfo=HOUSTON)
    _set_now(monkeypatch, today_dt)
    base = tmp_path / "artifacts"
    target = base / "autonomous-briefings" / today_dt.strftime("%Y-%m-%d") / "DAILY_BRIEFING.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# stale")
    six_hours_ago = time.time() - 6 * 3600
    os.utime(target, (six_hours_ago, six_hours_ago))
    findings = run_invariants({"artifacts_dir": base})
    matches = _only(findings, "morning_briefing_freshness")
    assert len(matches) == 1
    assert "old" in (matches[0].recommendation or "")


def test_morning_briefing_silent_before_630am(tmp_path: Path, monkeypatch) -> None:
    early_dt = datetime(2026, 5, 19, 5, 0, tzinfo=HOUSTON)
    _set_now(monkeypatch, early_dt)
    base = tmp_path / "artifacts"
    base.mkdir()
    findings = run_invariants({"artifacts_dir": base})
    assert _only(findings, "morning_briefing_freshness") == []


def test_morning_briefing_silent_without_artifacts_dir(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 9, 0, tzinfo=HOUSTON))
    findings = run_invariants({"artifacts_dir": None})
    assert _only(findings, "morning_briefing_freshness") == []


# === 2. proactive_artifact_digest_delivery ===


def test_digest_recent_emits_nothing(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 10, 0, tzinfo=HOUSTON))
    conn = _seeded_activity_conn()
    now_utc = datetime.now(UTC)
    conn.execute(
        "INSERT INTO proactive_artifact_emails (artifact_id, recipient, sent_at) "
        "VALUES ('a1', 'kevin@x', ?)",
        ((now_utc - timedelta(hours=1)).isoformat(),),
    )
    conn.commit()
    findings = run_invariants({"activity_conn": conn})
    assert _only(findings, "proactive_artifact_digest_delivery") == []


def test_digest_old_emits_warn(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 10, 0, tzinfo=HOUSTON))
    conn = _seeded_activity_conn()
    old = datetime.now(UTC) - timedelta(hours=40)
    conn.execute(
        "INSERT INTO proactive_artifact_emails (artifact_id, recipient, sent_at) "
        "VALUES ('a1', 'kevin@x', ?)",
        (old.isoformat(),),
    )
    conn.commit()
    findings = run_invariants({"activity_conn": conn})
    matches = _only(findings, "proactive_artifact_digest_delivery")
    assert len(matches) == 1
    assert matches[0].severity == "warn"


def test_digest_empty_table_stays_quiet(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 10, 0, tzinfo=HOUSTON))
    conn = _seeded_activity_conn()
    findings = run_invariants({"activity_conn": conn})
    assert _only(findings, "proactive_artifact_digest_delivery") == []


def test_digest_silent_before_9am(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 8, 0, tzinfo=HOUSTON))
    conn = _seeded_activity_conn()
    old = datetime.now(UTC) - timedelta(hours=40)
    conn.execute(
        "INSERT INTO proactive_artifact_emails (artifact_id, recipient, sent_at) "
        "VALUES ('a1', 'kevin@x', ?)",
        (old.isoformat(),),
    )
    conn.commit()
    findings = run_invariants({"activity_conn": conn})
    assert _only(findings, "proactive_artifact_digest_delivery") == []


# === 3. hackernews_snapshot_cadence ===


def test_hn_fresh_snapshot_emits_nothing(tmp_path: Path, monkeypatch) -> None:
    fake_now = datetime(2026, 5, 19, 14, 0, tzinfo=HOUSTON)
    _set_now(monkeypatch, fake_now)
    base = tmp_path / "artifacts"
    snaps = base / "hackernews" / "snapshots"
    snaps.mkdir(parents=True)
    f = snaps / "20260519140000.json"
    f.write_text("{}")
    # Align file mtime to fake "now" so age math doesn't depend on real clock.
    fresh_mtime = fake_now.timestamp() - 60
    os.utime(f, (fresh_mtime, fresh_mtime))
    findings = run_invariants({"artifacts_dir": base})
    assert _only(findings, "hackernews_snapshot_cadence") == []


def test_hn_old_snapshot_emits_warn(tmp_path: Path, monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 14, 0, tzinfo=HOUSTON))
    base = tmp_path / "artifacts"
    snaps = base / "hackernews" / "snapshots"
    snaps.mkdir(parents=True)
    f = snaps / "20260519100000.json"
    f.write_text("{}")
    ninety_min_ago = time.time() - 90 * 60
    os.utime(f, (ninety_min_ago, ninety_min_ago))
    findings = run_invariants({"artifacts_dir": base})
    matches = _only(findings, "hackernews_snapshot_cadence")
    assert len(matches) == 1
    assert matches[0].severity == "warn"


def test_hn_silent_during_dormancy(tmp_path: Path, monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 23, 30, tzinfo=HOUSTON))
    base = tmp_path / "artifacts"
    snaps = base / "hackernews" / "snapshots"
    snaps.mkdir(parents=True)
    findings = run_invariants({"artifacts_dir": base})
    assert _only(findings, "hackernews_snapshot_cadence") == []


def test_hn_silent_in_early_6am_window(tmp_path: Path, monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 6, 15, tzinfo=HOUSTON))
    base = tmp_path / "artifacts"
    snaps = base / "hackernews" / "snapshots"
    snaps.mkdir(parents=True)
    findings = run_invariants({"artifacts_dir": base})
    assert _only(findings, "hackernews_snapshot_cadence") == []


# === 4. csi_convergence_sync_freshness ===


def test_csi_convergence_fresh_emits_nothing(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 14, 0, tzinfo=HOUSTON))
    conn = _seeded_activity_conn()
    conn.execute(
        "INSERT INTO proactive_convergence_events (event_id, primary_topic, detected_at) "
        "VALUES ('e1', 'MCP', ?)",
        ((datetime.now(UTC) - timedelta(minutes=20)).isoformat(),),
    )
    conn.commit()
    findings = run_invariants({"activity_conn": conn})
    assert _only(findings, "csi_convergence_sync_freshness") == []


def test_csi_convergence_stale_emits_warn(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 14, 0, tzinfo=HOUSTON))
    conn = _seeded_activity_conn()
    conn.execute(
        "INSERT INTO proactive_convergence_events (event_id, primary_topic, detected_at) "
        "VALUES ('e1', 'MCP', ?)",
        ((datetime.now(UTC) - timedelta(minutes=120)).isoformat(),),
    )
    conn.commit()
    findings = run_invariants({"activity_conn": conn})
    matches = _only(findings, "csi_convergence_sync_freshness")
    assert len(matches) == 1
    assert matches[0].severity == "warn"


def test_csi_convergence_empty_table_stays_quiet(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 14, 0, tzinfo=HOUSTON))
    conn = _seeded_activity_conn()
    findings = run_invariants({"activity_conn": conn})
    assert _only(findings, "csi_convergence_sync_freshness") == []


# === 5. nightly_wiki_daily_output ===


def test_nightly_wiki_present_emits_nothing(tmp_path: Path, monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 8, 0, tzinfo=HOUSTON))
    base = tmp_path / "artifacts"
    wiki = base / "nightly_wikis"
    wiki.mkdir(parents=True)
    today_str = datetime(2026, 5, 19).strftime("%Y-%m-%d")
    (wiki / f"{today_str}_wiki_report_TOP.md").write_text("# wiki")
    findings = run_invariants({"artifacts_dir": base})
    assert _only(findings, "nightly_wiki_daily_output") == []


def test_nightly_wiki_missing_emits_warn(tmp_path: Path, monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 8, 0, tzinfo=HOUSTON))
    base = tmp_path / "artifacts"
    wiki = base / "nightly_wikis"
    wiki.mkdir(parents=True)
    (wiki / "2026-05-18_wiki_report_TOP.md").write_text("# old wiki")
    findings = run_invariants({"artifacts_dir": base})
    matches = _only(findings, "nightly_wiki_daily_output")
    assert len(matches) == 1
    assert matches[0].severity == "warn"


def test_nightly_wiki_silent_before_5am(tmp_path: Path, monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 4, 0, tzinfo=HOUSTON))
    base = tmp_path / "artifacts"
    wiki = base / "nightly_wikis"
    wiki.mkdir(parents=True)
    findings = run_invariants({"artifacts_dir": base})
    assert _only(findings, "nightly_wiki_daily_output") == []


def test_nightly_wiki_silent_without_artifacts_dir(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 8, 0, tzinfo=HOUSTON))
    findings = run_invariants({"artifacts_dir": None})
    assert _only(findings, "nightly_wiki_daily_output") == []

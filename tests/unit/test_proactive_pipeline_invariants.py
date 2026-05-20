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
        "nightly_wiki_persistent_silence",
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


def test_morning_briefing_existing_file_with_old_mtime_stays_quiet(
    tmp_path: Path, monkeypatch
) -> None:
    """The 6:30 AM cron fires once per day. By afternoon today's file is
    legitimately 6+ hours old. We should NOT fire — the today-dated parent
    directory IS the freshness gate. Probe corrected 2026-05-20 after a live
    false positive (13.5h-old file at 8 PM Houston)."""
    today_dt = datetime(2026, 5, 19, 18, 0, tzinfo=HOUSTON)  # 6 PM Houston
    _set_now(monkeypatch, today_dt)
    base = tmp_path / "artifacts"
    target = base / "autonomous-briefings" / today_dt.strftime("%Y-%m-%d") / "DAILY_BRIEFING.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# briefing produced at 6:30 AM")
    # mtime is 11h ago (6:30 AM Houston this morning) — legitimate.
    eleven_hours_ago = today_dt.timestamp() - 11 * 3600
    os.utime(target, (eleven_hours_ago, eleven_hours_ago))
    findings = run_invariants({"artifacts_dir": base})
    assert _only(findings, "morning_briefing_freshness") == []


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
    findings = run_invariants({"runtime_conn": conn})
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
    findings = run_invariants({"runtime_conn": conn})
    matches = _only(findings, "proactive_artifact_digest_delivery")
    assert len(matches) == 1
    assert matches[0].severity == "warn"


def test_digest_empty_table_stays_quiet(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 10, 0, tzinfo=HOUSTON))
    conn = _seeded_activity_conn()
    findings = run_invariants({"runtime_conn": conn})
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
    findings = run_invariants({"runtime_conn": conn})
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
    fake_now = datetime(2026, 5, 19, 14, 0, tzinfo=HOUSTON)
    _set_now(monkeypatch, fake_now)
    base = tmp_path / "artifacts"
    snaps = base / "hackernews" / "snapshots"
    snaps.mkdir(parents=True)
    f = snaps / "20260519100000.json"
    f.write_text("{}")
    # Anchor mtime to the mocked "now" so the age math doesn't depend on
    # the real wall clock at test-run time.
    ninety_min_ago = fake_now.timestamp() - 90 * 60
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


# === 5. nightly_wiki_persistent_silence ===
#
# Probe redesigned 2026-05-20 after WS2 live investigation revealed the
# nightly_wiki cron is "produce only when fresh CSI signals" — quiet days
# are legitimate. Old probe ("file for today exists") false-fired on every
# signal-light day. New probe fires only if NO wiki has been produced in
# the last 7 days.


def test_nightly_wiki_recent_file_keeps_quiet(tmp_path: Path, monkeypatch) -> None:
    """A wiki produced 2 days ago means the pipeline is healthy; today
    having no wiki is fine (cron's nothing-to-do case)."""
    today_dt = datetime(2026, 5, 19, 8, 0, tzinfo=HOUSTON)
    _set_now(monkeypatch, today_dt)
    base = tmp_path / "artifacts"
    wiki = base / "nightly_wikis"
    wiki.mkdir(parents=True)
    f = wiki / "2026-05-17_wiki_report_TOP.md"
    f.write_text("# wiki from 2 days ago")
    # Anchor mtime 2 days back from the mocked now.
    two_days_ago = today_dt.timestamp() - 2 * 86400
    os.utime(f, (two_days_ago, two_days_ago))
    findings = run_invariants({"artifacts_dir": base})
    assert _only(findings, "nightly_wiki_persistent_silence") == []


def test_nightly_wiki_no_files_at_all_stays_quiet(tmp_path: Path, monkeypatch) -> None:
    """Empty dir = fresh-box / never-deployed scenario, not a finding."""
    _set_now(monkeypatch, datetime(2026, 5, 19, 8, 0, tzinfo=HOUSTON))
    base = tmp_path / "artifacts"
    wiki = base / "nightly_wikis"
    wiki.mkdir(parents=True)
    findings = run_invariants({"artifacts_dir": base})
    assert _only(findings, "nightly_wiki_persistent_silence") == []


def test_nightly_wiki_persistent_silence_emits_warn(tmp_path: Path, monkeypatch) -> None:
    """Newest wiki >7 days old = stuck. THIS is what the probe should catch
    (e.g. the 2026-05-20 live state where newest wiki was 14 days old)."""
    today_dt = datetime(2026, 5, 19, 8, 0, tzinfo=HOUSTON)
    _set_now(monkeypatch, today_dt)
    base = tmp_path / "artifacts"
    wiki = base / "nightly_wikis"
    wiki.mkdir(parents=True)
    f = wiki / "2026-05-05_wiki_report_TOP.md"
    f.write_text("# very old wiki")
    fourteen_days_ago = today_dt.timestamp() - 14 * 86400
    os.utime(f, (fourteen_days_ago, fourteen_days_ago))
    findings = run_invariants({"artifacts_dir": base})
    matches = _only(findings, "nightly_wiki_persistent_silence")
    assert len(matches) == 1
    assert matches[0].severity == "warn"
    obs = matches[0].observed_value
    assert obs["quiet_days"] >= 14
    assert "very old wiki" not in matches[0].recommendation  # not the file contents
    assert "stuck" in (matches[0].recommendation or "").lower()


def test_nightly_wiki_silent_without_artifacts_dir(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 8, 0, tzinfo=HOUSTON))
    findings = run_invariants({"artifacts_dir": None})
    assert _only(findings, "nightly_wiki_persistent_silence") == []


# === WS3 invariants (2026-05-20) ===
#
# Five new invariants for the remaining proactive pipelines that have durable
# output traces. Each follows the same fail-open + time-gate patterns as the
# earlier 5.


def _seeded_runtime_conn() -> sqlite3.Connection:
    """In-memory runtime_state.db with the proactive tables this PR queries."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE proactive_intelligence_reports (
            report_id TEXT PRIMARY KEY,
            period TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            stats_json TEXT NOT NULL DEFAULT '{}',
            analysis TEXT NOT NULL DEFAULT '',
            email_message_id TEXT DEFAULT '',
            email_thread_id TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE proactive_artifacts (
            artifact_id TEXT PRIMARY KEY,
            artifact_type TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_ref TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'produced',
            delivery_state TEXT NOT NULL DEFAULT 'not_surfaced',
            priority INTEGER NOT NULL DEFAULT 2,
            artifact_uri TEXT NOT NULL DEFAULT '',
            artifact_path TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            topic_tags_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            feedback_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            surfaced_at TEXT NOT NULL DEFAULT '',
            accepted_at TEXT NOT NULL DEFAULT '',
            rejected_at TEXT NOT NULL DEFAULT '',
            archived_at TEXT NOT NULL DEFAULT ''
        );
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
    conn.commit()
    return conn


# --- 6. proactive_reports_daily_trio ---


def test_proactive_reports_daily_trio_all_present_quiet(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 18, 0, tzinfo=HOUSTON))
    conn = _seeded_runtime_conn()
    today = "2026-05-19"
    for period in ["morning", "midday", "afternoon"]:
        conn.execute(
            "INSERT INTO proactive_intelligence_reports "
            "(report_id, period, timestamp, created_at) VALUES (?, ?, ?, ?)",
            (f"r_{period}", period, f"{today}T12:00:00", f"{today}T12:00:00"),
        )
    conn.commit()
    findings = run_invariants({"runtime_conn": conn})
    assert _only(findings, "proactive_reports_daily_trio") == []


def test_proactive_reports_daily_trio_only_one_fires_warn(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 18, 0, tzinfo=HOUSTON))
    conn = _seeded_runtime_conn()
    today = "2026-05-19"
    conn.execute(
        "INSERT INTO proactive_intelligence_reports "
        "(report_id, period, timestamp, created_at) VALUES ('r1', 'morning', ?, ?)",
        (f"{today}T07:05:00", f"{today}T07:05:00"),
    )
    conn.commit()
    findings = run_invariants({"runtime_conn": conn})
    matches = _only(findings, "proactive_reports_daily_trio")
    assert len(matches) == 1
    obs = matches[0].observed_value
    assert obs["reports_today"] == 1
    assert "midday" in obs["periods_missing"]
    assert "afternoon" in obs["periods_missing"]


def test_proactive_reports_daily_trio_two_of_three_stays_quiet(monkeypatch) -> None:
    """Tolerate 1 missed slot as routine API blip."""
    _set_now(monkeypatch, datetime(2026, 5, 19, 18, 0, tzinfo=HOUSTON))
    conn = _seeded_runtime_conn()
    today = "2026-05-19"
    for period in ["morning", "midday"]:
        conn.execute(
            "INSERT INTO proactive_intelligence_reports "
            "(report_id, period, timestamp, created_at) VALUES (?, ?, ?, ?)",
            (f"r_{period}", period, f"{today}T12:00:00", f"{today}T12:00:00"),
        )
    conn.commit()
    findings = run_invariants({"runtime_conn": conn})
    assert _only(findings, "proactive_reports_daily_trio") == []


def test_proactive_reports_daily_trio_silent_before_5pm(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 14, 0, tzinfo=HOUSTON))
    conn = _seeded_runtime_conn()
    findings = run_invariants({"runtime_conn": conn})
    assert _only(findings, "proactive_reports_daily_trio") == []


# --- 7. claude_code_intel_packet_freshness ---


def test_claude_code_intel_recent_packet_quiet(tmp_path: Path, monkeypatch) -> None:
    fake_now = datetime(2026, 5, 19, 14, 0, tzinfo=HOUSTON)
    _set_now(monkeypatch, fake_now)
    base = tmp_path / "artifacts"
    pkts = base / "proactive" / "claude_code_intel" / "packets"
    pkts.mkdir(parents=True)
    f = pkts / "packet_001.json"
    f.write_text("{}")
    fresh_mtime = fake_now.timestamp() - 3600  # 1h ago
    os.utime(f, (fresh_mtime, fresh_mtime))
    findings = run_invariants({"artifacts_dir": base})
    assert _only(findings, "claude_code_intel_packet_freshness") == []


def test_claude_code_intel_stale_packet_fires(tmp_path: Path, monkeypatch) -> None:
    fake_now = datetime(2026, 5, 19, 14, 0, tzinfo=HOUSTON)
    _set_now(monkeypatch, fake_now)
    base = tmp_path / "artifacts"
    pkts = base / "proactive" / "claude_code_intel" / "packets"
    pkts.mkdir(parents=True)
    f = pkts / "packet_001.json"
    f.write_text("{}")
    twelve_hours_ago = fake_now.timestamp() - 12 * 3600
    os.utime(f, (twelve_hours_ago, twelve_hours_ago))
    findings = run_invariants({"artifacts_dir": base})
    matches = _only(findings, "claude_code_intel_packet_freshness")
    assert len(matches) == 1
    assert matches[0].severity == "warn"
    assert matches[0].observed_value["age_hours"] >= 9


def test_claude_code_intel_silent_during_dormancy(tmp_path: Path, monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 23, 0, tzinfo=HOUSTON))
    base = tmp_path / "artifacts"
    pkts = base / "proactive" / "claude_code_intel" / "packets"
    pkts.mkdir(parents=True)
    findings = run_invariants({"artifacts_dir": base})
    assert _only(findings, "claude_code_intel_packet_freshness") == []


# --- 8. csi_demo_triage_rank_artifact ---


def test_csi_demo_triage_rank_recent_quiet(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 16, 0, tzinfo=HOUSTON))
    conn = _seeded_runtime_conn()
    recent_iso = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    conn.execute(
        "INSERT INTO proactive_artifacts (artifact_id, artifact_type, source_kind, "
        "title, created_at, updated_at) VALUES "
        "('a1', 'csi_demo_triage_run', 'cron', 'rank', ?, ?)",
        (recent_iso, recent_iso),
    )
    conn.commit()
    findings = run_invariants({"runtime_conn": conn})
    assert _only(findings, "csi_demo_triage_rank_artifact") == []


def test_csi_demo_triage_rank_stale_fires_critical(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 16, 0, tzinfo=HOUSTON))
    conn = _seeded_runtime_conn()
    old_iso = (datetime.now(UTC) - timedelta(hours=12)).isoformat()
    conn.execute(
        "INSERT INTO proactive_artifacts (artifact_id, artifact_type, source_kind, "
        "title, created_at, updated_at) VALUES "
        "('a1', 'csi_demo_triage_run', 'cron', 'rank', ?, ?)",
        (old_iso, old_iso),
    )
    conn.commit()
    findings = run_invariants({"runtime_conn": conn})
    matches = _only(findings, "csi_demo_triage_rank_artifact")
    assert len(matches) == 1
    assert matches[0].severity == "critical"


def test_csi_demo_triage_rank_empty_table_stays_quiet(monkeypatch) -> None:
    """No rows of this artifact_type yet — feature not active, stay quiet."""
    _set_now(monkeypatch, datetime(2026, 5, 19, 16, 0, tzinfo=HOUSTON))
    conn = _seeded_runtime_conn()
    findings = run_invariants({"runtime_conn": conn})
    assert _only(findings, "csi_demo_triage_rank_artifact") == []


# --- 9. paper_to_podcast_email_delivery ---


def test_paper_to_podcast_recent_email_quiet(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 8, 0, tzinfo=HOUSTON))
    conn = _seeded_runtime_conn()
    recent_iso = (datetime.now(UTC) - timedelta(hours=10)).isoformat()
    conn.execute(
        "INSERT INTO proactive_artifact_emails (artifact_id, recipient, subject, sent_at) "
        "VALUES ('a1', 'kevinjdragan@gmail.com', 'Daily Papers Brief', ?)",
        (recent_iso,),
    )
    conn.commit()
    findings = run_invariants({"runtime_conn": conn})
    assert _only(findings, "paper_to_podcast_email_delivery") == []


def test_paper_to_podcast_old_email_fires_critical(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 8, 0, tzinfo=HOUSTON))
    conn = _seeded_runtime_conn()
    old_iso = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    conn.execute(
        "INSERT INTO proactive_artifact_emails (artifact_id, recipient, subject, sent_at) "
        "VALUES ('a1', 'kevinjdragan@gmail.com', 'Daily Papers Brief', ?)",
        (old_iso,),
    )
    conn.commit()
    findings = run_invariants({"runtime_conn": conn})
    matches = _only(findings, "paper_to_podcast_email_delivery")
    assert len(matches) == 1
    assert matches[0].severity == "critical"


def test_paper_to_podcast_empty_table_stays_quiet(monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 8, 0, tzinfo=HOUSTON))
    conn = _seeded_runtime_conn()
    findings = run_invariants({"runtime_conn": conn})
    assert _only(findings, "paper_to_podcast_email_delivery") == []


# --- 10. vault_lint_contradictions_monthly ---


def test_vault_lint_current_month_present_quiet(tmp_path: Path, monkeypatch) -> None:
    today_dt = datetime(2026, 5, 19, 8, 0, tzinfo=HOUSTON)
    _set_now(monkeypatch, today_dt)
    base = tmp_path / "artifacts"
    vault = base / "knowledge-vaults" / "claude-code-intelligence"
    vault.mkdir(parents=True)
    f = vault / "contradiction-report-2026-05-01.md"
    f.write_text("# report")
    # Set mtime to within current month (May 2026).
    may_5 = datetime(2026, 5, 5, 12, 0, tzinfo=HOUSTON).timestamp()
    os.utime(f, (may_5, may_5))
    findings = run_invariants({"artifacts_dir": base})
    assert _only(findings, "vault_lint_contradictions_monthly") == []


def test_vault_lint_no_current_month_report_fires(tmp_path: Path, monkeypatch) -> None:
    today_dt = datetime(2026, 5, 19, 8, 0, tzinfo=HOUSTON)
    _set_now(monkeypatch, today_dt)
    base = tmp_path / "artifacts"
    vault = base / "knowledge-vaults" / "claude-code-intelligence"
    vault.mkdir(parents=True)
    f = vault / "contradiction-report-2026-04-01.md"
    f.write_text("# april report")
    april_1 = datetime(2026, 4, 1, 12, 0, tzinfo=HOUSTON).timestamp()
    os.utime(f, (april_1, april_1))
    findings = run_invariants({"artifacts_dir": base})
    matches = _only(findings, "vault_lint_contradictions_monthly")
    assert len(matches) == 1
    assert matches[0].observed_value["current_month"] == "2026-05"


def test_vault_lint_silent_on_first_of_month(tmp_path: Path, monkeypatch) -> None:
    """Probe stays quiet on the 1st to give the day's cron time to run."""
    _set_now(monkeypatch, datetime(2026, 5, 1, 8, 0, tzinfo=HOUSTON))
    base = tmp_path / "artifacts"
    (base / "knowledge-vaults" / "anything").mkdir(parents=True)
    findings = run_invariants({"artifacts_dir": base})
    assert _only(findings, "vault_lint_contradictions_monthly") == []


def test_vault_lint_empty_dir_stays_quiet(tmp_path: Path, monkeypatch) -> None:
    _set_now(monkeypatch, datetime(2026, 5, 19, 8, 0, tzinfo=HOUSTON))
    base = tmp_path / "artifacts"
    (base / "knowledge-vaults" / "anything").mkdir(parents=True)
    findings = run_invariants({"artifacts_dir": base})
    assert _only(findings, "vault_lint_contradictions_monthly") == []


# === Registration assertion includes all 10 invariants ===


def test_all_ten_invariants_register_on_import() -> None:
    ids = {inv.id for inv in pi.get_registered_invariants()}
    expected = {
        "morning_briefing_freshness",
        "proactive_artifact_digest_delivery",
        "hackernews_snapshot_cadence",
        "csi_convergence_sync_freshness",
        "nightly_wiki_persistent_silence",
        "proactive_reports_daily_trio",
        "claude_code_intel_packet_freshness",
        "csi_demo_triage_rank_artifact",
        "paper_to_podcast_email_delivery",
        "vault_lint_contradictions_monthly",
    }
    assert expected.issubset(ids)

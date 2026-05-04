"""Pin the contract of the intelligence-event emitter.

Tier-1 card discovery only surfaces what the activity_events table
actually contains. The emitter has to be:
  - reliable (writes the row, schema is created if missing)
  - silent on failure (never raises into the caller)
  - schema-compatible with the gateway's reader so the dashboard
    surfaces these events without a separate migration
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from universal_agent.services.intelligence_emitter import (
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_SUCCESS,
    emit_intelligence_event,
)


def _read_rows(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM activity_events ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def test_emit_creates_schema_on_first_call(tmp_path: Path):
    db = tmp_path / "act.db"
    event_id = emit_intelligence_event(
        source_domain="proactive_report",
        kind="intelligence_report_generated",
        title="Morning intelligence report ready",
        summary="3 recommendations, 12 signals analyzed.",
        severity=SEVERITY_INFO,
        metadata={"report_id": "rpt-x", "period": "morning"},
        db_path=str(db),
    )
    assert event_id and event_id.startswith("proactive_report_intelligence_report_generated_")
    rows = _read_rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row["source_domain"] == "proactive_report"
    assert row["kind"] == "intelligence_report_generated"
    assert row["severity"] == "info"
    assert row["title"] == "Morning intelligence report ready"
    assert "rpt-x" in row["metadata_json"]


def test_emit_with_success_severity_round_trips(tmp_path: Path):
    """The `success` severity is the band the LLM uses for celebratory
    intelligence cards. The emitter must accept and persist it."""
    db = tmp_path / "act.db"
    emit_intelligence_event(
        source_domain="cron",
        kind="cron_job_success",
        title="Nightly reconciliation completed",
        summary="Reconciled 47 stale missions.",
        severity=SEVERITY_SUCCESS,
        metadata={"job_id": "reconcile_nightly", "duration_s": 12.3},
        db_path=str(db),
    )
    rows = _read_rows(db)
    assert rows[0]["severity"] == "success"


def test_emit_unknown_severity_coerces_to_info(tmp_path: Path):
    db = tmp_path / "act.db"
    emit_intelligence_event(
        source_domain="x",
        kind="y",
        title="t",
        summary="s",
        severity="not-a-real-band",  # type: ignore[arg-type]
        db_path=str(db),
    )
    rows = _read_rows(db)
    assert rows[0]["severity"] == "info"


def test_emit_never_raises_on_db_error(monkeypatch, tmp_path: Path):
    """Caller workflow must never break because instrumentation failed.
    Simulate a totally unwritable path; the function must return None
    silently rather than raise."""
    bad_path = tmp_path / "definitely" / "not" / "writeable.db"
    # No mkdir — sqlite will fail.
    out = emit_intelligence_event(
        source_domain="x", kind="y", title="t", summary="s",
        db_path=str(bad_path),
    )
    assert out is None


def test_emit_is_picked_up_by_tier1_evidence_filter(tmp_path: Path):
    """The whole point: an event written by emit_intelligence_event
    must pass the tier-1 evidence filter so the LLM card discovery
    sees it.

    Tier-1 filter (mission_control_tier1.py:145-149):
        severity IN ('warning','error','critical')
        OR requires_action = 1
        OR LOWER(source_domain) NOT IN ('heartbeat')

    A success-severity proactive_report event has source_domain ≠
    heartbeat, so it should pass even though severity is benign.
    """
    db = tmp_path / "act.db"
    emit_intelligence_event(
        source_domain="proactive_report",
        kind="intelligence_report_generated",
        title="t",
        summary="s",
        severity=SEVERITY_SUCCESS,
        db_path=str(db),
    )
    conn = sqlite3.connect(str(db))
    try:
        # Mirror the tier-1 SQL filter exactly.
        rows = conn.execute(
            """
            SELECT id FROM activity_events
            WHERE (
                LOWER(COALESCE(severity, '')) IN ('warning','error','critical')
                OR COALESCE(requires_action, 0) = 1
                OR LOWER(COALESCE(source_domain, '')) NOT IN ('heartbeat')
            )
            """
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1


def test_emit_with_critical_severity_and_requires_action(tmp_path: Path):
    """Critical-severity events should land with requires_action=1 so
    the dashboard's notifications widget surfaces them."""
    db = tmp_path / "act.db"
    emit_intelligence_event(
        source_domain="vp_mission",
        kind="vp_mission_failed",
        title="VP mission failed",
        summary="3 retries exhausted.",
        severity=SEVERITY_CRITICAL,
        requires_action=True,
        db_path=str(db),
    )
    rows = _read_rows(db)
    assert rows[0]["severity"] == "critical"
    assert rows[0]["requires_action"] == 1

"""Schema + filter behaviour for the activity_events.expires_at column.

The dashboard delivery-reminder path stores short-lived notifications
with expires_at = now + 90 min.  These rows must:

  1. Be returned by the dashboard query while still in their TTL window.
  2. Disappear from the dashboard query once expires_at has elapsed
     (whether or not the purge sweep has run yet).
  3. Be physically deleted by the purge sweep on its next tick.

This module exercises (1)–(3) directly against the schema helpers and
the public purge function without booting the whole gateway.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import sqlite3
import uuid

import pytest


@pytest.fixture
def activity_db(tmp_path, monkeypatch):
    db_path = tmp_path / "activity.db"
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(db_path))
    # Make sure the gateway module reads our tmp path on import.
    return str(db_path)


def _insert_row(
    db_path: str,
    *,
    expires_at: str | None,
    kind: str = "daily_digest_delivered",
    severity: str = "success",
    created_at: str | None = None,
) -> str:
    """Bypass the emitter and write directly so we can test pure schema behaviour."""
    from universal_agent.services.intelligence_emitter import _ensure_schema

    event_id = f"evt-{uuid.uuid4().hex[:12]}"
    ts = created_at or datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO activity_events (
                id, event_class, source_domain, kind, title, summary,
                full_message, severity, status, requires_action,
                session_id, created_at, updated_at,
                entity_ref_json, actions_json, metadata_json,
                channels_json, email_targets_json, expires_at
            ) VALUES (?, 'notification', 'youtube_daily_digest', ?, 'T', 'S',
                      'FM', ?, 'new', 0, NULL, ?, ?, '{}', '[]', '{}',
                      '[]', '[]', ?)
            """,
            (event_id, kind, severity, ts, ts, expires_at),
        )
        conn.commit()
    finally:
        conn.close()
    return event_id


def test_expires_at_column_present(activity_db):
    """The schema bootstrap MUST yield a table with an expires_at column.

    We allow either the CREATE TABLE path (new DB) or the ALTER TABLE
    migration path (pre-existing DB) — exercising both is left to the
    integration suite.
    """
    from universal_agent.services.intelligence_emitter import _ensure_schema

    conn = sqlite3.connect(activity_db)
    try:
        _ensure_schema(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(activity_events)")}
    finally:
        conn.close()
    assert "expires_at" in cols


def test_alter_migration_path_adds_column_to_legacy_db(activity_db):
    """Simulate a DB created before 2026-05-19 (no expires_at column),
    then assert the migration adds it without dropping existing rows."""
    # Step 1: create the legacy schema explicitly (NO expires_at).
    conn = sqlite3.connect(activity_db)
    try:
        conn.executescript(
            """
            CREATE TABLE activity_events (
                id TEXT PRIMARY KEY,
                event_class TEXT NOT NULL DEFAULT 'notification',
                source_domain TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                full_message TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                status TEXT NOT NULL DEFAULT 'new',
                requires_action INTEGER NOT NULL DEFAULT 0,
                session_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                entity_ref_json TEXT NOT NULL DEFAULT '{}',
                actions_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                channels_json TEXT NOT NULL DEFAULT '[]',
                email_targets_json TEXT NOT NULL DEFAULT '[]'
            );
            """
        )
        conn.execute(
            "INSERT INTO activity_events (id, source_domain, kind, title, summary, "
            "full_message, created_at, updated_at) "
            "VALUES ('legacy-1', 'system', 'k', 'T', 'S', 'F', '2026-05-01T00:00:00+00:00', "
            "'2026-05-01T00:00:00+00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    # Step 2: run the schema ensurer — this should ALTER the table in place.
    from universal_agent.services.intelligence_emitter import _ensure_schema

    conn = sqlite3.connect(activity_db)
    try:
        _ensure_schema(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(activity_events)")}
        # Pre-existing row preserved
        row = conn.execute("SELECT id FROM activity_events WHERE id='legacy-1'").fetchone()
    finally:
        conn.close()
    assert "expires_at" in cols
    assert row is not None


def test_expired_row_present_in_db_but_filtered_by_query(activity_db):
    """A row whose expires_at is in the past must still physically exist
    after insertion (until the purge sweep runs) but must NOT be returned
    by the gateway dashboard query helper.

    We invoke ``_purge_expired_activity_events_once`` directly to exercise
    the sweep; the dashboard-query filter is exercised separately below.
    """
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(minutes=60)).isoformat()
    expired_id = _insert_row(activity_db, expires_at=past)
    live_id = _insert_row(activity_db, expires_at=future)

    # Both rows physically present before purge
    conn = sqlite3.connect(activity_db)
    try:
        ids = {r[0] for r in conn.execute("SELECT id FROM activity_events")}
    finally:
        conn.close()
    assert expired_id in ids and live_id in ids

    # Purge sweep deletes the expired one, keeps the live one
    from universal_agent.gateway_server import _purge_expired_activity_events_once
    removed = _purge_expired_activity_events_once()
    assert removed >= 1

    conn = sqlite3.connect(activity_db)
    try:
        ids = {r[0] for r in conn.execute("SELECT id FROM activity_events")}
    finally:
        conn.close()
    assert expired_id not in ids
    assert live_id in ids


def test_query_filters_expired_rows_even_before_purge(activity_db):
    """The dashboard query helper hides rows past expires_at, regardless
    of whether the purge sweep has yet deleted them."""
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(minutes=60)).isoformat()
    expired_id = _insert_row(activity_db, expires_at=past)
    live_id = _insert_row(activity_db, expires_at=future)
    never_expires_id = _insert_row(activity_db, expires_at=None)

    from universal_agent.gateway_server import _query_activity_events
    items = _query_activity_events(limit=100, apply_default_window=False)
    returned_ids = {item["id"] for item in items}
    assert live_id in returned_ids
    assert never_expires_id in returned_ids
    assert expired_id not in returned_ids


def test_telegram_reminder_schedule_round_trip(activity_db):
    """The digest_telegram_reminders table is created by the schema
    bootstrap and round-trips a scheduled dismissal correctly."""
    from universal_agent.services.digest_delivery_reminder import _schedule_dismissal

    now = datetime.now(timezone.utc)
    dismiss_at = (now + timedelta(minutes=90)).isoformat()
    ok = _schedule_dismissal(
        chat_id="999",
        message_id=12345,
        dismiss_at_iso=dismiss_at,
        db_path=activity_db,
    )
    assert ok is True

    conn = sqlite3.connect(activity_db)
    try:
        rows = conn.execute(
            "SELECT chat_id, message_id, dismiss_at, dismissed_at FROM digest_telegram_reminders"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "999"
    assert rows[0][1] == 12345
    assert rows[0][2] == dismiss_at
    assert rows[0][3] is None  # not dismissed yet

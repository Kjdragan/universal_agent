"""Regression tests for the dict(row) hydration bug in proactive_artifacts.py.

Bug: Without row_factory = sqlite3.Row, sqlite3 returns raw tuples.
Calling dict(row) on a tuple crashes; accessing row["col"] also crashes.

Fix: Set conn.row_factory = sqlite3.Row in ensure_schema(), matching
the task_hub.py fix from PR #114.
"""

from __future__ import annotations

import sqlite3

import pytest

from universal_agent.services.proactive_artifacts import (
    ensure_schema,
    get_artifact,
    list_artifacts,
    record_email_delivery,
    find_artifact_for_reply,
    upsert_artifact,
    update_artifact_state,
)


def _make_conn() -> sqlite3.Connection:
    """Create an in-memory connection WITHOUT manually setting row_factory.

    The module's ensure_schema() must set it — this is the regression guard.
    """
    conn = sqlite3.connect(":memory:")
    return conn


class TestDictRowHydration:
    """Verify that dict(row) and row["col"] work after ensure_schema."""

    def test_get_artifact_returns_dict(self):
        """get_artifact uses dict(row) — must not crash on raw tuple."""
        conn = _make_conn()
        artifact = upsert_artifact(
            conn,
            artifact_type="report",
            source_kind="test",
            title="Dict row regression test",
        )
        assert isinstance(artifact, dict)
        assert artifact["title"] == "Dict row regression test"
        assert artifact["artifact_type"] == "report"

    def test_get_artifact_by_id(self):
        """Fetch an existing artifact — dict(row) hydration path."""
        conn = _make_conn()
        created = upsert_artifact(
            conn,
            artifact_type="report",
            source_kind="test",
            title="Fetch test",
        )
        aid = created["artifact_id"]
        fetched = get_artifact(conn, aid)
        assert fetched is not None
        assert fetched["artifact_id"] == aid
        assert fetched["title"] == "Fetch test"

    def test_get_artifact_nonexistent(self):
        """Returns None for missing artifact — no crash."""
        conn = _make_conn()
        result = get_artifact(conn, "pa_nonexistent0000")
        assert result is None

    def test_list_artifacts_returns_dicts(self):
        """list_artifacts uses [dict(row) for row in rows] — must not crash."""
        conn = _make_conn()
        upsert_artifact(conn, artifact_type="report", source_kind="test", title="A")
        upsert_artifact(conn, artifact_type="report", source_kind="test", title="B")
        upsert_artifact(conn, artifact_type="report", source_kind="test", title="C")

        results = list_artifacts(conn)
        assert len(results) == 3
        for r in results:
            assert isinstance(r, dict)
            assert "title" in r
            assert "topic_tags" in r
            assert "metadata" in r
            assert "feedback" in r

    def test_list_artifacts_filter_by_status(self):
        """Filtering still works after row_factory fix."""
        conn = _make_conn()
        upsert_artifact(conn, artifact_type="report", source_kind="test", title="X")
        results = list_artifacts(conn, status="produced")
        assert len(results) == 1
        results_empty = list_artifacts(conn, status="archived")
        assert len(results_empty) == 0

    def test_find_artifact_for_reply_uses_row_col_access(self):
        """find_artifact_for_reply accesses row["artifact_id"] — must not crash."""
        conn = _make_conn()
        created = upsert_artifact(
            conn,
            artifact_type="report",
            source_kind="test",
            title="Reply lookup test",
        )
        aid = created["artifact_id"]
        record_email_delivery(
            conn,
            artifact_id=aid,
            thread_id="thread_123",
            message_id="msg_456",
            subject="Test subject",
            recipient="test@example.com",
        )
        # Lookup by thread_id — hits row["artifact_id"] path
        found = find_artifact_for_reply(conn, thread_id="thread_123")
        assert found is not None
        assert found["artifact_id"] == aid

        # Lookup by message_id — hits the other row["artifact_id"] path
        found2 = find_artifact_for_reply(conn, message_id="msg_456")
        assert found2 is not None
        assert found2["artifact_id"] == aid

    def test_hydrate_artifact_removes_json_columns(self):
        """_hydrate_artifact should replace _json cols with parsed objects."""
        conn = _make_conn()
        created = upsert_artifact(
            conn,
            artifact_type="report",
            source_kind="test",
            title="Hydration test",
            topic_tags=["ai", "ml"],
            metadata={"key": "value"},
        )
        # topic_tags_json should be gone; topic_tags should be a list
        assert isinstance(created.get("topic_tags"), list)
        assert "ai" in created["topic_tags"]
        # metadata_json should be gone; metadata should be a dict
        assert isinstance(created.get("metadata"), dict)
        assert created["metadata"]["key"] == "value"

    def test_update_artifact_state_returns_dict(self):
        """update_artifact_state internally calls get_artifact — dict(row) path."""
        conn = _make_conn()
        created = upsert_artifact(
            conn,
            artifact_type="report",
            source_kind="test",
            title="State update test",
        )
        aid = created["artifact_id"]
        updated = update_artifact_state(
            conn, artifact_id=aid, status="surfaced", delivery_state="emailed"
        )
        assert isinstance(updated, dict)
        assert updated["status"] == "surfaced"
        assert updated["delivery_state"] == "emailed"

    def test_no_row_factory_before_ensure_schema_does_not_break(self):
        """If someone creates a conn without calling ensure_schema first,
        upsert_artifact calls ensure_schema which sets row_factory.
        This should still work."""
        conn = sqlite3.connect(":memory:")
        # Deliberately do NOT call ensure_schema
        result = upsert_artifact(
            conn,
            artifact_type="report",
            source_kind="test",
            title="No pre-ensure_schema",
        )
        assert isinstance(result, dict)
        assert result["title"] == "No pre-ensure_schema"

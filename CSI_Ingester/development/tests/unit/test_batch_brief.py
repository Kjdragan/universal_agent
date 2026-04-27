"""Unit tests for csi_ingester.batch_brief module."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

from csi_ingester.batch_brief import (
    _build_prompt,
    _fallback_brief,
    fetch_undelivered_events,
    mark_events_delivered,
    run_batch_cycle,
)
from csi_ingester.store.sqlite import ensure_schema
import pytest


@pytest.fixture
def conn() -> sqlite3.Connection:
    """In-memory SQLite with full CSI schema."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    ensure_schema(db)
    return db


def _insert_event(conn: sqlite3.Connection, event_id: str, source: str = "youtube_channel_rss",
                  event_type: str = "new_video", title: str = "Test Video") -> None:
    conn.execute(
        """
        INSERT INTO events (event_id, dedupe_key, source, event_type, occurred_at, received_at,
                            subject_json, routing_json, metadata_json, delivered)
        VALUES (?, ?, ?, ?, datetime('now'), datetime('now'), ?, '{}', '{}', 0)
        """,
        (event_id, f"dk_{event_id}", source, event_type,
         json.dumps({"title": title, "summary": f"Summary for {title}"})),
    )
    conn.commit()


class TestFetchUndeliveredEvents:
    def test_empty_table(self, conn: sqlite3.Connection) -> None:
        assert fetch_undelivered_events(conn) == []

    def test_returns_undelivered(self, conn: sqlite3.Connection) -> None:
        _insert_event(conn, "e1", title="Video A")
        _insert_event(conn, "e2", title="Video B")
        # Mark e1 delivered
        conn.execute("UPDATE events SET delivered = 1 WHERE event_id = 'e1'")
        conn.commit()

        rows = fetch_undelivered_events(conn)
        assert len(rows) == 1
        assert rows[0]["event_id"] == "e2"
        assert rows[0]["subject"]["title"] == "Video B"

    def test_limit_500(self, conn: sqlite3.Connection) -> None:
        for i in range(10):
            _insert_event(conn, f"e{i}", title=f"V{i}")
        rows = fetch_undelivered_events(conn)
        assert len(rows) == 10


class TestMarkEventsDelivered:
    def test_marks_multiple(self, conn: sqlite3.Connection) -> None:
        _insert_event(conn, "e1")
        _insert_event(conn, "e2")
        _insert_event(conn, "e3")

        count = mark_events_delivered(conn, ["e1", "e3"])
        assert count == 2

        remaining = fetch_undelivered_events(conn)
        assert len(remaining) == 1
        assert remaining[0]["event_id"] == "e2"

    def test_empty_list(self, conn: sqlite3.Connection) -> None:
        assert mark_events_delivered(conn, []) == 0


class TestBuildPrompt:
    def test_builds_numbered_list(self) -> None:
        rows = [
            {"source": "reddit", "event_type": "new_post", "occurred_at": "2026-03-15T01:00:00Z",
             "subject": {"title": "AI Agents Explosion", "summary": "Big trend in AI agents"}},
            {"source": "youtube_channel_rss", "event_type": "new_video", "occurred_at": "2026-03-15T02:00:00Z",
             "subject": {"title": "GPT-5 Release", "summary": "OpenAI releases GPT-5"}},
        ]
        prompt = _build_prompt(rows)
        assert "## Batch of 2 events" in prompt
        assert "1. [reddit]" in prompt
        assert "2. [youtube_channel_rss]" in prompt
        assert "AI Agents Explosion" in prompt
        assert "GPT-5 Release" in prompt


class TestFallbackBrief:
    def test_groups_by_source(self) -> None:
        rows = [
            {"source": "reddit", "subject": {"title": "Post A"}},
            {"source": "reddit", "subject": {"title": "Post B"}},
            {"source": "youtube_channel_rss", "subject": {"title": "Video C"}},
        ]
        brief = _fallback_brief(rows)
        assert "3 events" in brief
        assert "reddit (2 events)" in brief
        assert "youtube_channel_rss (1 events)" in brief
        assert "Post A" in brief
        assert "Video C" in brief
        assert "LLM unavailable" in brief


class TestRunBatchCycle:
    @pytest.mark.asyncio
    async def test_skips_below_threshold(self, conn: sqlite3.Connection) -> None:
        _insert_event(conn, "e1")  # Only 1 event, threshold is 3
        config = MagicMock()
        config.batch_min_events = 3
        config.gemini_api_key = ""
        config.gemini_model = "gemini-3-flash-preview"
        config.batch_interval_seconds = 7200

        result = await run_batch_cycle(conn=conn, config=config, emitter=None)
        assert result["status"] == "skipped"
        assert result["reason"] == "below_threshold"
        assert result["event_count"] == 1

    @pytest.mark.asyncio
    async def test_generates_fallback_without_api_key(self, conn: sqlite3.Connection) -> None:
        for i in range(5):
            _insert_event(conn, f"e{i}", title=f"Video {i}", source="youtube_channel_rss")
        config = MagicMock()
        config.batch_min_events = 3
        config.gemini_api_key = ""
        config.gemini_model = "gemini-3-flash-preview"
        config.batch_interval_seconds = 7200

        result = await run_batch_cycle(conn=conn, config=config, emitter=None)
        assert result["status"] == "generated"
        assert result["event_count"] == 5
        assert result["llm_used"] is False
        assert result["events_marked"] == 5

        # All events should now be marked delivered
        remaining = fetch_undelivered_events(conn)
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_calls_gemini_when_key_set(self, conn: sqlite3.Connection) -> None:
        for i in range(3):
            _insert_event(conn, f"e{i}", title=f"V{i}")
        config = MagicMock()
        config.batch_min_events = 3
        config.gemini_api_key = "fake-key"
        config.gemini_model = "gemini-3-flash-preview"
        config.batch_interval_seconds = 7200

        with patch("csi_ingester.batch_brief._call_gemini", new_callable=AsyncMock) as mock_gemini:
            mock_gemini.return_value = "# AI Trends Digest\n\nThree new signals detected."
            result = await run_batch_cycle(conn=conn, config=config, emitter=None)

        assert result["status"] == "generated"
        assert result["llm_used"] is True
        assert result["brief_headline"] == "AI Trends Digest"
        mock_gemini.assert_called_once()
        # Check model was passed
        call_kwargs = mock_gemini.call_args
        assert call_kwargs.kwargs["model"] == "gemini-3-flash-preview"

    @pytest.mark.asyncio
    async def test_fallback_on_llm_failure(self, conn: sqlite3.Connection) -> None:
        for i in range(3):
            _insert_event(conn, f"e{i}", title=f"V{i}")
        config = MagicMock()
        config.batch_min_events = 3
        config.gemini_api_key = "fake-key"
        config.gemini_model = "gemini-3-flash-preview"
        config.batch_interval_seconds = 7200

        with patch("csi_ingester.batch_brief._call_gemini", new_callable=AsyncMock) as mock_gemini:
            mock_gemini.side_effect = Exception("API error")
            result = await run_batch_cycle(conn=conn, config=config, emitter=None)

        assert result["status"] == "generated"
        assert result["llm_used"] is False
        assert result["event_count"] == 3

    @pytest.mark.asyncio
    async def test_emits_to_ua_when_emitter_present(self, conn: sqlite3.Connection) -> None:
        for i in range(3):
            _insert_event(conn, f"e{i}", title=f"V{i}")
        config = MagicMock()
        config.batch_min_events = 3
        config.gemini_api_key = ""
        config.gemini_model = "gemini-3-flash-preview"
        config.batch_interval_seconds = 7200

        emitter = MagicMock()
        emitter.emit_with_retries = AsyncMock(return_value=(True, 200, {"ok": True}))

        result = await run_batch_cycle(conn=conn, config=config, emitter=emitter)
        assert result["status"] == "emitted"
        assert result["delivered"] is True
        assert result["events_marked"] == 3
        emitter.emit_with_retries.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_lite_model(self, conn: sqlite3.Connection) -> None:
        """Test that an alternative model like gemini-3.1-flash-lite-preview is passed."""
        for i in range(3):
            _insert_event(conn, f"e{i}", title=f"V{i}")
        config = MagicMock()
        config.batch_min_events = 3
        config.gemini_api_key = "fake-key"
        config.gemini_model = "gemini-3.1-flash-lite-preview"
        config.batch_interval_seconds = 7200

        with patch("csi_ingester.batch_brief._call_gemini", new_callable=AsyncMock) as mock_gemini:
            mock_gemini.return_value = "# Lite Brief\n\nQuick summary."
            result = await run_batch_cycle(conn=conn, config=config, emitter=None)

        call_kwargs = mock_gemini.call_args
        assert call_kwargs.kwargs["model"] == "gemini-3.1-flash-lite-preview"
        assert result["llm_used"] is True

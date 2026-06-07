"""Unit tests for transcript_error column persistence in rss_semantic_enrich."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from csi_ingester.store.sqlite import connect, ensure_schema


def _extract_transcript_error_class(transcript_result: dict[str, Any]) -> str | None:
    """Copy of the helper from csi_rss_semantic_enrich.py for testing."""
    if transcript_result.get("ok"):
        return None

    attempts = transcript_result.get("endpoint_attempts", [])
    if attempts and isinstance(attempts, list):
        last_attempt = attempts[-1]
        if isinstance(last_attempt, dict):
            if last_attempt.get("anti_bot_suspected"):
                return "ip_block"
            http_status = int(last_attempt.get("http_status") or 0)
            if http_status == 401:
                return "http_401"
            elif http_status == 403:
                return "http_403"
            elif http_status == 429:
                return "http_429"
            elif http_status >= 500:
                return f"http_{http_status}"
            elif http_status >= 400:
                return f"http_{http_status}"
            error = str(last_attempt.get("error") or "").strip().lower()
            if "timeout" in error:
                return "timeout"
            elif error == "request_exception":
                return "request_exception"
            elif error == "http_error":
                return "http_error"

    error = str(transcript_result.get("error") or "").strip().lower()
    if "timeout" in error:
        return "timeout"
    elif error == "request_exception":
        return "request_exception"
    elif error == "http_error":
        return "http_error"
    elif error == "no_endpoints_configured":
        return "no_endpoints"
    elif error:
        return error

    return "unknown"


@pytest.fixture
def db_path():
    """Create a temporary database for each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "test.db"
        yield db_file


def test_transcript_error_classifies_http_401(db_path):
    """HTTP 401 auth error is classified correctly."""
    error_class = _extract_transcript_error_class({
        "ok": False,
        "endpoint_attempts": [
            {
                "endpoint": "http://endpoint-1/api",
                "ok": False,
                "error": "http_error",
                "http_status": 401,
                "anti_bot_suspected": False,
            }
        ],
    })
    assert error_class == "http_401"


def test_transcript_error_classifies_ip_block(db_path):
    """IP block (anti_bot_suspected) is classified correctly."""
    error_class = _extract_transcript_error_class({
        "ok": False,
        "endpoint_attempts": [
            {
                "endpoint": "http://endpoint-1/api",
                "ok": False,
                "error": "http_error",
                "http_status": 429,
                "anti_bot_suspected": True,
            }
        ],
    })
    assert error_class == "ip_block"


def test_transcript_error_classifies_timeout(db_path):
    """Timeout error is classified correctly.

    The last endpoint attempt's ``error`` field carries the 'timeout' marker,
    so the helper's endpoint-attempts branch classifies it as ``timeout``.
    """
    error_class = _extract_transcript_error_class({
        "ok": False,
        "endpoint_attempts": [
            {
                "endpoint": "http://endpoint-1/api",
                "ok": False,
                "error": "timeout",
                "http_status": 0,
                "anti_bot_suspected": False,
            }
        ],
        "error": "timeout",
    })
    assert error_class == "timeout"


def test_transcript_error_top_level_timeout_fallback(db_path):
    """When no endpoint_attempts are present, the top-level 'timeout' error
    field is used to classify as ``timeout``."""
    error_class = _extract_transcript_error_class({
        "ok": False,
        "endpoint_attempts": [],
        "error": "timeout",
    })
    assert error_class == "timeout"


def test_transcript_error_classifies_request_exception(db_path):
    """request_exception (network error) is classified correctly."""
    error_class = _extract_transcript_error_class({
        "ok": False,
        "endpoint_attempts": [
            {
                "endpoint": "http://endpoint-1/api",
                "ok": False,
                "error": "request_exception",
                "http_status": 0,
                "anti_bot_suspected": False,
            }
        ],
    })
    assert error_class == "request_exception"


def test_transcript_error_none_when_ok(db_path):
    """No error classification when transcript fetch succeeds."""
    error_class = _extract_transcript_error_class({
        "ok": True,
        "transcript_text": "This is a transcript",
        "endpoint_attempts": [],
    })
    assert error_class is None


def test_transcript_error_persists_to_database(db_path):
    """A failed transcript result with error class persists to transcript_error column."""
    conn = connect(db_path)
    ensure_schema(conn)

    # Simulate a failed transcript fetch with http_401
    transcript_result = {
        "ok": False,
        "error": "http_error",
        "endpoint_attempts": [
            {
                "endpoint": "http://endpoint-1/api",
                "ok": False,
                "error": "http_error",
                "http_status": 401,
                "anti_bot_suspected": False,
            }
        ],
    }
    transcript_error = _extract_transcript_error_class(transcript_result)
    assert transcript_error == "http_401"

    # Insert a row with the error class
    conn.execute(
        """
        INSERT INTO rss_event_analysis (
            event_id, source, video_id, channel_id, transcript_status,
            category, transcript_error, analysis_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "test_event_401",
            "youtube_channel_rss",
            "vid_123",
            "ch_456",
            "failed",
            "other_interest",
            transcript_error,
            json.dumps({"error": transcript_result.get("error")}),
        ),
    )
    conn.commit()

    # Query back and verify
    row = conn.execute(
        "SELECT transcript_error FROM rss_event_analysis WHERE event_id = ?",
        ("test_event_401",),
    ).fetchone()
    assert row is not None
    assert row["transcript_error"] == "http_401"
    conn.close()


def test_transcript_error_persists_with_failover_attempts(db_path):
    """Failed transcript across multiple endpoints captures the final error."""
    conn = connect(db_path)
    ensure_schema(conn)

    # Simulate failover across 3 endpoints, final one is http_403
    transcript_result = {
        "ok": False,
        "error": "http_error",
        "_endpoint": "http://endpoint-3/api",
        "endpoint_attempts": [
            {
                "endpoint": "http://endpoint-1/api",
                "ok": False,
                "error": "request_exception",
                "http_status": 0,
                "anti_bot_suspected": False,
            },
            {
                "endpoint": "http://endpoint-2/api",
                "ok": False,
                "error": "request_exception",
                "http_status": 0,
                "anti_bot_suspected": False,
            },
            {
                "endpoint": "http://endpoint-3/api",
                "ok": False,
                "error": "http_error",
                "http_status": 403,
                "anti_bot_suspected": False,
            },
        ],
    }
    transcript_error = _extract_transcript_error_class(transcript_result)
    assert transcript_error == "http_403"

    # Insert the row
    conn.execute(
        """
        INSERT INTO rss_event_analysis (
            event_id, source, video_id, channel_id, transcript_status,
            category, transcript_error, analysis_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "test_event_failover",
            "youtube_channel_rss",
            "vid_789",
            "ch_000",
            "failed",
            "other_interest",
            transcript_error,
            json.dumps({"endpoint_attempts": transcript_result.get("endpoint_attempts")}),
        ),
    )
    conn.commit()

    # Verify the error was persisted
    row = conn.execute(
        "SELECT transcript_error FROM rss_event_analysis WHERE event_id = ?",
        ("test_event_failover",),
    ).fetchone()
    assert row is not None
    assert row["transcript_error"] == "http_403"
    conn.close()


def test_migration_0012_is_idempotent(db_path):
    """Migration 0012 adds transcript_error column only if it doesn't exist."""
    conn = connect(db_path)
    ensure_schema(conn)

    # Column should exist after ensure_schema
    columns = {row[1] for row in conn.execute(
        "PRAGMA table_info(rss_event_analysis)"
    ).fetchall()}
    assert "transcript_error" in columns

    # Running ensure_schema again should not fail (idempotent)
    ensure_schema(conn)
    columns = {row[1] for row in conn.execute(
        "PRAGMA table_info(rss_event_analysis)"
    ).fetchall()}
    assert "transcript_error" in columns
    conn.close()

"""Tests for the PR B feedback/digest-pause HMAC helpers in cron_artifact_notifier."""

from __future__ import annotations

import sqlite3

import pytest

from universal_agent.services.cron_artifact_notifier import (
    is_digest_paused,
    set_digest_pause,
    sign_digest_pause_token,
    sign_feedback_token,
    verify_digest_pause_token,
    verify_feedback_token,
)


@pytest.fixture(autouse=True)
def _hmac_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UA_ARTIFACT_ACK_SECRET", "test-ack-secret-do-not-use-in-prod")


class TestFeedbackToken:
    def test_round_trip_up(self):
        tok = sign_feedback_token("pa_test001", "up")
        assert tok
        assert verify_feedback_token("pa_test001", "up", tok)

    def test_round_trip_down(self):
        tok = sign_feedback_token("pa_test002", "down")
        assert tok
        assert verify_feedback_token("pa_test002", "down", tok)

    def test_tampered_token_fails(self):
        tok = sign_feedback_token("pa_test003", "up")
        bad = "0" + tok[1:] if tok[0] != "0" else "1" + tok[1:]
        assert not verify_feedback_token("pa_test003", "up", bad)

    def test_wrong_vote_fails(self):
        tok = sign_feedback_token("pa_test004", "up")
        assert not verify_feedback_token("pa_test004", "down", tok)

    def test_wrong_artifact_id_fails(self):
        tok = sign_feedback_token("pa_test005", "up")
        assert not verify_feedback_token("pa_test006", "up", tok)

    def test_invalid_vote_returns_empty_token(self):
        assert sign_feedback_token("pa_test007", "neutral") == ""
        assert sign_feedback_token("pa_test007", "") == ""

    def test_empty_artifact_id_returns_empty_token(self):
        assert sign_feedback_token("", "up") == ""

    def test_garbage_token_does_not_raise(self):
        assert not verify_feedback_token("pa_x", "up", "not-a-real-token")
        assert not verify_feedback_token("pa_x", "up", "")
        assert not verify_feedback_token("pa_x", "up", "   ")

    def test_no_secret_returns_empty(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("UA_ARTIFACT_ACK_SECRET", raising=False)
        monkeypatch.delenv("UA_OPS_TOKEN", raising=False)
        monkeypatch.delenv("UA_INTERNAL_API_TOKEN", raising=False)
        assert sign_feedback_token("pa_x", "up") == ""
        assert not verify_feedback_token("pa_x", "up", "anything")


class TestDigestPauseToken:
    def test_round_trip(self):
        tok = sign_digest_pause_token(24)
        assert tok
        assert verify_digest_pause_token(24, tok)

    def test_wrong_hours_fails(self):
        tok = sign_digest_pause_token(24)
        assert not verify_digest_pause_token(48, tok)

    def test_zero_hours_returns_empty(self):
        assert sign_digest_pause_token(0) == ""

    def test_negative_hours_returns_empty(self):
        assert sign_digest_pause_token(-1) == ""

    def test_garbage_token_does_not_raise(self):
        assert not verify_digest_pause_token(24, "garbage")
        assert not verify_digest_pause_token(24, "")


class TestDigestPauseState:
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE digest_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                paused_until TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            INSERT OR IGNORE INTO digest_state (id) VALUES (1);
            """
        )
        return conn

    def test_initially_not_paused(self):
        conn = self._conn()
        assert not is_digest_paused(conn)

    def test_set_paused_future(self):
        from datetime import datetime, timedelta, timezone
        conn = self._conn()
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        set_digest_pause(conn, future)
        assert is_digest_paused(conn)

    def test_paused_past_is_not_active(self):
        from datetime import datetime, timedelta, timezone
        conn = self._conn()
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        set_digest_pause(conn, past)
        assert not is_digest_paused(conn)

    def test_missing_table_is_not_paused(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        assert not is_digest_paused(conn)

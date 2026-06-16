"""Regression tests for the trusted-inbox queue retry backoff.

A poison item (triage perpetually "not ready", or a permanently "busy" dispatch)
used to retry forever, inflating attempt_count without bound until
``base_seconds * 2 ** (attempts - 1)`` raised ``OverflowError: int too large to
convert to float``. The fix: cap the exponent AND fail-terminal past a max-attempts
ceiling.
"""

import pytest

from universal_agent.services.agentmail_service import (
    _QUEUE_STATUS_BUSY_RETRY,
    _QUEUE_STATUS_FAILED,
    AgentMailService,
)


@pytest.fixture
def svc(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(tmp_path / "activity_state.db"))
    monkeypatch.setenv("UA_AGENTMAIL_INBOX_RETRY_BASE_SECONDS", "10")
    monkeypatch.setenv("UA_AGENTMAIL_INBOX_RETRY_MAX_SECONDS", "900")
    monkeypatch.setenv("UA_AGENTMAIL_INBOX_RETRY_MAX_ATTEMPTS", "12")
    s = AgentMailService()
    s._ensure_queue_schema()
    return s


def _seed(svc, queue_id, attempt_count=0):
    now = "2026-06-16T00:00:00Z"
    with svc._queue_connect() as conn:
        conn.execute(
            """
            INSERT INTO agentmail_inbox_queue
              (queue_id, message_id, sender, sender_email, session_key,
               status, attempt_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)
            """,
            (queue_id, f"m-{queue_id}", "s", "s@example.com", f"sk-{queue_id}",
             int(attempt_count), now, now),
        )


def _status(svc, queue_id):
    with svc._queue_connect() as conn:
        row = conn.execute(
            "SELECT status, next_attempt_at FROM agentmail_inbox_queue WHERE queue_id=?",
            (queue_id,),
        ).fetchone()
    return (str(row["status"]), row["next_attempt_at"]) if row else (None, None)


def test_retry_does_not_overflow_on_huge_attempts(svc):
    # The exact regression: this used to raise OverflowError.
    svc._retry_queue_item("q-huge", error="triage_not_ready", attempts=10_000)


def test_retry_fails_terminal_past_max_attempts(svc):
    _seed(svc, "q-exhausted", attempt_count=11)
    svc._retry_queue_item("q-exhausted", error="busy", attempts=12)  # >= max(12)
    status, _ = _status(svc, "q-exhausted")
    assert status == _QUEUE_STATUS_FAILED


def test_retry_reschedules_under_max_attempts(svc):
    _seed(svc, "q-retry", attempt_count=1)
    svc._retry_queue_item("q-retry", error="busy", attempts=2)  # < max
    status, next_at = _status(svc, "q-retry")
    assert status == _QUEUE_STATUS_BUSY_RETRY
    assert next_at  # a backoff time was scheduled


def test_huge_attempts_seeded_item_is_failed_not_retried(svc):
    # A pre-existing poison row with a giant attempt_count is retired cleanly.
    _seed(svc, "q-poison", attempt_count=4000)
    svc._retry_queue_item("q-poison", error="triage_not_ready", attempts=4001)
    status, _ = _status(svc, "q-poison")
    assert status == _QUEUE_STATUS_FAILED

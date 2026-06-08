from __future__ import annotations

from contextlib import asynccontextmanager
import sqlite3

from fastapi.testclient import TestClient
import pytest

from universal_agent import gateway_server


@pytest.fixture
def csi_db(tmp_path, monkeypatch):
    """A temp csi.db with the transcript_incidents table created."""
    db_path = tmp_path / "csi.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transcript_incidents (
            incident_key TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            first_red_at TEXT,
            last_red_at TEXT,
            opened_epoch INTEGER,
            email_count INTEGER NOT NULL DEFAULT 0,
            last_email_epoch INTEGER,
            next_email_epoch INTEGER,
            resolved_at TEXT,
            last_reason TEXT
        )
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("CSI_DB_PATH", str(db_path))
    return db_path


class _FakeClock:
    def __init__(self, now: int):
        self.now = now

    def time(self):
        return self.now


@pytest.fixture
def email_calls(monkeypatch):
    """Patch the email resolver so no real mail is sent; record call subjects."""
    calls: list[dict[str, str]] = []

    async def _fake_send(*, subject: str, text: str) -> bool:
        calls.append({"subject": subject, "text": text})
        return True

    monkeypatch.setattr(gateway_server, "_send_csi_incident_email", _fake_send)
    return calls


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(gateway_server, "_DEPLOYMENT_PROFILE", "local_workstation")
    monkeypatch.setattr(gateway_server, "SESSION_API_TOKEN", "")
    monkeypatch.setenv("UA_YOUTUBE_INGEST_TOKEN", "ingest-token")
    monkeypatch.setenv("UA_CSI_INCIDENT_REMINDER_HOURS", "4")
    # Keep reminders inside the waking window for the cadence tests by default.
    monkeypatch.setattr(gateway_server, "_csi_incident_in_waking_window", lambda _epoch: True)

    @asynccontextmanager
    async def _test_lifespan(_app):
        yield

    monkeypatch.setattr(gateway_server.app.router, "lifespan_context", _test_lifespan)
    with TestClient(gateway_server.app) as c:
        yield c


_AUTH = {"Authorization": "Bearer ingest-token"}
_BASE_EPOCH = 1_700_000_000  # date-pinned-ok (fixed reference instant for clock control)
_INTERVAL = 4 * 3600


def _post(client, status, *, summary="s", reasons=None):
    return client.post(
        "/api/v1/csi/transcript_incident",
        headers=_AUTH,
        json={"status": status, "summary": summary, "reasons": reasons or ["r1"]},
    )


def test_first_red_emails_and_seeds_next(client, csi_db, email_calls, monkeypatch):
    monkeypatch.setattr(gateway_server, "time", _FakeClock(_BASE_EPOCH))
    resp = _post(client, "red")
    assert resp.status_code == 200
    body = resp.json()
    assert body["emailed"] is True
    assert body["state"] == "open"
    assert body["email_count"] == 1
    assert body["next_email_at"] is not None
    assert len(email_calls) == 1
    assert "reminder 1" in email_calls[0]["subject"]


def test_immediate_second_red_does_not_email(client, csi_db, email_calls, monkeypatch):
    monkeypatch.setattr(gateway_server, "time", _FakeClock(_BASE_EPOCH))
    assert _post(client, "red").json()["email_count"] == 1
    # Same instant — well before next_email_epoch.
    resp = _post(client, "red")
    body = resp.json()
    assert body["emailed"] is False
    assert body["email_count"] == 1
    assert len(email_calls) == 1


def test_red_after_interval_emails_again(client, csi_db, email_calls, monkeypatch):
    monkeypatch.setattr(gateway_server, "time", _FakeClock(_BASE_EPOCH))
    assert _post(client, "red").json()["email_count"] == 1
    # Advance past the reminder interval.
    monkeypatch.setattr(gateway_server, "time", _FakeClock(_BASE_EPOCH + _INTERVAL + 1))
    resp = _post(client, "red")
    body = resp.json()
    assert body["emailed"] is True
    assert body["email_count"] == 2
    assert len(email_calls) == 2
    assert "reminder 2" in email_calls[1]["subject"]


def test_green_resolves_and_sends_recovery_once(client, csi_db, email_calls, monkeypatch):
    monkeypatch.setattr(gateway_server, "time", _FakeClock(_BASE_EPOCH))
    _post(client, "red")
    assert len(email_calls) == 1

    monkeypatch.setattr(gateway_server, "time", _FakeClock(_BASE_EPOCH + 60))
    resp = _post(client, "green")
    body = resp.json()
    assert body["emailed"] is True
    assert body["state"] == "resolved"
    assert len(email_calls) == 2
    assert "Recovered" in email_calls[1]["subject"]

    # Second green on an already-resolved incident -> no email, no-op.
    resp2 = _post(client, "green")
    body2 = resp2.json()
    assert body2["emailed"] is False
    assert body2["state"] == "resolved"
    assert len(email_calls) == 2


def test_reminder_outside_waking_window_not_sent(client, csi_db, email_calls, monkeypatch):
    # First red still fires (first email is never window-gated).
    monkeypatch.setattr(gateway_server, "time", _FakeClock(_BASE_EPOCH))
    assert _post(client, "red").json()["email_count"] == 1

    # Reminder is now DUE (past interval) but we are outside the waking window.
    monkeypatch.setattr(gateway_server, "_csi_incident_in_waking_window", lambda _epoch: False)
    monkeypatch.setattr(gateway_server, "time", _FakeClock(_BASE_EPOCH + _INTERVAL + 1))
    resp = _post(client, "red")
    body = resp.json()
    assert body["emailed"] is False
    assert body["email_count"] == 1  # not advanced
    assert len(email_calls) == 1

    # next_email_epoch must NOT have advanced — once back in window, it fires.
    monkeypatch.setattr(gateway_server, "_csi_incident_in_waking_window", lambda _epoch: True)
    resp2 = _post(client, "red")
    body2 = resp2.json()
    assert body2["emailed"] is True
    assert body2["email_count"] == 2
    assert len(email_calls) == 2

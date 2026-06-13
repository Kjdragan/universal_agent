"""Tests for the proactive_health finding-acknowledge endpoint.

Mirrors ``tests/unit/test_artifact_ack_endpoint.py``: the HTTP endpoint
(``gateway_server.proactive_health_ack_get``) is a thin wrapper over HMAC
verification (``verify_finding_ack_token``) + the idempotent
``proactive_health_snapshot.record_ack`` transition, so we exercise that core
directly rather than booting the full FastAPI app.
"""

from __future__ import annotations

import sqlite3

import pytest

from universal_agent.services import proactive_health_snapshot as snap
from universal_agent.services.proactive_health_notifier import (
    sign_finding_ack_token,
    verify_finding_ack_token,
)

FINDING_ID = "invariant:zai_inference_health"


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    snap.ensure_ack_schema(c)
    return c


def _ack_via_endpoint_core(conn: sqlite3.Connection, finding_id: str, token: str):
    """Mirror of gateway_server.proactive_health_ack_get's decision core —
    kept here so the test doesn't have to import the full FastAPI module
    surface (same convention as test_artifact_ack_endpoint.py)."""
    if not (finding_id or "").strip() or not verify_finding_ack_token(
        finding_id, token or ""
    ):
        return None  # the route renders the 401 "Link expired or invalid" page
    return snap.record_ack(conn, finding_id=finding_id, ack_source="email_link")


def test_hmac_token_required(monkeypatch: pytest.MonkeyPatch, conn) -> None:
    """Without a valid HMAC token the route must reject before any write."""
    monkeypatch.setenv("UA_ARTIFACT_ACK_SECRET", "shared-secret-x")
    good = sign_finding_ack_token(FINDING_ID)
    assert verify_finding_ack_token(FINDING_ID, good) is True

    assert _ack_via_endpoint_core(conn, FINDING_ID, "wrong-token") is None
    assert _ack_via_endpoint_core(conn, "invariant:other", good) is None
    assert _ack_via_endpoint_core(conn, "", good) is None
    # Nothing was written on the rejected paths.
    assert snap.get_active_acks(conn) == {}


def test_valid_token_records_active_ack(monkeypatch: pytest.MonkeyPatch, conn) -> None:
    monkeypatch.setenv("UA_ARTIFACT_ACK_SECRET", "shared-secret-x")
    token = sign_finding_ack_token(FINDING_ID)

    out = _ack_via_endpoint_core(conn, FINDING_ID, token)
    assert out is not None
    assert out["created"] is True
    assert out["status"] == "active"
    assert out["ack_source"] == "email_link"
    assert out["acked_at_utc"]
    assert FINDING_ID in snap.get_active_acks(conn)


def test_re_click_is_idempotent(monkeypatch: pytest.MonkeyPatch, conn) -> None:
    """A second click returns the existing ack (the route renders the
    "already acknowledged" page off created=False)."""
    monkeypatch.setenv("UA_ARTIFACT_ACK_SECRET", "shared-secret-x")
    token = sign_finding_ack_token(FINDING_ID)

    first = _ack_via_endpoint_core(conn, FINDING_ID, token)
    second = _ack_via_endpoint_core(conn, FINDING_ID, token)
    assert first["created"] is True
    assert second["created"] is False
    assert second["id"] == first["id"]
    assert second["acked_at_utc"] == first["acked_at_utc"]
    assert (
        conn.execute("SELECT COUNT(*) FROM proactive_health_acks").fetchone()[0] == 1
    )


def test_no_secret_fails_closed(monkeypatch: pytest.MonkeyPatch, conn) -> None:
    """With no signing secret anywhere, every token is invalid (the email
    side never minted a link in this configuration either)."""
    for var in ("UA_ARTIFACT_ACK_SECRET", "UA_OPS_TOKEN", "UA_INTERNAL_API_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert _ack_via_endpoint_core(conn, FINDING_ID, "123.deadbeef") is None
    assert snap.get_active_acks(conn) == {}

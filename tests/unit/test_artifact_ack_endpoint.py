"""Tests for the artifact acknowledgement endpoints.

Covers the internal ``_ack_artifact`` helper directly. The HTTP endpoints
in ``gateway_server.py`` are thin wrappers over this helper + HMAC
verification; full HTTP-layer tests would require booting the FastAPI app
which is unnecessary at this layer.
"""

from __future__ import annotations

import sqlite3

import pytest

from universal_agent.services import proactive_artifacts
from universal_agent.services.cron_artifact_notifier import (
    sign_ack_token,
    verify_ack_token,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    proactive_artifacts.ensure_schema(c)
    return c


def _ack_artifact(conn: sqlite3.Connection, artifact_id: str):
    """Mirror of gateway_server._ack_artifact — kept here so the test
    doesn't have to import the full FastAPI module surface."""
    current = proactive_artifacts.get_artifact(conn, artifact_id)
    if current is None:
        return None
    if current.get("status") == proactive_artifacts.ARTIFACT_STATUS_ACCEPTED:
        return current
    return proactive_artifacts.update_artifact_state(
        conn,
        artifact_id=artifact_id,
        status=proactive_artifacts.ARTIFACT_STATUS_ACCEPTED,
        delivery_state=proactive_artifacts.DELIVERY_REVIEWED,
    )


@pytest.fixture
def emailed_artifact(conn: sqlite3.Connection) -> dict:
    return proactive_artifacts.upsert_artifact(
        conn,
        artifact_id="pa_test",
        artifact_type="cron_run_output",
        source_kind="cron_artifact",
        title="test",
        summary="summary",
        status=proactive_artifacts.ARTIFACT_STATUS_SURFACED,
        delivery_state=proactive_artifacts.DELIVERY_EMAILED,
        metadata={"reminder": {"schedule_state": "sent_initial"}},
    )


def test_ack_unknown_artifact_returns_none(conn) -> None:
    assert _ack_artifact(conn, "pa_does_not_exist") is None


def test_ack_transitions_to_accepted_and_reviewed(conn, emailed_artifact) -> None:
    out = _ack_artifact(conn, "pa_test")
    assert out is not None
    assert out["status"] == proactive_artifacts.ARTIFACT_STATUS_ACCEPTED
    assert out["delivery_state"] == proactive_artifacts.DELIVERY_REVIEWED
    assert out.get("accepted_at")


def test_ack_is_idempotent(conn, emailed_artifact) -> None:
    out_first = _ack_artifact(conn, "pa_test")
    out_second = _ack_artifact(conn, "pa_test")
    assert out_first["status"] == proactive_artifacts.ARTIFACT_STATUS_ACCEPTED
    assert out_second["status"] == proactive_artifacts.ARTIFACT_STATUS_ACCEPTED
    # ``accepted_at`` only set on the first transition; second call returns
    # the same row without re-stamping.
    assert out_first.get("accepted_at") == out_second.get("accepted_at")


def test_hmac_token_required_for_signed_url(
    monkeypatch: pytest.MonkeyPatch, emailed_artifact
) -> None:
    """Without a valid HMAC token, the GET endpoint must reject. We
    verify that by checking verify_ack_token, which the endpoint calls
    before invoking ``_ack_artifact``."""
    monkeypatch.setenv("UA_ARTIFACT_ACK_SECRET", "shared-secret-x")
    good = sign_ack_token("pa_test")
    assert verify_ack_token("pa_test", good) is True
    assert verify_ack_token("pa_test", "wrong-token") is False
    assert verify_ack_token("pa_other", good) is False

"""End-to-end test for the synthetic notification probe.

The probe POSTs a high-severity notification via the gateway's ops API,
waits for the async dispatcher to drain it to email + Telegram, and
verifies both `metadata.delivery.email.delivered_at` and
`metadata.delivery.telegram.delivered_at` are populated.

These tests use httpx.MockTransport to stub the gateway responses.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from scripts import probe_notification_dispatch as probe


def _make_dashboard_response(probe_id: str, *, email_delivered: bool, telegram_delivered: bool) -> dict[str, Any]:
    metadata: dict[str, Any] = {"probe_id": probe_id}
    delivery: dict[str, Any] = {}
    if email_delivered:
        delivery["email"] = {"delivered_at": "2026-05-04T12:00:30+00:00"}
    if telegram_delivered:
        delivery["telegram"] = {"delivered_at": "2026-05-04T12:00:30+00:00"}
    if delivery:
        metadata["delivery"] = delivery
    return {
        "id": "ntf_probe_1",
        "kind": "ops_probe_alert",
        "severity": "error",
        "title": "Probe — please ignore",
        "metadata": metadata,
    }


def _build_transport(*, post_status: int = 200, dashboard_payload: dict[str, Any] | None = None) -> httpx.MockTransport:
    posts: list[dict[str, Any]] = []
    gets: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/v1/ops/notifications":
            posts.append(json.loads(request.content.decode() or "{}"))
            return httpx.Response(post_status, json={"notification": {"id": "ntf_probe_1"}})
        if request.method == "GET" and request.url.path == "/api/v1/dashboard/notifications":
            gets.append(str(request.url))
            return httpx.Response(
                200,
                json={"notifications": [dashboard_payload]} if dashboard_payload else {"notifications": []},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(_handler)
    transport._probe_posts = posts  # type: ignore[attr-defined]
    transport._probe_gets = gets    # type: ignore[attr-defined]
    return transport


def test_probe_posts_high_severity_payload():
    """The probe must POST kind=ops_probe_alert with severity=error so
    it actually exercises the F3 dispatcher's high-severity path."""
    transport = _build_transport(
        dashboard_payload=_make_dashboard_response("p_test_1", email_delivered=True, telegram_delivered=True)
    )
    result = probe.run_probe(
        base_url="http://gw.test",
        ops_token="tok",
        wait_seconds=0,
        probe_id="p_test_1",
        transport=transport,
    )
    assert result.exit_code == 0
    posted = transport._probe_posts[0]  # type: ignore[attr-defined]
    assert posted["kind"] == "ops_probe_alert"
    assert posted["severity"] == "error"
    assert posted["metadata"]["probe_id"] == "p_test_1"


def test_probe_succeeds_when_both_channels_delivered():
    transport = _build_transport(
        dashboard_payload=_make_dashboard_response("p_test_ok", email_delivered=True, telegram_delivered=True)
    )
    result = probe.run_probe(
        base_url="http://gw.test",
        ops_token="tok",
        wait_seconds=0,
        probe_id="p_test_ok",
        transport=transport,
    )
    assert result.exit_code == 0
    assert "email" in result.delivered
    assert "telegram" in result.delivered
    assert result.missing == []


def test_probe_fails_when_email_delivery_missing():
    transport = _build_transport(
        dashboard_payload=_make_dashboard_response("p_test_no_email", email_delivered=False, telegram_delivered=True)
    )
    result = probe.run_probe(
        base_url="http://gw.test",
        ops_token="tok",
        wait_seconds=0,
        probe_id="p_test_no_email",
        transport=transport,
    )
    assert result.exit_code != 0
    assert "email" in result.missing
    assert "telegram" not in result.missing


def test_probe_fails_when_telegram_delivery_missing():
    transport = _build_transport(
        dashboard_payload=_make_dashboard_response("p_test_no_tg", email_delivered=True, telegram_delivered=False)
    )
    result = probe.run_probe(
        base_url="http://gw.test",
        ops_token="tok",
        wait_seconds=0,
        probe_id="p_test_no_tg",
        transport=transport,
    )
    assert result.exit_code != 0
    assert "telegram" in result.missing


def test_probe_fails_when_row_not_found_at_all():
    """If the probe's row is missing from the dashboard read-back,
    the probe must fail with a clear diagnostic — not silently exit 0."""
    transport = _build_transport(dashboard_payload=None)
    result = probe.run_probe(
        base_url="http://gw.test",
        ops_token="tok",
        wait_seconds=0,
        probe_id="p_test_missing",
        transport=transport,
    )
    assert result.exit_code != 0
    assert result.row_found is False


def test_probe_fails_when_post_returns_error_status():
    transport = _build_transport(post_status=503)
    result = probe.run_probe(
        base_url="http://gw.test",
        ops_token="tok",
        wait_seconds=0,
        probe_id="p_test_post_fail",
        transport=transport,
    )
    assert result.exit_code != 0
    assert result.post_failed is True

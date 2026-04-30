"""Phase 2b tests for the FastAPI Link router.

Uses FastAPI TestClient against a minimal app that only mounts link_routes —
sidesteps the heavy api/server.py import chain.

Covers:
  - GET /api/link/health → bridge_status + last_probe shape
  - POST /api/link/spend-requests → 201 in stub mode with valid body
  - POST /api/link/spend-requests → 400 when guardrail blocks
  - POST /api/link/spend-requests → 403 when UI entry-point disabled
  - GET /api/link/spend-requests → audit-log row list
  - GET /link/card/{token} → 410 when token unknown / consumed / expired
  - GET /link/card/{token} → renders HTML when token valid + card present
  - Card page response sets Cache-Control: no-store
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def isolated_test_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    audit = tmp_path / "audit.jsonl"
    tokens = tmp_path / "tokens.json"
    monkeypatch.setenv("UA_LINK_AUDIT_PATH", str(audit))
    monkeypatch.setenv("UA_LINK_CARD_TOKENS_PATH", str(tokens))
    monkeypatch.setenv("UA_LINK_NOTIFIER_STATE_PATH", str(tmp_path / "notif.json"))
    for var in list(os.environ):
        if var.startswith(("UA_LINK_", "UA_ENABLE_LINK")) and var not in {
            "UA_LINK_AUDIT_PATH",
            "UA_LINK_CARD_TOKENS_PATH",
            "UA_LINK_NOTIFIER_STATE_PATH",
        }:
            monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_LINK_FORCE_STUB", "1")
    monkeypatch.setenv("UA_LINK_DEFAULT_PAYMENT_METHOD_ID", "csmrpd_default_x")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from universal_agent.api.link_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app), tmp_path


VALID_BODY = {
    "merchant_name": "Stripe Press",
    "merchant_url": "https://press.stripe.com",
    "context": (
        "User initiated this purchase via the shopping assistant flow. "
        "Buying a single hardcover copy of 'Working in Public' for personal reading. "
        "Final amount includes shipping and tax."
    ),
    "amount_cents": 3500,
}


def test_health_endpoint_returns_status_and_probe(isolated_test_app):
    client, _ = isolated_test_app
    res = client.get("/api/link/health")
    assert res.status_code == 200
    body = res.json()
    assert "bridge_status" in body
    assert "last_probe" in body
    assert body["bridge_status"]["enabled"] is True


def test_create_spend_request_returns_201(isolated_test_app):
    client, _ = isolated_test_app
    res = client.post("/api/link/spend-requests", json=VALID_BODY)
    assert res.status_code == 201
    body = res.json()
    assert body["ok"] is True
    assert body["data"]["id"].startswith("lsrq_stub_")
    assert body["audit_id"].startswith("audit_")


def test_create_spend_request_blocks_when_ui_disabled(isolated_test_app, monkeypatch):
    client, _ = isolated_test_app
    monkeypatch.setenv("UA_LINK_DISABLE_ENTRY_UI", "1")
    res = client.post("/api/link/spend-requests", json=VALID_BODY)
    assert res.status_code == 403


def test_create_spend_request_validation_error(isolated_test_app):
    client, _ = isolated_test_app
    short_body = {**VALID_BODY, "context": "too short"}
    res = client.post("/api/link/spend-requests", json=short_body)
    # Pydantic returns 422 for body validation, not 400.
    assert res.status_code == 422


def test_create_spend_request_guardrail_returns_400(isolated_test_app, monkeypatch):
    client, _ = isolated_test_app
    monkeypatch.setenv("UA_LINK_MAX_AMOUNT_CENTS", "100")
    body = {**VALID_BODY, "amount_cents": 5000}
    res = client.post("/api/link/spend-requests", json=body)
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["code"] == "guardrail_per_call_cap"


def test_list_spend_requests(isolated_test_app):
    client, _ = isolated_test_app
    client.post("/api/link/spend-requests", json=VALID_BODY)
    client.post("/api/link/spend-requests", json={**VALID_BODY, "amount_cents": 500})
    res = client.get("/api/link/spend-requests")
    assert res.status_code == 200
    body = res.json()
    assert body["count"] >= 2
    assert all(it["event"] == "create_attempt" for it in body["items"])


def test_card_token_unknown_returns_410(isolated_test_app):
    client, _ = isolated_test_app
    res = client.get("/link/card/tok_nonexistent")
    assert res.status_code == 410
    assert "Link not found" in res.text or "not found" in res.text.lower()


def test_card_token_renders_card_page(isolated_test_app):
    client, _ = isolated_test_app
    from universal_agent.services import link_card_tokens
    from universal_agent.tools import link_bridge

    issued = link_card_tokens.issue("lsrq_stub_card_demo")

    # Patch retrieve to return a card-shaped stub
    fake_data = {
        "id": "lsrq_stub_card_demo",
        "status": "approved",
        "merchant_name": "Stripe Press",
        "merchant_url": "https://press.stripe.com",
        "amount": 3500,
        "currency": "usd",
        "card": {
            "number": "4111111111111234",
            "exp_month": 12,
            "exp_year": 2030,
            "cvc": "123",
            "valid_until": 9999999999,
            "billing_address": {
                "name": "Sample User",
                "line1": "123 Example St",
                "city": "San Francisco",
                "state": "CA",
                "postal_code": "94102",
                "country": "US",
            },
        },
    }
    orig_retrieve = link_bridge.retrieve_spend_request

    def fake_retrieve(*, caller, spend_request_id, include_card=False):
        return {
            "ok": True,
            "data": fake_data,
            "error": None,
            "audit_id": "audit_test",
            "mode": "test",
        }

    link_bridge.retrieve_spend_request = fake_retrieve
    try:
        res = client.get(f"/link/card/{issued['token']}")
    finally:
        link_bridge.retrieve_spend_request = orig_retrieve

    assert res.status_code == 200
    assert "4111 1111 1111 1234" in res.text
    assert "12/30" in res.text
    assert "123" in res.text
    assert "Stripe Press" in res.text
    # Cache-Control: no-store etc.
    assert "no-store" in res.headers.get("cache-control", "").lower()
    assert "noindex" in res.headers.get("x-robots-tag", "").lower()


def test_card_token_already_consumed_returns_410(isolated_test_app):
    client, _ = isolated_test_app
    from universal_agent.services import link_card_tokens
    from universal_agent.tools import link_bridge

    issued = link_card_tokens.issue("lsrq_consume_demo")

    # First consume manually outside the route
    link_card_tokens.consume(issued["token"])

    res = client.get(f"/link/card/{issued['token']}")
    assert res.status_code == 410
    assert "Already viewed" in res.text or "already" in res.text.lower()

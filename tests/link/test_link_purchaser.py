"""Phase 4 tests for the agent-purchaser orchestration.

Covers:
  - Disabled when UA_ENABLE_LINK=0 → status='disabled'.
  - Disabled when UA_LINK_AUTO_CHECKOUT=0 → status='disabled'.
  - Idempotent: second attempt returns 'duplicate'.
  - Bridge retrieve fail → 'fallback_retrieve_failed'.
  - Spend request not approved → 'fallback_not_approved'.
  - Credential type SPT → 'fallback_not_card'.
  - Card not yet minted → 'fallback_no_card'.
  - Successful dispatch returns ok=True with status='completed'.
  - Dispatcher exceptions → 'fallback_error', not raised.
  - Card details (PAN/CVC) NEVER persist in attempts file.
  - Captcha budget tracking: budget records, remaining decrements.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from universal_agent.services import link_purchaser
from universal_agent.tools import link_bridge


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UA_LINK_CAPTCHA_USAGE_PATH", str(tmp_path / "captcha.jsonl"))
    monkeypatch.setenv("UA_LINK_PURCHASER_ATTEMPTS_PATH", str(tmp_path / "attempts.json"))
    monkeypatch.setenv("UA_LINK_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    for var in list(os.environ):
        if var.startswith(("UA_LINK_", "UA_ENABLE_LINK")) and var not in {
            "UA_LINK_CAPTCHA_USAGE_PATH",
            "UA_LINK_PURCHASER_ATTEMPTS_PATH",
            "UA_LINK_AUDIT_PATH",
        }:
            monkeypatch.delenv(var, raising=False)

    # Reset module-level dispatcher between tests
    link_purchaser._dispatcher = None
    return tmp_path


CARD = {
    "number": "4111111111111234",
    "exp_month": 12,
    "exp_year": 2030,
    "cvc": "987",
    "billing_address": {"name": "Sam User", "line1": "1 St", "city": "SF",
                         "state": "CA", "postal_code": "94102", "country": "US"},
    "valid_until": 9999999999,
    "last4": "1234",
}

APPROVED_DATA = {
    "id": "lsrq_001",
    "status": "approved",
    "merchant_name": "Stripe Press",
    "merchant_url": "https://press.stripe.com",
    "amount": 3500,
    "currency": "usd",
    "credential_type": "card",
    "card": CARD,
}


# ── Disabled paths ────────────────────────────────────────────────────────────


def test_disabled_when_link_off(isolated):
    res = link_purchaser.attempt_checkout("lsrq_001")
    assert res["ok"] is False
    assert res["status"] == "disabled"
    assert "UA_ENABLE_LINK" in res["reason"]


def test_disabled_when_auto_checkout_off(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_LINK_DISABLE_AUTO_CHECKOUT", "1")
    res = link_purchaser.attempt_checkout("lsrq_001")
    assert res["status"] == "disabled"
    assert "AUTO_CHECKOUT" in res["reason"]


# ── Idempotency ───────────────────────────────────────────────────────────────


def test_duplicate_attempt_blocked(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")

    monkeypatch.setattr(
        link_bridge, "retrieve_spend_request",
        lambda **kw: {"ok": True, "data": APPROVED_DATA, "error": None,
                       "audit_id": "x", "mode": "test"},
    )
    link_purchaser.set_dispatcher(lambda payload: {"ok": True, "status": "completed",
                                                     "evidence": "/tmp/screenshot.png"})

    first = link_purchaser.attempt_checkout("lsrq_001")
    assert first["ok"] is True
    assert first["status"] == "completed"

    second = link_purchaser.attempt_checkout("lsrq_001")
    assert second["status"] == "duplicate"
    assert second["first_attempt"]["status"] == "completed"


# ── Pre-dispatch fallbacks ────────────────────────────────────────────────────


def test_retrieve_failed(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setattr(
        link_bridge, "retrieve_spend_request",
        lambda **kw: {"ok": False, "error": {"code": "cli_timeout", "message": "x"},
                       "data": None, "audit_id": "x", "mode": "test"},
    )
    res = link_purchaser.attempt_checkout("lsrq_002")
    assert res["status"] == "fallback_retrieve_failed"


def test_not_approved(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setattr(
        link_bridge, "retrieve_spend_request",
        lambda **kw: {"ok": True,
                       "data": {**APPROVED_DATA, "status": "pending_approval"},
                       "error": None, "audit_id": "x", "mode": "test"},
    )
    res = link_purchaser.attempt_checkout("lsrq_003")
    assert res["status"] == "fallback_not_approved"
    assert res["spend_request_status"] == "pending_approval"


def test_credential_type_spt(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setattr(
        link_bridge, "retrieve_spend_request",
        lambda **kw: {"ok": True,
                       "data": {**APPROVED_DATA, "credential_type": "shared_payment_token"},
                       "error": None, "audit_id": "x", "mode": "test"},
    )
    res = link_purchaser.attempt_checkout("lsrq_spt")
    assert res["status"] == "fallback_not_card"


def test_card_not_minted(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setattr(
        link_bridge, "retrieve_spend_request",
        lambda **kw: {"ok": True,
                       "data": {**APPROVED_DATA, "card": {}},
                       "error": None, "audit_id": "x", "mode": "test"},
    )
    res = link_purchaser.attempt_checkout("lsrq_no_card")
    assert res["status"] == "fallback_no_card"


# ── Successful dispatch ───────────────────────────────────────────────────────


def test_dispatcher_completes_checkout(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    received_payloads: list[dict] = []

    def fake_dispatcher(payload):
        received_payloads.append(payload)
        return {"ok": True, "status": "completed", "evidence": "/tmp/proof.png"}

    monkeypatch.setattr(
        link_bridge, "retrieve_spend_request",
        lambda **kw: {"ok": True, "data": APPROVED_DATA, "error": None,
                       "audit_id": "x", "mode": "test"},
    )
    link_purchaser.set_dispatcher(fake_dispatcher)

    res = link_purchaser.attempt_checkout("lsrq_dispatch")
    assert res["ok"] is True
    assert res["status"] == "completed"
    assert res["evidence"] == "/tmp/proof.png"
    assert res["card_last4"] == "1234"
    # Dispatcher received card data fresh
    assert received_payloads[0]["card"]["number"] == CARD["number"]


def test_dispatcher_exception_falls_back(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setattr(
        link_bridge, "retrieve_spend_request",
        lambda **kw: {"ok": True, "data": APPROVED_DATA, "error": None,
                       "audit_id": "x", "mode": "test"},
    )

    def boom(payload):
        raise RuntimeError("playwright crashed")

    link_purchaser.set_dispatcher(boom)
    res = link_purchaser.attempt_checkout("lsrq_boom")
    assert res["ok"] is False
    assert res["status"] == "fallback_error"


def test_default_dispatcher_returns_no_dispatcher_status(isolated, monkeypatch):
    """Without set_dispatcher, attempts return fallback_no_dispatcher cleanly."""
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setattr(
        link_bridge, "retrieve_spend_request",
        lambda **kw: {"ok": True, "data": APPROVED_DATA, "error": None,
                       "audit_id": "x", "mode": "test"},
    )
    res = link_purchaser.attempt_checkout("lsrq_nd")
    assert res["status"] == "fallback_no_dispatcher"


# ── Card-data hygiene ────────────────────────────────────────────────────────


def test_attempts_file_never_contains_card_pan(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setattr(
        link_bridge, "retrieve_spend_request",
        lambda **kw: {"ok": True, "data": APPROVED_DATA, "error": None,
                       "audit_id": "x", "mode": "test"},
    )
    link_purchaser.set_dispatcher(
        lambda p: {"ok": True, "status": "completed", "evidence": "/tmp/x"}
    )
    link_purchaser.attempt_checkout("lsrq_no_pan")

    raw = link_purchaser.resolve_attempts_path().read_text()
    assert CARD["number"] not in raw
    assert CARD["cvc"] not in raw
    # last4 IS allowed (it's already in the audit log).
    assert "1234" in raw


# ── Captcha budget ───────────────────────────────────────────────────────────


def test_captcha_budget_default_snapshot(isolated):
    snap = link_purchaser.captcha_budget_snapshot()
    assert snap["cap"] == 20  # default
    assert snap["used"] == 0
    assert snap["remaining"] == 20


def test_captcha_budget_records_usage(isolated):
    link_purchaser.record_captcha_usage("lsrq_x", merchant_url="https://x.example")
    link_purchaser.record_captcha_usage("lsrq_y")
    snap = link_purchaser.captcha_budget_snapshot()
    assert snap["used"] == 2
    assert snap["remaining"] == 18


def test_captcha_budget_respects_cap_override(isolated, monkeypatch):
    monkeypatch.setenv("UA_LINK_DAILY_CAPTCHA_BUDGET", "5")
    snap = link_purchaser.captcha_budget_snapshot()
    assert snap["cap"] == 5


def test_captcha_budget_excludes_old_entries(isolated):
    """Usage entries older than 24h must not count."""
    path = link_purchaser.resolve_captcha_usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        fh.write(json.dumps({"ts": time.time() - (25 * 3600), "spend_request_id": "old"}) + "\n")
        fh.write(json.dumps({"ts": time.time() - 60, "spend_request_id": "new"}) + "\n")
    assert link_purchaser._read_captcha_usage_today() == 1


def test_captcha_budget_available_signal(isolated, monkeypatch):
    monkeypatch.setenv("UA_LINK_DAILY_CAPTCHA_BUDGET", "1")
    assert link_purchaser.captcha_budget_available() is True
    link_purchaser.record_captcha_usage("lsrq_x")
    assert link_purchaser.captcha_budget_available() is False

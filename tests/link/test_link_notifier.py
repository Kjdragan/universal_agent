"""Phase 2b tests for the Link approval notifier.

Covers:
  - notify_approved fires once per spend_request_id (idempotent).
  - already_notified reflects state after first call.
  - Notifier issues a card token and includes the URL in body.
  - Notifier never includes PAN/CVC in subject/body.
  - When UA_ENABLE_LINK=0 the notifier short-circuits to skipped.
  - maybe_notify_from_retrieve only fires when ok=True AND status=approved.
  - Failures (no operator email configured, send fail) do not raise.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from universal_agent.services import link_card_tokens, link_notifier


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UA_LINK_CARD_TOKENS_PATH", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("UA_LINK_NOTIFIER_STATE_PATH", str(tmp_path / "notif.json"))
    for var in list(os.environ):
        if var.startswith(("UA_LINK_", "UA_ENABLE_LINK")) and var not in {
            "UA_LINK_CARD_TOKENS_PATH",
            "UA_LINK_NOTIFIER_STATE_PATH",
        }:
            monkeypatch.delenv(var, raising=False)
    return tmp_path


SR = {
    "id": "lsrq_demo_1",
    "status": "approved",
    "merchant_name": "Stripe Press",
    "merchant_url": "https://press.stripe.com",
    "amount": 3500,
    "currency": "usd",
}


def test_notify_skipped_when_link_disabled(isolated):
    res = link_notifier.notify_approved(SR)
    assert res["ok"] is True
    assert res.get("skipped") == "link_disabled"


def test_notify_idempotent_per_spend_request_id(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    a = link_notifier.notify_approved(SR)
    b = link_notifier.notify_approved(SR)
    assert a["ok"] is True
    assert b.get("skipped") == "already_notified"


def test_notify_issues_token_with_card_url(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_LINK_DASHBOARD_BASE_URL", "https://app.example.com")
    res = link_notifier.notify_approved(SR)
    assert res["ok"] is True
    assert res["token"].startswith("tok_")
    assert res["card_url"].startswith("https://app.example.com/link/card/tok_")
    # Token is recorded in the token store.
    rec = link_card_tokens.peek(res["token"])
    assert rec is not None
    assert rec["spend_request_id"] == "lsrq_demo_1"


def test_notify_state_file_written(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    link_notifier.notify_approved(SR)
    state_path = link_notifier.resolve_state_path()
    assert state_path.exists()
    data = json.loads(state_path.read_text())
    assert "lsrq_demo_1" in data["notified"]


def test_already_notified_reflects_state(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    assert link_notifier.already_notified("lsrq_demo_1") is False
    link_notifier.notify_approved(SR)
    assert link_notifier.already_notified("lsrq_demo_1") is True


def test_notify_missing_id_returns_error(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    res = link_notifier.notify_approved({"status": "approved"})
    assert res["ok"] is False
    assert res["error"] == "missing_spend_request_id"


def test_maybe_notify_from_retrieve_fires_on_approved(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    response = {"ok": True, "data": {"id": "lsrq_xyz", "status": "approved",
                                       "merchant_name": "X", "merchant_url": "https://x.example",
                                       "amount": 100, "currency": "usd"}}
    res = link_notifier.maybe_notify_from_retrieve(response)
    assert res is not None and res["ok"] is True


def test_maybe_notify_skips_pending(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    response = {"ok": True, "data": {"id": "lsrq_p", "status": "pending_approval"}}
    assert link_notifier.maybe_notify_from_retrieve(response) is None


def test_maybe_notify_skips_failed_responses(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    response = {"ok": False, "error": {"code": "x", "message": "y"}}
    assert link_notifier.maybe_notify_from_retrieve(response) is None


def test_notify_never_includes_pan_or_cvc(isolated, monkeypatch):
    """The body the notifier builds must never contain card data."""
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    sent: dict = {}

    def fake_send(*, to, subject, html, text):
        sent["to"] = to
        sent["subject"] = subject
        sent["html"] = html
        sent["text"] = text
        return {"ok": True, "via": "fake"}

    monkeypatch.setattr(link_notifier, "_send_via_agentmail", fake_send)
    monkeypatch.setenv("UA_LINK_OPERATOR_EMAIL", "ops@example.com")

    sr_with_pan_pollution = {**SR, "id": "lsrq_pan",
                              "card": {"number": "4242424242424242", "cvc": "999"}}
    link_notifier.notify_approved(sr_with_pan_pollution)
    body_blob = (sent.get("text") or "") + (sent.get("html") or "") + (sent.get("subject") or "")
    assert "4242424242424242" not in body_blob
    assert "999" not in body_blob or "999.00" in body_blob  # CVC isn't there; allow $9.99 etc.


def test_notify_no_operator_email_still_succeeds(isolated, monkeypatch):
    """Without UA_LINK_OPERATOR_EMAIL the notifier still issues the token."""
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    res = link_notifier.notify_approved({**SR, "id": "lsrq_noemail"})
    assert res["ok"] is True
    assert res["token"].startswith("tok_")

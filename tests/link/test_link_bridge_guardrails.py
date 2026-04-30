"""Phase 1 tests for link_bridge guardrails (stub-mode only).

Covers:
  - master_switch (UA_ENABLE_LINK gate)
  - caller_allowlist
  - per_call_cap
  - daily_cap (rolling 24h sum from audit log)
  - merchant_allowlist (off by default, scoped when set)
  - validation: amount range, currency format, context length
  - guardrail evaluation order (master first, validation before caps, etc.)
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from universal_agent.tools import link_bridge


VALID_CONTEXT = (
    "User initiated this purchase via the shopping assistant flow. "
    "Buying a single hardcover copy of 'Working in Public' for personal reading. "
    "Final amount includes shipping and tax."
)


@pytest.fixture
def isolated_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect the audit log to a temp file and clear all UA_LINK_* env vars."""
    audit_file = tmp_path / "link_audit.jsonl"
    monkeypatch.setenv("UA_LINK_AUDIT_PATH", str(audit_file))
    for var in list(os.environ):
        if var.startswith(("UA_LINK_", "UA_ENABLE_LINK")):
            if var != "UA_LINK_AUDIT_PATH":
                monkeypatch.delenv(var, raising=False)
    return audit_file


@pytest.fixture
def link_enabled(monkeypatch: pytest.MonkeyPatch):
    """Turn on the master switch with stub-mode forced.

    These tests exercise guardrails/audit/validation logic, not the subprocess
    path. UA_LINK_FORCE_STUB=1 keeps `_bridge_mode()` returning 'stub' so the
    bridge never shells out to link-cli.
    """
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_LINK_FORCE_STUB", "1")


# ── master_switch ─────────────────────────────────────────────────────────────


def test_master_switch_off_blocks_create(isolated_audit):
    result = link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=3500,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "guardrail_disabled"
    assert result["mode"] == "stub"


def test_master_switch_off_blocks_retrieve(isolated_audit):
    result = link_bridge.retrieve_spend_request(caller="test", spend_request_id="lsrq_x")
    assert result["ok"] is False
    assert result["error"]["code"] == "guardrail_disabled"


def test_master_switch_off_blocks_list_pms(isolated_audit):
    result = link_bridge.list_payment_methods(caller="test")
    assert result["ok"] is False
    assert result["error"]["code"] == "guardrail_disabled"


def test_master_switch_off_blocks_mpp_pay(isolated_audit):
    result = link_bridge.mpp_pay(
        caller="test",
        spend_request_id="lsrq_x",
        url="https://example.com/pay",
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "guardrail_disabled"


# ── caller_allowlist ──────────────────────────────────────────────────────────


def test_caller_must_be_in_allowlist(isolated_audit, link_enabled):
    result = link_bridge.create_spend_request(
        caller="proactive_advisor",  # not allowed
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=3500,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "guardrail_caller"


def test_caller_empty_string_rejected(isolated_audit, link_enabled):
    result = link_bridge.create_spend_request(
        caller="",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=3500,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "guardrail_caller"


@pytest.mark.parametrize("caller", list(link_bridge.ALLOWED_CALLERS))
def test_all_allowlist_callers_pass(isolated_audit, link_enabled, caller):
    result = link_bridge.create_spend_request(
        caller=caller,
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=3500,
    )
    assert result["ok"] is True
    assert result["data"]["id"].startswith("lsrq_stub_")


# ── validation ────────────────────────────────────────────────────────────────


def test_context_under_100_chars_rejected(isolated_audit, link_enabled):
    result = link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context="Too short.",
        amount_cents=3500,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "validation_context"


def test_amount_zero_rejected(isolated_audit, link_enabled):
    result = link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=0,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "validation_amount"


def test_amount_over_link_max_rejected(isolated_audit, link_enabled):
    # Link API ceiling is 50000. Even if our local cap is set higher, this fails.
    os.environ["UA_LINK_MAX_AMOUNT_CENTS"] = "100000"
    try:
        result = link_bridge.create_spend_request(
            caller="test",
            payment_method_id="csmrpd_x",
            merchant_name="Stripe Press",
            merchant_url="https://press.stripe.com",
            context=VALID_CONTEXT,
            amount_cents=60000,
        )
    finally:
        del os.environ["UA_LINK_MAX_AMOUNT_CENTS"]
    assert result["ok"] is False
    assert result["error"]["code"] == "validation_amount"


def test_currency_must_be_three_letter_iso(isolated_audit, link_enabled):
    result = link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=3500,
        currency="dollars",
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "validation_currency"


# ── per_call_cap ──────────────────────────────────────────────────────────────


def test_per_call_cap_blocks_above_threshold(isolated_audit, link_enabled, monkeypatch):
    monkeypatch.setenv("UA_LINK_MAX_AMOUNT_CENTS", "1000")
    result = link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=2000,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "guardrail_per_call_cap"


def test_per_call_cap_allows_at_threshold(isolated_audit, link_enabled, monkeypatch):
    monkeypatch.setenv("UA_LINK_MAX_AMOUNT_CENTS", "2000")
    result = link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=2000,
    )
    assert result["ok"] is True


# ── daily_cap ─────────────────────────────────────────────────────────────────


def test_daily_cap_sums_today_attempts(isolated_audit, link_enabled, monkeypatch):
    monkeypatch.setenv("UA_LINK_MAX_AMOUNT_CENTS", "10000")
    monkeypatch.setenv("UA_LINK_DAILY_BUDGET_CENTS", "5000")

    # Two attempts of 2000 each — both pass per-call but second pushes daily over.
    first = link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=2000,
    )
    assert first["ok"] is True

    second = link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=2000,
    )
    assert second["ok"] is True

    third = link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=2000,
    )
    assert third["ok"] is False
    assert third["error"]["code"] == "guardrail_daily_cap"


def test_daily_cap_does_not_count_blocked_attempts(
    isolated_audit, link_enabled, monkeypatch
):
    """Guardrail-blocked attempts must not count toward the daily budget."""
    monkeypatch.setenv("UA_LINK_MAX_AMOUNT_CENTS", "10000")
    monkeypatch.setenv("UA_LINK_DAILY_BUDGET_CENTS", "5000")

    # Blocked attempt (bad caller) — must not consume budget.
    blocked = link_bridge.create_spend_request(
        caller="not_in_allowlist",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=4000,
    )
    assert blocked["ok"] is False

    # Now a legit attempt at 4500 should succeed (only 0¢ counted prior).
    ok = link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=4500,
    )
    assert ok["ok"] is True


# ── merchant_allowlist ────────────────────────────────────────────────────────


def test_merchant_allowlist_off_by_default(isolated_audit, link_enabled):
    result = link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Random Shop",
        merchant_url="https://random-shop.example",
        context=VALID_CONTEXT,
        amount_cents=3500,
    )
    assert result["ok"] is True


def test_merchant_allowlist_blocks_when_set(isolated_audit, link_enabled, monkeypatch):
    monkeypatch.setenv("UA_LINK_MERCHANT_ALLOWLIST", "press.stripe.com,powdur.com")
    result = link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Random Shop",
        merchant_url="https://random-shop.example",
        context=VALID_CONTEXT,
        amount_cents=3500,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "guardrail_merchant_allowlist"


def test_merchant_allowlist_subdomain_match(isolated_audit, link_enabled, monkeypatch):
    monkeypatch.setenv("UA_LINK_MERCHANT_ALLOWLIST", "stripe.com")
    result = link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com/working-in-public",
        context=VALID_CONTEXT,
        amount_cents=3500,
    )
    assert result["ok"] is True


def test_merchant_allowlist_exact_match(isolated_audit, link_enabled, monkeypatch):
    monkeypatch.setenv("UA_LINK_MERCHANT_ALLOWLIST", "press.stripe.com")
    result = link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=3500,
    )
    assert result["ok"] is True


# ── happy path / response shape ───────────────────────────────────────────────


def test_create_returns_stub_payload_with_audit_id(isolated_audit, link_enabled):
    result = link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=3500,
    )
    assert result["ok"] is True
    assert result["data"]["_stub"] is True
    assert result["data"]["id"].startswith("lsrq_stub_")
    assert result["audit_id"].startswith("audit_")
    assert result["mode"] == "test"  # link_enabled fixture activates non-live test mode
    assert result["error"] is None


def test_retrieve_with_include_card_returns_stub_card(isolated_audit, link_enabled):
    result = link_bridge.retrieve_spend_request(
        caller="test",
        spend_request_id="lsrq_stub_abc",
        include_card=True,
    )
    assert result["ok"] is True
    assert result["data"]["card"]["last4"] == "4242"
    # PAN/CVC must NEVER appear in the stub or any response.
    assert "number" not in result["data"]["card"]
    assert "cvc" not in result["data"]["card"]


def test_list_payment_methods_returns_stub_list(isolated_audit, link_enabled):
    result = link_bridge.list_payment_methods(caller="test")
    assert result["ok"] is True
    assert len(result["data"]["payment_methods"]) == 1
    assert result["data"]["payment_methods"][0]["last4"] == "4242"


def test_mpp_pay_stub_returns_success(isolated_audit, link_enabled):
    result = link_bridge.mpp_pay(
        caller="test",
        spend_request_id="lsrq_stub_abc",
        url="https://climate.stripe.dev/api/contribute",
        method="POST",
        data={"amount": 100},
    )
    assert result["ok"] is True
    assert result["data"]["response"]["status"] == 200


# ── bridge_status ─────────────────────────────────────────────────────────────


def test_bridge_status_reports_stub_when_disabled(isolated_audit):
    status = link_bridge.bridge_status()
    assert status["enabled"] is False
    assert status["live_mode"] is False
    assert status["mode"] == "stub"


def test_bridge_status_reports_test_when_enabled(isolated_audit, link_enabled):
    status = link_bridge.bridge_status()
    assert status["enabled"] is True
    assert status["live_mode"] is False
    assert status["test_mode"] is True
    assert status["mode"] == "test"


def test_bridge_status_live_requires_both_gates(isolated_audit, monkeypatch):
    # Master on but live gate not set → still test mode.
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_ENABLE_LINK_LIVE", "1")
    # UA_LINK_TEST_MODE not set explicitly → defaults to test on (test_mode_on True).
    status = link_bridge.bridge_status()
    assert status["live_mode"] is False
    assert status["mode"] == "test"

    # Now flip test mode off — live becomes active.
    monkeypatch.setenv("UA_LINK_TEST_MODE", "0")
    status = link_bridge.bridge_status()
    assert status["live_mode"] is True
    assert status["mode"] == "live"

    # Drop the live gate — back to test even with TEST_MODE=0.
    monkeypatch.delenv("UA_ENABLE_LINK_LIVE")
    status = link_bridge.bridge_status()
    assert status["live_mode"] is False
    assert status["mode"] == "test"

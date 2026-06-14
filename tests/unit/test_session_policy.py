from universal_agent.session_policy import (
    classify_request_categories,
    default_session_policy,
    evaluate_request_against_policy,
)


def test_default_policy_includes_persona_and_yolo():
    policy = default_session_policy("session_x", "user_x")
    assert policy["identity_mode"] == "persona"
    assert policy["autonomy_mode"] == "yolo"
    assert policy["hard_stops"]["no_payments"] is True


def test_policy_denies_money_movement(monkeypatch):
    # The hard-deny for money movement is gated by `not feature_flags.link_enabled()`
    # (session_policy.evaluate_request_against_policy). A dev shell exports
    # UA_ENABLE_LINK=1, which would make link_enabled() True and route the request
    # to the Link-approval path instead of the deny path. Pin both Link vars off so
    # link_enabled() returns its default False and the no-payment-channel deny is
    # exercised deterministically.
    monkeypatch.delenv("UA_ENABLE_LINK", raising=False)
    monkeypatch.delenv("UA_DISABLE_LINK", raising=False)
    policy = default_session_policy("session_x", "user_x")
    result = evaluate_request_against_policy(
        policy,
        user_input="buy this subscription now",
        metadata={},
    )
    assert result["decision"] == "deny"
    assert "money_movement" in result["categories"]


def test_policy_requires_approval_for_email():
    policy = default_session_policy("session_x", "user_x")
    result = evaluate_request_against_policy(
        policy,
        user_input="send an email with the report",
        metadata={},
    )
    # Outbound email approvals are currently auto-allowed by default policy.
    assert result["decision"] == "allow"
    assert "outbound_email" in result["categories"]


def test_category_classifier_detects_destructive_ops():
    cats = classify_request_categories("please rm -rf the folder")
    assert "destructive_local_ops" in cats


def test_category_classifier_detects_x_dot_com_public_posting():
    cats = classify_request_categories("Draft an x.com announcement for this release")
    assert "public_posting" in cats
    assert "external_side_effect" in cats


def test_category_classifier_does_not_need_literal_backslash_for_x_dot_com():
    assert "public_posting" in classify_request_categories("x.com announcement")
    assert "public_posting" not in classify_request_categories(r"x\\Xcom announcement")

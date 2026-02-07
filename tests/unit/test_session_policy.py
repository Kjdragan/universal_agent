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


def test_policy_denies_money_movement():
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
    assert result["decision"] == "require_approval"
    assert "outbound_email" in result["categories"]


def test_category_classifier_detects_destructive_ops():
    cats = classify_request_categories("please rm -rf the folder")
    assert "destructive_local_ops" in cats

from universal_agent.identity.registry import (
    clear_identity_registry_cache,
    resolve_email_recipients,
    validate_recipient_policy,
)


def _reset_env(monkeypatch) -> None:
    monkeypatch.delenv("UA_PRIMARY_EMAIL", raising=False)
    monkeypatch.delenv("UA_SECONDARY_EMAILS", raising=False)
    monkeypatch.delenv("UA_EMAIL_ALIASES", raising=False)
    monkeypatch.delenv("UA_IDENTITY_REGISTRY_PATH", raising=False)
    clear_identity_registry_cache()


def test_resolve_me_direct(monkeypatch):
    _reset_env(monkeypatch)
    monkeypatch.setenv("UA_PRIMARY_EMAIL", "kevin.dragan@outlook.com")
    clear_identity_registry_cache()

    tool_input = {"to": "me", "subject": "hello", "body": "test"}
    updated, errors, replacements = resolve_email_recipients(
        "GMAIL_SEND_EMAIL", tool_input
    )

    assert not errors
    assert updated["to"] == "kevin.dragan@outlook.com"
    assert ("me", "kevin.dragan@outlook.com") in replacements


def test_resolve_me_in_multi_execute(monkeypatch):
    _reset_env(monkeypatch)
    monkeypatch.setenv("UA_PRIMARY_EMAIL", "kevin.dragan@outlook.com")
    clear_identity_registry_cache()

    tool_input = {
        "tools": [
            {
                "tool_slug": "GMAIL_SEND_EMAIL",
                "arguments": {"recipient_email": "me", "subject": "x", "body": "y"},
            }
        ]
    }
    updated, errors, _ = resolve_email_recipients(
        "COMPOSIO_MULTI_EXECUTE_TOOL", tool_input
    )

    assert not errors
    assert (
        updated["tools"][0]["arguments"]["recipient_email"]
        == "kevin.dragan@outlook.com"
    )


def test_unresolved_alias(monkeypatch):
    _reset_env(monkeypatch)

    tool_input = {"to": "me", "subject": "hello", "body": "test"}
    updated, errors, replacements = resolve_email_recipients(
        "GMAIL_SEND_EMAIL", tool_input
    )

    assert updated is None
    assert errors == ["me"]
    assert replacements == []


def test_recipient_policy_enforced(monkeypatch):
    _reset_env(monkeypatch)
    monkeypatch.setenv("UA_PRIMARY_EMAIL", "kevin.dragan@outlook.com")
    monkeypatch.setenv("UA_ENFORCE_IDENTITY_RECIPIENTS", "1")
    clear_identity_registry_cache()

    tool_input = {
        "recipient_email": "kevinjdragan@gmail.com",
        "subject": "hello",
        "body": "test",
    }
    invalid = validate_recipient_policy(
        "GMAIL_SEND_EMAIL", tool_input, "email me at kevin.dragan@outlook.com"
    )

    assert invalid == ["kevinjdragan@gmail.com"]


def test_recipient_policy_allows_query_email(monkeypatch):
    _reset_env(monkeypatch)
    monkeypatch.setenv("UA_ENFORCE_IDENTITY_RECIPIENTS", "1")
    clear_identity_registry_cache()

    tool_input = {
        "recipient_email": "person@example.com",
        "subject": "hello",
        "body": "test",
    }
    invalid = validate_recipient_policy(
        "GMAIL_SEND_EMAIL", tool_input, "email person@example.com the report"
    )

    assert invalid == []

from __future__ import annotations

from universal_agent import feature_flags


def test_sdk_flags_default_to_disabled(monkeypatch):
    monkeypatch.delenv("UA_ENABLE_SDK_TYPED_TASK_EVENTS", raising=False)
    monkeypatch.delenv("UA_DISABLE_SDK_TYPED_TASK_EVENTS", raising=False)
    monkeypatch.delenv("UA_ENABLE_SDK_SESSION_HISTORY", raising=False)
    monkeypatch.delenv("UA_DISABLE_SDK_SESSION_HISTORY", raising=False)
    monkeypatch.delenv("UA_ENABLE_DYNAMIC_MCP", raising=False)
    monkeypatch.delenv("UA_DISABLE_DYNAMIC_MCP", raising=False)

    assert feature_flags.sdk_typed_task_events_enabled(default=False) is False
    assert feature_flags.sdk_session_history_enabled(default=False) is False
    assert feature_flags.dynamic_mcp_enabled(default=False) is False


def test_sdk_flags_enable_when_requested(monkeypatch):
    monkeypatch.setenv("UA_ENABLE_SDK_TYPED_TASK_EVENTS", "1")
    monkeypatch.setenv("UA_ENABLE_SDK_SESSION_HISTORY", "true")
    monkeypatch.setenv("UA_ENABLE_DYNAMIC_MCP", "yes")

    assert feature_flags.sdk_typed_task_events_enabled(default=False) is True
    assert feature_flags.sdk_session_history_enabled(default=False) is True
    assert feature_flags.dynamic_mcp_enabled(default=False) is True


def test_sdk_flags_disable_overrides_enable(monkeypatch):
    monkeypatch.setenv("UA_ENABLE_SDK_TYPED_TASK_EVENTS", "1")
    monkeypatch.setenv("UA_DISABLE_SDK_TYPED_TASK_EVENTS", "1")
    monkeypatch.setenv("UA_ENABLE_SDK_SESSION_HISTORY", "1")
    monkeypatch.setenv("UA_DISABLE_SDK_SESSION_HISTORY", "1")
    monkeypatch.setenv("UA_ENABLE_DYNAMIC_MCP", "1")
    monkeypatch.setenv("UA_DISABLE_DYNAMIC_MCP", "1")

    assert feature_flags.sdk_typed_task_events_enabled(default=False) is False
    assert feature_flags.sdk_session_history_enabled(default=False) is False
    assert feature_flags.dynamic_mcp_enabled(default=False) is False

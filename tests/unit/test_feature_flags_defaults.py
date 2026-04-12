from __future__ import annotations


def test_heartbeat_enabled_defaults_on(monkeypatch):
    from universal_agent.feature_flags import heartbeat_enabled

    monkeypatch.delenv("UA_ENABLE_HEARTBEAT", raising=False)
    monkeypatch.delenv("UA_DISABLE_HEARTBEAT", raising=False)

    assert heartbeat_enabled() is True


def test_cron_enabled_defaults_on(monkeypatch):
    from universal_agent.feature_flags import cron_enabled

    monkeypatch.delenv("UA_ENABLE_CRON", raising=False)
    monkeypatch.delenv("UA_DISABLE_CRON", raising=False)

    assert cron_enabled() is True


def test_disable_switches_still_win(monkeypatch):
    from universal_agent.feature_flags import cron_enabled, heartbeat_enabled

    monkeypatch.setenv("UA_DISABLE_HEARTBEAT", "1")
    monkeypatch.setenv("UA_DISABLE_CRON", "1")

    assert heartbeat_enabled() is False
    assert cron_enabled() is False

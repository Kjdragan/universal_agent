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


def test_task_hub_missions_defaults_off(monkeypatch):
    from universal_agent.feature_flags import task_hub_missions_enabled

    monkeypatch.delenv("UA_TASK_HUB_MISSIONS_ENABLED", raising=False)
    monkeypatch.delenv("UA_DISABLE_TASK_HUB_MISSIONS", raising=False)

    assert task_hub_missions_enabled() is False


def test_task_hub_missions_enable_and_disable(monkeypatch):
    from universal_agent.feature_flags import task_hub_missions_enabled

    monkeypatch.setenv("UA_TASK_HUB_MISSIONS_ENABLED", "1")
    assert task_hub_missions_enabled() is True

    monkeypatch.setenv("UA_DISABLE_TASK_HUB_MISSIONS", "1")
    assert task_hub_missions_enabled() is False

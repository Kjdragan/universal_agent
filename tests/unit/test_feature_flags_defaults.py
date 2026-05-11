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


# ─── Dev-mode defaults (PR #200 follow-up: loop_control integration) ─────


def test_heartbeat_enabled_defaults_off_in_dev(monkeypatch):
    """In ``UA_RUNTIME_STAGE=development``, heartbeat defaults OFF."""
    from universal_agent.feature_flags import heartbeat_enabled

    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.delenv("UA_ENABLE_HEARTBEAT", raising=False)
    monkeypatch.delenv("UA_DISABLE_HEARTBEAT", raising=False)
    monkeypatch.delenv("UA_HEARTBEAT_ENABLED", raising=False)

    assert heartbeat_enabled() is False


def test_cron_enabled_defaults_off_in_dev(monkeypatch):
    """In ``UA_RUNTIME_STAGE=development``, cron defaults OFF."""
    from universal_agent.feature_flags import cron_enabled

    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.delenv("UA_ENABLE_CRON", raising=False)
    monkeypatch.delenv("UA_DISABLE_CRON", raising=False)
    monkeypatch.delenv("UA_CRON_ENABLED", raising=False)

    assert cron_enabled() is False


def test_heartbeat_legacy_enable_is_IGNORED_in_dev(monkeypatch):
    """Phase D (2026-05-11): ``UA_ENABLE_HEARTBEAT=1`` is treated as Infisical
    prod-parity pollution in dev and IGNORED. Use ``UA_DEV_HEARTBEAT_FORCE_ON=1``
    to opt in instead."""
    from universal_agent.feature_flags import heartbeat_enabled

    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.setenv("UA_ENABLE_HEARTBEAT", "1")
    monkeypatch.delenv("UA_DEV_HEARTBEAT_FORCE_ON", raising=False)

    assert heartbeat_enabled() is False


def test_cron_legacy_enable_is_IGNORED_in_dev(monkeypatch):
    """Phase D: ``UA_ENABLE_CRON=1`` is ignored in dev (Infisical pollution)."""
    from universal_agent.feature_flags import cron_enabled

    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.setenv("UA_ENABLE_CRON", "1")
    monkeypatch.delenv("UA_DEV_CRON_FORCE_ON", raising=False)

    assert cron_enabled() is False


def test_heartbeat_modern_flag_is_IGNORED_in_dev(monkeypatch):
    """Phase D: ``UA_HEARTBEAT_ENABLED=1`` is also ignored in dev."""
    from universal_agent.feature_flags import heartbeat_enabled

    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.delenv("UA_ENABLE_HEARTBEAT", raising=False)
    monkeypatch.delenv("UA_DISABLE_HEARTBEAT", raising=False)
    monkeypatch.setenv("UA_HEARTBEAT_ENABLED", "1")
    monkeypatch.delenv("UA_DEV_HEARTBEAT_FORCE_ON", raising=False)

    assert heartbeat_enabled() is False


def test_heartbeat_dev_force_on_opts_in(monkeypatch):
    """Phase D: ``UA_DEV_HEARTBEAT_FORCE_ON=1`` is the canonical dev opt-in."""
    from universal_agent.feature_flags import heartbeat_enabled

    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.delenv("UA_HEARTBEAT_ENABLED", raising=False)
    monkeypatch.delenv("UA_ENABLE_HEARTBEAT", raising=False)
    monkeypatch.delenv("UA_DISABLE_HEARTBEAT", raising=False)
    monkeypatch.setenv("UA_DEV_HEARTBEAT_FORCE_ON", "1")

    assert heartbeat_enabled() is True


def test_cron_dev_force_on_opts_in(monkeypatch):
    """Phase D: ``UA_DEV_CRON_FORCE_ON=1`` opts the cron service into dev."""
    from universal_agent.feature_flags import cron_enabled

    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.delenv("UA_CRON_ENABLED", raising=False)
    monkeypatch.delenv("UA_ENABLE_CRON", raising=False)
    monkeypatch.delenv("UA_DISABLE_CRON", raising=False)
    monkeypatch.setenv("UA_DEV_CRON_FORCE_ON", "1")

    assert cron_enabled() is True


def test_heartbeat_legacy_enable_still_wins_in_prod(monkeypatch):
    """Production behavior unchanged: legacy ``UA_ENABLE_HEARTBEAT=1`` still works."""
    from universal_agent.feature_flags import heartbeat_enabled

    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    monkeypatch.delenv("UA_DISABLE_HEARTBEAT", raising=False)
    monkeypatch.setenv("UA_ENABLE_HEARTBEAT", "1")

    assert heartbeat_enabled() is True


def test_heartbeat_explicit_off_in_prod(monkeypatch):
    """``UA_HEARTBEAT_ENABLED=0`` turns heartbeat OFF even in production."""
    from universal_agent.feature_flags import heartbeat_enabled

    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    monkeypatch.delenv("UA_ENABLE_HEARTBEAT", raising=False)
    monkeypatch.delenv("UA_DISABLE_HEARTBEAT", raising=False)
    monkeypatch.setenv("UA_HEARTBEAT_ENABLED", "0")

    assert heartbeat_enabled() is False

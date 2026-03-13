import os

from universal_agent.runtime_role import (
    FactoryRole,
    build_factory_runtime_policy,
    normalize_llm_provider_override,
    resolve_factory_role,
    resolve_machine_slug,
    resolve_runtime_stage,
)


def test_unknown_factory_role_falls_back_to_local_worker(monkeypatch):
    monkeypatch.setenv("FACTORY_ROLE", "not_a_role")
    assert resolve_factory_role() is FactoryRole.LOCAL_WORKER


def test_runtime_policy_matrix(monkeypatch):
    monkeypatch.setenv("FACTORY_ROLE", "HEADQUARTERS")
    hq = build_factory_runtime_policy()
    assert hq.gateway_mode == "full"
    assert hq.can_publish_delegations is True

    monkeypatch.setenv("FACTORY_ROLE", "LOCAL_WORKER")
    worker = build_factory_runtime_policy()
    assert worker.gateway_mode == "health_only"
    assert worker.can_publish_delegations is False
    assert worker.can_listen_delegations is True

    monkeypatch.setenv("FACTORY_ROLE", "STANDALONE_NODE")
    standalone = build_factory_runtime_policy()
    assert standalone.gateway_mode == "full"
    assert standalone.can_publish_delegations is False
    assert standalone.can_listen_delegations is False


def test_llm_provider_override_validation(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_OVERRIDE", "openai")
    assert normalize_llm_provider_override() == "OPENAI"

    monkeypatch.setenv("LLM_PROVIDER_OVERRIDE", "invalid-provider")
    assert normalize_llm_provider_override() is None
    assert os.getenv("LLM_PROVIDER_OVERRIDE") in {"", None}


def test_capability_overrides(monkeypatch):
    monkeypatch.setenv("FACTORY_ROLE", "LOCAL_WORKER")
    # Base local worker has start_ui=False, delegation_mode="listen_only"
    
    # Apply overrides
    monkeypatch.setenv("UA_CAPABILITY_START_UI", "true")
    monkeypatch.setenv("UA_CAPABILITY_DELEGATION_MODE", "publish_and_listen")
    
    worker_with_overrides = build_factory_runtime_policy()
    
    assert worker_with_overrides.start_ui is True
    assert worker_with_overrides.delegation_mode == "publish_and_listen"
    assert worker_with_overrides.can_publish_delegations is True
    assert worker_with_overrides.gateway_mode == "health_only"  # Should remain unchanged


def test_runtime_stage_and_machine_slug_helpers(monkeypatch):
    monkeypatch.setenv("UA_RUNTIME_STAGE", "staging")
    monkeypatch.setenv("UA_MACHINE_SLUG", "kevins-desktop")

    assert resolve_runtime_stage() == "staging"
    assert resolve_machine_slug() == "kevins-desktop"

import os

from universal_agent.runtime_role import (
    FactoryRole,
    build_factory_runtime_policy,
    normalize_llm_provider_override,
    resolve_factory_role,
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

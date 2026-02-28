import logging

import pytest

from universal_agent import infisical_loader


@pytest.fixture(autouse=True)
def _reset_loader_cache(monkeypatch):
    monkeypatch.setattr(infisical_loader, "_BOOTSTRAP_RESULT", None)


def test_initialize_runtime_secrets_strict_mode_missing_machine_identity_raises(monkeypatch):
    monkeypatch.setenv("UA_DEPLOYMENT_PROFILE", "vps")
    monkeypatch.setenv("UA_INFISICAL_ENABLED", "1")
    monkeypatch.delenv("INFISICAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("INFISICAL_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("INFISICAL_PROJECT_ID", raising=False)

    with pytest.raises(RuntimeError, match="Infisical bootstrap is required"):
        infisical_loader.initialize_runtime_secrets(force_reload=True)


def test_initialize_runtime_secrets_strict_mode_fetch_failure_raises(monkeypatch):
    monkeypatch.setenv("UA_DEPLOYMENT_PROFILE", "standalone_node")
    monkeypatch.setenv("UA_INFISICAL_ENABLED", "1")
    monkeypatch.setenv("INFISICAL_CLIENT_ID", "client")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "project")
    monkeypatch.setattr(
        infisical_loader,
        "_fetch_infisical_secrets",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="Infisical bootstrap is required"):
        infisical_loader.initialize_runtime_secrets(force_reload=True)


def test_initialize_runtime_secrets_local_profile_falls_back_to_dotenv(monkeypatch):
    monkeypatch.setenv("UA_DEPLOYMENT_PROFILE", "local_workstation")
    monkeypatch.setenv("UA_INFISICAL_ENABLED", "1")
    monkeypatch.setenv("UA_INFISICAL_ALLOW_DOTENV_FALLBACK", "1")
    monkeypatch.setenv("INFISICAL_CLIENT_ID", "client")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "project")
    monkeypatch.setattr(
        infisical_loader,
        "_fetch_infisical_secrets",
        lambda: (_ for _ in ()).throw(RuntimeError("network down")),
    )
    monkeypatch.setattr(infisical_loader, "_load_local_dotenv", lambda: 3)

    result = infisical_loader.initialize_runtime_secrets(force_reload=True)
    assert result.ok is True
    assert result.strict_mode is False
    assert result.source == "dotenv"
    assert result.loaded_count == 3
    assert result.fallback_used is True
    assert result.errors


def test_initialize_runtime_secrets_does_not_log_sensitive_values(monkeypatch, caplog):
    secret_marker = "super-secret-value"
    monkeypatch.setenv("UA_DEPLOYMENT_PROFILE", "local_workstation")
    monkeypatch.setenv("UA_INFISICAL_ENABLED", "1")
    monkeypatch.setenv("UA_INFISICAL_ALLOW_DOTENV_FALLBACK", "0")
    monkeypatch.setenv("INFISICAL_CLIENT_ID", "client")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "project")
    monkeypatch.setattr(
        infisical_loader,
        "_fetch_infisical_secrets",
        lambda: (_ for _ in ()).throw(RuntimeError(f"token leaked: {secret_marker}")),
    )

    caplog.set_level(logging.WARNING)
    result = infisical_loader.initialize_runtime_secrets(force_reload=True)

    assert result.ok is True
    assert all(secret_marker not in err for err in result.errors)
    assert secret_marker not in caplog.text

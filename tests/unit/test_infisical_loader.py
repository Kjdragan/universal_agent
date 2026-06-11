import builtins
import logging

import pytest

from universal_agent import infisical_loader


@pytest.fixture(autouse=True)
def _reset_loader_cache(monkeypatch):
    monkeypatch.setattr(infisical_loader, "_BOOTSTRAP_RESULT", None)
    monkeypatch.setenv("UA_DOTENV_PATH", "/tmp/ua-test-missing.env")
    for key in [
        "INFISICAL_CLIENT_ID",
        "INFISICAL_CLIENT_SECRET",
        "INFISICAL_PROJECT_ID",
        "INFISICAL_API_URL",
        "INFISICAL_ENVIRONMENT",
        "INFISICAL_SECRET_PATH",
        "UA_RUNTIME_STAGE",
        "UA_MACHINE_SLUG",
        "FACTORY_ROLE",
        "UA_OPS_TOKEN",
        "UA_INFISICAL_ENABLED",
        "UA_INFISICAL_STRICT",
        "UA_INFISICAL_ALLOW_DOTENV_FALLBACK",
        "UA_DEPLOYMENT_PROFILE",
    ]:
        monkeypatch.delenv(key, raising=False)


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
    monkeypatch.setattr(infisical_loader, "_load_local_dotenv", lambda **_: 3)

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


def test_fetch_infisical_secrets_uses_rest_fallback_when_sdk_unavailable(monkeypatch):
    monkeypatch.setenv("INFISICAL_CLIENT_ID", "client")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "project")
    monkeypatch.setenv("INFISICAL_ENVIRONMENT", "dev")
    monkeypatch.setenv("INFISICAL_SECRET_PATH", "/")

    called = {"rest": False}

    def _fake_rest(**kwargs):
        called["rest"] = True
        assert kwargs["client_id"] == "client"
        assert kwargs["project_id"] == "project"
        return {"LOG_LEVEL": "INFO"}

    monkeypatch.setattr(infisical_loader, "_fetch_infisical_secrets_via_rest", _fake_rest)

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "infisical_client":
            raise ModuleNotFoundError("No module named 'infisical_client'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    values = infisical_loader._fetch_infisical_secrets()
    assert called["rest"] is True
    assert values["LOG_LEVEL"] == "INFO"


def test_initialize_runtime_secrets_normalizes_legacy_environment_aliases(monkeypatch):
    monkeypatch.setenv("UA_DEPLOYMENT_PROFILE", "local_workstation")
    monkeypatch.setenv("UA_INFISICAL_ENABLED", "0")
    monkeypatch.setenv("INFISICAL_ENVIRONMENT", "dev")
    monkeypatch.delenv("UA_RUNTIME_STAGE", raising=False)

    result = infisical_loader.initialize_runtime_secrets(force_reload=True)

    assert result.environment == "development"
    assert result.runtime_stage == "development"
    assert result.deployment_profile == "local_workstation"
    assert infisical_loader.os.getenv("INFISICAL_ENVIRONMENT") == "development"


def test_initialize_runtime_secrets_rejects_invalid_runtime_stage(monkeypatch):
    monkeypatch.setenv("UA_DEPLOYMENT_PROFILE", "local_workstation")
    monkeypatch.setenv("UA_INFISICAL_ENABLED", "0")
    monkeypatch.setenv("INFISICAL_ENVIRONMENT", "development")
    monkeypatch.setenv("UA_RUNTIME_STAGE", "sandbox")

    with pytest.raises(ValueError, match="Unsupported UA_RUNTIME_STAGE"):
        infisical_loader.initialize_runtime_secrets(force_reload=True)


def test_initialize_runtime_secrets_preserves_bootstrap_identity_over_infisical(monkeypatch):
    monkeypatch.setenv("UA_DEPLOYMENT_PROFILE", "local_workstation")
    monkeypatch.setenv("UA_INFISICAL_ENABLED", "1")
    monkeypatch.setenv("INFISICAL_CLIENT_ID", "client")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "project")
    monkeypatch.setenv("INFISICAL_ENVIRONMENT", "staging")
    monkeypatch.setenv("UA_RUNTIME_STAGE", "staging")
    monkeypatch.setenv("FACTORY_ROLE", "LOCAL_WORKER")
    monkeypatch.setenv("UA_MACHINE_SLUG", "kevins-desktop")
    monkeypatch.setattr(
        infisical_loader,
        "_fetch_infisical_secrets",
        lambda: {
            "FACTORY_ROLE": "HEADQUARTERS",
            "UA_RUNTIME_STAGE": "production",
            "UA_MACHINE_SLUG": "vps-hq-production",
            "UA_OPS_TOKEN": "token",
        },
    )

    result = infisical_loader.initialize_runtime_secrets(force_reload=True)

    assert result.environment == "staging"
    assert result.runtime_stage == "staging"
    assert result.machine_slug == "kevins-desktop"
    assert infisical_loader.os.getenv("FACTORY_ROLE") == "LOCAL_WORKER"
    assert infisical_loader.os.getenv("UA_RUNTIME_STAGE") == "staging"
    assert infisical_loader.os.getenv("UA_MACHINE_SLUG") == "kevins-desktop"
    assert infisical_loader.os.getenv("UA_OPS_TOKEN") == "token"

def test_initialize_runtime_secrets_overwrites_preexisting_env_for_non_identity_keys(monkeypatch):
    """Regression: 2026-05-16 incident — operator set
    UA_ATLAS_DIRECT_DISPATCH_ENABLED=0 in Infisical to disable a runaway cron,
    but the loader silently skipped it because the key was already present in
    os.environ (set by systemd / .env / module-import side effects). Infisical
    is the single source of truth for application config — its values MUST
    win over pre-existing env entries for any key that isn't a bootstrap
    identity key.
    """
    monkeypatch.setenv("UA_DEPLOYMENT_PROFILE", "local_workstation")
    monkeypatch.setenv("UA_INFISICAL_ENABLED", "1")
    monkeypatch.setenv("INFISICAL_CLIENT_ID", "client")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "project")
    # Simulate systemd/.env pre-populating the env with a stale value.
    monkeypatch.setenv("UA_ATLAS_DIRECT_DISPATCH_ENABLED", "1")
    monkeypatch.setenv("UA_SOME_FEATURE_FLAG", "old")
    monkeypatch.setattr(
        infisical_loader,
        "_fetch_infisical_secrets",
        lambda: {
            "UA_ATLAS_DIRECT_DISPATCH_ENABLED": "0",
            "UA_SOME_FEATURE_FLAG": "new",
        },
    )

    result = infisical_loader.initialize_runtime_secrets(force_reload=True)

    assert result.ok is True
    assert infisical_loader.os.getenv("UA_ATLAS_DIRECT_DISPATCH_ENABLED") == "0"
    assert infisical_loader.os.getenv("UA_SOME_FEATURE_FLAG") == "new"


def test_normalize_secret_value_strips_path_vars():
    """Cert-path env vars are single-line filesystem paths — a stray trailing
    newline (live defect 2026-06-11 in the Infisical CURL_CA_BUNDLE value) makes
    curl/OpenSSL treat the path as an unreadable cert file. These are trimmed.
    """
    assert (
        infisical_loader._normalize_secret_value(
            "CURL_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt\n"
        )
        == "/etc/ssl/certs/ca-certificates.crt"
    )
    for key in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        assert infisical_loader._normalize_secret_value(key, " /p/cert.pem \n") == "/p/cert.pem"
    # Suffix matcher catches future siblings without an explicit allowlist entry.
    assert (
        infisical_loader._normalize_secret_value("MY_CUSTOM_CA_BUNDLE", "/x/bundle.crt\n")
        == "/x/bundle.crt"
    )
    # Already-clean value is a no-op.
    assert (
        infisical_loader._normalize_secret_value(
            "CURL_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt"
        )
        == "/etc/ssl/certs/ca-certificates.crt"
    )


def test_normalize_secret_value_preserves_non_path_secrets():
    """Multi-line secrets (PEM keys, JSON blobs) and ordinary tokens are NOT in
    the path allowlist, so their bytes — including trailing newlines — survive.
    This is why the backstop is a targeted allowlist, never a blanket strip.
    """
    pem = "-----BEGIN PRIVATE KEY-----\nABC\n-----END PRIVATE KEY-----\n"
    assert infisical_loader._normalize_secret_value("SERVICE_ACCOUNT_KEY", pem) == pem
    assert infisical_loader._normalize_secret_value("SOME_TOKEN", "tok\n") == "tok\n"


def test_inject_environment_values_normalizes_path_vars(monkeypatch):
    # setenv registers both keys for monkeypatch teardown restore; overwrite=True
    # lets the injector replace them.
    monkeypatch.setenv("CURL_CA_BUNDLE", "")
    monkeypatch.setenv("UA_TEST_MULTILINE_SECRET", "")
    infisical_loader._inject_environment_values(
        {
            "CURL_CA_BUNDLE": "/etc/ssl/certs/ca-certificates.crt\n",
            "UA_TEST_MULTILINE_SECRET": "line1\nline2\n",
        },
        overwrite=True,
    )
    assert (
        infisical_loader.os.environ["CURL_CA_BUNDLE"]
        == "/etc/ssl/certs/ca-certificates.crt"
    )
    # Non-path multi-line value is preserved byte-for-byte.
    assert infisical_loader.os.environ["UA_TEST_MULTILINE_SECRET"] == "line1\nline2\n"


def test_initialize_runtime_secrets_aliases_zai_api_key(monkeypatch):
    monkeypatch.setenv("UA_DEPLOYMENT_PROFILE", "local_workstation")
    monkeypatch.setenv("UA_INFISICAL_ENABLED", "1")
    monkeypatch.setenv("INFISICAL_CLIENT_ID", "client")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "project")
    
    monkeypatch.setattr(
        infisical_loader,
        "_fetch_infisical_secrets",
        lambda: {"ZAI_API_KEY": "test-zai-key"},
    )

    result = infisical_loader.initialize_runtime_secrets(force_reload=True)

    assert result.ok is True
    assert infisical_loader.os.getenv("ZAI_API_KEY") == "test-zai-key"
    assert infisical_loader.os.getenv("Z_AI_API_KEY") == "test-zai-key"

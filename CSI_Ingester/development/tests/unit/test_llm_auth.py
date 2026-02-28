from __future__ import annotations

import pytest

from csi_ingester.llm_auth import resolve_csi_llm_auth


def test_mode_zero_uses_shared_precedence(monkeypatch) -> None:
    monkeypatch.setenv("CSI_LLM_AUTH_MODE", "0")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "shared_token")
    monkeypatch.setenv("ZAI_API_KEY", "shared_zai")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    settings = resolve_csi_llm_auth({}, default_base_url="https://api.anthropic.com")
    assert settings.mode == 0
    assert settings.lane == "shared_ua"
    assert settings.api_key == "shared_token"
    assert settings.base_url == "https://api.anthropic.com"


def test_mode_one_uses_dedicated_only(monkeypatch) -> None:
    monkeypatch.setenv("CSI_LLM_AUTH_MODE", "1")
    monkeypatch.setenv("CSI_ANTHROPIC_AUTH_TOKEN", "dedicated_token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "shared_should_not_be_used")
    settings = resolve_csi_llm_auth({}, default_base_url="https://api.anthropic.com")
    assert settings.mode == 1
    assert settings.lane == "csi_dedicated"
    assert settings.api_key == "dedicated_token"


def test_mode_one_without_dedicated_key_fails_fast(monkeypatch) -> None:
    monkeypatch.setenv("CSI_LLM_AUTH_MODE", "1")
    monkeypatch.delenv("CSI_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CSI_ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CSI_ZAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "shared_should_not_be_used")
    with pytest.raises(ValueError):
        resolve_csi_llm_auth({}, default_base_url="https://api.anthropic.com")


def test_mode_one_prefers_csi_base_url_alias(monkeypatch) -> None:
    monkeypatch.setenv("CSI_LLM_AUTH_MODE", "1")
    monkeypatch.setenv("CSI_ANTHROPIC_API_KEY", "dedicated_key")
    monkeypatch.setenv("CSI_ZAI_BASE_URL", "https://zai.example.test")
    monkeypatch.delenv("CSI_ANTHROPIC_BASE_URL", raising=False)
    settings = resolve_csi_llm_auth({}, default_base_url="https://api.anthropic.com")
    assert settings.base_url == "https://zai.example.test"


def test_mode_zero_supports_env_file_fallback(monkeypatch) -> None:
    monkeypatch.setenv("CSI_LLM_AUTH_MODE", "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    settings = resolve_csi_llm_auth(
        {"ZAI_API_KEY": "from_env_file", "ANTHROPIC_BASE_URL": "https://proxy.example.test"},
        default_base_url="https://api.anthropic.com",
    )
    assert settings.api_key == "from_env_file"
    assert settings.base_url == "https://proxy.example.test"

"""Unit tests for ``csi_ingester.infisical_bootstrap`` allowlist behavior.

The allowlist (``CSI_INFISICAL_KEYS``) exists specifically to prevent the
#820/#824 regression: enabling the bootstrap pulled the full production vault
into csi-ingester and overrode its proxy creds (``error=407 NO_USER``). These
tests pin the contract that an allowlisted bootstrap injects ONLY the listed
keys and leaves everything else (proxy creds, unrelated vault secrets) alone.
"""
from __future__ import annotations

import os

from csi_ingester import infisical_bootstrap as ib


def test_allowlist_keys_unset_returns_none(monkeypatch):
    monkeypatch.delenv("CSI_INFISICAL_KEYS", raising=False)
    assert ib._allowlist_keys() is None


def test_allowlist_keys_parsed_and_trimmed(monkeypatch):
    monkeypatch.setenv("CSI_INFISICAL_KEYS", "ZAI_API_KEY, ANTHROPIC_BASE_URL , ,foo")
    assert ib._allowlist_keys() == {"ZAI_API_KEY", "ANTHROPIC_BASE_URL", "foo"}


def test_bootstrap_disabled_is_noop(monkeypatch):
    monkeypatch.setenv("CSI_INFISICAL_ENABLED", "0")
    res = ib.bootstrap_csi_secrets()
    assert res.source == "disabled"
    assert res.loaded_count == 0


def test_bootstrap_allowlist_injects_only_listed(monkeypatch):
    """#820/#824 regression guard: allowlist must NOT inject proxy / unrelated secrets."""
    monkeypatch.setenv("CSI_INFISICAL_ENABLED", "1")
    monkeypatch.setenv("CSI_INFISICAL_KEYS", "ZAI_API_KEY,ANTHROPIC_BASE_URL")
    fake_vault = {
        "ZAI_API_KEY": "zk-secret",
        "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
        "HTTP_PROXY": "http://must-not-inject:8080",
        "HTTPS_PROXY": "http://must-not-inject:8080",
        "CSI_UA_SHARED_SECRET": "must-not-inject",
        "RANDOM_VAULT_SECRET": "must-not-inject",
    }
    monkeypatch.setattr(ib, "_fetch_secrets", lambda: fake_vault)
    for key in fake_vault:
        monkeypatch.delenv(key, raising=False)

    res = ib.bootstrap_csi_secrets()

    assert res.source == "infisical"
    assert res.loaded_count == 2
    assert os.environ.get("ZAI_API_KEY") == "zk-secret"
    assert os.environ.get("ANTHROPIC_BASE_URL") == "https://api.z.ai/api/anthropic"
    # The proxy creds + unrelated vault secrets must NOT have been injected.
    assert "HTTP_PROXY" not in os.environ
    assert "HTTPS_PROXY" not in os.environ
    assert os.environ.get("CSI_UA_SHARED_SECRET") != "must-not-inject"
    assert "RANDOM_VAULT_SECRET" not in os.environ


def test_bootstrap_no_allowlist_injects_all(monkeypatch):
    """Back-compat: with no allowlist, every fetched secret is injected."""
    monkeypatch.setenv("CSI_INFISICAL_ENABLED", "1")
    monkeypatch.delenv("CSI_INFISICAL_KEYS", raising=False)
    fake_vault = {"ZAI_API_KEY": "zk", "HTTP_PROXY": "p", "RANDOM": "r"}
    monkeypatch.setattr(ib, "_fetch_secrets", lambda: fake_vault)
    for key in fake_vault:
        monkeypatch.delenv(key, raising=False)

    res = ib.bootstrap_csi_secrets()

    assert res.loaded_count == 3


def test_bootstrap_allowlist_skips_keys_already_in_env(monkeypatch):
    """Injection never overrides keys already present (precedence: env-file > vault)."""
    monkeypatch.setenv("CSI_INFISICAL_ENABLED", "1")
    monkeypatch.setenv("CSI_INFISICAL_KEYS", "ZAI_API_KEY")
    monkeypatch.setattr(ib, "_fetch_secrets", lambda: {"ZAI_API_KEY": "from-vault"})
    monkeypatch.setenv("ZAI_API_KEY", "from-env-file")

    res = ib.bootstrap_csi_secrets()

    assert res.loaded_count == 0  # already present -> not injected
    assert os.environ.get("ZAI_API_KEY") == "from-env-file"



def test_bootstrap_allowlist_blocks_csi_rss_proxy_url(monkeypatch):
    """2026-06-16 regression: CSI_RSS_PROXY_URL in the vault must NOT be injected.

    The youtube RSS adapter honors CSI_RSS_PROXY_URL explicitly (os.getenv at
    adapters/youtube_channel_rss.py). If the bootstrap leaks it from the vault,
    public RSS fetches route through the residential proxy and return
    ``407 NO_USER``, tripping the circuit breaker at 50%. The allowlist
    (``CSI_INFISICAL_KEYS``) is the guard: proxy routing is an explicit env-file
    opt-in only, never auto-injected from the vault.
    """
    monkeypatch.setenv("CSI_INFISICAL_ENABLED", "1")
    monkeypatch.setenv(
        "CSI_INFISICAL_KEYS",
        "ZAI_API_KEY,ANTHROPIC_AUTH_TOKEN,ANTHROPIC_BASE_URL",
    )
    fake_vault = {
        "ZAI_API_KEY": "zk",
        "ANTHROPIC_AUTH_TOKEN": "tok",
        "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
        "CSI_RSS_PROXY_URL": "http://user:pass@residential-proxy.example:8080",
        "HTTP_PROXY": "http://must-not-inject:8080",
    }
    monkeypatch.setattr(ib, "_fetch_secrets", lambda: fake_vault)
    for key in fake_vault:
        monkeypatch.delenv(key, raising=False)

    res = ib.bootstrap_csi_secrets()

    assert res.loaded_count == 3  # only the 3 allowlisted keys
    assert "CSI_RSS_PROXY_URL" not in os.environ  # the active 2026-06-16 leak vector
    assert "HTTP_PROXY" not in os.environ

"""Regression guards for ANTHROPIC_* exclusion in interactive `claude` launch.

Phase B of the interactive-coding inversion was silently defeated multiple
times because Infisical fetches every secret and UA Python services need
ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / etc. for direct-SDK code paths,
so they live in Infisical alongside MCP credentials. The interactive
launcher must keep the entire ANTHROPIC_* namespace off ``os.environ`` so
Anthropic Max OAuth (``~/.claude/.credentials.json``) is the resolved auth
path. Defense in two layers:

1. Load-time filter: ``initialize_runtime_secrets(exclude_prefixes=…)`` skips
   matching keys before they reach ``os.environ``.
2. Post-bootstrap strip: ``_strip_interactive_routing_vars`` removes any
   ANTHROPIC_* leaked from a non-Infisical source (bootstrap .env, parent
   shell, etc.).

Both layers are exercised here.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _import_launcher_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "scripts" / "_claude_launcher.py"
    spec = importlib.util.spec_from_file_location("_claude_launcher", module_path)
    assert spec and spec.loader, f"could not build spec for {module_path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_claude_launcher", module)
    spec.loader.exec_module(module)
    return module


_launcher = _import_launcher_module()
_strip_interactive_routing_vars = _launcher._strip_interactive_routing_vars
_INTERACTIVE_STRIP_PREFIX = _launcher._INTERACTIVE_STRIP_PREFIX


# ─── Layer 2: post-bootstrap strip (defense-in-depth) ──────────────────────


def test_strip_prefix_is_anthropic_namespace():
    """Strip target is the entire ANTHROPIC_* namespace, not a fixed list."""
    assert _INTERACTIVE_STRIP_PREFIX == "ANTHROPIC_"


def test_strip_removes_routing_keys_and_api_key_and_preserves_mcp_creds():
    """Both routing keys (BASE_URL/AUTH_TOKEN/MODEL) and ANTHROPIC_API_KEY
    must go — the latter is what Claude Code treats as an external API key
    that overrides OAuth and yields 'Invalid API key · Fix external API key'."""
    env = {
        "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
        "ANTHROPIC_AUTH_TOKEN": "secret-zai-token",
        "ANTHROPIC_API_KEY": "sk-ant-account-with-no-max-billing",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5-turbo",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5-turbo",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.1",
        "ANTHROPIC_VERTEX_PROJECT_ID": "some-vertex-project",
        "AGENTMAIL_API_KEY": "keep-mcp-cred",
        "DISCORD_BOT_TOKEN": "keep-this-too",
        "HOSTINGER_API_TOKEN": "keep-this-three",
    }

    stripped = _strip_interactive_routing_vars(env)

    assert sorted(stripped) == [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_VERTEX_PROJECT_ID",
    ]
    assert env == {
        "AGENTMAIL_API_KEY": "keep-mcp-cred",
        "DISCORD_BOT_TOKEN": "keep-this-too",
        "HOSTINGER_API_TOKEN": "keep-this-three",
    }


def test_strip_is_noop_when_no_anthropic_vars_present():
    env = {"AGENTMAIL_API_KEY": "k", "DISCORD_BOT_TOKEN": "d"}

    stripped = _strip_interactive_routing_vars(env)

    assert stripped == []
    assert env == {"AGENTMAIL_API_KEY": "k", "DISCORD_BOT_TOKEN": "d"}


def test_strip_handles_partial_set():
    env = {
        "ANTHROPIC_API_KEY": "leaked-from-bootstrap-dotenv",
        "AGENTMAIL_API_KEY": "k",
    }

    stripped = _strip_interactive_routing_vars(env)

    assert stripped == ["ANTHROPIC_API_KEY"]
    assert env == {"AGENTMAIL_API_KEY": "k"}


def test_strip_is_prefix_match_not_substring():
    """A key containing 'ANTHROPIC_' but not starting with it is preserved."""
    env = {
        "ANTHROPIC_API_KEY": "strip-this",
        "MY_ANTHROPIC_NOTE": "keep-this",
    }

    stripped = _strip_interactive_routing_vars(env)

    assert stripped == ["ANTHROPIC_API_KEY"]
    assert env == {"MY_ANTHROPIC_NOTE": "keep-this"}


# ─── Layer 1: load-time filter via _inject_environment_values ──────────────


def _import_infisical_loader():
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root / "src"))
    from universal_agent.infisical_loader import _inject_environment_values

    return _inject_environment_values


def test_inject_skips_excluded_prefixes(monkeypatch):
    """Load-time filter prevents excluded keys from entering os.environ."""
    inject = _import_infisical_loader()

    sentinel_keys = (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "AGENTMAIL_API_KEY",
        "DISCORD_BOT_TOKEN",
    )
    for key in sentinel_keys:
        monkeypatch.delenv(key, raising=False)

    inserted = inject(
        {
            "ANTHROPIC_API_KEY": "would-poison-oauth",
            "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.1",
            "AGENTMAIL_API_KEY": "real-mcp-cred",
            "DISCORD_BOT_TOKEN": "real-mcp-cred-too",
        },
        overwrite=False,
        exclude_prefixes=("ANTHROPIC_",),
    )

    import os

    for key in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
    ):
        assert key not in os.environ, f"{key} should have been excluded at load"
    assert os.environ["AGENTMAIL_API_KEY"] == "real-mcp-cred"
    assert os.environ["DISCORD_BOT_TOKEN"] == "real-mcp-cred-too"
    assert inserted == 2


def test_inject_default_no_filter_loads_all(monkeypatch):
    """Without exclude_prefixes (UA service path), every key flows through."""
    inject = _import_infisical_loader()

    sentinel_keys = ("ANTHROPIC_API_KEY", "AGENTMAIL_API_KEY")
    for key in sentinel_keys:
        monkeypatch.delenv(key, raising=False)

    inserted = inject(
        {
            "ANTHROPIC_API_KEY": "needed-by-refinement-agent",
            "AGENTMAIL_API_KEY": "real-mcp-cred",
        },
        overwrite=False,
    )

    import os

    assert os.environ["ANTHROPIC_API_KEY"] == "needed-by-refinement-agent"
    assert os.environ["AGENTMAIL_API_KEY"] == "real-mcp-cred"
    assert inserted == 2

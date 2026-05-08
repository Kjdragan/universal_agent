"""Regression guard for `_claude_launcher._strip_interactive_routing_vars`.

Phase B of the interactive-coding inversion was silently defeated between
2026-05-07 and 2026-05-08 because `scripts/claude_with_mcp_env.sh` (the
canonical interactive launcher per `docs/deployment/secrets_and_environments.md`)
fetches every Infisical secret onto `os.environ`, and Phase A had staged the 5
`ANTHROPIC_*` ZAI routing vars in Infisical for UA Python services. The strip
helper exists so interactive `claude` falls through to Anthropic Max OAuth.
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
_INTERACTIVE_STRIP_KEYS = _launcher._INTERACTIVE_STRIP_KEYS


def test_strip_keys_match_phase_a_zai_inventory():
    """The strip set must equal the 5 keys Phase A staged in Infisical."""
    assert set(_INTERACTIVE_STRIP_KEYS) == {
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
    }


def test_strip_removes_all_zai_routing_vars_and_preserves_mcp_creds():
    env = {
        "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
        "ANTHROPIC_AUTH_TOKEN": "secret-zai-token",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5-turbo",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5-turbo",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.1",
        "AGENTMAIL_API_KEY": "keep-mcp-cred",
        "DISCORD_BOT_TOKEN": "keep-this-too",
        "HOSTINGER_API_TOKEN": "keep-this-three",
    }

    stripped = _strip_interactive_routing_vars(env)

    assert sorted(stripped) == sorted(_INTERACTIVE_STRIP_KEYS)
    for key in _INTERACTIVE_STRIP_KEYS:
        assert key not in env, f"{key} should be stripped"
    assert env == {
        "AGENTMAIL_API_KEY": "keep-mcp-cred",
        "DISCORD_BOT_TOKEN": "keep-this-too",
        "HOSTINGER_API_TOKEN": "keep-this-three",
    }


def test_strip_is_noop_when_no_zai_vars_present():
    env = {"AGENTMAIL_API_KEY": "k", "DISCORD_BOT_TOKEN": "d"}

    stripped = _strip_interactive_routing_vars(env)

    assert stripped == []
    assert env == {"AGENTMAIL_API_KEY": "k", "DISCORD_BOT_TOKEN": "d"}


def test_strip_handles_partial_set():
    env = {
        "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
        "AGENTMAIL_API_KEY": "k",
    }

    stripped = _strip_interactive_routing_vars(env)

    assert stripped == ["ANTHROPIC_BASE_URL"]
    assert env == {"AGENTMAIL_API_KEY": "k"}


def test_strip_returns_keys_in_inventory_order():
    """Order is part of the contract for stable stderr telemetry."""
    env = {key: "v" for key in _INTERACTIVE_STRIP_KEYS}

    stripped = _strip_interactive_routing_vars(env)

    assert stripped == list(_INTERACTIVE_STRIP_KEYS)

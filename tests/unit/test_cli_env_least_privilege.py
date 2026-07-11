"""The spawned `claude` CLI must get a least-privilege env at the trust boundary.

Handing the CLI the full parent os.environ leaks every Infisical-loaded secret
(most dangerously INFISICAL_CLIENT_SECRET, the machine identity that can fetch
the whole vault) to an autonomous agent with Bash access. The env is now
filtered to an allow-list: operational vars + the CLI's own auth + the specific
MCP-server secrets (.mcp.json), with UA_CLI_ENV_LEAST_PRIVILEGE=0 as an instant
kill-switch.
"""

from pathlib import Path

from universal_agent.vp.clients import claude_cli_client as c


def test_allow_list_keeps_operational_auth_and_mcp_secrets():
    keep = {
        # operational
        "PATH", "HOME", "USER", "LANG", "TZ", "SSL_CERT_FILE",
        "PYTHONPATH", "NODE_OPTIONS", "NPM_CONFIG_CACHE", "GIT_CONFIG_GLOBAL",
        # CLI auth (both modes)
        "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN",
        # MCP server secrets
        "ZAI_API_KEY", "ZAI_BASE_URL", "AGENTMAIL_API_KEY", "DISCORD_BOT_TOKEN",
        # non-secret app config
        "UA_DEPLOYMENT_PROFILE", "UA_ARTIFACTS_DIR", "FACTORY_ROLE",
    }
    for k in keep:
        assert c._cli_env_key_allowed(k), f"{k} should be allowed"


def test_deny_list_drops_infisical_and_unrelated_secrets():
    drop = {
        # the crown jewels — machine identity that can fetch the whole vault
        "INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET", "INFISICAL_PROJECT_ID",
        # unrelated service secrets the CLI/MCP don't need
        "GH_TOKEN", "GITHUB_TOKEN", "COMPOSIO_API_KEY", "OPENAI_API_KEY",
        "STRIPE_SECRET_KEY", "TELEGRAM_BOT_TOKEN", "SOME_VENDOR_SECRET",
        # UA_* secrets
        "UA_OPS_TOKEN", "UA_INTERNAL_API_TOKEN", "UA_OPS_JWT_SECRET",
        "UA_ARTIFACT_ACK_SECRET", "UA_FEEDBACK_HMAC_SECRET",
    }
    for k in drop:
        assert not c._cli_env_key_allowed(k), f"{k} should be dropped"


def test_filter_is_pure_allow_list():
    src = {"PATH": "/bin", "ZAI_API_KEY": "z", "INFISICAL_CLIENT_SECRET": "s", "GH_TOKEN": "g"}
    out = c._filter_cli_env_least_privilege(src)
    assert out == {"PATH": "/bin", "ZAI_API_KEY": "z"}


def test_build_cli_env_drops_secrets_keeps_needed(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("UA_CLI_ENV_LEAST_PRIVILEGE", raising=False)  # default ON
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "vault-master")
    monkeypatch.setenv("SOME_VENDOR_SECRET", "leak-me")
    monkeypatch.setenv("ZAI_API_KEY", "zai-key")
    monkeypatch.setenv("AGENTMAIL_API_KEY", "am-key")

    env = c._build_cli_env(enable_agent_teams=False, workspace_dir=tmp_path, cody_mode="zai")

    assert "INFISICAL_CLIENT_SECRET" not in env
    assert "SOME_VENDOR_SECRET" not in env
    assert env.get("ZAI_API_KEY") == "zai-key"
    assert env.get("AGENTMAIL_API_KEY") == "am-key"
    assert "PATH" in env  # operational var preserved
    # App-controlled vars set after the filter still land.
    assert env["CURRENT_RUN_WORKSPACE"] == str(tmp_path)


def test_kill_switch_restores_full_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("UA_CLI_ENV_LEAST_PRIVILEGE", "0")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "vault-master")

    env = c._build_cli_env(enable_agent_teams=False, workspace_dir=tmp_path, cody_mode="zai")

    # Kill-switch off → old full-inheritance behavior.
    assert env.get("INFISICAL_CLIENT_SECRET") == "vault-master"

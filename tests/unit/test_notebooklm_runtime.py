from __future__ import annotations

from pathlib import Path

from universal_agent import notebooklm_runtime


class _FakeCompleted:
    def __init__(self, returncode: int):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def test_build_notebooklm_mcp_server_config_disabled(monkeypatch):
    monkeypatch.delenv("UA_ENABLE_NOTEBOOKLM_MCP", raising=False)
    cfg = notebooklm_runtime.build_notebooklm_mcp_server_config()
    assert cfg is None


def test_build_notebooklm_mcp_server_config_enabled(monkeypatch):
    monkeypatch.setenv("UA_ENABLE_NOTEBOOKLM_MCP", "1")
    monkeypatch.setenv("UA_NOTEBOOKLM_MCP_COMMAND", "custom-notebooklm-mcp")
    monkeypatch.setenv("UA_NOTEBOOKLM_PROFILE", "vps")
    monkeypatch.setenv("NOTEBOOKLM_MCP_TRANSPORT", "stdio")

    cfg = notebooklm_runtime.build_notebooklm_mcp_server_config()

    assert cfg is not None
    assert cfg["command"] == "custom-notebooklm-mcp"
    assert cfg["type"] == "stdio"
    assert cfg["env"]["NOTEBOOKLM_PROFILE"] == "vps"


def test_auth_seed_defaults_enabled_for_vps_profile(monkeypatch):
    monkeypatch.delenv("UA_DEPLOYMENT_PROFILE", raising=False)
    monkeypatch.delenv("UA_NOTEBOOKLM_AUTH_SEED_ENABLED", raising=False)
    monkeypatch.setenv("UA_NOTEBOOKLM_PROFILE", "vps")

    assert notebooklm_runtime.notebooklm_auth_seed_enabled() is True


def test_auth_preflight_passes_without_seed(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_NOTEBOOKLM_PROFILE", "vps")
    monkeypatch.setenv("UA_NOTEBOOKLM_CLI_COMMAND", "nlm")

    def _fake_run(args, timeout_seconds):
        assert args[:2] == ["nlm", "login"]
        return _FakeCompleted(0)

    monkeypatch.setattr(notebooklm_runtime, "_run_command", _fake_run)

    result = notebooklm_runtime.run_auth_preflight(str(tmp_path))

    assert result.ok is True
    assert result.seeded is False
    assert result.checks_attempted == 1


def test_auth_preflight_seeds_and_cleans_file(monkeypatch, tmp_path):
    secret = "SID=abc; HSID=def"
    monkeypatch.setenv("UA_NOTEBOOKLM_PROFILE", "vps")
    monkeypatch.setenv("UA_NOTEBOOKLM_CLI_COMMAND", "nlm")
    monkeypatch.setenv("UA_NOTEBOOKLM_AUTH_SEED_ENABLED", "1")
    monkeypatch.setenv("NOTEBOOKLM_AUTH_COOKIE_HEADER", secret)

    calls: list[list[str]] = []
    seen_seed_file: str | None = None

    def _fake_run(args, timeout_seconds):
        nonlocal seen_seed_file
        calls.append(args)
        if args[:4] == ["nlm", "login", "--check", "--profile"]:
            # Fail first check, pass second.
            if len([c for c in calls if c[:4] == ["nlm", "login", "--check", "--profile"]]) == 1:
                return _FakeCompleted(1)
            return _FakeCompleted(0)
        if args[:3] == ["nlm", "login", "--manual"]:
            seed_file = Path(args[args.index("--file") + 1])
            seen_seed_file = str(seed_file)
            assert seed_file.exists()
            assert secret in seed_file.read_text(encoding="utf-8")
            return _FakeCompleted(0)
        return _FakeCompleted(1)

    monkeypatch.setattr(notebooklm_runtime, "_run_command", _fake_run)

    result = notebooklm_runtime.run_auth_preflight(str(tmp_path))

    assert result.ok is True
    assert result.seeded is True
    assert seen_seed_file is not None
    assert not Path(seen_seed_file).exists(), "seed file should be removed after use"


def test_auth_preflight_never_leaks_secret_in_errors(monkeypatch, tmp_path):
    secret = "TOP_SECRET_COOKIE"
    monkeypatch.setenv("UA_NOTEBOOKLM_PROFILE", "vps")
    monkeypatch.setenv("UA_NOTEBOOKLM_AUTH_SEED_ENABLED", "1")
    monkeypatch.setenv("NOTEBOOKLM_AUTH_COOKIE_HEADER", secret)

    def _always_raise(args, timeout_seconds):
        raise RuntimeError(f"leaked:{secret}")

    monkeypatch.setattr(notebooklm_runtime, "_run_command", _always_raise)

    result = notebooklm_runtime.run_auth_preflight(str(tmp_path))

    assert result.ok is False
    assert all(secret not in err for err in result.errors)


def test_auth_preflight_reports_missing_cli_cleanly(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_NOTEBOOKLM_PROFILE", "vps")
    monkeypatch.setenv("UA_NOTEBOOKLM_CLI_COMMAND", "nlm")
    monkeypatch.delenv("NOTEBOOKLM_AUTH_COOKIE_HEADER", raising=False)
    monkeypatch.setattr(notebooklm_runtime.shutil, "which", lambda name: None)

    result = notebooklm_runtime.run_auth_preflight(str(tmp_path))

    assert result.ok is False
    assert result.checks_attempted == 0
    assert "cli_missing" in result.notes
    assert "auth_cli_missing:FileNotFoundError" in result.errors

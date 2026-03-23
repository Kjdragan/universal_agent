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
        assert args[:2] == ["nlm", "auth"]
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
        if args[:3] == ["nlm", "auth", "status"]:
            # Fail first check, pass second.
            if len([c for c in calls if c[:3] == ["nlm", "auth", "status"]]) == 1:
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


# --- v0.5.4 upgrade tests ---


def test_auth_preflight_uses_auth_status_command(monkeypatch, tmp_path):
    """Verify the preflight now invokes 'nlm auth status' instead of 'nlm login --check'."""
    monkeypatch.setenv("UA_NOTEBOOKLM_PROFILE", "vps")
    monkeypatch.setenv("UA_NOTEBOOKLM_CLI_COMMAND", "nlm")

    captured_args: list[list[str]] = []

    def _fake_run(args, timeout_seconds):
        captured_args.append(list(args))
        return _FakeCompleted(0)

    monkeypatch.setattr(notebooklm_runtime, "_run_command", _fake_run)

    result = notebooklm_runtime.run_auth_preflight(str(tmp_path))
    assert result.ok is True
    # The first call should be 'nlm auth status --profile vps'
    assert captured_args[0] == ["nlm", "auth", "status", "--profile", "vps"]


def test_notebooklm_research_import_timeout_default(monkeypatch):
    monkeypatch.delenv("UA_NOTEBOOKLM_RESEARCH_IMPORT_TIMEOUT", raising=False)
    assert notebooklm_runtime.notebooklm_research_import_timeout() == 1200


def test_notebooklm_research_import_timeout_env_override(monkeypatch):
    monkeypatch.setenv("UA_NOTEBOOKLM_RESEARCH_IMPORT_TIMEOUT", "1800")
    assert notebooklm_runtime.notebooklm_research_import_timeout() == 1800


def test_notebooklm_research_import_timeout_minimum_clamp(monkeypatch):
    monkeypatch.setenv("UA_NOTEBOOKLM_RESEARCH_IMPORT_TIMEOUT", "10")
    assert notebooklm_runtime.notebooklm_research_import_timeout() == 60


def test_parse_nlm_error_hint_extracts_hint():
    error = {"status": "error", "error": "Notebook not found", "hint": "Run 'nlm notebook list' to see available notebooks"}
    hint = notebooklm_runtime.parse_nlm_error_hint(error)
    assert hint == "Run 'nlm notebook list' to see available notebooks"


def test_parse_nlm_error_hint_returns_none_on_missing():
    error = {"status": "error", "error": "Some error"}
    assert notebooklm_runtime.parse_nlm_error_hint(error) is None
    assert notebooklm_runtime.parse_nlm_error_hint("not json") is None
    assert notebooklm_runtime.parse_nlm_error_hint(42) is None  # type: ignore[arg-type]


def test_parse_nlm_error_hint_from_json_string():
    import json
    error_str = json.dumps({"status": "error", "error": "Auth", "hint": "Run 'nlm login'"})
    hint = notebooklm_runtime.parse_nlm_error_hint(error_str)
    assert hint == "Run 'nlm login'"


def test_is_auth_hint_detects_login():
    assert notebooklm_runtime.is_auth_hint("Run 'nlm login' to authenticate") is True
    assert notebooklm_runtime.is_auth_hint("Your session has expired") is True
    assert notebooklm_runtime.is_auth_hint("Run 'nlm notebook list' to see available notebooks") is False
    assert notebooklm_runtime.is_auth_hint(None) is False
    assert notebooklm_runtime.is_auth_hint("") is False


def test_run_nlm_doctor_success(monkeypatch):
    monkeypatch.setenv("UA_NOTEBOOKLM_CLI_COMMAND", "nlm")

    def _fake_run(args, timeout_seconds):
        assert args == ["nlm", "doctor"]
        result = _FakeCompleted(0)
        result.stdout = "✓ Authenticated as user@example.com\n✓ Browser profile found"
        result.stderr = ""
        return result

    monkeypatch.setattr(notebooklm_runtime, "_run_command", _fake_run)

    doctor = notebooklm_runtime.run_nlm_doctor()
    assert doctor.ok is True
    assert doctor.auth_ok is True
    assert doctor.browser_ok is True


def test_run_nlm_doctor_cli_missing(monkeypatch):
    monkeypatch.setenv("UA_NOTEBOOKLM_CLI_COMMAND", "nlm")
    monkeypatch.setattr(notebooklm_runtime.shutil, "which", lambda name: None)

    doctor = notebooklm_runtime.run_nlm_doctor()
    assert doctor.ok is False
    assert "nlm_cli_not_found" in doctor.errors


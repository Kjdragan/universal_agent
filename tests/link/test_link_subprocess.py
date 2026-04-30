"""Phase 2a tests for link_bridge subprocess invocation.

Covers:
  - Auth-blob restoration writes file to UA_LINK_AUTH_BLOB_PATH (idempotent).
  - Auth seed status reflects no_blob / no_path / applied / seed_disabled.
  - _run_link_cli builds correct argv (with --format json, --test, env).
  - _run_link_cli parses successful JSON output.
  - _run_link_cli maps non-zero exit + JSON error body → typed error.
  - _run_link_cli handles FileNotFoundError → cli_not_found.
  - _run_link_cli handles TimeoutExpired → cli_timeout.
  - create_spend_request dispatches to CLI when not in stub mode.
  - retrieve_spend_request, list_payment_methods, mpp_pay similarly dispatch.
  - build_link_mcp_server_config returns None when disabled, dict when on.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from universal_agent.tools import link_bridge


VALID_CONTEXT = (
    "User initiated this purchase via the shopping assistant flow. "
    "Buying a single hardcover copy of 'Working in Public' for personal reading. "
    "Final amount includes shipping and tax."
)


@pytest.fixture(autouse=True)
def reset_auth_seed_state():
    """Reset the module-level auth-seed cache between tests."""
    link_bridge._AUTH_SEED_STATUS.clear()
    link_bridge._AUTH_SEED_STATUS.update({"applied": False, "path": None, "reason": None})
    yield
    link_bridge._AUTH_SEED_STATUS.clear()
    link_bridge._AUTH_SEED_STATUS.update({"applied": False, "path": None, "reason": None})


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    audit_file = tmp_path / "link_audit.jsonl"
    monkeypatch.setenv("UA_LINK_AUDIT_PATH", str(audit_file))
    for var in list(os.environ):
        if (
            var.startswith(("UA_LINK_", "UA_ENABLE_LINK"))
            and var != "UA_LINK_AUDIT_PATH"
        ):
            monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("LINK_AUTH_BLOB", raising=False)
    return tmp_path


# ── auth seed restoration ────────────────────────────────────────────────────


def test_auth_seed_no_blob_yields_no_blob_reason(isolated, monkeypatch):
    monkeypatch.setenv("UA_LINK_AUTH_SEED_ENABLED", "1")
    status = link_bridge._ensure_auth_seeded(force=True)
    assert status["applied"] is False
    assert status["reason"] == "no_blob"


def test_auth_seed_no_path_yields_no_path_reason(isolated, monkeypatch):
    monkeypatch.setenv("UA_LINK_AUTH_SEED_ENABLED", "1")
    monkeypatch.setenv("LINK_AUTH_BLOB", base64.b64encode(b"{}").decode())
    status = link_bridge._ensure_auth_seeded(force=True)
    assert status["applied"] is False
    assert status["reason"] == "no_path"


def test_auth_seed_disabled(isolated, monkeypatch):
    monkeypatch.setenv("UA_LINK_AUTH_SEED_ENABLED", "0")
    monkeypatch.setenv("LINK_AUTH_BLOB", base64.b64encode(b"{}").decode())
    monkeypatch.setenv("UA_LINK_AUTH_BLOB_PATH", str(isolated / "config.json"))
    status = link_bridge._ensure_auth_seeded(force=True)
    assert status["applied"] is False
    assert status["reason"] == "seed_disabled"


def test_auth_seed_writes_decoded_blob_with_secure_perms(isolated, monkeypatch):
    target = isolated / "deep" / "config.json"
    payload = b'{"token":"abc"}'
    monkeypatch.setenv("UA_LINK_AUTH_SEED_ENABLED", "1")
    monkeypatch.setenv("LINK_AUTH_BLOB", base64.b64encode(payload).decode())
    monkeypatch.setenv("UA_LINK_AUTH_BLOB_PATH", str(target))

    status = link_bridge._ensure_auth_seeded(force=True)
    assert status["applied"] is True
    assert Path(status["path"]) == target
    assert target.read_bytes() == payload
    # 0o600 perms on the file itself
    mode = target.stat().st_mode & 0o777
    assert mode == 0o600


def test_auth_seed_idempotent(isolated, monkeypatch):
    target = isolated / "config.json"
    monkeypatch.setenv("UA_LINK_AUTH_SEED_ENABLED", "1")
    monkeypatch.setenv("LINK_AUTH_BLOB", base64.b64encode(b'{"v":1}').decode())
    monkeypatch.setenv("UA_LINK_AUTH_BLOB_PATH", str(target))
    link_bridge._ensure_auth_seeded(force=True)
    assert target.exists()

    # Tamper with file then call again WITHOUT force — should not rewrite.
    target.write_bytes(b"tampered")
    link_bridge._ensure_auth_seeded(force=False)
    assert target.read_bytes() == b"tampered"

    # With force=True, restored.
    link_bridge._ensure_auth_seeded(force=True)
    assert target.read_bytes() == b'{"v":1}'


# ── _run_link_cli ────────────────────────────────────────────────────────────


def _fake_completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_link_cli_appends_format_json(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs.get("env")
        return _fake_completed(stdout=json.dumps({"id": "lsrq_x"}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(link_bridge.shutil, "which", lambda name: "/usr/bin/" + name if name in {"link-cli", "npx"} else None)

    res = link_bridge._run_link_cli(["spend-request", "create"])
    assert res["ok"] is True
    assert res["data"]["id"] == "lsrq_x"
    assert "--format" in captured["argv"]
    assert "json" in captured["argv"]
    assert captured["env"]["NO_UPDATE_NOTIFIER"] == "1"


def test_run_link_cli_appends_test_flag(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return _fake_completed(stdout="{}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(link_bridge.shutil, "which", lambda name: "/usr/bin/" + name if name in {"link-cli", "npx"} else None)

    link_bridge._run_link_cli(["spend-request", "create"], test_flag=True)
    assert "--test" in captured["argv"]


def test_run_link_cli_maps_json_error_body(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")

    def fake_run(argv, **kwargs):
        return _fake_completed(
            stdout=json.dumps({"code": "verification-failed", "message": "SPT consumed"}),
            returncode=1,
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(link_bridge.shutil, "which", lambda name: "/usr/bin/" + name if name in {"link-cli", "npx"} else None)

    res = link_bridge._run_link_cli(["mpp", "pay", "https://x"])
    assert res["ok"] is False
    assert res["error"]["code"] == "verification-failed"
    assert "SPT consumed" in res["error"]["message"]
    assert res["exit_code"] == 1


def test_run_link_cli_handles_file_not_found(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")

    def fake_run(*a, **kw):
        raise FileNotFoundError("no link-cli")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(link_bridge.shutil, "which", lambda name: None)

    res = link_bridge._run_link_cli(["auth", "status"])
    assert res["ok"] is False
    assert res["error"]["code"] == "cli_not_found"
    assert res["exit_code"] == 127


def test_run_link_cli_handles_timeout(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")

    def fake_run(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="link-cli", timeout=5)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(link_bridge.shutil, "which", lambda name: "/usr/bin/" + name)

    res = link_bridge._run_link_cli(["auth", "status"])
    assert res["ok"] is False
    assert res["error"]["code"] == "cli_timeout"


def test_run_link_cli_falls_back_to_npx(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    captured = {}

    def fake_which(name):
        return "/usr/bin/npx" if name == "npx" else None

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return _fake_completed(stdout="{}")

    monkeypatch.setattr(link_bridge.shutil, "which", fake_which)
    monkeypatch.setattr(subprocess, "run", fake_run)

    link_bridge._run_link_cli(["auth", "status"])
    assert captured["argv"][:3] == ["npx", "-y", "@stripe/link-cli"]


# ── public API dispatches to CLI in non-stub modes ───────────────────────────


def test_create_dispatches_to_cli_in_test_mode(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return _fake_completed(stdout=json.dumps({"id": "lsrq_real_001", "status": "pending_approval"}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(link_bridge.shutil, "which", lambda name: "/usr/bin/" + name if name in {"link-cli"} else None)

    res = link_bridge.create_spend_request(
        caller="test",
        payment_method_id="csmrpd_x",
        merchant_name="Stripe Press",
        merchant_url="https://press.stripe.com",
        context=VALID_CONTEXT,
        amount_cents=3500,
    )
    assert res["ok"] is True
    assert res["data"]["id"] == "lsrq_real_001"
    assert "--test" in captured["argv"], "test mode should pass --test to CLI"
    assert "--payment-method-id" in captured["argv"]
    assert "--request-approval" in captured["argv"]


def test_retrieve_dispatches_to_cli(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return _fake_completed(stdout=json.dumps({"id": "lsrq_x", "status": "approved"}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(link_bridge.shutil, "which", lambda name: "/usr/bin/" + name if name == "link-cli" else None)

    res = link_bridge.retrieve_spend_request(caller="test", spend_request_id="lsrq_x", include_card=True)
    assert res["ok"] is True
    assert "lsrq_x" in captured["argv"]
    assert "--include" in captured["argv"]
    assert "card" in captured["argv"]


def test_list_payment_methods_dispatches_to_cli(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")

    def fake_run(argv, **kwargs):
        return _fake_completed(stdout=json.dumps([{"id": "csmrpd_real", "type": "card", "last4": "1234"}]))

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(link_bridge.shutil, "which", lambda name: "/usr/bin/" + name if name == "link-cli" else None)

    res = link_bridge.list_payment_methods(caller="test")
    assert res["ok"] is True
    assert res["data"]["payment_methods"][0]["last4"] == "1234"


def test_mpp_pay_dispatches_to_cli(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return _fake_completed(stdout=json.dumps({"status": 200}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(link_bridge.shutil, "which", lambda name: "/usr/bin/" + name if name == "link-cli" else None)

    res = link_bridge.mpp_pay(
        caller="test",
        spend_request_id="lsrq_x",
        url="https://climate.stripe.dev/api/contribute",
        method="POST",
        data={"amount": 100},
        headers={"X-Custom": "value"},
    )
    assert res["ok"] is True
    assert "--spend-request-id" in captured["argv"]
    assert "--data" in captured["argv"]
    assert "--header" in captured["argv"]


def test_stub_mode_does_not_invoke_subprocess(isolated, monkeypatch):
    """Master switch off → subprocess.run must NEVER be called."""
    called = []

    def fake_run(*a, **kw):
        called.append(True)
        return _fake_completed(stdout="{}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = link_bridge.create_spend_request(
        caller="test",
        payment_method_id="x",
        merchant_name="X",
        merchant_url="https://x.example",
        context=VALID_CONTEXT,
        amount_cents=100,
    )
    # master switch off → guardrail_disabled, subprocess never invoked
    assert res["ok"] is False
    assert res["error"]["code"] == "guardrail_disabled"
    assert called == []


# ── MCP server config builder ────────────────────────────────────────────────


def test_build_mcp_config_returns_none_when_disabled(isolated):
    assert link_bridge.build_link_mcp_server_config() is None


def test_build_mcp_config_uses_global_link_cli_if_present(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setattr(link_bridge.shutil, "which", lambda name: "/usr/bin/link-cli" if name == "link-cli" else None)
    cfg = link_bridge.build_link_mcp_server_config()
    assert cfg is not None
    assert cfg["type"] == "stdio"
    assert cfg["command"] == "link-cli"
    assert cfg["args"] == ["--mcp"]


def test_build_mcp_config_falls_back_to_npx(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setattr(link_bridge.shutil, "which", lambda name: "/usr/bin/npx" if name == "npx" else None)
    cfg = link_bridge.build_link_mcp_server_config()
    assert cfg is not None
    assert cfg["command"] == "npx"
    assert cfg["args"] == ["-y", "@stripe/link-cli", "--mcp"]


def test_build_mcp_config_returns_none_when_no_cli(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setattr(link_bridge.shutil, "which", lambda name: None)
    assert link_bridge.build_link_mcp_server_config() is None


def test_build_mcp_config_honors_path_override(isolated, monkeypatch):
    monkeypatch.setenv("UA_ENABLE_LINK", "1")
    monkeypatch.setenv("UA_LINK_CLI_PATH", "/opt/custom/link-cli")
    cfg = link_bridge.build_link_mcp_server_config()
    assert cfg is not None
    assert cfg["command"] == "/opt/custom/link-cli"

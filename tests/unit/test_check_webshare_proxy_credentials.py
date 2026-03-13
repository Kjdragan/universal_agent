from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "check_webshare_proxy_credentials.py"
    spec = importlib.util.spec_from_file_location("check_webshare_proxy_credentials_test_module", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bootstrap_result() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        source="infisical",
        environment="development",
        runtime_stage="development",
        deployment_profile="local_workstation",
        loaded_count=12,
        errors=(),
    )


def test_credentials_probe_detects_missing_credentials(monkeypatch, capsys) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_load_local_env", lambda: None)
    monkeypatch.setattr(module, "initialize_runtime_secrets", lambda **_kwargs: _bootstrap_result())
    monkeypatch.setattr(
        module,
        "_resolve_proxy_settings",
        lambda: {
            "username": "",
            "password": "",
            "host": "p.webshare.io",
            "port": 80,
            "locations": [],
        },
    )
    monkeypatch.setattr(sys, "argv", ["check_webshare_proxy_credentials.py"])

    code = module.main()
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["ok"] is False
    codes = {item["code"] for item in payload["issues"]}
    assert "missing_proxy_credentials" in codes


def test_credentials_probe_flags_email_like_username(monkeypatch, capsys) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "_load_local_env", lambda: None)
    monkeypatch.setattr(module, "initialize_runtime_secrets", lambda **_kwargs: _bootstrap_result())
    monkeypatch.setattr(
        module,
        "_resolve_proxy_settings",
        lambda: {
            "username": "user@example.com",
            "password": "secret-pass",
            "host": "proxy.webshare.io",
            "port": 80,
            "locations": [],
        },
    )
    monkeypatch.setattr(sys, "argv", ["check_webshare_proxy_credentials.py"])

    code = module.main()
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["ok"] is True
    codes = {item["code"] for item in payload["issues"]}
    assert "username_looks_like_account_login" in codes
    assert "stale_proxy_host_override" in codes

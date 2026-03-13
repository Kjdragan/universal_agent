from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path


def _load_proxy_script_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "check_webshare_proxy.py"
    spec = importlib.util.spec_from_file_location("check_webshare_proxy_test_module", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bootstrap_result() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        ok=True,
        source="infisical",
        strict_mode=False,
        loaded_count=10,
        fallback_used=False,
        environment="development",
        runtime_stage="development",
        machine_slug="mint-desktop",
        deployment_profile="local_workstation",
        errors=(),
    )


def test_proxy_probe_main_reports_not_configured(monkeypatch, capsys) -> None:
    module = _load_proxy_script_module()
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
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_webshare_proxy.py",
            "--skip-tcp",
            "--skip-http",
            "--skip-https",
            "--skip-youtube",
        ],
    )

    code = module.main()
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["configured"] is False
    assert payload["failure_class"] == "proxy_not_configured"
    assert payload["bootstrap"]["source"] == "infisical"


def test_proxy_probe_main_surfaces_auth_failures(monkeypatch, capsys) -> None:
    module = _load_proxy_script_module()
    monkeypatch.setattr(module, "_load_local_env", lambda: None)
    monkeypatch.setattr(module, "initialize_runtime_secrets", lambda **_kwargs: _bootstrap_result())
    monkeypatch.setattr(
        module,
        "_resolve_proxy_settings",
        lambda: {
            "username": "proxy-user",
            "password": "proxy-pass",
            "host": "p.webshare.io",
            "port": 80,
            "locations": [],
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        module,
        "_tcp_probe",
        lambda *_args, **_kwargs: {
            "ok": True,
            "host": "p.webshare.io",
            "port": 80,
            "latency_ms": 20.0,
            "failure_class": "",
            "error": "",
        },
    )
    monkeypatch.setattr(
        module,
        "_request_via_proxy",
        lambda **kwargs: {
            "ok": False,
            "label": kwargs["label"],
            "url": kwargs["url"],
            "http_status": 0,
            "latency_ms": 50.0,
            "failure_class": "proxy_auth_failed",
            "error": "407 Proxy Authentication Required",
            "response_snippet": "",
        },
    )
    monkeypatch.setattr(sys, "argv", ["check_webshare_proxy.py"])

    code = module.main()
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["configured"] is True
    assert payload["ok"] is False
    assert payload["failure_classes"] == [
        "proxy_auth_failed",
        "proxy_auth_failed",
        "proxy_auth_failed",
    ]

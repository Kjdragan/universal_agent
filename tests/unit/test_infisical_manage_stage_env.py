from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_script_module(module_name: str, relative_path: str):
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_stage_accepts_legacy_aliases():
    module = _load_script_module(
        "infisical_manage_stage_env",
        "scripts/infisical_manage_stage_env.py",
    )

    assert module._validate_stage("dev") == "development"
    assert module._validate_stage("prod") == "production"
    assert module._validate_stage("kevins-desktop") == "staging"


def test_parse_preserve_flattens_repeated_and_comma_separated_values():
    module = _load_script_module(
        "infisical_manage_stage_env_parse",
        "scripts/infisical_manage_stage_env.py",
    )

    parsed = module._parse_preserve(["FOO,BAR", "BAR", "BAZ"])
    assert parsed == {"FOO", "BAR", "BAZ"}


def test_bulk_delete_secrets_uses_delete_batch_raw_payload(monkeypatch):
    module = _load_script_module(
        "infisical_manage_stage_env_delete",
        "scripts/infisical_manage_stage_env.py",
    )

    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

    def _fake_request(method, url, headers=None, json=None, timeout=None):
        captured["method"] = method
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(module.httpx, "request", _fake_request)

    module._bulk_delete_secrets(
        api_url="https://app.infisical.com",
        token="token",
        project_id="project-id",
        environment="staging",
        secret_path="/",
        secret_keys=["B", "A", "A"],
    )

    assert captured["method"] == "DELETE"
    assert captured["url"] == "https://app.infisical.com/api/v3/secrets/batch/raw"
    assert captured["json"]["workspaceId"] == "project-id"
    assert captured["json"]["environment"] == "staging"
    assert captured["json"]["secretPath"] == "/"
    assert captured["json"]["secrets"] == [
        {"secretKey": "A", "type": "shared"},
        {"secretKey": "B", "type": "shared"},
    ]

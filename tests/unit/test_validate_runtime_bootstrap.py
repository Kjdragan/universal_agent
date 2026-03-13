from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_script_module(module_name: str, relative_path: str):
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_runtime_bootstrap_reports_identity_json(monkeypatch, capsys):
    module = _load_script_module(
        "validate_runtime_bootstrap",
        "scripts/validate_runtime_bootstrap.py",
    )

    monkeypatch.setattr(
        module,
        "initialize_runtime_secrets",
        lambda profile=None, force_reload=False: SimpleNamespace(
            source="infisical",
            loaded_count=5,
            strict_mode=True,
            fallback_used=False,
            errors=(),
        ),
    )
    monkeypatch.setenv("INFISICAL_ENVIRONMENT", "staging")
    monkeypatch.setenv("UA_RUNTIME_STAGE", "staging")
    monkeypatch.setenv("FACTORY_ROLE", "HEADQUARTERS")
    monkeypatch.setenv("UA_DEPLOYMENT_PROFILE", "vps")
    monkeypatch.setenv("UA_MACHINE_SLUG", "vps-hq-staging")
    monkeypatch.setenv("UA_OPS_TOKEN", "token")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_runtime_bootstrap.py",
            "--profile",
            "vps",
            "--expect-environment",
            "staging",
            "--expect-runtime-stage",
            "staging",
            "--expect-factory-role",
            "HEADQUARTERS",
            "--expect-deployment-profile",
            "vps",
            "--expect-machine-slug",
            "vps-hq-staging",
            "--require",
            "UA_OPS_TOKEN",
            "--json",
        ],
    )

    assert module.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["identity"]["runtime_stage"] == "staging"
    assert payload["identity"]["machine_slug"] == "vps-hq-staging"


def test_validate_runtime_bootstrap_raises_on_missing_required_key(monkeypatch):
    module = _load_script_module(
        "validate_runtime_bootstrap_missing",
        "scripts/validate_runtime_bootstrap.py",
    )

    monkeypatch.setattr(
        module,
        "initialize_runtime_secrets",
        lambda profile=None, force_reload=False: SimpleNamespace(
            source="infisical",
            loaded_count=1,
            strict_mode=True,
            fallback_used=False,
            errors=(),
        ),
    )
    monkeypatch.setenv("INFISICAL_ENVIRONMENT", "production")
    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    monkeypatch.setenv("FACTORY_ROLE", "HEADQUARTERS")
    monkeypatch.setenv("UA_DEPLOYMENT_PROFILE", "vps")
    monkeypatch.setenv("UA_MACHINE_SLUG", "vps-hq-production")
    monkeypatch.delenv("UA_OPS_TOKEN", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_runtime_bootstrap.py",
            "--expect-environment",
            "production",
            "--expect-runtime-stage",
            "production",
            "--expect-factory-role",
            "HEADQUARTERS",
            "--expect-deployment-profile",
            "vps",
            "--expect-machine-slug",
            "vps-hq-production",
            "--require",
            "UA_OPS_TOKEN",
        ],
    )

    with pytest.raises(RuntimeError, match="Missing required keys: UA_OPS_TOKEN"):
        module.main()

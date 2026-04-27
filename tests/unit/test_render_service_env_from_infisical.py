from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace


def _load_script_module(module_name: str, relative_path: str):
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_identity_entries_are_emitted(monkeypatch):
    module = _load_script_module(
        "render_service_env_from_infisical",
        "scripts/render_service_env_from_infisical.py",
    )

    monkeypatch.setenv("INFISICAL_ENVIRONMENT", "staging")
    monkeypatch.setenv("UA_RUNTIME_STAGE", "staging")
    monkeypatch.setenv("FACTORY_ROLE", "HEADQUARTERS")
    monkeypatch.setenv("UA_DEPLOYMENT_PROFILE", "vps")
    monkeypatch.setenv("UA_MACHINE_SLUG", "vps-hq-staging")

    lines = module._render_lines(module._runtime_identity_entries(), allow_missing=False)

    assert "INFISICAL_ENVIRONMENT=staging" in lines
    assert "UA_RUNTIME_STAGE=staging" in lines
    assert "FACTORY_ROLE=HEADQUARTERS" in lines
    assert "UA_DEPLOYMENT_PROFILE=vps" in lines
    assert "UA_MACHINE_SLUG=vps-hq-staging" in lines


def test_main_writes_requested_entries_and_runtime_identity(tmp_path, monkeypatch):
    module = _load_script_module(
        "render_service_env_from_infisical_main",
        "scripts/render_service_env_from_infisical.py",
    )

    output_path = tmp_path / "service.env"

    monkeypatch.setattr(
        module,
        "initialize_runtime_secrets",
        lambda profile=None, force_reload=False: SimpleNamespace(ok=True),
    )
    monkeypatch.setenv("UA_OPS_TOKEN", "ops-token")
    monkeypatch.setenv("INFISICAL_ENVIRONMENT", "production")
    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    monkeypatch.setenv("FACTORY_ROLE", "HEADQUARTERS")
    monkeypatch.setenv("UA_DEPLOYMENT_PROFILE", "vps")
    monkeypatch.setenv("UA_MACHINE_SLUG", "vps-hq-production")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "render_service_env_from_infisical.py",
            "--output",
            str(output_path),
            "--entry",
            "UA_DASHBOARD_OPS_TOKEN=UA_DASHBOARD_OPS_TOKEN,UA_OPS_TOKEN",
            "--include-runtime-identity",
        ],
    )

    assert module.main() == 0
    text = output_path.read_text(encoding="utf-8")
    assert "UA_DASHBOARD_OPS_TOKEN=ops-token" in text
    assert "INFISICAL_ENVIRONMENT=production" in text
    assert "UA_RUNTIME_STAGE=production" in text
    assert "UA_MACHINE_SLUG=vps-hq-production" in text

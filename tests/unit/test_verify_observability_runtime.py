from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace


def _load_script_module(module_name: str, relative_path: str):
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_observability_runtime_probe_passes_for_real_logfire(monkeypatch):
    module = _load_script_module(
        "verify_observability_runtime_pass",
        "scripts/verify_observability_runtime.py",
    )
    fake_logfire = ModuleType("logfire")

    def fake_import(name: str):
        if name == "opentelemetry.context":
            return ModuleType("opentelemetry.context")
        if name == "logfire":
            return fake_logfire
        raise AssertionError(f"unexpected import {name}")

    monkeypatch.setattr(module, "import_module", fake_import)
    monkeypatch.setattr(
        module,
        "_entry_points_for_group",
        lambda group: [SimpleNamespace(name="contextvars_context")],
    )

    payload = module.collect_observability_runtime_state()

    assert payload["ok"] is True
    assert payload["failures"] == []
    assert payload["logfire_stub"] is False


def test_observability_runtime_probe_fails_without_context_entry_point(monkeypatch):
    module = _load_script_module(
        "verify_observability_runtime_missing_ep",
        "scripts/verify_observability_runtime.py",
    )

    monkeypatch.setattr(
        module,
        "import_module",
        lambda name: ModuleType(name),
    )
    monkeypatch.setattr(module, "_entry_points_for_group", lambda group: [])

    payload = module.collect_observability_runtime_state()

    assert payload["ok"] is False
    assert payload["failures"] == [
        "opentelemetry_context entry point 'contextvars_context' is missing"
    ]


def test_observability_runtime_probe_fails_when_logfire_is_stub(monkeypatch):
    module = _load_script_module(
        "verify_observability_runtime_stub",
        "scripts/verify_observability_runtime.py",
    )
    stub_logfire = ModuleType("logfire")
    stub_logfire.__ua_stub__ = True
    stub_logfire.__ua_stub_error__ = "StopIteration('otel context entry point missing')"

    def fake_import(name: str):
        if name == "opentelemetry.context":
            return ModuleType("opentelemetry.context")
        if name == "logfire":
            return stub_logfire
        raise AssertionError(f"unexpected import {name}")

    monkeypatch.setattr(module, "import_module", fake_import)
    monkeypatch.setattr(
        module,
        "_entry_points_for_group",
        lambda group: [SimpleNamespace(name="contextvars_context")],
    )

    payload = module.collect_observability_runtime_state()

    assert payload["ok"] is False
    assert payload["logfire_stub"] is True
    assert payload["failures"] == [
        "logfire resolved to Universal Agent fail-open stub: StopIteration('otel context entry point missing')"
    ]

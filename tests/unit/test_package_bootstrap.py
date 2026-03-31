import importlib
import os
import sys
import builtins
from types import ModuleType


def _reset_bootstrap_modules() -> None:
    sys.modules.pop("universal_agent", None)
    sys.modules.pop("logfire", None)
    sys.modules.pop("logfire.query_client", None)


def test_package_bootstrap_disables_logfire_pydantic_plugin(monkeypatch):
    monkeypatch.delenv("PYDANTIC_DISABLE_PLUGINS", raising=False)
    _reset_bootstrap_modules()

    importlib.import_module("universal_agent")

    assert os.environ.get("PYDANTIC_DISABLE_PLUGINS") == "logfire-plugin"


def test_package_bootstrap_reports_disabled_mode_when_token_missing(monkeypatch):
    fake_logfire = ModuleType("logfire")

    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    monkeypatch.setitem(sys.modules, "logfire", fake_logfire)
    _reset_bootstrap_modules()
    sys.modules["logfire"] = fake_logfire

    module = importlib.import_module("universal_agent")

    assert module.get_logfire_runtime_state() == {
        "mode": "disabled",
        "token_present": False,
        "error": None,
        "reason": None,
    }


def test_package_bootstrap_reports_real_mode_when_token_present(monkeypatch):
    fake_logfire = ModuleType("logfire")

    monkeypatch.setenv("LOGFIRE_TOKEN", "test-token")
    monkeypatch.setitem(sys.modules, "logfire", fake_logfire)
    _reset_bootstrap_modules()
    sys.modules["logfire"] = fake_logfire

    module = importlib.import_module("universal_agent")

    assert module.get_logfire_runtime_state() == {
        "mode": "real",
        "token_present": True,
        "error": None,
        "reason": None,
    }


def test_package_bootstrap_installs_logfire_stub_on_non_importerror(monkeypatch):
    original_import = builtins.__import__

    def broken_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "logfire":
            raise StopIteration("otel context entry point missing")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", broken_import)
    monkeypatch.delenv("PYDANTIC_DISABLE_PLUGINS", raising=False)
    monkeypatch.setenv("LOGFIRE_TOKEN", "test-token")
    _reset_bootstrap_modules()

    module = importlib.import_module("universal_agent")

    logfire_module = sys.modules["logfire"]
    query_client_module = sys.modules["logfire.query_client"]

    assert getattr(logfire_module, "__ua_stub__", False) is True
    assert "otel context entry point missing" in getattr(logfire_module, "__ua_stub_error__", "")
    assert query_client_module.LogfireQueryClient().query_json_rows("select 1") == []
    assert module.get_logfire_runtime_state() == {
        "mode": "stub",
        "token_present": True,
        "error": "StopIteration",
        "reason": "otel context entry point missing",
    }

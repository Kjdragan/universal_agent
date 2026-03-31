import importlib
import os
import sys
import builtins


def test_package_bootstrap_disables_logfire_pydantic_plugin(monkeypatch):
    monkeypatch.delenv("PYDANTIC_DISABLE_PLUGINS", raising=False)
    sys.modules.pop("universal_agent", None)

    importlib.import_module("universal_agent")

    assert os.environ.get("PYDANTIC_DISABLE_PLUGINS") == "logfire-plugin"


def test_package_bootstrap_installs_logfire_stub_on_non_importerror(monkeypatch):
    original_import = builtins.__import__

    def broken_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "logfire":
            raise StopIteration("otel context entry point missing")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", broken_import)
    monkeypatch.delenv("PYDANTIC_DISABLE_PLUGINS", raising=False)
    sys.modules.pop("universal_agent", None)
    sys.modules.pop("logfire", None)
    sys.modules.pop("logfire.query_client", None)

    importlib.import_module("universal_agent")

    logfire_module = sys.modules["logfire"]
    query_client_module = sys.modules["logfire.query_client"]

    assert getattr(logfire_module, "__ua_stub__", False) is True
    assert "otel context entry point missing" in getattr(logfire_module, "__ua_stub_error__", "")
    assert query_client_module.LogfireQueryClient().query_json_rows("select 1") == []

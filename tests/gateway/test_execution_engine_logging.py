import importlib
import os


def test_process_stdio_redirect_disabled_by_default(monkeypatch):
    monkeypatch.delenv("UA_GATEWAY_PROCESS_STDIO_REDIRECT", raising=False)
    module = importlib.import_module("universal_agent.execution_engine")
    module = importlib.reload(module)
    assert module.USE_PROCESS_STDIO_REDIRECT is False


def test_process_stdio_redirect_can_be_enabled(monkeypatch):
    monkeypatch.setenv("UA_GATEWAY_PROCESS_STDIO_REDIRECT", "1")
    module = importlib.import_module("universal_agent.execution_engine")
    module = importlib.reload(module)
    assert module.USE_PROCESS_STDIO_REDIRECT is True

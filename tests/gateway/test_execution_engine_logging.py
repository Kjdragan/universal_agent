import importlib
import os
import tempfile
from io import StringIO


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


def test_tee_writer_tolerates_closed_log_handle():
    module = importlib.import_module("universal_agent.execution_engine")
    module = importlib.reload(module)

    stream = StringIO()
    with tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8") as handle:
        handle.write("before")
        handle.flush()
        handle.close()
        writer = module._TeeWriter(handle, stream)
        # Should not raise even if underlying file handle is closed.
        writer.write("hello")
        writer.flush()

    assert stream.getvalue() == "hello"

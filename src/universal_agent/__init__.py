"""Universal Agent package bootstrap.

This module is imported before any ``universal_agent.*`` submodule, so it is
the earliest safe place to establish process-wide defaults that must exist
before heavy imports start.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import sys
from types import ModuleType
from typing import Any


def _disable_implicit_pydantic_plugins() -> None:
    """Disable the implicit Logfire Pydantic plugin auto-loader.

    Pydantic v2 loads plugins via the ``PYDANTIC_DISABLE_PLUGINS`` environment
    variable and plugin entry-point names. We configure Logfire explicitly in
    our runtime entrypoints, so the implicit ``logfire-plugin`` should never be
    active during module import.
    """

    disabled = str(os.environ.get("PYDANTIC_DISABLE_PLUGINS") or "").strip()
    if not disabled:
        os.environ["PYDANTIC_DISABLE_PLUGINS"] = "logfire-plugin"
        return
    tokens = [token.strip() for token in disabled.split(",") if token.strip()]
    if "__all__" in tokens or "logfire-plugin" in tokens:
        return
    tokens.append("logfire-plugin")
    os.environ["PYDANTIC_DISABLE_PLUGINS"] = ",".join(tokens)


def _logfire_token_present() -> bool:
    return bool(str(os.getenv("LOGFIRE_TOKEN") or "").strip())


_LOGFIRE_RUNTIME_STATE: dict[str, Any] = {
    "mode": "disabled",
    "token_present": _logfire_token_present(),
    "error": None,
    "reason": None,
}


def _set_logfire_runtime_state(
    mode: str,
    *,
    error: str | None = None,
    reason: str | None = None,
) -> None:
    _LOGFIRE_RUNTIME_STATE["mode"] = mode
    _LOGFIRE_RUNTIME_STATE["token_present"] = _logfire_token_present()
    _LOGFIRE_RUNTIME_STATE["error"] = error
    _LOGFIRE_RUNTIME_STATE["reason"] = reason


def get_logfire_runtime_state() -> dict[str, Any]:
    """Return the current runtime tracing mode for health/status surfaces."""

    state = dict(_LOGFIRE_RUNTIME_STATE)
    state["token_present"] = _logfire_token_present()
    return state


class _NoopSpan:
    def __enter__(self) -> "_NoopSpan":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False

    async def __aenter__(self) -> "_NoopSpan":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


@dataclass
class _StubScrubMatch:
    path: Any = None
    value: Any = None


@dataclass
class _StubScrubbingOptions:
    callback: Any = None


class _StubLogfireQueryClient:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.available = False

    def query_json_rows(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return []


def _install_logfire_fail_open_stub() -> None:
    """Make observability fail-open instead of crashing service startup.

    Logfire is optional. If it cannot import because its OpenTelemetry runtime
    context is broken, the application should still boot without tracing.
    """

    if "logfire" in sys.modules:
        existing = sys.modules["logfire"]
        if getattr(existing, "__ua_stub__", False):
            _set_logfire_runtime_state(
                "stub",
                error="StubActive",
                reason=str(getattr(existing, "__ua_stub_error__", "")) or None,
            )
        else:
            _set_logfire_runtime_state("real" if _logfire_token_present() else "disabled")
        return

    try:
        __import__("logfire")
        _set_logfire_runtime_state("real" if _logfire_token_present() else "disabled")
        return
    except BaseException as exc:
        bootstrap_logger = logging.getLogger("universal_agent.bootstrap")

        stub = ModuleType("logfire")
        stub.__dict__.update(
            {
                "__doc__": "Universal Agent fail-open stub for optional Logfire observability.",
                "__ua_stub__": True,
                "__ua_stub_error__": repr(exc),
                "ScrubMatch": _StubScrubMatch,
                "ScrubbingOptions": _StubScrubbingOptions,
                "configure": lambda *args, **kwargs: None,
                "info": lambda *args, **kwargs: None,
                "warning": lambda *args, **kwargs: None,
                "warn": lambda *args, **kwargs: None,
                "error": lambda *args, **kwargs: None,
                "instrument_httpx": lambda *args, **kwargs: None,
                "instrument_mcp": lambda *args, **kwargs: None,
                "instrument_sqlite3": lambda *args, **kwargs: None,
                "install_auto_tracing": lambda *args, **kwargs: None,
                "set_baggage": lambda *args, **kwargs: None,
                "span": lambda *args, **kwargs: _NoopSpan(),
            }
        )

        query_client_stub = ModuleType("logfire.query_client")
        query_client_stub.LogfireQueryClient = _StubLogfireQueryClient

        sys.modules["logfire"] = stub
        sys.modules["logfire.query_client"] = query_client_stub
        _set_logfire_runtime_state(
            "stub",
            error=type(exc).__name__,
            reason=str(exc) or repr(exc),
        )
        bootstrap_logger.warning(
            "Logfire import failed during package bootstrap; using no-op stub",
            exc_info=exc,
        )


_disable_implicit_pydantic_plugins()
_install_logfire_fail_open_stub()

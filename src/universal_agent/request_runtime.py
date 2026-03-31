"""Per-request runtime context for internal tool policy enforcement."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RequestRuntimeContext:
    session_id: str = ""
    workspace_dir: str = ""
    source: str = ""
    run_kind: str = ""
    user_input: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


_REQUEST_RUNTIME: ContextVar[Optional[RequestRuntimeContext]] = ContextVar(
    "_REQUEST_RUNTIME",
    default=None,
)


def get_request_runtime() -> Optional[RequestRuntimeContext]:
    return _REQUEST_RUNTIME.get()


def set_request_runtime(ctx: RequestRuntimeContext) -> Token:
    return _REQUEST_RUNTIME.set(ctx)


def reset_request_runtime(token: Token) -> None:
    _REQUEST_RUNTIME.reset(token)

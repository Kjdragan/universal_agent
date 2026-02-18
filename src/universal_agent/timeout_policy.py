"""Central timeout and websocket tuning policy for UA services.

This module keeps runtime timeout knobs discoverable and consistent across
Telegram, gateway, API bridges, and websocket transport.
"""

from __future__ import annotations

import inspect
import os
from typing import Any, Callable


def _read_float(
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    raw = os.getenv(name)
    try:
        value = float((raw or "").strip()) if raw is not None else float(default)
    except ValueError:
        value = float(default)
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _read_positive_float_or_none(name: str, default: float) -> float | None:
    raw = os.getenv(name)
    if raw is None:
        value = float(default)
    else:
        text = raw.strip().lower()
        if text in {"0", "off", "none", "disabled", "false"}:
            return None
        try:
            value = float(text)
        except ValueError:
            value = float(default)
    if value <= 0:
        return None
    return value


def telegram_task_timeout_seconds(default: float = 1800.0) -> float:
    return _read_float(
        "UA_TELEGRAM_TASK_TIMEOUT_SECONDS",
        default,
        minimum=1.0,
    )


def process_turn_timeout_seconds(default: float = 0.0) -> float:
    # 0 keeps existing "no hard timeout" semantics.
    return _read_float(
        "UA_PROCESS_TURN_TIMEOUT_SECONDS",
        default,
        minimum=0.0,
    )


def gateway_http_timeout_seconds(default: float = 60.0) -> float:
    return _read_float("UA_GATEWAY_HTTP_TIMEOUT_SECONDS", default, minimum=1.0)


def gateway_owner_lookup_timeout_seconds(default: float = 20.0) -> float:
    return _read_float("UA_API_GATEWAY_OWNER_TIMEOUT_SECONDS", default, minimum=1.0)


def gateway_ws_handshake_timeout_seconds(default: float = 20.0) -> float:
    return _read_float("UA_GATEWAY_WS_HANDSHAKE_TIMEOUT_SECONDS", default, minimum=1.0)


def gateway_ws_send_timeout_seconds(default: float = 8.0) -> float:
    return _read_float("UA_WS_SEND_TIMEOUT_SECONDS", default, minimum=0.1)


def session_cancel_wait_seconds(default: float = 10.0) -> float:
    return _read_float("UA_SESSION_CANCEL_WAIT_SECONDS", default, minimum=0.1)


def websocket_transport_tuning() -> dict[str, float | None]:
    return {
        "open_timeout": _read_positive_float_or_none(
            "UA_GATEWAY_WS_OPEN_TIMEOUT_SECONDS", 20.0
        ),
        "close_timeout": _read_positive_float_or_none(
            "UA_GATEWAY_WS_CLOSE_TIMEOUT_SECONDS", 10.0
        ),
        "ping_interval": _read_positive_float_or_none(
            "UA_GATEWAY_WS_PING_INTERVAL_SECONDS", 20.0
        ),
        "ping_timeout": _read_positive_float_or_none(
            "UA_GATEWAY_WS_PING_TIMEOUT_SECONDS", 20.0
        ),
    }


def websocket_connect_kwargs(connect_callable: Callable[..., Any]) -> dict[str, Any]:
    """Return compatible kwargs for ``websockets.connect`` across versions."""
    try:
        params = inspect.signature(connect_callable).parameters
    except (TypeError, ValueError):
        params = {}

    kwargs: dict[str, Any] = {}
    for key, value in websocket_transport_tuning().items():
        if value is None:
            continue
        if key in params:
            kwargs[key] = value
    return kwargs

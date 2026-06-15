"""Central timeout and websocket tuning policy for UA services.

This module keeps runtime timeout knobs discoverable and consistent across
Telegram, gateway, API bridges, and websocket transport.
"""

from __future__ import annotations

import inspect
import os
import time
from typing import Any, Callable, Optional


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
    # 0 keeps existing "no hard timeout" semantics. This is the LEGACY explicit
    # hard-cap escape hatch; the default control is now the idle watchdog below.
    return _read_float(
        "UA_PROCESS_TURN_TIMEOUT_SECONDS",
        default,
        minimum=0.0,
    )


def process_turn_idle_kill_seconds(default: float = 600.0) -> float:
    """Idle / no-progress kill threshold for the in-process ProcessTurnAdapter
    (Simone heartbeat / daemon + in-process VP coder).

    A turn is killed only after this many seconds with NO sign of life (no
    event) AND no tool in flight. Idle-based — **NOT** a wall-clock cap — so a
    long-but-progressing turn runs freely. ``0`` disables. Default 600 s
    (10 min), matching the subprocess lane's ``vp_no_progress_kill_seconds`` so
    both lanes share one number. See :class:`LivenessWatchdog`.
    """
    return _read_float("UA_PROCESS_TURN_IDLE_KILL_SECONDS", default, minimum=0.0)


def process_turn_absolute_backstop_seconds(default: float = 7200.0) -> float:
    """Absolute last-resort ceiling for a fully-wedged in-process turn.

    Catches a turn whose tool never returns and whose own tool timeout also
    failed — i.e. a genuinely hung process the idle watchdog can't see (idle is
    suspended while a tool is in flight). Very high by design (default 7200 s =
    2 h); the idle watchdog is the primary control. ``0`` disables.
    """
    return _read_float(
        "UA_PROCESS_TURN_ABSOLUTE_BACKSTOP_SECONDS", default, minimum=0.0
    )


class LivenessWatchdog:
    """Canonical idle / no-progress kill policy for UA agent-execution lanes.

    UA runs agent turns on three lanes that all consume the same kind of
    progress signal and must all decide *when to kill a turn* the same way:

    * the in-process SDK adapter
      (``execution_engine.py::ProcessTurnAdapter.execute`` — Simone heartbeat /
      daemon + in-process VP coder),
    * the VP SDK event consumer
      (``vp/clients/base.py::consume_adapter_events_with_idle_timeout``),
    * the external ``claude --print`` subprocess
      (``vp/clients/claude_cli_client.py::_monitor_cli_output``).

    This watchdog is the ONE shared convention. The rule (operator requirement,
    2026-06-14): **never kill a live, working turn on an arbitrary wall-clock
    cap.** A turn is killed only when:

    * it has shown no sign of life for ``idle_kill_seconds`` AND is not waiting
      on an in-flight tool — a long build/test legitimately emits no inference
      output between its ``tool_call`` and ``tool_result``, so idle time while a
      tool runs is exempt (tools carry their own timeouts); or
    * the ``absolute_backstop_seconds`` ceiling is hit — a last resort for a
      fully-wedged process where even tool timeouts fail (very high by design);
      or
    * an explicit ``hard_cap_seconds`` is set by the caller (e.g. a cron's
      per-job ``timeout_seconds`` budget) and elapsed time exceeds it.

    "Sign of life" = any event/output from the running agent. Call
    :meth:`note_activity` on every event (with ``tool_started`` /
    ``tool_finished`` derived from TOOL_CALL / TOOL_RESULT), and poll
    :meth:`overdue` to learn whether the turn should be killed now. Size the
    poll/readline timeout with :meth:`seconds_until_due` so the loop never
    oversleeps a deadline.

    Do NOT add a bare wall-clock cap to any execution lane — use this watchdog.
    See ``project_docs/02_execution_core/01_gateway_sessions_execution.md``
    (§ "Liveness / no-progress timeout (NOT a hard wall-clock cap)").
    """

    def __init__(
        self,
        *,
        idle_kill_seconds: float,
        absolute_backstop_seconds: float = 0.0,
        hard_cap_seconds: float = 0.0,
        now: Optional[Callable[[], float]] = None,
    ) -> None:
        self._idle_kill_s = max(0.0, float(idle_kill_seconds or 0.0))
        self._backstop_s = max(0.0, float(absolute_backstop_seconds or 0.0))
        self._hard_cap_s = max(0.0, float(hard_cap_seconds or 0.0))
        self._now = now or time.monotonic
        t = self._now()
        self._start = t
        self._last_activity = t
        self._tools_in_flight = 0

    @property
    def tools_in_flight(self) -> int:
        return self._tools_in_flight

    def idle_seconds(self) -> float:
        """Seconds since the last sign of life — for observability / payloads."""
        return self._now() - self._last_activity

    def note_activity(
        self, *, tool_started: bool = False, tool_finished: bool = False
    ) -> None:
        """Record a sign of life (resets the idle window).

        ``tool_started`` / ``tool_finished`` adjust the in-flight-tool counter
        so idle time while a tool runs is exempt from the idle kill. The counter
        is floored at 0 so a stray ``tool_finished`` (or a TOOL_RESULT whose
        TOOL_CALL was missed) can never drive it negative and silently disarm
        the idle kill forever.
        """
        self._last_activity = self._now()
        if tool_started:
            self._tools_in_flight += 1
        if tool_finished and self._tools_in_flight > 0:
            self._tools_in_flight -= 1

    def overdue(self) -> Optional[str]:
        """Return a human-readable kill reason if the turn should be killed
        now, else ``None``. Pure read — safe to call every poll tick."""
        t = self._now()
        elapsed = t - self._start
        if self._hard_cap_s > 0 and elapsed >= self._hard_cap_s:
            return f"hard cap {self._hard_cap_s:.0f}s exceeded"
        if self._backstop_s > 0 and elapsed >= self._backstop_s:
            return f"absolute backstop {self._backstop_s:.0f}s exceeded"
        if self._idle_kill_s > 0 and self._tools_in_flight == 0:
            idle = t - self._last_activity
            if idle >= self._idle_kill_s:
                return (
                    f"no progress for {idle:.0f}s "
                    f"(idle kill {self._idle_kill_s:.0f}s, no tool in flight)"
                )
        return None

    def seconds_until_due(self) -> float:
        """Seconds until the soonest kill condition could fire given current
        state. Size a poll/readline timeout with this so the loop wakes exactly
        when a decision is needed and never oversleeps. ``inf`` when nothing is
        armed (idle kill suspended by an in-flight tool and no cap/backstop)."""
        t = self._now()
        candidates: list[float] = []
        if self._hard_cap_s > 0:
            candidates.append(self._start + self._hard_cap_s - t)
        if self._backstop_s > 0:
            candidates.append(self._start + self._backstop_s - t)
        if self._idle_kill_s > 0 and self._tools_in_flight == 0:
            candidates.append(self._last_activity + self._idle_kill_s - t)
        if not candidates:
            return float("inf")
        return max(0.0, min(candidates))


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

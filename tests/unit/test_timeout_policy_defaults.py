"""Regression tests for the named default constants in ``timeout_policy``.

The env-var reader functions (telegram / process-turn / cron / gateway / WS
transport timeouts) historically had no direct coverage: their default values
were bare numeric literals scattered across the module. After extracting those
literals into named module constants, these tests pin that:

* each reader returns its named ``DEFAULT_*`` constant when the env var is unset,
* the ``UA_*`` override still wins,
* the numeric floor (``minimum=``) is still enforced, and
* the ``_DISABLE_SENTINELS`` ("off"/"none"/...) tokens still disable a
  positive-float-or-none reader.

These are behavior-preserving guards: if a future edit accidentally re-couples
two coincidentally-equal defaults (e.g. the five websocket knobs that share
``20.0``) or drifts a documented value, a test fails here.
"""

from __future__ import annotations

import pytest

from universal_agent import timeout_policy as tp

# (reader, env_var_name, named_default_constant)
# One entry per public env-var reader so the default↔constant binding is pinned
# explicitly rather than via an opaque dict.
_READER_CASES = [
    (tp.telegram_task_timeout_seconds, "UA_TELEGRAM_TASK_TIMEOUT_SECONDS", tp.DEFAULT_TELEGRAM_TASK_TIMEOUT_SECONDS),
    (tp.process_turn_timeout_seconds, "UA_PROCESS_TURN_TIMEOUT_SECONDS", tp.DEFAULT_PROCESS_TURN_TIMEOUT_SECONDS),
    (tp.process_turn_idle_kill_seconds, "UA_PROCESS_TURN_IDLE_KILL_SECONDS", tp.DEFAULT_PROCESS_TURN_IDLE_KILL_SECONDS),
    (tp.process_turn_absolute_backstop_seconds, "UA_PROCESS_TURN_ABSOLUTE_BACKSTOP_SECONDS", tp.DEFAULT_PROCESS_TURN_ABSOLUTE_BACKSTOP_SECONDS),
    (tp.cron_script_idle_kill_seconds, "UA_CRON_SCRIPT_IDLE_KILL_SECONDS", tp.DEFAULT_CRON_SCRIPT_IDLE_KILL_SECONDS),
    (tp.cron_pre_spawn_timeout_seconds, "UA_CRON_PRE_SPAWN_TIMEOUT_SECONDS", tp.DEFAULT_CRON_PRE_SPAWN_TIMEOUT_SECONDS),
    (tp.gateway_http_timeout_seconds, "UA_GATEWAY_HTTP_TIMEOUT_SECONDS", tp.DEFAULT_GATEWAY_HTTP_TIMEOUT_SECONDS),
    (tp.gateway_owner_lookup_timeout_seconds, "UA_API_GATEWAY_OWNER_TIMEOUT_SECONDS", tp.DEFAULT_GATEWAY_OWNER_LOOKUP_TIMEOUT_SECONDS),
    (tp.gateway_ws_handshake_timeout_seconds, "UA_GATEWAY_WS_HANDSHAKE_TIMEOUT_SECONDS", tp.DEFAULT_GATEWAY_WS_HANDSHAKE_TIMEOUT_SECONDS),
    (tp.gateway_ws_send_timeout_seconds, "UA_WS_SEND_TIMEOUT_SECONDS", tp.DEFAULT_GATEWAY_WS_SEND_TIMEOUT_SECONDS),
    (tp.session_cancel_wait_seconds, "UA_SESSION_CANCEL_WAIT_SECONDS", tp.DEFAULT_SESSION_CANCEL_WAIT_SECONDS),
]


@pytest.mark.parametrize("reader,env_name,expected_default", _READER_CASES)
def test_reader_returns_named_default_when_env_unset(
    monkeypatch, reader, env_name, expected_default
):
    monkeypatch.delenv(env_name, raising=False)
    assert reader() == expected_default


@pytest.mark.parametrize("reader,env_name", [(r, e) for r, e, _ in _READER_CASES])
def test_env_override_wins(monkeypatch, reader, env_name):
    monkeypatch.setenv(env_name, "1234.5")
    assert reader() == 1234.5


def test_gateway_ws_send_floor_enforced(monkeypatch):
    """The 0.1s minimum floor clamps an out-of-spec override."""
    monkeypatch.setenv("UA_WS_SEND_TIMEOUT_SECONDS", "0.001")
    assert tp.gateway_ws_send_timeout_seconds() == 0.1


def test_telegram_minimum_floor_enforced(monkeypatch):
    """The 1.0s minimum floor clamps a sub-second override."""
    monkeypatch.setenv("UA_TELEGRAM_TASK_TIMEOUT_SECONDS", "0.2")
    assert tp.telegram_task_timeout_seconds() == 1.0


def test_named_defaults_match_documented_values():
    """Pin the actual documented magic numbers so a silent value drift fails
    the suite. Update this mapping only if the default is intentionally
    changed (and update the docstring in timeout_policy.py to match)."""
    assert tp.DEFAULT_TELEGRAM_TASK_TIMEOUT_SECONDS == 1800.0
    assert tp.DEFAULT_PROCESS_TURN_TIMEOUT_SECONDS == 0.0
    assert tp.DEFAULT_PROCESS_TURN_IDLE_KILL_SECONDS == 600.0
    assert tp.DEFAULT_PROCESS_TURN_ABSOLUTE_BACKSTOP_SECONDS == 7200.0
    assert tp.DEFAULT_CRON_SCRIPT_IDLE_KILL_SECONDS == 60.0
    assert tp.DEFAULT_CRON_PRE_SPAWN_TIMEOUT_SECONDS == 30.0
    assert tp.DEFAULT_GATEWAY_HTTP_TIMEOUT_SECONDS == 60.0
    assert tp.DEFAULT_GATEWAY_OWNER_LOOKUP_TIMEOUT_SECONDS == 20.0
    assert tp.DEFAULT_GATEWAY_WS_HANDSHAKE_TIMEOUT_SECONDS == 20.0
    assert tp.DEFAULT_GATEWAY_WS_SEND_TIMEOUT_SECONDS == 8.0
    assert tp.DEFAULT_SESSION_CANCEL_WAIT_SECONDS == 10.0
    assert tp.DEFAULT_GATEWAY_WS_OPEN_TIMEOUT_SECONDS == 20.0
    assert tp.DEFAULT_GATEWAY_WS_CLOSE_TIMEOUT_SECONDS == 10.0
    assert tp.DEFAULT_GATEWAY_WS_PING_INTERVAL_SECONDS == 20.0
    assert tp.DEFAULT_GATEWAY_WS_PING_TIMEOUT_SECONDS == 20.0


def test_websocket_transport_tuning_defaults(monkeypatch):
    for name in (
        "UA_GATEWAY_WS_OPEN_TIMEOUT_SECONDS",
        "UA_GATEWAY_WS_CLOSE_TIMEOUT_SECONDS",
        "UA_GATEWAY_WS_PING_INTERVAL_SECONDS",
        "UA_GATEWAY_WS_PING_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    tuning = tp.websocket_transport_tuning()
    assert tuning == {
        "open_timeout": tp.DEFAULT_GATEWAY_WS_OPEN_TIMEOUT_SECONDS,
        "close_timeout": tp.DEFAULT_GATEWAY_WS_CLOSE_TIMEOUT_SECONDS,
        "ping_interval": tp.DEFAULT_GATEWAY_WS_PING_INTERVAL_SECONDS,
        "ping_timeout": tp.DEFAULT_GATEWAY_WS_PING_TIMEOUT_SECONDS,
    }


@pytest.mark.parametrize("token", ["0", "off", "none", "disabled", "false"])
def test_disable_sentinels_turn_off_ws_ping(monkeypatch, token):
    """Every disable token yields None (disabled) for a positive-float-or-none
    reader. Guards the extracted ``_DISABLE_SENTINELS`` set."""
    monkeypatch.setenv("UA_GATEWAY_WS_PING_INTERVAL_SECONDS", token)
    assert tp.websocket_transport_tuning()["ping_interval"] is None


def test_disable_sentinels_is_frozenset():
    """_DISABLE_SENTINELS must be immutable so a stray mutation can't quietly
    change disable semantics at a distance."""
    assert isinstance(tp._DISABLE_SENTINELS, frozenset)

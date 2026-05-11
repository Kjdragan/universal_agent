"""Unit tests for ``universal_agent.loop_control``.

The contract:

* Explicit ``UA_<NAME>_ENABLED`` always wins.
* In ``development`` stage, default is OFF.
* In ``production`` (or anything not ``development``), default is ``prod_default``.
* Unrecognized env values fall through to the default (with a warning).
"""
from __future__ import annotations

import logging

import pytest

from universal_agent.loop_control import (
    explain_loop_decision,
    is_development_runtime,
    should_run_loop,
)

# ─── is_development_runtime ────────────────────────────────────────────


@pytest.mark.parametrize(
    "stage,expected",
    [
        ("development", True),
        ("DEVELOPMENT", True),
        (" Development ", True),
        ("production", False),
        ("staging", False),
        ("", False),
        (None, False),
    ],
)
def test_is_development_runtime(monkeypatch, stage, expected) -> None:
    if stage is None:
        monkeypatch.delenv("UA_RUNTIME_STAGE", raising=False)
    else:
        monkeypatch.setenv("UA_RUNTIME_STAGE", stage)
    assert is_development_runtime() is expected


# ─── should_run_loop: explicit override wins ───────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [("1", True), ("true", True), ("YES", True), ("on", True), ("0", False), ("false", False), ("no", False), ("OFF", False)],
)
def test_explicit_override_wins_over_dev_default(monkeypatch, value, expected) -> None:
    """Even in dev, an explicit UA_<NAME>_ENABLED override is honored."""
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.setenv("UA_HEARTBEAT_AUTONOMOUS_ENABLED", value)
    assert should_run_loop("heartbeat_autonomous", prod_default=True) is expected


@pytest.mark.parametrize(
    "value,expected",
    [("1", True), ("true", True), ("0", False), ("false", False)],
)
def test_explicit_override_wins_over_prod_default(monkeypatch, value, expected) -> None:
    """An operator forcing OFF in prod is respected; same for forcing ON when prod_default=False."""
    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    monkeypatch.setenv("UA_FOO_ENABLED", value)
    assert should_run_loop("foo", prod_default=True) is expected
    assert should_run_loop("foo", prod_default=False) is expected


# ─── should_run_loop: dev default = OFF ────────────────────────────────


def test_dev_default_off_when_no_explicit_flag(monkeypatch) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.delenv("UA_HEARTBEAT_AUTONOMOUS_ENABLED", raising=False)
    assert should_run_loop("heartbeat_autonomous", prod_default=True) is False
    assert should_run_loop("heartbeat_autonomous", prod_default=False) is False


# ─── should_run_loop: prod uses prod_default ───────────────────────────


@pytest.mark.parametrize(
    "prod_default,expected",
    [(True, True), (False, False)],
)
def test_prod_uses_prod_default_when_no_explicit_flag(monkeypatch, prod_default, expected) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    monkeypatch.delenv("UA_FOO_ENABLED", raising=False)
    assert should_run_loop("foo", prod_default=prod_default) is expected


def test_no_runtime_stage_falls_back_to_prod_default(monkeypatch) -> None:
    """Belt and suspenders: if UA_RUNTIME_STAGE is unset, we're NOT in dev mode."""
    monkeypatch.delenv("UA_RUNTIME_STAGE", raising=False)
    monkeypatch.delenv("UA_FOO_ENABLED", raising=False)
    assert should_run_loop("foo", prod_default=True) is True
    assert should_run_loop("foo", prod_default=False) is False


# ─── name normalization ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "input_name,expected_var",
    [
        ("heartbeat", "UA_HEARTBEAT_ENABLED"),
        ("HEARTBEAT", "UA_HEARTBEAT_ENABLED"),
        ("heartbeat-autonomous", "UA_HEARTBEAT_AUTONOMOUS_ENABLED"),
        ("Heartbeat_Autonomous", "UA_HEARTBEAT_AUTONOMOUS_ENABLED"),
        (" idle_poll ", "UA_IDLE_POLL_ENABLED"),
    ],
)
def test_name_normalization(monkeypatch, input_name, expected_var) -> None:
    """The same loop name in different cases resolves to the same env var."""
    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    monkeypatch.setenv(expected_var, "0")
    assert should_run_loop(input_name, prod_default=True) is False


# ─── unrecognized values warn and fall through ─────────────────────────


def test_unrecognized_value_falls_through_in_prod(monkeypatch, caplog) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    monkeypatch.setenv("UA_FOO_ENABLED", "maybe")
    with caplog.at_level(logging.WARNING):
        assert should_run_loop("foo", prod_default=True) is True
    assert any("Unrecognized" in rec.message for rec in caplog.records)


def test_unrecognized_value_falls_through_in_dev(monkeypatch, caplog) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.setenv("UA_FOO_ENABLED", "maybe")
    with caplog.at_level(logging.WARNING):
        assert should_run_loop("foo", prod_default=True) is False


# ─── explain_loop_decision ─────────────────────────────────────────────


def test_explain_explicit_on(monkeypatch) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.setenv("UA_FOO_ENABLED", "1")
    msg = explain_loop_decision("foo", prod_default=True)
    assert "UA_FOO_ENABLED=1" in msg
    assert "ON" in msg
    assert "explicit" in msg


def test_explain_dev_default(monkeypatch) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.delenv("UA_FOO_ENABLED", raising=False)
    msg = explain_loop_decision("foo", prod_default=True)
    assert "development" in msg
    assert "OFF" in msg
    assert "dev default" in msg


def test_explain_prod_default(monkeypatch) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    monkeypatch.delenv("UA_FOO_ENABLED", raising=False)
    msg_on = explain_loop_decision("foo", prod_default=True)
    msg_off = explain_loop_decision("foo", prod_default=False)
    assert "ON" in msg_on
    assert "OFF" in msg_off
    assert "prod_default=True" in msg_on
    assert "prod_default=False" in msg_off

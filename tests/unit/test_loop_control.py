"""Unit tests for ``universal_agent.loop_control``.

The contract (post-2026-05-11 dev-hygiene update):

* In ``production`` (or anything not ``development``):
    - Explicit ``UA_<NAME>_ENABLED`` always wins.
    - Else ``prod_default``.
* In ``development``:
    - ``UA_DEV_<NAME>_FORCE_ON=1`` wins (operator opt-in).
    - Else ``UA_<NAME>_ENABLED=0/false`` wins (explicit off).
    - Else False (default OFF; **any truthy UA_<NAME>_ENABLED is IGNORED**
      in dev to defend against Infisical prod-parity injection).

Pre-2026-05-11 behavior honored truthy ``UA_<NAME>_ENABLED`` even in dev
as an "explicit override." That was correct for operator-set env vars but
wrong for Infisical-injected ones, so the contract was tightened.
"""
from __future__ import annotations

import logging

import pytest

from universal_agent.loop_control import (
    explain_loop_decision,
    is_development_runtime,
    report_dev_overrides,
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


# ─── Dev mode: UA_<NAME>_ENABLED=truthy is IGNORED ─────────────────────


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_dev_ignores_truthy_main_var(monkeypatch, value) -> None:
    """Defensive: Infisical injecting UA_HEARTBEAT_ENABLED=1 from prod parity
    should NOT make the heartbeat run in dev."""
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.setenv("UA_HEARTBEAT_ENABLED", value)
    monkeypatch.delenv("UA_DEV_HEARTBEAT_FORCE_ON", raising=False)
    assert should_run_loop("heartbeat", prod_default=True) is False


# ─── Dev mode: UA_<NAME>_ENABLED=falsy is HONORED ──────────────────────


@pytest.mark.parametrize("value", ["0", "false", "no", "OFF"])
def test_dev_honors_falsy_main_var(monkeypatch, value) -> None:
    """Operator-set UA_<NAME>_ENABLED=0 in dev is honored (explicit off)."""
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.setenv("UA_HEARTBEAT_ENABLED", value)
    monkeypatch.delenv("UA_DEV_HEARTBEAT_FORCE_ON", raising=False)
    assert should_run_loop("heartbeat", prod_default=True) is False


# ─── Dev mode: UA_DEV_<NAME>_FORCE_ON=1 opts-in ────────────────────────


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_dev_force_on_opts_in(monkeypatch, value) -> None:
    """Operator can opt a specific loop into dev by setting UA_DEV_<NAME>_FORCE_ON=1."""
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.setenv("UA_DEV_HEARTBEAT_FORCE_ON", value)
    monkeypatch.delenv("UA_HEARTBEAT_ENABLED", raising=False)
    assert should_run_loop("heartbeat", prod_default=True) is True


def test_dev_force_on_beats_main_var_false(monkeypatch) -> None:
    """UA_DEV_<NAME>_FORCE_ON=1 wins even when UA_<NAME>_ENABLED=0 is set."""
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.setenv("UA_DEV_HEARTBEAT_FORCE_ON", "1")
    monkeypatch.setenv("UA_HEARTBEAT_ENABLED", "0")
    assert should_run_loop("heartbeat", prod_default=True) is True


# ─── Dev mode: no flags set → OFF ──────────────────────────────────────


def test_dev_default_off_when_no_flag(monkeypatch) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.delenv("UA_HEARTBEAT_ENABLED", raising=False)
    monkeypatch.delenv("UA_DEV_HEARTBEAT_FORCE_ON", raising=False)
    assert should_run_loop("heartbeat", prod_default=True) is False
    assert should_run_loop("heartbeat", prod_default=False) is False


# ─── Prod mode: explicit override wins ─────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [("1", True), ("true", True), ("YES", True), ("on", True), ("0", False), ("false", False), ("no", False), ("OFF", False)],
)
def test_prod_explicit_override_wins(monkeypatch, value, expected) -> None:
    """In production, UA_<NAME>_ENABLED honors both truthy and falsy."""
    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    monkeypatch.setenv("UA_FOO_ENABLED", value)
    assert should_run_loop("foo", prod_default=True) is expected
    assert should_run_loop("foo", prod_default=False) is expected


# ─── Prod mode: no override → prod_default ─────────────────────────────


@pytest.mark.parametrize("prod_default,expected", [(True, True), (False, False)])
def test_prod_uses_prod_default(monkeypatch, prod_default, expected) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    monkeypatch.delenv("UA_FOO_ENABLED", raising=False)
    assert should_run_loop("foo", prod_default=prod_default) is expected


def test_no_runtime_stage_falls_back_to_prod_default(monkeypatch) -> None:
    """Belt and suspenders: UA_RUNTIME_STAGE unset → NOT dev mode → prod_default."""
    monkeypatch.delenv("UA_RUNTIME_STAGE", raising=False)
    monkeypatch.delenv("UA_FOO_ENABLED", raising=False)
    assert should_run_loop("foo", prod_default=True) is True
    assert should_run_loop("foo", prod_default=False) is False


# ─── Name normalization ────────────────────────────────────────────────


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
def test_name_normalization_prod(monkeypatch, input_name, expected_var) -> None:
    """Same loop name in different cases resolves to the same env var (prod)."""
    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    monkeypatch.setenv(expected_var, "0")
    assert should_run_loop(input_name, prod_default=True) is False


# ─── Unrecognized values fall through (prod only) ──────────────────────


def test_unrecognized_value_falls_through_in_prod(monkeypatch, caplog) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    monkeypatch.setenv("UA_FOO_ENABLED", "maybe")
    with caplog.at_level(logging.WARNING):
        assert should_run_loop("foo", prod_default=True) is True
    assert any("Unrecognized" in rec.message for rec in caplog.records)


def test_unrecognized_value_in_dev_is_treated_as_truthy_pollution(monkeypatch) -> None:
    """Unrecognized value in dev is ignored just like a truthy override would be."""
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.setenv("UA_FOO_ENABLED", "maybe")
    monkeypatch.delenv("UA_DEV_FOO_FORCE_ON", raising=False)
    assert should_run_loop("foo", prod_default=True) is False


# ─── explain_loop_decision ─────────────────────────────────────────────


def test_explain_dev_force_on(monkeypatch) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.setenv("UA_DEV_FOO_FORCE_ON", "1")
    msg = explain_loop_decision("foo", prod_default=True)
    assert "UA_DEV_FOO_FORCE_ON" in msg
    assert "ON" in msg
    assert "opt-in" in msg


def test_explain_dev_ignored_truthy(monkeypatch) -> None:
    """In dev, a truthy UA_<NAME>_ENABLED is reported as ignored."""
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.setenv("UA_FOO_ENABLED", "1")
    monkeypatch.delenv("UA_DEV_FOO_FORCE_ON", raising=False)
    msg = explain_loop_decision("foo", prod_default=True)
    assert "IGNORED" in msg
    assert "OFF" in msg
    assert "Infisical" in msg


def test_explain_dev_explicit_off(monkeypatch) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.setenv("UA_FOO_ENABLED", "0")
    monkeypatch.delenv("UA_DEV_FOO_FORCE_ON", raising=False)
    msg = explain_loop_decision("foo", prod_default=True)
    assert "explicit OFF" in msg


def test_explain_dev_default(monkeypatch) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.delenv("UA_FOO_ENABLED", raising=False)
    monkeypatch.delenv("UA_DEV_FOO_FORCE_ON", raising=False)
    msg = explain_loop_decision("foo", prod_default=True)
    assert "dev default" in msg
    assert "OFF" in msg


def test_explain_prod_explicit(monkeypatch) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    monkeypatch.setenv("UA_FOO_ENABLED", "1")
    msg = explain_loop_decision("foo", prod_default=True)
    assert "UA_FOO_ENABLED=1" in msg
    assert "ON" in msg
    assert "explicit" in msg


def test_explain_prod_default(monkeypatch) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    monkeypatch.delenv("UA_FOO_ENABLED", raising=False)
    msg_on = explain_loop_decision("foo", prod_default=True)
    msg_off = explain_loop_decision("foo", prod_default=False)
    assert "ON" in msg_on
    assert "OFF" in msg_off


# ─── report_dev_overrides ──────────────────────────────────────────────


def test_report_dev_overrides_no_op_in_prod(monkeypatch, caplog) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "production")
    with caplog.at_level(logging.INFO):
        report_dev_overrides()
    # No lines about loop_control should appear.
    relevant = [r for r in caplog.records if "loop_control" in r.message]
    assert relevant == []


def test_report_dev_overrides_warns_on_truthy_main_var(monkeypatch, caplog) -> None:
    """When Infisical injects UA_<NAME>_ENABLED=1 in dev, the report warns."""
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.setenv("UA_HEARTBEAT_ENABLED", "1")
    monkeypatch.delenv("UA_DEV_HEARTBEAT_FORCE_ON", raising=False)
    with caplog.at_level(logging.WARNING):
        report_dev_overrides()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("UA_HEARTBEAT_ENABLED" in r.message and "IGNORED" in r.message for r in warnings)


def test_report_dev_overrides_lists_opt_ins(monkeypatch, caplog) -> None:
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    monkeypatch.setenv("UA_DEV_HEARTBEAT_FORCE_ON", "1")
    with caplog.at_level(logging.INFO):
        report_dev_overrides()
    assert any("opted-in" in r.message and "heartbeat" in r.message for r in caplog.records)


def test_report_dev_overrides_clean_dev(monkeypatch, caplog) -> None:
    """Clean dev (no Infisical pollution, no opt-ins) reports nothing scary."""
    monkeypatch.setenv("UA_RUNTIME_STAGE", "development")
    # Clear all known loop env vars
    for name in (
        "HEARTBEAT", "HEARTBEAT_AUTONOMOUS", "CRON", "CRON_REGISTRATION",
        "IDLE_POLL", "DISPATCH_STALE_SWEEP", "DAEMON_SESSIONS",
        "VP_EVENT_BRIDGE", "VP_STALE_RECONCILE", "AGENTMAIL_SERVICE",
        "NOTIFICATION_DISPATCHER", "YOUTUBE_PLAYLIST_WATCHER", "HQ_SELF_HEARTBEAT",
    ):
        monkeypatch.delenv(f"UA_{name}_ENABLED", raising=False)
        monkeypatch.delenv(f"UA_DEV_{name}_FORCE_ON", raising=False)
    with caplog.at_level(logging.INFO):
        report_dev_overrides()
    assert any("no dev opt-ins" in r.message for r in caplog.records)

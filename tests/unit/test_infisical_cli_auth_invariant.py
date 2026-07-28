"""Tests for the Infisical CLI auth-freshness invariant.

Locks the four behaviours that matter operationally:
- green CLI call (exit 0, or non-zero WITHOUT an auth signature) -> None (quiet)
- expired/unauthenticated response (403 / 'token has expired')   -> critical finding
- transient failure (timeout / OSError)                          -> None (fail open)
- kill-switch / not-prod / binary-missing                         -> None (not applicable)

All subprocess / host checks are patched so the test never shells out and is
host-independent.
"""

from __future__ import annotations

import importlib
from typing import Optional

import pytest

from universal_agent.services import pipeline_invariants as pi
from universal_agent.services.invariants import proactive_pipeline_invariants as ppi
from universal_agent.services.pipeline_invariants import (
    clear_registry_for_tests,
    run_invariants,
)

INV_ID = "infisical_cli_auth_fresh"


@pytest.fixture(autouse=True)
def _fresh_registry():
    clear_registry_for_tests()
    importlib.reload(ppi)
    yield
    clear_registry_for_tests()


class _FakeResult:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture(autouse=True)
def _force_applicable(monkeypatch):
    """Pretend we are on the prod VPS for every test."""
    monkeypatch.setattr(ppi, "_infisical_cli_auth_applicable", lambda: True)
    monkeypatch.setattr(ppi.shutil, "which", lambda name: "/usr/bin/infisical")


def _patch_run(monkeypatch, *, result: Optional[_FakeResult] = None, raises=None) -> None:
    def _run(*_args, **_kwargs):
        if raises is not None:
            raise raises
        return result

    monkeypatch.setattr(ppi.subprocess, "run", _run)


def _probe(ctx=None):
    return ppi.infisical_cli_auth_fresh(ctx or {})


def test_green_exit_zero_returns_none(monkeypatch):
    _patch_run(monkeypatch, result=_FakeResult(0, stdout=""))
    assert _probe() is None


def test_secret_not_found_is_healthy(monkeypatch):
    # Nonexistent key on a valid token -> non-zero, NO auth signature -> green.
    _patch_run(
        monkeypatch,
        result=_FakeResult(1, stderr="Error: secret '__ua_infisical_auth_probe__' was not found"),
    )
    assert _probe() is None


@pytest.mark.parametrize("stderr", [
    "Error: token has expired (403)",
    "Failed to authenticate: 403 Forbidden",
    "Unauthorized: invalid token",
])
def test_expired_token_fires_critical(monkeypatch, stderr):
    _patch_run(monkeypatch, result=_FakeResult(1, stderr=stderr))
    finding = _probe()
    assert finding is not None
    # The decorator declares severity=critical and the probe does not override.
    assert pi._REGISTRY[INV_ID].severity == "critical"
    assert finding["observed_value"]["matched_signatures"]
    assert finding["observed_value"]["returncode"] == 1
    assert "infisical login" in finding["message"].lower()


def test_timeout_fails_open(monkeypatch):
    _patch_run(monkeypatch, raises=ppi.subprocess.TimeoutExpired(["infisical"], 15))
    assert _probe() is None


def test_oserror_fails_open(monkeypatch):
    _patch_run(monkeypatch, raises=OSError("spawn failed"))
    assert _probe() is None


def test_unknown_error_fails_open(monkeypatch):
    # Non-zero exit but NO auth signature -> treat as non-auth, stay quiet.
    _patch_run(monkeypatch, result=_FakeResult(2, stderr="some unrelated cli error"))
    assert _probe() is None


def test_kill_switch_disables(monkeypatch):
    monkeypatch.setenv("UA_INVARIANT_INFISICAL_CLI_AUTH", "0")
    # Even with a 403 staring us in the face, disabled -> None.
    _patch_run(monkeypatch, result=_FakeResult(1, stderr="token has expired"))
    assert _probe() is None


def test_binary_missing_is_not_applicable(monkeypatch):
    monkeypatch.setattr(ppi.shutil, "which", lambda name: None)
    _patch_run(monkeypatch, result=_FakeResult(1, stderr="token has expired"))
    assert _probe() is None


def test_not_prod_is_not_applicable(monkeypatch):
    monkeypatch.setattr(ppi, "_infisical_cli_auth_applicable", lambda: False)
    _patch_run(monkeypatch, result=_FakeResult(1, stderr="token has expired"))
    assert _probe() is None


def test_runner_emits_critical_finding(monkeypatch):
    """End-to-end through run_invariants(): an expired CLI surfaces as exactly
    one HeartbeatFinding at severity=critical with the invariant finding_id."""
    _patch_run(monkeypatch, result=_FakeResult(1, stderr="token has expired (403)"))
    findings = [f for f in run_invariants({}) if f.finding_id == f"invariant:{INV_ID}"]
    assert len(findings) == 1
    assert findings[0].severity == "critical"
    assert findings[0].category == "proactive_health"
    assert findings[0].runbook_command  # operator has a remediation command

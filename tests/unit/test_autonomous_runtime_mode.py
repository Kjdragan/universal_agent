"""The autonomous-runtime split flag: exactly one process hosts the loops."""

from __future__ import annotations

import importlib

import pytest

import universal_agent.loop_control as lc


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("UA_AUTONOMOUS_RUNTIME_MODE", raising=False)
    monkeypatch.delenv("UA_GATEWAY_ROLE", raising=False)
    return monkeypatch


def _set(monkeypatch, mode=None, role=None):
    if mode is None:
        monkeypatch.delenv("UA_AUTONOMOUS_RUNTIME_MODE", raising=False)
    else:
        monkeypatch.setenv("UA_AUTONOMOUS_RUNTIME_MODE", mode)
    if role is None:
        monkeypatch.delenv("UA_GATEWAY_ROLE", raising=False)
    else:
        monkeypatch.setenv("UA_GATEWAY_ROLE", role)


def test_default_is_in_process_gateway_hosts(clean_env):
    # No flags == today's behavior: the gateway hosts the loops.
    assert lc.autonomous_runtime_mode() == "in_process"
    assert lc.should_host_autonomous_runtime() is True


def test_unknown_mode_falls_back_to_in_process(clean_env):
    _set(clean_env, mode="garbage")
    assert lc.autonomous_runtime_mode() == "in_process"
    assert lc.should_host_autonomous_runtime() is True


@pytest.mark.parametrize(
    "mode,role,gateway_hosts,worker_hosts",
    [
        # mode,          role(for the OTHER process is implicit)
        ("in_process", None, True, None),  # default: gateway runs, no worker
        ("in_process", "autonomous_worker", None, False),  # stray worker idles
        ("split", None, False, None),  # gateway sheds
        ("split", "autonomous_worker", None, True),  # worker runs
    ],
)
def test_mutual_exclusion(clean_env, mode, role, gateway_hosts, worker_hosts):
    _set(clean_env, mode=mode, role=role)
    got = lc.should_host_autonomous_runtime()
    if gateway_hosts is not None:
        assert got is gateway_hosts
    if worker_hosts is not None:
        assert got is worker_hosts


def test_split_is_exactly_one_process(clean_env):
    # In split mode, gateway(False) XOR worker(True) — never both, never neither.
    _set(clean_env, mode="split", role=None)
    gateway = lc.should_host_autonomous_runtime()
    _set(clean_env, mode="split", role="autonomous_worker")
    worker = lc.should_host_autonomous_runtime()
    assert gateway is False and worker is True  # exactly one hosts

    # Same invariant in default mode.
    _set(clean_env, mode="in_process", role=None)
    gateway = lc.should_host_autonomous_runtime()
    _set(clean_env, mode="in_process", role="autonomous_worker")
    worker = lc.should_host_autonomous_runtime()
    assert gateway is True and worker is False

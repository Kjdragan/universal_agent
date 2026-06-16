"""Tests for the proactive-activity control plane (per-process on/off).

Security is the point of this surface (it shells ``systemctl`` from a web
endpoint), so the tests focus on: the hardcoded allowlist (core units NOT
controllable; proactive units are), action validation, argv-not-shell
construction, fail-soft state reads, and the auth-gated endpoints. ``systemctl``
is never actually shelled — ``subprocess.run`` is mocked.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest


def _fake_run_factory(record: list):
    """A ``subprocess.run`` stand-in: records every argv, answers ``show``
    queries with a parseable payload, and reports success for control verbs."""

    def fake_run(argv, **kwargs):
        record.append((argv, kwargs))
        if "show" in argv:
            stdout = (
                "ActiveState=inactive\nSubState=dead\nUnitFileState=enabled\n"
                "LastTriggerUSec=\nNextElapseUSecRealtime=\n"
            )
            return SimpleNamespace(returncode=0, stdout=stdout, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return fake_run


# ── Allowlist security ──────────────────────────────────────────────────────


def test_core_and_infra_units_not_controllable():
    from universal_agent.services import zai_activity_control as zac

    for unit in (
        "universal-agent-gateway.service",
        "universal-agent-api.service",
        "universal-agent-webui.service",
        "universal-agent-docs.service",
        "universal-agent-telegram.service",
        "universal-agent-service-watchdog.service",
        "universal-agent-service-watchdog.timer",
        "universal-agent-proactive-health.timer",
        "universal-agent-oom-alert.timer",
        "universal-agent-uv-cache-prune.timer",
        "universal-agent-youtube-oauth-watchdog.timer",
    ):
        assert not zac.is_allowed_unit(unit), f"{unit} must NOT be controllable"


def test_allowlist_is_disjoint_from_never_controllable():
    from universal_agent.services import zai_activity_control as zac

    assert set(zac.ALLOWLIST) & set(zac.NEVER_CONTROLLABLE) == set()


def test_expected_proactive_units_are_controllable():
    from universal_agent.services import zai_activity_control as zac

    for unit in (
        "universal-agent-csi-convergence-sync.timer",
        "universal-agent-hourly-intel-digest.timer",
        "universal-agent-proactive-demo-build-sweep.timer",
        "universal-agent-mission-control-sweeper.service",
        "universal-agent-vp-worker@vp.coder.primary.service",
        "universal-agent-vp-worker@vp.general.primary.service",
        "ua-discord-intelligence.service",
        "ua-discord-cc-bot.service",
    ):
        assert zac.is_allowed_unit(unit), f"{unit} should be controllable"


def test_action_allowlist():
    from universal_agent.services import zai_activity_control as zac

    for ok in ("start", "stop", "restart", "enable", "disable", "mask", "unmask"):
        assert zac.is_allowed_action(ok)
    for bad in ("", "rm", "poweroff", "kill", "daemon-reexec", "STOP", "stop;reboot", "isolate"):
        assert not zac.is_allowed_action(bad)


def test_watchdog_guarded_is_derived_from_watch_set():
    """The guard is structural: every allowlisted unit the service-watchdog
    monitors is mask-guarded, and nothing else is. Adding a watched unit to the
    allowlist auto-guards it — the class of bug where a watched unit gets only a
    plain `stop` (which the watchdog undoes) is impossible by construction."""
    from universal_agent.services import zai_activity_control as zac

    assert set(zac.ALLOWLIST) & set(zac.WATCHDOG_WATCH_SET) == set(zac.WATCHDOG_GUARDED_UNITS)
    assert "universal-agent-mission-control-sweeper.service" in zac.WATCHDOG_GUARDED_UNITS
    # csi-ingester is watched by the watchdog but intentionally NOT controllable
    # here, so it never reaches a plain-stop trap.
    assert "csi-ingester.service" in zac.WATCHDOG_WATCH_SET
    assert "csi-ingester.service" not in zac.ALLOWLIST


def test_watchdog_guarded_unit_uses_mask_for_off():
    """The mission-control-sweeper is watched by the service-watchdog, so a
    plain stop is fought; its declarative off must include mask."""
    from universal_agent.services import zai_activity_control as zac

    meta = zac.ALLOWLIST["universal-agent-mission-control-sweeper.service"]
    assert meta["watchdog_guarded"] is True
    assert meta["off_actions"] == ["stop", "mask"]
    assert meta["on_actions"] == ["unmask", "start"]
    # A non-guarded unit uses the simple policy.
    plain = zac.ALLOWLIST["universal-agent-backlog-triage.timer"]
    assert plain["watchdog_guarded"] is False
    assert plain["off_actions"] == ["stop"]
    assert plain["on_actions"] == ["start"]


# ── control_unit: argv (not shell) + validation ─────────────────────────────


def test_control_unit_builds_argv_never_shell(monkeypatch):
    from universal_agent.services import zai_activity_control as zac

    calls: list = []
    monkeypatch.setattr(zac.subprocess, "run", _fake_run_factory(calls))

    res = zac.control_unit("universal-agent-backlog-triage.timer", "stop")
    assert res["ok"] is True
    assert res["unit"] == "universal-agent-backlog-triage.timer"
    assert res["action"] == "stop"

    # First call is the control verb, exact argv, sudo non-interactive.
    control_argv = calls[0][0]
    assert control_argv == ["sudo", "-n", "systemctl", "stop", "universal-agent-backlog-triage.timer"]
    # Nothing ever runs through a shell.
    for argv, kwargs in calls:
        assert isinstance(argv, list)
        assert kwargs.get("shell") is not True


def test_control_unit_rejects_unknown_unit_and_action():
    from universal_agent.services import zai_activity_control as zac

    with pytest.raises(ValueError):
        zac.control_unit("universal-agent-gateway.service", "stop")
    with pytest.raises(ValueError):
        zac.control_unit("universal-agent-backlog-triage.timer", "reboot")


def test_control_unit_returns_structured_failure_on_nonzero(monkeypatch):
    from universal_agent.services import zai_activity_control as zac

    def fail_run(argv, **kwargs):
        if "show" in argv:
            return SimpleNamespace(returncode=0, stdout="ActiveState=active\nUnitFileState=enabled\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="Failed to stop: unit busy")

    monkeypatch.setattr(zac.subprocess, "run", fail_run)
    res = zac.control_unit("universal-agent-backlog-triage.timer", "stop")
    assert res["ok"] is False
    assert res["returncode"] == 1
    assert "unit busy" in res["stderr"]


# ── state reads fail soft ───────────────────────────────────────────────────


def test_get_unit_state_fails_soft(monkeypatch):
    from universal_agent.services import zai_activity_control as zac

    def boom(*args, **kwargs):
        raise OSError("systemctl not found")

    monkeypatch.setattr(zac.subprocess, "run", boom)
    st = zac.get_unit_state("universal-agent-backlog-triage.timer")
    assert st["active_state"] == "unknown"
    assert st["is_active"] is False
    # Metadata from the allowlist is still surfaced even when the read fails.
    assert st["group"] == "timers"
    assert st["label"]


def test_show_many_keys_records_by_id(monkeypatch):
    """The batched read parses systemctl's multi-unit, blank-line-separated
    output and keys each record back to its unit via the Id property."""
    from universal_agent.services import zai_activity_control as zac

    units = [
        "universal-agent-backlog-triage.timer",
        "universal-agent-mission-control-sweeper.service",
    ]
    out_text = (
        "Id=universal-agent-backlog-triage.timer\nActiveState=active\nSubState=waiting\n"
        "UnitFileState=enabled\nLastTriggerUSec=Thu 2026-06-11 19:00:04 UTC\n"
        "NextElapseUSecRealtime=Thu 2026-06-11 20:01:15 UTC\n"
        "\n"
        "Id=universal-agent-mission-control-sweeper.service\nActiveState=inactive\nSubState=dead\n"
        "UnitFileState=masked\nLastTriggerUSec=\nNextElapseUSecRealtime=\n"
    )
    monkeypatch.setattr(zac.subprocess, "run", lambda argv, **kw: SimpleNamespace(returncode=0, stdout=out_text, stderr=""))

    states = zac._show_many(units)
    assert set(states) == set(units)
    timer = states["universal-agent-backlog-triage.timer"]
    assert timer["is_active"] is True
    assert timer["next_run"].endswith("UTC")
    sweeper = states["universal-agent-mission-control-sweeper.service"]
    assert sweeper["is_masked"] is True
    assert sweeper["is_active"] is False


def test_list_activities_shape(monkeypatch):
    from universal_agent.services import zai_activity_control as zac

    calls: list = []
    monkeypatch.setattr(zac.subprocess, "run", _fake_run_factory(calls))
    payload = zac.list_activities()

    assert set(payload) >= {"activities", "inprocess", "actions_allowed", "watchdog_guarded_units"}
    assert len(payload["activities"]) == len(zac.ALLOWLIST)
    sweeper = next(
        a for a in payload["activities"] if a["unit"].endswith("mission-control-sweeper.service")
    )
    assert sweeper["watchdog_guarded"] is True
    assert sweeper["heavy_zai"] is True
    inproc_keys = {i["key"] for i in payload["inprocess"]}
    assert {"heartbeat", "cron"} <= inproc_keys


def test_inprocess_loops_surfaces_dispatch_state(monkeypatch):
    """M5 §2a — the in-process read-out includes the dispatch-redesign control
    surface (priority dispatcher / prefer-ATLAS / coupling gate), read-only, with
    the operator-facing env-var labels and the default Stage-A state."""
    from universal_agent.services import zai_activity_control as zac

    for var in (
        "UA_PRIORITY_DISPATCHER_ENABLED",
        "UA_DISPATCHER_PREFER_ATLAS",
        "UA_CRON_HEARTBEAT_WAKE_SELECTIVE",
        "UA_CRON_HEARTBEAT_WAKE_ALLOWLIST",
        "UA_CRON_HEARTBEAT_WAKE_MIN_INTERVAL_SECONDS",
    ):
        monkeypatch.delenv(var, raising=False)

    rows = {r["key"]: r for r in zac._inprocess_loops()}
    assert {
        "heartbeat",
        "cron",
        "priority_dispatcher",
        "prefer_atlas",
        "cron_heartbeat_coupling_gate",
    } <= set(rows)
    assert rows["priority_dispatcher"]["env_var"] == "UA_PRIORITY_DISPATCHER_ENABLED"
    assert rows["prefer_atlas"]["env_var"] == "UA_DISPATCHER_PREFER_ATLAS"
    assert rows["cron_heartbeat_coupling_gate"]["env_var"] == "UA_CRON_HEARTBEAT_WAKE_SELECTIVE"
    # Default state: dispatcher ON, prefer-ATLAS OFF (Stage A), selective gate ON.
    assert rows["priority_dispatcher"]["enabled"] is True
    assert rows["prefer_atlas"]["enabled"] is False
    assert rows["cron_heartbeat_coupling_gate"]["enabled"] is True
    # The coupling row's note reflects the empty allowlist + default debounce.
    note = rows["cron_heartbeat_coupling_gate"]["note"]
    assert "empty (no cron wakes Simone)" in note
    assert "Debounce: 300s" in note


# ── Gateway endpoints ───────────────────────────────────────────────────────


def _client(monkeypatch):
    """TestClient with both auth guards neutralized so the dispatch logic is
    under test (auth has its own real-401 test)."""
    from fastapi.testclient import TestClient

    gateway_server = importlib.import_module("universal_agent.gateway_server")
    monkeypatch.setattr(gateway_server, "_require_ops_auth", lambda *a, **k: None)
    monkeypatch.setattr(gateway_server, "_require_headquarters_role_for_fleet", lambda *a, **k: None)
    return TestClient(gateway_server.app), gateway_server


def test_activity_endpoints_registered():
    gateway_server = importlib.import_module("universal_agent.gateway_server")
    routes = {getattr(r, "path", None) for r in gateway_server.app.routes}
    assert "/api/v1/ops/zai/activities" in routes
    assert "/api/v1/ops/zai/activity-control" in routes


def test_activity_control_requires_auth():
    """WITHOUT neutralizing auth, the control endpoint is gated (the test env
    has an ops token set, so an unauthenticated call is 401)."""
    from fastapi.testclient import TestClient

    gateway_server = importlib.import_module("universal_agent.gateway_server")
    client = TestClient(gateway_server.app)
    resp = client.post(
        "/api/v1/ops/zai/activity-control",
        json={"unit": "universal-agent-backlog-triage.timer", "action": "stop"},
    )
    assert resp.status_code == 401


def test_activity_endpoints_fail_closed_on_vps_without_ops_auth(monkeypatch):
    """A sudo-shelling surface must fail CLOSED (503), not open, if ops auth is
    entirely unconfigured on the server profile (secrets-bootstrap miss)."""
    from fastapi.testclient import TestClient

    gateway_server = importlib.import_module("universal_agent.gateway_server")
    monkeypatch.setattr(gateway_server, "_DEPLOYMENT_PROFILE", "vps")
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "")
    monkeypatch.setattr(gateway_server, "OPS_JWT_SECRET", "")
    client = TestClient(gateway_server.app)

    resp = client.post(
        "/api/v1/ops/zai/activity-control",
        json={"unit": "universal-agent-backlog-triage.timer", "action": "stop"},
    )
    assert resp.status_code == 503
    resp2 = client.get("/api/v1/ops/zai/activities")
    assert resp2.status_code == 503


def test_activities_endpoint_returns_payload(monkeypatch):
    from universal_agent.services import zai_activity_control as zac

    client, _ = _client(monkeypatch)
    monkeypatch.setattr(zac.subprocess, "run", _fake_run_factory([]))
    resp = client.get("/api/v1/ops/zai/activities")
    assert resp.status_code == 200
    body = resp.json()
    assert "activities" in body and isinstance(body["activities"], list)


def test_activity_control_dispatch_stop(monkeypatch):
    from universal_agent.services import zai_activity_control as zac

    client, _ = _client(monkeypatch)
    calls: list = []
    monkeypatch.setattr(zac.subprocess, "run", _fake_run_factory(calls))
    resp = client.post(
        "/api/v1/ops/zai/activity-control",
        json={"unit": "universal-agent-backlog-triage.timer", "action": "stop"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "stop"
    assert ["sudo", "-n", "systemctl", "stop", "universal-agent-backlog-triage.timer"] in [c[0] for c in calls]


def test_activity_control_rejects_core_unit_without_shelling(monkeypatch):
    """A core unit must be rejected with 400 BEFORE any systemctl call."""
    from universal_agent.services import zai_activity_control as zac

    client, _ = _client(monkeypatch)
    calls: list = []
    monkeypatch.setattr(zac.subprocess, "run", _fake_run_factory(calls))
    resp = client.post(
        "/api/v1/ops/zai/activity-control",
        json={"unit": "universal-agent-gateway.service", "action": "stop"},
    )
    assert resp.status_code == 400
    assert calls == []  # validation rejected before any shell-out


def test_activity_control_rejects_bad_action(monkeypatch):
    from universal_agent.services import zai_activity_control as zac

    client, _ = _client(monkeypatch)
    calls: list = []
    monkeypatch.setattr(zac.subprocess, "run", _fake_run_factory(calls))
    resp = client.post(
        "/api/v1/ops/zai/activity-control",
        json={"unit": "universal-agent-backlog-triage.timer", "action": "reboot"},
    )
    assert resp.status_code == 400
    assert calls == []

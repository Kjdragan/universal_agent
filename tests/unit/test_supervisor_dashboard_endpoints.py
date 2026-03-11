from __future__ import annotations

import pytest
from fastapi import HTTPException

from universal_agent import gateway_server


@pytest.mark.asyncio
async def test_supervisor_registry_endpoint_returns_both_supervisors(monkeypatch):
    monkeypatch.setattr(gateway_server, "_require_ops_auth", lambda _request: None)
    monkeypatch.setattr(gateway_server, "_require_headquarters_role_for_fleet", lambda: None)

    payload = await gateway_server.dashboard_supervisors_registry(request=object())
    assert payload["status"] == "ok"
    ids = {row.get("id") for row in payload.get("supervisors", [])}
    assert "factory-supervisor" in ids
    assert "csi-supervisor" in ids


@pytest.mark.asyncio
async def test_supervisor_snapshot_endpoint_for_known_supervisors(monkeypatch):
    monkeypatch.setattr(gateway_server, "_require_ops_auth", lambda _request: None)
    monkeypatch.setattr(gateway_server, "_require_headquarters_role_for_fleet", lambda: None)

    async def _fake_snapshot(supervisor_id: str):
        return {
            "status": "ok",
            "supervisor_id": supervisor_id,
            "generated_at": "2026-03-10T00:00:00Z",
            "summary": "ok",
            "severity": "info",
            "kpis": {},
            "diagnostics": {},
            "recommendations": [],
            "artifacts": {},
        }

    monkeypatch.setattr(gateway_server, "_build_supervisor_snapshot", _fake_snapshot)
    monkeypatch.setattr(
        gateway_server,
        "_latest_supervisor_artifacts",
        lambda _sid: {"markdown_path": "", "json_path": ""},
    )

    factory_payload = await gateway_server.dashboard_supervisor_snapshot(
        request=object(), supervisor_id="factory-supervisor"
    )
    csi_payload = await gateway_server.dashboard_supervisor_snapshot(
        request=object(), supervisor_id="csi-supervisor"
    )

    assert factory_payload["supervisor_id"] == "factory-supervisor"
    assert csi_payload["supervisor_id"] == "csi-supervisor"


@pytest.mark.asyncio
async def test_supervisor_snapshot_unknown_returns_404(monkeypatch):
    monkeypatch.setattr(gateway_server, "_require_ops_auth", lambda _request: None)
    monkeypatch.setattr(gateway_server, "_require_headquarters_role_for_fleet", lambda: None)

    with pytest.raises(HTTPException) as exc:
        await gateway_server.dashboard_supervisor_snapshot(
            request=object(), supervisor_id="unknown-supervisor"
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_supervisor_run_persists_artifact_paths_and_emits_event(monkeypatch):
    monkeypatch.setattr(gateway_server, "_require_ops_auth", lambda _request: None)
    monkeypatch.setattr(gateway_server, "_require_headquarters_role_for_fleet", lambda: None)

    async def _fake_snapshot(_supervisor_id: str):
        return {
            "status": "ok",
            "supervisor_id": "factory-supervisor",
            "generated_at": "2026-03-10T00:00:00Z",
            "summary": "summary",
            "severity": "info",
            "kpis": {},
            "diagnostics": {},
            "recommendations": [],
            "artifacts": {},
        }

    emitted: dict[str, str] = {}

    def _fake_persist_snapshot(*, supervisor_id: str, snapshot: dict, artifacts_root):
        assert supervisor_id == "factory-supervisor"
        assert isinstance(snapshot, dict)
        return {
            "markdown_path": "/tmp/supervisor-briefs/factory-supervisor/brief.md",
            "json_path": "/tmp/supervisor-briefs/factory-supervisor/brief.json",
        }

    def _fake_record_supervisor_brief_event(*, supervisor_id: str, snapshot: dict, reason=None):
        emitted["supervisor_id"] = supervisor_id
        emitted["reason"] = str(reason or "")
        emitted["summary"] = str(snapshot.get("summary") or "")

    monkeypatch.setattr(gateway_server, "_build_supervisor_snapshot", _fake_snapshot)
    monkeypatch.setattr(gateway_server, "persist_snapshot", _fake_persist_snapshot)
    monkeypatch.setattr(gateway_server, "_record_supervisor_brief_event", _fake_record_supervisor_brief_event)

    payload = await gateway_server.dashboard_supervisor_run(
        request=object(),
        supervisor_id="factory-supervisor",
        payload=gateway_server.SupervisorRunRequest(reason="test-run"),
    )

    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
    assert str(artifacts.get("markdown_path") or "").endswith("brief.md")
    assert str(artifacts.get("json_path") or "").endswith("brief.json")
    assert emitted.get("supervisor_id") == "factory-supervisor"
    assert emitted.get("reason") == "test-run"


@pytest.mark.asyncio
async def test_supervisor_endpoints_enforce_headquarters_role(monkeypatch):
    monkeypatch.setattr(gateway_server, "_require_ops_auth", lambda _request: None)

    def _deny_hq():
        raise HTTPException(status_code=403, detail="HQ only")

    monkeypatch.setattr(gateway_server, "_require_headquarters_role_for_fleet", _deny_hq)

    with pytest.raises(HTTPException) as exc:
        await gateway_server.dashboard_supervisors_registry(request=object())
    assert exc.value.status_code == 403

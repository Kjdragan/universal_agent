from __future__ import annotations

from types import SimpleNamespace
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from universal_agent import gateway_server


class _ReconCronStub:
    def __init__(self, jobs: list[SimpleNamespace] | None = None):
        self._jobs = {str(job.job_id): job for job in (jobs or [])}
        self.updated: list[tuple[str, dict]] = []

    def list_jobs(self):
        return list(self._jobs.values())

    def get_job(self, job_id: str):
        return self._jobs.get(str(job_id))

    def update_job(self, job_id: str, updates: dict):
        self.updated.append((str(job_id), dict(updates)))
        job = self._jobs[str(job_id)]
        metadata = dict(getattr(job, "metadata", {}) or {})
        metadata.update(dict(updates.get("metadata", {})))
        updated = SimpleNamespace(
            job_id=str(job_id),
            metadata=metadata,
            created_at=float(getattr(job, "created_at", 0.0) or 0.0),
        )
        self._jobs[str(job_id)] = updated
        return updated


@pytest.fixture
def ops_client(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(gateway_server, "_gateway", None)
    monkeypatch.setattr(gateway_server, "_ops_service", None)
    monkeypatch.setattr(gateway_server, "_sessions", {})
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "")

    @asynccontextmanager
    async def _test_lifespan(app):
        yield

    monkeypatch.setattr(gateway_server.app.router, "lifespan_context", _test_lifespan)
    with TestClient(gateway_server.app) as c:
        yield c


def test_reconcile_removes_stale_mapping(monkeypatch, tmp_path):
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(gateway_server, "_cron_service", _ReconCronStub([]))

    gateway_server._todoist_chron_mapping_upsert(
        "task_stale_1",
        {"cron_job_id": "missing_job_1", "schedule_text": "tonight at 2am"},
    )

    result = gateway_server._reconcile_todoist_chron_mappings(remove_stale=True, dry_run=False)
    assert result["ok"] is True
    assert result["removed"] == 1
    assert gateway_server._todoist_chron_mapping_get("task_stale_1") is None


def test_reconcile_relinks_missing_job_from_task_index(monkeypatch, tmp_path):
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    cron_job = SimpleNamespace(
        job_id="cron_job_live_1",
        metadata={"todoist_task_id": "task_relink_1"},
        created_at=100.0,
    )
    monkeypatch.setattr(gateway_server, "_cron_service", _ReconCronStub([cron_job]))

    gateway_server._todoist_chron_mapping_upsert(
        "task_relink_1",
        {"cron_job_id": "missing_job_old", "schedule_text": "tomorrow at 9am"},
    )

    result = gateway_server._reconcile_todoist_chron_mappings(remove_stale=True, dry_run=False)
    assert result["ok"] is True
    assert result["relinked"] == 1
    entry = gateway_server._todoist_chron_mapping_get("task_relink_1")
    assert entry is not None
    assert entry["cron_job_id"] == "cron_job_live_1"


def test_reconcile_dry_run_does_not_mutate_store(monkeypatch, tmp_path):
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(gateway_server, "_cron_service", _ReconCronStub([]))

    gateway_server._todoist_chron_mapping_upsert(
        "task_dryrun_1",
        {"cron_job_id": "missing_job_2", "schedule_text": "in 1 hour"},
    )
    before = gateway_server._todoist_chron_mapping_get("task_dryrun_1")
    assert before is not None

    result = gateway_server._reconcile_todoist_chron_mappings(remove_stale=True, dry_run=True)
    assert result["ok"] is True
    assert result["removed"] == 1

    after = gateway_server._todoist_chron_mapping_get("task_dryrun_1")
    assert after is not None
    assert after["cron_job_id"] == "missing_job_2"


def test_ops_reconcile_route_supports_remove_stale_false(ops_client, monkeypatch, tmp_path):
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(gateway_server, "_cron_service", _ReconCronStub([]))
    gateway_server._todoist_chron_mapping_upsert(
        "task_keep_1",
        {"cron_job_id": "missing_keep", "schedule_text": "tomorrow at 9am"},
    )

    resp = ops_client.post("/api/v1/ops/reconcile/todoist-chron?dry_run=false&remove_stale=false")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert payload["reconciliation"]["removed"] == 0
    assert gateway_server._todoist_chron_mapping_get("task_keep_1") is not None


def test_ops_reconcile_route_enforces_ops_auth_when_token_set(ops_client, monkeypatch):
    monkeypatch.setattr(gateway_server, "OPS_TOKEN", "secret_ops_token")
    monkeypatch.setattr(gateway_server, "_cron_service", _ReconCronStub([]))

    denied = ops_client.post("/api/v1/ops/reconcile/todoist-chron?dry_run=true")
    assert denied.status_code == 401

    allowed = ops_client.post(
        "/api/v1/ops/reconcile/todoist-chron?dry_run=true",
        headers={"x-ua-ops-token": "secret_ops_token"},
    )
    assert allowed.status_code == 200
    data = allowed.json()
    assert data["status"] == "ok"
    assert isinstance(data.get("metrics"), dict)

from __future__ import annotations

from types import SimpleNamespace

import pytest

from universal_agent import gateway_server


class _FakeTodoService:
    def __init__(self, *args, **kwargs):
        del args, kwargs
        self.comments: list[tuple[str, str]] = []

    def heartbeat_summary(self):
        return {"actionable_count": 2, "summary": "2 actionable tasks"}

    def get_pipeline_summary(self):
        return {"inbox": 3, "heartbeat_candidate": 1}

    def record_idea(self, **kwargs):
        return {"id": "idea_123", "content": kwargs.get("content", "")}

    def create_task(self, **kwargs):
        return {"id": "task_123", "content": kwargs.get("content", ""), "due_string": kwargs.get("due_string")}

    def add_comment(self, task_id: str, content: str):
        self.comments.append((task_id, content))
        return True


class _CronStub:
    def __init__(self):
        self.running_jobs: set[str] = set()
        self.created: list[SimpleNamespace] = []
        self.updated: list[tuple[str, dict]] = []
        self.jobs: dict[str, SimpleNamespace] = {}

    def add_job(self, **kwargs):
        idx = len(self.created) + 1
        job_id = f"cron_job_{idx}"
        job = SimpleNamespace(
            job_id=job_id,
            metadata=dict(kwargs.get("metadata", {})),
            to_dict=lambda: {
                "job_id": job_id,
                "cron_expr": kwargs.get("cron_expr"),
                "every_seconds": 0,
                "run_at": kwargs.get("run_at"),
            },
        )
        self.created.append(job)
        self.jobs[job_id] = job
        return job

    def get_job(self, job_id: str):
        return self.jobs.get(job_id)

    def update_job(self, job_id: str, updates: dict):
        self.updated.append((job_id, dict(updates)))
        existing = self.jobs[job_id]
        merged = {
            **existing.to_dict(),
            **updates,
            "job_id": job_id,
            "every_seconds": 0 if updates.get("every_seconds") in (0, "0", None) else existing.to_dict().get("every_seconds", 0),
            "run_at": updates.get("run_at"),
        }
        updated = SimpleNamespace(
            job_id=job_id,
            metadata=dict((updates.get("metadata") or {})),
            to_dict=lambda: merged,
        )
        self.jobs[job_id] = updated
        return updated


class _CronBootstrapStub:
    def __init__(self):
        self.running_jobs: set[str] = set()
        self.jobs: list[SimpleNamespace] = []
        self.updated: list[tuple[str, dict]] = []

    def list_jobs(self):
        return list(self.jobs)

    def add_job(self, **kwargs):
        job = SimpleNamespace(
            job_id="cron_brief_1",
            metadata=kwargs.get("metadata", {}),
            to_dict=lambda: {
                "job_id": "cron_brief_1",
                "cron_expr": kwargs.get("cron_expr"),
                "timezone": kwargs.get("timezone"),
                "enabled": kwargs.get("enabled"),
                "metadata": kwargs.get("metadata", {}),
            },
        )
        self.jobs = [job]
        return job

    def update_job(self, job_id: str, updates: dict):
        self.updated.append((job_id, updates))
        job = self.jobs[0]
        for key, value in updates.items():
            setattr(job, key, value)
        return SimpleNamespace(
            job_id=job_id,
            to_dict=lambda: {
                "job_id": job_id,
                "cron_expr": updates.get("cron_expr"),
                "timezone": updates.get("timezone"),
                "enabled": updates.get("enabled"),
                "metadata": updates.get("metadata", {}),
            },
        )


def test_extract_system_command_content_and_schedule():
    content, schedule = gateway_server._extract_system_command_content_and_schedule(
        "add to todoist review youtube artifacts tonight at 2am"
    )
    assert content == "review youtube artifacts"
    assert schedule == "tonight at 2am"


@pytest.mark.asyncio
async def test_dashboard_system_command_status_query(monkeypatch):
    monkeypatch.setattr("universal_agent.services.todoist_service.TodoService", _FakeTodoService)

    response = await gateway_server.dashboard_system_command(
        gateway_server.DashboardSystemCommandRequest(
            text="show todoist status",
            source_page="/dashboard",
        )
    )

    assert response["ok"] is True
    assert response["intent"] == "status_query"
    assert response["todoist"]["summary"]["actionable_count"] == 2


@pytest.mark.asyncio
async def test_dashboard_system_command_schedules_cron_from_natural_text(monkeypatch, tmp_path):
    monkeypatch.setattr("universal_agent.services.todoist_service.TodoService", _FakeTodoService)
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setenv("UA_SYSTEM_COMMAND_ENABLE_CRON_BRIDGE", "1")
    cron_stub = _CronStub()
    monkeypatch.setattr(gateway_server, "_cron_service", cron_stub)

    response = await gateway_server.dashboard_system_command(
        gateway_server.DashboardSystemCommandRequest(
            text="schedule review generated tutorial package tonight at 2am",
            source_page="/dashboard/tutorials",
            timezone="UTC",
        )
    )

    assert response["ok"] is True
    assert response["intent"] == "schedule_task"
    assert response["todoist"]["task"]["id"] == "task_123"
    assert response["cron"]["job"]["job_id"].startswith("cron_job_")
    assert response["cron"]["status"] == "created"
    assert len(cron_stub.created) == 1


@pytest.mark.asyncio
async def test_dashboard_system_command_reuses_mapped_cron_job(monkeypatch, tmp_path):
    monkeypatch.setattr("universal_agent.services.todoist_service.TodoService", _FakeTodoService)
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setenv("UA_SYSTEM_COMMAND_ENABLE_CRON_BRIDGE", "1")
    cron_stub = _CronStub()
    monkeypatch.setattr(gateway_server, "_cron_service", cron_stub)

    # Seed existing job + persisted mapping.
    seeded_job = cron_stub.add_job(
        user_id="cron_system",
        workspace_dir=str(tmp_path / "ws"),
        command="old command",
        cron_expr="0 2 * * *",
        timezone="UTC",
        run_at=None,
        delete_after_run=False,
        metadata={"todoist_task_id": "task_123"},
    )
    signature = gateway_server._todoist_chron_schedule_signature(
        schedule_text="tonight at 2am",
        timezone_name="UTC",
        every_raw=None,
        cron_expr=None,
        run_at_ts=gateway_server.parse_run_at("tonight at 2am", timezone_name="UTC"),
        delete_after_run=True,
    )
    gateway_server._todoist_chron_mapping_upsert(
        "task_123",
        {
            "cron_job_id": seeded_job.job_id,
            "schedule_signature": signature,
            "schedule_text": "tonight at 2am",
            "timezone": "UTC",
        },
    )

    response = await gateway_server.dashboard_system_command(
        gateway_server.DashboardSystemCommandRequest(
            text="schedule review generated tutorial package tonight at 2am",
            source_page="/dashboard/tutorials",
            timezone="UTC",
        )
    )

    assert response["ok"] is True
    assert response["cron"]["job"]["job_id"] == seeded_job.job_id
    assert response["cron"]["status"] == "reused_existing"
    assert len(cron_stub.created) == 1
    assert not cron_stub.updated


def test_ensure_autonomous_daily_briefing_job_bootstraps(monkeypatch):
    cron_stub = _CronBootstrapStub()
    monkeypatch.setattr(gateway_server, "_cron_service", cron_stub)
    monkeypatch.setenv("UA_AUTONOMOUS_DAILY_BRIEFING_ENABLED", "1")
    monkeypatch.setenv("UA_AUTONOMOUS_DAILY_BRIEFING_CRON", "0 7 * * *")
    monkeypatch.setenv("UA_AUTONOMOUS_DAILY_BRIEFING_TIMEZONE", "UTC")

    result = gateway_server._ensure_autonomous_daily_briefing_job()
    assert result is not None
    assert result["job_id"] == "cron_brief_1"
    assert cron_stub.jobs


def test_ensure_autonomous_daily_briefing_job_updates_existing(monkeypatch):
    cron_stub = _CronBootstrapStub()
    existing = SimpleNamespace(
        job_id="cron_existing",
        metadata={"system_job": gateway_server.AUTONOMOUS_DAILY_BRIEFING_JOB_KEY},
        to_dict=lambda: {"job_id": "cron_existing"},
    )
    cron_stub.jobs = [existing]
    monkeypatch.setattr(gateway_server, "_cron_service", cron_stub)
    monkeypatch.setenv("UA_AUTONOMOUS_DAILY_BRIEFING_ENABLED", "1")
    monkeypatch.setenv("UA_AUTONOMOUS_DAILY_BRIEFING_CRON", "5 7 * * *")
    monkeypatch.setenv("UA_AUTONOMOUS_DAILY_BRIEFING_TIMEZONE", "UTC")

    result = gateway_server._ensure_autonomous_daily_briefing_job()
    assert result is not None
    assert result["job_id"] == "cron_existing"
    assert cron_stub.updated


@pytest.mark.asyncio
async def test_dashboard_summary_includes_reconciliation_metrics(monkeypatch):
    monkeypatch.setattr(gateway_server, "_ops_service", None)
    monkeypatch.setattr(gateway_server, "_sessions", {})
    monkeypatch.setattr(gateway_server, "_session_runtime", {})
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "_cron_service", None)
    monkeypatch.setattr(
        gateway_server,
        "_scheduling_runtime_metrics",
        {
            "todoist_chron_reconciliation": {
                "runs": 4,
                "last_run_at": "2026-02-25T00:00:00+00:00",
                "last_error": None,
                "last_result": {"relinked": 1},
            }
        },
    )

    payload = await gateway_server.dashboard_summary()
    block = payload["todoist_chron_reconciliation"]
    assert block["runs"] == 4
    assert block["last_result"]["relinked"] == 1

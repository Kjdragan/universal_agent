from __future__ import annotations

from types import SimpleNamespace

import pytest

from universal_agent import gateway_server
from universal_agent import task_hub
from universal_agent.services.email_task_bridge import EmailTaskBridge


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


class _HeartbeatStub:
    def __init__(self):
        self.next_calls: list[tuple[str, str]] = []
        self.now_calls: list[tuple[str, str]] = []
        self.registered: list[str] = []
        self.busy_sessions: set[str] = set()

    def request_heartbeat_next(self, session_id: str, reason: str = "wake_next"):
        self.next_calls.append((session_id, reason))

    def request_heartbeat_now(self, session_id: str, reason: str = "wake"):
        self.now_calls.append((session_id, reason))

    def register_session(self, session):
        self.registered.append(str(getattr(session, "session_id", "")))


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
    assert "task_hub" in response
    assert "overview" in (response["task_hub"] or {})


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
    assert response["todoist"] is None
    assert response["cron"]["job"]["job_id"].startswith("cron_job_")
    assert response["cron"]["status"] == "created"
    assert len(cron_stub.created) == 1


@pytest.mark.asyncio
async def test_dashboard_system_command_schedule_boosts_priority_and_wakes_heartbeat(monkeypatch, tmp_path):
    monkeypatch.setattr("universal_agent.services.todoist_service.TodoService", _FakeTodoService)
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setenv("UA_SYSTEM_COMMAND_ENABLE_CRON_BRIDGE", "0")
    hb_stub = _HeartbeatStub()
    monkeypatch.setattr(gateway_server, "_heartbeat_service", hb_stub)

    response = await gateway_server.dashboard_system_command(
        gateway_server.DashboardSystemCommandRequest(
            text="change the heartbeat.md scheduled to run every ten minutes",
            source_page="/dashboard/todolist",
            source_context={"session_id": "ops-session-1"},
            timezone="America/Chicago",
        )
    )

    assert response["ok"] is True
    task = response["task_hub"]["task"]
    assert task["priority"] == 4
    assert "schedule-command" in list(task.get("labels") or [])
    assert hb_stub.next_calls == [("ops-session-1", "system_command_schedule")]


@pytest.mark.asyncio
async def test_dashboard_system_command_preserves_run_lineage_in_task_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr("universal_agent.services.todoist_service.TodoService", _FakeTodoService)
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))
    monkeypatch.setenv("UA_SYSTEM_COMMAND_ENABLE_CRON_BRIDGE", "0")

    response = await gateway_server.dashboard_system_command(
        gateway_server.DashboardSystemCommandRequest(
            text="capture this idea for later",
            source_page="/dashboard/todolist",
            source_context={
                "session_id": "ops-session-1",
                "run_id": "run-system-1",
                "attempt_id": "attempt-system-2",
            },
        )
    )

    assert response["ok"] is True
    task = response["task_hub"]["task"]
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    assert metadata.get("source_session_id") == "ops-session-1"
    assert metadata.get("source_run_id") == "run-system-1"
    assert metadata.get("source_attempt_id") == "attempt-system-2"
    assert task.get("source_ref") == "run-system-1"
    description = str(task.get("description") or "")
    assert "source_run_id: run-system-1" in description
    assert "source_attempt_id: attempt-system-2" in description
    assert "source_session_id: ops-session-1" in description


@pytest.mark.asyncio
async def test_dashboard_todolist_email_tasks_exposes_workflow_lineage(monkeypatch, tmp_path):
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))

    conn = gateway_server._task_hub_open_conn()
    try:
        bridge = EmailTaskBridge(db_conn=conn, todoist_service=None, heartbeat_path=str(tmp_path / "HEARTBEAT.md"))
        bridge.materialize(
            thread_id="thd_dashboard_email_001",
            message_id="msg_dashboard_email_001",
            sender_email="kevin@example.com",
            subject="Dashboard email lineage",
            reply_text="Track this email task on the run timeline",
            workflow_run_id="run_email_dashboard_1",
            workflow_attempt_id="attempt_email_dashboard_1",
            provider_session_id="session_email_dashboard_1",
        )
    finally:
        conn.close()

    response = await gateway_server.dashboard_todolist_email_tasks(limit=20)

    assert response["status"] == "ok"
    assert response["total"] >= 1
    item = next(
        row for row in response["items"]
        if row.get("thread_id") == "thd_dashboard_email_001"
    )
    assert item["workflow_run_id"] == "run_email_dashboard_1"
    assert item["workflow_attempt_id"] == "attempt_email_dashboard_1"
    assert item["provider_session_id"] == "session_email_dashboard_1"
    assert item["run_href"] == "/api/v1/runs/run_email_dashboard_1"


@pytest.mark.asyncio
async def test_dashboard_system_command_uses_deterministic_task_id(monkeypatch, tmp_path):
    monkeypatch.setattr("universal_agent.services.todoist_service.TodoService", _FakeTodoService)
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))
    monkeypatch.setenv("UA_SYSTEM_COMMAND_ENABLE_CRON_BRIDGE", "0")

    payload = gateway_server.DashboardSystemCommandRequest(
        text="change the heartbeat.md scheduled to run every ten minutes",
        source_page="/dashboard/todolist",
        source_context={"session_id": "ops-session-1"},
        timezone="America/Chicago",
    )
    first = await gateway_server.dashboard_system_command(payload)
    second = await gateway_server.dashboard_system_command(payload)
    assert first["ok"] is True
    assert second["ok"] is True
    assert first["task_hub"]["task"]["task_id"] == second["task_hub"]["task"]["task_id"]


@pytest.mark.asyncio
async def test_dashboard_system_command_parks_duplicate_signature_rows(monkeypatch, tmp_path):
    monkeypatch.setattr("universal_agent.services.todoist_service.TodoService", _FakeTodoService)
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))
    monkeypatch.setenv("UA_SYSTEM_COMMAND_ENABLE_CRON_BRIDGE", "0")

    with gateway_server._activity_store_lock:
        conn = gateway_server._task_hub_open_conn()
        try:
            task_hub.upsert_item(
                conn,
                {
                    "task_id": "scmd:manual-dup",
                    "source_kind": "system_command",
                    "source_ref": "ops-session-1",
                    "title": "change the heartbeat.md scheduled to run",
                    "description": "manual duplicate",
                    "project_key": "immediate",
                    "priority": 2,
                    "labels": ["agent-ready", "schedule-command"],
                    "status": task_hub.TASK_STATUS_OPEN,
                    "must_complete": False,
                    "agent_ready": True,
                    "metadata": {
                        "intent": "schedule_task",
                        "schedule_text": "every ten minutes",
                        "source_page": "/dashboard/todolist",
                        "source_session_id": "ops-session-1",
                        "repeat_schedule": True,
                    },
                },
            )
        finally:
            conn.close()

    response = await gateway_server.dashboard_system_command(
        gateway_server.DashboardSystemCommandRequest(
            text="change the heartbeat.md scheduled to run every ten minutes",
            source_page="/dashboard/todolist",
            source_context={"session_id": "ops-session-1"},
            timezone="America/Chicago",
        )
    )
    assert response["ok"] is True
    assert int(response["task_hub"].get("duplicates_parked") or 0) >= 1

    with gateway_server._activity_store_lock:
        conn = gateway_server._task_hub_open_conn()
        try:
            dup = task_hub.get_item(conn, "scmd:manual-dup")
        finally:
            conn.close()
    assert dup is not None
    assert dup["status"] == task_hub.TASK_STATUS_PARKED


@pytest.mark.asyncio
async def test_dashboard_system_command_parks_legacy_duplicate_when_new_request_has_run_lineage(monkeypatch, tmp_path):
    monkeypatch.setattr("universal_agent.services.todoist_service.TodoService", _FakeTodoService)
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))
    monkeypatch.setenv("UA_SYSTEM_COMMAND_ENABLE_CRON_BRIDGE", "0")

    with gateway_server._activity_store_lock:
        conn = gateway_server._task_hub_open_conn()
        try:
            task_hub.upsert_item(
                conn,
                {
                    "task_id": "scmd:legacy-session-dup",
                    "source_kind": "system_command",
                    "source_ref": "ops-session-1",
                    "title": "change the heartbeat.md scheduled to run",
                    "description": "legacy duplicate",
                    "project_key": "immediate",
                    "priority": 2,
                    "labels": ["agent-ready", "schedule-command"],
                    "status": task_hub.TASK_STATUS_OPEN,
                    "must_complete": False,
                    "agent_ready": True,
                    "metadata": {
                        "intent": "schedule_task",
                        "schedule_text": "every ten minutes",
                        "source_page": "/dashboard/todolist",
                        "source_session_id": "ops-session-1",
                        "repeat_schedule": True,
                    },
                },
            )
        finally:
            conn.close()

    response = await gateway_server.dashboard_system_command(
        gateway_server.DashboardSystemCommandRequest(
            text="change the heartbeat.md scheduled to run every ten minutes",
            source_page="/dashboard/todolist",
            source_context={
                "session_id": "ops-session-1",
                "run_id": "run-system-command-1",
                "attempt_id": "attempt-system-command-1",
            },
            timezone="America/Chicago",
        )
    )

    assert response["ok"] is True
    assert int(response["task_hub"].get("duplicates_parked") or 0) >= 1

    with gateway_server._activity_store_lock:
        conn = gateway_server._task_hub_open_conn()
        try:
            dup = task_hub.get_item(conn, "scmd:legacy-session-dup")
        finally:
            conn.close()
    assert dup is not None
    assert dup["status"] == task_hub.TASK_STATUS_PARKED


@pytest.mark.asyncio
async def test_todolist_overview_includes_heartbeat_runtime_snapshot(monkeypatch):
    monkeypatch.setattr(gateway_server, "_heartbeat_service", None)
    monkeypatch.setattr(gateway_server, "list_approvals", lambda status="pending": [])
    monkeypatch.setenv("UA_HEARTBEAT_INTERVAL", "25m")

    response = await gateway_server.dashboard_todolist_overview()

    assert response["status"] == "ok"
    heartbeat = response.get("heartbeat") or {}
    assert heartbeat.get("enabled") in {True, False}
    assert int(heartbeat.get("configured_every_seconds") or 0) == 1500
    assert int(heartbeat.get("min_interval_seconds") or 0) >= 1
    assert int(heartbeat.get("heartbeat_effective_interval_seconds") or 0) >= 1
    assert "heartbeat_interval_source" in heartbeat
    assert "cron_interval_seconds" in heartbeat


@pytest.mark.asyncio
async def test_todolist_overview_prefers_interval_over_legacy_every(monkeypatch):
    monkeypatch.setattr(gateway_server, "_heartbeat_service", None)
    monkeypatch.setattr(gateway_server, "list_approvals", lambda status="pending": [])
    monkeypatch.setenv("UA_HEARTBEAT_INTERVAL", "10m")
    monkeypatch.setenv("UA_HEARTBEAT_EVERY", "25m")

    response = await gateway_server.dashboard_todolist_overview()

    assert response["status"] == "ok"
    heartbeat = response.get("heartbeat") or {}
    assert int(heartbeat.get("configured_every_seconds") or 0) == 600


@pytest.mark.asyncio
async def test_todolist_overview_uses_runtime_min_interval_env(monkeypatch):
    monkeypatch.setattr(gateway_server, "_heartbeat_service", None)
    monkeypatch.setattr(gateway_server, "list_approvals", lambda status="pending": [])
    monkeypatch.setenv("UA_HEARTBEAT_INTERVAL", "10m")
    monkeypatch.setenv("UA_HEARTBEAT_MIN_INTERVAL_SECONDS", "600")

    response = await gateway_server.dashboard_todolist_overview()

    assert response["status"] == "ok"
    heartbeat = response.get("heartbeat") or {}
    assert int(heartbeat.get("configured_every_seconds") or 0) == 600
    assert int(heartbeat.get("min_interval_seconds") or 0) == 600
    assert int(heartbeat.get("effective_default_every_seconds") or 0) == 600


@pytest.mark.asyncio
async def test_wake_heartbeat_uses_gateway_sessions_when_runtime_sessions_empty(monkeypatch, tmp_path):
    hb_stub = _HeartbeatStub()
    monkeypatch.setattr(gateway_server, "_heartbeat_service", hb_stub)
    monkeypatch.setattr(gateway_server, "_sessions", {})
    gateway_sessions = [
        SimpleNamespace(session_id="sess-a", workspace_dir=str(tmp_path / "a")),
        SimpleNamespace(session_id="sess-b", workspace_dir=str(tmp_path / "b")),
    ]
    monkeypatch.setattr(
        gateway_server,
        "get_gateway",
        lambda: SimpleNamespace(list_sessions=lambda: gateway_sessions),
    )

    response = await gateway_server.wake_heartbeat(
        gateway_server.HeartbeatWakeRequest(mode="now", reason="unit-test-wake")
    )

    assert response["status"] == "queued"
    assert response["count"] == 2
    assert hb_stub.registered == ["sess-a", "sess-b"]
    assert hb_stub.now_calls == [("sess-a", "unit-test-wake"), ("sess-b", "unit-test-wake")]


@pytest.mark.asyncio
async def test_todolist_completed_and_history_endpoints_include_links(monkeypatch, tmp_path):
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path)
    monkeypatch.setattr(gateway_server, "get_activity_db_path", lambda: str(tmp_path / "activity_state.db"))
    sid = "ops-history-session"

    with gateway_server._activity_store_lock:
        conn = gateway_server._task_hub_open_conn()
        try:
            task_hub.upsert_item(
                conn,
                {
                    "task_id": "task:history-endpoint",
                    "source_kind": "internal",
                    "source_ref": sid,
                    "title": "History endpoint task",
                    "description": "completed row",
                    "project_key": "immediate",
                    "priority": 4,
                    "labels": ["agent-ready", "must-complete"],
                    "status": task_hub.TASK_STATUS_OPEN,
                    "must_complete": True,
                    "agent_ready": True,
                },
            )
            claimed = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id=f"heartbeat:{sid}")
            assignment_id = str(claimed[0]["assignment_id"])
            task_hub.perform_task_action(
                conn,
                task_id="task:history-endpoint",
                action="complete",
                reason="done",
                agent_id=f"heartbeat:{sid}",
            )
            task_hub.finalize_assignments(
                conn,
                assignment_ids=[assignment_id],
                state="completed",
                result_summary="done",
                reopen_in_progress=False,
                policy="heartbeat",
            )
        finally:
            conn.close()

    completed = await gateway_server.dashboard_todolist_completed(limit=20)
    items = completed.get("items") or []
    row = next((item for item in items if item.get("task_id") == "task:history-endpoint"), None)
    assert row is not None
    links = row.get("links") or {}
    assert str(links.get("session_href") or "").startswith("/dashboard/sessions")
    assert "run.log" in str(links.get("run_log_path") or "")

    history = await gateway_server.dashboard_todolist_task_history("task:history-endpoint", limit=20)
    assignments = history.get("assignments") or []
    assert len(assignments) >= 1
    first_links = assignments[0].get("links") or {}
    assert str(first_links.get("session_href") or "").startswith("/dashboard/sessions")
    assert str(first_links.get("run_log_href") or "")


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
    assert response["cron"]["job"]["job_id"].startswith("cron_job_")
    assert response["cron"]["status"] == "created"
    assert len(cron_stub.created) == 2
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

from __future__ import annotations

from types import SimpleNamespace

import pytest

from universal_agent import gateway_server, task_hub


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
                    "metadata": {"workflow_manifest": {"final_channel": "chat"}},
                },
            )
            claimed = task_hub.claim_next_dispatch_tasks(conn, limit=1, agent_id=f"heartbeat:{sid}")
            assignment_id = str(claimed[0]["assignment_id"])
            task_hub.record_task_outbound_delivery(
                conn,
                task_id="task:history-endpoint",
                channel="chat",
                message_id="chat_delivery",
            )
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

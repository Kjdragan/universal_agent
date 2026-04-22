import time
import urllib.parse
import json
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from universal_agent import gateway_server
from universal_agent.gateway import GatewaySessionSummary
from universal_agent.cron_service import CronRunRecord, CronService
from universal_agent.gateway import GatewaySession


class _StubGateway:
    async def create_session(self, user_id: str, workspace_dir: str):
        return SimpleNamespace(user_id=user_id, workspace_dir=workspace_dir)


@pytest.fixture(autouse=True)
def _isolate_gateway_notification_state(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str((tmp_path / "runtime_state.db").resolve()))
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "_sessions", {})
    monkeypatch.setattr(gateway_server, "_heartbeat_service", None)
    monkeypatch.setattr(gateway_server, "_cron_service", None)
    monkeypatch.setattr(
        gateway_server,
        "_agentmail_heartbeat_wake_seen_ids",
        gateway_server.deque(maxlen=1024),
    )
    monkeypatch.setattr(gateway_server, "_agentmail_heartbeat_wake_seen_set", set())


def test_autonomous_cron_to_heartbeat_enabled_defaults_off(monkeypatch):
    monkeypatch.delenv("UA_CRON_WAKE_HEARTBEAT_ON_AUTONOMOUS_RUN", raising=False)
    assert gateway_server._autonomous_cron_to_heartbeat_enabled() is False


def test_emit_cron_event_adds_completion_notification(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "_cron_service", CronService(_StubGateway(), tmp_path))

    cron_ws = tmp_path / "cron_notification_workspace"
    cron_ws.mkdir(parents=True, exist_ok=True)
    job = gateway_server._cron_service.add_job(
        user_id="calendar_owner",
        workspace_dir=str(cron_ws),
        command="notification demo",
        every_raw="30m",
        enabled=True,
    )

    before_count = len(gateway_server._notifications)
    gateway_server._emit_cron_event(
        {
            "type": "cron_run_completed",
            "run": {
                "run_id": "run_ntf_1",
                "job_id": job.job_id,
                "status": "success",
                "scheduled_at": time.time(),
                "started_at": time.time(),
                "finished_at": time.time(),
                "error": None,
            },
        }
    )

    assert len(gateway_server._notifications) == before_count + 1
    latest = gateway_server._notifications[-1]
    assert latest["kind"] == "cron_run_success"
    assert latest["title"] == "Chron Run Succeeded"
    assert latest["metadata"]["job_id"] == job.job_id
    assert latest["metadata"]["status"] == "success"


def test_emit_cron_event_labels_autonomous_runs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "_cron_service", CronService(_StubGateway(), tmp_path))

    cron_ws = tmp_path / "cron_autonomous_workspace"
    cron_ws.mkdir(parents=True, exist_ok=True)
    job = gateway_server._cron_service.add_job(
        user_id="cron_system",
        workspace_dir=str(cron_ws),
        command="autonomous demo",
        every_raw="30m",
        enabled=True,
        metadata={"autonomous": True, "system_job": "demo"},
    )

    gateway_server._emit_cron_event(
        {
            "type": "cron_run_completed",
            "run": {
                "run_id": "run_auto_1",
                "job_id": job.job_id,
                "status": "success",
                "scheduled_at": time.time(),
                "started_at": time.time(),
                "finished_at": time.time(),
                "error": None,
            },
        }
    )

    latest = gateway_server._notifications[-1]
    assert latest["kind"] == "autonomous_run_completed"
    assert latest["title"] == "Autonomous Task Completed"
    assert latest["metadata"]["autonomous"] is True


def test_emit_cron_event_adds_retry_notification_with_workflow_metadata(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "_cron_service", CronService(_StubGateway(), tmp_path))

    cron_ws = tmp_path / "cron_retry_notification_workspace"
    cron_ws.mkdir(parents=True, exist_ok=True)
    job = gateway_server._cron_service.add_job(
        user_id="calendar_owner",
        workspace_dir=str(cron_ws),
        command="retry demo",
        every_raw="30m",
        enabled=True,
    )

    gateway_server._emit_cron_event(
        {
            "type": "cron_run_retry_queued",
            "run": {
                "run_id": "run_retry_1",
                "job_id": job.job_id,
                "status": "retry_queued",
                "scheduled_at": time.time(),
                "started_at": time.time(),
                "finished_at": time.time(),
                "error": "execution timed out after 60s",
                "workflow_run_id": "run_cron_dispatch_123",
                "workflow_attempt_id": "attempt_abc",
                "workflow_attempt_number": 1,
                "dispatch_key": f"scheduled:{job.job_id}:1234567890",
                "next_attempt_id": "attempt_def",
                "next_attempt_number": 2,
            },
        }
    )

    latest = gateway_server._notifications[-1]
    assert latest["kind"] == "cron_run_retry_queued"
    assert latest["title"] == "Chron Retry Queued"
    assert "retry attempt 2 has been queued" in latest["message"]
    assert latest["metadata"]["job_id"] == job.job_id
    assert latest["metadata"]["workflow_run_id"] == "run_cron_dispatch_123"
    assert latest["metadata"]["workflow_attempt_id"] == "attempt_abc"
    assert latest["metadata"]["workflow_attempt_number"] == 1
    assert latest["metadata"]["dispatch_key"] == f"scheduled:{job.job_id}:1234567890"


def test_emit_cron_event_daily_briefing_records_autonomous_completion(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(gateway_server, "_cron_service", CronService(_StubGateway(), tmp_path))

    # Seed prior autonomous activity so the deterministic briefing has content.
    gateway_server._add_notification(
        kind="autonomous_run_completed",
        title="Autonomous Task Completed",
        message="autonomous demo completed",
        severity="info",
        metadata={
            "source": "cron",
            "autonomous": True,
            "job_id": "seed_job",
            "run_id": "seed_run",
        },
    )

    cron_ws = tmp_path / "cron_daily_briefing_workspace"
    cron_ws.mkdir(parents=True, exist_ok=True)
    job = gateway_server._cron_service.add_job(
        user_id="cron_system",
        workspace_dir=str(cron_ws),
        command="daily briefing job",
        cron_expr="0 7 * * *",
        timezone="UTC",
        enabled=True,
        metadata={
            "autonomous": True,
            "system_job": gateway_server.AUTONOMOUS_DAILY_BRIEFING_JOB_KEY,
        },
    )

    gateway_server._emit_cron_event(
        {
            "type": "cron_run_completed",
            "run": {
                "run_id": "run_daily_brief_1",
                "job_id": job.job_id,
                "status": "success",
                "scheduled_at": time.time(),
                "started_at": time.time(),
                "finished_at": time.time(),
                "error": None,
            },
        }
    )

    latest = gateway_server._notifications[-1]
    assert latest["kind"] == "autonomous_run_completed"
    assert latest["title"] == "Autonomous Task Completed"
    assert latest["metadata"]["autonomous"] is True
    assert latest["metadata"]["system_job"] == gateway_server.AUTONOMOUS_DAILY_BRIEFING_JOB_KEY


def test_emit_heartbeat_event_records_workspace_artifacts(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(gateway_server, "_sessions", {})
    monkeypatch.setattr(gateway_server, "_heartbeat_mediation_cooldowns", {})

    class _HookDispatchStub:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def dispatch_internal_action_background_with_admission(self, action_payload: dict):
            self.calls.append(action_payload)
            return {
                "decision": "accepted",
                "reason": "accepted",
                "run_id": "run_heartbeat_123",
                "attempt_id": "attempt_heartbeat_1",
            }

    hook_stub = _HookDispatchStub()
    monkeypatch.setattr(gateway_server, "_hooks_service", hook_stub)

    workspace_root = gateway_server.WORKSPACES_DIR
    workspace_root.mkdir(parents=True, exist_ok=True)
    session_ws = workspace_root / "session_abc"
    session_ws.mkdir(parents=True, exist_ok=True)
    output_file = session_ws / "work_products" / "summary.md"
    findings_file = session_ws / "work_products" / gateway_server._HEARTBEAT_FINDINGS_FILENAME
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# output\n", encoding="utf-8")
    findings_file.write_text(
        json.dumps(
            {
                "version": 1,
                "overall_status": "warn",
                "generated_at_utc": "2026-03-12T06:09:00Z",
                "source": "vps_system_health_check",
                "summary": "Gateway errors elevated",
                "findings": [
                    {
                        "finding_id": "gateway_errors_elevated",
                        "category": "gateway",
                        "severity": "warn",
                        "metric_key": "recent_errors_30m",
                        "observed_value": 67,
                        "threshold_text": ">10",
                        "known_rule_match": True,
                        "confidence": "high",
                        "title": "Gateway Errors Elevated",
                        "recommendation": "Inspect gateway logs for root cause.",
                        "runbook_command": "journalctl -u universal-agent-gateway --since '30 min ago' --no-pager",
                        "metadata": {"service": "universal-agent-gateway"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    session = GatewaySession(
        session_id="session_abc",
        user_id="tester",
        workspace_dir=str(session_ws),
        metadata={},
    )
    gateway_server._sessions[session.session_id] = session

    gateway_server._emit_heartbeat_event(
        {
            "type": "heartbeat_completed",
            "session_id": session.session_id,
            "timestamp": time.time(),
            "ok_only": False,
            "suppressed_reason": None,
            "sent": True,
            "artifacts": {
                "writes": [],
                "work_products": [str(output_file), str(findings_file)],
                "bash_commands": [],
            },
        }
    )

    heartbeat = next(item for item in reversed(gateway_server._notifications) if item["kind"] == "autonomous_heartbeat_completed")
    links = heartbeat["metadata"]["heartbeat_artifacts"]
    assert isinstance(links, list) and links
    assert links[0]["scope"] == "workspaces"
    assert links[0]["relative_path"].endswith("session_abc/work_products/summary.md")
    assert "scope=workspaces" in links[0]["storage_href"]
    assert heartbeat["requires_action"] is True
    assert heartbeat["severity"] == "warning"
    assert heartbeat["metadata"]["heartbeat_findings_status"] == "warn"
    assert heartbeat["metadata"]["heartbeat_findings_count"] == 1
    assert heartbeat["metadata"]["heartbeat_mediation_status"] == "dispatched"
    assert heartbeat["metadata"]["heartbeat_workflow_run_id"] == "run_heartbeat_123"
    assert heartbeat["metadata"]["heartbeat_workflow_attempt_id"] == "attempt_heartbeat_1"
    assert heartbeat["metadata"]["primary_runbook_command"].startswith("journalctl -u universal-agent-gateway")
    assert hook_stub.calls
    assert hook_stub.calls[0]["name"] == "AutoHeartbeatInvestigation"
    persisted = gateway_server._get_activity_event(heartbeat["id"])
    assert persisted is not None
    action_ids = {str(item.get("id") or "") for item in persisted.get("actions", [])}
    assert "copy_runbook_command" in action_ids
    assert "open_findings" in action_ids


def test_emit_heartbeat_event_falls_back_when_findings_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(gateway_server, "_sessions", {})
    monkeypatch.setattr(gateway_server, "_heartbeat_mediation_cooldowns", {})

    class _HookDispatchStub:
        async def dispatch_internal_action_background_with_admission(self, action_payload: dict):
            return {"decision": "accepted", "reason": "accepted"}

    monkeypatch.setattr(gateway_server, "_hooks_service", _HookDispatchStub())

    workspace_root = gateway_server.WORKSPACES_DIR
    workspace_root.mkdir(parents=True, exist_ok=True)
    session_ws = workspace_root / "session_missing"
    session_ws.mkdir(parents=True, exist_ok=True)
    markdown = session_ws / "work_products" / gateway_server._HEARTBEAT_REPORT_FILENAME
    markdown.parent.mkdir(parents=True, exist_ok=True)
    markdown.write_text(
        "Flags\n⚠️ WARN: Gateway Errors Elevated\nAction: Investigate gateway logs for root cause\nRun journalctl -u universal-agent-gateway --since '30 min ago' --no-pager\n",
        encoding="utf-8",
    )

    session = GatewaySession(
        session_id="session_missing",
        user_id="tester",
        workspace_dir=str(session_ws),
        metadata={},
    )
    gateway_server._sessions[session.session_id] = session

    gateway_server._emit_heartbeat_event(
        {
            "type": "heartbeat_completed",
            "session_id": session.session_id,
            "timestamp": time.time(),
            "ok_only": False,
            "sent": True,
            "artifacts": {"writes": [], "work_products": [str(markdown)], "bash_commands": []},
        }
    )

    parse_failed = next(item for item in reversed(gateway_server._notifications) if item["kind"] == "heartbeat_findings_parse_failed")
    heartbeat = next(item for item in reversed(gateway_server._notifications) if item["kind"] == "autonomous_heartbeat_completed")
    assert parse_failed["requires_action"] is True
    assert heartbeat["metadata"]["heartbeat_findings"]["findings"][0]["finding_id"] == "heartbeat_findings_parse_failed"
    assert heartbeat["metadata"]["primary_runbook_command"].startswith("Run journalctl -u universal-agent-gateway")


def test_emit_heartbeat_event_accepts_legacy_findings_filename(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "WORKSPACES_DIR", tmp_path / "workspaces")
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(gateway_server, "_sessions", {})
    monkeypatch.setattr(gateway_server, "_heartbeat_mediation_cooldowns", {})

    class _HookDispatchStub:
        async def dispatch_internal_action_background_with_admission(self, action_payload: dict):
            return {"decision": "accepted", "reason": "accepted"}

    monkeypatch.setattr(gateway_server, "_hooks_service", _HookDispatchStub())

    workspace_root = gateway_server.WORKSPACES_DIR
    workspace_root.mkdir(parents=True, exist_ok=True)
    session_ws = workspace_root / "session_legacy_findings"
    session_ws.mkdir(parents=True, exist_ok=True)
    findings_file = session_ws / "work_products" / "heartbeat_findings.json"
    findings_file.parent.mkdir(parents=True, exist_ok=True)
    findings_file.write_text(
        json.dumps(
            {
                "version": 1,
                "overall_status": "warn",
                "generated_at_utc": "2026-03-13T14:49:00Z",
                "source": "heartbeat",
                "summary": "Gateway errors elevated",
                "findings": [
                    {
                        "finding_id": "gateway_errors_elevated",
                        "category": "gateway",
                        "severity": "warn",
                        "metric_key": "recent_errors_30m",
                        "observed_value": 20,
                        "threshold_text": ">10",
                        "known_rule_match": True,
                        "confidence": "high",
                        "title": "Gateway Errors Elevated",
                        "recommendation": "Inspect gateway logs for root cause.",
                        "runbook_command": "journalctl -u universal-agent-gateway --since '30 min ago' --no-pager",
                        "metadata": {"service": "universal-agent-gateway"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    session = GatewaySession(
        session_id="session_legacy_findings",
        user_id="tester",
        workspace_dir=str(session_ws),
        metadata={},
    )
    gateway_server._sessions[session.session_id] = session

    gateway_server._emit_heartbeat_event(
        {
            "type": "heartbeat_completed",
            "session_id": session.session_id,
            "timestamp": time.time(),
            "ok_only": False,
            "sent": True,
            "artifacts": {"writes": [], "work_products": [str(findings_file)], "bash_commands": []},
        }
    )

    assert not any(item["kind"] == "heartbeat_findings_parse_failed" for item in gateway_server._notifications)
    heartbeat = next(item for item in reversed(gateway_server._notifications) if item["kind"] == "autonomous_heartbeat_completed")
    assert heartbeat["metadata"]["heartbeat_findings"]["findings"][0]["finding_id"] == "gateway_errors_elevated"


def test_agentmail_trusted_heartbeat_request_queues_wake(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str((tmp_path / "runtime_state.db").resolve()))

    class _HeartbeatStub:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def register_session(self, session):
            return None

        def request_heartbeat_now(self, session_id: str, reason: str = "wake"):
            self.calls.append((session_id, reason))

    class _GatewayStub:
        def list_sessions(self):
            return [
                GatewaySessionSummary(
                    session_id="archived_1",
                    workspace_dir="/tmp/archived_1",
                    status="archived",
                    metadata={"archived": True},
                ),
                GatewaySessionSummary(
                    session_id="live_3",
                    workspace_dir="/tmp/live_3",
                    status="active",
                    metadata={},
                ),
            ]

    notifications: list[dict] = []

    def _capture_notification(**kwargs):
        notifications.append(kwargs)
        return kwargs

    heartbeat_stub = _HeartbeatStub()
    monkeypatch.setattr(gateway_server, "_heartbeat_service", heartbeat_stub)
    monkeypatch.setattr(gateway_server, "_sessions", {"session_1": object(), "session_2": object()})
    monkeypatch.setattr(gateway_server, "get_gateway", lambda: _GatewayStub())
    monkeypatch.setattr(gateway_server, "_add_notification", _capture_notification)
    monkeypatch.setattr(gateway_server, "_agentmail_heartbeat_wake_seen_ids", gateway_server.deque(maxlen=1024))
    monkeypatch.setattr(gateway_server, "_agentmail_heartbeat_wake_seen_set", set())

    payload = {
        "name": "AgentMailInbound",
        "message": (
            "sender_trusted: True\n"
            "sender_email: kevinjdragan@gmail.com\n"
            "thread_id: thd_123\n"
            "message_id: <msg_123>\n"
            "subject: Re: Simone heartbeat review required\n\n"
            "--- Reply (new content) ---\n"
            "Run another heartbeat and check if it is running properly now.\n"
        ),
    }

    result = gateway_server._maybe_trigger_heartbeat_from_agentmail_action(payload)

    assert result["triggered"] is True
    assert result["count"] == 3
    assert len(heartbeat_stub.calls) == 3
    assert {sid for sid, _ in heartbeat_stub.calls} == {"session_1", "session_2", "live_3"}
    assert all(reason == "agentmail_trusted_heartbeat_request" for _, reason in heartbeat_stub.calls)
    assert notifications
    assert notifications[0]["kind"] == "agentmail_heartbeat_wake_queued"


def test_agentmail_heartbeat_request_requires_trusted_sender(monkeypatch):
    payload = {
        "name": "AgentMailInbound",
        "message": (
            "sender_trusted: False\n"
            "sender_email: kevinjdragan@gmail.com\n"
            "message_id: <msg_124>\n"
            "subject: Re: Simone heartbeat review required\n\n"
            "--- Reply (new content) ---\n"
            "Run another heartbeat.\n"
        ),
    }

    result = gateway_server._maybe_trigger_heartbeat_from_agentmail_action(payload)
    assert result["triggered"] is False
    assert result["reason"] == "not_requested"


def test_agentmail_heartbeat_request_dedupes_by_message_id(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str((tmp_path / "runtime_state.db").resolve()))

    class _HeartbeatStub:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def register_session(self, session):
            return None

        def request_heartbeat_now(self, session_id: str, reason: str = "wake"):
            self.calls.append((session_id, reason))

    class _GatewayStub:
        def list_sessions(self):
            return []

    heartbeat_stub = _HeartbeatStub()
    monkeypatch.setattr(gateway_server, "_heartbeat_service", heartbeat_stub)
    monkeypatch.setattr(gateway_server, "_sessions", {"session_1": object()})
    monkeypatch.setattr(gateway_server, "get_gateway", lambda: _GatewayStub())
    monkeypatch.setattr(gateway_server, "_add_notification", lambda **kwargs: kwargs)
    monkeypatch.setattr(gateway_server, "_agentmail_heartbeat_wake_seen_ids", gateway_server.deque(maxlen=1024))
    monkeypatch.setattr(gateway_server, "_agentmail_heartbeat_wake_seen_set", set())

    payload = {
        "name": "AgentMailInbound",
        "message": (
            "sender_trusted: True\n"
            "sender_email: kevinjdragan@gmail.com\n"
            "thread_id: thd_124\n"
            "message_id: <msg_125>\n"
            "subject: Re: Simone heartbeat review required\n\n"
            "--- Reply (new content) ---\n"
            "Please rerun heartbeat now.\n"
        ),
    }

    first = gateway_server._maybe_trigger_heartbeat_from_agentmail_action(payload)
    second = gateway_server._maybe_trigger_heartbeat_from_agentmail_action(payload)

    assert first["triggered"] is True
    assert second["triggered"] is False
    assert second["reason"] == "duplicate_message_id"
    assert len(heartbeat_stub.calls) == 1


def test_operator_email_heartbeat_wake_dedupes_via_workflow_admission(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str((tmp_path / "runtime_state.db").resolve()))

    class _HeartbeatStub:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def register_session(self, session):
            return None

        def request_heartbeat_now(self, session_id: str, reason: str = "wake"):
            self.calls.append((session_id, reason))

    class _GatewayStub:
        def list_sessions(self):
            return []

    heartbeat_stub = _HeartbeatStub()
    monkeypatch.setattr(gateway_server, "_heartbeat_service", heartbeat_stub)
    monkeypatch.setattr(gateway_server, "_sessions", {"session_1": object()})
    monkeypatch.setattr(gateway_server, "get_gateway", lambda: _GatewayStub())
    monkeypatch.setattr(gateway_server, "_add_notification", lambda **kwargs: kwargs)

    first = gateway_server._queue_heartbeat_wake_from_operator_email(
        sender_email="kevin@example.com",
        thread_id="thread-1",
        message_id="msg-901",
    )
    second = gateway_server._queue_heartbeat_wake_from_operator_email(
        sender_email="kevin@example.com",
        thread_id="thread-1",
        message_id="msg-901",
    )

    assert first["triggered"] is True
    assert second["triggered"] is False
    assert second["reason"] == "skip_duplicate"
    assert len(heartbeat_stub.calls) == 1


def test_autonomous_cron_heartbeat_wake_dedupes_via_workflow_admission(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str((tmp_path / "runtime_state.db").resolve()))
    monkeypatch.setenv("UA_CRON_WAKE_HEARTBEAT_ON_AUTONOMOUS_RUN", "1")

    class _HeartbeatStub:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def register_session(self, session):
            return None

        def request_heartbeat_next(self, session_id: str, reason: str = "wake"):
            self.calls.append((session_id, reason))

    class _GatewayStub:
        def list_sessions(self):
            return []

    heartbeat_stub = _HeartbeatStub()
    monkeypatch.setattr(gateway_server, "_heartbeat_service", heartbeat_stub)
    monkeypatch.setattr(gateway_server, "_sessions", {"session_1": object()})
    monkeypatch.setattr(gateway_server, "get_gateway", lambda: _GatewayStub())
    monkeypatch.setattr(gateway_server, "_task_hub_has_dispatch_eligible_items", lambda: True)

    gateway_server._maybe_wake_heartbeat_after_autonomous_cron(
        run_status="success",
        is_autonomous=True,
        reason="cron_autonomous_run:job-1",
    )
    gateway_server._maybe_wake_heartbeat_after_autonomous_cron(
        run_status="success",
        is_autonomous=True,
        reason="cron_autonomous_run:job-1",
    )

    assert len(heartbeat_stub.calls) == 1
    assert heartbeat_stub.calls[0] == ("session_1", "cron_autonomous_run:job-1")


def test_autonomous_cron_wake_ignores_cron_role_sessions(monkeypatch, tmp_path: Path):
    class _HeartbeatStub:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def request_heartbeat_next(self, session_id: str, reason: str = "wake"):
            self.calls.append((session_id, reason))

        def register_session(self, session) -> None:
            return

    class _Admission:
        action = "start_new_run"
        run_id = "run-1"
        attempt_id = "attempt-1"

    class _AdmissionStub:
        def admit(self, *args, **kwargs):
            return _Admission()

        def mark_completed(self, *args, **kwargs):
            return None

        def mark_failed(self, *args, **kwargs):
            return None

    heartbeat_stub = _HeartbeatStub()
    monkeypatch.setattr(gateway_server, "_heartbeat_service", heartbeat_stub)
    monkeypatch.setattr(gateway_server, "_workflow_admission_service", lambda: _AdmissionStub())
    monkeypatch.setattr(gateway_server, "_task_hub_has_dispatch_eligible_items", lambda: True)
    monkeypatch.setenv("UA_CRON_WAKE_HEARTBEAT_ON_AUTONOMOUS_RUN", "1")
    monkeypatch.setattr(
        gateway_server,
        "_sessions",
        {
            "cron_claude_code_intel_sync": GatewaySession(
                session_id="cron_claude_code_intel_sync",
                user_id="system",
                workspace_dir=str(tmp_path / "cron"),
                metadata={"session_role": "cron", "run_kind": "cron", "skip_heartbeat": True},
            ),
            "daemon_simone_heartbeat": GatewaySession(
                session_id="daemon_simone_heartbeat",
                user_id="daemon",
                workspace_dir=str(tmp_path / "heartbeat"),
                metadata={"session_role": "heartbeat", "run_kind": "heartbeat"},
            ),
        },
    )
    monkeypatch.setattr(
        gateway_server,
        "get_gateway",
        lambda: SimpleNamespace(list_live_sessions=lambda: []),
    )

    gateway_server._maybe_wake_heartbeat_after_autonomous_cron(
        run_status="success",
        is_autonomous=True,
        reason="cron_autonomous_run:job-claude",
    )

    assert heartbeat_stub.calls == [("daemon_simone_heartbeat", "cron_autonomous_run:job-claude")]


def test_process_heartbeat_investigation_notification_includes_origin_findings_href(monkeypatch):
    captured: dict = {}

    async def _fake_notify_operator(payload):
        captured.update(payload)
        return True, "sent"

    monkeypatch.setattr(
        gateway_server,
        "_get_activity_event",
        lambda _event_id: {
            "id": "ntf_origin",
            "metadata": {
                "heartbeat_findings_artifact_href": "/storage?scope=workspaces&path=session/work_products/heartbeat_findings_latest.json",
            },
        },
    )
    monkeypatch.setattr(gateway_server, "_update_notification_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(gateway_server, "_record_activity_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(gateway_server, "_add_notification", lambda **kwargs: kwargs)
    monkeypatch.setattr(gateway_server, "_notify_operator_of_heartbeat_recommendation", _fake_notify_operator)

    asyncio.run(
        gateway_server._process_heartbeat_investigation_notification(
            {
                "session_id": "session_test",
                "metadata": {
                    "source_notification_id": "ntf_origin",
                    "operator_review_required": True,
                    "classification": "known_rule_only",
                    "recommended_next_step": "rerun heartbeat",
                    "email_summary": "summary",
                },
            }
        )
    )

    assert captured["originating_notification_id"] == "ntf_origin"
    assert (
        captured["metadata"]["heartbeat_findings_artifact_href"]
        == "/storage?scope=workspaces&path=session/work_products/heartbeat_findings_latest.json"
    )


def test_generate_daily_briefing_includes_non_cron_artifacts_section(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(gateway_server, "_cron_service", None)

    gateway_server._add_notification(
        kind="autonomous_heartbeat_completed",
        title="Autonomous Heartbeat Activity Completed",
        message="heartbeat completed independent work",
        severity="info",
        metadata={
            "source": "heartbeat",
            "heartbeat_artifacts": [
                {
                    "scope": "workspaces",
                    "relative_path": "session_abc/work_products/summary.md",
                    "storage_href": "/storage?tab=explorer&scope=workspaces&path=session_abc%2Fwork_products%2Fsummary.md",
                    "api_url": "",
                }
            ],
        },
    )

    payload = gateway_server._generate_autonomous_daily_briefing_artifact(now_ts=time.time())
    markdown_path = gateway_server.ARTIFACTS_DIR / payload["markdown"]["relative_path"]
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "## Non-Cron Autonomous Artifact Outputs" in markdown
    assert "scope=workspaces" in markdown
    assert payload["counts"]["non_cron_artifacts"] == 1


def test_generate_daily_briefing_backfills_from_persisted_cron_runs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", tmp_path / "artifacts")
    cron = CronService(_StubGateway(), tmp_path / "workspaces")
    monkeypatch.setattr(gateway_server, "_cron_service", cron)

    cron_ws = tmp_path / "workspaces" / "cron_autonomous_backfill"
    cron_ws.mkdir(parents=True, exist_ok=True)
    job = cron.add_job(
        user_id="cron_system",
        workspace_dir=str(cron_ws),
        command="sync Todoist backlog and publish summary",
        every_raw="30m",
        enabled=True,
        metadata={"autonomous": True, "system_job": "todoist_sync"},
    )

    now_ts = time.time()
    cron.store.append_run(
        CronRunRecord(
            run_id="run_backfill_1",
            job_id=job.job_id,
            status="success",
            scheduled_at=now_ts - 5,
            started_at=now_ts - 3,
            finished_at=now_ts - 1,
            error=None,
            output_preview="sync complete",
        )
    )

    payload = gateway_server._generate_autonomous_daily_briefing_artifact(now_ts=now_ts)
    assert payload["counts"]["completed"] == 1
    assert payload["counts"]["failed"] == 0

    report_json = tmp_path / "artifacts" / "autonomous-briefings" / payload["day_slug"] / "briefing.json"
    data = report_json.read_text(encoding="utf-8")
    assert '"cron_backfill_applied": true' in data
    assert '"sync Todoist backlog and publish summary"' in data


def test_generate_daily_briefing_warns_when_only_self_run_exists(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gateway_server, "_notifications", [])
    monkeypatch.setattr(gateway_server, "ARTIFACTS_DIR", tmp_path / "artifacts")
    cron = CronService(_StubGateway(), tmp_path / "workspaces")
    monkeypatch.setattr(gateway_server, "_cron_service", cron)

    cron_ws = tmp_path / "workspaces" / "cron_daily_briefing"
    cron_ws.mkdir(parents=True, exist_ok=True)
    job = cron.add_job(
        user_id="cron_system",
        workspace_dir=str(cron_ws),
        command="daily briefing",
        cron_expr="0 7 * * *",
        timezone="UTC",
        enabled=True,
        metadata={
            "autonomous": True,
            "briefing": True,
            "system_job": gateway_server.AUTONOMOUS_DAILY_BRIEFING_JOB_KEY,
        },
    )

    now_ts = time.time()
    cron.store.append_run(
        CronRunRecord(
            run_id="run_brief_self_1",
            job_id=job.job_id,
            status="success",
            scheduled_at=now_ts - 5,
            started_at=now_ts - 3,
            finished_at=now_ts - 1,
            error=None,
            output_preview="briefing complete",
        )
    )

    payload = gateway_server._generate_autonomous_daily_briefing_artifact(now_ts=now_ts)
    assert payload["counts"]["completed"] == 0

    report_md = tmp_path / "artifacts" / "autonomous-briefings" / payload["day_slug"] / "DAILY_BRIEFING.md"
    markdown = report_md.read_text(encoding="utf-8")
    assert "## Data Quality Warnings" in markdown
    assert "Only the daily briefing cron job was observed in this window" in markdown


def test_storage_explorer_href_normalizes_file_path_to_parent_for_preview():
    file_rel = "youtube-tutorial-creation/2026-02-25/abc123/README.md"
    href = gateway_server._storage_explorer_href(scope="artifacts", path=file_rel, preview=file_rel)
    parsed = urllib.parse.urlparse(href)
    params = urllib.parse.parse_qs(parsed.query)

    assert params["tab"] == ["explorer"]
    assert params["scope"] == ["artifacts"]
    assert params["root_source"] == ["local"]
    assert params["path"] == ["youtube-tutorial-creation/2026-02-25/abc123"]
    assert params["preview"] == [file_rel]


def test_storage_explorer_href_supports_root_file_preview_without_path():
    href = gateway_server._storage_explorer_href(scope="workspaces", path="", preview="run.log")
    parsed = urllib.parse.urlparse(href)
    params = urllib.parse.parse_qs(parsed.query)

    assert params["tab"] == ["explorer"]
    assert params["scope"] == ["workspaces"]
    assert params["root_source"] == ["local"]
    assert "path" not in params
    assert params["preview"] == ["run.log"]

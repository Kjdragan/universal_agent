import time
from pathlib import Path
from types import SimpleNamespace

from universal_agent import gateway_server
from universal_agent.cron_service import CronService


class _StubGateway:
    async def create_session(self, user_id: str, workspace_dir: str):
        return SimpleNamespace(user_id=user_id, workspace_dir=workspace_dir)


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


def test_emit_cron_event_daily_briefing_includes_report_links(tmp_path: Path, monkeypatch):
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
    assert latest["kind"] == "autonomous_daily_briefing_ready"
    assert latest["title"] == "Daily Autonomous Briefing Ready"
    assert latest["metadata"]["report_api_url"].startswith("/api/artifacts/files/autonomous-briefings/")
    assert latest["metadata"]["report_relative_path"].endswith("/DAILY_BRIEFING.md")

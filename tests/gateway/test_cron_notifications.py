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
    assert latest["title"] == "Cron Run Succeeded"
    assert latest["metadata"]["job_id"] == job.job_id
    assert latest["metadata"]["status"] == "success"

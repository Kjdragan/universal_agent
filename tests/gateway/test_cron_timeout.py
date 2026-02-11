import asyncio
from pathlib import Path
from types import SimpleNamespace

from universal_agent.cron_service import CronService


class _SlowGateway:
    async def create_session(self, user_id: str, workspace_dir: str):
        return SimpleNamespace(user_id=user_id, workspace_dir=workspace_dir)

    async def run_query(self, session, request):
        await asyncio.sleep(1.5)
        return SimpleNamespace(response_text="late")


def test_cron_job_timeout_is_enforced(tmp_path: Path):
    service = CronService(_SlowGateway(), tmp_path)
    job = service.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "cron_timeout"),
        command="slow task",
        every_raw="10m",
        timeout_seconds=1,
    )

    record = asyncio.run(service.run_job_now(job.job_id, reason="manual"))

    assert record.status == "error"
    assert "timed out" in (record.error or "").lower()

from pathlib import Path
from types import SimpleNamespace

import asyncio

from universal_agent.cron_service import CronService


class _OutputGateway:
    async def create_session(self, user_id: str, workspace_dir: str):
        return SimpleNamespace(user_id=user_id, workspace_dir=workspace_dir)

    async def run_query(self, session, request):
        ws = Path(session.workspace_dir)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "chart.png").write_bytes(b"png")
        (ws / "notes.md").write_text("summary", encoding="utf-8")
        (ws / "script.py").write_text("print('keep')", encoding="utf-8")
        return SimpleNamespace(response_text="ok")


def test_cron_moves_root_outputs_into_work_products(tmp_path: Path):
    service = CronService(_OutputGateway(), tmp_path)
    workspace = tmp_path / "cron_outputs"
    job = service.add_job(
        user_id="cron",
        workspace_dir=str(workspace),
        command="generate outputs",
        every_raw="10m",
    )

    record = asyncio.run(service.run_job_now(job.job_id, reason="manual"))
    assert record.status == "success"

    assert (workspace / "work_products" / "media" / "chart.png").exists()
    assert (workspace / "work_products" / "notes.md").exists()
    assert (workspace / "script.py").exists()
    assert not (workspace / "chart.png").exists()
    assert not (workspace / "notes.md").exists()

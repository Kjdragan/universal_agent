import asyncio
from pathlib import Path
from types import SimpleNamespace

from universal_agent.cron_service import CronService
from universal_agent.durable.db import connect_runtime_db
from universal_agent.durable.migrations import ensure_schema
from universal_agent.workflow_admission import WorkflowAdmissionService


class _SlowGateway:
    async def create_session(self, user_id: str, workspace_dir: str):
        return SimpleNamespace(user_id=user_id, workspace_dir=workspace_dir)

    async def run_query(self, session, request):
        await asyncio.sleep(1.5)
        return SimpleNamespace(response_text="late")


def test_cron_job_timeout_is_enforced(tmp_path: Path):
    runtime_db_path = str((tmp_path / "runtime_state.db").resolve())
    service = CronService(_SlowGateway(), tmp_path)
    service._workflow_admission_service = lambda: WorkflowAdmissionService(runtime_db_path)
    service._schedule_retry_run = lambda **_: None
    job = service.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "cron_timeout"),
        command="slow task",
        every_raw="10m",
        timeout_seconds=1,
    )

    record = asyncio.run(service.run_job_now(job.job_id, reason="manual"))

    assert record.status == "retry_queued"
    assert "timed out" in (record.error or "").lower()
    conn = connect_runtime_db(runtime_db_path)
    ensure_schema(conn)
    attempts = conn.execute(
        "SELECT attempt_number, status FROM run_attempts WHERE run_id = ? ORDER BY attempt_number ASC",
        (str(record.workflow_run_id),),
    ).fetchall()
    conn.close()
    assert [int(row["attempt_number"]) for row in attempts] == [1, 2]
    assert str(attempts[0]["status"]) == "failed"
    assert str(attempts[1]["status"]) == "queued"

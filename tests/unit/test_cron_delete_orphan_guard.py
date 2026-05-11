"""Regression guard: deleted cron jobs must not generate orphan retry storms.

Background (observed 2026-05-11): test cron `2df80b6f95` was deleted via
`DELETE /api/v1/cron/jobs/{id}`. The DB row + `cron_jobs.json` entry both
got removed. But Simone's outbox kept emitting `[WARNING] Chron Retry
Queued` emails every ~5 minutes for that same job_id for 90+ minutes
afterward.

Root cause: `_finalize_workflow_attempt` → `_schedule_retry_run` →
`_run_job` is a chain of `asyncio.create_task(...)` calls. Each task holds
the `CronJob` object in closure. When `delete_job` purged the registry,
the in-flight task chain kept running off the stale reference, each
iteration emitting another retry-queued event and queuing the next tick.

Fix: `_run_job` now short-circuits with `status="skipped"` if
`job.job_id not in self.jobs`. `delete_job` also clears `running_jobs`
so any new scheduled tick sees the job as not-currently-running.

These tests pin the contract.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from universal_agent.cron_service import CronService
from universal_agent.workflow_admission import WorkflowAdmissionService


class _StubGateway:
    async def create_session(self, user_id: str, workspace_dir: str):
        return SimpleNamespace(user_id=user_id, workspace_dir=workspace_dir)

    async def run_query(self, session, request, **_kwargs):
        return SimpleNamespace(response_text="")


def _service(tmp_path: Path) -> CronService:
    svc = CronService(_StubGateway(), tmp_path)
    runtime_db_path = str((tmp_path / "runtime_state.db").resolve())
    svc._workflow_admission_service = lambda: WorkflowAdmissionService(runtime_db_path)
    return svc


def test_delete_job_clears_running_state(tmp_path: Path) -> None:
    """delete_job must clear `running_jobs` so a re-create or scheduled
    tick after deletion doesn't think the job is mid-flight."""
    svc = _service(tmp_path)
    job = svc.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "cron_dl"),
        command="echo hi",
        every_raw="10m",
    )
    svc.running_jobs.add(job.job_id)
    svc.running_job_scheduled_at[job.job_id] = 0.0
    svc.delete_job(job.job_id)
    assert job.job_id not in svc.jobs
    assert job.job_id not in svc.running_jobs
    assert job.job_id not in svc.running_job_scheduled_at


def test_run_job_short_circuits_for_deleted_job(tmp_path: Path) -> None:
    """_run_job called with a CronJob whose id is no longer in self.jobs
    must short-circuit with status='skipped', NOT execute the command,
    NOT call dispatch_vp_mission, NOT queue a retry."""
    svc = _service(tmp_path)
    job = svc.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "cron_run_deleted"),
        command="echo hi",
        every_raw="10m",
    )
    job_id = job.job_id
    # Delete via the API so registry + running state are both clean.
    svc.delete_job(job_id)
    # Hold the CronJob reference (mimicking what the orphan asyncio task
    # would do) and call _run_job directly.
    record = asyncio.run(svc._run_job(job, scheduled_at=None, reason="retry"))
    assert record.status == "skipped"
    assert record.error == "job_deleted_before_run"
    assert record.job_id == job_id


def test_run_job_proceeds_normally_for_live_job(tmp_path: Path) -> None:
    """Sanity check: the guard MUST NOT short-circuit a job that's still
    in the registry. Otherwise we'd block all legitimate runs."""
    svc = _service(tmp_path)
    job = svc.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "cron_live"),
        command="echo hi",
        every_raw="10m",
    )
    # Patch _schedule_retry_run to no-op so the test doesn't actually try
    # to spawn a real subprocess; we only need to confirm the guard
    # didn't trip.
    svc._schedule_retry_run = lambda **_kwargs: None
    record = asyncio.run(svc._run_job(job, scheduled_at=None, reason="manual"))
    # Job is in registry, so the guard does NOT short-circuit. The actual
    # run will fail (no real command) but the FAILURE PATH means we got
    # past the deleted-job guard. status will be 'failure', NOT 'skipped'
    # with error='job_deleted_before_run'.
    assert record.error != "job_deleted_before_run"

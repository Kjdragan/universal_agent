"""Lightweight crons must not starve behind the heavy LLM-cron semaphore.

Incident 2026-07-27 (proactive_health `cron_loop_liveness`): prod runs
``UA_CRON_MAX_CONCURRENCY=1`` to serialize heavy LLM crons, so the
per-minute stdlib-only ``simone_chat_auto_complete`` queued 1456 s behind
``paper_to_podcast_daily``'s nightly 02:00 UTC run (CRONDIAG:
``sem_wait=1456.6s sem_free_before=0``), blowing the 3-minute liveness
grace. Fix: jobs with ``metadata.lightweight=True`` acquire a separate
``_lightweight_semaphore`` in ``CronService._run_job`` instead of the
heavy ``_semaphore``.

These tests prove the lane split at the ``_run_job`` acquire site using
``UA_CRON_MOCK_RESPONSE=1`` (short-circuits inside the semaphore, before
any subprocess/LLM work).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from universal_agent.cron_service import CronService
from universal_agent.workflow_admission import WorkflowAdmissionService


class _StubGateway:
    async def create_session(self, user_id: str, workspace_dir: str):
        return SimpleNamespace(
            session_id="sess-test", user_id=user_id,
            workspace_dir=workspace_dir, metadata={},
        )

    async def run_query(self, session, request, event_callback=None):
        return SimpleNamespace(response_text="", metadata={}, tool_calls=0)


def _make_service(tmp_path: Path, monkeypatch) -> CronService:
    monkeypatch.setenv("UA_CRON_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("UA_CRON_MOCK_RESPONSE", "1")
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(tmp_path / "runtime_state.db"))
    monkeypatch.setenv("UA_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("UA_DISABLE_MEMORY", "1")
    svc = CronService(_StubGateway(), tmp_path)
    runtime_db_path = str((tmp_path / "runtime_state.db").resolve())
    svc._workflow_admission_service = lambda: WorkflowAdmissionService(runtime_db_path)
    return svc


def _add_job(svc: CronService, tmp_path: Path, *, lightweight: bool):
    metadata = {"skip_task_hub_link": True}
    if lightweight:
        metadata["lightweight"] = True
    return svc.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / ("ws_lw" if lightweight else "ws_heavy")),
        command=(
            "!script universal_agent.scripts.simone_chat_auto_complete"
            if lightweight
            else "Generate the nightly podcast"
        ),
        every_raw="60s",
        metadata=metadata,
    )


def test_lightweight_job_runs_while_heavy_semaphore_exhausted(tmp_path, monkeypatch):
    async def scenario():
        svc = _make_service(tmp_path, monkeypatch)
        assert svc.max_concurrency == 1
        job = _add_job(svc, tmp_path, lightweight=True)
        # Occupy the single heavy slot for the whole test — this is
        # paper_to_podcast_daily holding the lane for ~25 minutes.
        await svc._semaphore.acquire()
        svc.running_jobs.add(job.job_id)
        record = await asyncio.wait_for(
            svc._run_job(job, scheduled_at=time.time() - 30, reason="schedule"),
            timeout=10,
        )
        assert record.status == "success"
        # The lightweight run never touched the heavy lane.
        assert svc._semaphore._value == 0

    asyncio.run(scenario())


def test_heavy_job_still_serialized_on_heavy_semaphore(tmp_path, monkeypatch):
    """Guardrail: non-lightweight jobs still queue on the heavy lane."""

    async def scenario():
        svc = _make_service(tmp_path, monkeypatch)
        job = _add_job(svc, tmp_path, lightweight=False)
        await svc._semaphore.acquire()
        svc.running_jobs.add(job.job_id)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                svc._run_job(job, scheduled_at=time.time() - 30, reason="schedule"),
                timeout=1.0,
            )

    asyncio.run(scenario())

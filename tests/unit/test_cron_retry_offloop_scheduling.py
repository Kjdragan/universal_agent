"""Regression: retry scheduling from the lightweight-cron worker thread.

The lightweight ``!script`` cron path finalizes its run via
``await asyncio.to_thread(self._finalize_workflow_attempt, ...)``
(``cron_service.py``, the 2026-05-26 hot-patch that keeps the blocking
``shutil.copytree`` inside ``mark_completed`` off the event loop).

When such a run exits non-zero, ``_finalize_workflow_attempt`` walks the
retry path and calls ``_schedule_retry_run``. Before the fix that helper
used a bare ``asyncio.create_task(self._run_job(...))`` — but
``create_task`` is loop-affine, and inside an ``asyncio.to_thread`` worker
thread there is **no running event loop**. The result was the production
symptom seen on 2026-05-28/29/30::

    ERROR:universal_agent.cron_service:Chron job <id> failed: no running event loop
    RuntimeWarning: coroutine 'CronService._run_job' was never awaited

This test reproduces that exact call shape (schedule a retry from a thread
with no running loop, with the gateway's main loop captured on the service)
and asserts the retry coroutine is actually scheduled onto the main loop
instead of raising.
"""
from __future__ import annotations

import asyncio

import pytest

from universal_agent.cron_service import CronService


class _StubJob:
    def __init__(self, job_id: str = "ut_retry_job") -> None:
        self.job_id = job_id


@pytest.mark.asyncio
async def test_schedule_retry_from_worker_thread_does_not_need_running_loop() -> None:
    # Bare service: _schedule_retry_run only touches running_jobs, _run_job,
    # and (after the fix) _loop. Avoid the heavy gateway/store construction.
    svc = object.__new__(CronService)
    svc.running_jobs = set()
    svc._loop = asyncio.get_running_loop()

    invoked = asyncio.Event()
    captured: dict[str, object] = {}

    async def _fake_run_job(job, **kwargs):  # noqa: ANN001
        captured["job_id"] = job.job_id
        captured["kwargs"] = kwargs
        invoked.set()

    svc._run_job = _fake_run_job  # type: ignore[assignment]

    job = _StubJob()

    def _finalize_in_thread() -> None:
        # Mirrors the real call site: _finalize_workflow_attempt ->
        # _schedule_retry_run, executed inside asyncio.to_thread (no loop
        # in this thread). Must not raise "no running event loop".
        svc._schedule_retry_run(
            job=job,
            scheduled_at=123.0,
            reason="retry",
            dispatch_key="dk-1",
            workflow_run_id="wf-1",
            workflow_attempt_id="att-2",
        )

    # Run the finalize step in a worker thread, exactly like _run_job does.
    await asyncio.to_thread(_finalize_in_thread)

    # The retry coroutine must have been scheduled onto the main loop.
    await asyncio.wait_for(invoked.wait(), timeout=2.0)

    assert captured["job_id"] == "ut_retry_job"
    assert captured["kwargs"]["skip_workflow_admission"] is True
    assert captured["kwargs"]["workflow_attempt_id"] == "att-2"
    assert "ut_retry_job" in svc.running_jobs

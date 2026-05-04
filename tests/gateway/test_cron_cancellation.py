"""Cron cancellation handling.

Background: Python 3.8+ made `asyncio.CancelledError` a `BaseException`
subclass. Without an explicit handler in `_run_job`, a deploy/restart
that cancels in-flight cron tasks bypasses the generic `except Exception`
branch — the run record is never finalized, and on the next gateway
startup the recovery sweep re-emits a phantom "Cron Run Failed" event.

These tests pin down:
  - CancelledError DURING execution finalizes status='cancelled'
    (not 'running', not 'error').
  - The CancelledError still propagates so asyncio cancellation
    semantics complete cleanly.
  - The completion event emitted carries status='cancelled' so the
    gateway emitter routes it to the info-severity cron_run_cancelled
    notification (verified separately in test_cron_notifications).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from universal_agent.cron_service import CronService
from universal_agent.workflow_admission import WorkflowAdmissionService


class _CancellingGateway:
    """Gateway whose run_query gets cancelled before completing."""

    async def create_session(self, user_id: str, workspace_dir: str):
        return SimpleNamespace(user_id=user_id, workspace_dir=workspace_dir)

    async def run_query(self, session, request, **_kwargs):
        # Sleep long enough that the test can cancel us from outside.
        await asyncio.sleep(60)
        return SimpleNamespace(response_text="never")


def test_cron_cancellation_finalizes_status_cancelled(tmp_path: Path):
    """When the running cron task is cancelled mid-flight, the record
    must end up with status='cancelled' (not stuck on 'running' and not
    coerced to 'error'). The CancelledError must also re-raise so the
    asyncio task itself reports as cancelled to its awaiter."""
    runtime_db_path = str((tmp_path / "runtime_state.db").resolve())
    service = CronService(_CancellingGateway(), tmp_path)
    service._workflow_admission_service = lambda: WorkflowAdmissionService(runtime_db_path)
    service._schedule_retry_run = lambda **_: None

    job = service.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "cron_cancel"),
        command="long task",
        every_raw="10m",
    )

    captured_events: list[dict] = []
    service._emit_event = lambda payload: captured_events.append(payload)  # type: ignore[assignment]

    async def _drive():
        task = asyncio.create_task(service.run_job_now(job.job_id, reason="manual"))
        # Yield enough times for _run_job to enter its inner try block
        # and start awaiting run_query.
        for _ in range(5):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_drive())

    # The completion event must have been emitted with status='cancelled'.
    completed = [e for e in captured_events if e.get("type") == "cron_run_completed"]
    assert completed, f"no cron_run_completed event emitted; got {[e.get('type') for e in captured_events]}"
    final_run = completed[-1]["run"]
    assert final_run["status"] == "cancelled", (
        f"expected status='cancelled', got {final_run['status']!r}"
    )
    assert "cancelled" in (final_run.get("error") or "").lower()

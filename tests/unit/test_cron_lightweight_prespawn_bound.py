"""The lightweight cron claim→spawn window must be bounded (residual wedge).

task_3773b30ae294 (2026-07-27, Simone heartbeat diagnosis): the ORIGINAL
2026-07-23 wedge fix bounded spawn+drain via ``LivenessWatchdog``, but the
window BETWEEN the ``running_jobs`` claim and the spawn — dominated by the
``workflow_admission.mark_running`` synchronous sqlite write (15 s
``busy_timeout`` + workspace-scaffold FS I/O) — stayed unbounded and ON the
event loop. Under DB lock contention the job was claimed but dispatch never
started, so the spawn's idle-kill never armed and the scheduler skipped the
claimed job every tick: the flapping ``cron_scheduler_dispatching``
invariant ("timeouts never arm if dispatch never starts").

These tests hang ``mark_running`` like a lock-contended write and prove the
new ``cron_pre_spawn_timeout_seconds`` bound releases the claim within ~2×
the bound instead of stranding the job behind a 30 s (prod: unbounded)
stall — and that the event loop keeps ticking while the write runs
off-loop in a thread.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from universal_agent.cron_service import CronService


class _StubGateway:
    async def create_session(self, user_id: str, workspace_dir: str):
        return SimpleNamespace(
            session_id="sess-test", user_id=user_id,
            workspace_dir=workspace_dir, metadata={},
        )

    async def run_query(self, session, request, event_callback=None):
        return SimpleNamespace(response_text="", metadata={}, tool_calls=0)


class _HungAdmissionStub:
    """``mark_running`` blocks like a lock-contended sqlite write; every
    other admission call is a benign no-op. ``queue_retry`` declines a new
    attempt so the error path finalizes as needs_review instead of
    scheduling a background retry task the test would race against."""

    def __init__(self, hang_seconds: float):
        self.hang_seconds = hang_seconds
        self.mark_running_started = threading.Event()

    def mark_running(self, *args, **kwargs):
        self.mark_running_started.set()
        time.sleep(self.hang_seconds)

    def queue_retry(self, *args, **kwargs):
        return SimpleNamespace(action="mark_needs_review", attempt_id=None)

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _make_service(tmp_path: Path, monkeypatch) -> CronService:
    monkeypatch.setenv("UA_CRON_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("UA_CRON_MOCK_RESPONSE", "0")  # must REACH the lightweight branch
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(tmp_path / "runtime_state.db"))
    monkeypatch.setenv("UA_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("UA_DISABLE_MEMORY", "1")
    return CronService(_StubGateway(), tmp_path)


def _add_lightweight_job(svc: CronService, tmp_path: Path):
    return svc.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "ws_lw"),
        command="!script universal_agent.scripts.simone_chat_auto_complete",
        every_raw="60s",
        metadata={"skip_task_hub_link": True, "lightweight": True},
    )


def test_hung_mark_running_releases_claim_within_bound(tmp_path, monkeypatch):
    monkeypatch.setenv("UA_CRON_PRE_SPAWN_TIMEOUT_SECONDS", "1")
    # This test asserts the STEADY-STATE production shape: no deploy window,
    # so the stall classifies `error` (not `cancelled`). All three window
    # signals fire spuriously under pytest — the process is always "recently
    # started" (signal 2), and any earlier test that exercised stop() leaves
    # the module-global _SHUTDOWN_REQUESTED latched (signal 3) — so pin the
    # window off rather than fighting each signal individually.
    import universal_agent.cron_service as cron_service_module
    monkeypatch.setattr(
        cron_service_module, "_is_deploy_window_active", lambda: False
    )

    async def scenario():
        svc = _make_service(tmp_path, monkeypatch)
        job = _add_lightweight_job(svc, tmp_path)
        stub = _HungAdmissionStub(hang_seconds=30.0)
        svc._workflow_admission_service = lambda: stub

        svc.running_jobs.add(job.job_id)  # the scheduler's claim
        t0 = time.time()
        record = await asyncio.wait_for(
            svc._run_job(
                job,
                scheduled_at=time.time() - 30,
                reason="schedule",
                dispatch_key="test-prespawn",
                workflow_run_id="wf-test",
                workflow_attempt_id="at-test",
                skip_workflow_admission=True,
            ),
            timeout=10,
        )
        elapsed = time.time() - t0

        assert stub.mark_running_started.is_set(), "hung write never started"
        assert record.status == "error"
        assert "claim→spawn window stalled" in (record.error or "")
        # The whole point: the claim is RELEASED, so the next scheduler tick
        # can fire the job again (next_run_at was already advanced at claim).
        assert job.job_id not in svc.running_jobs
        # Bounded within ~2× the 1 s bound — nowhere near the 30 s hang.
        assert elapsed < 5.0, f"claim held {elapsed:.1f}s despite 1s pre-spawn bound"

    asyncio.run(scenario())


def test_healthy_mark_running_still_reaches_spawn(tmp_path, monkeypatch):
    """Guardrail: the bound must not break the healthy path — a fast
    mark_running proceeds to the spawn exactly as before the fix."""
    monkeypatch.setenv("UA_CRON_PRE_SPAWN_TIMEOUT_SECONDS", "30")

    async def scenario():
        svc = _make_service(tmp_path, monkeypatch)
        job = _add_lightweight_job(svc, tmp_path)

        calls: list[str] = []

        class _FastAdmissionStub(_HungAdmissionStub):
            def mark_running(self, *args, **kwargs):
                calls.append("mark_running")

        svc._workflow_admission_service = lambda: _FastAdmissionStub(0.0)

        async def _fake_spawn(**kwargs):
            calls.append("spawn")
            proc = SimpleNamespace(returncode=0)
            return proc, b"CRON_OK\n", b"", False

        import universal_agent.cron_service as cron_service_module
        monkeypatch.setattr(
            cron_service_module, "_spawn_script_with_timeout", _fake_spawn
        )

        svc.running_jobs.add(job.job_id)
        record = await asyncio.wait_for(
            svc._run_job(
                job,
                scheduled_at=time.time() - 30,
                reason="schedule",
                dispatch_key="test-prespawn-ok",
                workflow_run_id="wf-test",
                workflow_attempt_id="at-test",
                skip_workflow_admission=True,
            ),
            timeout=10,
        )

        assert calls == ["mark_running", "spawn"], calls
        assert record.status == "success"
        assert job.job_id not in svc.running_jobs

    asyncio.run(scenario())

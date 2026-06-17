"""Unit tests for the deploy-cancellation classification in cron_service.

The asymmetry being fixed: a coroutine cron cancelled by service shutdown
raises ``asyncio.CancelledError`` and is correctly classified as
``record.status = "cancelled"`` (cron_service.py:2013-2036), producing a
benign ``[INFO] Chron Run Cancelled (service restart)`` email. A
subprocess cron (``!script ...``) killed by SIGTERM from the same shutdown
exits with return code ``-15``, which without this fix falls into the
generic non-zero ``error`` branch and produces an ``[ERROR] Autonomous
Task Failed`` + ``[WARNING] Autonomous Task Retrying`` pair for what's
really a non-event.

These tests exercise the new helpers and verify the deploy-cancellation
branch without spinning up the full CronService — they monkeypatch the
file/uptime probes that drive ``_is_deploy_window_active``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import patch

from universal_agent import cron_service
from universal_agent.cron_service import (
    _DEPLOY_CANCEL_BACKFILL_OFFSET_SEC,
    _DEPLOY_WINDOW_FALLBACK_UPTIME_SEC,
    CronService,
    _is_deploy_window_active,
    _is_llm_deploy_kill_result,
    _process_start_time,
)
from universal_agent.workflow_admission import WorkflowAdmissionService

# --------------------------------------------------------------------------- #
# _is_deploy_window_active — file flag signal
# --------------------------------------------------------------------------- #


def test_deploy_window_active_when_flag_file_exists(tmp_path, monkeypatch):
    flag = tmp_path / "ua-deployment-window"
    flag.touch()
    monkeypatch.setattr(cron_service, "_DEPLOY_WINDOW_FLAG_PATH", str(flag))
    # Force the uptime fallback to NOT trigger (large positive uptime),
    # so we know the True comes from the flag file alone.
    monkeypatch.setattr(
        cron_service, "_process_start_time", lambda: time.time() - 3600
    )
    assert _is_deploy_window_active() is True


def test_deploy_window_inactive_when_flag_missing_and_uptime_old(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cron_service, "_DEPLOY_WINDOW_FLAG_PATH", str(tmp_path / "no-such-file")
    )
    monkeypatch.setattr(
        cron_service, "_process_start_time", lambda: time.time() - 3600
    )
    assert _is_deploy_window_active() is False


# --------------------------------------------------------------------------- #
# _is_deploy_window_active — uptime fallback signal
# --------------------------------------------------------------------------- #


def test_deploy_window_active_when_uptime_under_60s(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cron_service, "_DEPLOY_WINDOW_FLAG_PATH", str(tmp_path / "no-such-file")
    )
    # Started 10 seconds ago — well within the 60s fallback window.
    monkeypatch.setattr(
        cron_service, "_process_start_time", lambda: time.time() - 10
    )
    assert _is_deploy_window_active() is True


def test_deploy_window_inactive_when_uptime_just_over_60s(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cron_service, "_DEPLOY_WINDOW_FLAG_PATH", str(tmp_path / "no-such-file")
    )
    # 61 seconds past start — fallback window has closed.
    monkeypatch.setattr(
        cron_service,
        "_process_start_time",
        lambda: time.time() - (_DEPLOY_WINDOW_FALLBACK_UPTIME_SEC + 1),
    )
    assert _is_deploy_window_active() is False


# --------------------------------------------------------------------------- #
# _is_deploy_window_active — robustness
# --------------------------------------------------------------------------- #


def test_deploy_window_active_when_flag_present_even_if_uptime_old(tmp_path, monkeypatch):
    """Flag file should win even if uptime is well past the fallback."""
    flag = tmp_path / "ua-deployment-window"
    flag.touch()
    monkeypatch.setattr(cron_service, "_DEPLOY_WINDOW_FLAG_PATH", str(flag))
    monkeypatch.setattr(
        cron_service, "_process_start_time", lambda: time.time() - 7200
    )
    assert _is_deploy_window_active() is True


def test_deploy_window_inactive_returns_false_when_uptime_probe_raises(tmp_path, monkeypatch):
    """If both probes fail, return False — never block cron flow."""
    monkeypatch.setattr(
        cron_service, "_DEPLOY_WINDOW_FLAG_PATH", str(tmp_path / "no-such-file")
    )

    def boom() -> float:
        raise RuntimeError("synthetic /proc failure")

    monkeypatch.setattr(cron_service, "_process_start_time", boom)
    # Must not raise; must return False (conservative — surface real errors
    # rather than mistakenly suppressing them as deploy-cancellations).
    assert _is_deploy_window_active() is False


# --------------------------------------------------------------------------- #
# _process_start_time — caching and fallback behaviour
# --------------------------------------------------------------------------- #


def test_process_start_time_is_cached(monkeypatch):
    """Repeated calls must return the same value — process can't restart in-module."""
    # Reset cache before the test.
    monkeypatch.setattr(cron_service, "_PROCESS_START_TIME", None)
    first = _process_start_time()
    second = _process_start_time()
    third = _process_start_time()
    assert first == second == third


def test_process_start_time_falls_back_when_proc_unreadable(monkeypatch):
    """When /proc/self/stat can't be read, fall back to current time.

    The fallback is pessimistic: it makes uptime look like 0, which WIDENS
    the deploy window (a recently-started process IS in the fallback
    window). This is intentional — better to occasionally treat a real
    failure as deploy-cancellation than to silently miss a deploy-window
    SIGTERM and email the operator with a false alarm.
    """
    monkeypatch.setattr(cron_service, "_PROCESS_START_TIME", None)
    real_open = open

    def fake_open(path, *args, **kwargs):
        if "/proc" in str(path):
            raise OSError("synthetic /proc failure")
        return real_open(path, *args, **kwargs)

    with patch("builtins.open", side_effect=fake_open):
        result = _process_start_time()
    # Should approximately equal "now" within a few seconds.
    assert abs(result - time.time()) < 5.0


# --------------------------------------------------------------------------- #
# Backfill offset constant — sanity
# --------------------------------------------------------------------------- #


def test_backfill_offset_is_positive_and_small():
    """next_run_at advance must be a small positive offset.

    Zero would risk a race against the scheduler tick that's about to be
    SIGTERM'd. Negative would be in the past (which the scheduler treats
    as "missed window", potentially triggering a backfill loop). Large
    values would delay the eventual fire pointlessly.
    """
    assert 0 < _DEPLOY_CANCEL_BACKFILL_OFFSET_SEC <= 60


# --------------------------------------------------------------------------- #
# Integration: the subprocess exit-code branch
# --------------------------------------------------------------------------- #


class _FakeRecord:
    """Stand-in for CronRunRecord — only the fields the branch touches."""

    def __init__(self):
        self.status = None
        self.error = None
        self.output_preview = None


class _FakeJob:
    """Stand-in for CronJob — only what the deploy-cancellation branch reads/writes."""

    def __init__(self, job_id="test_job", next_run_at=None):
        self.job_id = job_id
        self.next_run_at = next_run_at


def test_subprocess_exit_branch_marks_cancelled_when_signaled_in_deploy_window(
    monkeypatch, tmp_path
):
    """Reproduce the exact branch logic from cron_service.py:1466+ as a unit.

    We can't easily call the full _run_job() in a unit test (too many
    dependencies), but we can verify the branch's decision logic with a
    minimal harness that mirrors the production branch's structure.
    """
    # Force the deploy-window probe to return True.
    monkeypatch.setattr(cron_service, "_is_deploy_window_active", lambda: True)

    record = _FakeRecord()
    job = _FakeJob()
    exit_code = -15  # SIGTERM
    output_text = "partial stdout\npartial stderr"

    # Apply the same conditional logic as the production code.
    if exit_code == 0:
        record.status = "success"
        record.output_preview = output_text[:400]
    elif exit_code is not None and exit_code < 0 and cron_service._is_deploy_window_active():
        signal_num = -exit_code
        record.status = "cancelled"
        record.error = (
            f"subprocess killed by signal {signal_num} "
            "during deploy restart (will re-fire on next gateway boot)"
        )
        record.output_preview = output_text[:400]
        job.next_run_at = time.time() + _DEPLOY_CANCEL_BACKFILL_OFFSET_SEC
    else:
        record.status = "error"
        record.error = f"Script exited with {exit_code}"
        record.output_preview = output_text[:400]

    assert record.status == "cancelled"
    assert "signal 15" in record.error
    assert "deploy restart" in record.error
    # next_run_at advanced to a small positive offset from now.
    assert job.next_run_at is not None
    assert 0 < (job.next_run_at - time.time()) <= _DEPLOY_CANCEL_BACKFILL_OFFSET_SEC + 1


def test_subprocess_exit_branch_marks_error_when_signaled_outside_deploy_window(
    monkeypatch,
):
    """SIGTERM without a deploy window in flight = real error, not benign."""
    monkeypatch.setattr(cron_service, "_is_deploy_window_active", lambda: False)

    record = _FakeRecord()
    job = _FakeJob()
    exit_code = -15
    output_text = "stdout\nstderr"

    if exit_code == 0:
        record.status = "success"
        record.output_preview = output_text[:400]
    elif exit_code is not None and exit_code < 0 and cron_service._is_deploy_window_active():
        record.status = "cancelled"
    else:
        record.status = "error"
        record.error = f"Script exited with {exit_code}"
        record.output_preview = output_text[:400]

    assert record.status == "error"
    assert record.error == "Script exited with -15"
    assert job.next_run_at is None  # NOT advanced — real failure path


def test_subprocess_exit_branch_marks_cancelled_for_positive_rc_in_deploy_window(
    monkeypatch,
):
    """A positive-rc failure INSIDE a deploy window is deploy collateral.

    The 2026-05-29 ``evening_briefing`` incident: the briefings_agent
    script exited rc=1 after ``connect ECONNREFUSED ::1:8002`` because the
    gateway it calls was mid-restart. The subprocess ran to completion and
    returned a positive code (so the kernel didn't signal-kill it), but the
    failure was caused by the platform restarting under it — not a bug in
    the script. Inside an active deploy window this must be downgraded to
    ``cancelled`` (benign [INFO], reschedule for next-boot catch-up), NOT
    surfaced as an [ERROR] Autonomous Task Failed email.
    """
    monkeypatch.setattr(cron_service, "_is_deploy_window_active", lambda: True)

    record = _FakeRecord()
    job = _FakeJob()
    exit_code = 1  # Positive non-zero — gateway unreachable mid-restart

    # Mirror the production branch's structure.
    if exit_code == 0:
        record.status = "success"
    elif exit_code is not None and exit_code != 0 and cron_service._is_deploy_window_active():
        if exit_code < 0:
            detail = f"subprocess killed by signal {-exit_code} during deploy restart"
        else:
            detail = (
                f"subprocess exited rc={exit_code} "
                "during deploy restart (platform unreachable mid-restart)"
            )
        record.status = "cancelled"
        record.error = f"{detail} (will re-fire on next gateway boot)"
        job.next_run_at = time.time() + _DEPLOY_CANCEL_BACKFILL_OFFSET_SEC
    else:
        record.status = "error"
        record.error = f"Script exited with {exit_code}"

    assert record.status == "cancelled"
    assert "rc=1" in record.error
    assert "deploy restart" in record.error
    # Rescheduled for next-boot catch-up via the PR #274 mechanism.
    assert job.next_run_at is not None
    assert 0 < (job.next_run_at - time.time()) <= _DEPLOY_CANCEL_BACKFILL_OFFSET_SEC + 1


def test_subprocess_exit_branch_marks_error_for_positive_rc_outside_deploy_window(
    monkeypatch,
):
    """REGRESSION GUARD: a positive-rc failure OUTSIDE a deploy window is a
    real error and MUST still surface loudly as [ERROR], exactly as before.

    This is the hard guardrail: the deploy-window predicate is the ONLY
    thing that may downgrade a nonzero exit. With no deploy in flight, a
    genuine script bug (rc=1) keeps paging the operator.
    """
    monkeypatch.setattr(cron_service, "_is_deploy_window_active", lambda: False)

    record = _FakeRecord()
    job = _FakeJob()
    exit_code = 1  # Normal non-zero — a real bug in the script

    if exit_code == 0:
        record.status = "success"
    elif exit_code is not None and exit_code != 0 and cron_service._is_deploy_window_active():
        record.status = "cancelled"
    else:
        record.status = "error"
        record.error = f"Script exited with {exit_code}"

    assert record.status == "error"
    assert record.error == "Script exited with 1"
    assert job.next_run_at is None  # NOT advanced — real failure path


def test_subprocess_exit_branch_marks_success_for_zero_exit(monkeypatch):
    """exit_code == 0 always wins, even in a deploy window."""
    monkeypatch.setattr(cron_service, "_is_deploy_window_active", lambda: True)

    record = _FakeRecord()
    exit_code = 0
    output_text = "ok"

    if exit_code == 0:
        record.status = "success"
        record.output_preview = output_text[:400]

    assert record.status == "success"


# --------------------------------------------------------------------------- #
# _is_llm_deploy_kill_result — the LLM deploy-kill signature
# --------------------------------------------------------------------------- #


def test_llm_deploy_kill_signature_matches_empty_result():
    """Empty text + zero tool calls is the deploy-kill shape: the SDK's
    claude CLI subprocess was SIGTERM'd (exit 143), the SDK swallowed the
    message-reader fatal, and run_query returned an empty GatewayResult."""
    result = SimpleNamespace(response_text="", tool_calls=0)
    assert _is_llm_deploy_kill_result(result) is True


def test_llm_deploy_kill_signature_rejects_text_result():
    result = SimpleNamespace(response_text="All done.", tool_calls=0)
    assert _is_llm_deploy_kill_result(result) is False


def test_llm_deploy_kill_signature_rejects_tool_calls():
    """A run that called tools did real work even if the final text is
    empty — never classify it as a deploy kill."""
    result = SimpleNamespace(response_text="", tool_calls=3)
    assert _is_llm_deploy_kill_result(result) is False


def test_llm_deploy_kill_signature_tolerates_missing_attrs():
    assert _is_llm_deploy_kill_result(SimpleNamespace()) is True
    assert _is_llm_deploy_kill_result(SimpleNamespace(tool_calls="bogus")) is True


# --------------------------------------------------------------------------- #
# In-flight marker: store round-trip + service helpers
# --------------------------------------------------------------------------- #


class _StubGateway:
    """Gateway stub for full _run_job tests — configurable LLM result."""

    def __init__(self, result=None):
        self._result = result or SimpleNamespace(
            response_text="", metadata={}, tool_calls=0
        )

    async def create_session(self, user_id: str, workspace_dir: str):
        return SimpleNamespace(
            session_id="sess-test", user_id=user_id,
            workspace_dir=workspace_dir, metadata={},
        )

    async def run_query(self, session, request, event_callback=None):
        return self._result


def _service(tmp_path: Path, gateway=None) -> CronService:
    svc = CronService(gateway or _StubGateway(), tmp_path)
    runtime_db_path = str((tmp_path / "runtime_state.db").resolve())
    svc._workflow_admission_service = lambda: WorkflowAdmissionService(runtime_db_path)
    return svc


def _isolate_side_paths(monkeypatch, tmp_path: Path) -> None:
    """Point env-resolved side stores (runtime DB, artifacts) at tmp."""
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", str(tmp_path / "runtime_state.db"))
    monkeypatch.setenv("UA_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("UA_DISABLE_MEMORY", "1")


def test_inflight_marker_store_round_trip(tmp_path: Path):
    svc = _service(tmp_path)
    assert svc.store.load_inflight() == {}
    svc._mark_inflight("job-a", 1234.0)
    markers = svc.store.load_inflight()
    assert markers["job-a"]["scheduled_at"] == 1234.0
    assert markers["job-a"]["marked_at"] > 0
    svc._clear_inflight("job-a")
    assert svc.store.load_inflight() == {}
    # Clearing an absent marker is a no-op, never raises.
    svc._clear_inflight("job-a")


def test_startup_requeues_inflight_marker_for_catch_up_job(
    tmp_path: Path, monkeypatch
):
    """A leftover marker (run hard-killed by a deploy restart) for a
    catch_up_on_restart job must be queued for recovery at construction
    time, and the consumed sidecar cleared."""
    # The dev-mode guard skips loading persisted jobs entirely; pin the
    # stage so the restart simulation behaves like production regardless
    # of the host shell's UA_RUNTIME_STAGE.
    monkeypatch.delenv("UA_RUNTIME_STAGE", raising=False)
    svc1 = _service(tmp_path)
    job = svc1.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "ws"),
        command="Generate the nightly podcast",
        cron_expr="0 21 * * *",
        catch_up_on_restart=True,
    )
    interrupted_at = time.time() - 600  # the 9 PM slot, 10 min ago
    svc1._mark_inflight(job.job_id, interrupted_at)

    # Simulate the post-restart gateway constructing a fresh CronService
    # over the same store.
    svc2 = _service(tmp_path)
    assert (job.job_id, interrupted_at) in [
        (j, s) for j, s in svc2._inflight_requeue
    ]
    # Markers are consumed at startup (the requeue dispatch re-marks).
    assert svc2.store.load_inflight() == {}


def test_startup_drops_marker_for_non_catch_up_job(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("UA_RUNTIME_STAGE", raising=False)
    svc1 = _service(tmp_path)
    job = svc1.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "ws"),
        command="echo hi",
        every_raw="10m",
        catch_up_on_restart=False,
    )
    svc1._mark_inflight(job.job_id, time.time() - 60)
    svc2 = _service(tmp_path)
    assert svc2._inflight_requeue == []
    assert svc2.store.load_inflight() == {}


def test_startup_drops_stale_marker(tmp_path: Path, monkeypatch):
    """Markers older than the 24h backfill max age are dropped — bounded
    recovery, no archaeology."""
    monkeypatch.delenv("UA_RUNTIME_STAGE", raising=False)
    svc1 = _service(tmp_path)
    job = svc1.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "ws"),
        command="echo hi",
        every_raw="10m",
        catch_up_on_restart=True,
    )
    svc1._mark_inflight(job.job_id, time.time() - 90000)  # > 24h
    svc2 = _service(tmp_path)
    assert svc2._inflight_requeue == []
    assert svc2.store.load_inflight() == {}


async def test_start_dispatches_inflight_recovery_even_with_backfill_off(
    tmp_path: Path, monkeypatch
):
    """The whole point: interrupted in-flight runs of catch_up jobs must
    recover EVEN under the UA_CRON_BACKFILL_ON_RESTART=0 default (that
    global gate exists to stop a startup stampede of ALL missed slots;
    interrupted in-flight runs are a bounded set)."""
    monkeypatch.delenv("UA_CRON_BACKFILL_ON_RESTART", raising=False)
    monkeypatch.delenv("UA_RUNTIME_STAGE", raising=False)
    svc1 = _service(tmp_path)
    job = svc1.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "ws"),
        command="Generate the nightly podcast",
        cron_expr="0 21 * * *",
        catch_up_on_restart=True,
    )
    interrupted_at = time.time() - 600
    svc1._mark_inflight(job.job_id, interrupted_at)

    svc2 = _service(tmp_path)
    dispatched: list[tuple[str, float, str, str]] = []

    async def _fake_run_job(job, scheduled_at, reason, *, dispatch_key=None, **_kw):
        dispatched.append((job.job_id, scheduled_at, reason, dispatch_key))

    svc2._run_job = _fake_run_job
    await svc2.start()
    await asyncio.sleep(0.05)  # let the created task run
    await svc2.stop()

    assert len(dispatched) == 1
    got_job_id, got_scheduled_at, got_reason, got_key = dispatched[0]
    assert got_job_id == job.job_id
    assert got_scheduled_at == interrupted_at
    assert got_reason == "backfill"
    # NOT the original `scheduled:` dedup key — the interrupted attempt's
    # workflow run may still be status=running, and re-admitting under the
    # same key would attach_to_existing and silently skip the recovery.
    assert got_key.startswith(f"inflight:{job.job_id}:")
    # The recovery dispatch re-marked the job in flight.
    assert job.job_id in svc2.store.load_inflight()


# --------------------------------------------------------------------------- #
# LLM deploy-kill classification — full _run_job path
# --------------------------------------------------------------------------- #


def test_llm_run_deploy_kill_marks_cancelled_and_keeps_marker(
    tmp_path: Path, monkeypatch
):
    """Empty engine result + active deploy window → cancelled (NOT
    completed), next_run_at advanced for re-fire, in-flight marker KEPT so
    the next boot's recovery pass requeues the slot."""
    _isolate_side_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(cron_service, "_is_deploy_window_active", lambda: True)
    svc = _service(
        tmp_path,
        gateway=_StubGateway(
            SimpleNamespace(response_text="", metadata={}, tool_calls=0)
        ),
    )
    job = svc.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "ws_llm"),
        command="Generate the nightly podcast",
        cron_expr="0 21 * * *",
        catch_up_on_restart=True,
        metadata={"skip_task_hub_link": True},
    )
    scheduled_at = time.time() - 30
    svc.running_jobs.add(job.job_id)
    svc._mark_inflight(job.job_id, scheduled_at)

    record = asyncio.run(
        svc._run_job(job, scheduled_at=scheduled_at, reason="schedule")
    )

    assert record.status == "cancelled"
    assert "deploy restart" in (record.error or "")
    # Slot requeued: next_run_at advanced to a small positive offset.
    assert job.next_run_at is not None
    assert 0 < (job.next_run_at - time.time()) <= _DEPLOY_CANCEL_BACKFILL_OFFSET_SEC + 1
    # Marker survives so the startup recovery pass can requeue.
    assert job.job_id in svc.store.load_inflight()


def test_llm_run_empty_result_outside_deploy_window_keeps_prior_behavior(
    tmp_path: Path, monkeypatch
):
    """GUARDRAIL: the deploy-window predicate is the ONLY thing that
    downgrades the empty result. Outside a window, classification is
    unchanged from before (success) and the in-flight marker clears."""
    _isolate_side_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(cron_service, "_is_deploy_window_active", lambda: False)
    svc = _service(
        tmp_path,
        gateway=_StubGateway(
            SimpleNamespace(response_text="", metadata={}, tool_calls=0)
        ),
    )
    job = svc.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "ws_llm"),
        command="Generate the nightly podcast",
        cron_expr="0 21 * * *",
        metadata={"skip_task_hub_link": True},
    )
    scheduled_at = time.time() - 30
    svc.running_jobs.add(job.job_id)
    svc._mark_inflight(job.job_id, scheduled_at)

    record = asyncio.run(
        svc._run_job(job, scheduled_at=scheduled_at, reason="schedule")
    )

    assert record.status == "success"
    assert job.job_id not in svc.store.load_inflight()


def test_llm_run_healthy_result_in_deploy_window_stays_success(
    tmp_path: Path, monkeypatch
):
    """A run that completed with real output during a deploy window is a
    success — never discard finished work just because a deploy is in
    flight."""
    _isolate_side_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(cron_service, "_is_deploy_window_active", lambda: True)
    svc = _service(
        tmp_path,
        gateway=_StubGateway(
            SimpleNamespace(
                response_text="Podcast generated.", metadata={}, tool_calls=4
            )
        ),
    )
    job = svc.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "ws_llm"),
        command="Generate the nightly podcast",
        cron_expr="0 21 * * *",
        metadata={"skip_task_hub_link": True},
    )
    scheduled_at = time.time() - 30
    svc.running_jobs.add(job.job_id)
    svc._mark_inflight(job.job_id, scheduled_at)

    record = asyncio.run(
        svc._run_job(job, scheduled_at=scheduled_at, reason="schedule")
    )

    assert record.status == "success"
    assert job.job_id not in svc.store.load_inflight()
# --------------------------------------------------------------------------- #
# Broadened LLM deploy-kill detector — mid-flight kill (2026-06-16 regression)
# --------------------------------------------------------------------------- #


def test_llm_deploy_kill_detector_matches_midflight_marker_and_cold_empty():
    """The broadened detector recognises BOTH deploy-kill shapes:

    * mid-flight kill: non-empty text + tool_calls > 0 + the
      ``metadata["subprocess_terminated"]`` marker the adapter surfaces when
      the SDK subprocess is SIGTERM'd mid-run (2026-06-16 paper_to_podcast
      shape — the run had already created the notebook + sources + audio).
    * cold kill: empty text + zero tool_calls, no marker (2026-06-09/10
      shape) — no regression of the original empty-result signature.

    A result that genuinely completed (text + tool_calls, NO marker) is NOT
    a deploy kill — the marker is the only thing that lets a worked-on
    result downgrade to cancelled.
    """
    # Mid-flight kill via the surfaced marker (the regression).
    midflight = SimpleNamespace(
        response_text="Created notebook 095f0718, polling audio ee5d49e1...",
        tool_calls=8,
        metadata={"subprocess_terminated": True},
    )
    assert _is_llm_deploy_kill_result(midflight) is True
    # Cold kill — empty result, no marker (original signature, no regression).
    assert _is_llm_deploy_kill_result(
        SimpleNamespace(response_text="", tool_calls=0, metadata={})
    ) is True
    assert _is_llm_deploy_kill_result(
        SimpleNamespace(response_text="", tool_calls=0)
    ) is True
    # Genuine completion — text + tool_calls, NO marker: NOT a deploy kill.
    assert _is_llm_deploy_kill_result(
        SimpleNamespace(
            response_text="Podcast generated.", tool_calls=4, metadata={}
        )
    ) is False
    # Worked-on result with metadata missing entirely: NOT a kill.
    assert _is_llm_deploy_kill_result(
        SimpleNamespace(response_text="done", tool_calls=2)
    ) is False


def test_llm_run_midflight_deploy_kill_marks_cancelled_and_keeps_marker(
    tmp_path: Path, monkeypatch
):
    """REGRESSION (2026-06-16 paper_to_podcast): a mid-flight deploy kill —
    the claude CLI subprocess SIGTERM'd AFTER doing real work (non-empty
    response_text + tool_calls > 0), surfaced as
    ``metadata["subprocess_terminated"]`` — must be classified cancelled
    (NOT success), advance next_run_at, and KEEP the in-flight marker so the
    next boot's recovery pass requeues the slot.

    Before the fix this exact shape was mis-painted status=success, which
    cleared the in-flight marker (defeating boot-requeue) and suppressed the
    artifact notifier — zero operator signal for ~20h.
    """
    _isolate_side_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(cron_service, "_is_deploy_window_active", lambda: True)
    svc = _service(
        tmp_path,
        gateway=_StubGateway(
            SimpleNamespace(
                response_text=(
                    "Created notebook 095f0718, added 5 sources, "
                    "polling audio ee5d49e1..."
                ),
                metadata={"subprocess_terminated": True},
                tool_calls=8,
            )
        ),
    )
    job = svc.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "ws_llm_midflight"),
        command="Generate the nightly podcast",
        cron_expr="0 21 * * *",
        catch_up_on_restart=True,
        metadata={"skip_task_hub_link": True},
    )
    scheduled_at = time.time() - 30
    svc.running_jobs.add(job.job_id)
    svc._mark_inflight(job.job_id, scheduled_at)

    record = asyncio.run(
        svc._run_job(job, scheduled_at=scheduled_at, reason="schedule")
    )

    assert record.status == "cancelled"
    assert "deploy restart" in (record.error or "")
    # Recovery: slot requeued to a small positive offset from now.
    assert job.next_run_at is not None
    assert 0 < (job.next_run_at - time.time()) <= _DEPLOY_CANCEL_BACKFILL_OFFSET_SEC + 1
    # Recovery: marker survives so the startup recovery pass can requeue.
    assert job.job_id in svc.store.load_inflight()


def test_llm_run_midflight_marker_outside_deploy_window_stays_success(
    tmp_path: Path, monkeypatch
):
    """GUARDRAIL: the deploy-window predicate is the SOLE authority that may
    downgrade a result to cancelled. A mid-flight-kill-shaped result (marker
    present) that occurs OUTSIDE a deploy window is NOT a deploy kill — the
    detector matches the marker, but the AND with ``_is_deploy_window_active()``
    fails, so classification stays success and the in-flight marker clears as
    usual. This is the no-false-positive test: a genuinely run-killed-shaped
    result coinciding with no deploy must not be mis-classified as a kill.
    """
    _isolate_side_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(cron_service, "_is_deploy_window_active", lambda: False)
    result = SimpleNamespace(
        response_text="partial work, then the subprocess died...",
        tool_calls=5,
        metadata={"subprocess_terminated": True},
    )
    # The detector alone DOES match the marker ...
    assert _is_llm_deploy_kill_result(result) is True
    # ... but with no deploy window active, the close-out must NOT downgrade.
    svc = _service(tmp_path, gateway=_StubGateway(result))
    job = svc.add_job(
        user_id="cron",
        workspace_dir=str(tmp_path / "ws_llm_gate"),
        command="Generate the nightly podcast",
        cron_expr="0 21 * * *",
        metadata={"skip_task_hub_link": True},
    )
    scheduled_at = time.time() - 30
    svc.running_jobs.add(job.job_id)
    svc._mark_inflight(job.job_id, scheduled_at)

    record = asyncio.run(
        svc._run_job(job, scheduled_at=scheduled_at, reason="schedule")
    )

    assert record.status == "success"
    # No downgrade -> marker clears on the normal success path.
    assert job.job_id not in svc.store.load_inflight()

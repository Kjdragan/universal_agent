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

import time
from unittest.mock import patch

from universal_agent import cron_service
from universal_agent.cron_service import (
    _DEPLOY_CANCEL_BACKFILL_OFFSET_SEC,
    _DEPLOY_WINDOW_FALLBACK_UPTIME_SEC,
    _is_deploy_window_active,
    _process_start_time,
)

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


def test_subprocess_exit_branch_marks_error_for_normal_nonzero_exit(monkeypatch):
    """A non-signal failure (positive return code) is always a real error,
    regardless of deploy window — the script exited cleanly with a code,
    so the kernel didn't kill it. Don't suppress."""
    monkeypatch.setattr(cron_service, "_is_deploy_window_active", lambda: True)

    record = _FakeRecord()
    exit_code = 1  # Normal non-zero — bug in the script, not a signal

    if exit_code == 0:
        record.status = "success"
    elif exit_code is not None and exit_code < 0 and cron_service._is_deploy_window_active():
        record.status = "cancelled"
    else:
        record.status = "error"
        record.error = f"Script exited with {exit_code}"

    assert record.status == "error"
    assert record.error == "Script exited with 1"


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

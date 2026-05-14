"""Unit tests for the pure classify_worker_exit function in worker_exit_classifier.py.

Covers all six outcome buckets and the priority ordering of the
classification decision tree:
  cancelled_mid_run > timeout_killed > signaled > nonzero_exit >
  clean_exit_zero | clean_exit_zero_no_disposition
"""

from __future__ import annotations

import pytest

from universal_agent.services.worker_exit_classifier import (
    PROTOCOL_VIOLATION_REASONS,
    WorkerExit,
    classify_worker_exit,
)


class TestClassifyWorkerExitCancelled:
    """was_cancelled takes top priority regardless of other flags."""

    def test_cancelled_only(self):
        result = classify_worker_exit(return_code=0, was_cancelled=True)
        assert result.outcome == "cancelled_mid_run"
        assert result.is_failure is True
        assert result.is_protocol_violation is False

    def test_cancelled_nonzero_rc(self):
        result = classify_worker_exit(return_code=1, was_cancelled=True)
        assert result.outcome == "cancelled_mid_run"

    def test_cancelled_with_timeout(self):
        result = classify_worker_exit(
            return_code=0, was_cancelled=True, was_timeout_killed=True,
        )
        assert result.outcome == "cancelled_mid_run"

    def test_cancelled_with_signal(self):
        result = classify_worker_exit(
            return_code=None, was_cancelled=True, was_signaled=True,
        )
        assert result.outcome == "cancelled_mid_run"

    def test_cancelled_task_not_closed(self):
        result = classify_worker_exit(
            return_code=0, was_cancelled=True, task_closed_normally=False,
        )
        assert result.outcome == "cancelled_mid_run"


class TestClassifyWorkerExitTimeoutKilled:
    """was_timeout_killed is second priority."""

    def test_timeout_only(self):
        result = classify_worker_exit(return_code=None, was_timeout_killed=True)
        assert result.outcome == "timeout_killed"
        assert result.is_failure is True
        assert result.is_protocol_violation is False

    def test_timeout_with_signal_flag(self):
        result = classify_worker_exit(
            return_code=0, was_timeout_killed=True, was_signaled=True,
        )
        assert result.outcome == "timeout_killed"

    def test_timeout_task_not_closed(self):
        result = classify_worker_exit(
            return_code=0, was_timeout_killed=True, task_closed_normally=False,
        )
        assert result.outcome == "timeout_killed"


class TestClassifyWorkerExitSignaled:
    """was_signaled is third priority."""

    def test_signaled_only(self):
        result = classify_worker_exit(return_code=None, was_signaled=True)
        assert result.outcome == "signaled"
        assert result.is_failure is True
        assert result.is_protocol_violation is False

    def test_signaled_with_zero_rc(self):
        result = classify_worker_exit(return_code=0, was_signaled=True)
        assert result.outcome == "signaled"

    def test_signaled_task_not_closed(self):
        result = classify_worker_exit(
            return_code=None, was_signaled=True, task_closed_normally=False,
        )
        assert result.outcome == "signaled"


class TestClassifyWorkerExitNonzero:
    """Non-zero return code is fourth priority."""

    def test_rc_1(self):
        result = classify_worker_exit(return_code=1)
        assert result.outcome == "nonzero_exit"
        assert result.is_failure is True
        assert result.is_protocol_violation is False

    def test_rc_negative(self):
        result = classify_worker_exit(return_code=-1)
        assert result.outcome == "nonzero_exit"

    def test_rc_137(self):
        result = classify_worker_exit(return_code=137)
        assert result.outcome == "nonzero_exit"

    def test_rc_none(self):
        result = classify_worker_exit(return_code=None)
        assert result.outcome == "nonzero_exit"

    def test_rc_255(self):
        result = classify_worker_exit(return_code=255)
        assert result.outcome == "nonzero_exit"


class TestClassifyWorkerExitClean:
    """rc=0 splits into clean success vs protocol violation."""

    def test_clean_exit_zero(self):
        result = classify_worker_exit(return_code=0, task_closed_normally=True)
        assert result.outcome == "clean_exit_zero"
        assert result.is_failure is False
        assert result.is_protocol_violation is False

    def test_clean_exit_zero_no_disposition(self):
        result = classify_worker_exit(return_code=0, task_closed_normally=False)
        assert result.outcome == "clean_exit_zero_no_disposition"
        assert result.is_failure is False
        assert result.is_protocol_violation is True

    def test_clean_exit_zero_default_closed(self):
        """task_closed_normally defaults to True."""
        result = classify_worker_exit(return_code=0)
        assert result.outcome == "clean_exit_zero"


class TestWorkerExitDataclass:
    """WorkerExit frozen dataclass basics."""

    def test_to_dict(self):
        we = WorkerExit(
            outcome="clean_exit_zero",
            is_protocol_violation=False,
            is_failure=False,
        )
        d = we.to_dict()
        assert d == {
            "outcome": "clean_exit_zero",
            "is_protocol_violation": False,
            "is_failure": False,
        }

    def test_frozen(self):
        we = WorkerExit(outcome="signaled", is_protocol_violation=False, is_failure=True)
        with pytest.raises(AttributeError):
            we.outcome = "clean_exit_zero"

    def test_equality(self):
        a = WorkerExit(outcome="nonzero_exit", is_protocol_violation=False, is_failure=True)
        b = WorkerExit(outcome="nonzero_exit", is_protocol_violation=False, is_failure=True)
        assert a == b


class TestProtocolViolationReasons:
    """PROTOCOL_VIOLATION_REASONS constant sanity check."""

    def test_has_expected_sites(self):
        for site in ("cron", "vp_cli", "demo"):
            assert site in PROTOCOL_VIOLATION_REASONS

    def test_values_are_prefixed(self):
        for key, value in PROTOCOL_VIOLATION_REASONS.items():
            assert value.startswith("protocol_violation_")

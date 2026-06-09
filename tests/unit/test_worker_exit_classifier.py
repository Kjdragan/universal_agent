"""Tests for worker_exit_classifier -- pure classification logic only.

Covers classify_worker_exit (the pure function) and the WorkerExit dataclass.
Does NOT cover park_task_for_protocol_violation, find_active_assignment_for_task,
or task_was_closed_normally (those touch the DB and are out of scope).
"""

from __future__ import annotations

import dataclasses

import pytest

from universal_agent.services.worker_exit_classifier import (
    WorkerExit,
    WorkerOutcome,
    classify_worker_exit,
)

# -- WorkerExit dataclass ----------------------------------------------------

class TestWorkerExit:
    def test_frozen(self) -> None:
        we = WorkerExit(
            outcome="clean_exit_zero",
            is_protocol_violation=False,
            is_failure=False,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            we.outcome = "mutated"  # type: ignore[misc]

    def test_to_dict(self) -> None:
        we = WorkerExit(
            outcome="timeout_killed",
            is_protocol_violation=False,
            is_failure=True,
        )
        d = we.to_dict()
        assert d == {
            "outcome": "timeout_killed",
            "is_protocol_violation": False,
            "is_failure": True,
        }


# -- classify_worker_exit ----------------------------------------------------

class TestClassifyWorkerExit:
    """Exercise every branch and the full precedence chain."""

    # --- cancelled_mid_run --------------------------------------------------

    def test_cancelled_mid_run(self) -> None:
        we = classify_worker_exit(return_code=0, was_cancelled=True)
        assert we.outcome == "cancelled_mid_run"
        assert we.is_failure is True
        assert we.is_protocol_violation is False

    def test_cancelled_ignores_return_code(self) -> None:
        """Cancelled takes precedence -- return_code is irrelevant."""
        we = classify_worker_exit(return_code=137, was_cancelled=True)
        assert we.outcome == "cancelled_mid_run"

    # --- timeout_killed -----------------------------------------------------

    def test_timeout_killed(self) -> None:
        we = classify_worker_exit(return_code=0, was_timeout_killed=True)
        assert we.outcome == "timeout_killed"
        assert we.is_failure is True

    def test_timeout_takes_precedence_over_signaled(self) -> None:
        """timeout_killed > signaled in the precedence chain."""
        we = classify_worker_exit(
            return_code=0,
            was_timeout_killed=True,
            was_signaled=True,
        )
        assert we.outcome == "timeout_killed"

    # --- signaled -----------------------------------------------------------

    def test_signaled(self) -> None:
        we = classify_worker_exit(return_code=0, was_signaled=True)
        assert we.outcome == "signaled"
        assert we.is_failure is True

    # --- nonzero_exit -------------------------------------------------------

    @pytest.mark.parametrize("rc", [1, 137, 255])
    def test_nonzero_return_code(self, rc: int) -> None:
        we = classify_worker_exit(return_code=rc)
        assert we.outcome == "nonzero_exit"
        assert we.is_failure is True
        assert we.is_protocol_violation is False

    def test_none_return_code_is_nonzero_exit(self) -> None:
        """return_code=None is treated as a nonzero exit."""
        we = classify_worker_exit(return_code=None)
        assert we.outcome == "nonzero_exit"
        assert we.is_failure is True

    # --- clean_exit_zero ----------------------------------------------------

    def test_clean_exit_zero(self) -> None:
        """rc=0 with task_closed_normally=True is a clean success."""
        we = classify_worker_exit(
            return_code=0,
            task_closed_normally=True,
        )
        assert we.outcome == "clean_exit_zero"
        assert we.is_failure is False
        assert we.is_protocol_violation is False

    def test_clean_exit_zero_not_closed_is_protocol_violation(self) -> None:
        """rc=0 but task NOT closed -> protocol violation, NOT a failure."""
        we = classify_worker_exit(
            return_code=0,
            task_closed_normally=False,
        )
        assert we.outcome == "clean_exit_zero_no_disposition"
        assert we.is_protocol_violation is True
        assert we.is_failure is False

    # --- default task_closed_normally ---------------------------------------

    def test_default_task_closed_normally_is_true(self) -> None:
        """When omitted, task_closed_normally defaults to True."""
        we = classify_worker_exit(return_code=0)
        assert we.outcome == "clean_exit_zero"

    # --- Precedence chain: cancelled > timeout > signaled > nonzero > clean --

    def test_precedence_cancelled_over_timeout(self) -> None:
        we = classify_worker_exit(
            return_code=0,
            was_cancelled=True,
            was_timeout_killed=True,
        )
        assert we.outcome == "cancelled_mid_run"

    def test_precedence_cancelled_over_signaled(self) -> None:
        we = classify_worker_exit(
            return_code=0,
            was_cancelled=True,
            was_signaled=True,
        )
        assert we.outcome == "cancelled_mid_run"

    def test_precedence_timeout_over_signaled(self) -> None:
        we = classify_worker_exit(
            return_code=0,
            was_timeout_killed=True,
            was_signaled=True,
        )
        assert we.outcome == "timeout_killed"

    def test_precedence_signaled_over_nonzero_rc(self) -> None:
        we = classify_worker_exit(
            return_code=1,
            was_signaled=True,
        )
        assert we.outcome == "signaled"

    def test_precedence_nonzero_over_clean(self) -> None:
        """Nonzero rc prevents the clean-exit path entirely."""
        we = classify_worker_exit(
            return_code=1,
            task_closed_normally=True,
        )
        assert we.outcome == "nonzero_exit"


# -- WorkerOutcome literal coverage ------------------------------------------

class TestWorkerOutcomeValues:
    """Parametrized smoke test: every declared outcome can be produced."""

    @pytest.mark.parametrize(
        "outcome, kwargs",
        [
            ("cancelled_mid_run", dict(return_code=0, was_cancelled=True)),
            ("timeout_killed", dict(return_code=0, was_timeout_killed=True)),
            ("signaled", dict(return_code=0, was_signaled=True)),
            ("nonzero_exit", dict(return_code=1)),
            ("clean_exit_zero", dict(return_code=0, task_closed_normally=True)),
            (
                "clean_exit_zero_no_disposition",
                dict(return_code=0, task_closed_normally=False),
            ),
        ],
    )
    def test_outcome_producible(
        self,
        outcome: WorkerOutcome,
        kwargs: dict,
    ) -> None:
        we = classify_worker_exit(**kwargs)
        assert we.outcome == outcome

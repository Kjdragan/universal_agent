"""Guard tests for the durable worker-pool run-status / tunable constants.

These pin the extracted constants in ``durable/worker_pool.py`` to the values
the rest of the durable run-state machine recognizes. The cross-module
assertions are the meaningful part: if a future edit drifts a worker-pool
status constant away from the vocabulary ``durable/state.py`` accepts (e.g.
renaming ``RUN_STATUS_COMPLETED`` to ``"complete"``), the worker pool would
silently write statuses the run-state machine doesn't treat as terminal/success
— these tests fail first.
"""

from __future__ import annotations

from universal_agent.durable import state, worker_pool

# -- run/attempt status string constants ------------------------------------

def test_run_status_string_values() -> None:
    assert worker_pool.RUN_STATUS_QUEUED == "queued"
    assert worker_pool.RUN_STATUS_RUNNING == "running"
    assert worker_pool.RUN_STATUS_COMPLETED == "completed"
    assert worker_pool.RUN_STATUS_FAILED == "failed"


def test_completed_is_a_recognized_success_status() -> None:
    # The worker pool writes RUN_STATUS_COMPLETED on a successful run; the
    # run-state machine must classify it as success (and therefore terminal).
    assert worker_pool.RUN_STATUS_COMPLETED in state._RUN_SUCCESS_STATUSES
    assert worker_pool.RUN_STATUS_COMPLETED in state._RUN_TERMINAL_STATUSES


def test_failed_is_a_recognized_terminal_status() -> None:
    assert worker_pool.RUN_STATUS_FAILED in state._RUN_TERMINAL_STATUSES
    # ...but failure is not a success.
    assert worker_pool.RUN_STATUS_FAILED not in state._RUN_SUCCESS_STATUSES


def test_queued_and_running_are_non_terminal() -> None:
    assert worker_pool.RUN_STATUS_QUEUED not in state._RUN_TERMINAL_STATUSES
    assert worker_pool.RUN_STATUS_RUNNING not in state._RUN_TERMINAL_STATUSES


# -- worker-pool timing / sizing tunables -----------------------------------

def test_numeric_tunables_are_positive_ints() -> None:
    for name in (
        "_WORKER_ID_SUFFIX_LEN",
        "_DRAIN_POLL_SECONDS",
        "_LOOP_ERROR_BACKOFF_SECONDS",
        "_QUEUE_POLL_LIMIT",
        "_QUEUE_DEPTH_SCAN_LIMIT",
        "_MONITOR_INTERVAL_SECONDS",
        "_POOL_STATS_LOG_INTERVAL_SECONDS",
    ):
        value = getattr(worker_pool, name)
        assert isinstance(value, int), f"{name} should be an int"
        assert value > 0, f"{name} should be positive"


def test_worker_id_suffix_len_matches_generated_ids() -> None:
    # WorkerConfig generates ids as ``worker_<N hex chars>``; the suffix length
    # constant must match what the default factory produces.
    suffix = worker_pool.WorkerConfig().worker_id.removeprefix("worker_")
    assert len(suffix) == worker_pool._WORKER_ID_SUFFIX_LEN


def test_queue_depth_scan_is_at_least_poll_limit() -> None:
    # The monitor's queue-depth scan should see at least as many queued runs as
    # a single processing-loop poll claims, so scale-up isn't starved.
    assert worker_pool._QUEUE_DEPTH_SCAN_LIMIT >= worker_pool._QUEUE_POLL_LIMIT

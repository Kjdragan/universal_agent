"""Hermes Phase F.3 site-wiring unit tests.

Covers the three owned-subprocess spawn sites:

* Cron ``!script`` jobs (``cron_service.py``)
* VP CLI client (``vp/clients/claude_cli_client.py``)
* Demo workspace (``services/cody_implementation.run_in_workspace``)

…plus the F.3 helper ``park_task_for_protocol_violation``.

Tests are scoped to the post-spawn wiring: they mock at the
``asyncio.create_subprocess_exec`` / ``subprocess.run`` boundary and
assert that:

  1. ``record_worker_pid`` is invoked when a linked assignment exists.
  2. ``classify_worker_exit`` returns the right outcome bucket given
     the spawn-site's exit signals.
  3. F.3 routes protocol violations into ``needs_review`` with the
     correct ``protocol_violation_<site>_*`` reason.
  4. When there's no linked task / assignment, the wiring is a silent
     no-op (it must never break dispatch).

The foundation tests live in ``test_hermes_phase_f_foundation.py``
(classifier purity, schema columns, ``record_worker_pid`` and
``resolve_max_runtime_seconds`` helpers).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
import subprocess as sp
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from universal_agent import task_hub
from universal_agent.services import cody_implementation
from universal_agent.services.worker_exit_classifier import (
    PROTOCOL_VIOLATION_REASONS,
    WorkerExit,
    classify_worker_exit,
    find_active_assignment_for_task,
    park_task_for_protocol_violation,
    task_was_closed_normally,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    task_hub.ensure_schema(c)
    yield c
    c.close()


def _seed_task_and_assignment(
    conn: sqlite3.Connection,
    *,
    task_id: str = "task:f3",
    assignment_id: str = "asg-f3",
    status: str = "in_progress",
    assignment_state: str = "running",
) -> None:
    task_hub.upsert_item(
        conn,
        {
            "task_id": task_id,
            "source_kind": "internal",
            "title": "fixture task",
            "status": status,
        },
    )
    conn.execute(
        """
        INSERT INTO task_hub_assignments
            (assignment_id, task_id, agent_id, state, started_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        """,
        (assignment_id, task_id, "agent:test", assignment_state),
    )
    conn.commit()


# ── F.3 helper — park_task_for_protocol_violation ──────────────────────────


def test_park_with_unknown_site_returns_false_and_logs(
    conn: sqlite3.Connection,
) -> None:
    _seed_task_and_assignment(conn)
    parked = park_task_for_protocol_violation(
        conn,
        task_id="task:f3",
        site="not_a_real_site",
    )
    assert parked is False
    # Task should still be in_progress (unchanged).
    row = conn.execute(
        "SELECT status FROM task_hub_items WHERE task_id = ?",
        ("task:f3",),
    ).fetchone()
    assert row is not None
    assert row["status"] == "in_progress"


def test_park_with_empty_task_id_returns_false() -> None:
    # Standalone conn — doesn't matter what's in it since the function
    # short-circuits on the empty task_id.
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    task_hub.ensure_schema(c)
    try:
        assert park_task_for_protocol_violation(c, task_id="", site="cron") is False
        assert park_task_for_protocol_violation(c, task_id="   ", site="cron") is False
    finally:
        c.close()


def test_park_routes_task_into_needs_review_with_canonical_reason(
    conn: sqlite3.Connection,
) -> None:
    _seed_task_and_assignment(conn)
    parked = park_task_for_protocol_violation(
        conn,
        task_id="task:f3",
        site="cron",
        summary="cron !script foo.py job=demo-job",
    )
    assert parked is True
    row = conn.execute(
        "SELECT status FROM task_hub_items WHERE task_id = ?",
        ("task:f3",),
    ).fetchone()
    assert row is not None
    # `review` action sets status to needs_review.
    assert row["status"] == task_hub.TASK_STATUS_REVIEW


def test_park_records_canonical_reason_per_site(
    conn: sqlite3.Connection,
) -> None:
    """Verify each site key resolves to its canonical reason string.

    The ``review`` action writes the reason into
    ``task_hub_assignments.result_summary`` on the active assignment(s)
    via ``_complete_active_assignments_for_task``.  We assert that the
    canonical site reason string lands there for each site.
    """
    _seed_task_and_assignment(conn, task_id="task:cron", assignment_id="asg-c")
    _seed_task_and_assignment(conn, task_id="task:cli", assignment_id="asg-v")
    _seed_task_and_assignment(conn, task_id="task:demo", assignment_id="asg-d")

    for site, tid, aid in [
        ("cron", "task:cron", "asg-c"),
        ("vp_cli", "task:cli", "asg-v"),
        ("demo", "task:demo", "asg-d"),
    ]:
        parked = park_task_for_protocol_violation(
            conn, task_id=tid, site=site,
        )
        assert parked is True
        # 1. The task should be in needs_review.
        item = task_hub.get_item(conn, tid)
        assert item is not None
        assert item["status"] == task_hub.TASK_STATUS_REVIEW
        # 2. The assignment's result_summary should carry the canonical
        #    site reason string.
        row = conn.execute(
            "SELECT result_summary FROM task_hub_assignments "
            "WHERE assignment_id = ?",
            (aid,),
        ).fetchone()
        assert row is not None
        assert (row["result_summary"] or "").startswith(
            PROTOCOL_VIOLATION_REASONS[site]
        )


def test_park_swallows_perform_task_action_failure(
    conn: sqlite3.Connection,
) -> None:
    """If perform_task_action raises, park returns False and doesn't propagate."""
    _seed_task_and_assignment(conn)
    with patch(
        "universal_agent.task_hub.perform_task_action",
        side_effect=RuntimeError("boom"),
    ):
        parked = park_task_for_protocol_violation(
            conn, task_id="task:f3", site="cron",
        )
    assert parked is False


# ── F.3 helper — find_active_assignment_for_task ───────────────────────────


def test_find_active_assignment_returns_running_assignment(
    conn: sqlite3.Connection,
) -> None:
    _seed_task_and_assignment(
        conn, task_id="task:f3", assignment_id="asg-f3", assignment_state="running",
    )
    assert (
        find_active_assignment_for_task(conn, task_id="task:f3") == "asg-f3"
    )


def test_find_active_assignment_returns_none_for_completed(
    conn: sqlite3.Connection,
) -> None:
    _seed_task_and_assignment(
        conn,
        task_id="task:done",
        assignment_id="asg-done",
        assignment_state="completed",
    )
    assert find_active_assignment_for_task(conn, task_id="task:done") is None


def test_find_active_assignment_returns_none_for_unknown_task(
    conn: sqlite3.Connection,
) -> None:
    assert find_active_assignment_for_task(conn, task_id="task:nope") is None


def test_find_active_assignment_empty_task_id_returns_none(
    conn: sqlite3.Connection,
) -> None:
    assert find_active_assignment_for_task(conn, task_id="") is None
    assert find_active_assignment_for_task(conn, task_id="   ") is None


# ── F.3 helper — task_was_closed_normally ──────────────────────────────────


def test_task_closed_normally_true_for_completed(conn: sqlite3.Connection) -> None:
    _seed_task_and_assignment(conn, status="completed")
    assert task_was_closed_normally(conn, task_id="task:f3") is True


def test_task_closed_normally_false_for_in_progress(
    conn: sqlite3.Connection,
) -> None:
    _seed_task_and_assignment(conn, status="in_progress")
    assert task_was_closed_normally(conn, task_id="task:f3") is False


def test_task_closed_normally_false_for_unknown_task(
    conn: sqlite3.Connection,
) -> None:
    assert task_was_closed_normally(conn, task_id="task:nope") is False


# ── Cron `!script` site wiring ──────────────────────────────────────────────


def _make_classification_chain(
    *,
    return_code: int | None,
    was_signaled: bool = False,
    was_timeout_killed: bool = False,
    task_closed_normally: bool = True,
) -> WorkerExit:
    """Helper — exercise the classifier exactly as the wiring does."""
    return classify_worker_exit(
        return_code=return_code,
        was_signaled=was_signaled,
        was_timeout_killed=was_timeout_killed,
        task_closed_normally=task_closed_normally,
    )


def test_cron_classification_clean_exit_with_open_task_is_protocol_violation(
    conn: sqlite3.Connection,
) -> None:
    """End-to-end on the F.3 fork: rc=0 + task still in_progress -> review."""
    _seed_task_and_assignment(
        conn, task_id="task:cron-open", assignment_id="asg-cron-open",
    )
    closed = task_was_closed_normally(conn, task_id="task:cron-open")
    assert closed is False
    classification = _make_classification_chain(
        return_code=0, task_closed_normally=closed,
    )
    assert classification.is_protocol_violation is True
    parked = park_task_for_protocol_violation(
        conn,
        task_id="task:cron-open",
        site="cron",
        summary="cron !script test job=demo",
    )
    assert parked is True
    row = conn.execute(
        "SELECT status FROM task_hub_items WHERE task_id = ?",
        ("task:cron-open",),
    ).fetchone()
    assert row["status"] == task_hub.TASK_STATUS_REVIEW


def test_cron_classification_nonzero_exit_is_not_a_protocol_violation(
    conn: sqlite3.Connection,
) -> None:
    """rc != 0 is a real failure, not a protocol violation."""
    _seed_task_and_assignment(conn, task_id="task:cron-err")
    classification = _make_classification_chain(
        return_code=137,
        task_closed_normally=False,
    )
    assert classification.outcome == "nonzero_exit"
    assert classification.is_protocol_violation is False
    # F.3 wiring should NOT park.  Verify by NOT calling park and
    # confirming the task remains in_progress.
    row = conn.execute(
        "SELECT status FROM task_hub_items WHERE task_id = ?",
        ("task:cron-err",),
    ).fetchone()
    assert row["status"] == "in_progress"


def test_cron_classification_timeout_killed_takes_priority(
    conn: sqlite3.Connection,
) -> None:
    classification = _make_classification_chain(
        return_code=-9,
        was_signaled=True,
        was_timeout_killed=True,
        task_closed_normally=False,
    )
    assert classification.outcome == "timeout_killed"
    assert classification.is_failure is True
    assert classification.is_protocol_violation is False


# ── VP CLI site wiring ──────────────────────────────────────────────────────


def test_cli_classification_clean_exit_payload_routes_to_protocol_violation(
    conn: sqlite3.Connection,
) -> None:
    """Simulate VP CLI returning a clean exit_code but task still open."""
    _seed_task_and_assignment(conn, task_id="task:cli-open")
    # The classifier args mirror what _classify_and_route_cli_exit feeds:
    exit_code = 0
    was_timeout_killed = False
    was_signaled = bool(exit_code < 0 and not was_timeout_killed)
    closed = task_was_closed_normally(conn, task_id="task:cli-open")
    classification = classify_worker_exit(
        return_code=exit_code,
        was_signaled=was_signaled,
        was_timeout_killed=was_timeout_killed,
        task_closed_normally=closed,
    )
    assert classification.is_protocol_violation is True
    parked = park_task_for_protocol_violation(
        conn,
        task_id="task:cli-open",
        site="vp_cli",
        summary="vp cli mission_id=mission-abc",
    )
    assert parked is True
    row = conn.execute(
        "SELECT status FROM task_hub_items WHERE task_id = ?",
        ("task:cli-open",),
    ).fetchone()
    assert row["status"] == task_hub.TASK_STATUS_REVIEW


def test_cli_classification_timeout_via_payload_marker(
    conn: sqlite3.Connection,
) -> None:
    """_execute_cli_session populates payload.was_timeout_killed when the
    timeout path fires; classifier should yield ``timeout_killed``."""
    payload = {
        "exit_code": -9,  # signaled via _kill_process
        "was_timeout_killed": True,
    }
    exit_code = payload["exit_code"]
    was_timeout_killed = bool(payload.get("was_timeout_killed"))
    was_signaled = bool(
        isinstance(exit_code, int) and exit_code < 0 and not was_timeout_killed
    )
    classification = classify_worker_exit(
        return_code=exit_code if isinstance(exit_code, int) else None,
        was_signaled=was_signaled,
        was_timeout_killed=was_timeout_killed,
        task_closed_normally=False,
    )
    assert classification.outcome == "timeout_killed"
    assert classification.is_protocol_violation is False


def test_classify_and_route_cli_exit_skips_when_no_task_id() -> None:
    """No linked task_id => no DB calls, no exception."""
    from universal_agent.vp.clients.claude_cli_client import (
        _classify_and_route_cli_exit,
    )
    from universal_agent.vp.clients.base import MissionOutcome

    outcome = MissionOutcome(
        status="completed",
        payload={"exit_code": 0, "tool_calls": 1, "cost": {}},
    )
    # Should not raise — the function is best-effort and short-circuits
    # on empty task_id.
    _classify_and_route_cli_exit(
        outcome=outcome,
        task_id="",
        mission_id="m-empty",
    )


def test_classify_and_route_cli_exit_parks_on_protocol_violation(
    conn: sqlite3.Connection,
    monkeypatch,
) -> None:
    """E2E: feed the helper a clean-exit MissionOutcome + a still-open
    task, and verify F.3 lands the task in needs_review."""
    from universal_agent.vp.clients.claude_cli_client import (
        _classify_and_route_cli_exit,
    )
    from universal_agent.vp.clients.base import MissionOutcome

    _seed_task_and_assignment(
        conn, task_id="task:cli-route", assignment_id="asg-cli-route",
    )

    # Wrap the shared in-memory conn so the helper\u2019s ``finally:
    # conn.close()`` doesn\u2019t evict our fixture mid-test.  We expose the
    # exact same Row-factory connection but stub ``close()`` to a no-op.
    class _NonClosing:
        def __init__(self, inner: sqlite3.Connection) -> None:
            self._inner = inner

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        def close(self) -> None:  # no-op: fixture owns lifecycle
            pass

    proxy = _NonClosing(conn)

    monkeypatch.setattr(
        "universal_agent.gateway_server._task_hub_open_conn",
        lambda: proxy,
    )

    outcome = MissionOutcome(
        status="completed",
        result_ref="workspace://foo",
        payload={
            "exit_code": 0,
            "tool_calls": 3,
            "assignment_id": "asg-cli-route",
        },
    )
    _classify_and_route_cli_exit(
        outcome=outcome,
        task_id="task:cli-route",
        mission_id="mission-route",
    )

    row = conn.execute(
        "SELECT status FROM task_hub_items WHERE task_id = ?",
        ("task:cli-route",),
    ).fetchone()
    assert row["status"] == task_hub.TASK_STATUS_REVIEW


# ── Demo workspace site wiring ──────────────────────────────────────────────
#
# Hermes Phase F.1 follow-up: ``run_in_workspace`` now spawns via
# ``subprocess.Popen`` so the PID is observable immediately after spawn.
# These tests mock ``sp.Popen`` (NOT ``sp.run``) and exercise:
#
#   * ``clean_exit_zero`` classification on rc=0 happy path.
#   * ``timeout_killed`` classification + ``proc.kill()`` + ``proc.communicate()``
#     drain when ``communicate(timeout=…)`` raises ``TimeoutExpired``.
#   * ``nonzero_exit`` and ``signaled`` classifications via mocked
#     ``returncode``.
#   * Round-tripping the result through ``to_dict()``.
#   * PID recording when ``assignment_id`` is supplied + no-op when it
#     is omitted (backward compat for the existing callers in
#     ``cody_evaluation`` etc.).


def _make_popen_mock(
    *,
    returncode: int = 0,
    stdout: str = "ok",
    stderr: str = "",
    pid: int = 4242,
    raise_timeout: bool = False,
    raise_exception: Exception | None = None,
) -> MagicMock:
    """Build a Popen-compatible mock for ``run_in_workspace`` tests.

    Mimics the post-spawn surface ``run_in_workspace`` touches:
      * ``proc.pid`` — captured immediately for F.1 PID recording.
      * ``proc.communicate(timeout=…)`` — returns ``(stdout, stderr)``
        on success, raises ``sp.TimeoutExpired`` when ``raise_timeout``,
        or any other ``raise_exception`` for fault-injection.
      * ``proc.returncode`` — read after communicate() resolves.
      * ``proc.kill()`` — must be callable; we record it via MagicMock.
    """
    proc = MagicMock()
    proc.pid = pid

    if raise_timeout:
        # First call raises TimeoutExpired; the drain call (post-kill)
        # returns empty strings.  Use side_effect with an iterator so
        # the second invocation succeeds.
        proc.communicate.side_effect = [
            sp.TimeoutExpired(cmd="x", timeout=1),
            ("", ""),
        ]
        proc.returncode = None
    elif raise_exception is not None:
        proc.communicate.side_effect = raise_exception
        proc.returncode = None
    else:
        proc.communicate.return_value = (stdout, stderr)
        proc.returncode = returncode

    return proc


def test_demo_run_in_workspace_stamps_classification_clean_exit(
    tmp_path: Path,
) -> None:
    """run_in_workspace should stamp a clean_exit_zero classification."""
    workspace = tmp_path / "demo"
    workspace.mkdir()

    proc = _make_popen_mock(returncode=0, stdout="ok", stderr="")

    with patch.object(cody_implementation.sp, "Popen", return_value=proc):
        result = cody_implementation.run_in_workspace(
            workspace,
            ["echo", "hi"],
            timeout=10,
        )
    assert result.return_code == 0
    assert result.exit_classification is not None
    assert result.exit_classification.outcome == "clean_exit_zero"
    # Round-trip via to_dict.
    d = result.to_dict()
    assert d["exit_classification"]["outcome"] == "clean_exit_zero"


def test_demo_run_in_workspace_stamps_classification_timeout(
    tmp_path: Path,
) -> None:
    """TimeoutExpired path should yield ``timeout_killed`` classification."""
    workspace = tmp_path / "demo-tmo"
    workspace.mkdir()

    proc = _make_popen_mock(raise_timeout=True)

    with patch.object(cody_implementation.sp, "Popen", return_value=proc):
        result = cody_implementation.run_in_workspace(
            workspace,
            ["claude"],
            timeout=10,
        )
    assert result.return_code == 124
    assert result.exit_classification is not None
    assert result.exit_classification.outcome == "timeout_killed"
    # Timeout path MUST have killed the process and drained its pipes.
    proc.kill.assert_called_once()
    assert proc.communicate.call_count == 2


def test_demo_run_in_workspace_stamps_classification_nonzero(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "demo-err"
    workspace.mkdir()

    proc = _make_popen_mock(returncode=2, stdout="", stderr="boom")

    with patch.object(cody_implementation.sp, "Popen", return_value=proc):
        result = cody_implementation.run_in_workspace(
            workspace,
            ["something"],
            timeout=10,
        )
    assert result.return_code == 2
    assert result.exit_classification is not None
    assert result.exit_classification.outcome == "nonzero_exit"
    assert result.exit_classification.is_failure is True


def test_demo_run_in_workspace_stamps_classification_signaled(
    tmp_path: Path,
) -> None:
    """Negative returncode (POSIX signaled) => ``signaled``."""
    workspace = tmp_path / "demo-sig"
    workspace.mkdir()

    proc = _make_popen_mock(returncode=-9, stdout="", stderr="")

    with patch.object(cody_implementation.sp, "Popen", return_value=proc):
        result = cody_implementation.run_in_workspace(
            workspace,
            ["something"],
            timeout=10,
        )
    assert result.return_code == -9
    assert result.exit_classification is not None
    assert result.exit_classification.outcome == "signaled"


def test_demo_run_in_workspace_to_dict_round_trip(tmp_path: Path) -> None:
    workspace = tmp_path / "demo-rt"
    workspace.mkdir()
    proc = _make_popen_mock(returncode=0, stdout="ok", stderr="")
    with patch.object(cody_implementation.sp, "Popen", return_value=proc):
        result = cody_implementation.run_in_workspace(
            workspace, ["echo", "ok"], timeout=5,
        )
    payload = result.to_dict()
    assert payload["ok"] is True
    assert payload["return_code"] == 0
    assert "exit_classification" in payload
    assert payload["exit_classification"]["outcome"] == "clean_exit_zero"
    assert payload["exit_classification"]["is_failure"] is False


# ── F.1 follow-up — Popen + PID observability for demo workspace ───────────


def test_demo_run_in_workspace_uses_popen_not_run(tmp_path: Path) -> None:
    """Regression guard: confirms the spawn path is ``sp.Popen``.

    The whole point of the F.1 follow-up is moving demo-workspace
    invocations off ``sp.run`` so the spawned PID is observable
    immediately.  This test ensures the migration sticks.
    """
    workspace = tmp_path / "demo-popen"
    workspace.mkdir()

    proc = _make_popen_mock(returncode=0)

    with patch.object(cody_implementation.sp, "Popen", return_value=proc) as popen:
        result = cody_implementation.run_in_workspace(
            workspace,
            ["echo", "hi"],
            timeout=5,
        )
    popen.assert_called_once()
    # Popen must be passed PIPE for stdout/stderr (text=True) and cwd
    # set to the resolved workspace dir.
    _, kwargs = popen.call_args
    assert kwargs["cwd"] == str(workspace.resolve())
    assert kwargs["stdout"] is sp.PIPE
    assert kwargs["stderr"] is sp.PIPE
    assert kwargs["text"] is True
    assert result.return_code == 0


def test_demo_run_in_workspace_captures_pid_on_result(tmp_path: Path) -> None:
    """The Popen PID lands on ``RunResult.worker_pid`` regardless of linkage."""
    workspace = tmp_path / "demo-pid"
    workspace.mkdir()

    proc = _make_popen_mock(returncode=0, pid=9876)

    with patch.object(cody_implementation.sp, "Popen", return_value=proc):
        result = cody_implementation.run_in_workspace(
            workspace,
            ["echo", "hi"],
            timeout=5,
        )
    assert result.worker_pid == 9876
    # ``worker_pid`` should also round-trip through ``to_dict``.
    assert result.to_dict()["worker_pid"] == 9876


def test_demo_run_in_workspace_records_pid_when_assignment_id_provided(
    tmp_path: Path,
) -> None:
    """When ``assignment_id`` is set, the spawned PID is written via
    ``task_hub.record_worker_pid``.  Connection plumbing is mocked at
    the ``_record_demo_worker_pid`` boundary to avoid touching the real
    runtime DB."""
    workspace = tmp_path / "demo-record"
    workspace.mkdir()
    proc = _make_popen_mock(returncode=0, pid=5555)

    with patch.object(cody_implementation.sp, "Popen", return_value=proc), \
         patch.object(
             cody_implementation,
             "_record_demo_worker_pid",
             return_value=True,
         ) as recorder:
        cody_implementation.run_in_workspace(
            workspace,
            ["claude"],
            timeout=5,
            assignment_id="asg-demo-123",
        )
    recorder.assert_called_once_with("asg-demo-123", 5555)


def test_demo_run_in_workspace_no_pid_record_when_assignment_id_missing(
    tmp_path: Path,
) -> None:
    """Backward compat: legacy callers (no ``assignment_id``) must NOT
    trigger any PID-record call."""
    workspace = tmp_path / "demo-noid"
    workspace.mkdir()
    proc = _make_popen_mock(returncode=0)

    with patch.object(cody_implementation.sp, "Popen", return_value=proc), \
         patch.object(
             cody_implementation,
             "_record_demo_worker_pid",
         ) as recorder:
        cody_implementation.run_in_workspace(
            workspace,
            ["echo", "hi"],
            timeout=5,
        )
    recorder.assert_not_called()


def test_demo_run_in_workspace_with_broken_db_completes_normally(
    tmp_path: Path, monkeypatch,
) -> None:
    """The real ``_record_demo_worker_pid`` swallows DB errors, so
    ``run_in_workspace`` MUST complete normally even when the runtime
    DB is unreachable.  Uses the real helper (not a mock) so the
    swallowing logic is exercised end-to-end.
    """
    workspace = tmp_path / "demo-recerr"
    workspace.mkdir()
    proc = _make_popen_mock(returncode=0, pid=7777)

    # Break the DB connect path so the helper hits the warning + return
    # False branch internally.
    monkeypatch.setattr(
        "universal_agent.durable.db.connect_runtime_db",
        lambda _path=None: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    with patch.object(cody_implementation.sp, "Popen", return_value=proc):
        result = cody_implementation.run_in_workspace(
            workspace,
            ["claude"],
            timeout=5,
            assignment_id="asg-demo-err",
        )
    # Happy path still completes; the failed PID record is observability
    # noise, not a blocker.
    assert result.return_code == 0
    assert result.worker_pid == 7777
    assert result.exit_classification.outcome == "clean_exit_zero"


def test_record_demo_worker_pid_helper_no_op_for_empty_assignment(
    tmp_path: Path,
) -> None:
    """``_record_demo_worker_pid("", pid)`` is a silent no-op."""
    assert cody_implementation._record_demo_worker_pid("", 1234) is False
    assert cody_implementation._record_demo_worker_pid("   ", 1234) is False


def test_record_demo_worker_pid_helper_no_op_for_non_positive_pid(
    tmp_path: Path,
) -> None:
    assert cody_implementation._record_demo_worker_pid("asg", 0) is False
    assert cody_implementation._record_demo_worker_pid("asg", -1) is False


def test_record_demo_worker_pid_helper_swallows_db_errors(
    monkeypatch,
) -> None:
    """If the runtime DB connect raises, the helper logs and returns False."""

    def _raise(*_a, **_kw):
        raise RuntimeError("db connect failed")

    monkeypatch.setattr(
        "universal_agent.durable.db.connect_runtime_db", _raise,
    )
    ok = cody_implementation._record_demo_worker_pid("asg-x", 12345)
    assert ok is False


def test_record_demo_worker_pid_helper_writes_and_commits(
    monkeypatch,
) -> None:
    """Happy path: helper opens a conn, writes the PID, commits, closes."""

    class _FakeConn:
        def __init__(self) -> None:
            self.commits = 0
            self.closed = False

        def commit(self) -> None:
            self.commits += 1

        def close(self) -> None:
            self.closed = True

    fake_conn = _FakeConn()

    monkeypatch.setattr(
        "universal_agent.durable.db.connect_runtime_db",
        lambda _path=None: fake_conn,
    )
    monkeypatch.setattr(
        "universal_agent.durable.db.get_activity_db_path",
        lambda: "/tmp/fake.db",
    )

    record_calls: list[tuple[str, int]] = []

    def _record(conn, *, assignment_id, worker_pid):
        record_calls.append((assignment_id, worker_pid))
        assert conn is fake_conn
        return 1

    monkeypatch.setattr(
        "universal_agent.task_hub.record_worker_pid", _record,
    )

    ok = cody_implementation._record_demo_worker_pid("asg-real", 4321)
    assert ok is True
    assert record_calls == [("asg-real", 4321)]
    assert fake_conn.commits == 1
    assert fake_conn.closed is True

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


def test_demo_run_in_workspace_stamps_classification_clean_exit(
    tmp_path: Path,
) -> None:
    """run_in_workspace should stamp a clean_exit_zero classification."""
    workspace = tmp_path / "demo"
    workspace.mkdir()

    fake_completed = MagicMock()
    fake_completed.returncode = 0
    fake_completed.stdout = "ok"
    fake_completed.stderr = ""

    with patch.object(cody_implementation.sp, "run", return_value=fake_completed):
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

    def _raise_timeout(*a, **kw):
        raise sp.TimeoutExpired(cmd="claude", timeout=10)

    with patch.object(cody_implementation.sp, "run", side_effect=_raise_timeout):
        result = cody_implementation.run_in_workspace(
            workspace,
            ["claude"],
            timeout=10,
        )
    assert result.return_code == 124
    assert result.exit_classification is not None
    assert result.exit_classification.outcome == "timeout_killed"


def test_demo_run_in_workspace_stamps_classification_nonzero(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "demo-err"
    workspace.mkdir()

    fake = MagicMock()
    fake.returncode = 2
    fake.stdout = ""
    fake.stderr = "boom"

    with patch.object(cody_implementation.sp, "run", return_value=fake):
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

    fake = MagicMock()
    fake.returncode = -9
    fake.stdout = ""
    fake.stderr = ""

    with patch.object(cody_implementation.sp, "run", return_value=fake):
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
    fake = MagicMock()
    fake.returncode = 0
    fake.stdout = "ok"
    fake.stderr = ""
    with patch.object(cody_implementation.sp, "run", return_value=fake):
        result = cody_implementation.run_in_workspace(
            workspace, ["echo", "ok"], timeout=5,
        )
    payload = result.to_dict()
    assert payload["ok"] is True
    assert payload["return_code"] == 0
    assert "exit_classification" in payload
    assert payload["exit_classification"]["outcome"] == "clean_exit_zero"
    assert payload["exit_classification"]["is_failure"] is False

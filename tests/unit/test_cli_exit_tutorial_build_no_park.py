"""Regression test for the tutorial_build premature protocol-violation park race.

`vp/clients/claude_cli_client.py::_classify_and_route_cli_exit` runs inside
``run_mission`` right after the CLI session returns. At that point a
``tutorial_build`` (or ``cody_demo_task``) source task is still ``in_progress``
because ``vp/worker_loop.py``'s deterministic finalize
(``finalize_tutorial_build_demo`` / cody_demo_task terminal routing) only runs
AFTER ``run_mission`` returns. ``classify_worker_exit`` therefore flags a
``clean_exit_zero_no_disposition`` protocol violation for the still-open task —
but that is NOT genuine for these worker-loop-finalized kinds; parking it here
double-counts the completed mission as both a protocol-violation review AND a
completion (and writes spurious ``proactive_outcomes`` ACTION_REVIEW rows).

The fix suppresses the PARK for those source kinds while STILL writing the
``_close_run`` classification for observability. Genuine violations at
cron/demo-unaware source kinds keep the park intact.
"""

from __future__ import annotations

from universal_agent import task_hub
from universal_agent.gateway_server import _task_hub_open_conn  # noqa: F401
from universal_agent.services import worker_exit_classifier
from universal_agent.vp.clients import claude_cli_client as cli
from universal_agent.vp.clients.base import MissionOutcome


class _FakeConn:
    def commit(self):  # pragma: no cover - trivial
        pass

    def close(self):  # pragma: no cover - trivial
        pass


def _wire(monkeypatch, *, source_kind: str, park_calls: list, close_calls: list):
    """Patch the dynamic-import targets _classify_and_route_cli_exit resolves."""

    # A still-open source task is never "closed normally" -> rc=0 classifies
    # as the protocol violation. (Mirrors the real classifier output.)
    monkeypatch.setattr(
        worker_exit_classifier, "task_was_closed_normally",
        lambda conn, *, task_id: False,
    )
    monkeypatch.setattr(
        worker_exit_classifier, "classify_worker_exit",
        lambda **kw: worker_exit_classifier.WorkerExit(
            outcome="clean_exit_zero_no_disposition",
            is_protocol_violation=True,
            is_failure=False,
        ),
    )
    monkeypatch.setattr(
        worker_exit_classifier, "park_task_for_protocol_violation",
        lambda conn, **kw: park_calls.append(kw),
    )

    monkeypatch.setattr(
        "universal_agent.gateway_server._task_hub_open_conn",
        lambda: _FakeConn(),
    )
    monkeypatch.setattr(
        task_hub, "get_item",
        lambda conn, task_id: {"task_id": task_id, "source_kind": source_kind},
    )
    monkeypatch.setattr(
        task_hub, "_close_run",
        lambda conn, **kw: close_calls.append(kw),
    )


def _outcome() -> MissionOutcome:
    # rc=0 clean exit, carries an assignment so _close_run fires.
    return MissionOutcome(
        status="completed",
        message="done",
        payload={"exit_code": 0, "assignment_id": "asg-1"},
    )


def test_tutorial_build_clean_exit_does_not_park(monkeypatch):
    park_calls: list = []
    close_calls: list = []
    _wire(monkeypatch, source_kind="tutorial_build",
          park_calls=park_calls, close_calls=close_calls)

    cli._classify_and_route_cli_exit(
        outcome=_outcome(), task_id="task-tutorial", mission_id="m-1",
    )

    # PARK suppressed for the worker-loop-finalized kind ...
    assert park_calls == []
    # ... but the _close_run classification IS still written for observability.
    assert len(close_calls) == 1


def test_cody_demo_task_clean_exit_does_not_park(monkeypatch):
    park_calls: list = []
    close_calls: list = []
    _wire(monkeypatch, source_kind="cody_demo_task",
          park_calls=park_calls, close_calls=close_calls)

    cli._classify_and_route_cli_exit(
        outcome=_outcome(), task_id="task-demo", mission_id="m-2",
    )

    assert park_calls == []
    assert len(close_calls) == 1


def test_unknown_source_kind_still_parks(monkeypatch):
    """A genuine cron/demo-unaware violation must still be parked."""
    park_calls: list = []
    close_calls: list = []
    _wire(monkeypatch, source_kind="csi_cron",
          park_calls=park_calls, close_calls=close_calls)

    cli._classify_and_route_cli_exit(
        outcome=_outcome(), task_id="task-cron", mission_id="m-3",
    )

    assert len(park_calls) == 1
    assert park_calls[0]["task_id"] == "task-cron"
    assert len(close_calls) == 1

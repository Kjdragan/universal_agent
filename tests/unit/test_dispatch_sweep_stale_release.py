"""Phase A.2 — stale-assignment release wired into ``dispatch_sweep``.

Verifies the wiring added in
``docs/reports/hermes-adaptation-phased-plan-2026-05-10.md`` Phase A.2:

* ``release_stale_assignments`` gains an ``exclude_session_ids`` parameter.
* ``dispatch_sweep`` calls it before each claim with the caller's
  ``provider_session_id`` (plus any ``additional_running_sessions``) as the
  exclude set, gated by ``UA_DISPATCH_STALE_SWEEP_ENABLED`` (default on) and
  ``UA_DISPATCH_STALE_AFTER_SECONDS`` (default 1800, 60s floor).

Mirrors Hermes' ``release_stale_claims`` call at the top of each
``dispatch_once`` tick (``kanban_db.py:3658``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from universal_agent import task_hub
from universal_agent.services import dispatch_service


@pytest.fixture(autouse=True)
def _hermetic_loop_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize ambient loop-gate env so this file asserts PROD-default
    semantics regardless of the shell it runs in.

    A dev shell exports ``UA_RUNTIME_STAGE=development``, which flips
    ``loop_control.should_run_loop`` into its dev branch — that branch defaults
    loops OFF and ignores ``UA_<NAME>_ENABLED`` truthy values, defeating the
    ``prod_default=True`` behavior every test here relies on (the stale sweep is
    gated by ``dispatch_service._stale_sweep_enabled`` → ``should_run_loop``).
    Tests that need a specific value still ``monkeypatch.setenv`` it after this
    autouse fixture runs.
    """
    monkeypatch.delenv("UA_RUNTIME_STAGE", raising=False)
    monkeypatch.delenv("UA_DISPATCH_STALE_SWEEP_ENABLED", raising=False)
    monkeypatch.delenv("UA_DISPATCH_STALE_AFTER_SECONDS", raising=False)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    task_hub.ensure_schema(conn)
    return conn


def _insert_stale_assignment(
    conn: sqlite3.Connection,
    *,
    assignment_id: str,
    task_id: str,
    agent_id: str,
    provider_session_id: str = "",
    age_seconds: int = 7200,
) -> None:
    """Helper: insert an assignment whose ``started_at`` is ``age_seconds`` in the past."""
    started_at = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    conn.execute(
        """
        INSERT INTO task_hub_assignments (
            assignment_id, task_id, agent_id, provider_session_id, state, started_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (assignment_id, task_id, agent_id, provider_session_id, "seized", started_at),
    )
    # Also flip the task to in_progress so finalize_assignments has something to reopen.
    conn.execute(
        "UPDATE task_hub_items SET status=?, seizure_state=? WHERE task_id=?",
        (task_hub.TASK_STATUS_IN_PROGRESS, "seized", task_id),
    )
    conn.commit()


# ── release_stale_assignments exclude_session_ids param ─────────────────────


def test_exclude_session_ids_skips_matching_assignment() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:live-session",
                "source_kind": "internal",
                "title": "Live session, age-eligible assignment",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
            },
        )
        _insert_stale_assignment(
            conn,
            assignment_id="asg-live",
            task_id="task:live-session",
            agent_id="heartbeat:session-live",
            provider_session_id="session-live",
            age_seconds=3600,
        )

        result = task_hub.release_stale_assignments(
            conn,
            agent_id_prefix="heartbeat:",
            stale_after_seconds=1800,
            exclude_session_ids={"session-live"},
        )
        assert result["stale_detected"] == 0
        assert result["finalized"] == 0
        assert result["skipped_live"] == 1
        # Assignment must remain seized — not released.
        row = conn.execute(
            "SELECT state FROM task_hub_assignments WHERE assignment_id = ?",
            ("asg-live",),
        ).fetchone()
        assert row is not None
        assert row["state"] == "seized"
    finally:
        conn.close()


def test_exclude_session_ids_none_preserves_default_behavior() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:no-exclude",
                "source_kind": "internal",
                "title": "Stale assignment, no exclude",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
            },
        )
        _insert_stale_assignment(
            conn,
            assignment_id="asg-no-exclude",
            task_id="task:no-exclude",
            agent_id="heartbeat:session-x",
            provider_session_id="session-x",
            age_seconds=3600,
        )

        result = task_hub.release_stale_assignments(
            conn,
            agent_id_prefix="heartbeat:",
            stale_after_seconds=1800,
        )
        assert result["stale_detected"] == 1
        assert result["finalized"] == 1
        assert result["skipped_live"] == 0
    finally:
        conn.close()


def test_exclude_session_ids_does_not_protect_unmatched_sessions() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:other-session",
                "source_kind": "internal",
                "title": "Stale assignment with a non-matching session",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
            },
        )
        _insert_stale_assignment(
            conn,
            assignment_id="asg-other",
            task_id="task:other-session",
            agent_id="heartbeat:session-other",
            provider_session_id="session-other",
            age_seconds=3600,
        )

        result = task_hub.release_stale_assignments(
            conn,
            agent_id_prefix="heartbeat:",
            stale_after_seconds=1800,
            exclude_session_ids={"session-different"},  # doesn't match
        )
        assert result["stale_detected"] == 1
        assert result["finalized"] == 1
        assert result["skipped_live"] == 0
    finally:
        conn.close()


def test_exclude_session_ids_accepts_list_and_tuple() -> None:
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:list-input",
                "source_kind": "internal",
                "title": "Stale assignment",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
            },
        )
        _insert_stale_assignment(
            conn,
            assignment_id="asg-list",
            task_id="task:list-input",
            agent_id="heartbeat:session-list",
            provider_session_id="session-list",
            age_seconds=3600,
        )

        # Tuple input
        result = task_hub.release_stale_assignments(
            conn,
            agent_id_prefix="heartbeat:",
            stale_after_seconds=1800,
            exclude_session_ids=("session-list",),
        )
        assert result["skipped_live"] == 1
        assert result["stale_detected"] == 0
    finally:
        conn.close()


# ── dispatch_sweep wiring ───────────────────────────────────────────────────


def test_dispatch_sweep_releases_stale_assignment_with_default_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UA_DISPATCH_STALE_SWEEP_ENABLED", raising=False)
    monkeypatch.delenv("UA_DISPATCH_STALE_AFTER_SECONDS", raising=False)
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:dispatch-sweep-stale",
                "source_kind": "internal",
                "title": "Stale before sweep",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
                "labels": ["agent-ready"],
            },
        )
        _insert_stale_assignment(
            conn,
            assignment_id="asg-stale-sweep",
            task_id="task:dispatch-sweep-stale",
            agent_id="heartbeat:dead-session",
            provider_session_id="dead-session",
            age_seconds=3600,  # > 1800 default
        )

        # Caller's session is fresh and different from the dead one.
        claimed = dispatch_service.dispatch_sweep(
            conn,
            agent_id="heartbeat:fresh-session",
            limit=1,
            provider_session_id="fresh-session",
        )

        # The stale assignment should have been finalized BEFORE the claim ran,
        # which reopens the task to OPEN. The sweep then re-claims it under
        # the fresh session.
        assert len(claimed) == 1
        assert str(claimed[0].get("task_id")) == "task:dispatch-sweep-stale"

        # Original stale assignment row should now be in terminal state.
        row = conn.execute(
            "SELECT state FROM task_hub_assignments WHERE assignment_id = ?",
            ("asg-stale-sweep",),
        ).fetchone()
        assert row is not None
        assert row["state"] != "seized"
        assert row["state"] != "running"
    finally:
        conn.close()


def test_dispatch_sweep_excludes_caller_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """A heartbeat tick that's been busy longer than the stale-cutoff must NOT
    release its own currently-active assignment when calling dispatch_sweep."""
    monkeypatch.delenv("UA_DISPATCH_STALE_SWEEP_ENABLED", raising=False)
    monkeypatch.delenv("UA_DISPATCH_STALE_AFTER_SECONDS", raising=False)
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:long-running-self",
                "source_kind": "internal",
                "title": "Caller's own busy task",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
            },
        )
        _insert_stale_assignment(
            conn,
            assignment_id="asg-self-busy",
            task_id="task:long-running-self",
            agent_id="heartbeat:self-busy-session",
            provider_session_id="self-busy-session",
            age_seconds=3600,
        )

        # Dispatch under THE SAME session_id — must not release self.
        # We don't care about the claim result here (the task is in_progress).
        dispatch_service.dispatch_sweep(
            conn,
            agent_id="heartbeat:self-busy-session",
            limit=1,
            provider_session_id="self-busy-session",
        )

        # The assignment must still be seized — self-exclusion worked.
        row = conn.execute(
            "SELECT state FROM task_hub_assignments WHERE assignment_id = ?",
            ("asg-self-busy",),
        ).fetchone()
        assert row is not None
        assert row["state"] == "seized"
    finally:
        conn.close()


def test_dispatch_sweep_disabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """When UA_DISPATCH_STALE_SWEEP_ENABLED=0, no stale-release happens."""
    monkeypatch.setenv("UA_DISPATCH_STALE_SWEEP_ENABLED", "0")
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:disabled-env",
                "source_kind": "internal",
                "title": "Stale but sweep disabled",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
            },
        )
        _insert_stale_assignment(
            conn,
            assignment_id="asg-disabled-env",
            task_id="task:disabled-env",
            agent_id="heartbeat:somebody-else",
            provider_session_id="somebody-else",
            age_seconds=3600,
        )

        dispatch_service.dispatch_sweep(
            conn,
            agent_id="heartbeat:current",
            limit=1,
            provider_session_id="current-session",
        )

        # Stale assignment should NOT have been released.
        row = conn.execute(
            "SELECT state FROM task_hub_assignments WHERE assignment_id = ?",
            ("asg-disabled-env",),
        ).fetchone()
        assert row is not None
        assert row["state"] == "seized"
    finally:
        conn.close()


def test_dispatch_sweep_additional_running_sessions_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller-supplied additional_running_sessions are protected on top of
    the auto-excluded current session."""
    monkeypatch.delenv("UA_DISPATCH_STALE_SWEEP_ENABLED", raising=False)
    monkeypatch.delenv("UA_DISPATCH_STALE_AFTER_SECONDS", raising=False)
    conn = _conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": "task:peer-busy",
                "source_kind": "internal",
                "title": "Peer session busy",
                "status": task_hub.TASK_STATUS_OPEN,
                "agent_ready": True,
            },
        )
        _insert_stale_assignment(
            conn,
            assignment_id="asg-peer",
            task_id="task:peer-busy",
            agent_id="heartbeat:peer-session",
            provider_session_id="peer-session",
            age_seconds=3600,
        )

        dispatch_service.dispatch_sweep(
            conn,
            agent_id="heartbeat:current",
            limit=1,
            provider_session_id="current-session",
            additional_running_sessions={"peer-session"},
        )

        row = conn.execute(
            "SELECT state FROM task_hub_assignments WHERE assignment_id = ?",
            ("asg-peer",),
        ).fetchone()
        assert row is not None
        assert row["state"] == "seized"
    finally:
        conn.close()


# ── env-var parsing ─────────────────────────────────────────────────────────


def test_stale_after_seconds_env_var_respects_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 60s floor in dispatch_service prevents the operator from
    configuring a dangerously tight stale-cutoff."""
    from universal_agent.services.dispatch_service import _stale_after_seconds

    monkeypatch.setenv("UA_DISPATCH_STALE_AFTER_SECONDS", "5")
    assert _stale_after_seconds() == 60  # clamped up to floor

    monkeypatch.setenv("UA_DISPATCH_STALE_AFTER_SECONDS", "7200")
    assert _stale_after_seconds() == 7200

    monkeypatch.setenv("UA_DISPATCH_STALE_AFTER_SECONDS", "garbage")
    assert _stale_after_seconds() == 1800  # fall back to default

    monkeypatch.delenv("UA_DISPATCH_STALE_AFTER_SECONDS", raising=False)
    assert _stale_after_seconds() == 1800


def test_stale_sweep_enabled_env_var_parses_truthy_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from universal_agent.services.dispatch_service import _stale_sweep_enabled

    monkeypatch.delenv("UA_DISPATCH_STALE_SWEEP_ENABLED", raising=False)
    assert _stale_sweep_enabled() is True  # default on

    for value, expected in (
        ("1", True),
        ("true", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("FALSE", False),  # case-insensitive
    ):
        monkeypatch.setenv("UA_DISPATCH_STALE_SWEEP_ENABLED", value)
        assert _stale_sweep_enabled() is expected, f"value={value!r}"

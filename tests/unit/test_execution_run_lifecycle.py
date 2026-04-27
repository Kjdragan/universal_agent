"""Tests for execution run lifecycle: allocate → finalize round-trip.

Validates that ``finalize_execution_run`` correctly closes the durable
run and attempt records created by ``allocate_execution_run``, preventing
the stuck-run reaper from falsely marking completed todo runs as timed-out.
"""

import os
from pathlib import Path
import sqlite3
import tempfile
from unittest import mock

import pytest

# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolated_runtime_db(tmp_path: Path, monkeypatch):
    """Provide an isolated runtime DB for each test."""
    db_path = str(tmp_path / "runtime_state.db")
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", db_path)
    # Also point the project root to tmp so workspace dirs resolve
    monkeypatch.setenv("UA_REPO_ROOT", str(tmp_path))

    # Bootstrap schema
    from universal_agent.durable.db import connect_runtime_db
    from universal_agent.durable.migrations import ensure_schema

    conn = connect_runtime_db(db_path)
    ensure_schema(conn)
    conn.close()
    yield db_path


# ── Tests ───────────────────────────────────────────────────────────────────

class TestFinalizeExecutionRun:
    """finalize_execution_run must update both runs and run_attempts tables."""

    def test_finalize_marks_run_completed(self, _isolated_runtime_db):
        from universal_agent.durable.db import connect_runtime_db
        from universal_agent.durable.state import get_run, get_run_attempt
        from universal_agent.services.execution_run_service import (
            allocate_execution_run,
            finalize_execution_run,
        )

        ctx = allocate_execution_run(
            task_id="test:task1",
            origin="test",
            run_kind="todo_execution",
            trigger_source="todo_dispatch",
        )

        # Before finalize: run should be "running"
        conn = connect_runtime_db(_isolated_runtime_db)
        run = get_run(conn, ctx.run_id)
        assert run is not None
        assert run["status"] == "running"

        # Finalize as completed
        finalize_execution_run(
            run_id=ctx.run_id,
            attempt_id=ctx.attempt_id,
            status="completed",
            terminal_reason="completed",
            tool_call_count=5,
            duration_seconds=12.3,
        )

        # After finalize: run should be "completed"
        conn2 = connect_runtime_db(_isolated_runtime_db)
        run_after = get_run(conn2, ctx.run_id)
        assert run_after["status"] == "completed"

        # Attempt should also be updated
        attempt = get_run_attempt(conn2, ctx.attempt_id)
        assert attempt is not None
        assert attempt["status"] == "completed"
        assert attempt["ended_at"] is not None
        conn.close()
        conn2.close()

    def test_finalize_marks_run_failed(self, _isolated_runtime_db):
        from universal_agent.durable.db import connect_runtime_db
        from universal_agent.durable.state import get_run
        from universal_agent.services.execution_run_service import (
            allocate_execution_run,
            finalize_execution_run,
        )

        ctx = allocate_execution_run(
            task_id="test:task2",
            origin="test",
            run_kind="todo_execution",
        )

        finalize_execution_run(
            run_id=ctx.run_id,
            attempt_id=ctx.attempt_id,
            status="failed",
            terminal_reason="goal_unsatisfied",
        )

        conn = connect_runtime_db(_isolated_runtime_db)
        run = get_run(conn, ctx.run_id)
        assert run["status"] == "failed"
        conn.close()

    def test_finalize_marks_run_cancelled(self, _isolated_runtime_db):
        from universal_agent.durable.db import connect_runtime_db
        from universal_agent.durable.state import get_run
        from universal_agent.services.execution_run_service import (
            allocate_execution_run,
            finalize_execution_run,
        )

        ctx = allocate_execution_run(
            task_id="test:task3",
            origin="test",
            run_kind="todo_execution",
        )

        finalize_execution_run(
            run_id=ctx.run_id,
            attempt_id=ctx.attempt_id,
            status="cancelled",
            terminal_reason="cancelled",
        )

        conn = connect_runtime_db(_isolated_runtime_db)
        run = get_run(conn, ctx.run_id)
        assert run["status"] == "cancelled"
        conn.close()

    def test_finalize_is_idempotent(self, _isolated_runtime_db):
        """Calling finalize twice does not raise or corrupt data."""
        from universal_agent.durable.db import connect_runtime_db
        from universal_agent.durable.state import get_run
        from universal_agent.services.execution_run_service import (
            allocate_execution_run,
            finalize_execution_run,
        )

        ctx = allocate_execution_run(
            task_id="test:task4",
            origin="test",
            run_kind="todo_execution",
        )

        finalize_execution_run(
            run_id=ctx.run_id,
            attempt_id=ctx.attempt_id,
            status="completed",
            terminal_reason="completed",
            tool_call_count=3,
        )

        # Call again — should not raise
        finalize_execution_run(
            run_id=ctx.run_id,
            attempt_id=ctx.attempt_id,
            status="completed",
            terminal_reason="completed",
            tool_call_count=3,
        )

        conn = connect_runtime_db(_isolated_runtime_db)
        run = get_run(conn, ctx.run_id)
        assert run["status"] == "completed"
        conn.close()

    def test_finalize_with_missing_attempt_still_updates_run(self, _isolated_runtime_db):
        """If the attempt row is missing, the run status is still updated."""
        from universal_agent.durable.db import connect_runtime_db
        from universal_agent.durable.state import get_run
        from universal_agent.services.execution_run_service import (
            allocate_execution_run,
            finalize_execution_run,
        )

        ctx = allocate_execution_run(
            task_id="test:task5",
            origin="test",
            run_kind="todo_execution",
        )

        # Delete the attempt row to simulate partial allocation
        conn = connect_runtime_db(_isolated_runtime_db)
        conn.execute("DELETE FROM run_attempts WHERE attempt_id = ?", (ctx.attempt_id,))
        conn.commit()
        conn.close()

        # Should not raise, and run status should still be updated
        finalize_execution_run(
            run_id=ctx.run_id,
            attempt_id=ctx.attempt_id,
            status="completed",
            terminal_reason="completed",
        )

        conn2 = connect_runtime_db(_isolated_runtime_db)
        run = get_run(conn2, ctx.run_id)
        assert run["status"] == "completed"
        conn2.close()

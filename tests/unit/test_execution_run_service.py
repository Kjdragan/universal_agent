"""Unit tests for ExecutionRunService — run-per-task workspace isolation."""

import os
from pathlib import Path
import sqlite3
import tempfile
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    """Redirect AGENT_RUN_WORKSPACES and runtime DB to temp dirs."""
    repo_root = tmp_path / "fake_repo"
    repo_root.mkdir()
    monkeypatch.setenv("UA_REPO_ROOT", str(repo_root))

    runtime_db_dir = tmp_path / "runtime"
    runtime_db_dir.mkdir()
    runtime_db_path = str(runtime_db_dir / "runtime_state.db")
    # get_runtime_db_path() reads this env var
    monkeypatch.setenv("UA_RUNTIME_DB_PATH", runtime_db_path)

    # Ensure the runtime DB exists with schema via the canonical connect_runtime_db
    from universal_agent.durable.db import connect_runtime_db
    from universal_agent.durable.migrations import ensure_schema
    conn = connect_runtime_db(runtime_db_path)
    ensure_schema(conn)
    conn.close()

    yield {"repo_root": repo_root, "runtime_db_path": runtime_db_path}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAllocateExecutionRun:
    def test_creates_workspace_directory(self, tmp_path):
        from universal_agent.services.execution_run_service import (
            allocate_execution_run,
        )

        ctx = allocate_execution_run(
            task_id="test_task_1",
            origin="chat_panel",
            provider_session_id="sess_abc",
        )

        assert ctx.run_id.startswith("run_")
        assert Path(ctx.workspace_dir).exists()
        assert Path(ctx.workspace_dir).is_dir()

    def test_workspace_under_agent_run_workspaces(self, tmp_path):
        from universal_agent.services.execution_run_service import (
            allocate_execution_run,
        )

        ctx = allocate_execution_run(task_id="t1", origin="test")

        repo_root = Path(os.environ["UA_REPO_ROOT"])
        assert Path(ctx.workspace_dir).is_relative_to(
            repo_root / "AGENT_RUN_WORKSPACES"
        )

    def test_registers_in_durable_catalog(self, tmp_path):
        from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
        from universal_agent.durable.state import get_run
        from universal_agent.services.execution_run_service import (
            allocate_execution_run,
        )

        ctx = allocate_execution_run(
            task_id="test_task_2",
            origin="todo_dispatch",
        )

        db_path = get_runtime_db_path()
        conn = connect_runtime_db(db_path)
        row = get_run(conn, ctx.run_id)
        conn.close()

        assert row is not None
        assert row["run_id"] == ctx.run_id
        assert row["workspace_dir"] == ctx.workspace_dir
        assert row["status"] == "running"

    def test_creates_attempt_record(self, tmp_path):
        from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
        from universal_agent.durable.state import list_run_attempts
        from universal_agent.services.execution_run_service import (
            allocate_execution_run,
        )

        ctx = allocate_execution_run(
            task_id="test_task_3",
            origin="email",
        )

        db_path = get_runtime_db_path()
        conn = connect_runtime_db(db_path)
        attempts = list_run_attempts(conn, ctx.run_id)
        conn.close()

        assert len(attempts) == 1
        assert attempts[0]["attempt_id"] == ctx.attempt_id

    def test_unique_run_ids(self, tmp_path):
        from universal_agent.services.execution_run_service import (
            allocate_execution_run,
        )

        ctx1 = allocate_execution_run(task_id="t1", origin="test")
        ctx2 = allocate_execution_run(task_id="t2", origin="test")

        assert ctx1.run_id != ctx2.run_id
        assert ctx1.workspace_dir != ctx2.workspace_dir

    def test_context_fields_populated(self, tmp_path):
        from universal_agent.services.execution_run_service import (
            allocate_execution_run,
        )

        ctx = allocate_execution_run(
            task_id="t_field_test",
            origin="chat_panel",
            provider_session_id="sess_xyz",
        )

        assert ctx.task_id == "t_field_test"
        assert ctx.origin == "chat_panel"
        assert ctx.provider_session_id == "sess_xyz"
        assert ctx.attempt_number == 1
        assert ctx.created_at  # non-empty


class TestResolveActiveExecutionWorkspace:
    def test_prefers_assignment(self):
        from universal_agent.services.execution_run_service import (
            resolve_active_execution_workspace,
        )

        result = resolve_active_execution_workspace(
            assignment={"workspace_dir": "/run/ws_a"},
            request_metadata={"workspace_dir": "/req/ws_b"},
        )
        assert result == "/run/ws_a"

    def test_falls_back_to_request_metadata(self):
        from universal_agent.services.execution_run_service import (
            resolve_active_execution_workspace,
        )

        result = resolve_active_execution_workspace(
            assignment={"workspace_dir": ""},
            request_metadata={"workspace_dir": "/req/ws_b"},
        )
        assert result == "/req/ws_b"

    def test_falls_back_to_session(self):
        from universal_agent.services.execution_run_service import (
            resolve_active_execution_workspace,
        )

        class FakeSession:
            workspace_dir = "/session/ws_c"

        result = resolve_active_execution_workspace(
            assignment=None,
            request_metadata=None,
            session=FakeSession(),
        )
        assert result == "/session/ws_c"

    def test_returns_none_when_nothing_available(self):
        from universal_agent.services.execution_run_service import (
            resolve_active_execution_workspace,
        )

        result = resolve_active_execution_workspace()
        assert result is None


class TestResolveActiveRunId:
    def test_prefers_assignment(self):
        from universal_agent.services.execution_run_service import resolve_active_run_id

        result = resolve_active_run_id(
            assignment={"workflow_run_id": "run_aaa"},
            request_metadata={"workflow_run_id": "run_bbb"},
        )
        assert result == "run_aaa"

    def test_falls_back_to_request_metadata(self):
        from universal_agent.services.execution_run_service import resolve_active_run_id

        result = resolve_active_run_id(
            assignment={"workflow_run_id": ""},
            request_metadata={"workflow_run_id": "run_bbb"},
        )
        assert result == "run_bbb"

    def test_falls_back_to_session_metadata(self):
        from universal_agent.services.execution_run_service import resolve_active_run_id

        class FakeSession:
            metadata = {"active_run_id": "run_ccc"}

        result = resolve_active_run_id(session=FakeSession())
        assert result == "run_ccc"

    def test_returns_none_when_nothing_available(self):
        from universal_agent.services.execution_run_service import resolve_active_run_id

        result = resolve_active_run_id()
        assert result is None

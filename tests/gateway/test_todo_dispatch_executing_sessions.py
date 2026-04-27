"""Regression tests for ToDoDispatchService.executing_sessions lifecycle.

These tests exist specifically because the nightly documentation drift audit
VP agent (doc_maintenance_agent.py) accidentally deleted the executing_sessions
attribute during an automated refactor (commit a37f1622, 2026-04-24).

The executing_sessions set is load-bearing infrastructure:
  - idle_dispatch_loop.py checks it to determine if a session is busy
  - gateway_server.py clears it when an execution task finishes
  - The dispatch service itself guards against double-dispatch via this set

If any automated refactor removes these patterns, these tests will catch it.

Related incidents:
  - 2026-04-24: AttributeError on gateway_server.py:4227 during run cleanup
  - Stuck runs not clearing because idle dispatch couldn't detect busy sessions
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from universal_agent.gateway import GatewaySession
from universal_agent.services.todo_dispatch_service import (
    ToDoDispatchService,
    build_execution_manifest,
)

# ---------------------------------------------------------------------------
# Source-code inspection helpers: these parse the actual .py file to verify
# that critical code patterns exist, independent of runtime behavior.
# ---------------------------------------------------------------------------

_SOURCE_PATH = Path(inspect.getfile(ToDoDispatchService)).resolve()


def _read_source() -> str:
    return _SOURCE_PATH.read_text(encoding="utf-8")


def _parse_ast() -> ast.Module:
    return ast.parse(_read_source(), filename=str(_SOURCE_PATH))


# ---------------------------------------------------------------------------
# Test 1: executing_sessions exists in __init__
# ---------------------------------------------------------------------------


def test_executing_sessions_exists_on_init():
    """ToDoDispatchService.__init__ MUST create self.executing_sessions as a set."""
    service = ToDoDispatchService()
    assert hasattr(service, "executing_sessions"), (
        "ToDoDispatchService is missing the 'executing_sessions' attribute. "
        "This was likely removed by an automated refactor. "
        "Restore: self.executing_sessions: set[str] = set() in __init__"
    )
    assert isinstance(service.executing_sessions, set), (
        f"executing_sessions must be a set, got {type(service.executing_sessions)}"
    )


# ---------------------------------------------------------------------------
# Test 2: executing_sessions.add() happens during execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executing_sessions_added_during_execution(monkeypatch):
    """Session ID must be added to executing_sessions when dispatch starts."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Track when executing_sessions is populated during callback
    captured_executing = []

    async def _capture_callback(session_id, request):
        # Snapshot executing_sessions at the moment the callback runs
        captured_executing.append(set(service.executing_sessions))
        return {"decision": "accepted", "turn_id": "turn-test"}

    service = ToDoDispatchService(execution_callback=_capture_callback)
    session = GatewaySession(
        session_id="test_exec_session",
        user_id="daemon",
        workspace_dir="/tmp/test_exec",
        metadata={"source": "daemon", "session_role": "todo_execution"},
    )

    monkeypatch.setattr(
        "universal_agent.durable.db.connect_runtime_db", lambda *a, **k: conn
    )
    monkeypatch.setattr(
        "universal_agent.services.execution_run_service.allocate_execution_run",
        lambda **kwargs: SimpleNamespace(
            run_id="run_test_001", workspace_dir="/tmp/run_test_001"
        ),
    )
    monkeypatch.setattr(
        "universal_agent.services.dispatch_service.dispatch_sweep",
        lambda _conn, **kwargs: [
            {
                "task_id": "test:1",
                "assignment_id": "asg-test",
                "title": "Test task",
                "description": "Test description",
                "metadata": {
                    "delivery_mode": "interactive_chat",
                    "workflow_manifest": build_execution_manifest(
                        user_input="Test task",
                        delivery_mode="interactive_chat",
                        final_channel="chat",
                    ),
                },
            }
        ],
    )
    monkeypatch.setattr(
        "universal_agent.services.capacity_governor.CapacityGovernor.get_instance",
        lambda: type(
            "Governor",
            (),
            {"can_dispatch": staticmethod(lambda: (True, "ok"))},
        )(),
    )
    monkeypatch.setattr(
        "universal_agent.services.capacity_governor.capacity_snapshot",
        lambda: {
            "available_slots": 1,
            "active_slots": 0,
            "max_concurrent": 2,
            "in_backoff": False,
        },
    )
    monkeypatch.setattr(
        "universal_agent.services.llm_classifier.classify_agent_route",
        AsyncMock(
            return_value={
                "agent_id": "simone",
                "confidence": "high",
                "reasoning": "test",
                "method": "llm",
                "should_delegate": False,
            }
        ),
    )
    monkeypatch.setattr(
        "universal_agent.task_hub.get_agent_activity",
        lambda _conn: {"active_assignments": []},
    )

    service.register_session(session)
    await service._process_session(session)

    # Verify executing_sessions contained the session ID during callback
    assert captured_executing, "Callback was never called"
    assert "test_exec_session" in captured_executing[0], (
        "Session ID was not in executing_sessions during execution callback. "
        "The self.executing_sessions.add() call is missing."
    )


# ---------------------------------------------------------------------------
# Test 3: executing_sessions cleaned up even on error (finally block)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executing_sessions_cleaned_up_on_error(monkeypatch):
    """executing_sessions MUST be cleaned up even if execution crashes."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    async def _crashing_callback(session_id, request):
        raise RuntimeError("Simulated execution crash")

    service = ToDoDispatchService(execution_callback=_crashing_callback)
    session = GatewaySession(
        session_id="test_crash_session",
        user_id="daemon",
        workspace_dir="/tmp/test_crash",
        metadata={"source": "daemon", "session_role": "todo_execution"},
    )

    monkeypatch.setattr(
        "universal_agent.durable.db.connect_runtime_db", lambda *a, **k: conn
    )
    monkeypatch.setattr(
        "universal_agent.services.execution_run_service.allocate_execution_run",
        lambda **kwargs: SimpleNamespace(
            run_id="run_crash_001", workspace_dir="/tmp/run_crash_001"
        ),
    )
    monkeypatch.setattr(
        "universal_agent.services.dispatch_service.dispatch_sweep",
        lambda _conn, **kwargs: [
            {
                "task_id": "crash:1",
                "assignment_id": "asg-crash",
                "title": "Crash task",
                "description": "Will crash",
                "metadata": {
                    "delivery_mode": "interactive_chat",
                    "workflow_manifest": build_execution_manifest(
                        user_input="Crash",
                        delivery_mode="interactive_chat",
                        final_channel="chat",
                    ),
                },
            }
        ],
    )
    monkeypatch.setattr(
        "universal_agent.services.capacity_governor.CapacityGovernor.get_instance",
        lambda: type(
            "Governor",
            (),
            {"can_dispatch": staticmethod(lambda: (True, "ok"))},
        )(),
    )
    monkeypatch.setattr(
        "universal_agent.services.capacity_governor.capacity_snapshot",
        lambda: {
            "available_slots": 1,
            "active_slots": 0,
            "max_concurrent": 2,
            "in_backoff": False,
        },
    )
    monkeypatch.setattr(
        "universal_agent.services.llm_classifier.classify_agent_route",
        AsyncMock(
            return_value={
                "agent_id": "simone",
                "confidence": "high",
                "reasoning": "test",
                "method": "llm",
                "should_delegate": False,
            }
        ),
    )
    monkeypatch.setattr(
        "universal_agent.task_hub.get_agent_activity",
        lambda _conn: {"active_assignments": []},
    )
    # Prevent finalize_assignments from hitting a real DB
    monkeypatch.setattr(
        "universal_agent.task_hub.finalize_assignments",
        lambda *a, **kw: None,
    )

    service.register_session(session)

    # This should NOT raise — the exception is caught internally
    await service._process_session(session)

    # The critical check: executing_sessions must be empty after crash
    assert "test_crash_session" not in service.executing_sessions, (
        "Session ID was NOT cleaned up from executing_sessions after crash. "
        "The finally block with self.executing_sessions.discard() is missing."
    )


# ---------------------------------------------------------------------------
# Test 4: AST source inspection — verify patterns exist in source code
# ---------------------------------------------------------------------------


def test_source_code_contains_executing_sessions_patterns():
    """Verify the source file contains all three critical executing_sessions patterns.

    This test reads the actual Python source file and checks for the presence
    of the three code patterns that must exist together:
    1. Initialization in __init__: self.executing_sessions
    2. Adding session ID: self.executing_sessions.add(
    3. Cleanup in finally: self.executing_sessions.discard(

    This catches automated refactors that remove code without running tests.
    """
    source = _read_source()

    assert "self.executing_sessions" in source, (
        "Source code is missing 'self.executing_sessions' entirely"
    )
    assert "self.executing_sessions.add(" in source, (
        "Source code is missing 'self.executing_sessions.add(' — "
        "the session-busy registration was removed"
    )
    assert "self.executing_sessions.discard(" in source, (
        "Source code is missing 'self.executing_sessions.discard(' — "
        "the cleanup in finally block was removed"
    )

    # Verify the patterns appear in the right methods by AST inspection
    tree = _parse_ast()
    class_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ToDoDispatchService":
            class_node = node
            break
    assert class_node is not None, "ToDoDispatchService class not found in source"

    # Check __init__ has executing_sessions assignment
    init_method = None
    process_method = None
    for item in class_node.body:
        if isinstance(item, ast.FunctionDef) or isinstance(item, ast.AsyncFunctionDef):
            if item.name == "__init__":
                init_method = item
            elif item.name == "_process_session":
                process_method = item

    assert init_method is not None, "__init__ method not found"
    assert process_method is not None, "_process_session method not found"

    # Verify __init__ contains an assignment to self.executing_sessions
    init_source = ast.get_source_segment(_read_source(), init_method)
    assert init_source is not None, "Could not extract __init__ source"
    assert "executing_sessions" in init_source, (
        "__init__ does not contain 'executing_sessions' assignment"
    )

    # Verify _process_session contains both add and discard
    process_source = ast.get_source_segment(_read_source(), process_method)
    assert process_source is not None, "Could not extract _process_session source"
    assert "executing_sessions" in process_source, (
        "_process_session does not reference 'executing_sessions'"
    )

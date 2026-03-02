"""
Tests for SessionContext and ContextVar isolation.

Key property being tested: asyncio.create_task() copies the current context,
so set_ctx() writes in one task are never visible to another task. This is
the foundation of the concurrency refactor.
"""
import asyncio
import time

import pytest

from universal_agent.session_ctx import (
    SessionContext,
    get_ctx,
    require_ctx,
    reset_ctx,
    set_ctx,
    _default_trace,
)


# ---------------------------------------------------------------------------
# SessionContext construction
# ---------------------------------------------------------------------------

def test_session_context_defaults():
    ctx = SessionContext()
    assert ctx.run_id == "unknown"
    assert ctx.current_step_id is None
    assert ctx.runtime_db_conn is None
    assert ctx.gateway_mode_active is False
    assert ctx.forced_tool_mode_active is False
    assert isinstance(ctx.trace, dict)
    assert "tool_calls" in ctx.trace
    assert isinstance(ctx.forced_tool_queue, list)
    assert isinstance(ctx.budget_state, dict)
    assert isinstance(ctx.tool_execution_emitted_ids, set)


def test_session_context_mutable_fields_not_shared_between_instances():
    """Each SessionContext instance must have its own mutable containers."""
    ctx_a = SessionContext()
    ctx_b = SessionContext()
    ctx_a.forced_tool_queue.append({"tool": "bash"})
    assert len(ctx_b.forced_tool_queue) == 0, "Mutable default must not be shared"
    ctx_a.trace["tool_calls"].append({"id": "x"})
    assert len(ctx_b.trace["tool_calls"]) == 0


def test_session_context_custom_run_id():
    ctx = SessionContext(run_id="test-run-42", gateway_mode_active=True)
    assert ctx.run_id == "test-run-42"
    assert ctx.gateway_mode_active is True


def test_default_trace_structure():
    t = _default_trace()
    assert t["tool_calls"] == []
    assert "input" in t["token_usage"]
    assert "context_pressure" in t


# ---------------------------------------------------------------------------
# ContextVar get/set/reset
# ---------------------------------------------------------------------------

def test_get_ctx_returns_none_when_unset():
    assert get_ctx() is None


def test_set_ctx_and_get_ctx():
    ctx = SessionContext(run_id="my-run")
    token = set_ctx(ctx)
    try:
        result = get_ctx()
        assert result is ctx
        assert result.run_id == "my-run"
    finally:
        reset_ctx(token)


def test_require_ctx_raises_when_unset():
    # Ensure no ctx is set in this thread/task
    token = set_ctx(None)  # explicitly clear
    try:
        with pytest.raises(RuntimeError, match="No SessionContext is active"):
            require_ctx()
    finally:
        reset_ctx(token)


def test_require_ctx_returns_ctx_when_set():
    ctx = SessionContext(run_id="required-run")
    token = set_ctx(ctx)
    try:
        assert require_ctx() is ctx
    finally:
        reset_ctx(token)


def test_reset_ctx_restores_prior_value():
    ctx_a = SessionContext(run_id="a")
    ctx_b = SessionContext(run_id="b")
    token_a = set_ctx(ctx_a)
    token_b = set_ctx(ctx_b)
    assert get_ctx().run_id == "b"
    reset_ctx(token_b)
    assert get_ctx().run_id == "a"
    reset_ctx(token_a)


# ---------------------------------------------------------------------------
# ContextVar isolation across asyncio tasks — the core concurrency property
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_context_isolation_across_tasks():
    """
    Two concurrent tasks must not see each other's SessionContext.
    asyncio.create_task() copies the context at task creation time.
    Writes inside the task are invisible to the parent and to sibling tasks.
    """
    task_a_run_id = None
    task_b_run_id = None

    barrier = asyncio.Event()

    async def task_a():
        nonlocal task_a_run_id
        ctx = SessionContext(run_id="session-A")
        set_ctx(ctx)
        await barrier.wait()  # yield; let task_b run
        task_a_run_id = require_ctx().run_id

    async def task_b():
        nonlocal task_b_run_id
        ctx = SessionContext(run_id="session-B")
        set_ctx(ctx)
        barrier.set()
        task_b_run_id = require_ctx().run_id

    await asyncio.gather(
        asyncio.create_task(task_a()),
        asyncio.create_task(task_b()),
    )

    assert task_a_run_id == "session-A", f"Task A saw wrong run_id: {task_a_run_id}"
    assert task_b_run_id == "session-B", f"Task B saw wrong run_id: {task_b_run_id}"


@pytest.mark.asyncio
async def test_mutable_state_isolation_across_tasks():
    """
    Mutations to a SessionContext field in one task must not affect another task.
    """
    results: dict = {}
    ready = asyncio.Event()
    done_a = asyncio.Event()

    async def task_a():
        ctx = SessionContext(run_id="A")
        set_ctx(ctx)
        ready.set()
        await done_a.wait()
        results["a_tool_calls"] = len(require_ctx().trace["tool_calls"])

    async def task_b():
        ctx = SessionContext(run_id="B")
        set_ctx(ctx)
        await ready.wait()
        # Mutate task B's trace — should NOT affect task A
        require_ctx().trace["tool_calls"].append({"id": "tool-b-1"})
        require_ctx().trace["tool_calls"].append({"id": "tool-b-2"})
        done_a.set()
        results["b_tool_calls"] = len(require_ctx().trace["tool_calls"])

    await asyncio.gather(
        asyncio.create_task(task_a()),
        asyncio.create_task(task_b()),
    )

    assert results["a_tool_calls"] == 0, "Task A's trace was contaminated by task B"
    assert results["b_tool_calls"] == 2


@pytest.mark.asyncio
async def test_three_concurrent_sessions_fully_isolated():
    """
    Three sessions run concurrently. Each must see only its own run_id.
    """
    seen = {}
    gate = asyncio.Barrier(3)

    async def run_session(name: str):
        ctx = SessionContext(run_id=f"run-{name}")
        set_ctx(ctx)
        await gate.wait()  # all three reach the gate simultaneously
        seen[name] = require_ctx().run_id

    await asyncio.gather(
        asyncio.create_task(run_session("alpha")),
        asyncio.create_task(run_session("beta")),
        asyncio.create_task(run_session("gamma")),
    )

    assert seen["alpha"] == "run-alpha"
    assert seen["beta"] == "run-beta"
    assert seen["gamma"] == "run-gamma"


@pytest.mark.asyncio
async def test_parent_context_not_affected_by_child_task():
    """
    The parent task's context must not be changed by child task's set_ctx().
    """
    parent_ctx = SessionContext(run_id="parent")
    set_ctx(parent_ctx)

    child_completed = asyncio.Event()

    async def child():
        ctx = SessionContext(run_id="child")
        set_ctx(ctx)
        child_completed.set()

    task = asyncio.create_task(child())
    await child_completed.wait()
    await task

    assert require_ctx().run_id == "parent", "Parent context was mutated by child task"

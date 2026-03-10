import asyncio

from universal_agent.hooks import AgentHookSet


def _run(coro):
    return asyncio.run(coro)


def _pre_task_stop(hooks: AgentHookSet, task_id: str | None):
    tool_input = {}
    if task_id is not None:
        tool_input["task_id"] = task_id
    return _run(
        hooks.on_pre_tool_use_ledger(
            {
                "tool_name": "TaskStop",
                "tool_input": tool_input,
            },
            "tool-taskstop",
            {},
        )
    )


def test_blocks_task_stop_with_missing_task_id():
    hooks = AgentHookSet(run_id="unit-taskstop-missing")
    result = _pre_task_stop(hooks, None)
    assert result.get("decision") == "block"
    assert "Missing `task_id`" in str(result.get("systemMessage", ""))


def test_blocks_task_stop_with_placeholder_id():
    hooks = AgentHookSet(run_id="unit-taskstop-placeholder")
    result = _pre_task_stop(hooks, "all")
    assert result.get("decision") == "block"
    assert "placeholder" in str(result.get("systemMessage", "")).lower()


def test_blocks_task_stop_with_session_id():
    hooks = AgentHookSet(run_id="unit-taskstop-session")
    result = _pre_task_stop(hooks, "session_20260309_073910_8099458a")
    assert result.get("decision") == "block"
    assert "session/run identifier" in str(result.get("systemMessage", "")).lower()


def test_blocks_task_stop_with_fabricated_id_prefix():
    hooks = AgentHookSet(run_id="unit-taskstop-fabricated")
    result = _pre_task_stop(hooks, "dummy-stop")
    assert result.get("decision") == "block"
    assert "fabricated" in str(result.get("systemMessage", "")).lower() or "placeholder" in str(
        result.get("systemMessage", "")
    ).lower()


def test_blocks_task_stop_with_weak_synthetic_task_id():
    hooks = AgentHookSet(run_id="unit-taskstop-weak-id")
    result = _pre_task_stop(hooks, "task_1")
    assert result.get("decision") == "block"
    assert "untrusted `task_id`" in str(result.get("systemMessage", "")).lower()


def test_allows_task_stop_with_concrete_task_id():
    hooks = AgentHookSet(run_id="unit-taskstop-allow")
    result = _pre_task_stop(hooks, "task_01HZYQ7QF1")
    assert result == {}


def test_blocks_duplicate_task_stop_after_successful_stop():
    hooks = AgentHookSet(run_id="unit-taskstop-duplicate")
    first = _pre_task_stop(hooks, "task_01HZYQ7QF1")
    assert first == {}

    _run(
        hooks.on_post_tool_use_ledger(
            {
                "tool_name": "TaskStop",
                "tool_input": {"task_id": "task_01HZYQ7QF1"},
                "tool_result": {"is_error": False, "content": [{"type": "text", "text": "ok"}]},
                "is_error": False,
            },
            "tool-taskstop-post",
            {},
        )
    )

    second = _pre_task_stop(hooks, "task_01HZYQ7QF1")
    assert second.get("decision") == "block"
    assert "duplicate" in str(second.get("systemMessage", "")).lower()

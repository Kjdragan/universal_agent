from universal_agent import main as agent_main


def _item(tool_call_id: str, *, step_id: str, created_at: str, replay_policy: str, status: str = "running"):
    return {
        "tool_call_id": tool_call_id,
        "tool_name": "task" if replay_policy == "RELAUNCH" else "Glob",
        "tool_namespace": "claude_code" if replay_policy == "RELAUNCH" else "composio",
        "replay_policy": replay_policy,
        "status": status,
        "step_id": step_id,
        "created_at": created_at,
    }


def test_partition_skips_tools_after_running_relaunch_task():
    inflight = [
        _item(
            "glob-before",
            step_id="step-1",
            created_at="2026-01-01T00:00:00+00:00",
            replay_policy="REPLAY_EXACT",
        ),
        _item(
            "task-run",
            step_id="step-1",
            created_at="2026-01-01T00:00:10+00:00",
            replay_policy="RELAUNCH",
        ),
        _item(
            "glob-after",
            step_id="step-1",
            created_at="2026-01-01T00:00:20+00:00",
            replay_policy="REPLAY_EXACT",
        ),
    ]
    replay, skipped = agent_main._partition_inflight_for_relaunch(inflight)
    assert [item["tool_call_id"] for item in replay] == ["glob-before", "task-run"]
    assert [item["tool_call_id"] for item in skipped] == ["glob-after"]


def test_partition_does_not_skip_prepared_relaunch_task():
    inflight = [
        _item(
            "task-prepared",
            step_id="step-1",
            created_at="2026-01-01T00:00:10+00:00",
            replay_policy="RELAUNCH",
            status="prepared",
        ),
        _item(
            "glob-after",
            step_id="step-1",
            created_at="2026-01-01T00:00:20+00:00",
            replay_policy="REPLAY_EXACT",
        ),
    ]
    replay, skipped = agent_main._partition_inflight_for_relaunch(inflight)
    assert [item["tool_call_id"] for item in replay] == ["task-prepared", "glob-after"]
    assert skipped == []

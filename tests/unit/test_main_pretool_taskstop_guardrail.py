import asyncio
from pathlib import Path
import sqlite3

from universal_agent import main as agent_main
from universal_agent.durable.ledger import ToolCallLedger
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import start_step, upsert_run
from universal_agent.session_ctx import SessionContext, reset_ctx, set_ctx


def _run(coro):
    return asyncio.run(coro)


def _runtime_conn(*, run_id: str, run_kind: str = "", with_task_evidence: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    upsert_run(
        conn,
        run_id=run_id,
        entrypoint="test",
        run_spec={},
        run_kind=run_kind or None,
    )
    if with_task_evidence:
        step_id = f"step:{run_id}"
        start_step(conn, run_id, step_id, 1)
        ledger = ToolCallLedger(conn)
        ledger.prepare_tool_call(
            tool_call_id=f"tool:{run_id}:task",
            run_id=run_id,
            step_id=step_id,
            tool_name="task",
            tool_namespace="claude_code",
            raw_tool_name="Task",
            tool_input={"subagent_type": "research-specialist", "prompt": "collect data"},
        )
    return conn


def _call_pre(tool_name: str, tool_input: dict):
    return _run(
        agent_main.on_pre_tool_use_ledger(
            {
                "tool_name": tool_name,
                "tool_input": tool_input,
            },
            "tool-pre-main",
            {},
        )
    )


def test_main_blocks_taskstop_placeholder_id(tmp_path: Path):
    token = set_ctx(SessionContext(run_id="run-main-taskstop", observer_workspace_dir=str(tmp_path)))
    try:
        result = _call_pre("TaskStop", {"task_id": "all"})
    finally:
        reset_ctx(token)

    assert result.get("decision") == "block"
    assert "Invalid `TaskStop` request blocked" in str(result.get("systemMessage", ""))


def test_main_blocks_taskstop_with_session_id(tmp_path: Path):
    token = set_ctx(SessionContext(run_id="run-main-taskstop-session", observer_workspace_dir=str(tmp_path)))
    try:
        result = _call_pre("TaskStop", {"task_id": "session_20260309_073910_8099458a"})
    finally:
        reset_ctx(token)

    assert result.get("decision") == "block"
    assert "session/run identifier" in str(result.get("systemMessage", "")).lower()


def test_main_blocks_taskstop_with_weak_synthetic_id(tmp_path: Path):
    token = set_ctx(SessionContext(run_id="run-main-taskstop-weak", observer_workspace_dir=str(tmp_path)))
    try:
        result = _call_pre("TaskStop", {"task_id": "task_1"})
    finally:
        reset_ctx(token)

    assert result.get("decision") == "block"
    assert "untrusted `task_id`" in str(result.get("systemMessage", "")).lower()


def test_main_blocks_taskstop_with_natural_language_id(tmp_path: Path):
    token = set_ctx(SessionContext(run_id="run-main-taskstop-natural", observer_workspace_dir=str(tmp_path)))
    try:
        result = _call_pre("TaskStop", {"task_id": "research-specialist"})
    finally:
        reset_ctx(token)

    assert result.get("decision") == "block"
    assert "untrusted `task_id`" in str(result.get("systemMessage", "")).lower()


def test_main_allows_agent_for_pipeline_subagent(tmp_path: Path):
    token = set_ctx(SessionContext(run_id="run-main-agent", observer_workspace_dir=str(tmp_path)))
    try:
        result = _call_pre(
            "Agent",
            {
                "subagent_type": "research-specialist",
                "description": "research task",
                "prompt": "collect sources",
            },
        )
    finally:
        reset_ctx(token)

    assert result == {}


def test_main_blocks_taskstop_with_valid_sdk_id_when_no_prior_task_evidence(tmp_path: Path):
    conn = _runtime_conn(run_id="run-main-taskstop-no-evidence", run_kind="general")
    token = set_ctx(
        SessionContext(
            run_id="run-main-taskstop-no-evidence",
            observer_workspace_dir=str(tmp_path),
            runtime_db_conn=conn,
        )
    )
    try:
        result = _call_pre("TaskStop", {"task_id": "task_01HZYQ7QF1"})
    finally:
        reset_ctx(token)

    assert result.get("decision") == "block"
    assert "no active sdk-managed task is known in this run" in str(result.get("systemMessage", "")).lower()


def test_main_allows_taskstop_with_sdk_evidence_in_general_lane(tmp_path: Path):
    conn = _runtime_conn(
        run_id="run-main-taskstop-allow",
        run_kind="general",
        with_task_evidence=True,
    )
    token = set_ctx(
        SessionContext(
            run_id="run-main-taskstop-allow",
            observer_workspace_dir=str(tmp_path),
            runtime_db_conn=conn,
        )
    )
    try:
        result = _call_pre("TaskStop", {"task_id": "task_01HZYQ7QF1"})
    finally:
        reset_ctx(token)

    assert result == {}


def test_main_blocks_taskstop_in_todo_execution_with_valid_sdk_id(tmp_path: Path):
    conn = _runtime_conn(
        run_id="run-main-taskstop-todo",
        run_kind="todo_execution",
        with_task_evidence=True,
    )
    token = set_ctx(
        SessionContext(
            run_id="run-main-taskstop-todo",
            observer_workspace_dir=str(tmp_path),
            runtime_db_conn=conn,
        )
    )
    try:
        result = _call_pre("TaskStop", {"task_id": "task_01HZYQ7QF1"})
    finally:
        reset_ctx(token)

    assert result.get("decision") == "block"
    text = str(result.get("systemMessage", "")).lower()
    assert "task hub" in text
    assert "task_hub_task_action" in text


def test_main_research_tool_injects_session_workspace_from_transcript_path(tmp_path: Path):
    session_workspace = tmp_path / "session_20260311_abc12345"
    session_workspace.mkdir()
    (session_workspace / "work_products").mkdir()
    (session_workspace / "session_policy.json").write_text("{}", encoding="utf-8")
    transcript_path = session_workspace / "subagent_outputs" / "research" / "transcript.md"
    transcript_path.parent.mkdir(parents=True)
    transcript_path.write_text("", encoding="utf-8")

    payload = {
        "tool_name": "mcp__internal__run_research_phase",
        "tool_input": {
            "query": "q",
            "task_name": "t",
        },
        "transcript_path": str(transcript_path),
    }

    token = set_ctx(SessionContext(run_id="run-main-research-ws", observer_workspace_dir="/opt/universal_agent"))
    try:
        result = _run(agent_main.on_pre_tool_use_ledger(payload, "tool-pre-main-research", {}))
    finally:
        reset_ctx(token)

    assert result == {}
    assert payload["tool_input"]["workspace_dir"] == str(session_workspace.resolve())


def test_main_research_tool_injects_run_workspace_from_transcript_path(tmp_path: Path):
    run_workspace = tmp_path / "run_20260311_abc12345"
    run_workspace.mkdir()
    (run_workspace / "work_products").mkdir()
    (run_workspace / "run_manifest.json").write_text("{}", encoding="utf-8")
    transcript_path = run_workspace / "subagent_outputs" / "research" / "transcript.md"
    transcript_path.parent.mkdir(parents=True)
    transcript_path.write_text("", encoding="utf-8")

    payload = {
        "tool_name": "mcp__internal__run_research_phase",
        "tool_input": {
            "query": "q",
            "task_name": "t",
        },
        "transcript_path": str(transcript_path),
    }

    token = set_ctx(SessionContext(run_id="run-main-research-run-ws", observer_workspace_dir="/opt/universal_agent"))
    try:
        result = _run(agent_main.on_pre_tool_use_ledger(payload, "tool-pre-main-research-run", {}))
    finally:
        reset_ctx(token)

    assert result == {}
    assert payload["tool_input"]["workspace_dir"] == str(run_workspace.resolve())

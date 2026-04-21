from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from universal_agent.gateway import GatewaySession
from universal_agent.services.todo_dispatch_service import (
    ToDoDispatchService,
    _enrich_with_llm_agent_routing,
    build_execution_manifest,
    build_todo_execution_prompt,
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


@pytest.mark.asyncio
async def test_todo_dispatch_service_executes_claimed_tasks(monkeypatch):
    conn = _conn()
    callback = AsyncMock(return_value={"decision": "accepted", "turn_id": "turn-1"})
    service = ToDoDispatchService(execution_callback=callback)
    session = GatewaySession(
        session_id="daemon_simone_todo",
        user_id="daemon",
        workspace_dir="/tmp/daemon_simone_todo",
        metadata={"source": "daemon", "session_role": "todo_execution", "run_kind": "todo_execution"},
    )

    monkeypatch.setattr("universal_agent.durable.db.connect_runtime_db", lambda *a, **k: conn)
    monkeypatch.setattr(
        "universal_agent.services.execution_run_service.allocate_execution_run",
        lambda **kwargs: SimpleNamespace(run_id="run_todo_001", workspace_dir="/tmp/run_todo_001"),
    )
    monkeypatch.setattr(
        "universal_agent.services.dispatch_service.dispatch_sweep",
        lambda _conn, **kwargs: [
            {
                "task_id": "email:1",
                "assignment_id": "asg-1",
                "title": "Weather report",
                "description": "Get the Houston weather",
                "metadata": {
                    "delivery_mode": "standard_report",
                    "workflow_manifest": build_execution_manifest(
                        user_input="Get the Houston weather",
                        delivery_mode="standard_report",
                        final_channel="email",
                    ),
                },
                "_routing": {"agent_id": "simone"},
            }
        ],
    )
    monkeypatch.setattr(
        "universal_agent.services.capacity_governor.CapacityGovernor.get_instance",
        lambda: type("Governor", (), {"can_dispatch": staticmethod(lambda: (True, "capacity_available"))})(),
    )
    monkeypatch.setattr(
        "universal_agent.services.capacity_governor.capacity_snapshot",
        lambda: {"available_slots": 1, "active_slots": 0, "max_concurrent": 2, "in_backoff": False},
    )
    monkeypatch.setattr(
        "universal_agent.services.llm_classifier.classify_agent_route",
        AsyncMock(
            return_value={
                "agent_id": "simone",
                "confidence": "medium",
                "reasoning": "Simone should coordinate the weather response.",
                "method": "llm",
                "should_delegate": False,
            }
        ),
    )
    monkeypatch.setattr(
        "universal_agent.task_hub.get_agent_activity",
        lambda _conn: {"active_assignments": [{"agent_id": "todo:daemon_simone_todo", "task_id": "email:x", "title": "Existing"}]},
    )

    service.register_session(session)
    await service._process_session(session)

    callback.assert_awaited_once()
    called_session_id, request = callback.await_args.args
    assert called_session_id == "daemon_simone_todo"
    assert request.metadata["source"] == "todo_dispatcher"
    assert request.metadata["run_kind"] == "todo_execution"
    assert request.metadata["claimed_task_ids"] == ["email:1"]
    assert "already claimed" in request.user_input.lower()
    assert "do not re-claim" in request.user_input.lower()
    assert "taskstop" in request.user_input.lower()
    assert "capacity snapshot" in request.user_input.lower()
    assert "delivery contract" in request.user_input.lower()
    assert "work item 1" in request.user_input.lower()
    assert "workflow_kind=research_report_email" in request.user_input
    # NOTE: vp.general.primary now legitimately appears in the VP-Targeted Email Tasks
    # section of the dispatch prompt as an example agent name, so we no longer assert its absence.


@pytest.mark.asyncio
async def test_todo_dispatch_service_requeues_when_capacity_blocked(monkeypatch):
    conn = _conn()
    callback = AsyncMock()
    service = ToDoDispatchService(execution_callback=callback)
    session = GatewaySession(
        session_id="daemon_simone_todo",
        user_id="daemon",
        workspace_dir="/tmp/daemon_simone_todo",
        metadata={"source": "daemon", "session_role": "todo_execution", "run_kind": "todo_execution"},
    )

    monkeypatch.setattr("universal_agent.durable.db.connect_runtime_db", lambda *a, **k: conn)
    monkeypatch.setattr(
        "universal_agent.services.capacity_governor.CapacityGovernor.get_instance",
        lambda: type("Governor", (), {"can_dispatch": staticmethod(lambda: (False, "capacity_full"))})(),
    )

    await service._process_session(session)

    callback.assert_not_called()
    assert "daemon_simone_todo" in service.wake_sessions


def test_todo_dispatch_service_ignores_non_todo_sessions():
    service = ToDoDispatchService()
    session = GatewaySession(
        session_id="session_hook_agentmail_test",
        user_id="webhook",
        workspace_dir="/tmp/hook",
        metadata={"source": "webhook", "session_role": "email_triage", "run_kind": "email_triage"},
    )

    service.register_session(session)

    assert service.active_sessions == {}


def test_todo_dispatch_service_emits_utc_timestamps():
    events: list[dict[str, object]] = []
    service = ToDoDispatchService(event_callback=events.append)
    session = GatewaySession(
        session_id="daemon_simone_todo",
        user_id="daemon",
        workspace_dir="/tmp/daemon_simone_todo",
        metadata={"source": "daemon", "session_role": "todo_execution", "run_kind": "todo_execution"},
    )

    service.register_session(session)
    service.request_dispatch_now("daemon_simone_todo")
    service.unregister_session("daemon_simone_todo")

    assert len(events) == 3
    for event in events:
        timestamp = str(event.get("timestamp") or "")
        assert timestamp.endswith("+00:00")


def test_build_execution_manifest_supports_interactive_email():
    manifest = build_execution_manifest(
        user_input="Write a short poem about a rabbit and email it to me.",
        delivery_mode="interactive_email",
        final_channel="email",
    )

    assert manifest["workflow_kind"] == "interactive_answer_email"
    assert manifest["delivery_mode"] == "interactive_email"
    assert manifest["requires_pdf"] is False
    assert manifest["final_channel"] == "email"


def test_build_execution_manifest_marks_code_change_with_codebase_root(monkeypatch):
    monkeypatch.setenv("UA_APPROVED_CODEBASE_ROOTS", "/opt/universal_agent")
    manifest = build_execution_manifest(
        user_input="Implement this feature in the repo, add tests, and fix the code.",
        delivery_mode="interactive_chat",
        final_channel="chat",
    )

    assert manifest["workflow_kind"] == "code_change"
    assert manifest["codebase_root"] == "/opt/universal_agent"
    assert manifest["repo_mutation_allowed"] is True


def test_build_execution_manifest_does_not_treat_report_as_repo(monkeypatch):
    monkeypatch.setenv("UA_APPROVED_CODEBASE_ROOTS", "/opt/universal_agent")
    manifest = build_execution_manifest(
        user_input=(
            "Create a knowledge base about the Hermes agent. Use NotebookLM deep "
            "research, create a report, an infographic, and an audio file, then "
            "email them to me."
        ),
        delivery_mode="standard_report",
        final_channel="email",
    )

    assert manifest["workflow_kind"] == "research_report_email"
    assert manifest["codebase_root"] == ""
    assert manifest["repo_mutation_allowed"] is False


@pytest.mark.asyncio
async def test_llm_routing_reconciles_false_code_change_manifest(monkeypatch):
    task = {
        "task_id": "chat:hermes",
        "title": "Hermes agent knowledge base",
        "description": (
            "Create a knowledge base about the Hermes agent and its latest updates "
            "over the last three weeks. Use NotebookLM deep research, then create "
            "a report, infographic, and audio file."
        ),
        "source_kind": "chat_panel",
        "project_key": "immediate",
        "labels": ["interactive"],
        "metadata": {
            "delivery_mode": "standard_report",
            "workflow_manifest": {
                "workflow_kind": "code_change",
                "delivery_mode": "standard_report",
                "requires_pdf": True,
                "final_channel": "email",
                "canonical_executor": "simone_first",
                "codebase_root": "/opt/universal_agent",
                "repo_mutation_allowed": True,
            },
        },
    }
    monkeypatch.setattr(
        "universal_agent.services.llm_classifier.classify_agent_route",
        AsyncMock(
            return_value={
                "agent_id": "vp.general.primary",
                "confidence": "high",
                "reasoning": "NotebookLM research and artifacts belong with ATLAS.",
                "method": "llm",
                "should_delegate": True,
            }
        ),
    )

    await _enrich_with_llm_agent_routing([task], active_assignments=[])

    manifest = task["metadata"]["workflow_manifest"]
    assert task["_routing"]["agent_id"] == "vp.general.primary"
    assert task["_routing"]["method"] == "llm"
    assert manifest["workflow_kind"] == "research_report_email"
    assert manifest["codebase_root"] == ""
    assert manifest["repo_mutation_allowed"] is False
    assert manifest["llm_agent_route"]["agent_id"] == "vp.general.primary"


def test_build_todo_execution_prompt_uses_mode_specific_delivery_contract():
    prompt = build_todo_execution_prompt(
        claimed_items=[
            {
                "task_id": "chat:1",
                "title": "Write a poem",
                "description": "Write a poem and email it.",
                "metadata": {
                    "delivery_mode": "interactive_email",
                    "workflow_manifest": build_execution_manifest(
                        user_input="Write a poem and email it.",
                        delivery_mode="interactive_email",
                        final_channel="email",
                    ),
                },
            }
        ],
        capacity_snapshot_data={"available_slots": 1, "active_slots": 0, "max_concurrent": 2, "in_backoff": False},
        active_assignments=[],
        origin_label="interactive_chat:session_test",
    )

    assert "interactive_email" in prompt
    assert "direct final email aligned to the user's request" in prompt
    assert "executive summary in the body" not in prompt


# ---------------------------------------------------------------------------
# Tests for hardened regex-based workflow markers (word-boundary matching)
# ---------------------------------------------------------------------------


class TestInferWorkflowKindWordBoundary:
    """Verify that infer_workflow_kind uses word-boundary regex patterns
    and no longer false-positives on substring matches."""

    def test_fix_with_trailing_period(self):
        manifest = build_execution_manifest(
            user_input="Please fix this issue.",
            delivery_mode="interactive_chat",
            final_channel="chat",
        )
        assert manifest["workflow_kind"] == "code_change"

    def test_fix_with_newline(self):
        manifest = build_execution_manifest(
            user_input="Fix\nthe broken auth flow",
            delivery_mode="interactive_chat",
            final_channel="chat",
        )
        assert manifest["workflow_kind"] == "code_change"

    def test_fixing_is_not_code_change(self):
        """'fixing' should not match the \\bfix\\b pattern."""
        manifest = build_execution_manifest(
            user_input="I am fixing dinner, not code.",
            delivery_mode="interactive_chat",
            final_channel="chat",
        )
        assert manifest["workflow_kind"] != "code_change"

    def test_debug_matches(self):
        manifest = build_execution_manifest(
            user_input="Debug the failing test suite",
            delivery_mode="interactive_chat",
            final_channel="chat",
        )
        assert manifest["workflow_kind"] == "code_change"

    def test_debugger_is_not_code_change(self):
        """'debugger' should not match the \\bdebug\\b pattern."""
        manifest = build_execution_manifest(
            user_input="Configure the debugger settings in my IDE",
            delivery_mode="interactive_chat",
            final_channel="chat",
        )
        assert manifest["workflow_kind"] != "code_change"

    def test_python_matches(self):
        manifest = build_execution_manifest(
            user_input="Write a python script to parse CSV files",
            delivery_mode="interactive_chat",
            final_channel="chat",
        )
        assert manifest["workflow_kind"] == "code_change"

    def test_pythonic_is_not_code_change(self):
        """'pythonic' should not match the \\bpython\\b pattern."""
        manifest = build_execution_manifest(
            user_input="Write in a pythonic style with list comprehensions",
            delivery_mode="interactive_chat",
            final_channel="chat",
        )
        assert manifest["workflow_kind"] != "code_change"

    def test_report_no_longer_false_positive_code_change(self):
        """Bare 'report' was removed from code markers, so 'bug report'
        should not trigger code_change."""
        manifest = build_execution_manifest(
            user_input="file a bug report about the login issue",
            delivery_mode="interactive_chat",
            final_channel="chat",
        )
        # Should not be code_change since 'report' alone is not a code marker
        assert manifest["workflow_kind"] != "code_change"

    def test_research_marker_triggers_research_report(self):
        manifest = build_execution_manifest(
            user_input="Do some research on quantum computing trends",
            delivery_mode="interactive_chat",
            final_channel="chat",
        )
        assert manifest["workflow_kind"] == "research_report_chat"

    def test_analysis_marker_triggers_research_report(self):
        manifest = build_execution_manifest(
            user_input="Perform a competitive analysis of SaaS pricing models",
            delivery_mode="interactive_chat",
            final_channel="chat",
        )
        assert manifest["workflow_kind"] == "research_report_chat"

    def test_pdf_word_boundary(self):
        """'pdf' should match as a word, not substring."""
        manifest = build_execution_manifest(
            user_input="generate a pdf summary of the findings",
            delivery_mode="interactive_chat",
            final_channel="email",
        )
        assert manifest["workflow_kind"] == "research_report_email"

    def test_general_execution_fallback(self):
        manifest = build_execution_manifest(
            user_input="Schedule a team sync for Friday afternoon",
            delivery_mode="interactive_chat",
            final_channel="chat",
        )
        assert manifest["workflow_kind"] == "interactive_answer"

    def test_refactor_matches(self):
        manifest = build_execution_manifest(
            user_input="Refactor the authentication module",
            delivery_mode="interactive_chat",
            final_channel="chat",
        )
        assert manifest["workflow_kind"] == "code_change"

    def test_implement_matches(self):
        manifest = build_execution_manifest(
            user_input="Implement user registration endpoint",
            delivery_mode="interactive_chat",
            final_channel="chat",
        )
        assert manifest["workflow_kind"] == "code_change"

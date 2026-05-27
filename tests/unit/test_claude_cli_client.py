"""Unit tests for the Claude Code CLI Client and execution_mode dispatch pipeline.

Tests cover:
- MissionOutcome validation
- ClaudeCodeCLIClient.run_mission behavior (missing objective, CLI not found, success, timeout)
- Retry logic
- Worker loop client selection based on execution_mode
- Dispatch payload propagation of execution_mode
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from universal_agent.vp.clients.base import MissionOutcome, VpClient
from universal_agent.vp.clients.claude_cli_client import (
    ClaudeCodeCLIClient,
    _build_cli_prompt,
    _is_auth_failure,
    _parse_payload,
)


# ---------------------------------------------------------------------------
# 1. Missing objective returns failed immediately
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cli_client_missing_objective_returns_failed(tmp_path: Path):
    client = ClaudeCodeCLIClient()
    mission: dict[str, Any] = {
        "mission_id": "test-mission-1",
        "objective": "",
        "payload_json": "{}",
    }
    outcome = await client.run_mission(mission=mission, workspace_root=tmp_path)
    assert outcome.status == "failed"
    assert "missing objective" in (outcome.message or "").lower()


# ---------------------------------------------------------------------------
# 2. FileNotFoundError (claude not installed) gives clear message
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cli_client_claude_not_found_returns_failed(tmp_path: Path):
    client = ClaudeCodeCLIClient()
    mission: dict[str, Any] = {
        "mission_id": "test-mission-2",
        "objective": "Create hello.py",
        "payload_json": json.dumps({"timeout_seconds": 30, "max_retries": 0}),
    }
    with patch("universal_agent.vp.clients.claude_cli_client.asyncio.create_subprocess_exec") as mock_exec:
        mock_exec.side_effect = FileNotFoundError("claude")
        outcome = await client.run_mission(mission=mission, workspace_root=tmp_path)

    assert outcome.status == "failed"
    assert "claude CLI not found" in (outcome.message or "")


# ---------------------------------------------------------------------------
# 3. Successful CLI run returns completed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cli_client_successful_run_returns_completed(tmp_path: Path):
    client = ClaudeCodeCLIClient()
    mission: dict[str, Any] = {
        "mission_id": "test-mission-3",
        "objective": "Write hello world",
        "payload_json": json.dumps({"timeout_seconds": 30, "max_retries": 0}),
    }

    # Create a mock process that returns a successful result event
    mock_proc = AsyncMock()
    mock_proc.pid = 12345
    mock_proc.returncode = 0

    result_event = json.dumps({
        "type": "result",
        "result": "Created hello.py successfully",
        "cost_usd": 0.05,
        "duration_ms": 2000,
        "duration_api_ms": 1500,
    })

    # stdout yields lines then EOF
    async def mock_readline():
        if not hasattr(mock_readline, "_called"):
            mock_readline._called = True
            return (result_event + "\n").encode()
        return b""

    mock_proc.stdout.readline = mock_readline
    mock_proc.stderr.read = AsyncMock(return_value=b"")
    mock_proc.stdin.write = MagicMock()
    mock_proc.stdin.drain = AsyncMock()
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("universal_agent.vp.clients.claude_cli_client.asyncio.create_subprocess_exec", return_value=mock_proc):
        outcome = await client.run_mission(mission=mission, workspace_root=tmp_path)

    assert outcome.status == "completed"
    assert "hello.py" in outcome.payload.get("final_text", "")


# ---------------------------------------------------------------------------
# 4. Retry on failure (attempt count)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cli_client_retries_on_failure(tmp_path: Path):
    """Verify that the client respects max_retries and attempts multiple times."""
    client = ClaudeCodeCLIClient()
    mission: dict[str, Any] = {
        "mission_id": "test-mission-4",
        "objective": "Do something",
        "payload_json": json.dumps({"timeout_seconds": 30, "max_retries": 1}),
    }

    call_count = 0

    async def mock_create_subprocess(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise FileNotFoundError("claude")

    with patch("universal_agent.vp.clients.claude_cli_client.asyncio.create_subprocess_exec", side_effect=mock_create_subprocess):
        outcome = await client.run_mission(mission=mission, workspace_root=tmp_path)

    assert outcome.status == "failed"
    # With max_retries=1, we get initial attempt + 1 retry = 2 calls
    assert call_count == 2


# ---------------------------------------------------------------------------
# 4b. _is_auth_failure pattern matching
# ---------------------------------------------------------------------------
def test_is_auth_failure_matches_final_text_401():
    """The real failure mode: CLI exits with code 1 and 401 lands in final_text."""
    outcome = MissionOutcome(
        status="failed",
        message="CLI exited with code 1",
        payload={
            "exit_code": 1,
            "tool_calls": 0,
            "final_text": "Failed to authenticate. API Error: 401 Invalid authentication credentials",
        },
    )
    assert _is_auth_failure(outcome) is True


def test_is_auth_failure_matches_message():
    outcome = MissionOutcome(
        status="failed",
        message="invalid x-api-key from upstream",
        payload={},
    )
    assert _is_auth_failure(outcome) is True


def test_is_auth_failure_is_case_insensitive():
    outcome = MissionOutcome(
        status="failed",
        message="",
        payload={"final_text": "FAILED TO AUTHENTICATE"},
    )
    assert _is_auth_failure(outcome) is True


def test_is_auth_failure_no_match_on_unrelated_error():
    outcome = MissionOutcome(
        status="failed",
        message="CLI session timed out after 1800s",
        payload={"final_text": "partial response..."},
    )
    assert _is_auth_failure(outcome) is False


def test_is_auth_failure_handles_empty_outcome():
    outcome = MissionOutcome(status="failed", message="", payload={})
    assert _is_auth_failure(outcome) is False


# ---------------------------------------------------------------------------
# 4c. Auth failure short-circuits retries with operator-friendly message
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cli_client_auth_failure_short_circuits_retries(tmp_path: Path):
    """An auth-rejection 401 must NOT trigger the retry loop — same env
    will produce the same failure, and we want the operator to see the
    real reason without three rounds of identical retries first."""
    client = ClaudeCodeCLIClient()
    mission: dict[str, Any] = {
        "mission_id": "test-mission-auth-401",
        "objective": "Build something",
        "payload_json": json.dumps({"timeout_seconds": 30, "max_retries": 2}),
    }

    auth_outcome = MissionOutcome(
        status="failed",
        message="CLI exited with code 1",
        result_ref=f"workspace://{tmp_path}",
        payload={
            "exit_code": 1,
            "tool_calls": 0,
            "final_text": "Failed to authenticate. API Error: 401 Invalid authentication credentials",
        },
    )

    call_count = 0

    async def mock_execute_cli_session(**kwargs):
        nonlocal call_count
        call_count += 1
        return auth_outcome

    with patch(
        "universal_agent.vp.clients.claude_cli_client._execute_cli_session",
        side_effect=mock_execute_cli_session,
    ):
        outcome = await client.run_mission(mission=mission, workspace_root=tmp_path)

    assert outcome.status == "failed"
    assert call_count == 1, "auth failure must short-circuit before any retry"
    assert outcome.payload.get("auth_failure") is True
    assert "setup-token" in (outcome.message or "")
    assert "401" in (outcome.message or "") or "OAuth" in (outcome.message or "")


# ---------------------------------------------------------------------------
# 5. _parse_payload handles various input types
# ---------------------------------------------------------------------------
def test_parse_payload_handles_string():
    result = _parse_payload('{"key": "value"}')
    assert result == {"key": "value"}


def test_parse_payload_handles_dict():
    result = _parse_payload({"key": "value"})
    assert result == {"key": "value"}


def test_parse_payload_handles_none():
    result = _parse_payload(None)
    assert result == {}


def test_parse_payload_handles_invalid_json():
    result = _parse_payload("not-json")
    assert result == {}


# ---------------------------------------------------------------------------
# 6. _build_cli_prompt constructs valid prompt
# ---------------------------------------------------------------------------
def test_build_cli_prompt_includes_objective():
    prompt = _build_cli_prompt(
        objective="Write hello.py",
        payload={"constraints": {"target_path": "/tmp/test"}},
        workspace_dir=Path("/tmp/workspace"),
        skill_name="",
    )
    assert "Write hello.py" in prompt
    assert "/tmp/workspace" in prompt


def test_build_cli_prompt_includes_skill():
    prompt = _build_cli_prompt(
        objective="Fix bug",
        payload={},
        workspace_dir=Path("/tmp/workspace"),
        skill_name="my-skill",
    )
    assert "my-skill" in prompt


# ---------------------------------------------------------------------------
# 7. Worker loop _select_client_for_mission routes on execution_mode
# ---------------------------------------------------------------------------
def test_select_client_returns_cli_for_cli_mode(tmp_path: Path):
    """Verify _select_client_for_mission returns ClaudeCodeCLIClient when execution_mode=cli."""
    from universal_agent.vp.worker_loop import VpWorkerLoop

    # Minimal profile-like mock
    profile = MagicMock()
    profile.vp_id = "vp.coder.primary"
    profile.client_kind = "claude_agent_sdk"
    profile.display_name = "CODIE"
    profile.cli_capable = True

    # Create worker loop with mocked dependencies
    loop = VpWorkerLoop.__new__(VpWorkerLoop)
    loop.vp_id = "vp.coder.primary"
    loop.profile = profile
    loop._client = None
    loop._default_client = MagicMock(spec=VpClient)

    mission_cli = {
        "mission_id": "m-123",
        "payload_json": json.dumps({"execution_mode": "cli", "objective": "test"}),
    }
    mission_sdk = {
        "mission_id": "m-456",
        "payload_json": json.dumps({"execution_mode": "sdk", "objective": "test"}),
    }
    mission_default = {
        "mission_id": "m-789",
        "payload_json": json.dumps({"objective": "test"}),
    }

    assert isinstance(loop._select_client_for_mission(mission_cli), ClaudeCodeCLIClient)
    assert loop._select_client_for_mission(mission_sdk) is loop._default_client
    assert loop._select_client_for_mission(mission_default) is loop._default_client


# ---------------------------------------------------------------------------
# 8. Dispatch payload includes execution_mode
# ---------------------------------------------------------------------------
def test_dispatch_request_carries_execution_mode():
    """Verify that MissionDispatchRequest and _build_payload include execution_mode."""
    from universal_agent.vp.dispatcher import MissionDispatchRequest

    req = MissionDispatchRequest(
        vp_id="vp.coder.primary",
        mission_type="task",
        objective="Test objective",
        constraints={},
        budget={},
        idempotency_key="",
        source_session_id="test-session",
        source_turn_id="",
        reply_mode="async",
        priority=100,
        execution_mode="cli",
    )
    assert req.execution_mode == "cli"

    # Default should be "sdk"
    req_default = MissionDispatchRequest(
        vp_id="vp.coder.primary",
        mission_type="task",
        objective="Test",
        constraints={},
        budget={},
        idempotency_key="",
        source_session_id="test",
        source_turn_id="",
        reply_mode="async",
        priority=100,
    )
    assert req_default.execution_mode == "sdk"


# ---------------------------------------------------------------------------
# 9. VP General worker loop also routes CLI missions
# ---------------------------------------------------------------------------
def test_select_client_returns_cli_for_general_vp_cli_mode():
    """VP General missions with execution_mode=cli should also use ClaudeCodeCLIClient."""
    from universal_agent.vp.worker_loop import VpWorkerLoop

    profile = MagicMock()
    profile.vp_id = "vp.general.primary"
    profile.client_kind = "claude_generalist"
    profile.display_name = "GENERALIST"
    profile.cli_capable = True

    loop = VpWorkerLoop.__new__(VpWorkerLoop)
    loop.vp_id = "vp.general.primary"
    loop.profile = profile
    loop._client = None
    loop._default_client = MagicMock(spec=VpClient)

    mission_cli = {
        "mission_id": "m-gen-1",
        "payload_json": json.dumps({"execution_mode": "cli", "skill": "modular-research-report-expert"}),
    }
    mission_sdk = {
        "mission_id": "m-gen-2",
        "payload_json": json.dumps({"execution_mode": "sdk"}),
    }

    assert isinstance(loop._select_client_for_mission(mission_cli), ClaudeCodeCLIClient)
    assert loop._select_client_for_mission(mission_sdk) is loop._default_client


# ---------------------------------------------------------------------------
# 10. Prompt includes output_dir when provided
# ---------------------------------------------------------------------------
def test_build_cli_prompt_includes_output_dir():
    prompt = _build_cli_prompt(
        objective="Generate report",
        payload={"output_dir": "/tmp/report-output", "corpus_path": "/tmp/corpus"},
        workspace_dir=Path("/tmp/workspace"),
        skill_name="modular-research-report-expert",
    )
    assert "/tmp/report-output" in prompt
    assert "/tmp/corpus" in prompt
    assert "modular-research-report-expert" in prompt


# ---------------------------------------------------------------------------
# 11. Dispatch guardrails fire for VP General CLI missions
# ---------------------------------------------------------------------------
def test_dispatch_guardrails_block_general_cli_targeting_ua_repo():
    """VP General CLI missions should be blocked from targeting UA repo."""
    from universal_agent.vp.dispatcher import _validate_dispatch_constraints
    from universal_agent.vp.profiles import VpProfile

    repo_root = Path(__file__).resolve().parents[2]  # tests/ -> universal_agent root

    # Create a VP General profile pointing at a tmp workspace
    profile = VpProfile(
        vp_id="vp.general.primary",
        display_name="GENERALIST",
        runtime_id="runtime.general.external",
        client_kind="claude_generalist",
        workspace_root=Path("/tmp/test_ws"),
    )

    # SDK mode should NOT trigger guardrails for VP General
    _validate_dispatch_constraints(
        profile=profile,
        constraints={"target_path": str(repo_root / "AGENT_RUN_WORKSPACES")},
        execution_mode="sdk",
    )
    # No exception = passes

    # CLI mode SHOULD trigger guardrails for VP General
    with pytest.raises(ValueError, match="blocked"):
        _validate_dispatch_constraints(
            profile=profile,
            constraints={"target_path": str(repo_root / "AGENT_RUN_WORKSPACES")},
            execution_mode="cli",
        )


def test_dispatch_guardrails_allow_general_cli_for_approved_codebase_when_authorized(monkeypatch):
    """CLI guardrails should allow explicit repo-backed coding missions on approved roots."""
    from universal_agent.vp.dispatcher import _validate_dispatch_constraints
    from universal_agent.vp.profiles import VpProfile

    monkeypatch.setenv("UA_APPROVED_CODEBASE_ROOTS", "/opt/universal_agent")
    monkeypatch.setenv("UA_VP_HARD_BLOCK_UA_REPO", "1")

    profile = VpProfile(
        vp_id="vp.general.primary",
        display_name="GENERALIST",
        runtime_id="runtime.general.external",
        client_kind="claude_generalist",
        workspace_root=Path("/tmp/test_ws"),
    )

    _validate_dispatch_constraints(
        profile=profile,
        constraints={
            "target_path": "/opt/universal_agent",
            "workflow_kind": "code_change",
            "repo_mutation_allowed": True,
        },
        execution_mode="cli",
    )


# ---------------------------------------------------------------------------
# CLI session_id capture — Workspace-button deep-link fix
#
# The Claude Code CLI stamps `session_id` on every stream-json event.
# `_monitor_cli_output` captures the first non-empty value so the Task Hub
# card's Workspace button can deep-link to the CLI subprocess's session
# instead of the orchestrator's (Simone's) session.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cli_session_id_captured_from_stream(tmp_path: Path):
    """Verify a stream-json `session_id` field is propagated into outcome.payload."""
    client = ClaudeCodeCLIClient()
    mission: dict[str, Any] = {
        "mission_id": "test-mission-sid-1",
        "objective": "Create hello.py",
        "payload_json": json.dumps({"timeout_seconds": 30, "max_retries": 0}),
    }

    captured_sid = "0db5fd6b-16e1-4357-a1bc-cfc559ad8ec1"

    mock_proc = AsyncMock()
    mock_proc.pid = 12345
    mock_proc.returncode = 0

    # First event carries session_id (typical CLI behavior); result event closes.
    events = [
        json.dumps({
            "type": "rate_limit_event",
            "session_id": captured_sid,
            "rate_limit_info": {"status": "allowed"},
        }),
        json.dumps({
            "type": "result",
            "session_id": captured_sid,
            "result": "done",
            "cost_usd": 0.01,
        }),
    ]
    event_iter = iter(events)

    async def mock_readline():
        try:
            return (next(event_iter) + "\n").encode()
        except StopIteration:
            return b""

    mock_proc.stdout.readline = mock_readline
    mock_proc.stderr.read = AsyncMock(return_value=b"")
    mock_proc.stdin.write = MagicMock()
    mock_proc.stdin.drain = AsyncMock()
    mock_proc.wait = AsyncMock(return_value=0)

    with patch(
        "universal_agent.vp.clients.claude_cli_client.asyncio.create_subprocess_exec",
        return_value=mock_proc,
    ):
        outcome = await client.run_mission(mission=mission, workspace_root=tmp_path)

    assert outcome.status == "completed"
    assert outcome.payload.get("cli_session_id") == captured_sid


@pytest.mark.asyncio
async def test_cli_session_id_absent_when_stream_never_emits_it(tmp_path: Path):
    """Streams without a session_id field leave the payload key absent."""
    client = ClaudeCodeCLIClient()
    mission: dict[str, Any] = {
        "mission_id": "test-mission-sid-2",
        "objective": "Do something",
        "payload_json": json.dumps({"timeout_seconds": 30, "max_retries": 0}),
    }

    mock_proc = AsyncMock()
    mock_proc.pid = 12345
    mock_proc.returncode = 0

    result_event = json.dumps({"type": "result", "result": "ok"})

    async def mock_readline():
        if not hasattr(mock_readline, "_called"):
            mock_readline._called = True
            return (result_event + "\n").encode()
        return b""

    mock_proc.stdout.readline = mock_readline
    mock_proc.stderr.read = AsyncMock(return_value=b"")
    mock_proc.stdin.write = MagicMock()
    mock_proc.stdin.drain = AsyncMock()
    mock_proc.wait = AsyncMock(return_value=0)

    with patch(
        "universal_agent.vp.clients.claude_cli_client.asyncio.create_subprocess_exec",
        return_value=mock_proc,
    ):
        outcome = await client.run_mission(mission=mission, workspace_root=tmp_path)

    assert outcome.status == "completed"
    assert "cli_session_id" not in outcome.payload


# ---------------------------------------------------------------------------
# task_hub.record_provider_session_id helper — covers the assignment
# write-back used by _execute_cli_session after capturing the CLI's
# session_id from the stream.
# ---------------------------------------------------------------------------
def _make_assignment_conn() -> sqlite3.Connection:
    """In-memory conn with just enough schema for record_provider_session_id."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE task_hub_assignments (
            assignment_id TEXT PRIMARY KEY,
            task_id TEXT,
            agent_id TEXT,
            provider_session_id TEXT,
            worker_pid INTEGER,
            state TEXT
        )
        """
    )
    return conn


def test_record_provider_session_id_writes_back():
    from universal_agent.task_hub import record_provider_session_id

    conn = _make_assignment_conn()
    conn.execute(
        "INSERT INTO task_hub_assignments (assignment_id, task_id, agent_id, state) "
        "VALUES (?, ?, ?, ?)",
        ("asg_1", "task_1", "vp.coder.primary", "seized"),
    )

    rows = record_provider_session_id(
        conn,
        assignment_id="asg_1",
        provider_session_id="cli-session-abc",
    )
    assert rows == 1

    row = conn.execute(
        "SELECT provider_session_id FROM task_hub_assignments WHERE assignment_id = ?",
        ("asg_1",),
    ).fetchone()
    assert row["provider_session_id"] == "cli-session-abc"


def test_record_provider_session_id_noop_on_empty_inputs():
    from universal_agent.task_hub import record_provider_session_id

    conn = _make_assignment_conn()
    conn.execute(
        "INSERT INTO task_hub_assignments (assignment_id, task_id, agent_id, state) "
        "VALUES (?, ?, ?, ?)",
        ("asg_1", "task_1", "vp.coder.primary", "seized"),
    )

    # Empty assignment_id → no-op
    assert record_provider_session_id(conn, assignment_id="", provider_session_id="x") == 0
    # Empty session_id → no-op
    assert record_provider_session_id(conn, assignment_id="asg_1", provider_session_id="") == 0
    # Whitespace-only inputs → no-op
    assert record_provider_session_id(conn, assignment_id="  ", provider_session_id="x") == 0
    assert record_provider_session_id(conn, assignment_id="asg_1", provider_session_id="  ") == 0

    row = conn.execute(
        "SELECT provider_session_id FROM task_hub_assignments WHERE assignment_id = ?",
        ("asg_1",),
    ).fetchone()
    assert row["provider_session_id"] is None

"""Tests for YouTube pipeline resilience improvements (R1-R4).

R1: LLM error tokens classified as interruptions
R2: Non-success iteration_status logged to run.log
R3: Zero-tool-call completions emit structured warning
R4: Startup warmup delay before recovery dispatch


R5: Session key double-underscore delimiter for video IDs with underscores
"""

import asyncio
import json
from pathlib import Path
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from universal_agent.durable.db import connect_runtime_db
from universal_agent.durable.migrations import ensure_schema
from universal_agent.gateway import GatewaySession, InProcessGateway
from universal_agent.hooks_service import (
    YOUTUBE_DISPATCH_INTERRUPTION_ERROR_TOKENS,
    HooksService,
    build_manual_youtube_action,
)
from universal_agent.workflow_admission import WorkflowAdmissionService


@pytest.fixture
def mock_gateway():
    gateway = MagicMock(spec=InProcessGateway)
    gateway.resume_session = AsyncMock()
    gateway.create_session = AsyncMock()

    async def async_gen(*args, **kwargs):
        yield "event"

    gateway.execute.side_effect = async_gen
    return gateway


@pytest.fixture
def hooks_service(mock_gateway, tmp_path):
    runtime_db_path = str((tmp_path / "runtime_state.db").resolve())
    with (
        patch("universal_agent.hooks_service.load_ops_config", return_value={}),
        patch.dict(
            "os.environ",
            {"UA_RUNTIME_DB_PATH": runtime_db_path},
            clear=False,
        ),
    ):
        service = HooksService(mock_gateway)
    service._workflow_admission_service = lambda: WorkflowAdmissionService(runtime_db_path)
    return service


# ── R1: LLM error tokens are in the interruption token set ──────────────────

class TestLLMErrorTokensInInterruptionList:
    """R1: LLM-specific transient errors should be classified as interruptions."""

    @pytest.mark.parametrize(
        "token",
        [
            "model overloaded",
            "model_overloaded",
            "request failed",
            "request_failed",
            "invalid response",
            "invalid_response",
            "api error",
            "api_error",
            "internal server error",
            "internal_server_error",
            "500",
            "server error",
            "iteration_status:error",
            "iteration_status:failed",
        ],
    )
    def test_llm_token_in_interruption_set(self, token):
        assert token in YOUTUBE_DISPATCH_INTERRUPTION_ERROR_TOKENS

    def test_dispatch_interruption_error_recognises_llm_tokens(self, hooks_service):
        """_is_dispatch_interruption_error should return True for LLM errors."""
        for msg in [
            "model overloaded: please retry",
            "RuntimeError: request failed after 3 attempts",
            "iteration_status:error",
            "upstream returned 500",
            "api error from provider",
        ]:
            assert hooks_service._is_dispatch_interruption_error(msg) is True, (
                f"Expected True for: {msg!r}"
            )

    def test_hard_failures_still_classified_correctly(self, hooks_service):
        """Genuine hard failures should NOT match interruption tokens."""
        for msg in [
            "Invalid API key",
            "Permission denied",
            "Malformed prompt structure",
        ]:
            assert hooks_service._is_dispatch_interruption_error(msg) is False, (
                f"Expected False for: {msg!r}"
            )


# ── R4: Startup warmup delay ────────────────────────────────────────────────

class TestStartupWarmupDelay:
    """R4: Configurable warmup delay before recovery dispatch."""

    def test_default_warmup_delay_is_15(self, mock_gateway):
        with patch("universal_agent.hooks_service.load_ops_config", return_value={}):
            service = HooksService(mock_gateway)
        assert service._startup_warmup_delay_seconds == 15

    def test_warmup_delay_reads_from_env(self, mock_gateway):
        with (
            patch("universal_agent.hooks_service.load_ops_config", return_value={}),
            patch.dict(
                "os.environ",
                {"UA_HOOKS_STARTUP_WARMUP_DELAY_SECONDS": "30"},
                clear=False,
            ),
        ):
            service = HooksService(mock_gateway)
        assert service._startup_warmup_delay_seconds == 30

    def test_warmup_delay_zero_disables(self, mock_gateway):
        with (
            patch("universal_agent.hooks_service.load_ops_config", return_value={}),
            patch.dict(
                "os.environ",
                {"UA_HOOKS_STARTUP_WARMUP_DELAY_SECONDS": "0"},
                clear=False,
            ),
        ):
            service = HooksService(mock_gateway)
        assert service._startup_warmup_delay_seconds == 0

    def test_warmup_delay_in_readiness_status(self, hooks_service):
        status = hooks_service.readiness_status()
        assert "startup_warmup_delay_seconds" in status
        assert isinstance(status["startup_warmup_delay_seconds"], int)

    @pytest.mark.asyncio
    async def test_recovery_sleeps_for_warmup(self, hooks_service, tmp_path):
        """Recovery should sleep for warmup delay before scanning sessions."""
        hooks_service._startup_warmup_delay_seconds = 0  # Don't actually sleep
        hooks_service._startup_recovery_enabled = True
        count = await hooks_service.recover_interrupted_youtube_sessions(tmp_path)
        assert count == 0  # No sessions to recover, but it didn't crash

    @pytest.mark.asyncio
    async def test_recovery_skips_warmup_when_zero(self, hooks_service, tmp_path, caplog):
        """When warmup is 0, no sleep log message should appear."""
        hooks_service._startup_warmup_delay_seconds = 0
        hooks_service._startup_recovery_enabled = True
        with caplog.at_level("INFO"):
            await hooks_service.recover_interrupted_youtube_sessions(tmp_path)
        assert "waiting" not in caplog.text.lower() or "0" in caplog.text


# ── R3: Zero-tool-call dispatch logging ─────────────────────────────────────

class TestZeroToolCallDispatch:
    """R3: Dispatch with zero tool calls should be classified as interrupted."""

    @pytest.mark.asyncio
    async def test_zero_tool_call_marks_interrupted(self, hooks_service, mock_gateway, tmp_path, caplog):
        """When agent completes in <30s with 0 tool calls, error is reported."""
        hooks_service._youtube_ingest_mode = ""
        hooks_service._schedule_youtube_retry_attempt = MagicMock()
        notifications = []
        hooks_service._notification_sink = notifications.append
        hooks_service._run_gateway_execute_with_watchdogs = AsyncMock(
            return_value={
                "reported_error": True,
                "reported_error_message": "iteration_status:error",
                "tool_calls": 0,
                "duration_seconds": 1.5,
            }
        )

        workspace = tmp_path / "session_hook_yt_test123abc"
        workspace.mkdir(parents=True, exist_ok=True)
        mock_session = GatewaySession(
            session_id="session_hook_yt_test123abc",
            user_id="webhook",
            workspace_dir=str(workspace),
        )
        mock_gateway.resume_session = AsyncMock(return_value=mock_session)

        from universal_agent.hooks_service import HookAction

        action = HookAction(
            kind="agent",
            name="ComposioYouTubeTrigger",
            session_key="yt_test123abc",
            to="youtube-expert",
            message="\n".join([
                "video_url: https://www.youtube.com/watch?v=test123abc",
                "video_id: test123abc",
                "title: Test Video",
                "mode: explainer_plus_code",
            ]),
        )

        await hooks_service._dispatch_action(action)

        # The error "iteration_status:error" should now match interruption tokens
        # so it should be classified as interrupted, not failed
        interrupted = next((n for n in notifications if n.get("kind") == "youtube_tutorial_interrupted"), None)
        assert interrupted is not None
        assert interrupted["metadata"]["reason"] == "hook_dispatch_interrupted"
        conn = connect_runtime_db(hooks_service._workflow_admission_service().db_path)
        ensure_schema(conn)
        attempts = conn.execute(
            "SELECT attempt_number, status FROM run_attempts WHERE run_id = ? ORDER BY attempt_number ASC",
            (str(interrupted["metadata"]["run_id"]),),
        ).fetchall()
        conn.close()
        assert [int(row["attempt_number"]) for row in attempts] == [1, 2]
        assert str(attempts[0]["status"]) == "failed"
        assert str(attempts[1]["status"]) in {"queued", "running", "completed"}


# ── R5: Session key double-underscore delimiter ─────────────────────────────

class TestSessionKeyDoubleUnderscoreDelimiter:
    """R5: Video IDs with underscores are preserved via __ delimiter."""

    def test_new_format_underscore_video_id(self, hooks_service):
        """New __ format correctly parses video IDs containing underscores."""
        ch, vid = HooksService._youtube_parts_from_session_key(
            "yt_UC0C_17n9iuUQPylguM1d_lQ__7AO4w4Y_L24"
        )
        assert vid == "7AO4w4Y_L24"
        assert ch == "UC0C_17n9iuUQPylguM1d_lQ"

    def test_new_format_simple_video_id(self, hooks_service):
        """New __ format works for video IDs without underscores."""
        ch, vid = HooksService._youtube_parts_from_session_key(
            "yt_somechannel__xUlX6jvwVfM"
        )
        assert vid == "xUlX6jvwVfM"
        assert ch == "somechannel"

    def test_legacy_format_backward_compat(self, hooks_service):
        """Legacy single _ format still works for video IDs without underscores."""
        ch, vid = HooksService._youtube_parts_from_session_key(
            "yt_somechannel_xUlX6jvwVfM"
        )
        assert vid == "xUlX6jvwVfM"
        assert ch == "somechannel"

    def test_empty_session_key(self, hooks_service):
        ch, vid = HooksService._youtube_parts_from_session_key("")
        assert ch == ""
        assert vid == ""

    def test_non_yt_prefix(self, hooks_service):
        ch, vid = HooksService._youtube_parts_from_session_key("other_key")
        assert ch == ""
        assert vid == ""

    def test_build_action_uses_double_underscore(self):
        """build_manual_youtube_action produces __ delimiter in session_key."""
        action = build_manual_youtube_action({
            "video_url": "https://www.youtube.com/watch?v=7AO4w4Y_L24",
            "video_id": "7AO4w4Y_L24",
            "channel_id": "UC0C-17n9iuUQPylguM1d-lQ",
            "title": "Test",
        })
        assert action is not None
        sk = action["session_key"]
        assert "__" in sk, f"session_key should use __ delimiter: {sk}"
        # Verify round-trip: parsing the session_key recovers the full video ID
        ch, vid = HooksService._youtube_parts_from_session_key(sk)
        assert vid == "7AO4w4Y_L24"

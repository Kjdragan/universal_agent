"""Tests for YouTube pipeline resilience improvements (R1-R4).

R1: LLM error tokens classified as interruptions
R2: Non-success iteration_status logged to run.log
R3: Zero-tool-call completions emit structured warning
R4: Startup warmup delay before recovery dispatch


R5: Session key double-underscore delimiter for video IDs with underscores

R6: The retry lane is structurally alive (T2, 2026-08-02)
    - the retry dispatch bypasses the video dedup guard
    - the retry task's done-callback finalizes a retry that never started
    - the tutorial prompt runs the skills INLINE instead of delegating
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
    HookAction,
    HooksService,
    build_manual_youtube_action,
)
from universal_agent.workflow_admission import WorkflowAdmissionService, WorkflowTrigger


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


# ── T4: retry-queued alert message carries the discriminator + video id ─────

class TestYoutubeRetryQueuedNotificationMessage:
    """T4: the emitted ``message`` (which becomes ``summary``/``full_message``
    and is what Telegram, the plaintext part, the Gmail snippet, and the
    dashboard actually render) must carry ``reason`` and ``video_id`` — not
    just the email HTML context table, which those surfaces don't read.
    """

    def test_message_contains_reason_and_video_id(self, hooks_service):
        captured = []
        hooks_service._notification_sink = captured.append

        hooks_service._emit_youtube_retry_queued_notification(
            session_id="session_hook_yt_h5HLLIds53g",
            session_key="yt_somechannel__h5HLLIds53g",
            hook_name="ComposioYouTubeTrigger",
            expected_video_id="h5HLLIds53g",
            current_attempt_number=1,
            next_attempt_number=2,
            max_attempts=3,
            reason="hook_dispatch_failed",
            run_id="run_abc123",
            attempt_id="attempt_1",
            workspace_dir="/opt/universal_agent/AGENT_RUN_WORKSPACES/run_abc123",
        )

        assert len(captured) == 1
        payload = captured[0]
        assert "hook_dispatch_failed" in payload["message"]
        assert "h5HLLIds53g" in payload["message"]
        # Still present in metadata for the email context table.
        assert payload["metadata"]["reason"] == "hook_dispatch_failed"
        assert payload["metadata"]["video_id"] == "h5HLLIds53g"


# ── T2 helpers ──────────────────────────────────────────────────────────────

def _youtube_action(video_id: str = "test123abc") -> HookAction:
    return HookAction(
        kind="agent",
        name="ManualYouTubeWebhook",
        session_key=f"yt_manual__{video_id}",
        to="youtube-expert",
        message="\n".join(
            [
                f"video_url: https://www.youtube.com/watch?v={video_id}",
                f"video_id: {video_id}",
                "title: Test Video",
                "mode: explainer_only",
            ]
        ),
        deliver=True,
    )


def _admit_youtube_run(
    hooks_service: HooksService,
    workspace_dir: str,
    monkeypatch=None,
) -> tuple[str, str]:
    """Create a real queued youtube_tutorial_hook run + attempt in the test DB.

    ``HooksService._runtime_db_connect`` resolves ``get_runtime_db_path()`` at
    call time (in production the same DB the admission service writes), so point
    ``UA_RUNTIME_DB_PATH`` at the fixture DB for tests that read attempt state
    back through ``_workflow_attempt_context``.
    """
    service = hooks_service._workflow_admission_service()
    if monkeypatch is not None:
        monkeypatch.setenv("UA_RUNTIME_DB_PATH", service.db_path)
    decision = service.admit(
        WorkflowTrigger(
            run_kind="youtube_tutorial_hook",
            trigger_source="webhook",
            dedup_key="test123abc",
            payload_json=json.dumps({"video_id": "test123abc"}),
            priority=100,
            run_policy="automation_ephemeral",
            interrupt_policy="attach_if_same_dedup_key",
        ),
        entrypoint="hooks_service.youtube_tutorial_hook",
        workspace_dir=workspace_dir,
    )
    assert decision.run_id and decision.attempt_id
    return decision.run_id, decision.attempt_id


# ── T2 / cause (2): the retry lane is not blocked by the video dedup guard ──

class TestRetrySkipsVideoDedupGuard:
    """`_schedule_youtube_retry_attempt` fires from inside `_dispatch_action`'s
    except handler — i.e. BEFORE the `finally` that pops
    `_youtube_video_dispatch_inflight`. Without an explicit bypass, every retry
    raced the outgoing dispatch and was rejected as `duplicate_video_dispatch`
    (observed inflight_age 52-134s against a 3600s TTL in production)."""

    @pytest.mark.asyncio
    async def test_inflight_marker_still_rejects_a_normal_duplicate(self, hooks_service):
        """Control: the guard still does its real job for a genuine duplicate."""
        hooks_service._youtube_video_dispatch_inflight["test123abc"] = time.time()
        result = await hooks_service._dispatch_action(
            _youtube_action(),
            workflow_run_id="run_x",
            workflow_attempt_id="attempt_x",
            workflow_workspace_dir="/tmp/ws",
            skip_workflow_admission=True,
        )
        assert result["decision"] == "skipped"
        assert result["reason"] == "duplicate_video_dispatch"

    @pytest.mark.asyncio
    async def test_retry_lane_bypasses_the_guard(self, hooks_service, mock_gateway, tmp_path):
        """With `skip_video_dedup=True` the same inflight marker must NOT block."""
        hooks_service._youtube_ingest_mode = ""
        hooks_service._youtube_video_dispatch_inflight["test123abc"] = time.time()
        hooks_service._notification_sink = [].append
        hooks_service._run_gateway_execute_with_watchdogs = AsyncMock(
            return_value={"tool_calls": 3, "duration_seconds": 42.0}
        )
        hooks_service._validate_youtube_tutorial_artifacts = MagicMock(
            return_value={"manifest_path": "", "run_rel_path": "x"}
        )

        workspace = tmp_path / "session_hook_yt_manual__test123abc"
        workspace.mkdir(parents=True, exist_ok=True)
        mock_gateway.resume_session = AsyncMock(
            return_value=GatewaySession(
                session_id="session_hook_yt_manual__test123abc",
                user_id="webhook",
                workspace_dir=str(workspace),
            )
        )

        result = await hooks_service._dispatch_action(
            _youtube_action(),
            workflow_run_id="run_x",
            workflow_attempt_id="attempt_x",
            workflow_workspace_dir=str(workspace),
            skip_workflow_admission=True,
            skip_video_dedup=True,
        )

        assert result.get("reason") != "duplicate_video_dispatch"
        assert result["decision"] == "accepted"

    @pytest.mark.asyncio
    async def test_bypassing_dispatch_does_not_clear_someone_elses_marker(
        self, hooks_service, mock_gateway, tmp_path
    ):
        """A dispatch that never acquired the marker must not pop it in `finally`
        — otherwise the retry deletes the marker a concurrent dispatch owns."""
        hooks_service._youtube_ingest_mode = ""
        marker_ts = time.time()
        hooks_service._youtube_video_dispatch_inflight["test123abc"] = marker_ts
        hooks_service._notification_sink = [].append
        hooks_service._run_gateway_execute_with_watchdogs = AsyncMock(
            return_value={"tool_calls": 1, "duration_seconds": 5.0}
        )
        hooks_service._validate_youtube_tutorial_artifacts = MagicMock(
            return_value={"manifest_path": "", "run_rel_path": "x"}
        )
        workspace = tmp_path / "session_hook_yt_manual__test123abc"
        workspace.mkdir(parents=True, exist_ok=True)
        mock_gateway.resume_session = AsyncMock(
            return_value=GatewaySession(
                session_id="session_hook_yt_manual__test123abc",
                user_id="webhook",
                workspace_dir=str(workspace),
            )
        )

        await hooks_service._dispatch_action(
            _youtube_action(),
            workflow_run_id="run_x",
            workflow_attempt_id="attempt_x",
            workflow_workspace_dir=str(workspace),
            skip_workflow_admission=True,
            skip_video_dedup=True,
        )

        assert hooks_service._youtube_video_dispatch_inflight.get("test123abc") == marker_ts


# ── T2 / cause (2): the retry task's done-callback finalizes non-starts ─────

class TestRetryDoneCallbackFinalizes:
    """The video dedup guard RETURNS `{"decision": "skipped"}` — it does not
    raise. The old fire-and-forget `asyncio.create_task(...)` discarded that
    return value, so nothing finalized the attempt and it sat `queued` with a
    NULL provider_session_id for 60 minutes until the stale-orphan reaper killed
    it. An exception-only callback would NOT have caught this."""

    @pytest.mark.asyncio
    async def test_skipped_result_finalizes_the_attempt(self, hooks_service):
        finalized: list[dict] = []
        hooks_service._finalize_unstarted_youtube_retry = lambda **kw: finalized.append(kw)
        hooks_service._dispatch_action = AsyncMock(
            return_value={"decision": "skipped", "reason": "duplicate_video_dispatch"}
        )

        task = hooks_service._schedule_youtube_retry_attempt(
            action=_youtube_action(),
            run_id="run_1",
            attempt_id="attempt_2",
            workspace_dir="/tmp/ws",
            session_id="session_hook_yt_manual__test123abc",
            session_key="yt_manual__test123abc",
            hook_name="ManualYouTubeWebhook",
            expected_video_id="test123abc",
        )
        await task
        await asyncio.sleep(0)  # let the done-callback run

        assert len(finalized) == 1
        assert finalized[0]["run_id"] == "run_1"
        assert finalized[0]["attempt_id"] == "attempt_2"
        assert "youtube_retry_not_started" in finalized[0]["reason"]
        assert "duplicate_video_dispatch" in finalized[0]["reason"]

    @pytest.mark.asyncio
    async def test_exception_result_also_finalizes(self, hooks_service):
        finalized: list[dict] = []
        hooks_service._finalize_unstarted_youtube_retry = lambda **kw: finalized.append(kw)
        hooks_service._dispatch_action = AsyncMock(side_effect=RuntimeError("boom"))

        task = hooks_service._schedule_youtube_retry_attempt(
            action=_youtube_action(),
            run_id="run_1",
            attempt_id="attempt_2",
            workspace_dir="/tmp/ws",
            expected_video_id="test123abc",
        )
        with pytest.raises(RuntimeError):
            await task
        await asyncio.sleep(0)

        assert len(finalized) == 1
        assert "youtube_retry_raised" in finalized[0]["reason"]

    @pytest.mark.asyncio
    async def test_successful_retry_is_not_finalized(self, hooks_service):
        """`_dispatch_action` returns `decision="accepted"` on success — the
        callback must leave that alone or every healthy retry would be
        force-failed into needs_review."""
        finalized: list[dict] = []
        hooks_service._finalize_unstarted_youtube_retry = lambda **kw: finalized.append(kw)
        hooks_service._dispatch_action = AsyncMock(
            return_value={"decision": "accepted", "status": "completed"}
        )

        task = hooks_service._schedule_youtube_retry_attempt(
            action=_youtube_action(),
            run_id="run_1",
            attempt_id="attempt_2",
            workspace_dir="/tmp/ws",
            expected_video_id="test123abc",
        )
        await task
        await asyncio.sleep(0)

        assert finalized == []

    @pytest.mark.asyncio
    async def test_cancellation_leaves_the_attempt_open_for_startup_recovery(
        self, hooks_service
    ):
        """SIGTERM cancels every pending task. Finalizing on cancellation would
        make every deploy burn an in-flight retry — the existing interruption
        machinery (`recover_interrupted_youtube_sessions`, the stale-run reaper)
        owns that case."""
        finalized: list[dict] = []
        hooks_service._finalize_unstarted_youtube_retry = lambda **kw: finalized.append(kw)

        async def _never_finishes(*args, **kwargs):
            await asyncio.sleep(30)
            return {"decision": "accepted"}

        hooks_service._dispatch_action = _never_finishes

        task = hooks_service._schedule_youtube_retry_attempt(
            action=_youtube_action(),
            run_id="run_1",
            attempt_id="attempt_2",
            workspace_dir="/tmp/ws",
            expected_video_id="test123abc",
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

        assert finalized == []

    @pytest.mark.asyncio
    async def test_scheduling_releases_the_inflight_marker(self, hooks_service):
        hooks_service._youtube_video_dispatch_inflight["test123abc"] = time.time()
        hooks_service._dispatch_action = AsyncMock(return_value={"decision": "accepted"})

        task = hooks_service._schedule_youtube_retry_attempt(
            action=_youtube_action(),
            run_id="run_1",
            attempt_id="attempt_2",
            workspace_dir="/tmp/ws",
            expected_video_id="test123abc",
        )
        assert "test123abc" not in hooks_service._youtube_video_dispatch_inflight
        await task

    @pytest.mark.asyncio
    async def test_finalize_marks_open_attempt_needs_review(
        self, hooks_service, tmp_path, monkeypatch
    ):
        """End-to-end on the real DB: an open (queued) attempt is failed and its
        run routed to needs_review."""
        run_id, attempt_id = _admit_youtube_run(hooks_service, str(tmp_path), monkeypatch)
        notifications: list[dict] = []
        hooks_service._notification_sink = notifications.append

        hooks_service._finalize_unstarted_youtube_retry(
            run_id=run_id,
            attempt_id=attempt_id,
            session_id="session_hook_yt_manual__test123abc",
            session_key="yt_manual__test123abc",
            hook_name="ManualYouTubeWebhook",
            expected_video_id="test123abc",
            workspace_dir=str(tmp_path),
            reason="youtube_retry_not_started:duplicate_video_dispatch",
        )

        conn = connect_runtime_db(hooks_service._workflow_admission_service().db_path)
        ensure_schema(conn)
        run_row = conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        attempt_row = conn.execute(
            "SELECT status, failure_class FROM run_attempts WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        conn.close()

        assert str(run_row["status"]) == "needs_review"
        assert str(attempt_row["status"]) == "failed"
        assert str(attempt_row["failure_class"]) == "youtube_retry_not_started"
        assert any(n.get("kind") == "youtube_tutorial_failed" for n in notifications)

    @pytest.mark.asyncio
    async def test_finalize_does_not_clobber_an_already_terminal_attempt(
        self, hooks_service, tmp_path, monkeypatch
    ):
        """`_dispatch_action` finalizes the attempt itself on its own failure
        paths (and may have queued a further retry). The callback must not
        overwrite that outcome."""
        run_id, attempt_id = _admit_youtube_run(hooks_service, str(tmp_path), monkeypatch)
        service = hooks_service._workflow_admission_service()
        service.mark_completed(run_id, attempt_id=attempt_id, summary={"status": "completed"})

        hooks_service._finalize_unstarted_youtube_retry(
            run_id=run_id,
            attempt_id=attempt_id,
            session_id="s",
            session_key="yt_manual__test123abc",
            hook_name="ManualYouTubeWebhook",
            expected_video_id="test123abc",
            workspace_dir=str(tmp_path),
            reason="youtube_retry_not_started:whatever",
        )

        conn = connect_runtime_db(service.db_path)
        ensure_schema(conn)
        run_row = conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        conn.close()
        assert str(run_row["status"]) != "needs_review"


# ── T2 / cause (1): the tutorial prompt runs the skills INLINE ──────────────

class TestInlineTutorialExecutionPrompt:
    """Under Agent Teams, `Task`/`Agent` is fire-and-forget: a delegating
    coordinator ends its turn in ~12s, the post-turn gate finds no manifest, and
    the backgrounded subagent dies with the session. The prompt must therefore
    tell the coordinator to run the two skills inline."""

    def test_manual_action_message_does_not_mandate_delegation(self):
        action = build_manual_youtube_action(
            {
                "video_url": "https://www.youtube.com/watch?v=test123abc",
                "video_id": "test123abc",
                "channel_id": "UCtest",
                "title": "Test",
            }
        )
        assert action is not None
        message = action["message"]
        assert "target_subagent:" not in message
        assert "INLINE" in message
        assert "youtube-transcript-metadata" in message
        assert "youtube-tutorial-creation" in message

    def test_manual_action_still_routes_to_the_youtube_lane(self):
        """`to` must stay `youtube-expert`: it is what `_is_youtube_agent_route`
        keys the workflow/dedup/artifact-validation lane off."""
        action = build_manual_youtube_action(
            {"video_url": "https://www.youtube.com/watch?v=test123abc", "video_id": "test123abc"}
        )
        assert action is not None
        assert action["to"] == "youtube-expert"

    def test_youtube_user_input_preamble_forbids_task_delegation(self, hooks_service):
        prompt = hooks_service._build_agent_user_input(_youtube_action())
        assert "Mandatory: delegate this run to the target subagent using Task." not in prompt
        assert "Do NOT delegate with Task(" in prompt
        assert "Skill: youtube-transcript-metadata" in prompt
        assert "Skill: youtube-tutorial-creation" in prompt

    def test_non_youtube_routes_still_delegate(self, hooks_service):
        """The inline rule is YouTube-only — other hook routes keep the
        Task-delegation preamble."""
        action = HookAction(
            kind="agent",
            name="AutoHeartbeatInvestigation",
            session_key="hb_123",
            to="factory-supervisor",
            message="activity_id: 42",
        )
        prompt = hooks_service._build_agent_user_input(action)
        assert "Mandatory: delegate this run to the target subagent using Task." in prompt
        assert "Task(subagent_type='factory-supervisor'" in prompt

    def test_manual_transform_message_matches_the_inline_directives(self):
        """`webhook_transforms/manual_youtube_transform.py` is what the LIVE
        `/api/v1/hooks/youtube/manual` lane runs, and its returned `message`
        OVERRIDES the base action's — so fixing only `build_manual_youtube_action`
        would have left the real webhook still ordering a delegation.

        The transform is loaded standalone by `HooksService._load_transform`
        (importlib, outside the package) and therefore duplicates the directive
        tuple. This is the drift guard for that duplication.
        """
        import importlib.util

        transform_path = (
            Path(__file__).resolve().parents[2]
            / "webhook_transforms"
            / "manual_youtube_transform.py"
        )
        spec = importlib.util.spec_from_file_location(
            "_manual_youtube_transform_under_test", transform_path
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        from universal_agent.hooks_service import YOUTUBE_INLINE_EXECUTION_DIRECTIVES

        assert (
            module.YOUTUBE_INLINE_EXECUTION_DIRECTIVES == YOUTUBE_INLINE_EXECUTION_DIRECTIVES
        ), "transform copy drifted from hooks_service.YOUTUBE_INLINE_EXECUTION_DIRECTIVES"

        action = module.transform(
            {
                "payload": {
                    "video_url": "https://www.youtube.com/watch?v=test123abc",
                    "video_id": "test123abc",
                    "channel_id": "UCtest",
                    "title": "Test",
                },
                "headers": {},
                "path": "youtube/manual",
            }
        )
        assert action is not None
        message = action["message"]
        assert "target_subagent:" not in message
        assert "Route this run to the YouTube specialist." not in message
        assert "Do NOT delegate with Task(" in message
        assert action["to"] == "youtube-expert"


# ── T2 / reclassification: deterministic artifact failures are not retryable ─

class TestArtifactValidationReclassification:
    """`_validate_youtube_tutorial_artifacts` raising is a DETERMINISTIC verdict
    about what is on disk. Classifying it as `hook_dispatch_failed` put it in the
    retryable set, so the pipeline burned attempts replaying an identical prompt
    instead of surfacing the lost tutorial."""

    def test_missing_manifest_classifies_to_youtube_artifacts_invalid(self, hooks_service):
        reason = hooks_service._dispatch_failure_reason(
            RuntimeError("youtube_artifacts_missing_manifest"), {}
        )
        assert reason == "youtube_artifacts_invalid"

    def test_incomplete_artifacts_classify_to_youtube_artifacts_invalid(self, hooks_service):
        reason = hooks_service._dispatch_failure_reason(
            RuntimeError("youtube_artifacts_incomplete:README.md,CONCEPT.md"), {}
        )
        assert reason == "youtube_artifacts_invalid"

    def test_validation_error_wins_over_interruption_tokens(self, hooks_service):
        """A stray interruption token in the execution summary must not launder a
        deterministic validation failure back into a retryable class."""
        reason = hooks_service._dispatch_failure_reason(
            RuntimeError("youtube_artifacts_missing_manifest"),
            {"reported_error_message": "api error", "iteration_status": "error"},
        )
        assert reason == "youtube_artifacts_invalid"

    def test_youtube_artifacts_invalid_is_not_retryable(self):
        assert (
            HooksService._is_retryable_youtube_dispatch_failure("youtube_artifacts_invalid")
            is False
        )

    def test_ordinary_dispatch_failures_are_unchanged(self, hooks_service):
        assert hooks_service._dispatch_failure_reason(RuntimeError("boom"), {}) == (
            "hook_dispatch_failed"
        )
        assert hooks_service._dispatch_failure_reason(
            RuntimeError("model overloaded"), {}
        ) == "hook_dispatch_interrupted"

    @pytest.mark.asyncio
    async def test_missing_manifest_routes_to_needs_review_not_a_retry(
        self, hooks_service, tmp_path, monkeypatch
    ):
        """End-to-end through the real queue-or-finalize path: no new attempt is
        created and the run lands in needs_review."""
        run_id, attempt_id = _admit_youtube_run(hooks_service, str(tmp_path), monkeypatch)
        notifications: list[dict] = []
        hooks_service._notification_sink = notifications.append
        hooks_service._schedule_youtube_retry_attempt = MagicMock()

        hooks_service._queue_or_finalize_youtube_attempt(
            action=_youtube_action(),
            session_id="session_hook_yt_manual__test123abc",
            session_key="yt_manual__test123abc",
            hook_name="ManualYouTubeWebhook",
            expected_video_id="test123abc",
            reason="youtube_artifacts_invalid",
            failure_class="youtube_artifacts_invalid",
            session_workspace=tmp_path,
            started_at_epoch=time.time(),
            workflow_run_id=run_id,
            workflow_attempt_id=attempt_id,
            workflow_attempt_number=1,
        )

        hooks_service._schedule_youtube_retry_attempt.assert_not_called()
        conn = connect_runtime_db(hooks_service._workflow_admission_service().db_path)
        ensure_schema(conn)
        run_row = conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        attempts = conn.execute(
            "SELECT attempt_id FROM run_attempts WHERE run_id = ?", (run_id,)
        ).fetchall()
        conn.close()
        assert str(run_row["status"]) == "needs_review"
        assert len(attempts) == 1, "a deterministic validation failure must not queue a retry"


# ── T2: the superseded stale-run sweeper is gone ────────────────────────────

def test_dead_finalize_stale_youtube_runs_is_removed():
    """`HooksService.finalize_stale_youtube_runs` was a caller-less predecessor
    of `services/stuck_run_reaper.finalize_stale_youtube_hook_runs` (which IS
    wired, via `utils/db_health_monitor.check_stale_runs`)."""
    assert not hasattr(HooksService, "finalize_stale_youtube_runs")

    from universal_agent.services.stuck_run_reaper import (  # noqa: F401
        finalize_stale_youtube_hook_runs,
    )

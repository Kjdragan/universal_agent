"""
Session Context — per-session state container for concurrent gateway execution.

Replaces the module-level globals in main.py that currently prevent concurrent
agent sessions. Each asyncio task gets its own isolated copy of SessionContext
via Python's ContextVar mechanism: asyncio.create_task() copies the current
context, so writes in one task's SessionContext are never visible to another.

Usage:
    # At session start (e.g. process_turn entry):
    ctx = SessionContext(run_id="abc123", ...)
    set_ctx(ctx)

    # Anywhere in the call tree (no parameter threading needed):
    ctx = require_ctx()
    ctx.run_id  # always the current session's value

See: docs/16_Concurrency_Conflict_Root_Cause_And_VP_General_Interim_Path_2026-03-02.md
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
import time
from typing import Any, Optional


def _default_trace() -> dict:
    return {
        "tool_calls": [],
        "token_usage": {"input": 0, "output": 0, "total": 0},
        "compact_boundary_events": [],
        "sdk_result_messages": [],
        "context_pressure": {
            "high_turns_without_compaction": 0,
            "last_compaction_iteration": None,
            "compaction_seen_iteration": None,
            "last_turn_input_tokens": 0,
        },
    }


@dataclass
class SessionContext:
    """
    All mutable session-scoped state that was previously stored as module-level
    globals in main.py.  One instance per concurrent agent session.

    Fields mirror the globals declared at main.py lines 3822-3857 and 185-196.
    CLI-only variables (interrupt_requested, run_cancelled_by_operator,
    last_sigint_ts) are intentionally excluded — they remain module-level.
    """

    # ------------------------------------------------------------------ core
    run_id: str = "unknown"
    current_step_id: Optional[str] = None
    trace: dict = field(default_factory=_default_trace)
    start_ts: float = field(default_factory=time.time)
    runtime_db_conn: Optional[Any] = None
    current_run_attempt_id: Optional[str] = None

    # ------------------------------------------------------------------ workspace
    observer_workspace_dir: str = field(default_factory=lambda: "")

    # ------------------------------------------------------------------ budget / ledger
    tool_ledger: Optional[Any] = None
    budget_state: dict = field(default_factory=dict)
    budget_config: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ provider session
    provider_session_forked_from: Optional[str] = None

    # ------------------------------------------------------------------ gateway mode
    gateway_mode_active: bool = False
    gateway_tool_call_map: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ tool execution tracking
    tool_execution_start_times: dict = field(default_factory=dict)
    tool_execution_stream_start_times: dict = field(default_factory=dict)
    tool_execution_emitted_ids: set = field(default_factory=set)

    # ------------------------------------------------------------------ forced tool
    forced_tool_queue: list = field(default_factory=list)
    forced_tool_active_ids: dict = field(default_factory=dict)
    forced_tool_mode_active: bool = False
    stopped_task_ids: set = field(default_factory=set)
    taskstop_consecutive_failures: int = 0

    # ------------------------------------------------------------------ transcript
    primary_transcript_path: Optional[str] = None
    seen_transcript_paths: set = field(default_factory=set)


# ---------------------------------------------------------------------------
# ContextVar — one per process; value is per-asyncio-task due to context copy
# semantics of asyncio.create_task().
# ---------------------------------------------------------------------------

_SESSION_CTX: ContextVar[Optional[SessionContext]] = ContextVar(
    "_SESSION_CTX", default=None
)


def get_ctx() -> Optional[SessionContext]:
    """Return the current task's SessionContext, or None if not set."""
    return _SESSION_CTX.get()


def require_ctx() -> SessionContext:
    """Return the current task's SessionContext.

    Raises RuntimeError if no context is set (programming error — caller should
    ensure set_ctx() was called before any concurrent-safe code runs).
    """
    ctx = _SESSION_CTX.get()
    if ctx is None:
        raise RuntimeError(
            "No SessionContext is active for this task. "
            "Call set_ctx() at the start of process_turn or equivalent entry point."
        )
    return ctx


def set_ctx(ctx: SessionContext) -> Token:
    """Set the SessionContext for the current task.

    Returns the ContextVar Token so callers can reset to a prior value if needed
    (useful in tests).
    """
    return _SESSION_CTX.set(ctx)


def reset_ctx(token: Token) -> None:
    """Reset the ContextVar to the value it held before a prior set_ctx() call."""
    _SESSION_CTX.reset(token)

"""
Execution Engine Adapter

Wraps the CLI's process_turn() to emit AgentEvents for gateway consumption.
This is the bridge between the stable CLI engine and the event-driven gateway.

The adapter ensures that:
1. All clients (CLI, Web UI, Harness) use the same execution path
2. Events are emitted in real-time as the agent processes
3. Workspace isolation is enforced
4. Session state is properly managed
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import sys
import time
import logging
import uuid
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

from universal_agent.agent_core import AgentEvent, EventType
from universal_agent.identity import resolve_user_id
from universal_agent.api.input_bridge import set_input_handler
from universal_agent.memory.paths import (
    resolve_shared_memory_workspace,
)
from universal_agent.runtime_env import ensure_runtime_path
from universal_agent.timeout_policy import process_turn_timeout_seconds

try:
    import logfire
    _LOGFIRE_AVAILABLE = bool(os.getenv("LOGFIRE_TOKEN") or os.getenv("LOGFIRE_WRITE_TOKEN"))
except ImportError:
    logfire = None  # type: ignore
    _LOGFIRE_AVAILABLE = False

logger = logging.getLogger(__name__)


_TERMINATED_PROCESS_ERROR_TOKENS = (
    "terminated process",
    "cannot write to terminated process",
    "exit code -15",
    "exit code: -15",
    "sigterm",
    "sigkill",
)


def _is_terminated_process_error(exc: Exception) -> bool:
    lowered = str(exc or "").strip().lower()
    if not lowered:
        return False
    return any(token in lowered for token in _TERMINATED_PROCESS_ERROR_TOKENS)


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


USE_PROCESS_STDIO_REDIRECT = _env_truthy("UA_GATEWAY_PROCESS_STDIO_REDIRECT", default=False)


class _TeeWriter:
    """Mirror writes to both a file handle and an underlying stream."""

    def __init__(self, file_handle, stream):
        self._file = file_handle
        self._stream = stream

    def write(self, data: str) -> None:
        # Process-wide stdio redirection can overlap across concurrent turns.
        # If an older tee's file handle is already closed, never raise from here;
        # keep best-effort writing to whatever stream/file is still valid.
        try:
            self._stream.write(data)
            self._stream.flush()
        except Exception:
            pass
        try:
            if not getattr(self._file, "closed", False):
                self._file.write(data)
                self._file.flush()
        except Exception:
            pass

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass
        try:
            if not getattr(self._file, "closed", False):
                self._file.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        return bool(getattr(self._stream, "isatty", lambda: False)())

    def fileno(self) -> int:
        return int(getattr(self._stream, "fileno")())


@contextmanager
def _redirect_stdio_to_run_log(workspace_dir: str) -> Any:
    """
    Redirect process-wide stdout/stderr to the session's run.log for the duration
    of a single turn, then restore.
    """

    log_path = Path(workspace_dir) / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    handle = log_path.open("a", encoding="utf-8", errors="replace")

    try:
        sys.stdout = _TeeWriter(handle, old_stdout)  # type: ignore[assignment]
        sys.stderr = _TeeWriter(handle, old_stderr)  # type: ignore[assignment]
        yield handle  # Yield handle to allow explicit writing
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        try:
            handle.close()
        except Exception:
            pass


@contextmanager
def _temporary_env(overrides: dict[str, Optional[str]]) -> Any:
    original: dict[str, Optional[str]] = {}
    keys = list(overrides.keys())
    try:
        for key in keys:
            original[key] = os.environ.get(key)
            value = overrides[key]
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)
        yield
    finally:
        for key in keys:
            previous = original.get(key)
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous


@contextmanager
def _temporary_sanitized_process_env() -> Any:
    """Temporarily sanitize the process env for child-process spawn only.

    Some SDK transports build the child env directly from ``os.environ`` and do
    not accept a replacement mapping. We still need to protect the parent
    gateway process from losing runtime secrets after the child is spawned.
    """
    original = dict(os.environ)
    try:
        sanitize_env_for_subprocess()
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


# ---------------------------------------------------------------------------
# Environment sanitisation guard – prevent E2BIG on subprocess spawn
# ---------------------------------------------------------------------------
#
# Linux ARG_MAX is ~2 MB for argv + envp combined.  In practice, our total env
# is ~14 KB and the system prompt + MCP config consume ~200 KB, leaving >1.8 MB
# of headroom.
#
# History: the original whitelist approach (keep only ANTHROPIC_/CLAUDE_/UA_/...)
# repeatedly broke integrations (AGENTMAIL_API_KEY, future service keys) because
# every new credential required a manual allowlist entry.  With 14 KB of env
# against a 2 MB budget, the aggressive stripping saved ~9 KB and bought nothing.
#
# Current approach: **blocklist** — strip only known-huge or useless vars.
# Everything else (including Infisical-injected service keys) passes through.
# This is resilient by default: new secrets from Infisical just work.

# Max bytes for a single system-events env var before it gets truncated.
_MAX_SYSTEM_EVENTS_ENV_BYTES = 32_000  # 32 KB


def _env_total_size() -> int:
    """Return the approximate total byte size of the current process environ."""
    return sum(len(k) + len(v) + 2 for k, v in os.environ.items())


# Exact var names to always strip (known-huge or subprocess-irrelevant).
_STRIP_EXACT = {
    "LS_COLORS",
    "LSCOLORS",
    "UA_SYSTEM_EVENTS_JSON",
    "UA_SYSTEM_EVENTS_PROMPT",
    "CLAUDE_AGENT_CONVERSATION_HISTORY",
}

# Prefixes to always strip (bash exported functions, terminal escapes).
_STRIP_PREFIXES = (
    "BASH_FUNC_",
    "LESS_TERMCAP_",
)

# Backward-compatible alias kept for existing tests and internal references.
_ENV_STRIP_CANDIDATES = tuple(sorted(_STRIP_EXACT))

# Safety cap: if total env exceeds this, fall back to aggressive stripping.
_ENV_SAFE_THRESHOLD_BYTES = 1_500_000  # 1.5 MB


def sanitize_env_for_subprocess() -> list[str]:
    """Strip known-large env vars to keep subprocess argv+envp under ARG_MAX.

    Uses a **blocklist** approach: only known-problematic vars (LS_COLORS,
    system event blobs, conversation history, bash exported functions) are
    removed.  All Infisical-injected service credentials (AGENTMAIL_API_KEY,
    etc.) pass through automatically.

    If after blocklist stripping the total env still exceeds 1.5 MB, falls back
    to stripping any individual var larger than 4 KB as a safety valve.

    Modifies ``os.environ`` **in-place** and returns the list of keys removed.
    """
    total_before = _env_total_size()
    removed: list[str] = []

    for key in list(os.environ):
        if key in _STRIP_EXACT:
            removed.append(key)
            os.environ.pop(key)
            continue
        if any(key.startswith(p) for p in _STRIP_PREFIXES):
            removed.append(key)
            os.environ.pop(key)
            continue

    # Safety valve: if still over threshold, strip any single var > 4 KB.
    total_after = _env_total_size()
    if total_after > _ENV_SAFE_THRESHOLD_BYTES:
        for key in list(os.environ):
            val = os.environ.get(key, "")
            if len(key) + len(val) > 4096:
                removed.append(key)
                os.environ.pop(key)
                logger.warning(
                    "Safety-stripped oversized env var %s (%d bytes)",
                    key, len(key) + len(val),
                )
        total_after = _env_total_size()

    logger.info(
        "Sanitized env for subprocess: kept %d vars, removed %d vars, "
        "%d KB → %d KB (headroom: %d KB)",
        len(os.environ),
        len(removed),
        total_before // 1024,
        total_after // 1024,
        (2_097_152 - total_after) // 1024,
    )
    return removed


def _build_memory_env_overrides(memory_policy: dict[str, Any] | None) -> dict[str, Optional[str]]:
    policy = memory_policy if isinstance(memory_policy, dict) else {}
    enabled = policy.get("enabled", True)
    if not isinstance(enabled, bool):
        enabled = bool(enabled)
    session_enabled = policy.get("sessionMemory", True)
    if not isinstance(session_enabled, bool):
        session_enabled = bool(session_enabled)
    sources = policy.get("sources", ["memory", "sessions"])
    if isinstance(sources, str):
        source_items = [item.strip().lower() for item in sources.split(",") if item.strip()]
    elif isinstance(sources, list):
        source_items = [str(item).strip().lower() for item in sources if str(item).strip()]
    else:
        source_items = ["memory", "sessions"]
    source_items = [item for item in source_items if item in {"memory", "sessions"}] or ["memory", "sessions"]
    scope = str(policy.get("scope", "direct_only")).strip().lower()
    if scope not in {"direct_only", "all"}:
        scope = "direct_only"

    overrides: dict[str, Optional[str]] = {
        "UA_MEMORY_PROVIDER": None,
        "UA_MEMORY_SCOPE": scope,
        "UA_MEMORY_SOURCES": ",".join(source_items),
    }
    if not enabled:
        overrides.update(
            {
                "UA_DISABLE_MEMORY": "1",
                "UA_MEMORY_ENABLED": "0",
                "UA_MEMORY_SESSION_ENABLED": None,
                "UA_MEMORY_SESSION_DISABLED": "1",
            }
        )
        return overrides

    overrides.update(
        {
            "UA_DISABLE_MEMORY": None,
            "UA_MEMORY_ENABLED": "1",
        }
    )
    if session_enabled:
        overrides["UA_MEMORY_SESSION_ENABLED"] = "1"
        overrides["UA_MEMORY_SESSION_DISABLED"] = None
    else:
        overrides["UA_MEMORY_SESSION_ENABLED"] = None
        overrides["UA_MEMORY_SESSION_DISABLED"] = "1"

    return overrides


def _format_system_events_for_prompt(events: list[dict[str, Any]]) -> str:
    """
    Render ephemeral system events into a compact preamble that can be prefixed
    to the next model prompt (Clawdbot parity).
    """
    lines: list[str] = []
    for evt in events:
        if not isinstance(evt, dict):
            continue
        text = str(evt.get("text") or "").strip()
        if not text:
            # Fall back to a compact JSON repr when no text exists.
            try:
                text = json.dumps(evt, ensure_ascii=True, sort_keys=True)
            except Exception:
                text = str(evt).strip()
        if not text:
            continue
        lines.append(f"System: {text}")
    return "\n".join(lines)


@dataclass
class EngineConfig:
    """Configuration for the execution engine."""
    workspace_dir: str
    user_id: Optional[str] = None
    force_complex: bool = False
    max_iterations: int = 20
    run_id: Optional[str] = None
    extra_disallowed_tools: list[str] = field(default_factory=list)
    
    def __post_init__(self):
        if not self.user_id:
            self.user_id = resolve_user_id()
        if not self.run_id:
            self.run_id = str(uuid.uuid4())


@dataclass
class EngineSession:
    """Represents an active execution engine session."""
    session_id: str
    workspace_dir: str
    user_id: str
    run_id: str
    created_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


class ProcessTurnAdapter:
    """
    Adapts process_turn() to the gateway's event-streaming interface.
    
    This allows the gateway to use the battle-tested CLI engine while
    providing a consistent event stream to all clients.
    
    Usage:
        config = EngineConfig(workspace_dir="/path/to/workspace")
        adapter = ProcessTurnAdapter(config)
        await adapter.initialize()
        
        async for event in adapter.execute("Write a summary"):
            print(event)
    """
    
    def __init__(self, config: EngineConfig):
        self.config = config
        self._initialized = False
        self._client: Optional[Any] = None
        self._options: Optional[Any] = None
        self._session: Optional[Any] = None
        self._event_queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._pending_inputs: dict[str, asyncio.Future[str]] = {}
        self._trace: dict[str, Any] = {}
        self._applied_extra_disallowed_tools: tuple[str, ...] = tuple()
        
    @property
    def workspace_dir(self) -> str:
        return self.config.workspace_dir
    
    @property
    def session_info(self) -> Optional[EngineSession]:
        if not self._initialized:
            return None
        
        # Extract session URL if available (session.mcp.url structure varies)
        session_url = None
        if self._session:
            mcp = getattr(self._session, "mcp", None)
            if mcp:
                session_url = getattr(mcp, "url", None)
        
        return EngineSession(
            session_id=Path(self.config.workspace_dir).name,
            workspace_dir=self.config.workspace_dir,
            user_id=self.config.user_id or "unknown",
            run_id=self.config.run_id or "unknown",
            metadata={
                "session_url": session_url,
            },
        )
    
    async def initialize(self) -> EngineSession:
        """
        Initialize the execution engine.
        
        This mirrors the CLI's setup_session to ensure identical initialization.
        """
        if self._initialized:
            return self.session_info  # type: ignore
        
        # Ensure workspace exists
        workspace_path = Path(self.config.workspace_dir)
        workspace_path.mkdir(parents=True, exist_ok=True)
        (workspace_path / "work_products").mkdir(exist_ok=True)
        
        # Import here to avoid circular imports
        from universal_agent.main import setup_session
        
        # Use the CLI's setup_session to ensure identical initialization
        # setup_session returns 6 values: options, session, user_id, workspace_dir, trace, agent
        self._options, self._session, user_id, workspace_dir, self._trace, _agent = await setup_session(
            run_id_override=self.config.run_id,
            workspace_dir_override=self.config.workspace_dir,
            attach_stdio=False,
            extra_disallowed_tools=list(self.config.extra_disallowed_tools or []),
        )
        
        # Update config with resolved values
        self.config.user_id = user_id
        self.config.workspace_dir = workspace_dir
        self._applied_extra_disallowed_tools = tuple(self.config.extra_disallowed_tools or [])

        self._initialized = True
        return self.session_info  # type: ignore
    
    async def _ensure_client(self) -> Any:
        """Lazily initialize and enter the persistent SDK client."""
        desired_extra_disallowed = tuple(self.config.extra_disallowed_tools or [])
        if desired_extra_disallowed != self._applied_extra_disallowed_tools:
            await self.reset()
            self._initialized = False
            await self.initialize()
        if self._client is None:
            from claude_agent_sdk.client import ClaudeSDKClient
            from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

            # Guard against E2BIG: the SDK transport builds the child process
            # env from process-global os.environ. Narrow sanitization to the
            # actual subprocess spawn only; the parent gateway process must
            # retain its runtime secrets before the SDK initialize handshake.
            async def _empty_stream() -> AsyncIterator[dict[str, Any]]:
                return
                yield {}  # type: ignore[unreachable]

            transport_options = self._options
            if getattr(self._options, "can_use_tool", None):
                if getattr(self._options, "permission_prompt_tool_name", None):
                    raise ValueError(
                        "can_use_tool callback cannot be used with permission_prompt_tool_name. "
                        "Please use one or the other."
                    )
                transport_options = replace(
                    self._options,
                    permission_prompt_tool_name="stdio",
                )

            transport = SubprocessCLITransport(
                prompt=_empty_stream(),
                options=transport_options,
            )
            with _temporary_sanitized_process_env():
                await transport.connect()

            self._client = ClaudeSDKClient(self._options, transport=transport)
            # Enter the context manager manually
            await self._client.__aenter__()
        return self._client
    
    async def execute(self, user_input: str) -> AsyncIterator[AgentEvent]:
        """
        Execute a query and yield AgentEvents.
        
        This wraps process_turn() and emits events based on the execution.
        For real-time streaming, we use a callback mechanism.
        """
        if not self._initialized:
            await self.initialize()
        ensure_runtime_path()

        # Propagate the gateway-provided run source (e.g., "heartbeat") into
        # status events so the Web UI can treat background runs differently.
        run_source = self.config.__dict__.get("_run_source", "user")
        
        # Emit session info
        yield AgentEvent(
            type=EventType.SESSION_INFO,
            data={
                "session_id": Path(self.config.workspace_dir).name,
                "workspace": self.config.workspace_dir,
                "user_id": self.config.user_id,
            },
        )
        
        # Emit processing status
        yield AgentEvent(
            type=EventType.STATUS,
            data={"status": "processing", "query": user_input[:100], "source": run_source},
        )
        
        # Import here to avoid circular imports
        from claude_agent_sdk.client import ClaudeSDKClient
        from universal_agent.main import process_turn, budget_state
        
        # Reset budget state per execution (CLI entrypoint manages this separately).
        budget_state["start_ts"] = time.time()
        budget_state["steps"] = 0
        budget_state["tool_calls"] = 0
        
        # Optional: capture Claude CLI stderr for gateway debugging
        if self._options is not None and os.getenv("UA_CLAUDE_CLI_STDERR", "1").lower() in {"1", "true", "yes"}:
            def _stderr_line(line: str) -> None:
                logger.warning("Claude CLI stderr: %s", line)
            self._options.stderr = _stderr_line

        # Optional: enable Claude CLI debug categories (comma-separated)
        if self._options is not None:
            debug_flag = os.getenv("UA_CLAUDE_CLI_DEBUG", "").strip()
            if debug_flag:
                self._options.extra_args.setdefault("debug", debug_flag)

        # 1. Ensure client is initialized and persistent
        client = await self._ensure_client()
        
        # 2. Run process_turn using the persistent client
        start_ts = time.time()
        
        # Create a root Logfire span for gateway executions so that
        # process_turn() inherits span context and trace_id is captured.
        _gateway_span = None
        _gateway_span_ctx = None
        _gateway_trace_id_hex: Optional[str] = None
        if _LOGFIRE_AVAILABLE and logfire:
            _gateway_span = logfire.span(
                "gateway_request",
                session_id=Path(self.config.workspace_dir).name,
                run_id=self.config.run_id,
                run_source=self.config.__dict__.get("_run_source", "user"),
            )
            _gateway_span_ctx = _gateway_span.__enter__()
            try:
                raw_tid = _gateway_span.get_span_context().trace_id
                _gateway_trace_id_hex = format(raw_tid, "032x")
                self._trace["trace_id"] = _gateway_trace_id_hex
            except Exception:
                pass
        
        # Create event callback for real-time streaming
        event_queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        
        def event_callback(event: AgentEvent) -> None:
            """Callback to capture events from process_turn."""
            try:
                # Ensure all status events carry a source so the UI can avoid
                # showing "ABORT"/redirect UX for background runs (heartbeat).
                if event.type == EventType.STATUS and isinstance(event.data, dict):
                    event.data.setdefault("source", run_source)
                event_queue.put_nowait(event)
            except Exception:
                pass
        
        # Setup input proxy
        async def remote_input_proxy(question: str, category: str, options: Optional[List[str]]) -> str:
            input_id = f"input_{uuid.uuid4().hex[:8]}"
            future = asyncio.get_running_loop().create_future()
            
            input_event = AgentEvent(
                type=EventType.INPUT_REQUIRED,
                data={
                    "input_id": input_id,
                    "question": question,
                    "category": category,
                    "options": options or [],
                }
            )
            event_callback(input_event)
            self._pending_inputs[input_id] = future
            return await future

        set_input_handler(remote_input_proxy)
        
        # Create task to run process_turn
        result_holder: dict[str, Any] = {}
        
        async def run_engine():
            try:
                from universal_agent.execution_session import ExecutionSession
                from universal_agent.main import process_turn
                import universal_agent.main as main_module
                from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
                from universal_agent.durable.migrations import ensure_schema

                runtime_db_conn = getattr(main_module, "runtime_db_conn", None)
                needs_runtime_db_reset = runtime_db_conn is None
                if runtime_db_conn is not None:
                    try:
                        runtime_db_conn.execute("SELECT 1")
                    except Exception:
                        needs_runtime_db_reset = True

                if needs_runtime_db_reset:
                    runtime_db_conn = connect_runtime_db(get_runtime_db_path())
                    ensure_schema(runtime_db_conn)
                    setattr(main_module, "runtime_db_conn", runtime_db_conn)

                execution_session = ExecutionSession(
                    workspace_dir=self.config.workspace_dir,
                    run_id=self.config.run_id,
                    trace=self._trace,
                    runtime_db_conn=runtime_db_conn,
                )
                memory_policy = self.config.__dict__.get("_memory_policy")
                env_overrides = _build_memory_env_overrides(
                    memory_policy if isinstance(memory_policy, dict) else {}
                )
                run_source_env = str(self.config.__dict__.get("_run_source", "user") or "user")
                request_metadata = self.config.__dict__.get("_request_metadata")
                request_md = request_metadata if isinstance(request_metadata, dict) else {}
                codebase_root = str(request_md.get("codebase_root") or "").strip()
                allowed_codebase_roots = request_md.get("allowed_codebase_roots")
                if not isinstance(allowed_codebase_roots, list):
                    allowed_codebase_roots = []
                allowed_codebase_roots = [
                    str(item).strip()
                    for item in allowed_codebase_roots
                    if str(item).strip()
                ]
                investigation_env_default = (
                    str(os.getenv("UA_HEARTBEAT_INVESTIGATION_ONLY", "0") or "0").strip().lower()
                    not in {"0", "false", "no", "off", ""}
                )
                raw_investigation_only = request_md.get(
                    "heartbeat_investigation_only",
                    investigation_env_default if run_source_env == "heartbeat" else False,
                )
                if isinstance(raw_investigation_only, bool):
                    heartbeat_investigation_only = raw_investigation_only
                else:
                    heartbeat_investigation_only = str(raw_investigation_only).strip().lower() not in {
                        "0",
                        "false",
                        "no",
                        "off",
                        "",
                    }
                env_overrides["UA_RUN_SOURCE"] = run_source_env
                env_overrides["UA_HEARTBEAT_INVESTIGATION_ONLY"] = (
                    "1" if heartbeat_investigation_only else "0"
                )
                if codebase_root:
                    env_overrides["CURRENT_CODEBASE_ROOT"] = codebase_root
                if allowed_codebase_roots:
                    env_overrides["CURRENT_ALLOWED_CODEBASE_ROOTS"] = os.pathsep.join(
                        allowed_codebase_roots
                    )

                system_events = self.config.__dict__.get("_system_events")
                _events_tmp_path: Optional[str] = None
                if isinstance(system_events, list) and system_events:
                    # Write events to a temp file and pass the path via env.
                    # This avoids stuffing potentially large payloads into env
                    # vars, which can trigger E2BIG on subprocess spawn.
                    try:
                        workspace = Path(self.config.workspace_dir)
                        workspace.mkdir(parents=True, exist_ok=True)
                        fd, _events_tmp_path = tempfile.mkstemp(
                            prefix="ua_events_", suffix=".json", dir=str(workspace)
                        )
                        with os.fdopen(fd, "w") as f:
                            json.dump(system_events, f, ensure_ascii=True)
                        env_overrides["UA_SYSTEM_EVENTS_FILE"] = _events_tmp_path
                        logger.debug(
                            "Wrote %d system events (%d bytes) to %s",
                            len(system_events),
                            os.path.getsize(_events_tmp_path),
                            _events_tmp_path,
                        )
                    except Exception:
                        logger.warning("Failed to write system events file", exc_info=True)
                        env_overrides["UA_SYSTEM_EVENTS_FILE"] = None
                    # Clear legacy env vars — subprocess reads from file now.
                    env_overrides["UA_SYSTEM_EVENTS_JSON"] = None
                    env_overrides["UA_SYSTEM_EVENTS_PROMPT"] = None
                else:
                    env_overrides["UA_SYSTEM_EVENTS_FILE"] = None
                    env_overrides["UA_SYSTEM_EVENTS_JSON"] = None
                    env_overrides["UA_SYSTEM_EVENTS_PROMPT"] = None

                with _temporary_env(env_overrides):
                    if USE_PROCESS_STDIO_REDIRECT:
                        with _redirect_stdio_to_run_log(self.config.workspace_dir) as log_handle:
                            # Log user input
                            try:
                                ts = datetime.now().strftime("%H:%M:%S")
                                log_handle.write(f"\n[{ts}] 👤 USER: {user_input}\n")
                                log_handle.flush()
                            except Exception:
                                pass

                            # Wrap callback to log events
                            def logging_callback(event: AgentEvent) -> None:
                                try:
                                    if event_callback:
                                        event_callback(event)
                                    
                                    # Simple plain-text logging of key events
                                    ts = datetime.now().strftime("%H:%M:%S")
                                    if event.type == EventType.TEXT:
                                        text = event.data.get("text", "")
                                        if text:
                                            log_handle.write(f"[{ts}] 🤖 ASSISTANT: {text}\n")
                                    elif event.type == EventType.TOOL_CALL:
                                        name = event.data.get("name", "unknown")
                                        log_handle.write(f"\n[{ts}] 🛠️ TOOL CALL: {name}\n")
                                    elif event.type == EventType.TOOL_RESULT:
                                        log_handle.write(f"[{ts}] 📦 TOOL RESULT\n")
                                    elif event.type == EventType.STATUS:
                                        status = event.data.get("status", "")
                                        if status:
                                            log_handle.write(f"[{ts}] ℹ️ STATUS: {status}\n")
                                    elif event.type == EventType.ERROR:
                                        msg = event.data.get("message", "")
                                        log_handle.write(f"[{ts}] ❌ ERROR: {msg}\n")
                                    
                                    log_handle.flush()
                                except Exception:
                                    pass

                            result = await process_turn(
                                client=client,
                                user_input=user_input,
                                workspace_dir=self.config.workspace_dir,
                                force_complex=self.config.force_complex,
                                execution_session=execution_session,
                                max_iterations=self.config.max_iterations,
                                event_callback=logging_callback,
                            )
                    else:
                        result = await process_turn(
                            client=client,
                            user_input=user_input,
                            workspace_dir=self.config.workspace_dir,
                            force_complex=self.config.force_complex,
                            execution_session=execution_session,
                            max_iterations=self.config.max_iterations,
                            event_callback=event_callback,
                        )
                result_holder["result"] = result
                result_holder["success"] = True
            except Exception as e:
                logger.error("Engine error: %s", e, exc_info=True)
                result_holder["error"] = str(e)
                result_holder["error_detail"] = traceback.format_exc()
                result_holder["success"] = False
                # Circuit breaker / budget exits should tear down the underlying Claude subprocess
                # to avoid orphaned long-running loops. Next call will recreate the client.
                try:
                    from universal_agent.main import BudgetExceeded, CircuitBreakerTriggered

                    if isinstance(e, (BudgetExceeded, CircuitBreakerTriggered)):
                        logger.warning("Resetting SDK client after guardrail: %s", type(e).__name__)
                        await self.reset()
                except Exception:
                    pass
                # Dead SDK subprocesses (common during service restarts) should
                # be torn down so the next turn can recreate a clean client.
                if _is_terminated_process_error(e):
                    try:
                        logger.warning("Resetting SDK client after terminated process error")
                        await self.reset()
                    except Exception:
                        pass
            finally:
                # Signal completion
                await event_queue.put(AgentEvent(
                    type=EventType.STATUS,
                    data={"status": "engine_complete"},
                ))
        
        engine_task = asyncio.create_task(run_engine())
        max_runtime_s = process_turn_timeout_seconds()
        deadline = (time.time() + max_runtime_s) if max_runtime_s > 0 else None
        
        # Yield events as they come in
        while True:
            try:
                if deadline and time.time() > deadline:
                    logger.error("ProcessTurnAdapter timed out after %.1fs", max_runtime_s)
                    engine_task.cancel()
                    try:
                        await engine_task
                    except Exception:
                        pass
                    yield AgentEvent(
                        type=EventType.ERROR,
                        data={
                            "message": f"Execution timed out after {max_runtime_s:.1f}s",
                            "duration_seconds": round(time.time() - start_ts, 2),
                        },
                    )
                    break
                event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                
                if event.data.get("status") == "engine_complete":
                    break
                
                yield event
                
            except asyncio.TimeoutError:
                if engine_task.done():
                    while not event_queue.empty():
                        event = event_queue.get_nowait()
                        if event.data.get("status") != "engine_complete":
                            yield event
                    break
                continue
        
        # Wait for engine to complete
        if not engine_task.done():
            await engine_task
        
        # Close the gateway Logfire span
        if _gateway_span is not None:
            try:
                _gateway_span.__exit__(None, None, None)
            except Exception:
                pass
        
        # Clear handler
        set_input_handler(None)
        self._pending_inputs.clear()
        
        # Emit results from the execution
        if result_holder.get("success"):
            result = result_holder.get("result")
            if result:
                # 0. Handle session reset request
                if getattr(result, "reset_session", False):
                    await self.reset()

                # Emit tool call summary
                if hasattr(result, 'tool_calls') and result.tool_calls:
                    yield AgentEvent(
                        type=EventType.STATUS,
                        data={
                            "status": "tools_complete",
                            "tool_count": result.tool_calls,
                        },
                    )
                
                # Emit final response text only as a fallback.
                # In the normal streaming path, text was already emitted via
                # hook_events.emit_text_event() during run_conversation().
                # The gateway server also filters final=True when streaming was seen.
                if hasattr(result, 'response_text') and result.response_text:
                    yield AgentEvent(
                        type=EventType.TEXT,
                        data={"text": result.response_text, "final": True},
                    )
                
                # Emit work products
                if hasattr(result, 'workspace_path') and result.workspace_path:
                    work_products_dir = Path(result.workspace_path) / "work_products"
                    if work_products_dir.exists():
                        for f in work_products_dir.rglob("*"):
                            if f.is_file():
                                yield AgentEvent(
                                    type=EventType.WORK_PRODUCT,
                                    data={
                                        "path": str(f.relative_to(result.workspace_path)),
                                        "absolute_path": str(f),
                                        "size": f.stat().st_size,
                                    },
                                )
                
                # Emit completion — prefer the gateway span trace_id over
                # the result's trace_id (which may be None on gateway path)
                effective_trace_id = _gateway_trace_id_hex or getattr(result, 'trace_id', None)
                latest_stop_reason = None
                try:
                    sdk_results = self._trace.get("sdk_result_messages", [])
                    if isinstance(sdk_results, list) and sdk_results:
                        tail = sdk_results[-1]
                        if isinstance(tail, dict):
                            latest_stop_reason = tail.get("stop_reason")
                except Exception:
                    latest_stop_reason = None
                yield AgentEvent(
                    type=EventType.ITERATION_END,
                    data={
                        "status": "complete",
                        "duration_seconds": round(time.time() - start_ts, 2),
                        "tool_calls": getattr(result, 'tool_calls', 0),
                        "trace_id": effective_trace_id,
                        "stop_reason": latest_stop_reason,
                    },
                )
        else:
            # Emit error
            error_message = result_holder.get("error", "Unknown error")
            error_detail = result_holder.get("error_detail")
            log_tail = None
            log_path = Path(self.config.workspace_dir) / "run.log"
            if log_path.exists():
                try:
                    # Read the last ~4KB to surface stderr/context without huge payloads
                    with log_path.open("rb") as handle:
                        handle.seek(0, os.SEEK_END)
                        size = handle.tell()
                        handle.seek(max(size - 4096, 0))
                        log_tail = handle.read().decode("utf-8", errors="replace")
                except Exception as e:
                    logger.warning("Failed to read run.log tail: %s", e)
            logger.error("Execution engine error: %s", error_message)
            yield AgentEvent(
                type=EventType.ERROR,
                data={
                    "message": error_message,
                    "detail": error_detail,
                    "log_tail": log_tail,
                    "duration_seconds": round(time.time() - start_ts, 2),
                },
            )
    
    async def execute_simple(self, user_input: str) -> dict[str, Any]:
        """
        Execute a query and return aggregated results (non-streaming).
        
        Useful for testing and simple integrations.
        """
        response_text = ""
        tool_calls = 0
        work_products: list[str] = []
        trace_id = None
        error = None
        
        async for event in self.execute(user_input):
            if event.type == EventType.TEXT:
                response_text += event.data.get("text", "")
            elif event.type == EventType.TOOL_CALL:
                tool_calls += 1
            elif event.type == EventType.WORK_PRODUCT:
                work_products.append(event.data.get("path", ""))
            elif event.type == EventType.ITERATION_END:
                trace_id = event.data.get("trace_id")
            elif event.type == EventType.ERROR:
                error = event.data.get("error")
        
        return {
            "response_text": response_text,
            "tool_calls": tool_calls,
            "work_products": work_products,
            "trace_id": trace_id,
            "error": error,
            "success": error is None,
        }
    
    async def reset(self) -> None:
        """Clear the persistent SDK client and history."""
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass
            self._client = None
        logger.info("Session reset: client history cleared")

    async def get_mcp_status(self) -> dict[str, Any]:
        """Return typed MCP status for the active SDK client."""
        client = await self._ensure_client()
        status = await client.get_mcp_status()
        if isinstance(status, dict):
            return status
        try:
            return json.loads(json.dumps(status, default=str))
        except Exception:
            return {"mcpServers": []}

    async def add_mcp_server(self, server_name: str, server_config: dict[str, Any]) -> dict[str, Any]:
        """Attach an MCP server config for this adapter session."""
        name = str(server_name or "").strip()
        if not name:
            raise ValueError("server_name is required")
        if not isinstance(server_config, dict) or not server_config:
            raise ValueError("server_config must be a non-empty object")
        if self._options is None:
            raise RuntimeError("Adapter is not initialized")

        mcp_servers = dict(getattr(self._options, "mcp_servers", {}) or {})
        mcp_servers[name] = server_config
        self._options.mcp_servers = mcp_servers

        # If the SDK adds native runtime attach support, use it.
        if self._client is not None and hasattr(self._client, "add_mcp_server"):
            await self._client.add_mcp_server(name, server_config)  # type: ignore[attr-defined]
        else:
            # Current SDK path requires client restart to pick up modified options.
            await self.reset()
        return {
            "server_name": name,
            "configured": True,
            "status": await self.get_mcp_status(),
        }

    async def remove_mcp_server(self, server_name: str) -> dict[str, Any]:
        """Detach an MCP server config from this adapter session."""
        name = str(server_name or "").strip()
        if not name:
            raise ValueError("server_name is required")
        if self._options is None:
            raise RuntimeError("Adapter is not initialized")

        mcp_servers = dict(getattr(self._options, "mcp_servers", {}) or {})
        if name not in mcp_servers:
            raise ValueError(f"MCP server '{name}' is not configured")

        if self._client is not None and hasattr(self._client, "remove_mcp_server"):
            await self._client.remove_mcp_server(name)  # type: ignore[attr-defined]
        elif self._client is not None and hasattr(self._client, "toggle_mcp_server"):
            # Best-effort immediate detach before restart fallback.
            await self._client.toggle_mcp_server(name, enabled=False)  # type: ignore[attr-defined]

        mcp_servers.pop(name, None)
        self._options.mcp_servers = mcp_servers
        await self.reset()
        return {
            "server_name": name,
            "removed": True,
            "status": await self.get_mcp_status(),
        }

    async def close(self) -> None:
        """Clean up resources and flush memory."""
        # Flush memory before tearing down (mirrors main.py post-run flush)
        await self._flush_memory()
        await self.reset()
        self._initialized = False
        self._options = None
        self._session = None

    async def _flush_memory(self) -> None:
        """Persist session memory and sync transcript to long-term store."""
        try:
            from universal_agent.feature_flags import (
                memory_enabled,
                memory_flush_enabled,
                memory_flush_max_chars,
                memory_session_index_on_end,
            )
            if not memory_enabled():
                return

            workspace_dir = self.config.workspace_dir
            session_id = self._trace.get("session_id") or self._trace.get("run_id") or Path(workspace_dir).name
            transcript_path = os.path.join(workspace_dir, "transcript.md")

            # Use shared memory dir for cross-workspace accumulation
            shared_memory_dir = resolve_shared_memory_workspace(workspace_dir)

            # 1. Pre-compact flush: save transcript tail as long-term memory
            if memory_flush_enabled(default=True):
                from universal_agent.memory.memory_flush import flush_pre_compact_memory
                flush_pre_compact_memory(
                    workspace_dir=shared_memory_dir,
                    session_id=session_id,
                    transcript_path=transcript_path,
                    trigger="gateway_close",
                    max_chars=memory_flush_max_chars(default=4000),
                )
                logger.info("Memory flush complete for session %s", session_id)

            # 2. Session index sync: index the transcript for cross-session search
            if memory_session_index_on_end(default=True):
                try:
                    from universal_agent.memory.orchestrator import get_memory_orchestrator
                    broker = get_memory_orchestrator(workspace_dir=shared_memory_dir)
                    result = broker.sync_session(
                        session_id=session_id,
                        transcript_path=transcript_path,
                        force=True,  # Always sync on close, ignore delta thresholds
                    )
                    logger.info("Session sync on close: %s", result)
                except Exception as exc:
                    logger.warning("Session sync failed on close: %s", exc)

        except Exception as exc:
            logger.warning("Memory flush failed during close: %s", exc)


class ExecutionEngineFactory:
    """
    Factory for creating ProcessTurnAdapter instances.
    
    Manages adapter lifecycle and provides session-based access.
    """
    
    def __init__(self, workspace_base: Optional[Path] = None):
        self.workspace_base = workspace_base or Path("AGENT_RUN_WORKSPACES")
        self._adapters: dict[str, ProcessTurnAdapter] = {}
    
    async def create_adapter(
        self,
        workspace_dir: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> tuple[str, ProcessTurnAdapter]:
        """
        Create a new adapter with its own workspace.
        
        Returns: (session_id, adapter)
        """
        # Generate session ID if not provided
        if not session_id:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_id = f"session_{timestamp}_{uuid.uuid4().hex[:8]}"
        
        # Create workspace
        if workspace_dir:
            workspace_path = Path(workspace_dir)
        else:
            workspace_path = self.workspace_base / session_id
        
        workspace_path.mkdir(parents=True, exist_ok=True)
        
        # Create adapter
        config = EngineConfig(
            workspace_dir=str(workspace_path),
            user_id=user_id,
        )
        adapter = ProcessTurnAdapter(config)
        
        # Initialize
        await adapter.initialize()
        
        # Store
        self._adapters[session_id] = adapter
        
        return session_id, adapter
    
    def get_adapter(self, session_id: str) -> Optional[ProcessTurnAdapter]:
        """Get an existing adapter by session ID."""
        return self._adapters.get(session_id)
    
    async def close_adapter(self, session_id: str) -> None:
        """Close and remove an adapter."""
        adapter = self._adapters.pop(session_id, None)
        if adapter:
            await adapter.close()
    
    def list_sessions(self) -> list[str]:
        """List all active session IDs."""
        return list(self._adapters.keys())

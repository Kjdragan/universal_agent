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
import os
import time
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

from universal_agent.agent_core import AgentEvent, EventType
from universal_agent.identity import resolve_user_id

logger = logging.getLogger(__name__)


@dataclass
class EngineConfig:
    """Configuration for the execution engine."""
    workspace_dir: str
    user_id: Optional[str] = None
    force_complex: bool = False
    max_iterations: int = 20
    run_id: Optional[str] = None
    
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
        self._trace: dict[str, Any] = {}
        
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
        )
        
        # Update config with resolved values
        self.config.user_id = user_id
        self.config.workspace_dir = workspace_dir
        
        self._initialized = True
        return self.session_info  # type: ignore
    
    async def execute(self, user_input: str) -> AsyncIterator[AgentEvent]:
        """
        Execute a query and yield AgentEvents.
        
        This wraps process_turn() and emits events based on the execution.
        For real-time streaming, we use a callback mechanism.
        """
        if not self._initialized:
            await self.initialize()
        
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
            data={"status": "processing", "query": user_input[:100]},
        )
        
        # Import here to avoid circular imports
        from claude_agent_sdk.client import ClaudeSDKClient
        from universal_agent.main import process_turn, budget_state
        
        # Initialize budget state for gateway execution if missing
        if "start_ts" not in budget_state:
            budget_state["start_ts"] = time.time()
        if "steps" not in budget_state:
            budget_state["steps"] = 0
        if "tool_calls" not in budget_state:
            budget_state["tool_calls"] = 0
        
        start_ts = time.time()
        
        # Create event callback for real-time streaming
        event_queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        
        def event_callback(event: AgentEvent) -> None:
            """Callback to capture events from process_turn."""
            try:
                # print(f"DEBUG ProcessTurnAdapter: event_callback received type={event.type}")
                event_queue.put_nowait(event)
            except Exception:
                pass
        
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

        # Run process_turn with event callback
        async with ClaudeSDKClient(self._options) as client:
            # Create task to run process_turn
            result_holder: dict[str, Any] = {}
            
            async def run_engine():
                try:
                    result = await process_turn(
                        client=client,
                        user_input=user_input,
                        workspace_dir=self.config.workspace_dir,
                        force_complex=self.config.force_complex,
                        max_iterations=self.config.max_iterations,
                        event_callback=event_callback,
                    )
                    result_holder["result"] = result
                    result_holder["success"] = True
                except Exception as e:
                    result_holder["error"] = str(e)
                    result_holder["success"] = False
                finally:
                    # Signal completion
                    await event_queue.put(AgentEvent(
                        type=EventType.STATUS,
                        data={"status": "engine_complete"},
                    ))
            
            # Start engine task
            engine_task = asyncio.create_task(run_engine())
            max_runtime_s = float(os.getenv("UA_PROCESS_TURN_TIMEOUT_SECONDS", "0") or 0)
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
                    
                    # print(f"DEBUG ProcessTurnAdapter: yielding event type={event.type}")
                    yield event
                    
                except asyncio.TimeoutError:
                    # Check if engine task is done
                    if engine_task.done():
                        # Drain remaining events
                        while not event_queue.empty():
                            event = event_queue.get_nowait()
                            if event.data.get("status") != "engine_complete":
                                yield event
                        break
                    continue
            
            # Wait for engine to complete
            if not engine_task.done():
                await engine_task
        
        # Emit results from the execution
        if result_holder.get("success"):
            result = result_holder.get("result")
            if result:
                # Emit tool call summary
                if hasattr(result, 'tool_calls') and result.tool_calls:
                    yield AgentEvent(
                        type=EventType.STATUS,
                        data={
                            "status": "tools_complete",
                            "tool_count": result.tool_calls,
                        },
                    )
                
                # Emit final response text
                if hasattr(result, 'response_text') and result.response_text:
                    yield AgentEvent(
                        type=EventType.TEXT,
                        data={"text": result.response_text},
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
                
                # Emit completion
                yield AgentEvent(
                    type=EventType.ITERATION_END,
                    data={
                        "status": "complete",
                        "duration_seconds": round(time.time() - start_ts, 2),
                        "tool_calls": getattr(result, 'tool_calls', 0),
                        "trace_id": getattr(result, 'trace_id', None),
                    },
                )
        else:
            # Emit error
            yield AgentEvent(
                type=EventType.ERROR,
                data={
                    "message": result_holder.get("error", "Unknown error"),
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
    
    async def close(self) -> None:
        """Clean up resources."""
        self._initialized = False
        self._client = None
        self._options = None
        self._session = None


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

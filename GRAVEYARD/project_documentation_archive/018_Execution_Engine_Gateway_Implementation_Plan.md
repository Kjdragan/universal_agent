---
title: "Execution Engine + Gateway Implementation Plan"
status: approved
last_updated: 2026-01-27
---

# 18. Execution Engine + Gateway Implementation Plan

## Executive Summary

This document provides a **concrete implementation plan** for unifying CLI, Web UI, and Harness execution paths under a single canonical engine (`process_turn`), exposed through a gateway control plane.

**Key Insight:** The CLI path (`main.py:process_turn()`) is battle-tested and stable. The Web UI path (`agent_core.py:UniversalAgent.run_query()`) has diverged. The solution is to make CLI the canonical engine and route all clients through the gateway.

---

## Architecture Overview

### Current State (Problematic)

```
┌─────────────────────────────────────────────────────────────────┐
│                     CURRENT (DIVERGENT)                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   CLI ──────────► main.py:process_turn() ──► Claude SDK        │
│                   (stable, full features)                       │
│                                                                 │
│   Web UI ──────► AgentBridge ──► UniversalAgent.run_query()    │
│                   (different path, timeouts, wrong output dir)  │
│                                                                 │
│   Harness ─────► process_turn() OR gateway (inconsistent)       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Target State (Unified)

```
┌─────────────────────────────────────────────────────────────────┐
│                     TARGET (UNIFIED)                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   CLI ───────┐                                                  │
│              │                                                  │
│   Web UI ────┼──► Gateway ──► ProcessTurnAdapter ──► process_turn()
│              │     (session    (event emission)     (canonical  │
│   Harness ───┤      mgmt)                            engine)    │
│              │                                                  │
│   Remote ────┘                                                  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase 0: Baseline Verification

**Goal:** Establish measurable baseline to confirm divergence and later verify fix.

**Deliverable:** `scripts/baseline_comparison.py`

```python
"""
Baseline Comparison Script

Runs the same prompt through CLI, Web UI, and Harness paths.
Records: tool call count, output paths, completion status, time.
"""

BASELINE_PROMPT = "Write a summary of what you can do to work_products/summary.md"

# Test 1: CLI path
# Test 2: Web UI path (via API)
# Test 3: Harness path

# Compare results and save to baseline_comparison_results.json
```

**Acceptance Criteria:**
- [ ] Script runs all 3 paths with same prompt
- [ ] Records tool_calls, output_path, status, duration
- [ ] Identifies current divergences (expected: output path differs for Web UI)

---

### Phase 1: Create ProcessTurnAdapter

**Goal:** Wrap `process_turn()` to emit `AgentEvent` objects compatible with the gateway.

**File:** `src/universal_agent/execution_engine.py` (new)

**Design:**

```python
"""
Execution Engine Adapter

Wraps the CLI's process_turn() to emit AgentEvents for gateway consumption.
This is the bridge between the stable CLI engine and the event-driven gateway.
"""

from dataclasses import dataclass
from typing import AsyncIterator, Optional, Any, Callable
import asyncio

from universal_agent.agent_core import AgentEvent, EventType
from universal_agent.main import process_turn, setup_session, ClaudeSDKClient


@dataclass
class EngineConfig:
    """Configuration for the execution engine."""
    workspace_dir: str
    user_id: str
    force_complex: bool = False
    max_iterations: int = 20


class ProcessTurnAdapter:
    """
    Adapts process_turn() to the gateway's event-streaming interface.
    
    This allows the gateway to use the battle-tested CLI engine while
    providing a consistent event stream to all clients.
    """
    
    def __init__(self, config: EngineConfig):
        self.config = config
        self._event_queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._client: Optional[ClaudeSDKClient] = None
        self._options: Optional[Any] = None
        self._initialized = False
    
    async def initialize(self) -> None:
        """Initialize the execution engine (mirrors CLI setup_session)."""
        if self._initialized:
            return
        
        # Use the CLI's setup_session to ensure identical initialization
        self._options, session, user_id, workspace_dir, trace = await setup_session(
            workspace_dir_override=self.config.workspace_dir,
        )
        
        self._client = ClaudeSDKClient(self._options)
        await self._client.__aenter__()
        self._initialized = True
    
    async def execute(self, user_input: str) -> AsyncIterator[AgentEvent]:
        """
        Execute a query and yield AgentEvents.
        
        This wraps process_turn() and converts its output to events.
        """
        if not self._initialized:
            await self.initialize()
        
        # Emit status event
        yield AgentEvent(type=EventType.STATUS, data={"status": "processing"})
        
        # Run process_turn in a way that captures events
        # Strategy: Hook into the trace/tool_calls and emit events
        result = await process_turn(
            client=self._client,
            user_input=user_input,
            workspace_dir=self.config.workspace_dir,
            force_complex=self.config.force_complex,
            max_iterations=self.config.max_iterations,
        )
        
        # Emit tool call events from result
        if hasattr(result, 'tool_breakdown'):
            for tc in result.tool_breakdown:
                yield AgentEvent(
                    type=EventType.TOOL_CALL,
                    data={"name": tc["name"], "time_offset": tc["time_offset"]},
                )
        
        # Emit final text
        if result.response_text:
            yield AgentEvent(
                type=EventType.TEXT,
                data={"text": result.response_text},
            )
        
        # Emit work products
        if result.workspace_path:
            yield AgentEvent(
                type=EventType.WORK_PRODUCT,
                data={"workspace_path": result.workspace_path},
            )
    
    async def close(self) -> None:
        """Clean up resources."""
        if self._client:
            await self._client.__aexit__(None, None, None)
```

**Key Implementation Notes:**

1. **Real-Time Events:** The above is simplified. For true real-time streaming, we need to hook into `run_conversation()` to emit events as they happen. Options:
   - **Option A (Recommended):** Add an `event_callback` parameter to `process_turn()` and `run_conversation()`
   - **Option B:** Use a shared queue that `run_conversation` pushes to

2. **Signature Change to process_turn:**
```python
async def process_turn(
    client: ClaudeSDKClient,
    user_input: str,
    workspace_dir: str,
    force_complex: bool = False,
    execution_session: Optional[ExecutionSession] = None,
    max_iterations: int = 20,
    event_callback: Optional[Callable[[AgentEvent], None]] = None,  # NEW
) -> ExecutionResult:
```

**Acceptance Criteria:**
- [ ] `ProcessTurnAdapter` class exists
- [ ] Can be instantiated with workspace_dir and user_id
- [ ] `execute()` yields AgentEvent objects
- [ ] Uses the exact same code path as CLI

---

### Phase 2: Rewire InProcessGateway

**Goal:** Replace `AgentBridge.run_query()` with `ProcessTurnAdapter.execute()`.

**File:** `src/universal_agent/gateway.py`

**Changes:**

```python
class InProcessGateway(Gateway):
    def __init__(
        self,
        hooks: Optional[dict] = None,
    ):
        # REMOVED: self._bridge = AgentBridge(hooks=hooks)
        self._adapters: dict[str, ProcessTurnAdapter] = {}
        self._hooks = hooks

    async def create_session(
        self, user_id: str, workspace_dir: Optional[str] = None
    ) -> GatewaySession:
        # Create workspace directory
        if workspace_dir:
            workspace_path = Path(workspace_dir).resolve()
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_id = f"session_{timestamp}_{uuid.uuid4().hex[:8]}"
            workspace_path = Path("AGENT_RUN_WORKSPACES") / session_id
        
        workspace_path.mkdir(parents=True, exist_ok=True)
        session_id = workspace_path.name
        
        # Create adapter for this session
        config = EngineConfig(
            workspace_dir=str(workspace_path),
            user_id=user_id,
        )
        adapter = ProcessTurnAdapter(config)
        await adapter.initialize()
        
        self._adapters[session_id] = adapter
        
        return GatewaySession(
            session_id=session_id,
            user_id=user_id,
            workspace_dir=str(workspace_path),
        )

    async def execute(
        self, session: GatewaySession, request: GatewayRequest
    ) -> AsyncIterator[AgentEvent]:
        adapter = self._adapters.get(session.session_id)
        if not adapter:
            raise RuntimeError(f"No adapter for session: {session.session_id}")
        
        async for event in adapter.execute(request.user_input):
            yield event
```

**Acceptance Criteria:**
- [ ] `InProcessGateway` no longer uses `AgentBridge`
- [ ] Uses `ProcessTurnAdapter` instead
- [ ] Session creation creates proper workspace directories
- [ ] Event streaming works end-to-end

---

### Phase 3: Workspace Path Enforcement

**Goal:** Add pre-tool guardrail ensuring file operations stay inside workspace.

**File:** `src/universal_agent/guardrails/workspace_guard.py` (new)

```python
"""
Workspace Path Guardrail

Ensures all file operations are scoped to the session workspace.
Prevents cross-workspace writes that caused Web UI divergence.
"""

from pathlib import Path
from typing import Optional


class WorkspaceGuardError(Exception):
    """Raised when a path escapes the workspace boundary."""
    pass


def enforce_workspace_path(
    file_path: str,
    workspace_root: Path,
    allow_reads_outside: bool = False,
) -> Path:
    """
    Validate and resolve a file path within workspace boundaries.
    
    Args:
        file_path: The path to validate
        workspace_root: The session workspace root
        allow_reads_outside: If True, allow reads from outside (e.g., /tmp)
    
    Returns:
        Resolved absolute path within workspace
    
    Raises:
        WorkspaceGuardError: If path escapes workspace
    """
    resolved = Path(file_path).expanduser().resolve()
    root = workspace_root.resolve()
    
    # Check if path is inside workspace
    try:
        resolved.relative_to(root)
        return resolved
    except ValueError:
        if allow_reads_outside:
            return resolved
        raise WorkspaceGuardError(
            f"Path '{file_path}' resolves outside workspace '{root}'. "
            "All writes must be inside the session workspace."
        )


def workspace_scoped_path(file_path: str, workspace_root: Path) -> Path:
    """
    Convert a relative path to an absolute workspace-scoped path.
    
    If path is relative, makes it relative to workspace_root.
    If path is absolute, validates it's inside workspace.
    """
    path = Path(file_path)
    
    if not path.is_absolute():
        # Relative path → make it inside workspace
        return (workspace_root / path).resolve()
    
    # Absolute path → validate it's inside workspace
    return enforce_workspace_path(file_path, workspace_root)
```

**Integration Point:** Add to `hooks.py` as a pre-tool hook:

```python
# In AgentHookSet
def _pre_tool_workspace_guard(self, tool_name: str, tool_input: dict) -> dict:
    """Ensure file paths are workspace-scoped."""
    from universal_agent.guardrails.workspace_guard import workspace_scoped_path
    
    path_keys = ["path", "file_path", "filepath", "destination", "output_path"]
    workspace = Path(self.active_workspace)
    
    for key in path_keys:
        if key in tool_input:
            original = tool_input[key]
            scoped = workspace_scoped_path(original, workspace)
            tool_input[key] = str(scoped)
    
    return tool_input
```

**Acceptance Criteria:**
- [ ] `WorkspaceGuard` class exists
- [ ] Relative paths are resolved inside workspace
- [ ] Absolute paths outside workspace raise error
- [ ] Hook is registered and active

---

### Phase 4: Session Lifecycle Unification

**Goal:** Make gateway the single entry point for session creation.

**Changes:**

1. **CLI Mode A (Direct):** Keep for dev/debug, uses `process_turn` directly
2. **CLI Mode B (Gateway):** CLI becomes a client to `InProcessGateway`
3. **Web UI:** Uses gateway (already the case, now with correct engine)

**File:** `src/universal_agent/main.py`

Add gateway CLI mode:

```python
async def main_via_gateway(args: argparse.Namespace):
    """Run CLI through the gateway (unified mode)."""
    from universal_agent.gateway import InProcessGateway, GatewayRequest
    
    gateway = InProcessGateway()
    
    # Create or resume session
    if args.resume and args.run_id:
        session = await gateway.resume_session(args.run_id)
    else:
        session = await gateway.create_session(
            user_id=resolve_user_id(),
            workspace_dir=args.workspace,
        )
    
    print(f"Session: {session.session_id}")
    print(f"Workspace: {session.workspace_dir}")
    
    # Interactive loop
    while True:
        user_input = input("\nYou: ").strip()
        if not user_input or user_input.lower() in ("exit", "quit"):
            break
        
        request = GatewayRequest(user_input=user_input)
        async for event in gateway.execute(session, request):
            _print_event(event)


def _print_event(event: AgentEvent):
    """Print event to console in CLI format."""
    if event.type == EventType.TEXT:
        print(event.data.get("text", ""), end="", flush=True)
    elif event.type == EventType.TOOL_CALL:
        print(f"\n🔧 {event.data.get('name')}")
    elif event.type == EventType.STATUS:
        print(f"⏳ {event.data.get('status')}")
    elif event.type == EventType.ERROR:
        print(f"❌ {event.data.get('error')}")
```

**Acceptance Criteria:**
- [ ] `--use-gateway` flag activates gateway mode
- [ ] CLI can run through gateway
- [ ] Session creation is identical to Web UI path

---

### Phase 5: Event Parity Verification

**Goal:** Verify Web UI receives same events as CLI.

**Checklist:**

| Event Type | CLI Shows | Web UI Shows | Parity |
|------------|-----------|--------------|--------|
| `STATUS` | ⏳ processing | status bar | ☐ |
| `TEXT` | streamed text | chat bubble | ☐ |
| `TOOL_CALL` | 🔧 tool_name | tool panel | ☐ |
| `TOOL_RESULT` | result preview | tool panel | ☐ |
| `THINKING` | (hidden) | thinking indicator | ☐ |
| `WORK_PRODUCT` | 📁 path | file browser | ☐ |
| `ERROR` | ❌ message | error toast | ☐ |

**Test Script:** `scripts/event_parity_test.py`

---

### Phase 6: Regression Test

**Goal:** "Same prompt → same behavior" across all entry points.

**Test Case:**

```python
REGRESSION_PROMPT = "Create a file called test.txt in work_products with the text 'hello world'"

# Expected behavior (all entry points):
# 1. Tool call: Write (or equivalent)
# 2. Output path: {workspace}/work_products/test.txt
# 3. File content: "hello world"
# 4. Status: success
```

**Acceptance Criteria:**
- [ ] CLI produces `{workspace}/work_products/test.txt`
- [ ] Web UI produces `{workspace}/work_products/test.txt`
- [ ] Harness produces `{workspace}/work_products/test.txt`
- [ ] All three have identical file content

---

## File Change Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `src/universal_agent/execution_engine.py` | **NEW** | ProcessTurnAdapter |
| `src/universal_agent/gateway.py` | **MODIFY** | Use ProcessTurnAdapter |
| `src/universal_agent/guardrails/workspace_guard.py` | **NEW** | Path enforcement |
| `src/universal_agent/hooks.py` | **MODIFY** | Add workspace guard hook |
| `src/universal_agent/main.py` | **MODIFY** | Add gateway CLI mode |
| `src/universal_agent/api/agent_bridge.py` | **DEPRECATE** | No longer used by gateway |
| `scripts/baseline_comparison.py` | **NEW** | Test script |
| `scripts/event_parity_test.py` | **NEW** | Test script |

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Breaking existing CLI | Keep direct mode (Mode A) as default |
| Breaking Web UI | Incremental rollout behind feature flag |
| Performance regression | Baseline measurements before/after |
| Event timing differences | Use same event queue mechanism |

---

## Success Metrics

1. **Behavioral Parity:** Same prompt produces same output path across CLI/WebUI/Harness
2. **No Regressions:** All existing tests pass
3. **Reduced Code Paths:** AgentBridge execution path removed
4. **Measurable:** Baseline comparison shows identical behavior

---

## Recommended Execution Order

1. **Phase 0** first — establishes baseline
2. **Phase 1** — core adapter (can be developed in isolation)
3. **Phase 3** — workspace guard (independent, low risk)
4. **Phase 2** — rewire gateway (depends on Phase 1)
5. **Phase 4** — CLI gateway mode (depends on Phase 2)
6. **Phase 5 & 6** — verification (after all implementation)

---

## Decision Points for Review

Before proceeding, confirm:

- [x] CLI `process_turn` is the canonical engine (confirmed via code analysis)
- [x] Gateway should host that engine (confirmed via architecture review)
- [x] All UIs become clients (confirmed via design)
- [ ] Keep direct CLI mode for dev (recommended, pending user confirmation)

---

*Document created: 2026-01-27*
*Based on: 016_Execution_Engine_Gateway_Model.md, 017_Development_Plan.md, codebase analysis*

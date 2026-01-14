# URW Integration Guide for Universal Agent System

This document provides specific instructions for integrating URW (Universal Ralph Wrapper) with the existing Universal Agent multi-agent system.

## Executive Summary

URW is an **outer loop harness** (NOT another agent) that wraps your existing multi-agent system to enable:

1. **Long-running tasks** that exceed a single context window
2. **Deterministic state persistence** that survives context resets
3. **Automatic task decomposition** for complex requests
4. **Multi-strategy completion evaluation** for fuzzy "done" criteria
5. **Guardrails** that prevent repeating failed approaches

**Key insight**: URW doesn't replace your agent system—it orchestrates it, providing the persistence layer and outer loop that enables unbounded task duration.

---

## Architecture Integration Points

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              URW LAYER                                      │
│  ┌─────────────┐  ┌──────────────────┐  ┌────────────────┐  ┌───────────┐  │
│  │   Plan      │  │  State Manager   │  │   Evaluator    │  │Orchestrator│  │
│  │  Manager    │  │  (SQLite+Git)    │  │  (LLM Judge)   │  │  (Loop)   │  │
│  └─────────────┘  └──────────────────┘  └────────────────┘  └───────────┘  │
│                              │                                              │
│                              │ AgentLoopInterface                           │
│                              ▼                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                     YOUR EXISTING SYSTEM                                    │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Universal Agent                                   │   │
│  │  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────────┐  │   │
│  │  │  Primary    │  │  Sub-agents  │  │    Composio Tool Router    │  │   │
│  │  │   Agent     │  │  (spawned)   │  │       (500+ tools)         │  │   │
│  │  │  (Sonnet 4) │  │              │  │                            │  │   │
│  │  └─────────────┘  └──────────────┘  └────────────────────────────┘  │   │
│  │                                                                      │   │
│  │  Note: Letta Memory Blocks can still be used for IN-SESSION context │   │
│  │  URW handles CROSS-SESSION persistence                              │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## The Contract: AgentLoopInterface

Your system must implement this interface:

```python
from urw_package import AgentExecutionResult
from pathlib import Path

class AgentLoopInterface(Protocol):
    async def execute_task(self, 
                          task: Task, 
                          context: str,
                          workspace_path: Path) -> AgentExecutionResult:
        """
        Execute a single task with fresh context.
        
        REQUIREMENTS:
        1. Create a NEW agent instance (fresh context window)
        2. Inject the provided `context` string into the agent
        3. Execute until completion or failure
        4. Extract structured results
        5. Return AgentExecutionResult
        
        The agent MUST NOT have access to any state from previous calls.
        All relevant history is in the `context` parameter.
        """
        ...
    
    async def cancel(self):
        """Cancel any running execution."""
        ...
```

### AgentExecutionResult Structure

```python
@dataclass
class AgentExecutionResult:
    # Core outcome
    success: bool                    # Did the agent believe it completed the task?
    output: str                      # Agent's final output/summary
    error: Optional[str] = None      # Error message if failed
    
    # Artifacts produced (files created)
    artifacts_produced: List[Dict] = []
    # Format: [{"path": "report.md", "type": "file", "metadata": {...}}, ...]
    
    # Side effects (non-idempotent actions)
    side_effects: List[Dict] = []
    # Format: [{"type": "email_sent", "key": "unique_id", "details": {...}}, ...]
    
    # Learnings for future iterations
    learnings: List[str] = []
    # Format: ["API X has rate limit of 100/hour", "Source Y is outdated", ...]
    
    # Failed approaches (for guardrails)
    failed_approaches: List[Dict] = []
    # Format: [{"approach": "Direct scraping", "why_failed": "Rate limited"}, ...]
    
    # Metrics
    context_tokens_used: int = 0
    tools_invoked: List[str] = []
    execution_time_seconds: float = 0
```

---

## Implementation Steps

### Step 1: Create the Adapter

Create a file `urw_adapter.py` in your project:

```python
"""
URW Adapter for Universal Agent System
"""

import asyncio
from pathlib import Path
from typing import Any, Dict, List

from urw_package import BaseAgentAdapter, AgentExecutionResult, Task


class UniversalAgentURWAdapter(BaseAgentAdapter):
    """
    Adapter connecting URW orchestrator to Universal Agent system.
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.artifacts_dir = None
    
    async def _create_agent(self) -> Any:
        """
        Create a FRESH agent instance.
        
        CRITICAL: This must return a NEW agent every time.
        """
        # TODO: Replace with your actual agent creation
        return {'model': self.config.get('model'), 'fresh': True}
    
    async def _run_agent(self, agent: Any, prompt: str,
                        workspace_path: Path) -> AgentExecutionResult:
        """
        Run the agent on the given prompt.
        """
        self.artifacts_dir = workspace_path / '.urw' / 'artifacts'
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        
        # TODO: Replace with your actual agent execution
        return AgentExecutionResult(
            success=True,
            output=f"[PLACEHOLDER] Implement real agent execution",
            artifacts_produced=[],
            learnings=["Placeholder - implement real agent"],
        )
```

### Step 2: Create the Runner

```python
"""run_urw.py - URW Runner"""

import asyncio
from pathlib import Path
from anthropic import Anthropic
from urw_package import URWOrchestrator, URWConfig
from urw_adapter import UniversalAgentURWAdapter


async def main():
    adapter = UniversalAgentURWAdapter({
        'anthropic_api_key': 'your-key',
        'model': 'claude-sonnet-4-20250514',
    })
    
    client = Anthropic()
    
    orchestrator = URWOrchestrator(
        agent_loop=adapter,
        llm_client=client,
        workspace_path=Path("./urw_workspace"),
        config=URWConfig(max_iterations_per_task=15, verbose=True),
    )
    
    result = await orchestrator.run("Research quantum computing and write a report")
    print(f"Result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## Context Injection Details

When URW calls your adapter, it provides a `context` string containing:

```markdown
## Plan Status
- Complete: 2 | In Progress: 1 | Pending: 3 | Blocked: 0

## Your Current Task
**Research Quantum Computing Companies**

Research and compile information about the top 5 quantum computing companies...

**Success Criteria:**
- Binary checks: file_exists:research_notes.md
- Constraint: min_length = 2000
- Qualitative: Is the research comprehensive?

## Available Inputs (from completed tasks)
- `/workspace/.urw/artifacts/scope_document.md` (file)

## Key Learnings (Apply These)
- Google Scholar provides more authoritative sources
- Company press releases contain recent achievements

## Failed Approaches (DO NOT REPEAT)
- **Direct web scraping**: Rate limited after 10 requests
- **Using Wikipedia as primary source**: Information outdated

## Iteration Budget
- Used: 2 / 15 iterations for this task
```

---

## Handling Sub-Agents

Sub-agents WITHIN a single `execute_task()` call are fine:

```python
async def _run_agent(self, agent, prompt, workspace_path):
    # OK: Sub-agents within one iteration
    research_result = await agent.spawn_subagent(task="Research")
    writing_result = await agent.spawn_subagent(task="Write", context=research_result)
    return AgentExecutionResult(success=True, output=writing_result.text)
```

NOT allowed - persisting agents between iterations:

```python
# BAD - don't do this
class BadAdapter:
    def __init__(self):
        self.persistent_agent = Agent()  # Remembers previous calls
```

---

## Side Effect Tracking (Idempotency)

Record non-idempotent actions:

```python
effects.append({
    "type": "email_sent",
    "key": f"email_{recipient}_{subject[:20]}",  # Unique key
    "details": {"to": recipient, "subject": subject}
})
```

URW uses the `key` for idempotency—won't re-execute on resume.

---

## Questions for Integration

1. **Agent Instantiation**: How does your system create agent instances?
2. **Tool Access**: Passed at creation or discovered dynamically?
3. **Output Parsing**: Structured result object or parse final message?
4. **Sub-Agent Results**: How do results flow to primary agent?
5. **File Handling**: Where does agent write files by default?

---

## Next Steps

1. Copy `urw_package/` to your project
2. Create `urw_adapter.py` with your agent integration
3. Test with `MockAgentAdapter` first
4. Implement real adapter incrementally
5. Run end-to-end test with simple task

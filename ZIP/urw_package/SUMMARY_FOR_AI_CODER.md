# URW System Summary for AI Coder Integration

**Document Purpose**: Quick reference for integrating Universal Ralph Wrapper (URW) with the existing Universal Agent system.

---

## What Is URW?

URW is a **programmatic harness** (not an agent) that enables long-running tasks by:

1. Breaking requests into atomic tasks
2. Executing each task with a fresh agent instance
3. Persisting state between context window resets
4. Evaluating completion using multiple strategies
5. Learning from failures to avoid repeating mistakes

**Think of it as**: A bash loop that spawns your agent, checks if done, records progress, and repeats—but for universal tasks, not just code.

---

## Key Files

| File | Purpose | You Need To |
|------|---------|-------------|
| `urw_state.py` | SQLite + Git state management | Understand, not modify |
| `urw_decomposer.py` | Breaks requests into tasks | Extend templates if needed |
| `urw_evaluator.py` | Determines if tasks are done | Configure criteria per task |
| `urw_orchestrator.py` | The main loop | Call from your system |
| `urw_integration.py` | Agent adapters | **Implement your adapter here** |

---

## The One Thing You Must Implement

```python
class YourAdapter(BaseAgentAdapter):
    async def _create_agent(self) -> Any:
        """Return a FRESH agent instance. No reuse."""
        return YourAgent(config=self.config)
    
    async def _run_agent(self, agent, prompt, workspace_path) -> AgentExecutionResult:
        """Run agent, extract results."""
        result = await agent.run(prompt)
        return AgentExecutionResult(
            success=result.success,
            output=result.output,
            artifacts_produced=[...],  # Files created
            side_effects=[...],        # Emails sent, APIs called
            learnings=[...],           # Insights for future
            failed_approaches=[...],   # What didn't work
        )
```

---

## Critical Requirement: Fresh Context

**EVERY call to `execute_task()` must use a fresh agent instance.**

The orchestrator provides ALL context via the `context` parameter. Your agent should NOT have memory of previous calls—URW handles that.

```python
# WRONG
def execute_task(self, ...):
    return self.persistent_agent.run(prompt)  # Agent remembers previous calls

# RIGHT  
def execute_task(self, ...):
    agent = create_new_agent()  # Fresh each time
    result = agent.run(prompt)
    return result
```

---

## State Structure

```
.urw/
├── state.db           # SQLite: tasks, artifacts, iterations, failed approaches
├── task_plan.json     # Current task graph (human-readable)
├── progress.md        # Status summary (human-readable)
├── guardrails.md      # Failed approaches (human-readable)
├── iterations/        # Log of each iteration
│   ├── 001_started.json
│   └── 001_complete.json
├── artifacts/         # Task outputs
│   └── research_notes.md
└── .git/              # Git tracks everything for rollback
```

---

## Context Injection

When URW calls your adapter, it provides context like:

```markdown
## Plan Status
Complete: 2 | In Progress: 1 | Pending: 3

## Your Current Task
**Research Quantum Computing**
Find information about top 5 companies...

## Available Inputs (from completed tasks)
- `artifacts/scope.md` - from: Define Scope

## Key Learnings
- Google Scholar provides better sources
- Press releases have recent achievements

## Failed Approaches (DO NOT REPEAT)
- **Web scraping**: Rate limited
- **Wikipedia**: Outdated

## Iteration Budget
Used: 2 / 15 iterations
```

Your agent reads this and works accordingly.

---

## Evaluation Strategies

| Type | How It Works | Example |
|------|--------------|---------|
| **Binary** | File exists? API returned 200? | `file_exists:report.md` |
| **Constraint** | Min length? Contains text? | `min_length: 1000` |
| **Qualitative** | LLM judges quality | "Is analysis comprehensive?" |
| **Composite** | All of the above | Default for most tasks |

---

## Side Effect Tracking (Idempotency)

Record non-idempotent actions so they're not repeated on resume:

```python
side_effects.append({
    "type": "email_sent",
    "key": f"email_{recipient}_{subject[:20]}",  # Unique key
    "details": {"to": recipient, "subject": subject}
})
```

---

## Integration Checklist

- [ ] Create adapter class extending `BaseAgentAdapter`
- [ ] Implement `_create_agent()` returning fresh instances
- [ ] Implement `_run_agent()` with result extraction
- [ ] Test with `MockAgentAdapter` first
- [ ] Test with real adapter on simple task
- [ ] Tune config (iterations, timeouts) for your use case

---

## Quick Start Code

```python
from urw_package import URWOrchestrator, URWConfig, MockAgentAdapter
from anthropic import Anthropic
from pathlib import Path
import asyncio

async def main():
    # Start with mock to verify URW works
    adapter = MockAgentAdapter({'success_rate': 0.9})
    
    orchestrator = URWOrchestrator(
        agent_loop=adapter,
        llm_client=Anthropic(),
        workspace_path=Path("./workspace"),
        config=URWConfig(verbose=True),
    )
    
    result = await orchestrator.run("Write a report about AI")
    print(result)

asyncio.run(main())
```

---

## Questions to Answer for Integration

1. How does your agent get instantiated currently?
2. Where do artifacts (files) get written?
3. How do you extract tool call history from results?
4. Does your agent have built-in persistence that needs disabling?
5. How do sub-agents return results to primary agent?

---

## Next Steps

1. Review `urw_integration.py` for adapter interface
2. Copy `urw_package/` to your project
3. Run `python examples.py --all` to see URW in action
4. Implement `YourAdapter._create_agent()` and `._run_agent()`
5. Test incrementally

---

## Contact Points in Code

**To execute a task**: `URWOrchestrator.run(request)`

**To check state**: `URWStateManager.get_all_tasks()`, `.get_completion_stats()`

**To resume**: `URWOrchestrator.resume(checkpoint_sha)`

**To customize decomposition**: Extend `DECOMPOSITION_TEMPLATES` in `urw_decomposer.py`

**To customize evaluation**: Set `verification_type`, `binary_checks`, `constraints`, `evaluation_rubric` on Task objects

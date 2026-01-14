# URW Handoff Document

**For:** AI Coder working on Universal Agent  
**From:** Claude (Architecture/Design Session)  
**Date:** January 2026  
**Purpose:** Integration of Universal Ralph Wrapper with existing Universal Agent system

---

## TL;DR

I've designed and implemented a **Universal Ralph Wrapper (URW)** - an outer loop harness that enables your multi-agent system to handle long-running tasks that exceed a single context window. 

**What URW does:** Orchestrates your agents across multiple iterations with persistent state  
**What URW doesn't do:** Replace your agent system - it wraps it  
**What you need to implement:** A single adapter class (~100 lines)

---

## The Problem We're Solving

Your Universal Agent system works great for tasks that fit in one context window. But for complex tasks like "Research quantum computing and write a comprehensive report," the agent needs to:

1. Work across multiple sessions (context resets)
2. Remember what's been done
3. Avoid repeating failed approaches
4. Pick up where it left off after failures

**URW handles all of this while your agent does the actual work.**

---

## What's Been Built

```
urw_package/
├── urw_state.py         # SQLite + Git + Files state management
├── urw_decomposer.py    # Task breakdown (templates + LLM)
├── urw_evaluator.py     # Completion checking (binary + LLM judge)
├── urw_orchestrator.py  # The main loop
├── urw_integration.py   # Agent adapters (including yours)
├── __init__.py          # Package exports
├── README.md            # Full documentation
├── INTEGRATION_GUIDE.md # Detailed integration instructions
└── example_usage.py     # Working example with mock agent
```

Total: ~2,500 lines of Python, fully documented and type-hinted.

---

## How It Works

```
User Request: "Research quantum computing companies"
                    │
                    ▼
┌─────────────────────────────────────────┐
│           URW Orchestrator              │
│                                         │
│  1. Decompose into atomic tasks         │
│  2. For each task:                      │
│     a. Generate context from state      │
│     b. Call YOUR agent (fresh instance) │
│     c. Evaluate completion              │
│     d. Checkpoint to Git                │
│     e. Update SQLite state              │
│  3. Handle failures (retry/replan)      │
│  4. Return final result                 │
└─────────────────────────────────────────┘
                    │
                    │ Each iteration calls
                    ▼
┌─────────────────────────────────────────┐
│         Your Universal Agent            │
│                                         │
│  - Fresh instance each time             │
│  - Context injected via prompt          │
│  - Does actual work (tools, sub-agents) │
│  - Returns structured result            │
└─────────────────────────────────────────┘
```

---

## The One Thing You Need to Implement

An adapter class that connects URW to your agent system:

```python
from urw_package import BaseAgentAdapter, AgentExecutionResult

class UniversalAgentAdapter(BaseAgentAdapter):
    
    async def _create_agent(self):
        """Create FRESH agent instance. Do NOT reuse."""
        # Your code to instantiate a new Primary Agent
        return your_create_agent_function(self.config)
    
    async def _run_agent(self, agent, prompt, workspace_path):
        """Run agent and return structured result."""
        result = await agent.run(prompt)
        
        return AgentExecutionResult(
            success=result.success,
            output=result.output,
            artifacts_produced=[...],   # Files created
            side_effects=[...],         # Emails sent, API calls, etc.
            learnings=[...],            # Insights for future iterations
            failed_approaches=[...],    # What didn't work
            tools_invoked=[...],        # Which tools were used
            context_tokens_used=...,    # Token count
        )
```

That's it. URW handles everything else.

---

## Key Design Decisions

### 1. Fresh Context Every Iteration

**Why:** Context windows fill up. Previous conversation history becomes cruft.

**How:** URW generates a deterministic context string from its state database. Your agent receives ONLY this context - no memory of previous calls.

### 2. SQLite + Git + Files (Not Letta)

**Why:** Letta's memory is conversation-dependent and hard to inspect. We need deterministic, queryable, rollback-able state.

**What you get:**
- `progress.md` - Human-readable status
- `guardrails.md` - Failed approaches (DON'T REPEAT THESE)
- `state.db` - Queryable task graph
- Git history - Checkpoints at every iteration

### 3. Hybrid Evaluation

**Why:** Universal tasks have fuzzy completion ("Is this research good enough?")

**How:** Three strategies combined:
- Binary checks: `file_exists:report.md`
- Constraints: `min_length: 1000`
- LLM-as-judge: "Is the analysis comprehensive?"

### 4. Idempotent Side Effects

**Why:** If we restart from a checkpoint, we don't want to send the same email twice.

**How:** URW tracks side effects by idempotency key. Before executing, check if already done.

---

## Integration Points with Your System

### Composio Tool Router
- Works unchanged - URW doesn't touch tool execution
- URW adds task-level idempotency ON TOP of Composio's tool-level idempotency

### Letta Memory Blocks
- Use for IN-SESSION context (within one `execute_task()` call)
- Do NOT persist Letta state between URW iterations
- URW's state replaces Letta's cross-session memory

### Sub-Agents
- Fine within a single iteration
- Cannot persist between `execute_task()` calls
- URW sees only the Primary Agent's final result

### Agent College
- URW can feed iteration results to Agent College
- Use `URWCallbacks.on_iteration_end` to send data

---

## What URW Context Looks Like

When URW calls your agent, it passes this context:

```markdown
## Plan Status
- Complete: 2 | In Progress: 1 | Pending: 3

## Your Current Task
**Analyze research findings**

Review the gathered research and identify key themes...

**Success Criteria:**
- Binary: file_exists:analysis.md
- Constraint: min_length = 1500
- Qualitative: Are themes clearly identified?

## Available Inputs (from completed tasks)
- `/workspace/.urw/artifacts/research_notes.md` - from: Research Phase

## Key Learnings (Apply These)
- Use multiple sources for verification
- Focus on 2023-2024 data

## Failed Approaches (DO NOT REPEAT)
- **Web scraping directly**: Rate limited
- **Single source reliance**: Data was outdated

## Iteration Budget
- Used: 2 / 10 iterations for this task
```

---

## Testing Without Your Agent

Use the MockAgentAdapter to test URW independently:

```python
from urw_package import URWOrchestrator, MockAgentAdapter

adapter = MockAgentAdapter({
    "success_rate": 0.9,
    "produce_artifacts": True,
})

orchestrator = URWOrchestrator(
    agent_loop=adapter,
    llm_client=anthropic_client,
    workspace_path=Path("./test"),
)

result = await orchestrator.run("Test request")
```

---

## Questions You Might Have

**Q: Do I need to change my agent's core logic?**  
A: No. Just wrap it in the adapter.

**Q: What about token counting?**  
A: Estimate in your adapter. URW uses it for monitoring only.

**Q: Can I use streaming?**  
A: Yes. Handle streaming internally, return final result.

**Q: What if my agent needs human input?**  
A: Within an iteration, that's fine. Between tasks, use `URWCallbacks.on_human_review_required`.

**Q: How do I debug?**  
A: Check `.urw/progress.md`, `.urw/guardrails.md`, or query `.urw/state.db`. Git history shows all checkpoints.

---

## Suggested Integration Steps

1. **Read `INTEGRATION_GUIDE.md`** for full details
2. **Run `example_usage.py`** to see URW in action
3. **Create `urw_adapter.py`** in your project
4. **Implement `_create_agent()`** with your agent creation code
5. **Implement `_run_agent()`** with your agent execution code
6. **Test with a simple request**
7. **Tune configuration** for your task patterns

---

## Files to Start With

1. **`INTEGRATION_GUIDE.md`** - Full integration instructions
2. **`urw_integration.py`** - See `UniversalAgentAdapter` template
3. **`example_usage.py`** - Working example to run

---

## Contact

This design came from a conversation between Kevin and Claude about adapting the Ralph loop pattern for universal (non-code) tasks. The key insight was that universal tasks need fuzzy evaluation and heterogeneous state management, which the original Ralph wasn't designed for.

If there are questions about design decisions or integration challenges, feel free to continue the conversation.

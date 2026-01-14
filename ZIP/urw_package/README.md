# Universal Ralph Wrapper (URW)

A harness for long-running universal agent tasks, inspired by Anthropic's Ralph loop pattern but adapted for non-code tasks.

## Overview

The Ralph loop is Anthropic's pattern for running agents on long tasks that exceed a single context window. It works brilliantly for coding tasks where:
- Completion is binary (tests pass or fail)
- State persists via git history
- Progress is measurable via file changes

**Universal tasks** (research, analysis, communication) lack these properties:
- Completion is fuzzy ("Is this research comprehensive?")
- State is heterogeneous (emails sent, data gathered, reports written)
- Progress requires semantic understanding

URW solves this by providing:
1. **Deterministic state management** (SQLite + Git + Files)
2. **Multi-modal completion evaluation** (binary + constraints + LLM-as-judge)
3. **Dynamic task decomposition** (templates + LLM for novel requests)
4. **Fresh context per iteration** (explicit context injection, not conversation memory)

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    URW ORCHESTRATOR (Programmatic Loop)                 │
│                                                                         │
│   ┌───────────────┐     ┌──────────────────┐     ┌─────────────────┐   │
│   │ Plan Manager  │────▶│ State Manager    │────▶│ Evaluator       │   │
│   │ (Decompose)   │     │ (SQLite+Git)     │     │ (LLM Judge)     │   │
│   └───────────────┘     └──────────────────┘     └─────────────────┘   │
│                                 │                                       │
│                                 │ generate_agent_context()              │
│                                 ▼                                       │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │                    YOUR MULTI-AGENT SYSTEM                      │   │
│   │                    (via AgentLoopInterface)                     │   │
│   │                                                                 │   │
│   │   • Receives: Task + Injected Context                          │   │
│   │   • Executes: With full context window available               │   │
│   │   • Returns: Output + Artifacts + Learnings                    │   │
│   │                                                                 │   │
│   │   CRITICAL: Fresh instance each call. No persistent state.     │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                                 │                                       │
│                                 │ AgentExecutionResult                  │
│                                 ▼                                       │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │              CHECKPOINT & EVALUATE                              │   │
│   │                                                                 │   │
│   │   • Register artifacts                                         │   │
│   │   • Record side effects (idempotency tracking)                 │   │
│   │   • Evaluate completion (binary + constraints + LLM)           │   │
│   │   • Git commit (atomic checkpoint)                             │   │
│   │   • Update progress.md and guardrails.md                       │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                                 │                                       │
│                    ┌────────────┴────────────┐                         │
│                    ▼                         ▼                         │
│              Task Complete?            Task Failed?                    │
│              → Next Task               → Replan/Decompose              │
└─────────────────────────────────────────────────────────────────────────┘
```

## State Management

URW uses a hybrid approach for deterministic, inspectable state:

```
.urw/                              # State directory
├── task_plan.json                 # Current task graph
├── progress.md                    # Human-readable status
├── guardrails.md                  # Failed approaches (DON'T REPEAT)
├── state.db                       # SQLite for structured queries
├── iterations/                    # Iteration logs (append-only)
│   ├── 001_started.json
│   ├── 001_complete.json
│   └── ...
├── artifacts/                     # Task outputs
│   ├── research_notes.md
│   ├── final_report.pdf
│   └── ...
└── .git/                          # Git tracks everything
```

### Why This Hybrid?

| Layer | Purpose | Advantage |
|-------|---------|-----------|
| **SQLite** | Structured queries | "Which tasks depend on X?" "All blocked tasks?" |
| **Git** | Checkpointing | Atomic commits, diffing, rollback, history |
| **Files** | Artifacts & human-readable state | Inspectable, portable, no deserialization |

### Key Tables

```sql
-- Task graph with dependencies
tasks (id, title, status, depends_on, verification_criteria, ...)

-- Side effects for idempotency
side_effects (task_id, effect_type, idempotency_key, details, ...)

-- Iteration log
iterations (iteration, task_id, outcome, learnings, commit_sha, ...)

-- Failed approaches (guardrails)
failed_approaches (task_id, approach, why_failed, ...)
```

## Task Decomposition

URW decomposes user requests into atomic tasks using:

### 1. Template Matching (Fast, Deterministic)

```python
# Built-in templates for common patterns
TEMPLATES = {
    "research_report": [...],   # Research → Analyze → Write
    "email_outreach": [...],    # Targets → Template → Personalize → Send
    "document_analysis": [...], # Ingest → Analyze → Output
}
```

### 2. LLM Decomposition (Flexible, Novel Requests)

For requests that don't match templates, an LLM decomposes into tasks with:
- Clear boundaries
- Verification criteria
- Dependency ordering

### 3. Hybrid (Default)

Template first, LLM fallback.

## Completion Evaluation

URW evaluates task completion using multiple strategies:

### Binary Checks
```python
binary_checks=["file_exists:report.md", "side_effect:email_sent"]
```

### Constraints
```python
constraints=[
    {"type": "min_length", "value": 1000},
    {"type": "contains", "value": "executive summary"},
]
```

### LLM-as-Judge
```python
evaluation_rubric="Is the research comprehensive? Are sources cited? Is the analysis actionable?"
```

### Composite (Default)
Combines all strategies. Binary checks are hard requirements; constraints and LLM scores are weighted.

## Integration

### Implement AgentLoopInterface

```python
from urw_package import BaseAgentAdapter, AgentExecutionResult

class YourAgentAdapter(BaseAgentAdapter):
    async def _create_agent(self) -> Any:
        """Create FRESH agent instance. Do NOT reuse."""
        return YourAgent(config=self.config)
    
    async def _run_agent(self, agent, prompt, workspace_path) -> AgentExecutionResult:
        """Run agent, extract structured results."""
        result = await agent.run(prompt)
        
        return AgentExecutionResult(
            success=result.success,
            output=result.output,
            artifacts_produced=[{"path": "output.md", "type": "file"}],
            side_effects=[{"type": "email_sent", "key": "email_123", "details": {...}}],
            learnings=["Key insight discovered"],
            failed_approaches=[{"approach": "...", "why_failed": "..."}],
            tools_invoked=["web_search", "file_write"],
            context_tokens_used=result.token_count,
        )
```

### Run the Orchestrator

```python
from urw_package import URWOrchestrator, URWConfig
from anthropic import Anthropic

adapter = YourAgentAdapter(config={...})
client = Anthropic()

orchestrator = URWOrchestrator(
    agent_loop=adapter,
    llm_client=client,
    workspace_path=Path("./workspace"),
    config=URWConfig(
        max_iterations_per_task=15,
        enable_dynamic_replanning=True,
    )
)

result = await orchestrator.run("Research quantum computing developments and write a comprehensive report")
```

## Configuration

```python
@dataclass
class URWConfig:
    # Iteration limits
    max_iterations_per_task: int = 15    # Per task
    max_total_iterations: int = 200       # Global
    max_consecutive_failures: int = 3     # Before replan
    
    # Completion thresholds
    min_completion_confidence: CompletionConfidence = MEDIUM
    
    # Re-planning
    enable_dynamic_replanning: bool = True
    require_human_approval_for_replan: bool = False
    
    # Timeouts (seconds)
    task_timeout: int = 3600              # 1 hour per task
    iteration_timeout: int = 600          # 10 min per iteration
    
    # Behavior
    pause_on_blockers: bool = True
    auto_decompose_failed_tasks: bool = True
```

## Callbacks

```python
callbacks = URWCallbacks(
    on_task_start=lambda task, iter: print(f"Starting: {task.title}"),
    on_task_complete=lambda task, eval: print(f"Done: {task.title}"),
    on_progress=lambda msg: log.info(msg),
    on_human_review_required=lambda task, eval: human_review(task),
)
```

## Key Concepts

### 1. Fresh Context Every Iteration

**CRITICAL**: Each call to your agent system must use a fresh instance with clean context. The only context available is what URW explicitly injects.

```python
# WRONG - reusing agent
self.persistent_agent.run(prompt)

# RIGHT - fresh agent each time
agent = create_fresh_agent()
result = agent.run(prompt)
# agent is discarded after
```

### 2. Atomic Tasks

Tasks should complete fully or fail cleanly. No "60% done" states. If a task is too big, decompose it.

### 3. Artifacts as Handoff

Inter-task communication happens via files, not conversation memory. Task B reads Task A's output file, not Task A's conversation history.

### 4. Guardrails Prevent Loops

Failed approaches are recorded and injected into subsequent iterations:

```markdown
## Failed Approaches (DO NOT REPEAT)
- **Direct API scraping**: Rate limited after 10 requests
- **Using source X**: Data was outdated (pre-2023)
```

### 5. Git as Time Machine

Every iteration creates a checkpoint. You can:
- Roll back to any point: `git checkout <sha> -- .urw/`
- See what changed: `git diff <sha1> <sha2>`
- Branch for experiments: `git checkout -b experiment`

## Comparison: Ralph vs URW

| Aspect | Ralph (Code) | URW (Universal) |
|--------|--------------|-----------------|
| **Task Definition** | Static PRD.json | Dynamic decomposition |
| **Completion Check** | Tests pass/fail | Binary + Constraints + LLM |
| **State Storage** | Git + progress.txt | SQLite + Git + Files |
| **Artifact Types** | Code files | Any (files, emails, data) |
| **Re-planning** | Manual | Automatic on failure |
| **Idempotency** | Git handles | Explicit side effect tracking |

## Files in This Package

```
urw_package/
├── __init__.py           # Package exports
├── urw_state.py          # State management (SQLite + Git)
├── urw_decomposer.py     # Task decomposition
├── urw_evaluator.py      # Completion evaluation
├── urw_orchestrator.py   # Main loop
├── urw_integration.py    # Agent adapters
├── README.md             # This file
└── INTEGRATION_GUIDE.md  # Detailed integration guide
```

## Next Steps for Integration

1. **Review `urw_integration.py`** for the AgentLoopInterface contract
2. **Implement your adapter** following the examples
3. **Test with MockAgentAdapter** to verify URW behavior
4. **Integrate with your actual system** incrementally
5. **Tune configuration** based on your task patterns

## Dependencies

- Python 3.10+
- anthropic (for LLM decomposition and evaluation)
- git (for checkpointing)

Optional:
- claude-agent-sdk (if using ClaudeAgentSDKAdapter)

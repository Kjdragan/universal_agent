# Long-Running Harness Development - Knowledge Base

This document is the **source of truth** for knowledge accumulated during development of Universal Agent's long-running harness capabilities. It is designed to persist across multiple chat sessions.

**Last Updated**: 2026-01-05 17:10 CST  
**Status**: Stress Testing Complete - Failure Point Identified

---

## Table of Contents

1. [Project Goal](#project-goal)
2. [Key Discoveries](#key-discoveries)
3. [Universal Agent Current State](#universal-agent-current-state)
4. [Anthropic Harness Patterns](#anthropic-harness-patterns)
5. [Design Decisions](#design-decisions)
6. [Open Questions](#open-questions)
7. [Next Steps](#next-steps)
8. [File References](#file-references)

---

## Project Goal

Enable Universal Agent to handle tasks that:
- Run for 1 hour to 24+ hours
- Survive context window exhaustion
- Continue across multiple sessions without losing progress
- Avoid duplicate side effects on restart

### Three-Mode System (Proposed)

| Mode | Trigger | Description |
|------|---------|-------------|
| **SIMPLE** | Direct inference | Single LLM call, no tools |
| **COMPLEX** | Multi-step tasks | Current agentic loop with Composio decomposition |
| **HARNESS** | Massive/long tasks | Multi-session orchestration with file-based state |

---

## Key Discoveries

### 1. Anthropic Harness Does NOT Use Compaction for Continuity

**Source**: Research on `anthropics/claude-quickstarts/autonomous-coding`

The harness creates a **fresh agent instance** each session with completely wiped memory. Continuity is achieved via:

- `feature_list.json` - Task list with `passes: true/false` flags
- `claude-progress.txt` - Narrative log of what was done
- Git history - Change tracking and rollback
- `init.sh` - Environment restoration script

The agent is explicitly told: *"This is a FRESH context window - you have no memory of previous sessions."*

### 2. "Get Your Bearings" Protocol is Critical

Each session starts with mandatory orientation:
```bash
cat feature_list.json | head -50
cat claude-progress.txt
git log --oneline -20
```

This 30-second step replaces any need for in-context memory.

### 3. Universal Agent's Existing Strengths

| Component | Status | Location |
|-----------|--------|----------|
| Durability DB (runs, steps, ledger) | ✅ Implemented | `src/universal_agent/durable/` |
| Idempotency via tool receipts | ✅ Implemented | `ledger.py` |
| Checkpointing | ✅ Implemented | `checkpointing.py` |
| File-based artifacts | ✅ Implemented | `search_results/`, `work_products/` |
| Session workspace | ✅ Implemented | `AGENT_RUN_WORKSPACES/session_*/` |
| Task decomposition | ✅ Via Composio SEARCH_TOOLS | Auto-decomposition exists |
| Explicit progress file | ❌ Missing | Need to add |
| Session boundary restart | ❌ Missing | Need to add |
| "Get Your Bearings" for sub-agents | ❌ Missing | Need to add |

### 4. Two Distinct Problems

| Problem | Description | Universal Agent Status |
|---------|-------------|----------------------|
| **Multi-Task Orchestration** | Break massive request into session-sized tasks | Composio SEARCH_TOOLS may handle this |
| **Same-Task Continuation** | Single task exceeds context, needs handoff | Not currently supported |

**Insight**: Composio SEARCH_TOOLS already decomposes requests. The main gap is same-task continuation when a sub-agent runs out of context.

### 5. Compaction vs Fresh Sessions

| Approach | How It Works | Used By |
|----------|--------------|---------|
| **Compaction** | Summarize old context, keep in same session | Claude SDK (internal) |
| **Fresh Session** | Wipe memory, reload from files | Anthropic Harness |

Anthropic chose fresh sessions because:
- Compaction loses specificity
- Agent doesn't know what was already done
- Agent may repeat work or skip incomplete work

### 6. ⚠️ CRITICAL: SDK Token Limits (New Discovery)

**L5 Stress Test (2026-01-05)** revealed SDK hard limits:

| Limit | Value | Source |
|-------|-------|--------|
| Tool output max | **25,000 tokens** | Claude Agent SDK |
| Effective words | ~17-18K | 25K tokens ÷ 1.4 tokens/word |
| Our safe limit | **15,000 words** | Recommended per batch |

**Failure sequence observed:**
1. `read_research_files` returned 50,583 tokens (29K words)
2. SDK truncated output, saved to temp file
3. Agent tried to recover with offset/limit
4. Agent got stuck in infinite retry loop with empty Write calls
5. Manual termination required

**This confirms harness intervention is required.** Agent cannot self-recover.

---

## Universal Agent Current State

### Durability System (Phases 0-4 Complete)

| Phase | Status | Features |
|-------|--------|----------|
| Phase 0 | ✅ | run_id, step_id propagation, budgets |
| Phase 1 | ✅ | Runtime DB, tool ledger, idempotency |
| Phase 2 | ✅ | Run/step state machine, checkpoints, resume |
| Phase 3 | ✅ | Replay policies, Task relaunch, crash hooks |
| Phase 4 | ✅ | Operator CLI, worker mode, receipts |

### Sub-Agent Architecture

- **report-creation-expert**: Research and HTML report generation
- **video-creation-expert**: FFmpeg video/audio processing
- **image-expert**: Gemini image generation
- **slack-expert**: Slack workspace interactions

Sub-agents are **ephemeral**:
- No cross-turn memory
- Context CAN exhaust mid-task
- Currently no way to continue if context fills

### Known Context Exhaustion Scenarios

1. `report-creation-expert` with 15+ research sources
2. Complex video editing with multiple operations
3. Any task requiring reading many large files

---

## Anthropic Harness Patterns

### File Structures

**`feature_list.json`** (Task tracking)
```json
[
  {
    "category": "functional",
    "description": "Task description",
    "steps": ["Step 1", "Step 2", "Step 3"],
    "passes": false
  }
]
```

**`claude-progress.txt`** (Narrative log)
```
Session completed: 2026-01-05

## Accomplishments
- Completed feature X

## Next Session Should
- Work on feature Y

## Status
Passing: 45/200 (22.5%)
```

### Session Loop Pattern

```python
while not all_tasks_complete:
    client = ClaudeSDKClient(options)  # FRESH client
    
    if is_first_run:
        prompt = initializer_prompt
    else:
        prompt = coding_prompt
    
    run_session(client, prompt)
    check_progress()
```

### Agent Instructions Pattern

From `coding_prompt.md`:
1. "This is a FRESH context window - you have no memory"
2. Run "Get Your Bearings" (read progress files)
3. Pick ONE task to work on
4. Implement and verify
5. Update progress files
6. End session cleanly before context fills

---

## Design Decisions

### Decision 1: Validation Strategy

**Chosen**: Self-Validation (Simpler)
- Same agent validates completion by checking artifacts/ledger
- No separate validation agent (introduces latency)
- If validation fails, retry same task

### Decision 2: Integration Point

**Proposed**: Separate Harness Mode
- New `--harness` flag or `/harness` command
- Distinct from COMPLEX (different mental model)
- Could auto-detect based on classifier in future

### Decision 3: Task File Format

**Proposed**: Follow Anthropic pattern
- `macro_tasks.json` (like `feature_list.json`)
- `session_progress.md` (like `claude-progress.txt`)
- Leverage existing workspace structure

---

## Open Questions

1. **Can Composio SEARCH_TOOLS decompose very complex requests?**
   - Need empirical testing at scale
   - May need pre-decomposition for massive requests

2. **What is the actual context limit for sub-agents?**
   - Need stress testing to identify failure points
   - Measure: tool calls, tokens, conversation turns

3. **How should sub-agents handle same-task continuation?**
   - Option A: Sub-agent writes progress file, gets re-delegated
   - Option B: Sub-agent uses internal progress tracking
   - Option C: Primary agent detects failure, orchestrates continuation

4. **Should we add compaction WITHIN sessions?**
   - Claude SDK may do this automatically
   - Need to understand SDK behavior better

5. **How does our durability system interact with fresh sessions?**
   - Current system assumes same process continues
   - Need to handle run continuation across SDK client restarts

---

## Next Steps

### Immediate (Stress Testing)

1. **Create stress test evaluation**
   - Progressively increase research complexity
   - Measure failure points
   - Identify context exhaustion patterns

2. **Document failure modes**
   - Where exactly does report-creation-expert fail?
   - What error messages appear?
   - How much progress was made before failure?

### Short-Term (Prototype)

3. **Add progress file to report-creation-expert**
   - `task_progress.md` written at checkpoints
   - Read at start of each sub-agent call

4. **Test continuation**
   - Does re-delegating with progress file work?
   - Can sub-agent pick up where it left off?

### Medium-Term (If Needed)

5. **Implement session boundary management**
   - Detect context exhaustion
   - Clean session termination
   - Fresh SDK client instantiation

6. **Implement macro-task decomposition**
   - Initializer agent for massive requests
   - Multi-session loop

---

## File References

### This Module

| File | Purpose |
|------|---------|
| `long_running_harness_exploration/README.md` | Directory overview |
| `long_running_harness_exploration/docs/KNOWLEDGE_BASE.md` | This file (source of truth) |
| `long_running_harness_exploration/research/anthropic_harness_deep_dive.md` | Detailed Anthropic research |
| `long_running_harness_exploration/experiments/` | Ad-hoc experiments |
| `long_running_harness_exploration/tests/` | Stress tests |
| `long_running_harness_exploration/prototypes/` | Prototype code |

### Main Project (Related)

| File | Relevance |
|------|-----------|
| `Project_Documentation/030_LONG_RUNNING_TASK_EXTENSIONS_DESIGN.md` | Initial design doc |
| `Project_Documentation/017_LONG_RUNNING_AGENTS_PROGRESS.md` | Durability progress |
| `src/universal_agent/durable/` | Existing durability system |
| `src/universal_agent/main.py` | Agent definitions, hooks |

### External References

| Source | URL |
|--------|-----|
| Anthropic Blog | https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents |
| Autonomous Coding Repo | https://github.com/anthropics/claude-quickstarts/tree/main/autonomous-coding |
| Claude Agent SDK | https://platform.claude.com/docs/en/agent-sdk/overview |

---

## Change Log

| Date | Change |
|------|--------|
| 2026-01-05 | Initial knowledge base created |
| 2026-01-05 | Added Anthropic harness research findings |
| 2026-01-05 | Documented three-mode system proposal |
| 2026-01-05 | Identified key gaps vs Anthropic approach |

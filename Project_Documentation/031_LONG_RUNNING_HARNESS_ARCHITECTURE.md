# 031: Long-Running Harness Architecture

**Date:** January 13, 2026  
**Status:** In Development  
**Goal:** Enable 1-24 hour autonomous task execution

---

## Executive Summary

This document outlines our approach to enabling long-running autonomous execution (1-24 hours) using a **harness-enhanced multi-agent system** that leverages Composio for real-world integrations.

**Key Decision:** Build on our existing system rather than adopt URW (Universal Ralph Wrapper) because:
1. **Composio Integration** - 6+ months of tool integration work; URW has none
2. **Foundation Already Solid** - Need tactical improvements, not rewrites
3. **Remaining Gaps Addressable** - Days of work, not weeks

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           HARNESS MODE (Long-Running)                       │
│                                                                             │
│  User Request: "Research quantum computing and write a comprehensive report"│
│                                 │                                           │
│                                 ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │              PHASE 1: MACRO DECOMPOSITION                           │    │
│  │                                                                     │    │
│  │   Option A: Composio Search Tools (preferred if viable)            │    │
│  │   • Send macro request → Get tool-aware task breakdown             │    │
│  │   • Includes: tool slugs, pitfalls, dependencies                   │    │
│  │                                                                     │    │
│  │   Option B: LLM Decomposition (fallback)                           │    │
│  │   • Claude breaks request into sequential phases                   │    │
│  │                                                                     │    │
│  │   Output: mission.json with PENDING tasks                          │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                 │                                           │
│                                 ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │              PHASE 2: SEQUENTIAL EXECUTION                          │    │
│  │                                                                     │    │
│  │   For each task (one at a time):                                   │    │
│  │   ┌─────────────────────────────────────────────────────────────┐   │    │
│  │   │  1. Inject ONLY current task into context                   │   │    │
│  │   │  2. Hide future tasks (prevent overwhelm)                   │   │    │
│  │   │  3. Execute with full MCP + Composio toolset               │   │    │
│  │   │  4. Verify completion (Binary + Format + LLM Judge)        │   │    │
│  │   │  5. Save artifacts + Mark task COMPLETED                   │   │    │
│  │   │  6. Record learnings + failed approaches (Guardrails)      │   │    │
│  │   └─────────────────────────────────────────────────────────────┘   │    │
│  │                                                                     │    │
│  │   If MAX_ITERATIONS exceeded → Context Reset + Next Task           │    │
│  │   If Task Failed → Add to Guardrails, Retry with new approach      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                 │                                           │
│                                 ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │              PHASE 3: COMPLETION                                    │    │
│  │                                                                     │    │
│  │   • All tasks COMPLETED → Output <promise>TASK_COMPLETE</promise>  │    │
│  │   • Generate summary + deliver results via Composio tools          │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Two Modes of Operation

### Regular Mode (Current Default)
- Single context window
- All tools available
- No harness overhead
- Best for: Quick tasks < 30 minutes

### Harness Mode (Long-Running)
- Multiple context windows (resets as needed)
- Sequential task execution
- State persistence across resets
- Guardrails to prevent repeated mistakes
- Best for: Complex tasks 1-24+ hours

**Harness mode is activated by:**
- `--harness` flag
- Detection of complex multi-phase request
- `completion_promise` in run spec

---

## Composio Search Tools for Decomposition

### Hypothesis
Composio Search Tools may be able to generate task plans with:
- Tool slugs (specific Composio actions)
- Pitfalls and constraints
- Dependency chains
- Estimated complexity

### Testing Plan
1. Send macro-level request to Composio Search Tools
2. Evaluate quality of returned task breakdown
3. Compare with LLM-based decomposition
4. Determine threshold for complexity (when to use each)

### Potential Flow
```
User: "Research quantum computing companies and send a report to my team"

Composio Search Tools Response:
{
  "phases": [
    {
      "id": "research",
      "description": "Search for quantum computing companies",
      "tools": ["COMPOSIO_SEARCH_NEWS", "COMPOSIO_SEARCH_WEB"],
      "pitfalls": ["Rate limits on search APIs", "Filter for recency"],
      "estimated_tokens": 50000
    },
    {
      "id": "analyze", 
      "description": "Analyze and structure findings",
      "tools": ["Write", "Edit"],
      "depends_on": ["research"]
    },
    {
      "id": "deliver",
      "description": "Send report via email",
      "tools": ["COMPOSIO_GMAIL_SEND_EMAIL"],
      "depends_on": ["analyze"]
    }
  ]
}
```

---

## Sequential Execution Strategy

### Problem
Agent sees all tasks in `mission.json` and tries to execute them simultaneously,
overloading context and causing failures.

### Solution: Single-Task Injection

When generating the agent prompt in harness mode:

```python
# Current (broken)
"Here are your tasks: [task1, task2, task3, task4, task5]"

# Fixed
"""
## Current Task (1 of 5)
**task1**: Description here

## Completed Tasks
- (none yet)

## Future Tasks (DO NOT START)
- task2, task3, task4, task5 are LOCKED until current task completes
"""
```

### Efficiency Consideration
We DON'T reset context for every task. Instead:
1. Execute as many tasks as fit in one context window
2. Only reset when context exceeds threshold (180k tokens)
3. Simple sequential tasks (e.g., "convert to PDF") run in same window

---

## Guardrails: Failed Approach Tracking

### Purpose
Prevent agent from repeating mistakes after context resets.

### Implementation
1. Create `guardrails.md` in session workspace
2. After each failure, append:
   ```markdown
   ## Failed Approach: [timestamp]
   - **Task**: Research quantum computing
   - **Approach**: Tried to scrape website directly
   - **Why Failed**: Rate limited after 10 requests
   - **Recommendation**: Use official APIs instead
   ```
3. Inject into resume prompts:
   ```markdown
   ## Failed Approaches (DO NOT REPEAT)
   - Web scraping: Rate limited
   - Single source: Data outdated
   ```

---

## Implementation Priorities

| Priority | Item | Effort | Status |
|----------|------|--------|--------|
| A | Stdout Race Condition | Low | ✅ DONE |
| B | Sequential Task Execution | Medium | 🔄 In Progress |
| B.1 | Test Composio Search Tools limits | Low | Pending |
| B.2 | Single-task injection | Medium | Pending |
| C | Guardrails | Low | Pending |
| D | Enhanced LLM Evaluation | Medium | Pending |

---

## Success Criteria

- [ ] Agent can complete a 5-phase research task without human intervention
- [ ] Context resets don't cause duplicate work
- [ ] Failed approaches are not repeated
- [ ] Tasks complete in dependency order
- [ ] Total run time can exceed 1 hour
- [ ] Final deliverables reach user (via Composio tools)

---

## Related Documents

- [030_CONTEXT_EXHAUSTION_FIX_SUMMARY.md](./030_CONTEXT_EXHAUSTION_FIX_SUMMARY.md) - Two-Phase Architecture
- [000_CURRENT_CONTEXT.md](./000_CURRENT_CONTEXT.md) - Project overview
- [000_CHAT_CONTEXT_HANDOFF.md](./000_CHAT_CONTEXT_HANDOFF.md) - Recent accomplishments

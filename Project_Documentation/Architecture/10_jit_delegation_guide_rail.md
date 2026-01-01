# JIT Delegation Guide Rail

**Document Version**: 1.0
**Last Updated**: 2025-12-29
**Component**: Universal Agent
**Primary Files**: `.claude/knowledge/report_workflow.md`, `src/universal_agent/main.py`

---

## Table of Contents

1. [Overview](#overview)
2. [The Problem: Hallucination from Snippets](#the-problem-hallucination-from-snippets)
3. [The Solution: Knowledge Base Injection](#the-solution-knowledge-base-injection)
4. [Architecture Comparison](#architecture-comparison)
5. [Implementation Details](#implementation-details)

---

## Overview

The **Just-In-Time (JIT) Delegation Guide Rail** is an architectural pattern designed to enforce mandatory delegation to sub-agents when specific triggers (like web search results) occur. It ensures the main agent does not attempt to perform complex tasks (like full report generation) using insufficient data (search snippets).

---

## The Problem: Hallucination from Snippets

Large Language Models (LLMs) are eager to please. When a user asks for a "comprehensive report" and the agent performs a search, the agent receives a list of **Search Snippets** (2-3 sentences each).

**Default Behavior**:
1.  User: "Research AI agents."
2.  Agent: `COMPOSIO_SEARCH_WEB(query="AI agents")`
3.  Agent receives: `[{"title": "AI Trends", "snippet": "AI agents are growing..."}]`
4.  **FAILURE**: Agent immediately writes a "Report" based ONLY on those 2 sentences, hallucinating details to fill the gap.

**Desired Behavior**:
1.  Agent receives snippets.
2.  Agent **RECOGNIZES** these are insufficient.
3.  Agent **DELEGATES** to `report-creation-expert` to perform full file crawling.

---

## The Solution: Knowledge Base Injection

Instead of complex code hooks (which are brittle with batched tool calls), we use **Static Knowledge Base Injection**.

We inject a specific Markdown file (`.claude/knowledge/report_workflow.md`) into the Agent's system prompt at startup. This file acts as a "Guide Rail" that is always present in the context.

### The Rule

The Guide Rail explicitly states:

> ⚠️ **CRITICAL**: When you receive web search results...
> **STOP**. DO NOT summarize the search snippets.
> These search results contain **incomplete snippets**.
> **REQUIRED ACTION**: Call the `Task` tool with `subagent_type='report-creation-expert'`.

### Why This Works
Claude models prioritize "Context" and "System Instructions" highly. By framing the search results as "incomplete" and the Delegation as "Mandatory," we align the model's incentive (providing a good answer) with our architectural requirement (using the sub-agent).

---

## Architecture Comparison

We explored two approaches before settling on Knowledge Base Injection.

### Attempt 1: Synchronous Flags & Hooks (Failed)
*   **Idea**: Use an `observer` to set a global flag `JIT_DELEGATION_PENDING = True` when search results arrive. Use a `PostToolUse` hook to inject a system message telling the agent to delegate.
*   **Result**: **FAILURE**.
*   **Reason**: Tool execution in the Claude SDK is often **batched**. The Observer runs *after* the tool completes, but the Agent might have already generated its *next* thought trace before the Hook has a chance to inject the correction. Race conditions made this unreliable.

### Attempt 2: Knowledge Base Injection (Success)
*   **Idea**: Pre-load the rule into the system prompt.
*   **Result**: **SUCCESS**.
*   **Reason**: No race conditions. The rule is part of the agent's "core identity" for the session. It requires zero code logic in the critical path and works regardless of tool batching.

---

## Implementation Details

### 1. Knowledge File
**Location**: `.claude/knowledge/report_workflow.md`

```markdown
# Report Creation Workflow
## Critical: Web Search Results Require Delegation
...
### Required Workflow
1. Search Phase
2. Delegation Phase: Call `Task` with `subagent_type='report-creation-expert'`
...
```

### 2. Injection Logic
**Location**: `src/universal_agent/main.py:1026` (approx)

```python
# Inject Knowledge Base (Static Tool Guidance)
knowledge_content = load_knowledge()
if knowledge_content:
    options.system_prompt += f"\n\n## Tool Knowledge\n{knowledge_content}"
    print(f"✅ Injected Knowledge Base ({len(knowledge_content)} chars)")
```

### 3. Observer Reinforcement (Optional)
The `observer_and_save_search_results` function still prints a console reminder:
`⚠️ Reminder: Delegate to 'report-creation-expert' for full analysis.`

This serves as a secondary reinforcement for the human operator (in CLI) but the primary driver is the Knowledge Base prompt.

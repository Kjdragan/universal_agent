# TaskStop Death Loop — Full Context Document for External Review

> **Purpose:** This document provides full context for an external AI coder to independently investigate why our autonomous agent (Simone) enters an infinite `TaskStop` death loop as its very first action on a brand new session. We need fresh eyes on the root cause.

---

## 1. System Architecture Overview

We run an autonomous AI agent called **Simone** built on top of the **Claude Agent SDK** (Anthropic's official SDK for agentic Claude deployments). The architecture:

```
User (Web UI) → Gateway (FastAPI) → Claude Agent SDK → Claude Sonnet 4 (API)
                                          ↑
                                  Hook System (hooks.py)
                                  MCP Tools (mcp_server.py)
                                  System Prompt (prompt_builder.py)
```

- **Claude Agent SDK** provides the core agent loop. It offers built-in tools: `Task`, `TaskStop`, `Bash`, `Read`, `Write`, `Glob`, `Grep`, `LS`, etc.
- **`Task`** is a delegation tool — it spawns sub-agents (e.g., `research-specialist`, `report-writer`) that run as child processes.
- **`TaskStop`** is the corresponding lifecycle tool — it stops a running `Task` by its `task_id`.
- **Our code** adds MCP tools (research pipeline, PDF generation, email, etc.), a hook system for guardrails, and a custom system prompt.

### Key Files

| File | Role |
|------|------|
| `src/universal_agent/main.py` (~10,700 lines) | Main agent loop, tool dispatch, hook orchestration |
| `src/universal_agent/hooks.py` (~2,160 lines) | PreToolUse/PostToolUse hooks, guardrails, circuit-breaker |
| `src/universal_agent/prompt_builder.py` (~545 lines) | Builds the system prompt from sections |
| `src/mcp_server.py` (~5,000 lines) | MCP tool implementations (research pipeline, etc.) |
| `.claude/agents/research-specialist.md` | Sub-agent instructions for research delegation |

### System Prompt Mode

The system prompt is built in **`claude_code_append` mode** — meaning the Claude Code preset (SDK default) is used as the base, and our custom prompt is **appended** to it:

```python
# From prompt_builder.py line 51-55
return {
    "type": "preset",
    "preset": "claude_code",
    "append": custom_prompt,  # Our custom sections appended
}, mode
```

This means the model receives:
1. **Claude Code's built-in system prompt** (which we do NOT control — it includes descriptions of built-in tools like `Task`, `TaskStop`, `Bash`, etc.)
2. **Our custom system prompt** (appended after the preset)

---

## 2. The Problem

### What happens

When a user submits a query like:
> "Search for the latest news from the Russia Ukraine war over the past five days. Create a report, save the report as a PDF and email it to me."

The model's **very first action** — before ANY productive work — is to call `TaskStop` with fabricated task IDs:

```
TaskStop(task_id="bg_research_russia_ukraine")  → Error: No task found with ID
TaskStop(task_id="bg_research_tech")             → Error: No task found with ID
TaskStop(task_id="bg_research_russia_ukraine")  → Error: No task found with ID
TaskStop(task_id="bg_research_tech")             → Error: No task found with ID
... (repeats endlessly)
```

### Evidence from an actual run

Session: `session_20260317_051402_4c4a2c23` (March 17, 2026, brand new session)

```
[05:14:22] 👤 USER: Search for the latest news from the Russia Ukraine war...
[05:14:40] 🔧 TOOL CALL: TaskStop   ← FIRST ACTION, 18 seconds after user query
[05:14:40] 📦 TOOL RESULT (82 bytes)
[05:14:41] 🔧 TOOL CALL: TaskStop
[05:14:41] 📦 TOOL RESULT (79 bytes)
... 120 CONSECUTIVE TaskStop calls over ~8 minutes ...
[05:22:13] 🔧 TOOL CALL: TaskStop   ← Still going. Never did any productive work.
[05:22:13] 📦 TOOL RESULT (79 bytes)
```

**241 total lines in the run log. 120 of them are TaskStop calls. Zero productive tool calls.**

### The model's own thinking

From the UI's "Thinking Process" panel:
> "The TaskStop calls are failing because no background tasks exist. Let me focus on the actual task — delegating to research-specialist to gather Russia-Ukraine news."

It KNOWS the tasks don't exist. It says it will refocus. But then it calls TaskStop again. It's caught in a reasoning loop.

### Session state at the time

- **Brand new session** — no prior activity
- **No running tasks** — this is the first query
- **No session memory** — the `memory/` directory was empty
- **No conversation history** — single-turn, first message
- **autonomy_mode: "yolo"** — full autonomy, no approval gates

---

## 3. What We've Investigated

### Exhaustive search for what primes "TaskStop" or "background tasks"

We checked every piece of context injected at session start:

| Source | Mentions TaskStop? | Mentions "background task"? |
|--------|-------------------|---------------------------|
| `SOUL.md` (persona instructions) | ❌ No | ❌ No |
| `HEARTBEAT.md` (proactive task config) | ❌ No | ❌ No |
| `AGENTS.md` (agent docs) | ❌ No | ❌ No |
| `BOOTSTRAP.md` (workspace setup) | ❌ No | ❌ No |
| `MEMORY.md` (core memory backup) | ❌ No | ❌ No |
| `TOOLS.md` (tool reference) | ❌ No | ❌ No |
| `capabilities.md` (42KB capability routing) | ❌ No | 1 generic match (irrelevant) |
| `session_policy.json` | N/A | N/A |
| `memory/*.md` (session memory) | Empty directory | N/A |
| `.claude/knowledge/*.md` files | ❌ No | ❌ No |
| Skill docs (`.claude/skills/`) | ❌ No | 1 generic match (irrelevant) |
| `prompt_builder.py` system prompt | ❌ No (actively cleaned) | ❌ No |
| `hooks.py` UserPromptSubmit injection | ❌ No | ❌ No |

**Result: None of our code, configuration, or knowledge files mention TaskStop.**

### What our system prompt DOES say (relevant sections)

From `prompt_builder.py` — the "Execution Strategy" section injected into the system prompt:

```
5. **First-action rule (mandatory)**: Your FIRST tool call in response to any user request
   MUST be productive work — a `Task()` delegation to a specialist, a direct MCP tool call,
   or a search/discovery action. Never begin a session with cleanup or housekeeping.
```

From `hooks.py` — the `UserPromptSubmit` hook injects task decomposition guidance:

```
### 🧭 Initial Task Assessment & Decomposition
Before beginning execution, decompose this request carefully:
1. **Analyze**: Break this request into atomic, logical steps.
2. **Happy Path Backbone**: Consider the deterministic path — your FIRST tool call
   should be productive work (e.g., `Task()` delegation, `mcp__composio__*` search, or discovery).
3. **Capability Match**: Evaluate your Capability Routing Doctrine.
4. **Execution**: Proceed methodically...

### ✅ Example: Research → Report → PDF → Email (Golden Path)
Step 1: Task(subagent_type='research-specialist', description='Research X', prompt='...')
Step 2: Task(subagent_type='report-writer', ...)
Step 3: mcp__internal__html_to_pdf (convert report.html → report.pdf)
Step 4: mcp__internal__upload_to_composio + COMPOSIO_GMAIL_SEND (email PDF)

**Key: Start with Step 1 immediately.**
```

Both explicitly say "start with productive work." Neither mentions TaskStop. The golden path example shows the exact correct flow.

---

## 4. What We Know About `TaskStop`

### Where it comes from

`TaskStop` is a **built-in Claude Agent SDK tool**. It is NOT defined in our code. The Claude Code preset (which we use as base) includes tool definitions for:
- `Task` — spawn a sub-agent
- `TaskStop` — stop a running task by `task_id`
- `Bash`, `Read`, `Write`, `Glob`, `Grep`, `LS`, etc.

We use `claude_code_append` mode, meaning the Claude Code preset's built-in system prompt (including these tool descriptions) is sent to the model FIRST, and our custom prompt is appended AFTER.

**We cannot inspect or modify the Claude Code preset's description of `TaskStop`.** We don't know exactly what it tells the model about when to use `TaskStop`.

### The SDK error response

When the model calls `TaskStop(task_id="bg_research_russia_ukraine")` and no such task exists, the SDK returns:

```xml
<tool_use_error>No task found with ID: bg_research_russia_ukraine</tool_use_error>
```

This terse error message does NOT redirect the model to productive work. It just says "not found," which the model interprets as "try again" or "try a different task_id."

### Pattern of hallucinated IDs

The model consistently fabricates task IDs that follow a pattern:
- `bg_research_russia_ukraine` — "bg" prefix + topic-based description
- `bg_research_tech` — same prefix, different topic

The `bg_` prefix suggests the model thinks it's managing "background" tasks. It's creating descriptive human-readable IDs (not real SDK-emitted opaque tokens).

---

## 5. Our Current Guardrails

### Circuit-breaker in `hooks.py` (PreToolUse hook)

When the model calls `TaskStop`, our hook intercepts:

```python
# hooks.py, line ~1079
if normalized_tool_name in ("taskstop", "task_stop"):
    # Circuit-breaker: after 2 consecutive failures, hard-redirect
    if self._taskstop_consecutive_failures >= 2:
        return {
            "systemMessage": (
                "⛔ Action blocked — no active tasks exist to manage. "
                f"({self._taskstop_consecutive_failures} consecutive invalid attempts).\n\n"
                "REDIRECT: Begin productive work NOW:\n"
                "→ Decompose the user request into steps\n"
                "→ Call `Task(subagent_type='research-specialist', ...)` or equivalent\n"
                "→ Chain results through the pipeline"
            ),
            "decision": "block",
        }
```

**Problem:** Despite getting this explicit redirect message, the model STILL calls TaskStop on the next iteration. The redirect is not strong enough to break the loop.

### Task ID validation in `_task_stop_rejection_reason()`

We have a function that validates task IDs before they reach the SDK. Originally it used a blacklist approach (blocking `task_*`, `session_*`, `run_*`, `dummy*`, etc.). But the model bypassed it by using the `bg_*` prefix, which wasn't on the blacklist.

We've since rewritten this to use an **allowlist approach** — only accepting IDs that look like real SDK-emitted opaque tokens (12+ char bodies, no English words). But this doesn't address the root cause.

### Guardrail in `main.py` (PreToolUse)

Same validation function, applied at a second layer:

```python
# main.py, line ~1412
if normalized_tool_name in ("taskstop", "task_stop"):
    task_id = _extract_task_stop_id(guard_tool_input)
    reason = _task_stop_rejection_reason(task_id)
    if reason:
        return {
            "systemMessage": "⚠️ Action blocked — no active tasks to manage.\n\n" + reason,
            "decision": "block",
        }
```

---

## 6. Our Hypothesis (But We're Not Sure)

The model sees:
1. Our system prompt telling it to delegate research via `Task(subagent_type='research-specialist', ...)`
2. `TaskStop` available as a built-in tool
3. It conflates its planned `Task()` delegations with "background tasks" needing lifecycle management
4. It fabricates IDs for tasks it *plans to create* and tries to "clean the slate" before starting

But we're NOT confident this is the full explanation because:
- The system prompt explicitly says "start with productive work"
- The golden path example shows the correct flow
- The model's own thinking says "I know these tasks don't exist"
- Yet it STILL can't break the loop

**We suspect there may be something in the Claude Code preset's built-in system prompt (which we can't see) that primes this behavior** — perhaps describing `TaskStop` in a way that makes the model want to use it proactively.

---

## 7. Questions We Need Answered

1. **Why does the model call TaskStop as its very first action on a fresh session with no tasks?** What is priming this behavior? Is it the Claude Code preset description of TaskStop?

2. **Why can't the model break the loop?** Even after receiving explicit redirect messages telling it no tasks exist and to start productive work, it continues calling TaskStop. Is this a known issue with Claude's tool-use loop behavior?

3. **Is the `claude_code_append` mode causing this?** Would switching to `custom_only` mode (where we provide the full system prompt and don't use the Claude Code preset) prevent this?

4. **Is there a way to remove `TaskStop` from the available tools?** Can we filter the tool list before it reaches the model?

5. **Is there a pattern in how the SDK's error responses trigger retry loops?** The `<tool_use_error>` format may be interpreted by the model as "try again" rather than "stop doing this."

---

## 8. Key File Locations

All paths relative to `/home/kjdragan/lrepos/universal_agent/` (or `/opt/universal_agent/` on VPS):

```
src/universal_agent/prompt_builder.py     — System prompt construction
src/universal_agent/hooks.py              — PreToolUse/PostToolUse hooks, circuit-breaker
src/universal_agent/main.py               — Main agent loop, tool dispatch
src/mcp_server.py                         — MCP tool implementations
.claude/agents/research-specialist.md     — Research sub-agent instructions
.claude/knowledge/                        — Knowledge base files
AGENT_RUN_WORKSPACES/                     — Session workspaces
```

### The failing session workspace

```
AGENT_RUN_WORKSPACES/session_20260317_051402_4c4a2c23/
├── run.log                  ← 241 lines, 120 TaskStop calls
├── turns/
│   └── turn_*.jsonl         ← SDK transcript (1 event: turn_started)
├── SOUL.md
├── HEARTBEAT.md
├── AGENTS.md
├── BOOTSTRAP.md
├── MEMORY.md
├── TOOLS.md
├── IDENTITY.md
├── USER.md
├── capabilities.md          ← 42KB capability routing document
├── session_policy.json
├── heartbeat_state.json
├── memory/                  ← EMPTY directory
├── downloads/
└── work_products/
```

---

## 9. What We've Already Tried

| Fix | Result |
|-----|--------|
| Removed "TaskStop" mentions from system prompt (prompt_builder.py) | ❌ Still happens — TaskStop wasn't in our prompt |
| Removed "TaskStop" from hook error messages | ❌ Still happens — model gets the term from SDK |
| Added positive first-action guidance to system prompt | ❌ Still happens — model ignores it |
| Added golden path example to UserPromptSubmit hook | ❌ Still happens — model ignores it |
| Blacklisted known fabricated prefixes (task_, session_, dummy_) | ❌ Model switched to `bg_*` prefix |
| Rewrote to allowlist approach (only opaque tokens accepted) | Untested — committed but VPS gateway not restarted |
| Circuit-breaker after 2 failures with aggressive redirect | ❌ Model ignores redirect and keeps calling |

---

## 10. Environment Details

- **Model**: Claude Sonnet 4 (via Anthropic API)
- **SDK**: `claude-agent-sdk` (Python, latest version in .venv)
- **Runtime**: Python 3.13, Ubuntu VPS
- **Gateway**: FastAPI + WebSocket, systemd-managed
- **System prompt mode**: `claude_code_append` (Claude Code preset + our appended custom prompt)
- **Session policy**: `autonomy_mode: "yolo"` (full autonomy)
- **Max tool calls configured**: 500
- **Session timeout**: 5400 seconds (90 minutes)

---

## 11. Resolution (2026-03-17)

### Root Cause Analysis

The model was primed by the Claude Code preset's built-in system prompt (which we cannot inspect or modify) to use `TaskStop` proactively for "background task lifecycle management." Since Simone doesn't actually use background tasks — all `Task()` delegations are foreground sub-agents that run to completion — the model was:

1. Hallucinating task IDs for tasks it *planned* to create (e.g., `bg_research_russia_ukraine`)
2. Trying to "clean the slate" before starting productive work
3. Getting stuck in a loop when those IDs didn't exist

### Fix Applied

**Added `TaskStop` to `DISALLOWED_TOOLS` in `src/universal_agent/constants.py`:**

```python
DISALLOWED_TOOLS = [
    # TaskStop blocked: Simone doesn't use background tasks - all Task() delegations are
    # foreground sub-agents that run to completion. The model was hallucinating task IDs
    # and entering death loops trying to "clean up" non-existent background tasks.
    "TaskStop",
    "task_stop",
    # ... other disallowed tools
]
```

This blocks TaskStop at the **SDK level** before the model can even attempt to call it, eliminating the death loop entirely.

### Why This Works

1. **SDK-level blocking**: The `disallowed_tools` parameter in `ClaudeAgentOptions` prevents the tool from being exposed to the model at all
2. **No legitimate use case**: Simone operates in a request-response pattern where `Task()` delegations are foreground operations that run to completion — there's never a need to stop them mid-execution
3. **Removes the temptation**: If the tool isn't available, the model can't get stuck in a loop trying to use it

### What Can Be Removed (Optional Cleanup)

With TaskStop blocked at the SDK level, the following guardrails are now redundant and can be removed in a follow-up:

- `_task_stop_rejection_reason()` in `hooks.py` and `main.py`
- `_extract_task_stop_id()` in `hooks.py` and `main.py`
- Circuit-breaker logic in `hooks.py` (lines 1079-1133)
- TaskStop guardrail in `main.py` (lines 1412-1429)
- Test files: `tests/unit/test_main_pretool_taskstop_guardrail.py`, `tests/unit/test_hooks_task_stop_guardrail.py`

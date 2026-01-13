# Hooks and Subagents in the Claude Agent SDK (Python)

**Document Version**: 1.0  
**Last Updated**: 2026-01-07  
**Component**: Universal Agent  
**Primary Files**: `src/universal_agent/main.py`, `src/universal_agent/agent_core.py`, `src/universal_agent/guardrails/tool_schema.py`

---

## Overview

This document explains how hooks work in the Claude Agent SDK (Python), how they apply to subagents, and the concrete patterns used in this codebase. The key takeaway: **hooks are configured globally on `ClaudeAgentOptions`** and **automatically apply to all sub-agents**. The hook system is "agent-agnostic" - it operates at the CLI level and does not differentiate between main agent and sub-agent contexts.

> **Important Update (2026-01)**: As of SDK version 0.1.3+, hooks from the main agent's `ClaudeAgentOptions` apply to sub-agent tool calls. This is a core feature, not a limitation.

---

## SDK Reality Check

### Hooks are client-level only

In the Python SDK, hooks are configured on `ClaudeAgentOptions.hooks` and executed by `ClaudeSDKClient`. The `query()` helper does **not** support hooks.

- Use `ClaudeSDKClient` for any workflow that needs hooks.
- Use `query()` only for one-off tasks without hooks.

### Supported hook events

The Python SDK supports:

- `PreToolUse`
- `PostToolUse`
- `UserPromptSubmit`
- `Stop`
- `SubagentStop`
- `PreCompact`

Session-level hooks such as `SessionStart`/`SessionEnd` are not supported in the Python SDK.

### Hook input and output

Hook callbacks receive a dict with fields like:

- `tool_name`
- `tool_input`
- `tool_response` (PostToolUse)
- `session_id`
- `transcript_path`

Hooks return a dict that may include:

- `decision: "block"` to prevent execution
- `systemMessage` for internal logging/instructions
- `hookSpecificOutput` (SDK-specific or tool policy metadata)

### AgentDefinition does not accept hooks directly

`AgentDefinition` supports `description`, `prompt`, `tools`, and `model`. There is no `hooks` field in `AgentDefinition` itself.

**However**: Hooks defined on the main agent's `ClaudeAgentOptions` **automatically apply to all sub-agents**. The hook system is agent-agnostic and operates at the CLI level, intercepting tool calls from both main and sub-agents.

---

## Subagents: How They Are Defined and Invoked

### Programmatic agents

Subagents are defined via `ClaudeAgentOptions.agents`:

```python
AgentDefinition(
    description="When to delegate",
    prompt="Subagent system prompt",
    tools=["Read", "Grep", "Task"],  # optional
    model="sonnet"
)
```

Key rules:

- The main agent must have `Task` in `allowed_tools`.
- Subagents **cannot** spawn their own subagents. Do **not** include `Task` in a subagent’s `tools` list.
- If `tools` is omitted, the subagent inherits **all** tools from the parent.

### Invocation patterns

- **Automatic**: The main agent picks a subagent based on `description`.
- **Explicit**: The prompt names the subagent, e.g., "Use the code-reviewer agent to..."

---

## How Hooks Apply to Subagents

### ✅ Global hooks fire for ALL subagent tool calls

**All hooks** defined on `ClaudeAgentOptions.hooks` execute for tool calls made by subagents. This includes:

- `PreToolUse` - can block dangerous tool calls from sub-agents
- `PostToolUse` - can add context after sub-agent tool execution
- `SubagentStop` - fires when a sub-agent completes

The Python SDK does **not** expose a direct subagent identifier to hook callbacks, but hooks still intercept and can control sub-agent behavior.

### What you can infer

There are two practical ways to detect subagent activity:

1) **Message stream metadata**  
   The SDK’s streamed message objects can include `parent_tool_use_id` for subagent context. This is visible when iterating messages, not inside hook callbacks.

2) **Transcript path drift (hook input)**  
   Hook inputs include `transcript_path`. In this repo, we log when a new transcript path appears and treat any non-primary transcript path as a signal that hooks are firing during subagent execution.

---

## Patterns Used in This Repo

### Primary agent hooks (global)

Hooks are registered on the client in `src/universal_agent/main.py`:

- `PreToolUse`: ledger/idempotency + schema guardrails + policy checks
- `PostToolUse`: validation nudges (e.g., empty Write retry guidance)
- `SubagentStop`: workflow handoff logic

These hooks are **global** and apply to subagent tool calls.

### Subagent tool restrictions

Since the Python SDK does not support per-subagent hooks, we restrict subagents through `AgentDefinition.tools`.

Example from this codebase (report agent):

- `Read`
- `Write`
- `Bash`
- `mcp__local_toolkit__finalize_research`
- `mcp__local_toolkit__read_research_files`
- `mcp__local_toolkit__list_directory`
- `mcp__local_toolkit__generate_image`

This ensures the subagent cannot use disallowed tools even if it attempts to.

### Runtime confirmation of subagent hook firing

We log when hook callbacks see a **secondary transcript path**. This provides a lightweight signal that hooks are executing during subagent tool calls.

Output example:

```
🧭 Hook fired for secondary transcript: Read
```

---

## Recommended Practices

1) **Always use `ClaudeSDKClient` for hooks**  
   `query()` does not support hooks.

2) **Treat hooks as global policy**  
   Hooks enforce the same policy across main and subagent tool calls.

3) **Constrain subagent tools explicitly**  
   Use `AgentDefinition.tools` to limit subagent capabilities.

4) **Do not include `Task` in subagent tools**  
   Subagents cannot spawn subagents.

5) **Use `SubagentStop` for structured handoffs**  
   This is the only subagent-specific hook event in the Python SDK.

6) **Detect subagents outside hooks**  
   Use `parent_tool_use_id` in streamed messages or track transcript path drift.

---

## Common Pitfalls and Fixes

### "AgentDefinition got unexpected keyword argument 'hooks'"

Cause: The Python SDK does not support per-subagent hooks.  
Fix: Remove `hooks` from `AgentDefinition`, keep hooks on `ClaudeAgentOptions`.

### Task/Bash blocked despite being valid

Cause: Tool name normalization vs schema guardrails.  
Fix: Make tool name comparison case-insensitive in guardrails (handled in `tool_schema.py`).

### Empty Write calls after large reads

Cause: Context exhaustion leads to missing parameters.  
Fix: Use `PostToolUse` nudges and guardrails to force retries with explicit `file_path` and `content`.

---

## Where to Look in Code

- Hook registration: `src/universal_agent/main.py`
- Hook implementations: `src/universal_agent/main.py` (`on_pre_tool_use_ledger`, `on_post_tool_use_validation`, `on_subagent_stop`)
- Tool schema guardrails: `src/universal_agent/guardrails/tool_schema.py`
- Subagent definitions: `src/universal_agent/main.py`, `src/universal_agent/agent_core.py`

---

## Appendix: Minimal Hook Setup Example

```python
options = ClaudeAgentOptions(
    hooks={
        "PreToolUse": [
            HookMatcher(matcher=None, hooks=[on_pre_tool_use_ledger]),
        ],
        "PostToolUse": [
            HookMatcher(matcher=None, hooks=[on_post_tool_use_validation]),
        ],
        "SubagentStop": [
            HookMatcher(matcher=None, hooks=[on_subagent_stop]),
        ],
    }
)
```

---

## Appendix: Python vs TypeScript SDK Differences (Hooks and Subagents)

The Python and TypeScript SDKs are not feature‑parity. The differences below
are the ones that materially impact hooks and subagents in this repo.

### Python SDK (current behavior, v0.1.3+)

- Hooks are configured on `ClaudeAgentOptions.hooks`.
- **Hooks apply to ALL sub-agents** via inheritance from main agent options.
- `query()` does **not** support hooks (use `ClaudeSDKClient`).
- `AgentDefinition` does **not** accept a `hooks` field directly.
- Subagent detection inside hooks: use `transcript_path` drift or
  message stream metadata (`parent_tool_use_id`) outside hooks.

### TypeScript SDK (not used here)

- Supports a broader hook surface in the CLI environment.
- Still uses `Task` for subagent invocation.
- Behavior around settings loading differs (TypeScript typically loads
  settings by default, Python does not unless `setting_sources` is provided).

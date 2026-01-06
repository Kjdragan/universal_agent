# 001: Hooks Architecture & Guardrails

## Core Philosophy: The Hook Spectrum

In agentic systems, "hooks" serve two distinct purposes: **Control** and **Observation**. We implement these using different patterns to optimize for latency, cost, and safety.

| Hook Type | Implementation Pattern | Execution Timing | Purpose | Blocking? |
|-----------|------------------------|------------------|---------|-----------|
| **Guardrails** | Claude SDK `HookMatcher` | **Pre-Tool** (Before Execution) | Safety, Schema Validation, Policy Enforcement | ✅ YES |
| **Observers** | Async Event Loop | **Post-Tool** (After Execution) | logging, Artifact Saving, Analytics | ❌ NO |

---

## 1. PreToolUse Guardrails (Claude SDK)

We use the **Claude Agent SDK's `HookMatcher`** system to intercept tool calls *before* they are sent to the tool executor. This is our primary defense line.

### How It Works
Hooks are registered in `ClaudeAgentOptions` within `agent_core.py`. They receive the raw tool input and can:
1.  **Allow**: Return `{}` to let the call proceed.
2.  **Block**: Return a `permissionDecision: deny` to reject the call.
3.  **Guide**: Inject a `systemMessage` to guide the agent to fix the error.

### Active Implementation: `malformed_tool_guardrail_hook`
We currently use a global hook (`matcher="*"`) to catch common errors:

```python
# src/universal_agent/agent_core.py

async def malformed_tool_guardrail_hook(input_data: dict, tool_use_id: str, context) -> dict:
    tool_name = input_data.get("tool_name")
    
    # 1. Detect XML Concatenation Errors
    if is_malformed_tool_name(tool_name):
        return {
            "systemMessage": "⚠️ BLOCKED: Malformed tool name. Use proper JSON arguments.",
            "hookSpecificOutput": { "permissionDecision": "deny", ... }
        }

    # 2. Schema Validation
    is_valid, missing, schema = validate_tool_input(tool_name, tool_input)
    if not is_valid:
        return {
            "systemMessage": f"⚠️ BLOCKED: Missing required fields: {missing}",
            "hookSpecificOutput": { "permissionDecision": "deny", ... }
        }
    
    return {}
```

### Wiring
```python
# Registered in ClaudeAgentOptions
hooks={
    "PreToolUse": [
        HookMatcher(matcher="*", hooks=[malformed_tool_guardrail_hook]),
    ],
}
```

---

## 2. PostToolUse Observers (Async Pattern)

Since we treat tool execution as the "source of truth," we use **Observers** to capture outputs *after* they happen. This ensures we capture exactly what the agent saw.

### Key Characteristics
-   **Non-blocking**: Uses `asyncio.create_task()` to run in the background.
-   **Zero Latency**: Does not delay the agent's next thought.
-   **Fire-and-Forget**: Failures in observers do not crash the agent.

### Active Observers
| Observer Name | Triggers On | Action |
|---------------|-------------|--------|
| `observe_and_save_search_results` | Search tools (`COMPOSIO_SEARCH_*`) | Parses JSON, saves cleaned results to `search_results/` |
| `observe_and_save_workbench_activity` | `COMPOSIO_REMOTE_WORKBENCH` | Logs code submission and execution results |
| `observe_and_save_work_products` | `Write` / `write_local_file` | Copies reports/artifacts to persistent storage |
| `observe_and_save_video_outputs` | Video tools | Copies generated media to `work_products/media/` |

---

## 3. Tool Definition & Disable Strategy

We control *which* tools are available to the agent at the **source definition level** first, and via **blacklists** second.

### Disabling Tools
To prevent the agent from using a problematic tool (e.g., `write_local_file` for large reports), we prefer **removing it from the source** over blacklisting:

1.  **Source Removal**: Comment out `@mcp.tool()` in `mcp_server.py`.
    *   *Effect*: Tool acts as if it doesn't exist. Agent sees only valid alternatives (e.g., native `Write`).
2.  **Blacklisting**: Add to `DISALLOWED_TOOLS` in `agent_core.py`.
    *   *Effect*: Tool exists but execution is blocked. Can confuse the agent if it sees the tool in the list.

### Recommendation: Native Tools
For large file I/O (>50KB), we explicitly disable custom MCP tools and rely on Claude's **native `Write` and `Read` tools**, which utilize highly optimized "overflow" mechanisms for handling massive contexts.

---

## Recommendations for Future Improvements

Based on recent stress testing, here are recommended enhancements to the hook architecture:

### 1. Post-Tool Output Validation Hook
**Problem**: The agent sometimes generates empty calls (0 bytes) under heavy load.
**Solution**: Implement a `PostToolExecution` hook that checks for:
-   Empty outputs
-   Truncated JSON
-   Repeated error patterns
**Action**: If detected, inject a *new* system message into the context to prompt the agent to retry with a specific strategy (e.g., "Your last call was empty. Try chunking the content.").

### 2. Token Budget Guardrail
**Problem**: Unlimited search loops can drain budgets.
**Solution**: A `PreToolUse` hook that tracks accumulated token usage or tool call counts.
**Action**: Deny new costly tool calls (like Search or Crawl) if a session budget is exceeded, forcing the agent to proceed with "best available info."

### 3. Prompt Injection Shield
**Problem**: External content (web pages) might contain "Ignore previous instructions" attacks.
**Solution**: A `PostToolUse` observer that scans incoming tool results (search snippets, crawled text) for adversarial patterns before they enter the context.

### 4. Smart Retry Logic
**Problem**: The agent often retries the exact same failed call.
**Solution**: A hybrid Pre/Post hook system that caches failed calls. If the agent attempts the exact same input validation failure twice, the hook intervenes with a more forceful "Stop and Think" system message.

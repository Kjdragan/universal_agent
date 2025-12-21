# 010: Lessons Learned

> [!NOTE]
> This document captures important implementation learnings specific to this project that may not be in standard training data. It is continuously updated as we discover new patterns and gotchas.

---

## Composio SDK & Tool Router

### Lesson 1: MCP Mode vs Native Tool Mode - Hooks Behavior
**Date**: 2025-12-21

**Discovery**: Composio hooks (`@before_execute`, `@after_execute`) only work in **Native Tool Mode**, not in **MCP Mode**.

| Mode | Hooks Fire? | Why |
|------|-------------|-----|
| Native (`session.tools()`) | ✅ Yes | Local SDK is in execution path |
| MCP (`session.mcp.url`) | ❌ No | Remote server executes, bypasses local SDK |

**Implication**: When using Claude Agent SDK with MCP server (our current architecture), you cannot intercept/transform tool data before Claude sees it using Composio hooks.

**Workaround**: Use the **Observer Pattern** - process results asynchronously after they return to the client. See `observe_and_save_search_results()` in `main.py`.

---

### Lesson 2: Native Tool Mode Requires Explicit Modifier Passing
**Date**: 2025-12-21

**Discovery**: Even in Native Tool Mode with `AnthropicProvider`, the `@after_execute` decorator doesn't auto-register. You must **explicitly pass modifiers** to `handle_tool_calls()`:

```python
# ❌ Decorator alone doesn't work for non-agentic providers
@after_execute(tools=["COMPOSIO_SEARCH_NEWS"])
def my_hook(tool, toolkit, result):
    ...

# ✅ Must pass explicitly
result = composio.provider.handle_tool_calls(
    user_id=user_id,
    response=response,
    modifiers=[my_hook]  # Required!
)
```

**Reference**: See `tests/test_native_tool_hooks.py` for working example.

---

### Lesson 3: MULTI_EXECUTE_TOOL Response Structure
**Date**: 2025-12-21

**Discovery**: When using `COMPOSIO_MULTI_EXECUTE_TOOL`, the response has deeply nested structure:

```
{
  "successful": true,
  "data": {
    "results": [
      {
        "response": {
          "data": {
            "results": {
              "news_results": [...]  ← Actual data here
            }
          }
        }
      }
    ]
  }
}
```

**Path to news data**: `data.data.results[0].response.data.results.news_results`

**Implication**: Any observer or hook processing SERP results must handle this nesting.

---

### Lesson 4: MCP Tool Result Content Format
**Date**: 2025-12-21

**Discovery**: When receiving tool results via MCP in Claude SDK, the content is a **string representation of a list**:

```python
# What you receive:
content = "[{'type': 'text', 'text': '{\"successful\":true,...}'}]"

# Not valid JSON, it's Python repr format
# Use ast.literal_eval to parse, then extract 'text' field
```

**Solution in code**:
```python
if content.startswith("[{") and "'type': 'text'" in content:
    parsed_list = ast.literal_eval(content)
    for item in parsed_list:
        if item.get("type") == "text":
            raw_json = item.get("text", "")
```

---

## Computation vs Reasoning

### Lesson 5: Agent Time is Expensive - Use Hooks/Observers for Deterministic Work
**Date**: 2025-12-21

**Discovery**: Having the agent do deterministic data transformation wastes time and tokens:

| Approach | Latency | Token Cost |
|----------|---------|------------|
| Hook/Observer (Python) | ~10 ms | 0 |
| Agent inference | 2-5 seconds | ~1,000 |
| Agent + Code Interpreter | 5-8 seconds | ~2,000+ |

**Rule**: If the task is a pure function (same input → same output), do it in code, not via the agent.

**Examples of hook-worthy tasks**:
- Data cleaning/normalization
- Artifact saving
- Date format conversion
- Response compression
- Caching
- Validation/guardrails

---

## Python/SDK Patterns

### Lesson 6: Async Fire-and-Forget for Non-Blocking Observers
**Date**: 2025-12-21

**Pattern**: Use `asyncio.create_task()` for observers that shouldn't block the main loop:

```python
# Fire-and-forget - doesn't wait for completion
asyncio.create_task(observe_and_save_search_results(...))
```

**Benefit**: Zero latency impact on the agent conversation loop.

---

### Lesson 7: pyproject.toml Dev Dependencies Format
**Date**: 2025-12-21

**Discovery**: The `tool.uv.dev-dependencies` format is deprecated:

```toml
# ❌ Deprecated
[tool.uv]
dev-dependencies = []

# ✅ Current
[dependency-groups]
dev = []
```

---

## File Structure Conventions

### Lesson 8: Agent Workspace Organization
**Date**: 2025-12-21

**Convention established**:
```
AGENT_RUN_WORKSPACES/
└── session_YYYYMMDD_HHMMSS/
    ├── run.log           # Full console output
    ├── summary.txt       # Brief summary
    ├── trace.json        # Tool call/result trace
    └── search_results/   # Cleaned SERP artifacts
        └── *.json
```

---

## Agent Behavior

### Lesson 9: Preventing Agent Confirmation Prompts
**Date**: 2025-12-21

**Problem**: Claude's default behavior is to ask for confirmation before sensitive actions like sending emails:
```
"Should I proceed with sending this email?"
```

**Solution**: Add explicit instructions to the system prompt:

```python
system_prompt=(
    "IMPORTANT EXECUTION GUIDELINES:\n"
    "- When the user requests an action (send email, upload file, execute code), "
    "proceed immediately without asking for confirmation.\n"
    "- The user has already authorized these actions by making the request.\n"
    "- Do not ask 'Should I proceed?' or 'Do you want me to send this?'\n"
    "- Complete the full task end-to-end in a single workflow."
)
```

**Note**: Even with `permission_mode="bypassPermissions"`, Claude may still ask for confirmation out of caution. The system prompt override is needed.

---

*Last updated: 2025-12-21*

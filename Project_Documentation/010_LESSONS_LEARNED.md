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

### Lesson 10: Handling Large Data from Composio (Data Previews)
**Date**: 2025-12-21

**Discovery**: For large payloads (e.g., broad search results or large file reads), Composio SDK often returns a **truncated preview** to the LLM context to conserve tokens, while saving the full data to a file on the Remote Workbench.

**Signs of Truncation**:
1. The tool result contains a `data_preview` key instead of `data`.
2. The stdout says "**Saved large response to /home/user/.composio/mex/pond.json**".
3. The content is noticeably short or summarized.

**Implication**: If the Agent attempts to write a report based solely on this tool result, it will be using low-fidelity data.

**Solution**:
1. **Explicit Download**: The Agent **MUST** use `workbench_download` to fetch the referenced remote file (e.g., `pond.json`) to the local environment to access the full dataset.
2. **System Prompting**: Explicitly instruct the Agent that `data_preview` = "Incomplete" and compel it to download the source file for "Heavy Lifting" tasks.
3. **Observer Updates**: Logic parsing tool results (like `observe_and_save_search_results`) must handle both `data` (full) and `data_preview` (partial) keys to avoid crashing or missing artifacts.

**Pattern**: "Sync (Download Full Data) -> Think (Process Locally) -> Inject". Do not "Think" on the Preview.

---

### Lesson 11: Composio Planner Scope & Implicit Handoff
**Date**: 2025-12-21

**Discovery**: The Composio Planner (`COMPOSIO_SEARCH_TOOLS` / `COMPOSIO_MULTI_EXECUTE_TOOL`) effectively plans for *Composio-available* (Remote) tools but often omits valid local steps (like Sub-Agent delegation, File I/O, or Report Generation).

**Observation**:
- In a "Research -> Report -> Email" workflow, the Composio planner might only list:
  1. `COMPOSIO_SEARCH_NEWS` (Remote)
  2. `GMAIL_SEND_EMAIL` (Remote)
- It may **skip** the "Generate Report" step if that capability isn't exposed as a remote tool.

**Mechanism**:
- This is acceptable functionality. The **Claude Agent SDK** maintains the full context ("Master Brain") and successfully identifies the missing logical step.
- The Agent sees the remote tool finish ("Fetched News"), recognizes the original goal ("Write Report"), and autonomously selects a **Local Tool** (e.g., `Task` for sub-agent) to fill the gap.

**Takeaway**: Do not expect the Remote Planner to be the comprehensive "Master Plan". Treat it as a "Sub-Planner" for remote actions. Trust the Agent to perform the "Implicit Handoff" back to local logic.

---

### Lesson 12: Tool Documentation & Examples Are Critical
**Date**: 2025-12-21

**Discovery**: LLM agents (including Claude) can misuse tools if the API isn't clearly documented with **concrete examples**. During testing, `workbench_download` failed because the implementation used outdated Composio SDK parameters (`action`, `params`, `entity_id`) instead of the current API (`slug`, `arguments`, `user_id`).

**Problem**: The tool's docstring only described **what** parameters to pass, not **how** to structure the call or which SDK version to use.

**Solution**:
1. **Add Working Examples**: Every MCP tool docstring should include a concrete, copy-paste example showing:
   - Exact parameter names
   - Realistic values (including placeholders like `{CURRENT_SESSION_WORKSPACE}`)
   - Expected context (e.g., "After COMPOSIO_MULTI_EXECUTE_TOOL saves results...")
2. **Document Best Practices**: Include guidance on path conventions, session_id reuse, and when to use the tool
3. **Version-Specific Guidance**: If the SDK API changes, update examples immediately to avoid runtime errors

**Impact**: With enhanced docstrings, the agent can:
- **Self-correct**: Understand proper syntax from examples
- **Avoid errors**: Match working patterns instead of guessing
- **Maintain workflows**: Session IDs and paths flow correctly through multi-step processes

**Pattern**: "Show, Don't Just Tell" – A working example is worth a thousand words of API documentation.

---

## Web Extraction (webReader)

### Lesson 13: [DEPRECATED] Z.AI webReader
**Date**: 2025-12-22
**Status**: REMOVED - Replaced by `crawl_parallel` (crawl4ai).

### Lesson 14: [DEPRECATED] Domain Blacklist
**Date**: 2025-12-22
**Status**: REMOVED - `crawl_parallel` handles failures internally.

### Lesson 15: [DEPRECATED] webReader `retain_images=false`
**Date**: 2025-12-22
**Status**: REMOVED.

---

## Report Quality

### Lesson 16: Report Quality Standards for LLM Output
**Date**: 2025-12-22

**Discovery**: LLMs tend to produce generic, vague reports without explicit quality guidelines. Concrete examples in the prompt dramatically improve output quality.

**Key Guidelines Added to Sub-Agent Prompt**:

| Do This ✅ | Don't Do This ❌ |
|-----------|-----------------|
| "GPT-5.2 achieved 70.7% on GDPval" | "The model performed well" |
| "Trained on 9.19M videos vs 72.5M" | "Uses less data" |
| Quote: "biggest dark horse in open-source LLM" | "DeepSeek is competitive" |
| "December 11, 2025" (specific date) | "Recently released" |

**Structure Requirements**:
- Executive Summary with highlight box
- Table of Contents with anchor links
- Thematic sections (NOT source-by-source)
- Summary data table
- Modern HTML with gradients, info boxes, stats cards

---

### Lesson 17: Hard Limits for Sub-Agent Extraction
**Date**: 2025-12-22

**Discovery**: Without explicit limits, sub-agents may over-extract (e.g., 18 articles when 10 was intended), wasting time on slow webReader calls.

**Solution**: Add hard stop rules to sub-agent prompt:
- **10 successful extractions** → STOP immediately
- **2 batches completed** → STOP even if <10 successful
- **Count after each batch** → Don't wait for all before deciding

**Enforcement**: This is prompt-based, not code-enforced. LLM compliance is sufficient when instructions are clear.

---

### Lesson 18: SubagentStop Hook for Sub-Agent Completion
**Date**: 2025-12-23

**Problem**: When the main agent calls `Task` to delegate work, the sub-agent appears to start but the main agent exits the loop prematurely. The `Task` tool is **non-blocking**—it returns immediately with the sub-agent's initial response.

**Solution**: Use the `SubagentStop` hook to get notified when sub-agents complete:

```python
from claude_agent_sdk.types import HookMatcher, HookContext, HookJSONOutput

async def on_subagent_stop(
    input_data: dict, tool_use_id: str | None, context: HookContext
) -> HookJSONOutput:
    # Verify artifacts, return system message with next steps
    return {"systemMessage": "✅ Sub-agent completed. Next: upload and email..."}

options = ClaudeAgentOptions(
    hooks={
        "SubagentStop": [HookMatcher(matcher=None, hooks=[on_subagent_stop])],
    },
)
```

**Benefits over TaskOutput polling**:
| TaskOutput Polling | SubagentStop Hook |
|---|---|
| LLM must remember to call | Automatic callback |
| Error-prone | Guaranteed to fire |
| Blocking wait | Event-driven |

**Related**: See `on_subagent_stop()` in `main.py` for full implementation.

---

### Lesson 19: Toolkit Banning via Session Configuration
**Date**: 2025-12-23

**Problem**: The `COMPOSIO_SEARCH_TOOLS` planner was recommending Firecrawl and Exa toolkits for web scraping, even though we have local MCP tools (`mcp__local_toolkit__crawl_parallel`) that we want to use instead.

**Solution**: Use `toolkits={"disable": [...]}` when creating the Composio session:

```python
session = composio.create(
    user_id=user_id,
    toolkits={"disable": ["firecrawl", "exa"]}  # Ban crawling toolkits
)
```

**Configuration Options**:
| Option | Usage |
|--------|-------|
| `toolkits=["github", "slack"]` | Whitelist only these toolkits |
| `toolkits={"disable": ["firecrawl"]}` | Ban specific toolkits |
| `toolkits={"enable": ["github"]}` | Explicit enable (same as list) |

**Result**: `COMPOSIO_SEARCH_TOOLS` no longer recommends banned toolkits in its execution plans, forcing use of our local MCP tools.

---

### Lesson 20: MCP Tag Filters for Tool Categorization
**Date**: 2025-12-23

**Discovery**: Composio v0.10.2 supports MCP tag filters for categorizing tools by behavior:

| Tag | Meaning |
|-----|---------|
| `readOnlyHint` | Tools that only read data |
| `destructiveHint` | Tools that modify or delete data |
| `idempotentHint` | Tools that can be safely retried |
| `openWorldHint` | Tools interacting with external/open-world entities (e.g., web search) |

**Usage**:
```python
session = composio.create(
    user_id='user_123',
    toolkits=['gmail', 'composio_search'],
    tags=['readOnlyHint', 'openWorldHint']  # Global filter
)
```

**Assessment**: For our use case, the explicit `toolkits={"disable": [...]}` approach is more targeted than tag filtering. Tags are useful for broader behavioral categorization.

---

### Lesson 21: Sub-Agent Tool Inheritance
**Date**: 2025-12-23

**Problem**: Sub-agents were not correctly discovering local MCP tools, instead defaulting to Composio tools or outputting XML tool representations.

**Root Cause**: Explicit `tools` field in `AgentDefinition` was limiting available tools.

**Solution**: Omit the `tools` field to enable inheritance:

```python
AgentDefinition(
    name="report-creation-expert",
    # tools=[...]  ← Remove this line to inherit ALL parent tools
    model="claude-sonnet-4-20250514",
    prompt="...",
)
```

**Key Insight**: Per Claude Agent SDK docs:
- **Omit `tools`** → Inherits ALL tools from parent (including MCP tools)
- **Specify `tools`** → Only those tools available (use simple names like `"Read"`, `"Bash"`)

**Complementary Fix**: Enhanced sub-agent prompt with explicit tool instructions to guide selection.

---

*Last updated: 2025-12-23 13:40 CST*


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

### Lesson 13: [OBSOLETE] Z.AI webReader
**Date**: 2025-12-22
**Status**: REMOVED. Replaced by `mcp__local_toolkit__crawl_parallel`.

### Lesson 14: [OBSOLETE] Domain Blacklist
**Date**: 2025-12-22
**Status**: REMOVED. `crawl_parallel` handles partial failures gracefully.

### Lesson 15: [OBSOLETE] webReader `retain_images=false`
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

### Lesson 17: UNLIMITED Parallel Extraction with crawl4ai
**Date**: 2025-12-23

**Update**: We have moved from `webReader` (slow) to `crawl_parallel` (fast/async).
**Old Rule**: "Limit to 10 URLs, use batches."
**New Rule**: **NO LIMITS. SCRAPE EVERYTHING.**

**Discovery**: The local MCP `crawl_parallel` tool uses `AsyncWebCrawler` which handles concurrency efficiently. It can process 20-30+ URLs in seconds.

**Policy**:
- **Always Scrape**: Never rely on snippets. If you have URLs, call `crawl_parallel` immediately.
- **No Batches**: Pass the full list of 20-50 URLs in a single call.
- **Full Context**: Accessing full markdown allows for significantly higher quality reports.

**Implementation**:
```python
# Sub-agent prompt
"Call `mcp__local_toolkit__crawl_parallel` DIRECTLY with ALL URLs found (no batch limit)."
"Our parallel scraper is instant - DO NOT skip this step. Scrape everything."
```

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

### Lesson 28: The Scout/Expert Hand-Off Protocol (Solving Context Bottlenecks)
**Problem:** A highly capable LLM (Main Agent) will often try to "do it all" if it has access to tools. If it searches and finds snippets, it may try to write the final report itself based on shallow data, bypassing the specialized Sub-Agent (Scraper).
**Root Cause:** "Mandatory Auto-Save" instructions inadvertently coerced the agent into generating low-quality reports just to have something to "save".
**Solution:**
1.  **Scout Role:** Explicitly define Main Agent as "Scout" who finds the *location* of data (`search_results/`) but is forbidden from processing it. "Don't dig the mine, just point to it."
2.  **Auto-Save Exception:** Explicitly FORBID the Main Agent from auto-saving "Reports". This removes the incentive to write them prematurely.
3.  **Location-Based Hand-Off:** Main Agent passes the directory path (`search_results/`) to the Sub-Agent, allowing the Sub-Agent to use `list_directory` + `read_local_file` to discover an unlimited number of URLs (bulk scraping), rather than relying on the Main Agent to cherry-pick a list.

---

### Lesson 29: Implicit Tool Selection Bias
**Date**: 2025-12-23
**Observation**: When multiple toolkits overlap (e.g. `gmail` and `outlook` both active), the Agent will choose based on context clues (e.g. recipient `@outlook.com` -> uses `outlook` tool).
**Impact**: Users might expect a specific tool (Gmail) but get another (Outlook) if the context is ambiguous.
**Solution**: If strict tool usage is required, either disable the unwanted toolkit in `session.create()` or explicitly prompt the agent (e.g. "Send using Gmail").


**Date**: 2025-12-23

**Problem**: Local MCP server tools (e.g., `mcp__local_toolkit__crawl_parallel`) were not available. Agent said "I don't have access to that tool" and fell back to webReader.

**Root Cause**: The `mcp_server.py` file had `@mcp.tool()` decorators registering tools, but was **missing** the critical `mcp.run()` call at the end.

**How FastMCP Works**:
```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("My Server")

@mcp.tool()
def my_tool(...):  # ← This REGISTERS the tool
    ...

# ❌ WRONG: File ends here - server never starts!

# ✅ CORRECT: Must start the stdio transport
if __name__ == "__main__":
    mcp.run(transport="stdio")  # ← REQUIRED!
```

**Why It Breaks**: Without `mcp.run()`, the Python process:
1. Registers tools in memory
2. Prints "Server starting..." 
3. Exits immediately
4. Claude SDK gets EOF on stdin → marks server as failed

**Fix**: Always add the `if __name__ == "__main__": mcp.run(transport="stdio")` block.

---

### Lesson 23: Composio Custom Tools Are In-Memory Only
**Date**: 2025-12-23

**Discovery**: The `@composio.tools.custom_tool` decorator creates tools that are **stored in memory only** and are **NOT** exposed through MCP endpoints.

**Per Official Documentation**:
> "Custom tools are stored in memory and are not persisted. They need to be recreated when the application restarts."

**What DOES Work**:
| Method | Works? | Notes |
|--------|--------|-------|
| `composio.tools.execute('SLUG', ...)` | ✅ | Direct Python call |
| `session.tools()` (framework adapters) | ✅ | Returns tool objects for LangChain/etc |
| `session.mcp.url` (Tool Router MCP) | ❌ | Remote server doesn't know about local functions |
| `composio.mcp.create()` with `allowed_tools` | ⚠️ | Only for Composio-registered tools, not local Python |

**Implication**: You cannot register a Python function locally and have it appear in the Composio MCP HTTP endpoint. The MCP is a **remote service** that only knows about tools registered with Composio's cloud.

**Solution**: Use a **local stdio MCP server** for custom Python tools.

---

### Lesson 24: Hybrid MCP Architecture (Composio + Local)
**Date**: 2025-12-23

**Pattern**: Use TWO MCP servers for full functionality:

```python
mcp_servers={
    # 1. Composio Tool Router - cloud tools (Gmail, Slack, GitHub, Search)
    "composio": {
        "type": "http",
        "url": session.mcp.url,
        "headers": {"x-api-key": os.environ["COMPOSIO_API_KEY"]},
    },
    # 2. Local MCP - custom Python tools (crawl_parallel, file ops)
    "local_toolkit": {
        "type": "stdio",
        "command": sys.executable,
        "args": ["/absolute/path/to/mcp_server.py"],
    },
}
```

**Tool Naming Convention**:
| Server | Tool Prefix | Example |
|--------|-------------|---------|
| composio | `mcp__composio__` | `mcp__composio__COMPOSIO_SEARCH_TOOLS` |
| local_toolkit | `mcp__local_toolkit__` | `mcp__local_toolkit__crawl_parallel` |

**Use Cases**:
- **Composio**: Cloud integrations, OAuth apps, remote workbench
- **Local**: Custom Python functions, file I/O, web scraping with crawl4ai

---

### Lesson 25: MCP Server Path Must Be Absolute or Resolvable
**Date**: 2025-12-23

**Problem**: Relative paths like `"args": ["src/mcp_server.py"]` can fail when the working directory changes.

**Solution**: Use absolute paths calculated from the module:

```python
# ✅ Correct: Calculate absolute path from __file__
"args": [os.path.join(os.path.dirname(os.path.dirname(__file__)), "mcp_server.py")]

# ❌ Risky: Relative path depends on cwd
"args": ["src/mcp_server.py"]
```

**Debugging Tip**: If MCP tools aren't discovered:
1. Check the path exists: `os.path.exists(path)`
2. Run the server manually: `python /full/path/mcp_server.py`
3. Verify it prints tool registration and waits (doesn't exit)

---

### Lesson 26: Composio MCP Config Dashboard (UI)
**Date**: 2025-12-23

**Discovery**: Composio provides a dashboard UI for creating MCP server configurations:
- Navigate to: https://app.composio.dev → Your Workspace → **MCP Configs**
- Can bundle multiple toolkits (Gmail, Slack, Google Photos, etc.)
- Generates an HTTP endpoint URL with API key authentication

**Limitations**:
- **Cloud-only**: Cannot include locally-defined Python functions
- **Composio toolkits only**: Must use their registered integrations

**When to Use**:
- Multi-tenant apps where each user gets subset of Composio toolkits
- Simplified auth management via dashboard instead of code
- Claude Desktop integration (external MCP config)

**When NOT to Use**:
- Custom Python functions (use local stdio MCP instead)
- Fine-grained dynamic tool control (use `composio.create()` in code)

---

### Lesson 27: Tool Router vs MCP Create - Key Differences
**Date**: 2025-12-23

**Two APIs That Sound Similar But Differ**:

| API | Purpose | Custom Tool Support |
|-----|---------|---------------------|
| `composio.create()` | Tool Router session | ❌ No (cloud-hosted) |
| `composio.mcp.create()` | MCP server config | ⚠️ `allowed_tools` for Composio tools only |

**`composio.create()` (Tool Router)**:
```python
session = composio.create(
    user_id="user_123",
    toolkits={"disable": ["firecrawl", "exa"]}
)
mcp_url = session.mcp.url  # HTTP endpoint
```
- Returns a `ToolRouterSession` with `.mcp.url`
- Best for: Dynamic per-user sessions with Composio tools

**`composio.mcp.create()` (MCP Config)**:
```python
server = composio.mcp.create(
    'my-server',
    toolkits=['gmail', 'slack'],
    allowed_tools=['GMAIL_SEND_EMAIL']
)
mcp_instance = server.generate('user_123')
```
- Returns a server config, then `.generate()` for per-user instance
- Best for: Pre-configured MCP servers with restricted tool sets

**Neither** can expose locally-registered Python functions. Use local stdio MCP for that.

---

### Lesson 30: The "Universal File Staging" Pattern
**Date**: 2025-12-23

**Problem**: Cloud-based tools (Gmail, Slack, Code Interpreter) running on remote infrastructure cannot access files on the Agent's local filesystem. Creating specific helpers for each tool (e.g., `prepare_email_attachment`, `upload_to_slack`) is brittle and non-scalable.

**Solution**: Implement a single, generic **"Teleport" Tool** (`upload_to_composio`) that serves all downstream consumers.

**Architecture**:
1.  **Stage (Teleport)**: Agent calls `upload_to_composio(path)`.
    -   *Logic*: Local File → Bridge → Remote Workbench → S3 Upload.
    -   *Output*: Returns `s3_key` (universal ID) and `s3_url`.
2.  **Act (Consume)**: Agent passes the `s3_key` to *any* cloud tool.
    -   `GMAIL_SEND_EMAIL(..., attachment={"s3key": "..."})`
    -   `SLACK_SEND_MESSAGE(..., attachments=[{"s3_key": "..."}])`

**Benefit**:
- **Decoupling**: The Agent separates "getting the file ready" from "using the file".
- **Robustness**: One tool to test/fix means higher reliability than N helpers.
- **Simplicity**: Agent mental model becomes "If I need to send a local file, I must stage it first."

---

*Last updated: 2025-12-23 17:15 CST*

---

### Lesson 31: Preventing Redundant Data Saves (Observer Feedback)
**Date**: 2025-12-24

**Problem**: The Agent was manually saving search results to the Remote Workbench (`sync_response_to_workbench=True` or manual upload) even though the Observer had already saved them locally. This doubled the latency and token cost.

**Solution**:
1.  **Observer Feedback**: When the Observer saves a file, it injects a warning into the context: `⚠️ Agent: DO NOT save search results again - already persisted locally.`
2.  **System Prompt**: Explicit instruction to trust the Observer's auto-save.

**Impact**: Saved ~100s per run.

---

### Lesson 32: One-Step Uploads with `upload_to_composio`
**Date**: 2025-12-24

**Problem**: The `write_local` -> `workbench_upload` -> `remote_script` -> `S3` pipeline for email attachments was brittle, prone to "file not found" errors, and took 3+ steps.

**Solution**: Created `mcp__local_toolkit__upload_to_composio`.
- **Single atomic call**.
- Handles path resolution, upload, and S3 signing.
- Returns the exact `s3_key` needed by Gmail/Slack tools.

**Pattern**:
```python
# Old (3 steps)
write_local(...)
workbench_upload(...)
script_to_s3(...)

# New (1 step)
upload_to_composio(path) -> returns {s3_key: "..."}
GMAIL_SEND_EMAIL(..., attachments=[{"s3_key": "..."}])
```

---

*Last updated: 2025-12-24*

---

### Lesson 33: Knowledge Base Injection vs Code Changes
**Date**: 2025-12-26

**Problem**: The `GMAIL_SEND_EMAIL` tool schema was confusing the agent (input `recipient` vs `recipient_email`).
**Observation**: We could have edited the Python code to wrap the tool, but that's brittle.
**Solution**: Updated `.claude/knowledge/composio.md`. The `load_knowledge()` system injected this guidance into the system prompt.
**Result**: Agent self-corrected instantly (0 retries).
**Lesson**: Fix "Brain" problems in the Knowledge Base (Markdown), not the Python code. Keep the codebase generic and the knowledge specific.

---

### Lesson 34: Smart Tool Routing via Skill Definition
**Date**: 2025-12-26

**Problem**: Ambiguity in "Create PDF" requests led to conflicts (e.g., trying to use Chrome to print Markdown).
**Solution**: Implemented **Conditional Logic** in `.claude/skills/pdf/SKILL.md`:
- IF input is `.html` -> MUST use Chrome.
- IF input is `.md` -> MUST use Pandoc.
**Benefit**:
- Decouples "Content Creation" (HTML vs MD) from "Conversion Tool".
- Prevents "Russian Roulette" errors.
- No code changes required in `main.py`.

---

### Lesson 35: Reconstructing Timelines from Tool Traces
**Date**: 2025-12-26

**Problem**: The raw `trace.json` separates `tool_calls` (requests) from `tool_results` (responses), making it hard to see the linear conversational flow in chronological order.

**Solution**: Use `iterations` as the anchor.
1. Iterate through `trace['iterations']` (1, 2, 3...)
2. Filter `tool_calls` where `iteration == i`
3. Match `tool_results` by ID (`tool_use_id == call_id`)
4. Sort by `time_offset_seconds`

**Result**: A perfectly reconstructed timeline ("Session Transcript") that shows exact cause-and-effect with timing. This is superior to simply listing calls or results separately.

---

### Lesson 36: MemoryManager Storage Path Consistency
**Date**: 2025-12-27

**Problem**: `LogfireFetch` service and `main.py` were writing to different SQLite databases because `MemoryManager()` was called with different (or default) `storage_dir` paths.

**Discovery**:
| Component | Path Used | Result |
|-----------|-----------|--------|
| `main.py` | `Memory_System_Data/` | ✅ |
| `LogfireFetch` | `Memory_System/data/` (default) | ❌ Split brain |

**Solution**: Always pass explicit `storage_dir` when instantiating `MemoryManager` from external services:
```python
storage_path = os.path.join(repo_root, "Memory_System_Data")
memory_manager = MemoryManager(storage_dir=storage_path)
```

**Lesson**: When multiple processes share a database, centralize the path as a constant or environment variable.

---

### Lesson 37: LogfireQueryClient API
**Date**: 2025-12-27

**Problem**: Initial implementation used `client.query(sql, as_pandas=True)` but `LogfireQueryClient` has no `.query()` method.

**Discovery**: The correct method is `query_json_rows(sql)` which returns `{'columns': [...], 'rows': [...]}`.

**Available Methods**:
| Method | Returns |
|--------|---------|
| `query_json_rows(sql)` | Dict with `rows` list of dicts |
| `query_json(sql)` | Column-oriented JSON |
| `query_arrow(sql)` | PyArrow Table |
| `query_csv(sql)` | CSV string |

**Import Path**: `from logfire.query_client import LogfireQueryClient` (re-exports from `logfire.experimental.query_client`)

---

### Lesson 38: Pydantic Settings Extra Fields
**Date**: 2025-12-27

**Problem**: FastAPI service failed on startup with `ValidationError` because `.env` file contained variables not defined in the Pydantic `Settings` class.

**Solution**: Add `extra = "ignore"` to the Settings Config:
```python
class Settings(BaseSettings):
    logfire_read_token: str
    
    class Config:
        env_file = ".env"
        extra = "ignore"  # ← Required
```

---

### Lesson 39: Relative Imports in Runnable Scripts
**Date**: 2025-12-27

**Problem**: `main.py` used `from .agent_college.integration import ...` but failed with "attempted relative import with no known parent package" when run directly.

**Cause**: Running `python src/universal_agent/main.py` treats it as a script, not a module.

**Solution**: Use absolute imports when the file is the entry point:
```python
# ❌ Fails when run as script
from .agent_college.integration import setup_agent_college

# ✅ Works always
from src.universal_agent.agent_college.integration import setup_agent_college
```

---

*Last updated: 2025-12-27*

### Lesson 40: Context Propagation in Subprocesses (Trace ID Mismatch)
**Date**: 2025-12-27

**Problem**: Logfire traces showed different Trace IDs for the Main Agent (e.g., `...328b`) and internal tool calls (e.g., `...d64b`).
**Root Cause**: Local MCP tools run in subprocesses or separate threads without explicit OpenTelemetry context propagation. the `trace_id` generated in the main process doesn't automatically flow to the child components.
**Implication**: You cannot query the full trace by a single ID. You must query by time window or tags.
**Solution (Future)**: Passes `traceparent` headers or context objects explicitly to subprocesses.

---

### Lesson 41: "Mandatory" vs "Encouraged" in System Prompts
**Date**: 2025-12-27

**Discovery**: When the prompt said "Creating visuals is HIGHLY ENCOURAGED", the agent often ignored it, preferring text synthesis.
**Fix**: Changed language to "**MANDATORY**: You MUST delegate to image-expert if visuals are requested."
**Result**: Agent behavior became consistent.
**Lesson**: LLMs optimize for the path of least resistance (text). To force tool usage (images, heavy compute), use "Must/Mandatory/Required" constraints, not "Should/Encouraged" suggestions.

---

### Lesson 42: Intermediate State Saving for UX
**Date**: 2025-12-27

**Problem**: The "Transcript" was only generated at the very end of the session. If the user ran a long task (10 mins) and wanted to check progress, they had to wait or quit only to find no file.
**Solution**: Injected a save call *inside* the main Request/Response loop.
**Pattern**:
```python
# Inside main loop
result = ...
transcript_builder.save(current_trace) # Snapshot
print("Intermediate transcript saved.")
```
**Benefit**: User gets real-time artifacts without breaking the session state.

---

---

### Lesson 40: Centralized Search Schema Registry
**Date**: 2025-12-29

**Problem**: Handling diverse JSON schemas from 15+ search tools (Web, News, Scholar, Shopping, Flights) using ad-hoc `if/else` conditions is brittle and unmaintainable. Adding a new tool required code changes in multiple places (Observer, specific Parsers).

**Solution**: Implement a single **Configuration Registry** (`SEARCH_TOOL_CONFIG`) that defines the parsing rules for every tool:
```python
SEARCH_TOOL_CONFIG = {
    "COMPOSIO_SEARCH_SCHOLAR": {"list_key": "articles", "url_key": "link"},
    "COMPOSIO_SEARCH_AMAZON":  {"list_key": "data",     "url_key": "product_url"},
    # ...
}
```

**Implementation**:
- **Observer (`main.py`)**: Uses config to normalize data before validating/saving.
- **Backend (`mcp_server.py`)**: Uses the same config to extract URLs for crawling.

**Benefit**:
- **Robustness**: Unknown schemas log warnings instead of crashing.
- **Scalability**: New tools work instantly by adding one line to the config.

---

### Lesson 41: Just-in-Time (JIT) Delegation Guide Rails
**Date**: 2025-12-29

**Problem**: The "Scout" agent would search, see 3-4 snippets in the search result JSON, and immediately try to write a "Report" based on those snippets, ignoring the "Delegate to Expert" instruction. The temptation of available text was too strong to resist.

**Solution**: **Active Observer Intervention**.
When the Observer detects it has saved a search result file, it injects a high-priority system message into the conversation stream:

```
✅ Search Results Saved for Sub-Agent.
⚠️ STOP. Do not summarize these snippets. They are incomplete.
👉 ACTION REQUIRED: Call 'Task' tool to delegate to 'report-creation-expert' for full analysis.
```

**Result**:
- The agent reads this "Traffic Sign" and immediately stops its thought process.
- It executes the `Task` tool as commanded.
- **Success Rate**: 100% in testing (vs ~40% with prompt-only instructions).

**Key Takeaway**: Don't rely on the System Prompt (static) for critical workflow branching. Use **Dynamic Context Injection** (Observer) to guide the agent *exactly when it matters*.

### Lesson 40: Static Knowledge Base vs Dynamic Hooks (The JIT Solution)
**Date**: 2025-12-29

**Problem**: We needed to force the agent to delegate to a sub-agent ONLY when search results were returned, to prevent it from summarizing snippets.
**Failed Approach**: Using `PostToolUse` hooks or Observers to inject a "Please delegate" message.
- **Why it failed**: Claude often batches tool calls or generates "Thinking" tokens immediately after tool execution. The hook fired too late (race condition), and the agent had already decided to summarize the snippets.

**Successful Approach**: **Static Knowledge Base Injection**.
- We created `.claude/knowledge/report_workflow.md` which explicitly states: "When you receive search results, they are INCOMPLETE snippets. You MUST delegate."
- We load this file into the System Prompt at startup.

**Lesson**: Don't try to "catch" the agent with code hooks during runtime execution flow. It's brittle. Instead, **pre-program the behavior** into the immutable System Prompt (Knowledge Base). If the rule is in the "Brain" from the start, the agent follows it 100% of the time.

---

### Lesson 41: Dual Trace Observability (MCP Subprocesses)
**Date**: 2025-12-29

**Discovery**: When using `stdio` aggregation for Local MCP servers, you effectively have two distinct processes:
1.  **Main Agent**: The orchestrator (CLI/Telegram).
2.  **MCP Server**: The subprocess running the tools.

**Logfire Behavior**: Because `stdio` doesn't automatically propagate distributed tracing headers like HTTP, Logfire treats these as **two separate traces**.
- **Trace A**: Main Agent calls `mcp__local_toolkit__crawl_parallel`.
- **Trace B**: MCP Server receives request and executes `crawl_parallel`.

**Workaround**: We accept the "Dual Trace" reality.
- Use the **Time Range** and **Tool Name** to correlate them if needed.
- This actually enables better isolation: if the MCP server crashes, the Main Agent trace remains clean and readable, showing only the error received from the pipe.

---

### Lesson 43: Batch File Reading with `read_research_files`
**Date**: 2025-12-30

**Problem**: After `crawl_parallel` generates 30+ `crawl_*.md` files, the agent was making **individual `read_local_file` calls** for each file. This took ~25 seconds and generated excessive tool calls.

**Discovery**: We had a batch reading tool `mcp__local_toolkit__read_research_files` that could read all files in 1 call, but the agent wasn't using it.

**Root Cause**: The Knowledge Base didn't instruct the agent to use the batch tool.

**Solution**: Updated `.claude/knowledge/report_workflow.md` with explicit instructions:

```markdown
## Critical: Batch File Reading

After `crawl_parallel` creates multiple `crawl_*.md` files:

⚠️ **DO NOT** call `read_local_file` individually for each crawled file.
✅ **DO** use `read_research_files` to read all files at once.
```

**Results**:
| Metric | Before | After |
|--------|--------|-------|
| Tool Calls | 30+ individual | 6 batch |
| Time | ~25 seconds | ~0.4 seconds |
| **Savings** | - | **~24.6 seconds** |

**Key Features of `read_research_files`**:
1. **Context Overflow Protection**: Stops at 25,000 words to prevent context overflow
2. **Parallel Batching**: Agent can call multiple times in parallel (6 files per call)
3. **Clear Output**: Returns word counts and file markers for easy parsing

**Lesson**: When you have a Python function that can replace many agent tool calls, **document it in the Knowledge Base**. The agent will follow explicit instructions to use efficient patterns.

---

*Last updated: 2025-12-30*

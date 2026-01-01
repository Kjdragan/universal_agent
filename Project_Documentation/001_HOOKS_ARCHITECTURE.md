# 004: Hooks Architecture - Computation vs Reasoning

## The Core Insight

Efficient agent systems separate **deterministic computation** from **LLM reasoning**. Tasks that can be described as pure functions (same input → same output) should execute as code, not consume LLM tokens.

| Task Type | Execute As | Examples |
|-----------|------------|----------|
| **Deterministic** | Python hook/observer | Data cleaning, formatting, caching, validation, artifact saving |
| **Reasoning** | LLM inference | Decision-making, synthesis, creative generation |

---

## Universal Agent's Implementation: Observer Pattern

**This project uses MCP Mode exclusively**, which means traditional Composio hooks (`@before_execute`, `@after_execute`) **do not fire**. Instead, we implement the **Observer Pattern** for post-execution processing.

### Why Observers Instead of Hooks?

| Aspect | Composio Hooks | Observer Pattern (Our Implementation) |
|--------|----------------|---------------------------------------|
| **Compatibility** | Native Tool Mode only | Works with MCP Mode ✅ |
| **Execution Point** | Before/during tool call | After tool result returns |
| **Agent Visibility** | Can transform data before agent sees it | Agent sees raw data |
| **Latency Impact** | Can block tool execution | Zero (async fire-and-forget) |
| **Use Cases** | Input validation, caching | Artifact saving, logging, analytics |

---

## Active Observer Functions

The project implements **4 observer functions**, all triggered in the conversation loop when tool results return (see `main.py:1529-1562`):

### 1. `observe_and_save_search_results`
**Purpose**: Parse and save cleaned SERP (Search Engine Results Page) artifacts.

**Triggers on**: Any search-related tool (COMPOSIO_SEARCH, SERPAPI, TAVILY, EXA, etc.)

**What it does**:
-   Filters out tool discovery searches (`COMPOSIO_SEARCH_TOOLS`)
-   Parses JSON from multiple search provider schemas
-   Handles nested `MULTI_EXECUTE_TOOL` responses
-   Extracts URLs from different field names (`url`, `link`, `product_url`, etc.)
-   Saves cleaned JSON to `search_results/<tool>_<timestamp>.json`
-   Saves individual URLs to `search_results/urls.txt` for batch crawling

**Performance**:
-   Async (non-blocking)
-   Processes 100s of search results in ~50ms
-   Zero token cost

**Example Saved Artifact**:
```json
{
  "tool": "COMPOSIO_SEARCH_NEWS",
  "query": "Russia Ukraine war latest",
  "timestamp": "2025-12-31T10:15:30",
  "results": [
    {
      "title": "Ukraine Reports Missile Strike...",
      "url": "https://example.com/article1",
      "snippet": "Latest developments..."
    }
  ],
  "url_count": 25
}
```

---

### 2. `observe_and_save_workbench_activity`
**Purpose**: Capture code execution activity from `COMPOSIO_REMOTE_WORKBENCH`.

**Triggers on**: `COMPOSIO_REMOTE_WORKBENCH` tool calls

**What it does**:
-   Logs code input (truncated to 1000 chars for readability)
-   Captures stdout, stderr, and execution results
-   Records session_id for debugging
-   Saves to `workbench_activity/workbench_<timestamp>.json`

**Use Case**: Debug remote code execution failures without re-running expensive agent loops.

**Example Saved Artifact**:
```json
{
  "timestamp": "2025-12-31T10:20:15",
  "tool": "COMPOSIO_REMOTE_WORKBENCH",
  "input": {
    "code": "import pandas as pd\ndf = pd.read_csv(...)",
    "session_id": "session_abc123"
  },
  "output": {
    "stdout": "Processing 1000 rows...",
    "stderr": "",
    "successful": true
  }
}
```

---

### 3. `observe_and_save_work_products`
**Purpose**: Copy work product reports to persistent storage outside session workspaces.

**Triggers on**: `write_local_file` calls targeting `work_products/` directory

**What it does**:
-   Monitors file writes to `work_products/` directory
-   Copies reports to persistent `SAVED_REPORTS/` directory (survives session cleanup)
-   Adds timestamp suffix for uniqueness
-   Waits 0.5s for file to finish writing before copying

**Use Case**: Preserve critical reports (e.g., research summaries, PDFs) even after workspace cleanup.

**Example**:
```
Source:      session_20251231_102030/work_products/ukraine_report.pdf
Destination: SAVED_REPORTS/ukraine_report_20251231_102045.pdf
```

---

### 4. `observe_and_save_video_outputs`
**Purpose**: Copy video/audio outputs to session workspace for persistence.

**Triggers on**: Video tools (`trim_video`, `download_video`, `add_text_overlay`, etc.)

**What it does**:
-   Detects video tool completion
-   Extracts output path from tool input or result message
-   Filters out intermediate files (`temp_`, `_part`, etc.)
-   Copies final videos to `work_products/media/`

**Use Case**: Preserve generated media files in session workspace.

**Example**:
```
Download → /tmp/video_12345.mp4
Observer → session/work_products/media/video_12345.mp4
```

---

## Implementation Pattern

All observers follow this pattern in the conversation loop (`main.py:1529-1562`):

```python
# In the main conversation loop, after receiving ToolResultBlock:
if tool_name and OBSERVER_WORKSPACE_DIR:
    # Fire-and-forget async observers (zero latency)
    asyncio.create_task(
        observe_and_save_search_results(
            tool_name, block_content, OBSERVER_WORKSPACE_DIR
        )
    )
    asyncio.create_task(
        observe_and_save_workbench_activity(
            tool_name, tool_input or {}, content_str, OBSERVER_WORKSPACE_DIR
        )
    )
    asyncio.create_task(
        observe_and_save_work_products(
            tool_name, tool_input or {}, content_str, OBSERVER_WORKSPACE_DIR
        )
    )
    asyncio.create_task(
        observe_and_save_video_outputs(
            tool_name, tool_input or {}, content_str, OBSERVER_WORKSPACE_DIR
        )
    )
```

**Key Characteristics**:
-   **Non-blocking**: `asyncio.create_task()` returns immediately
-   **Zero latency impact**: Observers run in background
-   **Safe**: Exceptions caught and logged via Logfire
-   **Selective**: Each observer filters for relevant tools only

---

## Performance Comparison

| Approach | Latency | Token Cost | When to Use |
|----------|---------|------------|-------------|
| **Observer (Async)** | ~0 ms (non-blocking) | 0 | Artifact saving, logging, analytics |
| **Composio Hook** | ~10 ms | 0 | Input validation, caching (Native Mode only) |
| **Agent Reasoning** | 2-5 seconds | ~1,000 | Decision-making, synthesis |

**Rule of Thumb**: If the operation is deterministic and doesn't need to modify what the agent sees, use an observer.

---

## Composio Hooks (For Reference)

> [!NOTE]
> The following hooks are **NOT used in this project** because we operate in MCP Mode. This section is retained for reference.

Composio provides three decorator-based hooks that intercept tool execution **in Native Tool Mode**:

### `@before_execute`
Runs **before** the tool executes.
```python
from composio import before_execute

@before_execute(tools=["GMAIL_SEND_EMAIL"])
def audit_emails(tool: str, toolkit: str, request: dict) -> dict:
    print(f"[AUDIT] Sending email to: {request['arguments']['recipient']}")
    return request
```

### `@after_execute`
Runs **after** the tool executes.
```python
from composio import after_execute

@after_execute(tools=["COMPOSIO_SEARCH_NEWS"])
def clean_and_save(tool: str, toolkit: str, result: dict) -> dict:
    # Transform and save
    return modified_result
```

### `@schema_modifier`
Modifies tool schemas before presentation to the agent.

---

## MCP Mode vs Native Tool Mode

| Aspect | Native Tool Mode | MCP Mode (Our Implementation) |
|--------|------------------|-------------------------------|
| **Tool Definition** | `session.tools()` | `session.mcp.url` |
| **Execution** | Local SDK via `handle_tool_calls()` | Remote Composio server |
| **Hooks** | ✅ Fire normally | ❌ Bypassed |
| **Alternative** | N/A | Observer Pattern ✅ |

---

## Design Principle

```
┌─────────────────────────────────────────────────────┐
│  REASONING LAYER (Agent)                            │
│  Decide • Synthesize • Judge • Create               │
│  Cost: Seconds, thousands of tokens                 │
└─────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│  DETERMINISTIC LAYER (Observers)                    │
│  Transform • Parse • Save • Log • Archive           │
│  Cost: Milliseconds, zero tokens, zero latency      │
└─────────────────────────────────────────────────────┘
```

**Rule**: If the operation is a pure function that doesn't need to modify agent input, use an observer.

# 008: Evolution of MCP Visibility - Subprocess vs. In-Process

This document outlines the architectural shift in the Universal Agent's MCP (Model Context Protocol) implementation, moving from "Silent Subprocesses" to "Vocal In-Process Tools" to improve developer visibility and debugging.

## The Problem: The "Silent" Subprocess
Previously, our local tools (like the research pipeline) were registered via a `stdio` server in `agent_setup.py`:

```python
"local_toolkit": {
    "type": "stdio",
    "command": sys.executable,
    "args": ["src/mcp_server.py"],
}
```

### Why it was "Silent":
1.  **Process Isolation**: The tool ran in a separate operating system process.
2.  **Stdio Buffering**: The `stdout` and `stderr` of that process were captured by the MCP client (the Agent SDK) to facilitate the JSON-RPC communication.
3.  **Delayed Delivery**: The agent's terminal only showed the tool's output *after* the entire function finished and returned its JSON response. 
4.  **The Result**: If a research task took 3 minutes, the user saw "⏳ Waiting for response..." for 3 minutes with zero feedback, even if the tool was printing progress bars or logs internally.

---

## The Solution: In-Process SDK Tools
We migrated critical tools (like `run_research_pipeline` and `crawl_parallel`) to run **In-Process** using the Claude Agent SDK's native `@tool` and `create_sdk_mcp_server` capabilities.

### Why it is "Vocal":
1.  **Shared Process**: The tool logic now runs inside the same process as the Main Agent.
2.  **Direct Output**: When the tool calls `print()` or `sys.stderr.write()`, it writes directly to the **same terminal stream** being watched by the user.
3.  **Real-Time Feedback**: Progress logs (e.g., `[Pipeline] Step 2/5: Crawling...`) appear in the console **immediately** as they occur, providing a "vocal" experience.
4.  **Shared Memory**: The tool can directly access global state, databases, and session objects without complex serialization.

---

## Technical Implementation

### 1. The Bridge (`research_bridge.py`)
We created a wrapper module that marks functions as native SDK tools. It acts as a bridge between the core implementations in `mcp_server.py` and the Agent SDK:
```python
@tool(name="run_research_pipeline")
async def run_research_pipeline_wrapper(args):
    # This runs in-process!
    return await original_pipeline_function(**args)

@tool(name="crawl_parallel")
async def crawl_parallel_wrapper(args):
    # High-speed crawling visibility!
    return await original_crawl_function(**args)
```

### 2. The Internal Server (`agent_setup.py` / `main.py`)
Instead of an external command, we register an internal server object:
```python
"internal": create_sdk_mcp_server(
    name="internal",
    tools=[
        run_research_pipeline_wrapper,
        crawl_parallel_wrapper
    ]
)
```

### 3. Tool Routing & Hiding
To prevent the agent from accidentally using the old "silent" version, we:
- Renamed the old function in `mcp_server.py` to `_legacy`.
- Removed the `@mcp.tool()` decorator from the old version.
- Explicitly disallowed the old tool name in `DISALLOWED_TOOLS`.

---

## Log Levels: Control Output Verbosity
We have introduced structured log levels to the in-process tools to balance visibility with terminal cleanliness.

### Environment Variable: `UA_LOG_LEVEL`
- **`INFO` (Default)**: Shows critical progress markers (e.g., "Step 1/5: Crawling...").
- **`DEBUG`**: Shows exhaustive details (URLs, extraction stats, startup components).

### Usage in Code:
```python
mcp_log("Major progress step", level="INFO")
mcp_log("Highly atomic detail", level="DEBUG")
```

---

## Agent Toolkit & Self-Correction
To prevent agent "fumbling," we have standardized the toolkit for sub-agents (Research Specialist, Report Writer):
1.  **Always Available**: `Bash` and `local_toolkit__list_directory` are included by default for diagnostics.
2.  **Explicit Protocol**: The sub-agent instructions include a "Self-Correction" section directing them to use `Bash` to investigate failures rather than hallucinating new tool names.

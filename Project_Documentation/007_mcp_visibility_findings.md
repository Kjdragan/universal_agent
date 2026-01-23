# MCP Visibility & Logging Findings

**Date:** January 23, 2026
**Status:** Approved for Implementation

## 1. The Issue
Users observed a significant "silence" (approx. 128 seconds) in the `run.log` and terminal during long-running MCP tool executions, specifically `run_research_pipeline`. 

- **Symptom:** The agent invokes the tool, and no output appears until the tool finishes 2+ minutes later.
- **Cause:** The `local_toolkit` MCP server runs as a **subprocess** via `stdio` transport. The Claude Agent SDK captures the subprocess's `stderr` but does not natively surface these logs to the user in real-time during the `query()` loop, effectively buffering or swallowing the progress indicators.

## 2. Research & Investigation

### DeepWiki Insights
We queried DeepWiki regarding the Claude Agent SDK's handling of external MCP servers:
> "The SDK does not directly handle stderr from external 'stdio' MCP servers... The SDK only handles stderr from the main Claude Code CLI process."

This confirmed that our architecture (Agent -> Subprocess MCP Server) was structurally preventing visibility unless we modified the SDK internals or the CLI's handling of pipes.

### Alternative Approaches Evaluated

#### A. FastMCP Context Logging (Rejected)
We considered using `ctx.info()` from the `FastMCP` framework.
- **Pros:** Standardized protocol way to emit logs.
- **Cons:** The Claude Agent SDK client (`query()` loop) does not explicitly handle `Notification` message types for logs. Using this would require patching the client message loop to "discover" these messages, which is unverified and risky.

#### B. In-Process SDK Tools (Selected)
DeepWiki recommended this approach:
> "SDK MCP servers run in-process (unlike external MCP servers...), so all your standard Python logging, print statements, and debugging tools work normally."

- **Pros:**
    - Zero subprocess boundary.
    - `sys.stdout` and `sys.stderr` are shared with the main agent process.
    - Standard `print()` statements work immediately.
    - Simplifies deployment (no separate server script management).

## 3. Verification Test
We created a proof-of-concept script `tests/verify_in_process_mcp.py` to validate the In-Process approach.

**Test Scenario:**
- Define a `@tool` that sleeps for 5 seconds and prints to `stdout` and `stderr`.
- Register it via `create_sdk_mcp_server`.
- Run an agent query invoking this tool.

**Results:**
The test confirmed that `[TOOL-STDOUT]` and `[TOOL-STDERR]` messages appeared in the terminal **interleaved** with the agent's execution logs.

```text
[AGENT] Calling Tool: mcp__test__simulate_long_task
[TOOL-STDOUT] Starting simulation for 5 seconds...
[TOOL-STDERR] Starting simulation (stderr)...
[TOOL-STDOUT] Progress: Step 1/5
...
[TOOL-STDOUT] Simulation complete!
```

## 4. Implementation Strategy
We will refactor the `universal_agent` to run the research pipeline in-process.

1.  **Refactor Server Code:** Ensure `src/mcp_server.py` exposes the `run_research_pipeline` function in a way that can be imported without triggering server startup side effects.
2.  **Update Agent Setup:** Modify `src/universal_agent/agent_setup.py` to:
    - Import the tool function.
    - Wrap it with `@tool`.
    - Register it via `create_sdk_mcp_server`.
    - Remove the configuration for the external `local_toolkit` subprocess (or keep it for legacy tools, but remove the duplicated research tool).

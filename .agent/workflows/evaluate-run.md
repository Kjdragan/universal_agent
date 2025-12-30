---
description: Evaluate the latest run traces using LogFire MCP and agent workspace artifacts to report issues, performance, and bottlenecks.
---

1.  **Identify Latest Run**
    *   **For local runs**: List the `AGENT_RUN_WORKSPACES` directory in the repo root.
    *   **For Docker/Telegram runs**: List the Docker container's temp directory:
        ```bash
        docker exec universal_agent_bot ls -la /tmp/AGENT_RUN_WORKSPACES/
        ```
    *   Identify the most recent session directory (e.g., `session_YYYYMMDD_HHMMSS`).
    *   Set this as the `TARGET_SESSION`.
    *   **Note**: For Docker runs, prefix all file access commands with `docker exec universal_agent_bot`.

2.  **Extract Run Metadata**
    *   Read the **FULL** `TARGET_SESSION/run.log` file (use view_file without line limits).
    *   > [!IMPORTANT]
    *   > You MUST read the entire log file, not just the first portion. Critical errors often appear mid-file (e.g., malformed tool calls, recovery loops, context overflow).
    *   Extract the **Trace ID** from the log header.
    *   Extract the **Start Time** and **End Time**.
    *   Note any `tool_use_error`, `Error:`, or malformed tool names (e.g., `TOOLname</arg_key>`).


3.  **Analyze Logfire Traces**
    *   Use the `mcp_logfire_arbitrary_query` tool to analyze the `Trace ID`.
    *   > [!IMPORTANT]
    *   > **Dual Trace Architecture**: The agent run produces TWO separate traces:
    *   > 1. **Main Agent Trace** (`service_name='universal-agent'`): Contains Claude SDK calls, Composio HTTP requests, observer spans, conversation iterations.
    *   > 2. **Local Toolkit Trace** (`service_name='local-toolkit'`): Contains MCP tool execution (`crawl_parallel`, `read_local_file`, etc.) in a subprocess with its own trace ID.
    *   > 
    *   > To correlate: Use the session timestamp window (from run.log) to find both traces.
    
    ### Main Agent Trace (from run.log `Trace ID:` line)
    *   **Query 1 (Errors)**: `SELECT * FROM records WHERE trace_id='<TRACE_ID>' AND level >= 'warning'`
    *   **Query 2 (Performance)**: `SELECT span_name, duration, start_timestamp FROM records WHERE trace_id='<TRACE_ID>' AND span_name IN ('conversation_iteration', 'tool_call', 'tool_result') ORDER BY start_timestamp`
    *   **Query 3 (Observer Audit)**: `SELECT span_name, attributes->>'tool' as tool, attributes->>'path' as path FROM records WHERE trace_id='<TRACE_ID>' AND span_name IN ('observer_search_results', 'observer_workbench_activity', 'observer_work_products', 'observer_video_outputs')`

    ### Local Toolkit Trace (by timestamp window)
    *   **Find Trace ID**: `SELECT DISTINCT trace_id, MIN(start_timestamp), MAX(start_timestamp) FROM records WHERE service_name='local-toolkit' AND start_timestamp BETWEEN '<SESSION_START>' AND '<SESSION_END>' GROUP BY trace_id`
    *   **Crawl Details**: `SELECT span_name, duration, attributes->>'url' as url FROM records WHERE service_name='local-toolkit' AND span_name LIKE '%crawl%' ORDER BY start_timestamp`
    *   **MCP Tool Errors**: `SELECT * FROM records WHERE service_name='local-toolkit' AND level >= 'warning'`

4.  **Evaluate Workspaces**
    *   Check `TARGET_SESSION/search_results/` for saved artifacts vs. those mentioned in logs.
    *   Check `summary.txt` for the reported outcome.

5.  **Identify Deviations (Happy Path Analysis)**
    *   **Fallback**: Did it try "Fast Path" and fail to "Complex Path"?
    *   **Loops**: Did it loop excessively (>3 iterations)?
    *   **Errors**: Were there `is_error=True` in tool results?
    *   **Recovery**: Did the agent fix the error and proceed?

6.  **Generate Performance Report**
    *   Create a file `TARGET_SESSION/evaluation_report.md`.
    *   **Phase Performance Table**:
        | Phase | Duration (s) | Tools Used | Bottleneck? |
        |-------|--------------|------------|-------------|
        | Planning (Classification) | ... | ... | ... |
        | Execution (Search/Tools) | ... | ... | ... |
        | Processing (Local/Bridge) | ... | ... | ... |
        | Reporting (Final Output) | ... | ... | ... |
    *   **Deviation Log**: List off-path events and recovery success.

7.  **Summary**
    *   Output a brief summary of the evaluation to the user.

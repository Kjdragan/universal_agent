---
description: Evaluate the latest run traces using LogFire MCP and agent workspace artifacts to report issues, performance, and bottlenecks.
---

1.  **Identify Latest Run**
    *   List the `AGENT_RUN_WORKSPACES` directory.
    *   Identify the most recent session directory (e.g., `session_YYYYMMDD_HHMMSS`).
    *   Set this as the `TARGET_SESSION`.

2.  **Extract Run Metadata**
    *   Read `TARGET_SESSION/run.log` or `trace.json`.
    *   Extract the **Trace ID**.
    *   Extract the **Start Time** and **End Time**.

3.  **Analyze Logfire Traces**
    *   Use the `mcp_logfire_arbitrary_query` tool to analyze the `Trace ID`.
    *   > [!IMPORTANT]
    *   > **MCP Subprocess Traces**: Local MCP tools (like `local-toolkit`, `video-audio-mcp`) execute in subprocesses and currently have **different Trace IDs** than the main agent due to missing context propagation. To find detailed internal traces for these tools (e.g. `crawl_parallel` steps), you must query `SELECT * FROM records WHERE service_name='local-toolkit' ...` and filter by the session timestamp window. Do not rely solely on the main `Trace ID` to find these child spans.
    *   **Query 1 (Errors)**: `SELECT * FROM records WHERE trace_id='<TRACE_ID>' AND level >= 'warning'`
    *   **Query 2 (Performance)**: `SELECT span_name, duration, start_timestamp FROM records WHERE trace_id='<TRACE_ID>' AND span_name IN ('conversation_iteration', 'tool_call', 'tool_result') ORDER BY start_timestamp`
    *   **Query 3 (Tool Usage)**: Sequence of tools called.
    *   **Query 4 (Context & Phases)**:
        *   **Filter by Agent**: `SELECT * FROM records WHERE trace_id='<TRACE_ID>' AND (attributes->>'agent') = 'main'`
        *   **Filter by Step**: `SELECT * FROM records WHERE trace_id='<TRACE_ID>' AND (attributes->>'step') = 'execution'`
        *   **Sub-agent Activity**: `SELECT * FROM records WHERE trace_id='<TRACE_ID>' AND (attributes->>'is_subagent') = 'true'`
    *   **Query 5 (Observer Audit)**:
        *   **Artifacts & Compliance**: `SELECT span_name, attributes->>'tool' as tool, attributes->>'path' as path FROM records WHERE trace_id='<TRACE_ID>' AND span_name IN ('observer_search_results', 'observer_workbench_activity', 'observer_work_products', 'observer_video_outputs')`

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

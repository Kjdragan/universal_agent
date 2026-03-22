---
description: Review the latest agent runs via Logfire MCP to extract performance and bottleneck data.
---

# Logfire Review Workflow

Use this workflow to query Logfire MCP for recent agent traces, analyze them, and summarize findings. This helps identify latency bottlenecks, SQLite inefficiencies, and LLM usage.

## Step 1: Query Recent Traces

1. Use the Logfire MCP tool to query recent spans, specifically looking for root spans or high-level transaction spans.
   - Example Logfire query: 
     `SELECT trace_id, span_name, start_timestamp, end_timestamp, duration FROM spans WHERE parent_id IS NULL ORDER BY start_timestamp DESC LIMIT 5`
2. Identify the target `trace_id` for the run you want to investigate. Typically, this is the most recent complete session or the specific trace requested by the user.

## Step 2: Analyze the Run's Child Spans

3. Query the child spans of the identified `trace_id` to get a breakdown of operations:
   - Example Logfire query:
     `SELECT span_name, duration, attributes_json FROM spans WHERE trace_id = '<YOUR_TRACE_ID>' ORDER BY start_timestamp ASC`
   - You can also filter by duration to find slow operations:
     `SELECT span_name, duration FROM spans WHERE trace_id = '<YOUR_TRACE_ID>' AND duration > '500ms' ORDER BY duration DESC`

## Step 3: Investigate SQLite and LLM Interactions

4. Look specifically for root cause indicators in the execution trace:
   - **SQLite**: Filter for spans where `span_name` indicates an SQLite operation (e.g., `sqlite3.query`). Check their `duration` and frequency. Are there multiple small queries indicating an N+1 problem?
   - **LLM/Claude SDK**: Filter for spans related to LLM generation (e.g., originating from the `langsmith` or `claude-agent-sdk` integration). Inspect the `attributes_json` for prompt tokens, completion tokens, and generation latency.
   - **HTTP/MCP**: Identify any slow outgoing requests via `httpx` or external MCP connections.

## Step 4: Summarize the Findings

5. Generate a conversational summary or markdown artifact containing:
   - The total duration and overall success/failure of the analyzed run.
   - The highest latency operations (e.g., top 3 slowest spans).
   - A breakdown of time spent (e.g., DB vs. LLM vs. Network).
   - Any apparent anomalies, excessive token usage, or errors captured in the trace.

## Rules
- When writing queries for the Logfire MCP, ensure you use the supported subset of SQL that Logfire provides. 
- Do not guess the exact structure of `attributes_json` natively without exploring; do an exploratory query on a single span to see the available structured data.
- Always tie your findings back to the actual agent code or architecture to suggest actionable improvements.

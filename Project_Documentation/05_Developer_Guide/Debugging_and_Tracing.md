# Debugging and Tracing

## Logfire Observability
The Universal Agent is instrumented with **Pydantic Logfire**. This provides a "X-Ray" view into the agent's brain.

### Setting Up
Ensure your `.env` has a valid token:
```bash
LOGFIRE_TOKEN=your_token_here
```

### Essential Logfire Queries (SQL)
To debug effectively, use these SQL patterns in the Logfire UI:

**1. Find a Run by ID:**
```sql
SELECT * FROM records 
WHERE run_id = 'YOUR_RUN_ID' 
ORDER BY start_timestamp ASC
```

**2. See All Tool Calls (Filtered):**
```sql
SELECT span_name, attributes->>'tool_name' as tool, message 
FROM records 
WHERE trace_id = 'YOUR_TRACE_ID' 
  AND (span_name = 'ToolExecution' OR attributes ? 'tool_name')
ORDER BY start_timestamp ASC
```

**3. Debug Context Exhaustion (Empty Writes):**
```sql
SELECT * FROM records 
WHERE message LIKE '%zero-param Write%' 
   OR attributes->>'tool_name' = 'Write'
```

### Visibility Gaps & Troubleshooting
*   **Missing Tool Names?** Ensure `logfire.instrument_mcp()` is active.
*   **Scrubbed Data?** Logfire automatically scrubs fields that look like auth tokens. If your tool input JSON contains keys like `password` or `Authorization`, the values will be hidden.
*   **Orphaned Traces?** Sub-agents running in their own process (like Agent College) have their own Trace IDs. Look for `system_link` attributes to find the parent.

## Common Issues & Fixes

### 1. "Context Exhaustion" (Empty Write Calls)
*   **Symptom**: The agent tries to write a file but passes `content=""`.
*   **Cause**: The generated report was so long that it got truncated by the LLM's max output token limit *before* it finished the JSON tool call payload.
*   **Fix**: The agent has a `tool_output_validator_hook` that catches this. It will automatically instruct the agent to "chunk" the write (e.g., write the first 500 lines, then append).

### 2. Malformed Tool Calls
*   **Symptom**: `PreToolUse` hook fires a warning.
*   **Cause**: The LLM tried to invent a tool name like `Write(file="foo.txt")` instead of the proper JSON schema.
*   **Fix**: The `malformed_tool_guardrail_hook` intercepts this specific pattern, blocks execution, and returns a system error message telling the agent the correct syntax. You don't need to fix code; the agent usually self-corrects in the next turn.

### 3. Infinite Loops
*   **Symptom**: Agent keeps calling `ls -R` or `read_file` on the same file.
*   **Fix**: Check `tool_call_ledger` in the database. The system detects exact duplicate calls and will eventually trigger a `HarnessError` to restart the session/context.

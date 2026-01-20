# Debugging and Tracing

## Logfire Observability
The Universal Agent is instrumented with **Pydantic Logfire**. This provides a "X-Ray" view into the agent's brain.

### Setting Up
Ensure your `.env` has a valid token:
```bash
LOGFIRE_TOKEN=your_token_here
```

### What You Will See
1.  **Spans**:
    *   `UniversalAgent.run`: The top-level session.
    *   `ClaudeSDKClient.create_message`: The raw API call to Anthropic.
    *   `ToolExecution`: The specific function call (e.g., `subprocess.run`).
2.  **Attributes**:
    *   `input_tokens` / `output_tokens`: Cost tracking.
    *   `tool_name`: Which tool was called.

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

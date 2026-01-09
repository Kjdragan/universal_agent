# Composio Tool Knowledge

## GMAIL_SEND_EMAIL

### HOW TO CALL (CRITICAL - READ FIRST)
🚨 **NEVER use Python code or Bash to call Composio SDK directly.**
🚨 **NEVER try `from composio import ...` or `composio_client.tools.execute(...)`**

**Use ONE of these MCP tools:**
1. `mcp__composio__GMAIL_SEND_EMAIL` - Direct MCP call (preferred)
2. `mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL` - Wrapper with `tool_slug: "GMAIL_SEND_EMAIL"`

**Full Call Example (via COMPOSIO_MULTI_EXECUTE_TOOL):**
```json
{
  "tools": [
    {
      "tool_slug": "GMAIL_SEND_EMAIL",
      "arguments": {
        "recipient_email": "user@example.com",
        "subject": "Your Report",
        "body": "Please find the report attached.",
        "attachment": {
          "name": "report.pdf",
          "mimetype": "application/pdf",
          "s3key": "<from upload_to_composio>"
        }
      }
    }
  ]
}
```

### Argument Names (CRITICAL)
- Use `recipient_email` (or `to`), NOT `recipient`.
- `recipient` is NOT a valid parameter and will cause a schema validation error.

### Attachment format (CRITICAL)
- `attachment` must be a **DICT**, not a list
- Format: `{"name": str, "mimetype": str, "s3key": str}`
- Get `s3key` from `mcp__local_toolkit__upload_to_composio` first

### Common Mistakes (WRONG)
```json
{
  "recipient": "user@example.com",              // ❌ Wrong parameter name!
  "attachment": [{"name": "report.pdf", ...}]  // ❌ List format fails!
}
```

🚫 **NEVER DO THIS:**
```python
# ❌ WRONG - This will fail! The agent cannot call Composio SDK directly.
from composio import Composio
client = Composio()
client.tools.execute(slug="GMAIL_SEND_EMAIL", ...)  # FAILS!
```

## upload_to_composio

- Returns the `s3key` needed for `GMAIL_SEND_EMAIL` attachments
- Always call this BEFORE attempting to send email with attachments
- The returned `s3key` is used directly in the attachment dict

## COMPOSIO_MULTI_EXECUTE_TOOL

**FORMAT IS CRITICAL**: Tool calls MUST use proper JSON with separate named parameters. **NEVER concatenate parameters into the tool name.**

**Correct Example:**
```json
{
  "tools": [
    {"tool_slug": "COMPOSIO_SEARCH_WEB", "arguments": {"query": "best food processors 2025"}},
    {"tool_slug": "COMPOSIO_SEARCH_WEB", "arguments": {"query": "Cuisinart vs Breville"}}
  ],
  "session_id": "my_session",
  "current_step": "SEARCHING",
  "next_step": "REPORT_GENERATION"
}
```

**Notes (Critical):**
- `tools` MUST be a JSON array, not a quoted JSON string.
- Each item MUST include `tool_slug` and `arguments` (object).
- Use the tool name exactly as `mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL` (no XML fragments).

**WRONG (causes tool_use_error):**
```
mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOLtools</arg_key><arg_value>[...]
```
This malformed XML-style concatenation will fail. Each parameter (`tools`, `session_id`, etc.) must be a separate JSON key.

## COMPOSIO_SEARCH_TOOLS

Use this to discover the best Composio search tools and get recommended steps.

**Schema (minimal):**
```json
{
  "queries": [
    {"use_case": "Search AI news last 30 days", "known_fields": "timeframe: last 30 days, topics: AI news"}
  ],
  "session": {"generate_id": true}
}
```

**Notes:**
- `queries` MUST be a JSON array (not a quoted JSON string).
- Each item should include `use_case` and `known_fields`.

## COMPOSIO_SEARCH_NEWS

**Schema:**
```json
{
  "query": "artificial intelligence industry news",
  "when": "m",
  "gl": "us",
  "hl": "en"
}
```

## COMPOSIO_SEARCH_WEB

**Schema:**
```json
{
  "query": "AI policy regulation January 2026"
}
```

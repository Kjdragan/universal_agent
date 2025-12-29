# Composio Tool Knowledge

## GMAIL_SEND_EMAIL

**Argument Names (CRITICAL)**:
- Use `recipient_email` (or `to`), NOT `recipient`.
- `recipient` is NOT a valid parameter and will cause a schema validation error.

**Attachment format (CRITICAL)**:
- `attachment` must be a **DICT**, not a list
- Format: `{"name": str, "mimetype": str, "s3key": str}`
- Get `s3key` from `upload_to_composio` first

**Correct Example:**
```json
{
  "recipient_email": "user@example.com",  // ✅ Correct (or "to")
  "subject": "Report",
  "body": "See attached.",
  "attachment": {
    "name": "report.pdf",
    "mimetype": "application/pdf",
    "s3key": "215406/gmail/GMAIL_SEND_EMAIL/report.pdf"
  }
}
```

**Common Mistakes (WRONG):**
```json
{
  "recipient": "user@example.com",              // ❌ Wrong parameter name!
  "attachment": [{"name": "report.pdf", ...}]  // ❌ List format fails!
}
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

**WRONG (causes tool_use_error):**
```
mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOLtools</arg_key><arg_value>[...]
```
This malformed XML-style concatenation will fail. Each parameter (`tools`, `session_id`, etc.) must be a separate JSON key.

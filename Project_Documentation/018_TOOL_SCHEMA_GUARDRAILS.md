# 018 Tool Schema Guardrails

## Goal
Create a small, curated validation map for core tools (local toolkit + frequent Composio tools) to quickly correct malformed tool calls without bloating system prompts.

## Why a Curated Map
- **Fast correction**: Missing args are caught at PreToolUse, and the model is immediately given the exact schema/example to reissue the call.
- **Low prompt cost**: No need to inject large tool manuals into the system prompt; guidance is shown only on error.
- **Predictable behavior**: Core tools have stable schemas and are used repeatedly, so a static map is low maintenance.

## Scope (Initial)
- Local toolkit tools we control and know:
  - `mcp__local_toolkit__write_local_file`
  - `mcp__local_toolkit__upload_to_composio`
  - `mcp__local_toolkit__read_local_file`
  - `mcp__local_toolkit__list_directory`
- Frequent Composio tools (curated list):
  - `COMPOSIO_SEARCH_NEWS`, `COMPOSIO_SEARCH_WEB`
  - `COMPOSIO_MULTI_EXECUTE_TOOL`
  - `GMAIL_SEND_EMAIL`

## Proposed Behavior
1. **PreToolUse validation** checks required fields for known tools.
2. If missing fields:
   - **Deny** the tool call.
   - **Inject a short schema example** in the same hook response.
3. **PostToolUse fallback nudge** remains for unexpected validation errors.

## Example Map Structure
```python
TOOL_REQUIRED_FIELDS = {
    "mcp__local_toolkit__write_local_file": {
        "required": ["path", "content"],
        "example": "write_local_file({path: '/tmp/report.html', content: '<html>...</html>'})",
    },
    "mcp__local_toolkit__upload_to_composio": {
        "required": ["path", "tool_slug", "toolkit_slug"],
        "example": "upload_to_composio({path: '/tmp/report.pdf', tool_slug: 'GMAIL_SEND_EMAIL', toolkit_slug: 'gmail'})",
    },
    "GMAIL_SEND_EMAIL": {
        "required": ["recipient_email", "subject", "body", "attachment"],
        "example": "GMAIL_SEND_EMAIL({recipient_email: 'x', subject: 'y', body: 'z', attachment: {...}})",
    },
}
```

## Prompt Impact
- The system prompt stays lean.
- Schema guidance appears only when the model makes a mistake.
- For unknown/dynamic tools, the normal prompt-based guidance still applies.

## Risks and Limits
- Requires maintaining the curated list as schemas evolve.
- Does not cover dynamically discovered tools (by design).
- Must avoid over-blocking valid but optional fields.

## Next Steps
1. Add the curated map in code (or a small config file).
2. Wire the map into PreToolUse for deny + schema injection.
3. Keep PostToolUse nudges as fallback only.
4. Add a short note in `.claude/knowledge` if we want human-readable guidance.

# Composio Knowledge Base

> [!IMPORTANT]
> **MANDATORY DELEGATION**: All research tasks (web search, news search, deep dives) MUST start by delegating to the `research-specialist` sub-agent.
> Do NOT use search tools directly in the primary agent. Use the `Task` tool to delegate.
> 
> [!CAUTION]
> **REMOTE WORKBENCH (PRIMARY AGENT)**: The Primary Agent is FORBIDDEN from using `COMPOSIO_REMOTE_WORKBENCH` directly.
> It should only be used by specialists (research-specialist, etc.) for browsing or code execution.
> If the Primary Agent needs to execute code, it should use local Python/Bash tools unless absolute isolation is required.

## Gmail (via gws MCP — Primary Path)

### HOW TO CALL (CRITICAL - READ FIRST)
🚨 **NEVER use Python code or Bash to call Gmail APIs or Composio SDK directly.**
🚨 **NEVER try `from composio import ...` or `gws` CLI via Bash**

**Use gws MCP tools:**
- `mcp__gws__gmail.+send` — Helper for quick send (preferred)
- `mcp__gws__gmail.users.messages.send` — Full API send
- `mcp__gws__gmail.+triage` — Inbox triage summary
- `mcp__gws__gmail.users.messages.list` / `.get` — List/read messages
- `mcp__gws__gmail.users.drafts.create` / `.send` — Draft management

### Attachments (SIMPLIFIED)
- Pass **local file paths** directly to the gws Gmail send tool
- No `upload_to_composio` step needed for Gmail attachments
- Multiple files can be attached in a single send call

### Common Mistakes (WRONG)
🚫 **NEVER DO THIS:**
```python
# ❌ WRONG - This will fail! The agent cannot call APIs directly.
from composio import Composio
client = Composio()
client.tools.execute(slug="GMAIL_SEND_EMAIL", ...)  # FAILS!
```
```bash
# ❌ WRONG - Do not call gws CLI from Bash
gws gmail send --to user@example.com  # FAILS! Use MCP tools.
```

## ⚠️ CRITICAL: DO NOT CONCATENATE ARGUMENTS INTO TOOL NAMES

Claude sometimes hallucinates tool calls by concatenating arguments into the tool name using XML syntax.

**WRONG (causes immediate rejection):**
```
mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOLtools</arg_key><arg_value>[...]
mcp__composio__COMPOSIO_SEARCH_NEWSquery</arg_key><arg_value>Russia Ukraine
```

**RIGHT:**
```json
{
  "tool_name": "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",
  "tool_input": {
    "tools": [...],
    "session_id": "..."
  }
}
```

**The guardrail will BLOCK any tool name containing `</arg_key>`, `<arg_value>`, etc.**

## upload_to_composio

- Returns the `s3key` needed for **non-Gmail Composio tool** attachments (e.g., Slack)
- **NOT needed for Gmail** — gws Gmail tools accept local file paths directly
- Preferred tool: `mcp__internal__upload_to_composio`

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

**⚠️ WHEN NOT TO USE:**
- Do **NOT** use this to "find" standard tools like Gmail, Google Calendar, or Web Search.
- For Gmail/Calendar/Drive/Sheets: use `mcp__gws__*` tools directly.
- If you know a Composio tool exists (e.g. `SLACK_SENDS_A_MESSAGE_TO_A_SLACK_CHANNEL`), **JUST USE IT DIRECTLY**.
- Using this tool unnecessarily wastes time and tokens.


**Notes:**
- `queries` MUST be a JSON array (not a quoted JSON string).
- Each item should include `use_case` and `known_fields`.

## COMPOSIO_SEARCH_NEWS

**Schema:**
```json
{
  "query": "artificial intelligence industry news",
  "when": "d",
  "gl": "us",
  "hl": "en"
}
```

**Parameters:**
- `when`: Time window.
  - `h`: Last hour
  - `d`: Last 24 hours (Use for "today/yesterday" requests)
  - `w`: Last week (Default if unspecified)
  - `m`: Last month
  - `y`: Last year
  - Note: Tighter windows (d or w) reduce noise and crawl time. Use `d` for breaking news.

## COMPOSIO_SEARCH_WEB

**Schema:**
```json
{
  "query": "AI policy regulation January 2026"
}
```

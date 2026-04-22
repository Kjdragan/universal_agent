---
name: agentmail
description: Use the official AgentMail MCP tools for Simone's own inbox. Send outbound mail with `mcp__agentmail__send_message`, reply with `mcp__agentmail__reply_to_message`, and prepare local attachments with `prepare_agentmail_attachment`. Do not use bash, curl, SDK scripts, or CLI commands for in-session email delivery.
---

# AgentMail — Official MCP Path

Simone sends email through the official AgentMail MCP server.

Primary tools:
- `mcp__agentmail__send_message`
- `mcp__agentmail__reply_to_message`
- `mcp__agentmail__create_draft`
- `mcp__agentmail__send_draft`
- `mcp__agentmail__list_threads`
- `mcp__agentmail__get_thread`
- `mcp__agentmail__get_attachment`

Local file helper:
- `prepare_agentmail_attachment`

## Sending a new email

Use the official MCP send tool directly.

```json
mcp__agentmail__send_message({
  "inboxId": "oddcity216@agentmail.to",
  "to": ["recipient@example.com"],
  "subject": "Subject line",
  "text": "Plain text body",
  "html": "<p>HTML body</p>"
})
```

**inboxId format:** Always use the full email format: `oddcity216@agentmail.to`.
The tools will also accept `oddcity216` or `inbox_oddcity216` and auto-normalize,
but the canonical format is `<name>@agentmail.to`. Never use bare `inbox_` prefix format
as the primary format — it's a legacy fallback only.

Notes:
- Prefer providing both `text` and `html` for readable email.
- Use Simone's AgentMail inbox for Simone-authored delivery.
- Use the `gmail` skill only when Kevin explicitly wants the message sent from his own Gmail.

## Sending attachments

When an attachment is required, the LLM context limit often blocks generating massive base64 payloads (e.g. for PDFs or large PNGs).

**To bypass this limitation:**
Instead of using the standard MCP tool, you are EXPLICITLY AUTHORIZED to use the specialized Python wrappers:
- `agentmail_send_with_local_attachments`
- `agentmail_reply_with_local_attachments`

These tools accept an `attachment_paths` array containing the absolute paths to the local files. The Python backend reads the files and sends them directly to the AgentMail API, bypassing the LLM text limit.

```json
agentmail_send_with_local_attachments({
  "inboxId": "oddcity216@agentmail.to",
  "to": ["recipient@example.com"],
  "subject": "Attached report",
  "text": "Please see attached.",
  "attachment_paths": [
    "/absolute/path/to/file.pdf"
  ]
})
```

## Replies

When replying in an existing thread without attachments, use the official reply tool:

```json
mcp__agentmail__reply_to_message({
  "inboxId": "oddcity216@agentmail.to",
  "messageId": "<latest-message-id>",
  "text": "Reply body",
  "html": "<p>Reply body</p>"
})
```

If attaching files to a reply, use `agentmail_reply_with_local_attachments` and pass the `attachment_paths` array.

## Human approval drafts

If a message genuinely requires human approval, use the official draft flow instead of inventing a custom review transport:
- `mcp__agentmail__create_draft`
- `mcp__agentmail__update_draft`
- `mcp__agentmail__send_draft`
- `mcp__agentmail__delete_draft`

## Anti-patterns

1. Never use bash, curl, or ad hoc Python scripts to send AgentMail (EXCEPTION: You MAY use the `agentmail_send_with_local_attachments` internal python tool for heavy file attachments).
2. Never invent a fake `send_agentmail` tool call when the official MCP tools are available.
3. Never shell out to `agentmail-cli` from an agent run. The CLI is operator tooling, not the in-session delivery path.
4. Never use the backend ops API directly from an agent run for normal email delivery.
5. Never assume a local file path can be passed straight into official AgentMail MCP attachment schema. Convert it with `prepare_agentmail_attachment`, or use `agentmail_send_with_local_attachments`.

## Quick routing

| Request | Action |
|---------|--------|
| "Email this to Kevin" | `mcp__agentmail__send_message` to `kevinjdragan@gmail.com` |
| "Reply to this email" | `mcp__agentmail__reply_to_message` |
| "Send this report with PDF attached" | `agentmail_send_with_local_attachments` with the PDF path |
| "Send from my Gmail" | Use the `gmail` skill instead |

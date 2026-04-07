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

Notes:
- Prefer providing both `text` and `html` for readable email.
- Use Simone's AgentMail inbox for Simone-authored delivery.
- Use the `gmail` skill only when Kevin explicitly wants the message sent from his own Gmail.

## Sending attachments

The official AgentMail MCP tools expect attachment payloads, not raw local paths.

1. Call:

```json
prepare_agentmail_attachment({
  "path": "/absolute/path/to/file.pdf"
})
```

2. Parse the returned JSON object.
3. Pass that object in the official AgentMail MCP `attachments` array.

```json
mcp__agentmail__send_message({
  "inboxId": "oddcity216@agentmail.to",
  "to": ["recipient@example.com"],
  "subject": "Attached report",
  "text": "Please see attached.",
  "attachments": [
    {
      "filename": "report.pdf",
      "content": "<base64>"
    }
  ]
})
```

## Replies

When replying in an existing thread, use the official reply tool instead of inventing your own thread logic.

```json
mcp__agentmail__reply_to_message({
  "inboxId": "oddcity216@agentmail.to",
  "messageId": "<latest-message-id>",
  "text": "Reply body",
  "html": "<p>Reply body</p>"
})
```

Use `list_threads` / `get_thread` first if you need to inspect the thread or recover the latest message ID.

## Human approval drafts

If a message genuinely requires human approval, use the official draft flow instead of inventing a custom review transport:
- `mcp__agentmail__create_draft`
- `mcp__agentmail__update_draft`
- `mcp__agentmail__send_draft`
- `mcp__agentmail__delete_draft`

## Anti-patterns

1. Never use bash, curl, or ad hoc Python scripts to send AgentMail.
2. Never invent a fake `send_agentmail` tool call when the official MCP tools are available.
3. Never shell out to `agentmail-cli` from an agent run. The CLI is operator tooling, not the in-session delivery path.
4. Never use the backend ops API directly from an agent run for normal email delivery.
5. Never assume a local file path can be passed straight into AgentMail attachments. Convert it with `prepare_agentmail_attachment`.

## Quick routing

| Request | Action |
|---------|--------|
| "Email this to Kevin" | `mcp__agentmail__send_message` to `kevinjdragan@gmail.com` |
| "Reply to this email" | `mcp__agentmail__reply_to_message` |
| "Send this report with PDF attached" | `prepare_agentmail_attachment` then `mcp__agentmail__send_message` |
| "Send from my Gmail" | Use the `gmail` skill instead |

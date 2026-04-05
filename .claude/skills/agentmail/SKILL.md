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
- Prefer providing both `text` and `html`.
- Use Simone's AgentMail inbox for Simone-authored delivery.
- Use the `gmail` skill only when Kevin explicitly wants the message sent from his own Gmail.

## Sending attachments

1. Convert the local file:

```json
prepare_agentmail_attachment({
  "path": "/absolute/path/to/file.pdf"
})
```

2. Parse the returned JSON.
3. Pass that object in the official AgentMail MCP `attachments` array.

## Replies

```json
mcp__agentmail__reply_to_message({
  "inboxId": "oddcity216@agentmail.to",
  "messageId": "<latest-message-id>",
  "text": "Reply body",
  "html": "<p>Reply body</p>"
})
```

If you need thread context first, use `mcp__agentmail__list_threads` and `mcp__agentmail__get_thread`.

## Human approval drafts

If a message genuinely requires human approval, use the official draft flow:
- `mcp__agentmail__create_draft`
- `mcp__agentmail__update_draft`
- `mcp__agentmail__send_draft`
- `mcp__agentmail__delete_draft`

## Anti-patterns

1. Never use bash, curl, or ad hoc Python scripts to send AgentMail.
2. Never invent a fake `send_agentmail` tool call when the official MCP tools are available.
3. Never shell out to `agentmail-cli` from an agent run.
4. Never hit backend ops endpoints directly from an agent run for normal email delivery.
5. Never pass a local file path directly as an attachment without first converting it through `prepare_agentmail_attachment`.

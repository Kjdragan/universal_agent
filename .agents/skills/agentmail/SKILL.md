---
name: agentmail
description: Send emails from Simone's own inbox using the native `mcp__internal__send_agentmail` MCP tool. Use when you need to send an email, reply, deliver a report, or communicate with anyone via email. Do NOT use bash, curl, SDK scripts, or CLI commands — just call the MCP tool directly.
---

# AgentMail — Simone's Email

Simone sends email via `mcp__internal__send_agentmail`. One tool call — no scripts, no CLI, no SDK.

## Sending Email

```json
mcp__internal__send_agentmail({
  "to": "recipient@example.com",
  "subject": "Subject line",
  "body": "Email body content — plain text or HTML"
})
```

Required: `to`, `subject`, `body`. Optional: `cc`, `bcc`, `dry_run`.

## Before Sending, Ask Yourself

- **Who is the sender?** If Simone → this tool. If Kevin's Gmail → `gmail` skill instead.
- **Is the body formatted?** For reports/structured content, wrap in basic HTML (`<h2>`, `<p>`, `<ul>`) for readable email. Raw markdown renders poorly in email clients.
- **Is this a duplicate?** The tool has built-in dedup guards. If it returns a "duplicate delivery blocked" error, your email was already sent — do NOT retry.
- **Is the recipient correct?** Kevin's email: `kevinjdragan@gmail.com`.

## Anti-Patterns — NEVER Do These

1. **NEVER use bash, curl, or Python scripts** to send email. The MCP tool handles auth, formatting, and Task Hub lifecycle tracking automatically. Scripts bypass all of this.
2. **NEVER use `mcp__AgentMail__send_message`** — that's the raw AgentMail MCP endpoint. It bypasses delivery tracking and dedup guards, causing phantom sends and orphaned tasks.
3. **NEVER send receipt acknowledgements** like "Got it, working on it" — the system blocks these. Only send the final, substantive response.
4. **NEVER construct AgentMail SDK code** (`from agentmail import AgentMail`, `client.inboxes.messages.send(...)`) — that's for external app development, not for Simone.
5. **NEVER install `agentmail-cli` via npm** — the CLI is not needed; the native MCP tool is the correct path.

## Error Handling

| Error Message | Meaning | Action |
|--------------|---------|--------|
| `'to' is required` | Missing recipient | Add the `to` field |
| `AgentMail service is not available` | Service not configured | Report error to user — cannot send |
| `duplicate final delivery blocked` | Email already sent for this task | **Stop** — delivery succeeded earlier. Do NOT retry. |
| `Receipt acknowledgement blocked` | Tried to send a "got it" message | Skip the ack — only send final substantive content |
| Connection/timeout error | Transient failure | Retry once, then report failure |

## Quick Routing

| Request | Action |
|---------|--------|
| "Email this to Kevin" | `mcp__internal__send_agentmail` → `kevinjdragan@gmail.com` |
| "Send this report" | `mcp__internal__send_agentmail` → specified recipient |
| "Reply to this email" | `mcp__internal__send_agentmail` → original sender |
| "Send from Kevin's Gmail" | Use `gmail` skill (gws CLI) — completely different channel |

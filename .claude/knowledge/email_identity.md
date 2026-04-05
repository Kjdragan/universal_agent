# Email & Identity Resolution

## Primary Architecture

Use the official AgentMail stack in two places:

- **Agent runtime**: official AgentMail MCP tools
  - `mcp__agentmail__send_message`
  - `mcp__agentmail__reply_to_message`
  - `mcp__agentmail__list_threads`
  - `mcp__agentmail__get_thread`
  - `mcp__agentmail__get_attachment`
- **Backend services**: official AgentMail SDK in `agentmail_service.py`

Do not use bash, curl, ad hoc SDK scripts, or CLI commands for normal in-session delivery.

## Two identities

### 1. AgentMail — Simone's own identity
- Sends from Simone's AgentMail inbox.
- Use for Simone-authored reports, replies, notifications, and deliverables.
- Replies come back into Simone's inbound queue and flow through email triage.

### 2. Kevin's Gmail
- Use only when Kevin explicitly wants mail sent from his own Gmail account.
- Route through the `gmail` skill, not AgentMail.

## Routing

| Scenario | Channel |
|---|---|
| Simone sends Kevin a report or artifact | AgentMail |
| Simone replies from her own inbox | AgentMail |
| Kevin asks "send this from my Gmail" | Gmail skill |
| Kevin asks "check my Gmail" | Gmail skill |

## Attachments

Official AgentMail MCP tools expect attachment payload objects, not local file paths.

When attaching a local file:
1. Call `prepare_agentmail_attachment(path=...)`
2. Parse the returned JSON object
3. Pass it in the official AgentMail MCP tool's `attachments` field

## Inbound mail

Inbound mail should be handled as:
1. AgentMail inbox listener receives the message
2. restricted email-triage run evaluates safety and produces a triage brief
3. trusted clean mail is handed to the canonical ToDo executor
4. untrusted clean mail goes to review
5. unsafe mail is quarantined

## Delivery rules

- Simone's agent runtime should use the official AgentMail MCP tools directly.
- Backend workflow/lifecycle tracking can supplement delivery by observing successful official AgentMail tool calls.
- Do not invent custom transport paths when the official MCP tools are available.

---
name: gmail
description: Guide for using Google Workspace CLI (gws) natively to access Kevin's Gmail. Use when asked to send an email on behalf of Kevin, check his inbox, or manage his labels.
---

# Gmail (gws CLI)

This skill provides instructions for interacting with Kevin's Gmail using the Google Workspace CLI natively.

**Primary use:** sending email on behalf of Kevin, checking his inbox, managing his labels.

**Secondary use (NEW):** Simone's AgentMailService transparently uses this CLI as a fallback when AgentMail returns HTTP 429 ("Daily send limit exceeded"). The fallback is gated by `UA_AGENTMAIL_GMAIL_FALLBACK=1` and lives in `src/universal_agent/services/agentmail_service.py:_send_via_gmail_cli` — agents do **not** invoke the CLI directly for Simone's outbound mail; AgentMailService routes there automatically when configured.

## Gmail Skill (via gws CLI)

You can interact with Google Workspace using the `gws` command via the Bash tool. The command is executed via `npx -y @googleworkspace/cli` to assure it works even if not globally linked.

The helper subcommands begin with a `+` prefix (`+send`, `+reply`, etc.). The underlying resource-style commands (`gws gmail users messages list`, etc.) also work but are more verbose.

## Tools (CLI commands)

- `npx -y @googleworkspace/cli gmail +send --to "email@example.com" --subject "Hello" --body "This is a test"`
- `npx -y @googleworkspace/cli gmail +triage` — show unread inbox summary
- `npx -y @googleworkspace/cli gmail +read --id "MESSAGE_ID"` — read a message body/headers
- `npx -y @googleworkspace/cli gmail +reply --id "MESSAGE_ID" --body "Got it."`
- `npx -y @googleworkspace/cli gmail +forward --id "MESSAGE_ID" --to "team@example.com"`
- `npx -y @googleworkspace/cli gmail +send --to "..." --subject "..." --body "..." --draft` — save as draft instead of sending
- `npx -y @googleworkspace/cli gmail users messages list --params '{"userId": "me", "maxResults": 5}'` — list messages (resource-style)

*Attachments use `-a/--attach`, repeat the flag for multiple files (25 MB total cap).*
```bash
npx -y @googleworkspace/cli gmail +send \
  --to "john@example.com" \
  --subject "Weekly Report" \
  --body "Here is the report." \
  -a "/path/to/report.pdf"
```

HTML body with CC and attachment (use `--html` to tell `+send` the body is HTML):
```bash
npx -y @googleworkspace/cli gmail +send \
  --to "team@example.com" \
  --cc "manager@example.com" \
  --subject "Project Alpha Update" \
  --html \
  --body "<h1>Status Update</h1><p>We are on track.</p>" \
  -a "/path/to/presentation.pdf"
```

## Usage Guidelines

- NEVER attempt to use `mcp__gws__*` tools or Composio tools for Gmail. ALWAYS use the raw `npx -y @googleworkspace/cli` commands via Bash.
- When sending a file, always use absolute paths for the `--attach` argument.

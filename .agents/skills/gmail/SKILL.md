---
name: gmail
description: Guide for using Google Workspace CLI (gws) natively to access Kevin's Gmail. Use when asked to send an email on behalf of Kevin, check his inbox, or manage his labels.
---

# Gmail (gws CLI)

This skill provides instructions for interacting with Kevin's Gmail using the Google Workspace CLI natively.
**NOTE:** Do not use this for Simone's own outbound emails (e.g., sending agent reports). For Simone's emails, use the `agentmail` skill.

## Executing gws Commands

You can interact with Google Workspace using the `gws` command via the Bash tool. The command is executed via `npx @googleworkspace/cli` to assure it works even if not globally linked.

### Basic Commands
- `npx @googleworkspace/cli gmail send --to "email@example.com" --subject "Hello" --body "This is a test"`
- `npx @googleworkspace/cli gmail list --max 5`
- `npx @googleworkspace/cli gmail get --id "MESSAGE_ID"`

### Sending Email with Attachments
To send an email with an attachment, use the `--attach` flag:
```bash
npx @googleworkspace/cli gmail send \
  --to "recipient@example.com" \
  --subject "Report Attached" \
  --body "Here is the requested report." \
  --attach "/absolute/path/to/report.pdf"
```

### Creating Drafts
To create a draft rather than sending immediately:
```bash
npx @googleworkspace/cli gmail create-draft \
  --to "recipient@example.com" \
  --subject "Review this draft" \
  --body "Draft content..."
```

**CRITICAL RULES:**
- NEVER attempt to use `mcp__gws__*` tools or Composio tools for Gmail. ALWAYS use the raw `npx @googleworkspace/cli` commands via Bash.
- When sending a file, always use absolute paths for the `--attach` argument.

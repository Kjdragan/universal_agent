---
name: gmail
description: Guide for using Google Workspace CLI (gws) natively to access Kevin's Gmail. Use when asked to send an email on behalf of Kevin, check his inbox, or manage his labels.
---

# Gmail (gws CLI)

This skill provides instructions for interacting with Kevin's Gmail using the Google Workspace CLI natively.
**NOTE:** Do not use this for Simone's own outbound emails (e.g., sending agent reports). For Simone's emails, use the `agentmail` skill.

## Gmail Skill (via gws CLI)

You can interact with Google Workspace using the `gws` command via the Bash tool. The command is executed via `npx -y @googleworkspace/cli` to assure it works even if not globally linked.

## Tools (CLI commands)

- `npx -y @googleworkspace/cli gmail send --to "email@example.com" --subject "Hello" --body "This is a test"`
- `npx -y @googleworkspace/cli gmail list --max 5`
- `npx -y @googleworkspace/cli gmail get --id "MESSAGE_ID"`
- `npx -y @googleworkspace/cli gmail thread get --id "THREAD_ID"`
- `npx -y @googleworkspace/cli gmail create-draft --to "email@example.com" --subject "Draft" --body "This is a draft"`
- `npx -y @googleworkspace/cli gmail list-drafts`
- `npx -y @googleworkspace/cli gmail delete-draft --id "DRAFT_ID"`

*Note: For attachments, use `--attach "/path/to/file"`*
```bash
npx -y @googleworkspace/cli gmail send \
  --to "john@example.com" \
  --subject "Weekly Report" \
  --body "Here is the report." \
  --attach "/path/to/report.pdf"
```

To create an HTML drafted response with CC and attachment:
```bash
npx -y @googleworkspace/cli gmail create-draft \
  --to "team@example.com" \
  --cc "manager@example.com" \
  --subject "Project Alpha Update" \
  --html "<h1>Status Update</h1><p>We are on track.</p>" \
  --attach "/path/to/presentation.pdf"
```

## Usage Guidelines

- NEVER attempt to use `mcp__gws__*` tools or Composio tools for Gmail. ALWAYS use the raw `npx -y @googleworkspace/cli` commands via Bash.
- When sending a file, always use absolute paths for the `--attach` argument.

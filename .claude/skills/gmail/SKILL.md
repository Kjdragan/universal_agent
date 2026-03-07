---
name: gmail
description: Guide for using Gmail via gws MCP tools to send emails, manage drafts, and handle attachments. Use when the user asks to send emails, check inbox, or manage labels.
---

# Gmail Skill (via gws MCP)

Gmail operations are handled through **gws MCP tools** (`mcp__gws__*`). These tools use the Google Workspace CLI, which provides native Gmail API access with built-in authentication and attachment support.

## Core Capabilities

1.  **Sending Emails**: `mcp__gws__gmail.users.messages.send` or `mcp__gws__gmail.+send` (helper)
2.  **Inbox Triage**: `mcp__gws__gmail.+triage` (helper — unread summary)
3.  **Message Listing**: `mcp__gws__gmail.users.messages.list`
4.  **Message Reading**: `mcp__gws__gmail.users.messages.get`
5.  **Draft Management**: `mcp__gws__gmail.users.drafts.create`, `mcp__gws__gmail.users.drafts.send`
6.  **Label Management**: `mcp__gws__gmail.users.labels.create`, `mcp__gws__gmail.users.messages.modify`

## Critical Usage Guidelines

### 1. Handling Attachments (IMPORTANT)

The gws Gmail tools support **native file attachments** — no upload_to_composio step needed.

*   Pass local file paths directly in the send parameters.
*   Multiple files can be attached in a single send call.
*   No S3 key intermediary required.

### 2. Sending HTML Emails

*   Set the appropriate MIME type in the message body when sending HTML content.
*   The `+send` helper accepts body content directly.

### 3. Drafts First Policy (Best Practice)

For critical or sensitive emails, prefer creating a draft first and asking the user to confirm/send it, unless the user explicitly said "send this email".

## Common Workflows

### Sending a Report

1.  **Generate Content**: Create the file (e.g., PDF report).
2.  **Verify Files**: Ensure the file exists and you have the path.
3.  **Send**: Use the gws Gmail send tool with the local file path as attachment — single step, no upload needed.

### Inbox Check

1.  **Quick triage**: Use `mcp__gws__gmail.+triage` for an unread inbox summary.
2.  **Detailed listing**: Use `mcp__gws__gmail.users.messages.list` with query parameters.

### Responding to a Thread

1.  **Find Thread**: Use message list/get tools to find the context.
2.  **Get ID**: Extract the `threadId`.
3.  **Reply**: Use the send tool with the `threadId` parameter to ensure correct threading.

## Troubleshooting

*   **"Authentication Error"**: Run `gws auth status` to check credentials. Ensure `UA_ENABLE_GWS_CLI=1` is set.
*   **"Tool not found"**: Ensure the gws MCP server is registered and the feature flag is enabled.
*   **SDK Error**: Do NOT call gws directly via Bash. Always use the `mcp__gws__*` MCP tools.

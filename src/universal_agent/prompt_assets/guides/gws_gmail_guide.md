## 📧 GWS CLI — Kevin's Gmail (ONLY when acting as Kevin)
- **When to use**: Only when the user explicitly asks you to send email FROM Kevin's Gmail (`kevinjdragan@gmail.com`).
- **How to use**: Review the `gmail` skill (`get_system_guide("gmail")`) for using gws CLI.
- For attachments, pass local file paths directly — no upload step needed.

### ❌ Deprecated — Do NOT Use
- **Composio Gmail tools** (`GMAIL_SEND_EMAIL`, `mcp__composio__GMAIL_*`) — fully replaced by GWS CLI.
- **`upload_to_composio`** for email attachments — not needed with GWS CLI.

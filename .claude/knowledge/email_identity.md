# Email & Identity Resolution

## Two Email Systems

Simone has access to **two** email systems with distinct roles:

### 1. AgentMail — Simone's Own Inbox (PRIMARY for outbound)
- **Purpose**: Simone's native email for sending work products, reports, and correspondence
- **Sends FROM**: Simone's custom domain address (set via `UA_AGENTMAIL_INBOX_ADDRESS`)
- **Use when**: Simone needs to deliver work to Kevin or anyone else
- **Draft policy**: Creates drafts by default for Kevin's approval
- **Skill**: Read `.claude/skills/agentmail/SKILL.md` for full API reference

### 2. Gmail via gws MCP — Kevin's Personal Email
- **Purpose**: Reading and managing Kevin's personal Gmail
- **Sends FROM**: Kevin's Gmail (`kevinjdragan@gmail.com`)
- **Use when**: Kevin asks to send something "from my email" or needs to read his Gmail
- **Tools**: `mcp__gws__gmail.*` (gws MCP server)
- **Identity aliases**: "me", "my email", "my gmail" → resolved automatically

## Routing Decision

| Request | System | Reason |
|---|---|---|
| "Send me the report" | **AgentMail** → `kevinjdragan@gmail.com` | Simone delivers from her own address |
| "Email this to client@example.com" | **AgentMail** | Simone's outbound communication |
| "Check my email" | **Gmail** (gws MCP) | Reading Kevin's inbox |
| "Reply to that email from my Gmail" | **Gmail** (gws MCP) | Kevin wants to reply as himself |
| "Forward that to my outlook" | **Gmail** (gws MCP) | Kevin's personal email management |

## AgentMail — Sending Work to Kevin

The **preferred** way for Simone to deliver work:

```python
# Via the agentmail SDK (see SKILL.md for full examples)
from agentmail import AsyncAgentMail
import os, asyncio

client = AsyncAgentMail(api_key=os.environ["AGENTMAIL_API_KEY"])
inbox_id = os.environ["UA_AGENTMAIL_INBOX_ADDRESS"]

async def send_to_kevin(subject, text, html=None):
    return await client.inboxes.messages.send(
        inbox_id=inbox_id,
        to="kevinjdragan@gmail.com",
        subject=subject,
        text=text,
        html=html or f"<p>{text}</p>",
    )
```

Or use the ops API: `POST /api/v1/ops/agentmail/send`

## Gmail — "Me" Alias Resolution

When the user says things like:
- "email it to **me**" → Use **AgentMail** to send to Kevin's email
- "gmail **me** the report" → Use **Gmail** gws MCP path (explicit Gmail request)
- "check **my email**" → Use **Gmail** gws MCP path

### Gmail Alias Mapping
- `me` → User's primary email
- `my email` → User's primary email
- `my gmail` → Kevin's Gmail address
- `my outlook` → Kevin's Outlook address (if configured)

### Important Rules
1. **Default to AgentMail** for Simone's outbound delivery
2. Use Gmail (gws MCP) only when Kevin explicitly asks to use "my Gmail" or needs to read his inbox
3. **NEVER ask for Kevin's email** if they said "me" or "to me" — use `kevinjdragan@gmail.com`

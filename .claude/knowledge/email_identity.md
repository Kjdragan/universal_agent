# Email & Identity Resolution

## Two Concepts — Two Identities

Simone has access to **two** email systems that represent **two different identities**:

### 1. AgentMail — Simone's Own Identity (DEFAULT)
- **Purpose**: Simone's own email for ALL her independent work
- **Sends FROM**: `Simone D <oddcity216@agentmail.to>`
- **Use when**:
  - Communicating with Kevin (digests, reports, status updates)
  - Doing research, outreach, or any work on Simone's own behalf
  - Sending emails where the recipient should know they're talking to the agent, not Kevin
- **Why**: When Simone does research, sends reports, or communicates — it should leave **Simone's trail**, not Kevin's. Kevin should not appear to be the one doing the research.
- **Replies**: Come back to Simone's inbox → automatically dispatched to email-handler agent
- **Draft policy**: Creates drafts by default for Kevin's approval
- **Skill**: Read `.claude/skills/agentmail/SKILL.md` for full API reference

### 2. Gmail via gws MCP — Kevin's Identity (ON BEHALF OF KEVIN ONLY)
- **Purpose**: Acting as Kevin when Simone needs to spoof/act on Kevin's behalf
- **Sends FROM**: Kevin's Gmail (`kevinjdragan@gmail.com`)
- **Use ONLY when**:
  - Kevin explicitly asks to send something "from my email"
  - Kevin needs Simone to read or manage his Gmail inbox
  - A process specifically requires Kevin's personal email identity
- **Tools**: `mcp__gws__gmail.*` (gws MCP server)
- **Identity aliases**: "me", "my email", "my gmail" → resolved to Kevin's accounts

## Routing Decision

| Scenario | System | Why |
|---|---|---|
| Simone sends Kevin a digest/report | **AgentMail** | Simone's own work, replies come back to her |
| Simone does research and emails findings | **AgentMail** | Simone's trail, not Kevin's |
| Simone contacts an external party | **AgentMail** | Simone's identity |
| Kevin says "send from my email" | **Gmail** | Spoofing as Kevin, on his explicit request |
| Kevin says "check my email" | **Gmail** | Reading Kevin's inbox |
| Kevin says "reply to that email" | **Gmail** | Kevin acting as himself |
| Kevin says "forward that from my Gmail" | **Gmail** | Kevin's personal email management |

## Digest & Report Emails — ALWAYS AgentMail

When sending periodic digests or reports to Kevin:
- **ALWAYS use AgentMail** — NEVER Gmail
- This ensures Kevin's replies come back to Simone's inbox for processing
- Include both `text` and `html` for best deliverability
- Use descriptive labels for filtering:
  - `["digest", "youtube-rss"]` for YouTube RSS digests
  - `["digest", "csi-report"]` for CSI reports
  - `["report", "research"]` for research deliverables

**Why this matters:** If Simone sends a digest via Gmail, it looks like Kevin emailed himself, and replies loop back to Kevin's inbox — Simone never sees them. When sent via AgentMail, Kevin can tell it's from the agent, and replies go to Simone for automatic processing.

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

## Inbound Emails — Automatic Processing

When someone emails Simone's AgentMail address:
1. The WebSocket listener receives the message in real-time
2. It is automatically dispatched to the **email-handler** agent
3. The email-handler classifies intent and takes action:
   - **Kevin's replies to digests** → parse instructions, delegate to specialists
   - **External inquiries** → draft a professional reply for Kevin's approval
   - **Spam/automated** → label and ignore

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

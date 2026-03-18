---
name: agentmail
description: "Simone's native email inbox via AgentMail. Use when Simone needs to send emails, deliver reports/artifacts to Kevin or external recipients, read inbound emails, reply to threads, or manage drafts. This is Simone's OWN email — not Gmail. Simone sends FROM her custom domain address directly."
---

# AgentMail — Simone's Native Email

Simone has her own email inbox via AgentMail: **Simone D** `<oddcity216@agentmail.to>`. This is her primary identity for all independent work — research, reports, digests, and communications with Kevin.

## Key Concepts

- **Simone's inbox**: `oddcity216@agentmail.to` (display name: Simone D)
- **Draft-first policy**: By default (`UA_AGENTMAIL_AUTO_SEND=0`), outbound emails are created as drafts for Kevin's approval. Set `force_send=True` or `UA_AGENTMAIL_AUTO_SEND=1` to send directly.
- **Gmail is separate**: Gmail (via gws MCP tools) is for reading/managing Kevin's personal email. AgentMail is for Simone's own outbound communications.
- **Inbound emails** are received via WebSocket listener (when `UA_AGENTMAIL_WS_ENABLED=1`) and dispatched to the `email-handler` agent.

## When to Use AgentMail vs Gmail

| Scenario | Use |
|---|---|
| Simone sends a report to Kevin | **AgentMail** — `send_email(to="kevinjdragan@gmail.com", ...)` |
| Simone sends work to an external contact | **AgentMail** — sends from Simone's custom domain |
| Read Kevin's personal Gmail inbox | **Gmail** (gws MCP `mcp__gws__gmail.*`) |
| Reply as Kevin from his Gmail | **Gmail** (gws MCP `mcp__gws__gmail.+send`) |
| Someone emails Simone directly | **AgentMail** inbound → email-handler agent |

## Sending Email (Python SDK — ALWAYS use this)

> **CRITICAL**: ALWAYS use the Python SDK (`from agentmail import AsyncAgentMail`) shown below.
> NEVER use `curl`, `requests.post`, or the ops API endpoint to send emails.
> - `curl` with HTML content will BREAK due to bash shell escaping of `<` `>` characters.
> - The ops API port varies by deployment and `localhost:8000` is often WRONG.
> - The Python SDK approach handles all escaping safely and works in every environment.

Use `python -c "..."` via the Bash tool with the SDK:

```python
import asyncio
import os
from agentmail import AsyncAgentMail

client = AsyncAgentMail(api_key=os.environ["AGENTMAIL_API_KEY"])
inbox_id = os.environ.get("UA_AGENTMAIL_INBOX_ADDRESS", "")

# Send email (both text and html for best deliverability)
async def send():
    msg = await client.inboxes.messages.send(
        inbox_id=inbox_id,
        to="kevinjdragan@gmail.com",
        subject="Research Report Ready",
        text="Hi Kevin, the research report is attached.",
        html="<p>Hi Kevin,</p><p>The research report is attached.</p>",
        labels=["outbound", "report"],
    )
    print(f"Sent: {msg.message_id}")

asyncio.run(send())
```

## Creating Drafts (Human-in-the-Loop)

```python
# Create a draft for Kevin to review before sending
async def create_draft():
    draft = await client.inboxes.drafts.create(
        inbox_id=inbox_id,
        to="recipient@example.com",
        subject="Partnership Proposal",
        text="Draft content for review...",
        html="<p>Draft content for review...</p>",
    )
    print(f"Draft created: {draft.draft_id} — awaiting approval")
    
    # After approval:
    # await client.inboxes.drafts.send(inbox_id=inbox_id, draft_id=draft.draft_id)
```

## Replying to Threads

```python
# Reply to an existing email thread
async def reply():
    msg = await client.inboxes.messages.reply(
        inbox_id=inbox_id,
        message_id="msg_abc123",
        text="Thanks for your email! I'll look into this.",
        html="<p>Thanks for your email! I'll look into this.</p>",
    )
    print(f"Reply sent: {msg.message_id}")
```

## Reading Messages

```python
# List recent messages
async def check_inbox():
    messages = await client.inboxes.messages.list(inbox_id=inbox_id)
    for msg in messages.messages:
        print(f"From: {msg.from_} | Subject: {msg.subject}")

# Get specific message
async def read_message(message_id: str):
    msg = await client.inboxes.messages.get(inbox_id=inbox_id, message_id=message_id)
    print(f"Body: {msg.text}")
```

## Sending Attachments

```python
import base64

async def send_with_attachment(file_path: str, to: str, subject: str):
    with open(file_path, "rb") as f:
        content = base64.b64encode(f.read()).decode()
    
    filename = os.path.basename(file_path)
    # Infer content type
    ct = "application/pdf" if filename.endswith(".pdf") else "application/octet-stream"
    
    msg = await client.inboxes.messages.send(
        inbox_id=inbox_id,
        to=to,
        subject=subject,
        text=f"Please find {filename} attached.",
        html=f"<p>Please find <strong>{filename}</strong> attached.</p>",
        attachments=[{"content": content, "filename": filename, "content_type": ct}],
    )
    print(f"Sent with attachment: {msg.message_id}")
```

## Ops API Endpoints (Dashboard/UI reference only — do NOT use from agent execution)

> **WARNING**: These endpoints are for the web dashboard UI only.
> During agent execution, ALWAYS use the Python SDK above.
> The API port varies by deployment (8000, 9001, etc.) and `localhost` may not resolve correctly.

- `GET /api/v1/ops/agentmail` — Service status (inbox address, WS state, counters)
- `GET /api/v1/ops/agentmail/messages?label=&limit=20` — List recent messages
- `POST /api/v1/ops/agentmail/send` — Send email `{"to", "subject", "text", "html?", "force_send?"}`
- `POST /api/v1/ops/agentmail/drafts/{draft_id}/send` — Approve and send a draft

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `UA_AGENTMAIL_ENABLED` | `1` | Master toggle |
| `AGENTMAIL_API_KEY` | — | API key (Infisical) |
| `UA_AGENTMAIL_INBOX_ADDRESS` | `oddcity216@agentmail.to` | Simone's inbox address |
| `UA_AGENTMAIL_INBOX_USERNAME` | `simone` | Username if creating new inbox |
| `UA_AGENTMAIL_AUTO_SEND` | `0` | `1` = send directly, `0` = create drafts |
| `UA_AGENTMAIL_WS_ENABLED` | `1` | WebSocket listener for inbound email |

## Real-Time Events (Reference)

- [webhooks.md](references/webhooks.md) — HTTP-based notifications (VPS production)
- [websockets.md](references/websockets.md) — Persistent connection (no public URL needed)

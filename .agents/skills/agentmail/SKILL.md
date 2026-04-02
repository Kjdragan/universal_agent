---
name: agentmail
description: FOR DEVELOPMENT ONLY - Not for agent use. Guide for the AgentMail Python/Node SDK. Use ONLY when writing code for an external application that integrates AgentMail. If YOU need to send an email as an agent, DO NOT use this skill—use your native MCP email tool instead.
---

# AgentMail SDK

AgentMail is an API-first email platform for AI agents. Install the SDK and initialize the client.

## Installation

```bash
# TypeScript/Node
npm install agentmail

# Python
pip install agentmail
```

## Setup

```typescript
import { AgentMailClient } from "agentmail";
const client = new AgentMailClient({ apiKey: "YOUR_API_KEY" });
```

```python
from agentmail import AgentMail
client = AgentMail(api_key="YOUR_API_KEY")
```

## ⚠️ CRITICAL: SDK Response Objects (MUST READ)

AgentMail SDK methods return **Pydantic model objects**, NOT Python dicts. You MUST access properties via **dot notation**:

```python
# ✅ CORRECT — use dot notation for response properties
msg = client.inboxes.messages.send(inbox_id=..., to=..., subject=..., text=...)
print(msg.message_id)  # str — the sent message ID
print(msg.thread_id)   # str — the thread ID

inbox = client.inboxes.create(username="test")
print(inbox.inbox_id)  # str — the inbox email address

draft = client.inboxes.drafts.create(inbox_id=..., to=..., subject=..., text=...)
print(draft.draft_id)  # str — the draft ID

# ❌ WRONG — these WILL CRASH with AttributeError
msg.get("message_id")    # AttributeError: 'SendMessageResponse' has no attribute 'get'
msg["message_id"]        # TypeError: not subscriptable
result.get("status")     # AttributeError — response objects are NOT dicts
```

**Common response properties:**
- `SendMessageResponse`: `.message_id`, `.thread_id`
- `Inbox`: `.inbox_id`
- `Draft`: `.draft_id`
- `Message`: `.message_id`, `.thread_id`, `.from_`, `.to`, `.subject`, `.text`, `.html`, `.labels`, `.created_at`, `.attachments`
- `Thread`: `.thread_id`, `.messages`, `.subject`, `.updated_at`

## Inboxes

Create scalable inboxes on-demand. Each inbox has a unique email address.

```typescript
// Create inbox (auto-generated address)
const autoInbox = await client.inboxes.create();

// Create with custom username and domain
const customInbox = await client.inboxes.create({
  username: "support",
  domain: "yourdomain.com",
});

// List, get, delete
const inboxes = await client.inboxes.list();
const fetchedInbox = await client.inboxes.get({
  inboxId: "inbox@agentmail.to",
});
await client.inboxes.delete({ inboxId: "inbox@agentmail.to" });
```

```python
# Create inbox (auto-generated address)
inbox = client.inboxes.create()

# Create with custom username and domain
inbox = client.inboxes.create(username="support", domain="yourdomain.com")

# List, get, delete
inboxes = client.inboxes.list()
inbox = client.inboxes.get(inbox_id="inbox@agentmail.to")
client.inboxes.delete(inbox_id="inbox@agentmail.to")
```

## Messages

Always send both `text` and `html` for best deliverability.

```typescript
// Send message
await client.inboxes.messages.send({
  inboxId: "agent@agentmail.to",
  to: "recipient@example.com",
  subject: "Hello",
  text: "Plain text version",
  html: "<p>HTML version</p>",
  labels: ["outreach"],
});

// Reply to message
await client.inboxes.messages.reply({
  inboxId: "agent@agentmail.to",
  messageId: "msg_123",
  text: "Thanks for your email!",
});

// List and get messages
const messages = await client.inboxes.messages.list({
  inboxId: "agent@agentmail.to",
});
const message = await client.inboxes.messages.get({
  inboxId: "agent@agentmail.to",
  messageId: "msg_123",
});

// Update labels
await client.inboxes.messages.update({
  inboxId: "agent@agentmail.to",
  messageId: "msg_123",
  addLabels: ["replied"],
  removeLabels: ["unreplied"],
});
```

```python
# Send message
client.inboxes.messages.send(
    inbox_id="agent@agentmail.to",
    to="recipient@example.com",
    subject="Hello",
    text="Plain text version",
    html="<p>HTML version</p>",
    labels=["outreach"]
)

# Reply to message
client.inboxes.messages.reply(
    inbox_id="agent@agentmail.to",
    message_id="msg_123",
    text="Thanks for your email!"
)

# List and get messages
messages = client.inboxes.messages.list(inbox_id="agent@agentmail.to")
message = client.inboxes.messages.get(inbox_id="agent@agentmail.to", message_id="msg_123")

# Update labels
client.inboxes.messages.update(
    inbox_id="agent@agentmail.to",
    message_id="msg_123",
    add_labels=["replied"],
    remove_labels=["unreplied"]
)
```

## Threads

Threads group related messages in a conversation.

```typescript
// List threads (with optional label filter)
const threads = await client.inboxes.threads.list({
  inboxId: "agent@agentmail.to",
  labels: ["unreplied"],
});

// Get thread details
const thread = await client.inboxes.threads.get({
  inboxId: "agent@agentmail.to",
  threadId: "thd_123",
});

// Org-wide thread listing
const allThreads = await client.threads.list();
```

```python
# List threads (with optional label filter)
threads = client.inboxes.threads.list(inbox_id="agent@agentmail.to", labels=["unreplied"])

# Get thread details
thread = client.inboxes.threads.get(inbox_id="agent@agentmail.to", thread_id="thd_123")

# Org-wide thread listing
all_threads = client.threads.list()
```

## Attachments

Send attachments with Base64 encoding. Retrieve from messages or threads.

```typescript
// Send with attachment
const content = Buffer.from(fileBytes).toString("base64");
await client.inboxes.messages.send({
  inboxId: "agent@agentmail.to",
  to: "recipient@example.com",
  subject: "Report",
  text: "See attached.",
  attachments: [
    { content, filename: "report.pdf", contentType: "application/pdf" },
  ],
});

// Get attachment
const fileData = await client.inboxes.messages.getAttachment({
  inboxId: "agent@agentmail.to",
  messageId: "msg_123",
  attachmentId: "att_456",
});
```

```python
import base64

# Send with attachment
content = base64.b64encode(file_bytes).decode()
client.inboxes.messages.send(
    inbox_id="agent@agentmail.to",
    to="recipient@example.com",
    subject="Report",
    text="See attached.",
    attachments=[{"content": content, "filename": "report.pdf", "content_type": "application/pdf"}]
)

# Get attachment
file_data = client.inboxes.messages.get_attachment(
    inbox_id="agent@agentmail.to",
    message_id="msg_123",
    attachment_id="att_456"
)
```

## Drafts

Create drafts for human-in-the-loop approval before sending.

```typescript
// Create draft
const draft = await client.inboxes.drafts.create({
  inboxId: "agent@agentmail.to",
  to: "recipient@example.com",
  subject: "Pending approval",
  text: "Draft content",
});

// Send draft (converts to message)
await client.inboxes.drafts.send({
  inboxId: "agent@agentmail.to",
  draftId: draft.draftId,
});
```

```python
# Create draft
draft = client.inboxes.drafts.create(
    inbox_id="agent@agentmail.to",
    to="recipient@example.com",
    subject="Pending approval",
    text="Draft content"
)

# Send draft (converts to message)
client.inboxes.drafts.send(inbox_id="agent@agentmail.to", draft_id=draft.draft_id)
```

## Pods

Multi-tenant isolation for SaaS platforms. Each customer gets isolated inboxes.

```typescript
// Create pod for a customer
const pod = await client.pods.create({ clientId: "customer_123" });

// Create inbox within pod
const inbox = await client.inboxes.create({ podId: pod.podId });

// List resources scoped to pod
const inboxes = await client.inboxes.list({ podId: pod.podId });
```

```python
# Create pod for a customer
pod = client.pods.create(client_id="customer_123")

# Create inbox within pod
inbox = client.inboxes.create(pod_id=pod.pod_id)

# List resources scoped to pod
inboxes = client.inboxes.list(pod_id=pod.pod_id)
```

## Idempotency

Use `clientId` for safe retries on create operations.

```typescript
const inbox = await client.inboxes.create({
  clientId: "unique-idempotency-key",
});
// Retrying with same clientId returns the original inbox, not a duplicate
```

```python
inbox = client.inboxes.create(client_id="unique-idempotency-key")
# Retrying with same client_id returns the original inbox, not a duplicate
```

## Real-Time Events

For real-time notifications, see the reference files:

- [webhooks.md](references/webhooks.md) - HTTP-based notifications (requires public URL)
- [websockets.md](references/websockets.md) - Persistent connection (no public URL needed)

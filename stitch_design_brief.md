# AgentMail Tab — Stitch Design Brief

## What This Page Is

A "Mail" tab within the Universal Agent dashboard. This tab gives the human operator full visibility into all email conversations happening across 3 AI agent inboxes, pending drafts awaiting human approval, and links to email-triggered tasks/sessions.

It's a **tactical email command center** — not a traditional inbox, but an operational view of agent-to-human and agent-to-system communications.

---

## Functional Requirements

### 1. Inbox Filtering

The system has 3 inboxes. Users must be able to filter the view by inbox:

| Inbox | Purpose | Traffic Type |
|---|---|---|
| **simone** (`oddcity216@agentmail.to`) | Primary — Simone (AI assistant) ↔ Kevin (operator) | Personal emails, task responses, reports |
| **vp.agents** (`vp.agents@agentmail.to`) | VP agent outputs + freelance demo testing | Agent work products, demo/test emails tagged `[DEMO]` |
| **system.alerts** (`system.alerts@agentmail.to`) | System health notifications | Automated alerts, monitoring, errors |

An **"All"** view combines all inboxes. Each filter should show its count of active threads.

### 2. Thread/Conversation List

Shows email threads sorted by most recent activity. Each thread displays:
- **Subject line** (truncated to ~2 lines)
- **Preview** of the latest message body (1–2 lines)
- **Time since last activity** (e.g., "2m ago", "1h ago")
- **Message count** in the thread (e.g., "4 msgs")
- **Participants** (sender names)
- **Inbox source** — which inbox this thread belongs to (visually differentiated)
- **Badges** for special classifications:
  - `[DEMO]` — freelance project test thread
  - `[ALERT]` — system alert thread
  - `NEW` — unreplied/unread indicator
- **Attachment indicator** if any message has attachments

Clicking a thread selects it and shows its full message history.

### 3. Thread Detail / Message Reader

When a thread is selected, display the full conversation:
- **Thread header**: Subject, inbox badge, timestamp
- **Message list** (chronological): Each message shows sender, timestamp, and body
- **Sender context**: Distinguish between AI agent messages and human messages (different avatar/icon)
- **Linked operational items** (if they exist):
  - Todoist task created from this email thread → clickable link to Todolist tab
  - Agent session spawned by this email → clickable link to Sessions tab

### 4. Draft Approval Queue

Simone (the AI) can create email drafts for human review before sending. This section surfaces:
- **Pending drafts** awaiting approval
- For each draft: recipient, subject, preview text, send_status
- **One-click "Approve & Send" action** — sends the draft immediately via API
- **Scheduled drafts** — shows send_at time if one is set

This is arguably the most actionable section — it's where the human-in-the-loop workflow lives.

### 5. Stats Summary

Quick-glance operational counters:
- Messages sent (total)
- Messages received (total)
- Active inboxes (count)
- Pending drafts (count)
- WebSocket connection status (connected/disconnected)

### 6. User Actions

| Action | Description |
|---|---|
| **Filter by inbox** | Toggle the thread list between all / simone / vp.agents / system.alerts |
| **Select thread** | Click to load full message history |
| **Approve & send draft** | One-click to send a pending Simone-authored draft |
| **Refresh** | Manual reload of threads and stats |
| **Dismiss thread** | Remove a thread from the list |
| **Quick reply** | Input field + send button to reply via Simone's inbox |
| **Navigate to linked task** | Click badge to jump to Todolist tab |
| **Navigate to linked session** | Click badge to jump to Sessions tab |

---

## Data Sources

All data is fetched from the AgentMail REST API via our backend. Here's what's available:

### Thread List (from `client.threads.list()`)
```
subject, preview, senders[], recipients[], message_count,
inbox_id, labels[], timestamp, attachments[{filename, size}],
created_at, updated_at
```

### Message Detail (from `client.inboxes.messages.get()`)
```
from_, to[], subject, text, html,
timestamp, attachments[{filename, size, content_type}]
```

### Drafts (from `client.drafts.list()`)
```
to[], subject, preview, send_status ("scheduled" | null),
send_at, inbox_id, attachments[]
```

### Stats (from our internal `AgentMailService.status()`)
```
messages_sent, messages_received, connected (bool),
inbox_count, ws_enabled, last_error
```

---

## Design System Reference (for Stitch)

These are the existing dashboard's design tokens. Use them as a starting point for visual consistency.

### Color Palette (Dark Mode)

| Role | HSL | Hex | Name |
|---|---|---|---|
| Background | `136, 28%, 9%` | `#111D13` | Carbon Black |
| Foreground / Text | `38, 28%, 80%` | `#D4C9A8` | Warm Cream |
| Card surfaces | `108, 16%, 31%` | `#4B5D43` | Hunter Green |
| Primary accent | `128, 24%, 71%` | `#A0CCA5` | Celadon |
| Secondary | `33, 22%, 64%` | `#B8A98F` | Warm Tan |
| Muted text | `120, 12%, 50%` | `#709275` | Muted Teal |
| Action / CTA | `36, 56%, 58%` | `#D4A056` | Warm Amber |
| Error | `0, 33%, 60%` | `#CC6666` | Muted Red |
| Borders | `110, 14%, 22%` | — | Subtle green-black |

### Fonts

- **Body / Monospace**: `JetBrains Mono` — used as the default system font
- **Display / Headers**: `Inter` — clean sans-serif for page titles and card headers
- **Icons**: `Material Symbols Outlined` (Google Fonts, variable weight)

### Surface Style

- Dark glassmorphic panels with subtle blur and transparency
- Very subtle grid overlay on the background
- Noise texture overlay at very low opacity
- Thin scrollbars with gradient thumbs
- Corner bracket decorative elements on panels

### Existing Tab Reference

The closest existing tab in terms of layout pattern is the **CSI (Creator Signal Intelligence)** tab, which uses a two-panel centered layout: a list panel on the left and a detail/reader panel on the right.

# AgentMail Activation & Digest Email Flow — Full Plan

**Created**: 2026-03-06
**Status**: Implementation in progress

## Goal

Enable Simone to send YouTube RSS Digest emails FROM her own AgentMail address (`oddcity216@agentmail.to`, display name: Simone D), so that when Kevin replies, the reply goes directly to Simone's inbox where she can read and act on instructions.

## Current State

- **AgentMail account**: Active at console.agentmail.io (Kevin's Org)
- **Simone's inbox**: `oddcity216@agentmail.to` (display name: Simone D)
- **AGENTMAIL_API_KEY**: In Infisical (dev + kevins-desktop)
- **AgentMail service**: Fully built (`src/universal_agent/services/agentmail_service.py`)
- **WebSocket listener**: Implemented with reconnect logic
- **email-handler agent**: Enhanced with classification → action table, sender recognition, delegation keywords
- **email_identity.md**: Updated with two-concept identity model (Simone's identity vs Kevin's identity)

## Phase 1: Activate AgentMail (Infrastructure) — MOSTLY DONE

### 1.1 Account & API Key ✅
- Account: Kevin's Org at console.agentmail.to
- API key: In Infisical (dev + kevins-desktop)
- Inbox: `oddcity216@agentmail.to`

### 1.2 Add Remaining Config to Infisical ⬜
```
UA_AGENTMAIL_ENABLED=1
UA_AGENTMAIL_INBOX_ADDRESS=oddcity216@agentmail.to
UA_AGENTMAIL_WS_ENABLED=1
UA_AGENTMAIL_AUTO_SEND=0
```

### 1.3 Deploy & Verify ⬜
- Restart gateway on VPS
- Check logs: `journalctl -u universal-agent -n 50 --no-pager | grep AgentMail`
- Verify via ops endpoint: `curl http://localhost:8002/api/v1/ops/agentmail`
- Confirm inbox address and WS connected status

## Phase 2: Switch Digest Emails to AgentMail

### 2.1 Understand Current Digest Flow
The YouTube RSS Digest email is **not** sent by any CSI script. It's composed and sent by Simone during an autonomous heartbeat/cron session using GWS Gmail MCP tools (`mcp__gws__gmail.*`), which sends FROM `kevinjdragan@gmail.com`.

### 2.2 Create Digest Email Routing Rule
Add routing guidance so that when Simone composes digest emails, she uses AgentMail instead of Gmail:

**Option A — Agent Knowledge Update** (preferred):
Update `.claude/knowledge/email_identity.md` to explicitly route digest/report emails through AgentMail:
```markdown
### Digest & Report Emails
When sending periodic digests (YouTube RSS Digest, CSI reports, etc.) to Kevin:
- ALWAYS use **AgentMail** — never Gmail
- This ensures replies come back to Simone's inbox for processing
- Use labels: ["digest", "youtube-rss"] for easy filtering
```

**Option B — Dedicated Digest Script**:
Create a Python script that runs on a cron timer, reads the latest CSI report product from SQLite, formats it, and sends via AgentMail SDK. This is more reliable than relying on the agent to choose the right email system.

### 2.3 Test Outbound Digest
- Trigger a digest manually via ops endpoint: `POST /api/v1/ops/agentmail/send`
- Verify Kevin receives it from Simone's AgentMail address
- Verify Reply-To goes to Simone's inbox

## Phase 3: Wire Up Inbound Reply Processing

### 3.1 Verify email-handler Agent
The `_handle_inbound_email` method in `agentmail_service.py` already dispatches to the `email-handler` agent via the hooks pipeline:
```python
action_payload = {
    "kind": "agent",
    "name": "AgentMailInbound",
    "session_key": f"agentmail_{thread_id or message_id}",
    "to": "email-handler",
    "deliver": True,
    "message": f"Inbound email received...\n{text_body[:4000]}",
}
```

### 3.2 Configure email-handler Agent Capabilities
Ensure `.claude/agents/email-handler.md` can:
- Parse Kevin's reply instructions (e.g., "investigate YouTube transcript blocking")
- Route tasks to appropriate sub-agents (youtube-expert, csi agent, etc.)
- Create sessions for investigation work
- Report back via AgentMail reply

### 3.3 Test End-to-End Reply Flow
1. Simone sends a YouTube RSS Digest via AgentMail → Kevin's Gmail
2. Kevin replies with instructions (e.g., "Investigate why transcript fetching is failing")
3. Reply arrives at Simone's AgentMail inbox via WebSocket
4. `_handle_inbound_email` dispatches to email-handler agent
5. email-handler processes the instruction
6. Simone replies in-thread with findings

## Phase 4: Hardening & Polish

### 4.1 Switch to Auto-Send for Digests
Once verified working:
```env
UA_AGENTMAIL_AUTO_SEND=1  # No longer need draft approval for digests
```

### 4.2 Add Digest Labels
Tag outbound digests with labels (`["digest", "youtube-rss"]`, `["digest", "csi-report"]`) for inbox organization.

### 4.3 Add Reply Classification
Enhance email-handler to classify reply intent:
- **Investigation request** → spawn research session
- **Acknowledgement** → log and close thread
- **Configuration change** → route to ops handler
- **Follow-up question** → reply with context from previous digest

### 4.4 Monitor & Alert
- Track `messages_sent` / `messages_received` counters via ops endpoint
- Alert if WebSocket disconnects and doesn't reconnect within 5 minutes
- Alert if inbound emails fail to dispatch

## Environment Variables Summary

| Variable | Value | Purpose |
|---|---|---|
| `UA_AGENTMAIL_ENABLED` | `1` | Master toggle |
| `AGENTMAIL_API_KEY` | (Infisical) | API authentication |
| `UA_AGENTMAIL_INBOX_ADDRESS` | (from app.agentmail.to) | Simone's inbox address |
| `UA_AGENTMAIL_INBOX_USERNAME` | `simone` | Fallback username for inbox creation |
| `UA_AGENTMAIL_AUTO_SEND` | `0` → `1` | Start with drafts, graduate to auto-send |
| `UA_AGENTMAIL_WS_ENABLED` | `1` | WebSocket listener for inbound email |

## Risk Mitigations

- **Draft-first policy** prevents accidental emails during initial testing
- **WebSocket reconnect** has exponential backoff (2s → 120s max) with jitter
- **Inbox creation is idempotent** — uses `client_id=ua-simone-primary` to avoid duplicates
- **Gmail remains available** for Kevin's personal email management — no disruption

## Dependencies

- AgentMail account and API key
- Infisical secret store access
- VPS deployment restart
- email-handler agent definition review

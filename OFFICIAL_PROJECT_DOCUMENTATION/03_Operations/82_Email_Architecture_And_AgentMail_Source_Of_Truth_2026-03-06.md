# Email Architecture and AgentMail Source of Truth (2026-03-06)

## Purpose

This document is the canonical source of truth for how email works in Universal Agent today.

It defines:
- which email identity Simone should use in each scenario
- how outbound email is sent
- how inbound email is received and routed
- where the implementation lives
- what environment variables and operational checks matter
- what is implemented now versus what remains optional or legacy

This document supersedes ad hoc mental models and should be preferred over older planning notes when describing current system behavior.

## Executive Summary

Universal Agent currently uses **two distinct email identities**:

- **AgentMail** is Simone's own email identity and is the default for Simone's independent work
- **Gmail via gws MCP** is Kevin's identity and is used only when Simone is explicitly acting as Kevin or managing Kevin's inbox

The production inbound path for Simone email is:

1. AgentMail inbox receives a message
2. `AgentMailService` consumes the event via **WebSocket**
3. The service extracts the **new reply content** from the thread body
4. The service dispatches a trusted internal action to the hooks pipeline
5. The hooks pipeline routes the message to the `email-handler` agent
6. The `email-handler` agent classifies intent and acts

The current production outbound policy is:

- Simone's work goes out through **AgentMail**
- outbound mail is **draft-first by default** unless explicitly forced or policy is changed
- digest/report traffic to Kevin should **always** use AgentMail so replies come back to Simone

The current trusted inbound sender list defaults to:

- `kevin.dragan@outlook.com`
- `kevinjdragan@gmail.com`
- `kevin@clearspringcg.com`

Trusted inbound mail from those addresses is treated as operator mail. It receives an immediate in-thread acknowledgement from the transport layer, is persisted to a durable inbox queue, and is retried with exponential backoff when Simone is busy before being routed into the normal email handling flow.

## Identities and Routing Rules

### 1. Simone Identity — AgentMail

**System**: AgentMail

**Current inbox**: `Simone D <oddcity216@agentmail.to>`

**Use AgentMail when**:
- Simone sends digests, reports, or status updates to Kevin
- Simone sends research or work products to anyone
- Simone communicates on her own behalf as the agent
- the recipient should understand they are speaking to Simone, not Kevin

**Why**:
- the work should leave Simone's trail, not Kevin's
- replies should return to Simone's inbox for automated handling
- this preserves clean identity separation between operator and agent

### 2. Kevin Identity — Gmail via gws MCP

**System**: Google Workspace / Gmail MCP tooling

**Current user-facing identity**: Kevin's Gmail

**Use Gmail only when**:
- Kevin explicitly asks to send something from his email
- Kevin asks Simone to check or manage his inbox
- the task is clearly about Kevin acting as himself

**Do not use Gmail when**:
- sending Simone-authored digests or reports
- sending Simone-authored research summaries
- delivering normal agent work products where replies should route to Simone

## Canonical Routing Table

| Scenario | System | Rationale |
|---|---|---|
| Simone sends Kevin a digest | AgentMail | Replies must come back to Simone |
| Simone sends Kevin a report | AgentMail | Simone's own authored work |
| Simone sends research findings | AgentMail | Preserve Simone identity |
| Simone emails an external contact as herself | AgentMail | Agent identity |
| Kevin says "send from my email" | Gmail | Explicit Kevin identity |
| Kevin says "check my email" | Gmail | Kevin inbox management |
| Kevin replies to Simone's digest | AgentMail inbound | reply is handled in Simone pipeline |

## Current Production Implementation

### Core Service

Primary implementation:
- `src/universal_agent/services/agentmail_service.py`

This service is responsible for:
- enabling/disabling AgentMail from env vars
- initializing the AgentMail SDK client
- resolving or creating Simone's inbox idempotently
- sending direct emails
- creating drafts
- replying in-thread
- listing messages and threads
- maintaining a persistent WebSocket listener for inbound messages
- dispatching inbound mail into the hooks system
- exposing operational status counters

### Gateway Integration

AgentMail is instantiated during gateway startup in:
- `src/universal_agent/gateway_server.py`

The gateway wires a trusted dispatch function:
- `AgentMailService` -> `_agentmail_dispatch_fn(...)` -> `HooksService.dispatch_internal_action(...)`

Operational endpoints are also exposed from the gateway:
- `GET /api/v1/ops/agentmail`
- `GET /api/v1/ops/agentmail/messages`
- `POST /api/v1/ops/agentmail/send`
- `POST /api/v1/ops/agentmail/drafts/{draft_id}/send`

### Trusted Internal Dispatch Path

Inbound AgentMail messages do **not** enter through public webhook auth in the primary production path.

Instead, the service dispatches an in-process trusted action through:
- `src/universal_agent/hooks_service.py`
- `HooksService.dispatch_internal_action(...)`

This bypasses external auth and mapping resolution because the caller is already trusted and inside the UA process.

## Inbound Email Flow

### Primary Path: WebSocket Listener

The primary inbound architecture is **WebSocket-based**, not webhook-based.

Implementation:
- `AgentMailService._ws_loop()`
- `AgentMailService._ws_connect_and_listen()`

Behavior:
- opens an outbound WebSocket connection to AgentMail
- subscribes to Simone's inbox
- listens for `MessageReceivedEvent`
- reconnects with exponential backoff and jitter on disconnect

This design is preferred because:
- it does not require exposing a public webhook endpoint
- it works with outbound-only VPS networking
- it gives low-latency inbound handling
- it keeps the connection always on during gateway runtime

### Inbound Parsing and Reply Extraction

Implementation:
- `AgentMailService._handle_inbound_email(...)`
- `AgentMailService._extract_reply_text(...)`

Before forwarding an inbound email to the `email-handler` agent, the service extracts the **new reply content** from the plain-text body using:
- `email-reply-parser`

This avoids confusing the email agent with full quoted thread history.

Current behavior:
- if quoted history is detected, the dispatch payload contains a `--- Reply (new content) ---` section first
- if extraction changed the content, the full original body is also included afterward as reference
- if extraction fails or yields nothing useful, the original body is preserved

This is important for digest replies, because Kevin's instruction should be isolated from the quoted digest below it.

### Trusted Sender Handling

Implementation:
- `AgentMailService._normalize_sender_email(...)`
- `AgentMailService._trusted_sender_addresses(...)`

Current behavior:
- trusted sender addresses are read from `UA_AGENTMAIL_TRUSTED_SENDERS`
- if the env var is unset, the three Kevin addresses above are used as the default allowlist
- sender trust is determined in runtime transport logic, not by LLM prompt interpretation
- trusted inbound mail gets an immediate acknowledgement reply before the deeper handler work continues
- trusted inbound mail is stored in `agentmail_inbox_queue` inside the activity DB before dispatch is attempted
- when the target session is busy, the queue item moves to retry mode and is re-attempted with exponential backoff instead of being dropped
- trusted sender metadata is attached to the internal payload:
  - `sender_email`
  - `sender_role`
  - `sender_trusted`

This closes the gap where unsolicited direct mail from one of Kevin's valid addresses could be treated like generic external mail.

### Trusted Inbox Queue and Retry Behavior

Implementation:
- `AgentMailService._queue_insert_trusted_inbound(...)`
- `AgentMailService._trusted_inbox_queue_loop(...)`
- `HooksService.dispatch_internal_action_with_admission(...)`

Current behavior:
- trusted inbound messages are persisted before work admission is attempted
- if the hook dispatch path reports `busy`, the queue item is not discarded
- the queue retries with exponential backoff and jitter until Simone is free
- queue state is visible through ops endpoints:
  - `GET /api/v1/ops/agentmail/inbox-queue`
  - `GET /api/v1/ops/agentmail/inbox-queue/{queue_id}`
  - `POST /api/v1/ops/agentmail/inbox-queue/{queue_id}/retry-now`
  - `POST /api/v1/ops/agentmail/inbox-queue/{queue_id}/cancel`

This is the current production answer to unsolicited direct mail from Kevin: queue first, acknowledge immediately, retry until admitted.

### Routing to the Email Handler Agent

Inbound mail is dispatched to:
- `.claude/agents/email-handler.md`

The `email-handler` agent is responsible for:
- sender recognition
- intent classification
- deciding whether to investigate, reply, log, ignore, or draft a response
- handling Kevin's replies as high-priority instructions

Current agent rules include:
- Kevin replies are high-value operational instructions
- external inquiries should usually become drafted replies
- spam and bounce-like messages should not be replied to

## Outbound Email Flow

### AgentMail Outbound

Primary methods in `AgentMailService`:
- `send_email(...)`
- `_send_direct(...)`
- `_create_draft(...)`
- `send_draft(...)`
- `reply(...)`

Current policy:
- if `UA_AGENTMAIL_AUTO_SEND=0`, outbound mail becomes a draft by default
- if `force_send=True` or `UA_AGENTMAIL_AUTO_SEND=1`, the message is sent directly

This means the current default posture is **human-in-the-loop** for outbound mail.

### Digest and Report Policy

Digest/report mail to Kevin should be sent from Simone's AgentMail inbox, not Kevin's Gmail.

Reasons:
- replies route back into Simone's processing loop
- Kevin sees the message as coming from Simone
- it avoids Kevin appearing to email himself

Recommended labels for outbound email include:
- `digest`
- `youtube-rss`
- `csi-report`
- `report`
- `research`

## Webhooks: Status and Role

There is also an AgentMail webhook transform implementation at:
- `webhook_transforms/agentmail_transform.py`

That code transforms AgentMail webhook payloads into hook actions for the `email-handler` agent.

However, **this is not the primary production path today**.

Current status:
- WebSocket delivery is the **canonical production** inbound path
- the webhook transform is **formally deprecated** as of 2026-03-06
- a runtime deprecation warning is logged whenever the webhook transform is invoked
- the webhook transform file is retained only as reference and emergency fallback

Important note on parity:
- the webhook transform now imports `_extract_reply_text` from `agentmail_service.py`, so reply extraction parity exists
- however, the WebSocket path remains the only actively maintained and tested path

Do **not** use the webhook transform as the primary ingest path for new deployments.

## Environment Variables

Canonical AgentMail env vars:

| Variable | Current Intended Value | Purpose |
|---|---|---|
| `UA_AGENTMAIL_ENABLED` | `1` | Master toggle |
| `AGENTMAIL_API_KEY` | secret in Infisical | AgentMail auth |
| `UA_AGENTMAIL_INBOX_ADDRESS` | `oddcity216@agentmail.to` | Simone inbox |
| `UA_AGENTMAIL_INBOX_USERNAME` | `simone` | Fallback inbox username if creation is needed |
| `UA_AGENTMAIL_AUTO_SEND` | `0` | Draft-first policy |
| `UA_AGENTMAIL_WS_ENABLED` | `1` | Enable WebSocket listener |
| `UA_AGENTMAIL_WS_RECONNECT_BASE_DELAY` | `2` | Base reconnect backoff seconds |
| `UA_AGENTMAIL_WS_RECONNECT_MAX_DELAY` | `120` | Max reconnect backoff seconds |

Current known secret/config state:
- `AGENTMAIL_API_KEY` is stored in Infisical
- AgentMail configuration has been populated in `dev` and `kevins-desktop`
- deployed production state has already been updated and restarted

## Operations and Verification

### Ops Checks

Use these endpoints with ops auth:

- `GET /api/v1/ops/agentmail`
  - confirms enabled state
  - inbox address
  - websocket state
  - reconnect count
  - sent/received counters
  - last error

- `GET /api/v1/ops/agentmail/messages`
  - quick inbox inspection

- `POST /api/v1/ops/agentmail/send`
  - send or draft outbound test messages

- `POST /api/v1/ops/agentmail/drafts/{draft_id}/send`
  - approve a previously created draft

### What “Healthy” Looks Like

A healthy AgentMail deployment should show:
- `enabled=true`
- `started=true`
- `inbox_address=oddcity216@agentmail.to`
- `ws_enabled=true`
- `ws_connected=true`
- low or stable reconnect count
- no persistent `last_error`

### Failure Modes to Watch

- missing `AGENTMAIL_API_KEY`
- configured inbox not resolvable
- websocket disconnect loop
- inbound dispatch rejected because hooks are disabled or invalid payload
- email-handler ambiguity from missing reply extraction parity in non-WebSocket paths

## Security and Policy Constraints

The current project policy is:
- default to AgentMail for Simone-authored work
- use Gmail only for Kevin-explicit identity actions
- keep outbound email draft-first by default
- do not auto-send replies without deliberate approval unless policy changes
- do not expose internal architecture or tool names in external replies
- do not forward full inbound email contents to external systems unnecessarily

## Testing and Validation References

Relevant tests:
- `tests/unit/test_agentmail_service.py`

Current coverage includes:
- service enable/disable behavior
- inbox resolution
- send/draft/reply/read operations
- reply extraction behavior
- inbound dispatch payload behavior when quoted history is present

## Source Files That Define Current Truth

Implementation:
- `src/universal_agent/services/agentmail_service.py`
- `src/universal_agent/gateway_server.py`
- `src/universal_agent/hooks_service.py`

Agent and knowledge behavior:
- `.claude/agents/email-handler.md`
- `.claude/knowledge/email_identity.md`
- `.agents/skills/agentmail/SKILL.md`

Operational reference and transition note:
- `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/AgentMail_Digest_Email_Plan.md`

Tests:
- `tests/unit/test_agentmail_service.py`

## Current Gaps and Follow-Up Items

The system is implemented and deployed, but a few improvements remain reasonable:

1. **Webhook parity**
   - if the webhook transform is ever used again, it should gain the same reply-extraction behavior as the WebSocket path

2. **HTML-aware reply extraction**
   - current extraction uses plain-text body only
   - this is sufficient for current Kevin reply handling, but richer HTML-aware extraction could be added later if needed

3. **Dedicated digest sender path**
   - digests are currently guided by routing policy and agent knowledge
   - a dedicated scripted digest sender could reduce reliance on model/tool choice in long-running autonomy scenarios

4. **Observability expansion**
   - additional alerting on prolonged websocket disconnects or repeated inbound dispatch failures would improve reliability

## Bottom Line

The official email architecture for Universal Agent is now:

- **AgentMail is Simone's primary email identity**
- **Gmail is Kevin's identity and should be used only explicitly on his behalf**
- **WebSockets are the authoritative inbound path**
- **`email-handler` is the dedicated inbound processing agent**
- **reply extraction is part of the inbound processing path to prevent quoted-thread confusion**
- **draft-first remains the default outbound safety policy**

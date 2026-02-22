# Composio YouTube Webhook Investigation Plan

**Goal**: Understand how Composio webhooks work for YouTube triggers and build a standalone test project to verify end-to-end functionality.

**Context**: Based on existing VPS deployment at `api.clearspringcg.com` with working webhook ingress, but user reported issues getting triggers to fire reliably.

---

## Phase 1: Discovery - Understand Composio's Webhook System

### 1.1 What We Know Already

From existing documentation:

**Working Components**:
- UA gateway receives webhooks at `/api/v1/hooks/composio`
- HMAC signature verification works (`composio_hmac` strategy)
- Transform `composio_youtube_transform.py` handles payload parsing
- Test webhook succeeds with synthetic payload

**Potential Issues Identified**:
- Payload shape variations (envelope vs direct form)
- Video ID extraction pitfalls (`item.id` vs `resourceId.videoId`)
- Composio UI shows confusing status messages

**Open Questions**:
1. What triggers are available in Composio for YouTube?
2. How do you configure a YouTube trigger in Composio UI?
3. What are the trigger requirements (OAuth connection, specific playlist, channel)?
4. Why does `sdkTriggerInvocation.status=ERROR` appear even when webhooks work?

### 1.2 Investigation Tasks

1. **Research Composio's YouTube integration**:
   - Available triggers (playlist, channel, subscription)
   - Required authentication/setup
   - Trigger configuration options

2. **Understand Composio webhook delivery mechanism**:
   - How triggers are registered
   - What polling interval Composio uses (it's not real push)
   - Webhook payload formats

3. **Document Composio UI behavior**:
   - What "queued" status means
   - What "no subscribers" error means
   - How to verify trigger is active

### 1.3 Deliverables

- Document: `composio_webhook_research.md` - Everything learned about Composio
- Screenshots: Composio UI showing trigger configuration
- Diagram: Composio → Webhook flow

---

## Phase 2: Build Standalone Test Project

### 2.1 Project Structure

Create isolated test environment:
```
composio_webhook_test/
├── main.py                 # FastAPI test server
├── composio_client.py       # Helper to query Composio API
├── webhook_receiver.py      # Mock webhook endpoint
├── test_payloads.py         # Sample payloads to test
├── requirements.txt
└── README.md
```

### 2.2 Components

**1. Webhook Receiver Server** (`main.py`):
- FastAPI server receiving webhooks
- HMAC signature verification (same as UA)
- Payload logging and validation
- Web dashboard to see received webhooks

**2. Composio API Client** (`composio_client.py`):
- List available triggers
- Get trigger configuration
- Check webhook subscription status
- Trigger test events

**3. Test Payloads** (`test_payloads.py`):
- Synthetic webhook payloads
- Real-world examples from documentation
- Edge cases (empty fields, missing video_id)

### 2.3 Success Criteria

- Server receives and logs webhooks from Composio
- Can verify signature locally
- Can query Composio API for trigger status
- Can send test webhook to self

---

## Phase 3: Live Testing with Real YouTube Events

### 3.1 Test Setup

1. Create a test YouTube playlist
2. Configure Composio trigger for that playlist
3. Add videos to playlist
4. Observe what gets delivered

### 3.2 Data Collection

For each webhook received:
- Timestamp
- Full payload
- Signature headers
- Processing time
- Any errors

### 3.3 Variations to Test

1. **Different event types**:
   - Video added to playlist
   - New upload from subscribed channel
   - Video metadata updates

2. **Different payload shapes**:
   - Envelope form vs direct form
   - With/without optional fields

3. **Error cases**:
   - Duplicate delivery
   - Malformed signatures
   - Missing required fields

---

## Phase 4: Analysis and Documentation

### 4.1 What Works

Document:
- Exact trigger configuration that works
- Payload format received
- Frequency of polling (deduced from timestamps)
- Reliability observations

### 4.2 What Doesn't Work

Document:
- Trigger configurations that fail
- Common failure modes
- Composio UI confusion points

### 4.3 Recommendations

For CSI integration:
- Should we use Composio webhooks or poll directly?
- What's the latency like?
- How reliable is it?
- What are the gotchas?

---

## Phase 5: Integration Decision

### 5.1 Evaluation Criteria

| Factor | Composio Webhooks | Direct API Polling |
|--------|-------------------|-------------------|
| Setup complexity | ? | Medium |
| Latency | ? | Configurable (30s+) |
| Reliability | ? | High (control it) |
| Cost | ? | Quota-based |
| Maintenance | ? | Self-contained |

### 5.2 Go/No-Go Decision

Based on findings, recommend:
1. **Go ahead with Composio** → Document integration path
2. **Stick with polling** → Document why Composio isn't viable
3. **Hybrid approach** → Use Composio when available, fallback to polling

---

## Implementation Steps (Starting Order)

1. **Create test project directory** outside UA repo
2. **Build basic webhook receiver** with FastAPI
3. **Implement Composio API client** to query status
4. **Register test webhook** with Composio
5. **Configure YouTube trigger** in Composio UI
6. **Trigger live events** and capture payloads
7. **Analyze results** and document findings
8. **Write integration recommendation** for CSI

---

## Open Questions - ANSWERED

1. **Composio Account Access**: ✅ Full API access available
   - Can use .env for `COMPOSIO_API_KEY`, project ID, etc.

2. **Test Playlist**: ✅ Create new test playlist
   - Avoid disrupting existing setup
   - Clean slate for controlled testing

3. **Deployment**: ✅ Both VPS + local
   - VPS: receive real webhooks from Composio
   - Local: faster development iteration

4. **Time Horizon**: ✅ Deep dive
   - Thorough investigation with full documentation
   - Live testing with real YouTube events

---

## Next Steps

Once questions are answered:
1. Create test project in separate directory
2. Begin Phase 1 discovery (Composio API research)
3. Build test infrastructure
4. Run live tests and document findings

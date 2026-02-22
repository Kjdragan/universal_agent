# CSI Ingester v1 - Product Requirements Document

**Version**: 1.0
**Date**: 2026-02-22
**Status**: Ready for Implementation Planning

---

## For the Implementation Team

**This PRD is your complete specification for building CSI Ingester v1.**

### How to Use This Document

1. **Read First**: Sections 1-7 provide context, scope, and architecture
2. **Build From**: Sections 8-11 contain actionable requirements (functional, data model, API)
3. **Test Against**: Section 12 defines testing strategy and acceptance criteria
4. **Operate From**: Section 13 contains runbooks and deployment guidance

### Key Constraints Already Decided

| Decision | Value | Rationale |
|----------|-------|-----------|
| **Persistence** | SQLite | Free, simple, single-instance VPS deployment |
| **Delivery Mode** | HTTP push to UA | Simple, standard, with DLQ fallback |
| **Dedupe** | CSI-only | Separation of concerns, simpler UA boundary |
| **SLOs** | Relaxed | 95% success, 5% dupes, 99% uptime |
| **Priority Tiers** | Urgent + Standard | Playlist: < 30s, Others: 5-30 min |
| **Sources (v1)** | YouTube → Reddit | Phased rollout, Threads/X deferred |
| **Config** | YAML + env vars | Familiar, simple, no auth needed for v1 |
| **Quota** | YouTube free tier | 10,000 units/day, careful polling |

### Before You Start Coding

1. **Read the strategy docs**:
   - `01_Document_01_CSI_Strategy_2026-02-22.md` - Architectural vision
   - `02_Context_For_PRD_CSI_Ingester_And_UA_Consumer_2026-02-22.md` - Handoff context

2. **Understand the UA boundary**:
   - CSI emits to: `POST /api/v1/signals/ingest`
   - UA's `hooks_service.py` has auth patterns to reference
   - Existing transforms show how UA currently handles YouTube events

3. **Lock the contract first**:
   - `CreatorSignalEvent` schema (Section 8) MUST be implemented as specified
   - Any field changes require PRD update

### Implementation Priorities

Build in this order:

```
Phase 0: Contract & Schema
├── CreatorSignalEvent Pydantic model
├── SQLite schema (events, dedupe_keys, dead_letter)
└── CSI→UA signature logic

Phase 1: Core Service
├── Service skeleton (FastAPI)
├── Config loader (YAML)
├── Health/readiness endpoints
└── Metrics/logging

Phase 2: YouTube Adapters
├── Playlist API poller
├── Channel RSS poller
├── Normalizer to Contract v1
└── Event store + dedupe

Phase 3: Delivery
├── HTTP emitter to UA
├── Retry logic with backoff
├── DLQ handling
└── Batch support

Phase 4: Operations
├── systemd service files
├── CLI tool for DLQ replay
└── Migration from existing scripts
```

### Success Criteria

You're done when all of these pass:

- [ ] Both YouTube adapters (playlist + RSS) working end-to-end
- [ ] 95%+ delivery success over 7-day test window
- [ ] < 5% duplicate rate at UA boundary
- [ ] **Urgent (playlist)**: P95 latency < 30 seconds to UA
- [ ] **Standard (channel RSS)**: P95 latency < 30 minutes to UA
- [ ] All contract tests passing (CSI schema matches UA expectations)
- [ ] Service runs as systemd with health checks

---

## Executive Summary

CSI (Creator Signal Intelligence) Ingester is a standalone VPS service that continuously ingests creator and platform signals from configured sources (YouTube first, then Reddit), normalizes them into a common event contract, and delivers them reliably to Universal Agent (UA) for downstream analysis and action.

**Primary Goal**: Replace ad-hoc trigger pipelines with a scalable, source-agnostic ingestion framework.

**Key Design Decisions**:
- **Hybrid Architecture**: CSI Ingester (separate service) + UA Consumer (within UA)
- **Persistence**: SQLite for v1 (simple, free, single-instance deployment)
- **Delivery**: HTTP push to UA webhook endpoint
- **Deduplication**: CSI-owned using SQLite
- **Rollout**: YouTube → Reddit in v1

---

## 1. Problem Statement

### 1.1 Current Pain Points

1. **One-off trigger pipelines**: Each new source requires custom integration code
2. **No unified signal contract**: Different sources produce incompatible event formats
3. **Operational burden**: Manual polling scripts, scattered transforms, no central visibility
4. **Scalability concerns**: Adding Reddit, Threads, X would multiply complexity

### 1.2 Desired Outcomes

| Outcome | Success Metric |
|---------|----------------|
| **Urgent detection** (playlist) | < 30 sec from event to UA action |
| **Standard detection** (watchlist) | < 30 min from event to UA action |
| Reliable delivery | 95%+ delivery success rate |
| Low duplicate rate | < 5% duplicate events at UA boundary |
| Low operational burden | < 1 hour/week maintenance for v1 |

**Priority Tiers**:
- **Urgent**: Playlist triggers requiring immediate explainer processing
- **Standard**: Channel RSS, watchlist monitoring, notifications, reporting

---

## 2. Scope and Non-Goals

### 2.1 In-Scope (v1)

| Component | Description |
|-----------|-------------|
| **CSI Ingester core** | Service runtime, config, health, metrics |
| **Creator Signal Contract v1** | Unified event schema |
| **CSI→UA API** | Auth, signature, retry, DLQ |
| **YouTube adapters** | Playlist API polling, Channel RSS watchlist |
| **Event management** | Dedupe, retries, dead-letter, metrics |
| **Config model** | YAML-based watchlists, routes, credentials |

### 2.2 Out-of-Scope (v1)

| Item | Reason |
|------|--------|
| Full Reddit implementation | Deferred to v1.1 |
| Threads/X adapters | Future phases |
| Cross-source trend scoring | UA responsibility |
| UI-heavy control plane | Config-file driven for v1 |
| High-availability multi-instance | Single-instance VPS deployment |

### 2.3 CSI Ingester MUST NOT Own

1. Long-form reasoning/synthesis → UA
2. Agent orchestration logic → UA
3. Skill-specific artifact generation → UA
4. Report authoring beyond raw signal stats → UA

---

## 3. Personas and User Journeys

### 3.1 Primary Personas

| Persona | Role | Goals |
|---------|------|-------|
| **Operator** | DevOps managing CSI service | Deploy, monitor, troubleshoot CSI with minimal effort |
| **UA Pipeline** | Automated consumption of events | Receive reliable, normalized events for processing |
| **Strategy User** | Consumes trend intelligence | Get timely summaries and notifications via multiple channels |

### 3.2 User Journey: YouTube Playlist Trigger

1. User adds video to curated playlist
2. CSI Playlist API poller detects new video_id
3. CSI normalizes to `CreatorSignalEvent` v1
4. CSI POSTs to `/api/v1/signals/ingest` on UA
5. UA validates and routes to `youtube-tutorial-learning` skill
6. Skill generates CONCEPT.md + IMPLEMENTATION.md artifacts
7. UA queues summary for daily report

### 3.3 User Journey: Channel Watchlist Notification

1. Creator uploads new video
2. CSI Channel RSS poller detects entry
3. CSI normalizes to `CreatorSignalEvent` v1
4. CSI POSTs to UA
5. UA generates quick summary
6. UA batches notifications (5-min window)
7. UA sends collated list to Telegram channel
8. UA queues for YouTube tab (filterable view)

---

## 4. System Architecture

### 4.1 Hybrid Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         CSI Ingester (VPS Service)                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │ YouTube      │  │ Source       │  │ Event        │              │
│  │ Adapters     │  │ Normalizer   │  │ Store (SQLite│              │
│  │              │  │              │  │ + Dedupe)    │              │
│  │ • Playlist   │  │              │  │              │              │
│  │   API Poller │  │ Contract v1  │  │ • Events     │              │
│  │ • Channel    │  │              │  │ • DLQ        │              │
│  │   RSS Watcher│  │              │  │ • Seen IDs   │              │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘              │
│         │                  │                  │                      │
│         └──────────────────┴──────────────────┘                      │
│                            │                                         │
│                            │ HTTP POST + Signature                   │
│                            ▼                                         │
└────────────────────────────│─────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Universal Agent (UA Consumer)                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │ Signal       │  │ Action       │  │ Intelligence │              │
│  │ Validator    │  │ Router       │  │ Outputs      │              │
│  │              │  │              │  │              │              │
│  │ • Auth       │  │ • Skills     │  │ • Daily      │              │
│  │ • Contract   │  │ • Agents     │  │   Report     │              │
│  │ • Dedupe     │  │ • Reports    │  │ • Telegram   │              │
│  │              │  │ • Notify     │  │ • YouTube    │              │
│  └──────────────┘  └──────────────┘  │   Tab        │              │
│                                     └──────────────┘              │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.2 CSI Ingester Components

| Component | Responsibility |
|-----------|----------------|
| **Source Adapters** | External API polling, auth, rate limiting |
| **Normalizer** | Transform raw payloads to Contract v1 |
| **Event Store** | SQLite persistence, dedupe indexing |
| **Emitter** | HTTP POST to UA with signature, retry logic |
| **Scheduler** | Per-source polling cadence management |
| **Config Loader** | YAML watchlists, routes, credentials |

---

## 5. Functional Requirements

### 5.1 Source Adapter Framework

**FR-1.1**: CSI SHALL support pluggable source adapters via Python module registry

**FR-1.2**: Each adapter SHALL implement:
```python
from abc import ABC, abstractmethod
from typing import List
from dataclasses import dataclass

@dataclass
class RawEvent:
    source: str
    event_type: str
    payload: dict
    occurred_at: str

class SourceAdapter(ABC):
    @abstractmethod
    async def fetch_events(self) -> List[RawEvent]:
        """Fetch new events from the source."""
        ...

    @abstractmethod
    def normalize(self, raw: RawEvent) -> CreatorSignalEvent:
        """Transform raw payload to CreatorSignalEvent v1."""
        ...

    @abstractmethod
    def get_dedupe_key(self, event: CreatorSignalEvent) -> str:
        """Generate dedupe key for the event."""
        ...
```

**FR-1.3**: YouTube Playlist Adapter SHALL:
- Poll `playlistItems.list` API at configured interval
- Detect new `videoId` additions
- Handle OAuth/token auth
- Respect 10,000 units/day quota budget
- Support adaptive polling: fast interval when active, slow interval when idle
- Track last-new-video timestamp per playlist to determine active/idle state
- Force idle mode when remaining quota < `min_quota_buffer`

**FR-1.4**: YouTube Channel RSS Adapter SHALL:
- Poll channel RSS feeds at configured interval (default: 300 seconds / 5 minutes)
- Parse `<yt:videoId>`, `<yt:channelId>`, `<entry>`
- Use conditional requests (`ETag`/`If-Modified-Since`)

**FR-1.5**: High-Volume Source Adapters (Reddit, Threads - future) SHALL:
- Support configurable polling intervals per source (not global)
- Use `high_volume` priority tier by default
- Support streaming mode for large feed sizes
- Implement per-source rate limiting

### 5.2 Signal Normalization

**FR-2.1**: All sources SHALL emit `CreatorSignalEvent` v1 (see Section 8)

**FR-2.2**: Normalization SHALL include:
- Source-specific field mapping to contract
- Timestamp normalization to ISO 8601 UTC
- Dedupe key generation per source/event_type
- Routing metadata attachment

### 5.3 Event Store and Dedupe

**FR-3.1**: CSI SHALL persist all normalized events to SQLite

**FR-3.2**: Dedupe keys SHALL be stored with TTL of 90 days

**FR-3.3**: CSI SHALL reject events with seen dedupe keys (idempotent)

**FR-3.4**: CSI SHALL maintain separate tables:
- `events`: Normalized events (30-day retention)
- `dedupe_keys`: Dedupe index (90-day retention)
- `dead_letter`: Failed deliveries (90-day retention)

**SQLite Schema**:
```sql
-- Normalized events
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    dedupe_key TEXT NOT NULL,
    source TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    emitted_at TEXT,
    subject_json TEXT NOT NULL,
    routing_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    delivered BOOLEAN DEFAULT FALSE,
    created_at TEXT DEFAULT (datetime('utc'))
);

CREATE INDEX idx_events_dedupe ON events(dedupe_key);
CREATE INDEX idx_events_source ON events(source);
CREATE INDEX idx_events_delivered ON events(delivered);

-- Dedupe keys (fast lookup)
CREATE TABLE dedupe_keys (
    key TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL
);
CREATE INDEX idx_dedupe_expires ON dedupe_keys(expires_at);

-- Dead letter queue
CREATE TABLE dead_letter (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    event_json TEXT NOT NULL,
    error_reason TEXT,
    retry_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('utc'))
);
```

### 5.4 CSI→UA Delivery

**FR-4.1**: CSI SHALL POST normalized events to `UA_INGEST_ENDPOINT` (configurable)

**FR-4.2**: Request format:
```http
POST /api/v1/signals/ingest
Authorization: Bearer CSI_SHARED_SECRET
Content-Type: application/json
X-CSI-Signature: sha256=<hmac_hex>
X-CSI-Timestamp: <unix_ts>

{
  "csi_version": "1.0.0",
  "csi_instance_id": "csi-vps-01",
  "batch_id": "batch_20260222_120000",
  "events": [<CreatorSignalEvent>, ...]
}
```

**FR-4.3**: CSI SHALL retry on failure with exponential backoff (max 3 attempts)

**FR-4.4**: CSI SHALL move failed events to DLQ after max retries

**FR-4.5**: CSI SHALL support batch delivery (up to 100 events per request)

### 5.5 Dead Letter Queue

**FR-5.1**: DLQ SHALL store:
- Original event payload
- Failure reason
- Retry count
- Timestamps

**FR-5.2**: CSI SHALL provide CLI tool for DLQ inspection and replay

### 5.6 Configuration Management

**FR-6.1**: Config SHALL be YAML-based at `/etc/csi/config.yaml`

**FR-6.2**: Secrets SHALL be environment variables only

**FR-6.3**: Config SHALL support:
- Watchlist definitions (per-source)
- Route profiles (event_type → action mapping)
- Source credentials references
- Polling intervals per adapter
- Feature flags

**Example Config**:
```yaml
csi:
  instance_id: "csi-vps-01"
  environment: "production"
  log_level: "INFO"

sources:
  youtube_playlist:
    enabled: true
    poll_interval_seconds: 30  # Urgent: < 30 sec latency target (when active)
    adaptive_polling:
      enabled: true
      active_interval_seconds: 30      # When recent activity detected
      idle_interval_seconds: 300       # When no activity for threshold
      activity_threshold_minutes: 15   # Consider idle after 15 min no new videos
      min_quota_buffer: 1000           # Reserve quota for other sources
    priority: "urgent"          # Fast-track delivery
    quota_limit: 10000
    playlists:
      - id: "PLxxx"
        name: "AI Tutorial Playlist"
        route_profile: "youtube_explainer"

  youtube_channel_rss:
    enabled: true
    poll_interval_seconds: 300  # 5 minutes (increased from 10 min for better batching)
    priority: "standard"        # Normal delivery
    watchlist:
      - channel_id: "UCxxx"
        creator_name: "Example Creator"
        route_profile: "channel_watchlist"

route_profiles:
  youtube_explainer:
    pipeline: "youtube_tutorial_explainer"
    priority: "urgent"          # Matches source priority
    tags: ["ai", "tutorial"]

  channel_watchlist:
    pipeline: "creator_watchlist_handler"
    priority: "standard"        # Matches source priority
    tags: ["watchlist", "notification"]

delivery:
  ua_endpoint: "${CSI_UA_ENDPOINT}"
  shared_secret: "${CSI_UA_SHARED_SECRET}"
  priority_behavior:
    urgent:
      batch_size: 1              # Deliver immediately, no batching
      max_delay_seconds: 5       # Send within 5 seconds
    standard:
      batch_size: 100            # Increased from 50 for high-volume sources
      max_delay_seconds: 30      # Send at least every 30 seconds
    high_volume:                 # For Reddit, Threads, etc. (future)
      batch_size: 500            # Large batches for high-volume sources
      max_delay_seconds: 60      # Send at least every 60 seconds
  timeout_seconds: 30

retention:
  events_days: 30
  dlq_days: 90
  dedupe_days: 90
```

### Volume Considerations by Source

| Source | Typical Volume | 10-Min Window | 5-Min Window | Recommended Polling |
|--------|---------------|---------------|--------------|-------------------|
| **YouTube Playlist** | 0-10/day | ~0.007 videos | ~0.003 videos | 30 sec (adaptive) |
| **YouTube Channel RSS** | 0-5/day | ~0.003 videos | ~0.002 videos | 5 minutes |
| **Reddit (single sub)** | 100-1000/day | 1-7 posts | 0.3-3.5 posts | 2-5 minutes |
| **Reddit (10 AI subs)** | ~5,000/day | ~35 posts | ~17 posts | 2-5 minutes |
| **Threads (future)** | Unknown | Unknown | Unknown | TBD |

**Key Insights**:

1. **YouTube is low-volume**: Even at 5-minute polling, you'll rarely see more than 1 new video per channel. Batching is fine.

2. **Reddit is high-volume**: Popular AI subs (r/MachineLearning, r/artificial, r/LocalLLaMA) can easily generate 50+ posts in 10 minutes during peak hours.

3. **Polling frequency matters for high-volume sources**:
   - 10-minute polling → 35 posts per poll → risk of overload
   - 5-minute polling → 17 posts per poll → smoother processing
   - 2-minute polling → 7 posts per poll → ideal but higher API usage

**Recommendation for v1**:
- YouTube RSS: 5-minute polling (sufficient, low volume)
- Reddit (v1.1+): 2-5 minute polling per subreddit, use `high_volume` priority tier

**Streaming vs Batching**:

For high-volume sources like Reddit, consider a **hybrid approach**:

```yaml
sources:
  reddit_subreddit:
    enabled: true
    priority: "high_volume"
    poll_interval_seconds: 180  # 3 minutes
    streaming_mode: true        # Process as stream, not batch
    max_concurrent_events: 10   # Process up to 10 in parallel
```

This allows CSI to:
- Fetch feed
- Stream events to UA as they're processed
- Not be limited by batch_size
- Handle bursts gracefully

---

## 6. Non-Functional Requirements

### 6.1 Performance

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Urgent latency** (playlist) | < 30 seconds (P95) | Event `occurred_at` to UA receipt |
| **Standard latency** (RSS) | < 30 minutes (P95) | Event `occurred_at` to UA receipt |
| Polling overhead | < 5% CPU | Per-adapter CPU usage |
| Memory footprint | < 512 MB RSS | Resident set size |

**Polling Intervals by Priority**:
- **Urgent (playlist)**: 30 seconds polling interval
- **Standard (channel RSS)**: 600 seconds (10 minutes) polling interval

### 6.2 Reliability (SLOs)

| Metric | Target | Error Budget |
|--------|--------|--------------|
| Delivery success rate | 95% | 5% downtime tolerated |
| Duplicate rate at UA | < 5% | 5% dupes tolerated |
| Service uptime | 99% | ~7 hours/month downtime |

### 6.3 Scalability

**NFR-3.1**: CSI SHALL support:
- Up to 100 YouTube playlist watches
- Up to 500 YouTube channel RSS watches
- Up to 50 Reddit subreddit watches (future)
- Up to 10,000 events/day throughput (YouTube only)
- Up to 100,000 events/day throughput (with Reddit)

**NFR-3.2**: CSI SHALL NOT require horizontal scaling for v1 (single-instance VPS)

**NFR-3.3**: For high-volume sources (Reddit, Threads), CSI SHALL:
- Support `high_volume` priority tier with larger batches (500+)
- Support streaming mode to process events without batch limits
- Use configurable polling intervals per source (not global)
- Implement backpressure if UA is overloaded

### 6.4 Security

**NFR-4.1**: All CSI→UA requests SHALL be signed with HMAC-SHA256

**NFR-4.2**: Secrets SHALL NEVER be logged or exposed in error messages

**NFR-4.3**: API keys SHALL be stored as environment variables only

**NFR-4.4**: CSI SHALL validate UA TLS certificates

### 6.5 Observability

**NFR-5.1**: CSI SHALL expose `/healthz` endpoint (always 200 if service is up)

**NFR-5.2**: CSI SHALL expose `/readyz` endpoint (200 if adapters are polling)

**NFR-5.3**: CSI SHALL log structured JSON with fields:
- `level`, `timestamp`, `source`, `event_type`, `event_id`
- `error`, `stack_trace` (on failure)

**NFR-5.4**: CSI SHALL emit metrics:
- `csi_events_total{source, status}`
- `csi_delivery_success_rate`
- `csi_dedupe_hit_rate`
- `csi_adapter_poll_duration_seconds`

### 6.6 Maintainability

**NFR-6.1**: CSI SHALL use SQLite for simplicity (single-file, zero-ops)

**NFR-6.2**: Database schema SHALL support migration to PostgreSQL

**NFR-6.3**: Code SHALL be type-hinted and documented

**NFR-6.4**: Adapters SHALL be testable in isolation

---

## 7. Data Model: Creator Signal Contract v1

### 7.1 Event Schema (Python/Pydantic)

```python
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional
from datetime import datetime

class CreatorSignalEvent(BaseModel):
    """Universal creator signal event contract v1."""

    # Identity
    event_id: str = Field(..., description="Unique event identifier")
    dedupe_key: str = Field(..., description="Deduplication key")
    source: str = Field(..., description="Source adapter name")
    event_type: str = Field(..., description="Event type identifier")

    # Timing
    occurred_at: str = Field(..., description="ISO 8601 UTC when event happened")
    received_at: str = Field(..., description="ISO 8601 UTC when CSI ingested")
    emitted_at: Optional[str] = Field(None, description="ISO 8601 UTC when sent to UA")

    # Subject (platform-specific)
    subject: Dict[str, Any] = Field(..., description="Platform-specific event data")

    # Routing
    routing: Dict[str, Any] = Field(..., description="Routing hints for UA")

    # Metadata
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Event metadata")

    # Optional raw reference
    raw_ref: Optional[str] = Field(None, description="Reference to raw payload")

    class Config:
        json_schema_extra = {
            "example": {
                "event_id": "src:yt:playlist:dQw4w9WgXcQ:PLabc:1708600000",
                "dedupe_key": "youtube:video:dQw4w9WgXcQ:PLabc",
                "source": "youtube_playlist",
                "event_type": "video_added_to_playlist",
                "occurred_at": "2026-02-22T12:00:00Z",
                "received_at": "2026-02-22T12:05:23Z",
                "emitted_at": "2026-02-22T12:05:25Z",
                "subject": {
                    "platform": "youtube",
                    "video_id": "dQw4w9WgXcQ",
                    "channel_id": "UCxxxxxxxxxxxxxx",
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "title": "Never Gonna Give You Up",
                    "description": "Rick Astley official video",
                    "duration_seconds": 212,
                    "published_at": "2009-10-25T08:57:31Z",
                    "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/default.jpg",
                    "playlist_id": "PLabc123",
                    "playlist_position": 25
                },
                "routing": {
                    "pipeline": "youtube_tutorial_explainer",
                    "priority": "normal",
                    "tags": ["ai", "tutorial", "watchlist"]
                },
                "metadata": {
                    "source_adapter": "youtube_playlist_api_v1",
                    "source_ref": "PLabc123",
                    "quality_score": 1.0
                }
            }
        }
```

### 7.2 YouTube-Specific Subject Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `platform` | string | Yes | Always `"youtube"` |
| `video_id` | string | Yes | YouTube video ID |
| `channel_id` | string | Yes | YouTube channel ID |
| `url` | string | Yes | Full watch URL |
| `title` | string | Yes | Video title |
| `description` | string | No | Video description |
| `duration_seconds` | int | No | Video duration |
| `published_at` | string | Yes | ISO 8601 publish time |
| `thumbnail_url` | string | No | Thumbnail URL |
| `playlist_id` | string | No | For playlist events |
| `playlist_position` | int | No | Position in playlist |

### 7.3 Event Types

| Event Type | Source | Trigger | Dedupe Key Pattern |
|------------|--------|---------|-------------------|
| `video_added_to_playlist` | YouTube Playlist API | New videoId in playlistItems.list | `youtube:video:{video_id}:{playlist_id}` |
| `channel_new_upload` | YouTube Channel RSS | New entry in RSS feed | `youtube:video:{video_id}` |
| `channel_new_upload_api` | YouTube Data API | New video in search results | `youtube:video:{video_id}` |

### 7.4 Versioning Strategy

**FC-4.1**: Contract version SHALL be in event envelope: `"contract_version": "1.0"`

**FC-4.2**: Field additions SHALL be optional (backward compatible)

**FC-4.3**: Field removals REQUIRE contract version bump

---

## 8. API and Integration Contracts

### 8.1 CSI → UA Ingest API

**Endpoint**: `POST /api/v1/signals/ingest`

UA implementation guidance for v1:

1. Add route wiring in `src/universal_agent/gateway_server.py`.
2. Keep request validation/auth/business logic in `src/universal_agent/signals_ingest.py`.
3. Use dedicated CSI-specific auth env vars and do not reuse existing hook/composio secrets.

Signature format lock for v1:

1. Header format: `X-CSI-Signature: sha256=<hmac_hex>`
2. Signing string: `<timestamp>.<request_id>.<canonical_json_body>`
3. Canonical JSON: `json.dumps(body, separators=(',', ':'), sort_keys=True)`

**Request**:
```http
POST /api/v1/signals/ingest HTTP/1.1
Host: ua-gateway.example.com
Authorization: Bearer CSI_SHARED_SECRET
Content-Type: application/json
X-CSI-Signature: sha256=<hmac_hex>
X-CSI-Timestamp: 1708600000
X-CSI-Request-ID: req_abc123

{
  "csi_version": "1.0.0",
  "csi_instance_id": "csi-vps-01",
  "batch_id": "batch_20260222_120000",
  "events": [
    <CreatorSignalEvent>,
    ...
  ]
}
```

**Request Envelope Schema**:
```python
class CSIIngestRequest(BaseModel):
    csi_version: str = Field(..., description="CSI version")
    csi_instance_id: str = Field(..., description="CSI instance identifier")
    batch_id: str = Field(..., description="Batch identifier for tracing")
    events: List[CreatorSignalEvent] = Field(..., max_length=100)
```

**Signature Calculation** (Python reference):
```python
import hmac
import hashlib
import time
import json

def generate_signature(shared_secret: str, request_id: str, body: dict) -> tuple[str, str]:
    timestamp = str(int(time.time()))
    body_json = json.dumps(body, separators=(',', ':'), sort_keys=True)
    signing_string = f"{timestamp}.{request_id}.{body_json}"
    signature = hmac.new(
        shared_secret.encode('utf-8'),
        signing_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature, timestamp
```

**Response** (Success):
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "ok": true,
  "accepted": 10,
  "rejected": 0,
  "errors": []
}
```

**Response** (Partial Failure):
```http
HTTP/1.1 207 Multi-Status
Content-Type: application/json

{
  "ok": false,
  "accepted": 8,
  "rejected": 2,
  "errors": [
    {"index": 3, "error": "invalid_schema"},
    {"index": 7, "error": "duplicate_event"}
  ]
}
```

**Response** (CSI Should Retry):
```http
HTTP/1.1 503 Service Unavailable
Retry-After: 60

{
  "ok": false,
  "error": "ua_overloaded",
  "retry_after_seconds": 60
}
```

### 8.2 Retry Semantics

| HTTP Status | Action |
|-------------|--------|
| 2xx | Success, mark delivered |
| 400, 401, 403 | Permanent failure, move to DLQ |
| 404 | Permanent failure, move to DLQ |
| 409 (duplicate) | Success, deduped, mark delivered |
| 429 | Retry after `Retry-After` |
| 5xx | Retry with backoff |

**Backoff Schedule**:
- Attempt 1: Immediate
- Attempt 2: 20 seconds ± jitter
- Attempt 3: 60 seconds ± jitter
- After 3 failures: Move to DLQ

---

## 9. Source Adapter Specifications

### 9.1 YouTube Playlist Adapter

**Purpose**: Detect new videos added to configured playlists

**API**: YouTube Data API v3 `playlistItems.list`

**Configuration**:
```yaml
sources:
  youtube_playlist:
    enabled: true
    poll_interval_seconds: 30   # Urgent: < 30 sec latency (when active)
    adaptive_polling:
      enabled: true
      active_interval_seconds: 30      # When recent activity detected
      idle_interval_seconds: 300       # When no activity for threshold
      activity_threshold_minutes: 15   # Consider idle after 15 min no new videos
      min_quota_buffer: 1000           # Reserve quota for other sources
    priority: "urgent"           # Fast-track processing
    quota_limit: 10000
    api_key_env: "YOUTUBE_API_KEY"
    playlists:
      - id: "PLxxx"
        name: "AI Tutorial Playlist"
        route_profile: "youtube_explainer"
```

**Adaptive Polling Behavior**:

When `adaptive_polling.enabled: true`, the adapter SHALL:

1. **Track activity**: Store timestamp of last new video per playlist
2. **Calculate state**: If `now - last_new_video > activity_threshold`, switch to idle
3. **Adjust interval**:
   - **Active state**: Poll every `active_interval_seconds` (30s)
   - **Idle state**: Poll every `idle_interval_seconds` (300s)
4. **Respect quota**: If remaining quota < `min_quota_buffer`, force idle
5. **Auto-activate**: Switch back to active when new video detected

**Quota Impact Calculation**:

| State | Interval | Calls/Day (per playlist) | 10 Playlists |
|-------|----------|-------------------------|--------------|
| Active | 30s | 2,880 | 28,800 (exceeds free tier) |
| Idle | 300s | 288 | 2,880 (safe) |
| Mixed (50% active) | - | ~1,584 | ~15,840 (exceeds) |

**Recommendation for v1**:
- Use adaptive polling with `activity_threshold_minutes: 15`
- Monitor quota usage via metrics
- If regularly exceeding quota, reduce playlists or increase idle interval

**Future Enhancement: Composio Webhook Integration** (v1.1+)

Composio provides managed YouTube webhook triggers that eliminate polling overhead entirely.

**Benefits**:
- Zero quota consumption (webhooks push to us)
- True real-time detection (< 5 sec latency)
- No adaptive polling logic needed

**Blockers** (to be resolved):
- [ ] Composio webhook signature verification with UA hooks_service.py
- [ ] Mapping Composio payload to `CreatorSignalEvent` v1
- [ ] Failover from webhook → polling if webhook misses events

**Implementation Plan for v1.1**:
1. Enable Composio webhook as primary source for playlists
2. Keep API polling as fallback/reconciliation
3. Detect duplicate events via dedupe_key (both sources emit same key)

**Implementation Details**:
```python
import aiohttp
from typing import List

class YouTubePlaylistAdapter(SourceAdapter):
    def __init__(self, config: dict):
        self.api_key = os.getenv(config["api_key_env"])
        self.playlists = config["playlists"]
        self.poll_interval = config["poll_interval_seconds"]

    async def fetch_events(self) -> List[RawEvent]:
        """Fetch new videos from configured playlists."""
        events = []
        async with aiohttp.ClientSession() as session:
            for playlist in self.playlists:
                url = (
                    f"https://www.googleapis.com/youtube/v3/playlistItems"
                    f"?part=snippet,contentDetails"
                    f"&playlistId={playlist['id']}"
                    f"&maxResults=50"
                    f"&key={self.api_key}"
                )
                async with session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    for item in data.get("items", []):
                        # Check if we've seen this video_id
                        video_id = item["snippet"]["resourceId"]["videoId"]
                        if await self._is_seen(video_id, playlist["id"]):
                            continue
                        events.append(RawEvent(
                            source="youtube_playlist",
                            event_type="video_added_to_playlist",
                            payload=item,
                            occurred_at=item["snippet"]["publishedAt"]
                        ))
        return events

    def normalize(self, raw: RawEvent) -> CreatorSignalEvent:
        """Transform to CreatorSignalEvent v1."""
        snippet = raw.payload["snippet"]
        content = raw.payload.get("contentDetails", {})
        return CreatorSignalEvent(
            event_id=f"src:yt:playlist:{snippet['resourceId']['videoId']}:{raw.payload.get('playlist_id', 'unknown')}:{int(datetime.now().timestamp())}",
            dedupe_key=f"youtube:video:{snippet['resourceId']['videoId']}:{raw.payload.get('playlist_id', 'unknown')}",
            source="youtube_playlist",
            event_type="video_added_to_playlist",
            occurred_at=raw.occurred_at,
            received_at=datetime.utcnow().isoformat() + "Z",
            subject={
                "platform": "youtube",
                "video_id": snippet["resourceId"]["videoId"],
                "channel_id": snippet["channelId"],
                "url": f"https://www.youtube.com/watch?v={snippet['resourceId']['videoId']}",
                "title": snippet["title"],
                "description": snippet.get("description", ""),
                "thumbnail_url": snippet.get("thumbnails", {}).get("default", {}).get("url"),
                "playlist_id": raw.payload.get("playlist_id"),
                "playlist_position": snippet.get("position"),
            },
            routing={
                "pipeline": self._get_route_profile(raw.payload),
                "priority": "normal",
                "tags": ["youtube", "playlist"]
            },
            metadata={
                "source_adapter": "youtube_playlist_api_v1",
                "source_ref": raw.payload.get("playlist_id")
            }
        )

    def get_dedupe_key(self, event: CreatorSignalEvent) -> str:
        return event.dedupe_key
```

### 9.2 YouTube Channel RSS Adapter

**Purpose**: Detect new uploads from watchlist channels

**Feed Format**: YouTube XML RSS

**Configuration**:
```yaml
sources:
  youtube_channel_rss:
    enabled: true
    poll_interval_seconds: 300  # 5 minutes (sufficient for low-volume YouTube uploads)
    watchlist:
      - channel_id: "UCxxx"
        creator_name: "Example Creator"
        route_profile: "channel_watchlist"
```

**Implementation Details**:
```python
import feedparser
from typing import List

class YouTubeChannelRSSAdapter(SourceAdapter):
    RSS_URL_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

    async def fetch_events(self) -> List[RawEvent]:
        """Fetch new videos from RSS feeds."""
        events = []
        for channel in self.config["watchlist"]:
            url = self.RSS_URL_TEMPLATE.format(channel_id=channel["channel_id"])
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    content = await resp.text()
                    feed = feedparser.parse(content)
                    for entry in feed.entries:
                        # Extract video_id from yt:videoId
                        video_id = entry.get("yt_videoid") or entry.get("id")
                        if not video_id or await self._is_seen(video_id):
                            continue
                        events.append(RawEvent(
                            source="youtube_channel_rss",
                            event_type="channel_new_upload",
                            payload={
                                "entry": entry,
                                "channel_id": channel["channel_id"]
                            },
                            occurred_at=entry.get("published")
                        ))
        return events

    def normalize(self, raw: RawEvent) -> CreatorSignalEvent:
        """Transform RSS entry to CreatorSignalEvent v1."""
        entry = raw.payload["entry"]
        video_id = entry.get("yt_videoid")
        return CreatorSignalEvent(
            event_id=f"src:yt:rss:{video_id}:{int(datetime.now().timestamp())}",
            dedupe_key=f"youtube:video:{video_id}",
            source="youtube_channel_rss",
            event_type="channel_new_upload",
            occurred_at=raw.occurred_at,
            received_at=datetime.utcnow().isoformat() + "Z",
            subject={
                "platform": "youtube",
                "video_id": video_id,
                "channel_id": raw.payload["channel_id"],
                "url": entry.link,
                "title": entry.title,
                "description": entry.get("description", ""),
                "published_at": raw.occurred_at,
            },
            routing={
                "pipeline": self._get_route_profile(raw.payload),
                "priority": "low",
                "tags": ["watchlist", "notification"]
            },
            metadata={
                "source_adapter": "youtube_channel_rss_v1",
                "source_ref": raw.payload["channel_id"]
            }
        )
```

---

## 10. Testing Strategy

### 10.1 Unit Tests

```python
import pytest
from csi.ingester.event import CreatorSignalEvent

def test_creator_signal_event_validation():
    """Valid event should parse correctly."""
    event = CreatorSignalEvent(
        event_id="test:123",
        dedupe_key="youtube:video:abc123",
        source="youtube_playlist",
        event_type="video_added_to_playlist",
        occurred_at="2026-02-22T12:00:00Z",
        received_at="2026-02-22T12:05:00Z",
        subject={"platform": "youtube", "video_id": "abc123"},
        routing={"pipeline": "test", "priority": "normal", "tags": []}
    )
    assert event.source == "youtube_playlist"

def test_dedupe_key_generation():
    """Dedupe keys should be consistent."""
    from csi.adapters.youtube_playlist import YouTubePlaylistAdapter

    adapter = YouTubePlaylistAdapter(config={})
    event = CreatorSignalEvent(
        event_id="test",
        dedupe_key="youtube:video:abc123:PLxyz",
        source="youtube_playlist",
        event_type="video_added_to_playlist",
        occurred_at="2026-02-22T12:00:00Z",
        received_at="2026-02-22T12:05:00Z",
        subject={"platform": "youtube", "video_id": "abc123"},
        routing={"pipeline": "test", "priority": "normal", "tags": []}
    )
    assert adapter.get_dedupe_key(event) == "youtube:video:abc123:PLxyz"
```

### 10.2 Contract Tests

```python
def test_csi_to_ua_contract():
    """CSI events should match UA's expected schema."""
    event = CreatorSignalEvent(**SAMPLE_EVENT)
    payload = CSIIngestRequest(
        csi_version="1.0.0",
        csi_instance_id="test",
        batch_id="test-batch",
        events=[event]
    )
    # Validate against UA's expected schema
    assert payload.events[0].event_id is not None
    assert payload.events[0].dedupe_key is not None
    assert payload.events[0].subject.get("platform") == "youtube"
```

### 10.3 Integration Tests

```python
import pytest
from httpx import ASGITransport, AsyncClient

@pytest.mark.asyncio
async def test_youtube_playlist_to_ua_flow():
    """End-to-end: YouTube playlist → CSI → UA."""
    from csi.main import app

    # Start CSI service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Trigger playlist poll
        response = await client.post("/admin/adapters/youtube_playlist/poll")
        assert response.status_code == 200

        # Check event was stored
        events_response = await client.get("/admin/events")
        assert len(events_response.json()["events"]) > 0
```

### 10.4 Failure Injection Tests

| Scenario | Test | Expected Behavior |
|----------|------|-------------------|
| UA unavailable (503) | Mock UA returning 503 | Retry 3x, then DLQ |
| UA returns 400 | Mock UA returning 400 | No retry, DLQ immediately |
| Duplicate event | Send same event twice | Second one deduped |
| Malformed payload | Invalid schema | Log error, DLQ |
| Network timeout | Mock timeout | Retry with backoff |

---

## 11. Operational Requirements

### 11.1 Deployment Model

**OR-1.1**: CSI SHALL run as systemd service on VPS

**OR-1.2**: Directory structure:
```
/opt/csi-ingester/
├── venv/                    # Python virtual environment
├── config.yaml              # Service config
├──csi.db                   # SQLite database
└── logs/                    # Log files

/etc/csi/
└── config.yaml              # Config symlink

/lib/systemd/system/
└── csi-ingester.service     # systemd unit
```

**systemd unit file**:
```ini
[Unit]
Description=CSI Ingester Service
After=network.target

[Service]
Type=simple
User=csi
Group=csi
WorkingDirectory=/opt/csi-ingester
Environment="PATH=/opt/csi-ingester/venv/bin"
Environment="CSI_UA_ENDPOINT=http://localhost:8000/api/v1/signals/ingest"
Environment="CSI_UA_SHARED_SECRET=%h"
EnvironmentFile=/etc/csi/env
ExecStart=/opt/csi-ingester/venv/bin/python -m csi.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 11.2 Health Endpoints

**GET /healthz**
```http
HTTP/1.1 200 OK
{"status": "healthy", "timestamp": "2026-02-22T12:00:00Z"}
```

**GET /readyz**
```http
HTTP/1.1 200 OK
{
  "status": "ready",
  "adapters": {
    "youtube_playlist": {"polling": true, "last_poll": "2026-02-22T12:00:00Z"},
    "youtube_channel_rss": {"polling": true, "last_poll": "2026-02-22T11:55:00Z"}
  },
  "delivery": {
    "ua_reachable": true,
    "last_success": "2026-02-22T11:58:00Z"
  }
}
```

### 11.3 DLQ CLI Tool

```bash
# Inspect DLQ
csi dlq list

# Replay specific event
csi dlq replay --id 123

# Replay all from last hour
csi dlq replay --since "1 hour ago"

# Clear old DLQ entries
csi dlq prune --older-than 90d
```

### 11.4 Monitoring Metrics

**Prometheus text format at /metrics**:
```
# HELP csi_events_total Total events processed
# TYPE csi_events_total counter
csi_events_total{source="youtube_playlist",status="delivered"} 1234
csi_events_total{source="youtube_playlist",status="dlq"} 5

# HELP csi_delivery_success_rate Delivery success rate
# TYPE csi_delivery_success_rate gauge
csi_delivery_success_rate 0.98

# HELP csi_dedupe_hit_rate Dedupe hit rate
# TYPE csi_dedupe_hit_rate gauge
csi_dedupe_hit_rate 0.15

# HELP csi_adapter_poll_duration_seconds Adapter poll duration
# TYPE csi_adapter_poll_duration_seconds histogram
csi_adapter_poll_duration_seconds_bucket{source="youtube_playlist",le="0.1"} 100
csi_adapter_poll_duration_seconds_bucket{source="youtube_playlist",le="1.0"} 500
csi_adapter_poll_duration_seconds_bucket{source="youtube_playlist",le="+Inf"} 502
csi_adapter_poll_duration_seconds_sum{source="youtube_playlist"} 250.5
csi_adapter_poll_duration_seconds_count{source="youtube_playlist"} 502
```

### 11.5 Runbook Templates

| Failure Mode | Detection | Resolution |
|--------------|-----------|------------|
| High duplicate rate (>5%) | Metrics alert | Check dedupe table for TTL issues: `sqlite3 csi.db "SELECT COUNT(*) FROM dedupe_keys WHERE expires_at < datetime('now')"` |
| Delivery failures (>5%) | Metrics alert | Check UA health: `curl http://ua:8000/healthz` |
| Adapter not polling | `/readyz` fails | Check source auth, rate limits: `journalctl -u csi-ingester | tail -100` |
| Disk space >90% | System alert | Prune old events: `sqlite3 csi.db "DELETE FROM events WHERE created_at < datetime('now', '-30 days')"` |

### 11.6 Alerting

**Critical Alerts** (immediate action):
- CSI service down (systemd failed)
- Delivery success rate < 90% (1 hour window)

**Warning Alerts** (investigate within 24h):
- Duplicate rate > 5%
- Adapter polling errors > 10%
- DLQ size > 100 events

---

## 12. Phased Implementation Plan

### Phase 0: Foundation (Week 1-2)

**Deliverables**:
- [ ] `CreatorSignalEvent` Pydantic model
- [ ] SQLite schema with migrations
- [ ] CSI→UA signature implementation
- [ ] Service skeleton (FastAPI + health endpoints)
- [ ] Config loader (YAML)

**Files to create**:
```
csi/ingester/
├── __init__.py
├── event.py          # CreatorSignalEvent model
├── schema.py         # SQLite schema
├── signature.py      # HMAC signature logic
├── config.py         # Config loader
└── models.py         # SQLAlchemy models

csi/main.py            # FastAPI app
csi/config.yaml.example
```

### Phase 1: YouTube Adapters (Week 3-4)

**Deliverables**:
- [ ] `YouTubePlaylistAdapter` implementation
- [ ] `YouTubeChannelRSSAdapter` implementation
- [ ] Event store with dedupe
- [ ] Scheduler for polling

**Files to create**:
```
csi/adapters/
├── __init__.py
├── base.py           # SourceAdapter ABC
├── youtube_playlist.py
└── youtube_channel_rss.py

csi/store/
├── __init__.py
├── events.py         # Event CRUD
└── dedupe.py         # Dedupe logic

csi/scheduler.py      # Polling scheduler
```

### Phase 1.5: Emitter & DLQ (Week 4-5)

**Deliverables**:
- [ ] HTTP emitter to UA
- [ ] Retry logic with backoff
- [ ] DLQ storage and handling
- [ ] CLI tool for DLQ replay

**Files to create**:
```
csi/emitter/
├── __init__.py
├── http.py           # HTTP delivery
└── retry.py          # Retry logic

csi/dlq/
├── __init__.py
├── store.py          # DLQ persistence
└── replay.py         # Replay logic

cli/
├── __init__.py
└── dlq.py            # CLI tool
```

### Phase 2: Migration (Week 5)

**Deliverables**:
- [ ] Migration script from existing pollers
- [ ] Integration testing with UA
- [ ] systemd service files
- [ ] Deployment documentation

**Files to create**:
```
scripts/
└── migrate_to_csi.sh # Migration script

deployment/
└── systemd/
    ├── csi-ingester.service
    └── csi-ingester.env.example
```

### Phase 3: Operations (Week 6)

**Deliverables**:
- [ ] Metrics endpoint (Prometheus)
- [ ] Structured logging
- [ ] Runbook documentation
- [ ] DLQ CLI tool

---

### v1.1: Composio Webhook Integration (Future)

**Blockers to Resolve**:
- [ ] Composio webhook signature verification with UA hooks_service.py
- [ ] Mapping Composio payload to `CreatorSignalEvent` v1
- [ ] Failover from webhook → polling if webhook misses events

**Deliverables**:
- [ ] Composio webhook receiver in CSI
- [ ] Webhook → Contract v1 normalizer
- [ ] Hybrid mode: webhook primary, polling fallback
- [ ] Reconciliation logic (poll to catch missed webhooks)

**Benefits**:
- Zero API quota consumption
- True real-time detection (< 5 sec)
- Simpler architecture (no adaptive polling)

**Deliverables**:
- [ ] Metrics endpoint (Prometheus)
- [ ] Structured logging
- [ ] Runbook documentation
- [ ] DLQ CLI tool

---

## 13. Success Criteria (v1 Launch)

CSI v1 is considered "launch-ready" when:

1. [ ] **Functional**: Both YouTube adapters (playlist + RSS) working
2. [ ] **Reliability**: 95%+ delivery success over 7-day window
3. [ ] **Quality**: < 5% duplicate rate at UA boundary
4. [ ] **Urgent Performance** (playlist): P95 latency < 30 seconds
5. [ ] **Standard Performance** (channel RSS): P95 latency < 30 minutes
6. [ ] **Operations**: Health checks, metrics, structured logs working
7. [ ] **Testing**: All contract tests passing
8. [ ] **Documentation**: Runbooks, config examples, API docs complete

---

## 14. Appendix A: Example Payloads

### A.1 YouTube Playlist Event

```json
{
  "event_id": "src:yt:playlist:dQw4w9WgXcQ:PLabc:1708600000",
  "dedupe_key": "youtube:video:dQw4w9WgXcQ:PLabc",
  "source": "youtube_playlist",
  "event_type": "video_added_to_playlist",
  "occurred_at": "2026-02-22T12:00:00Z",
  "received_at": "2026-02-22T12:05:23Z",
  "emitted_at": "2026-02-22T12:05:25Z",
  "subject": {
    "platform": "youtube",
    "video_id": "dQw4w9WgXcQ",
    "channel_id": "UCxxxxxxxxxxxxxx",
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "title": "Never Gonna Give You Up",
    "description": "Rick Astley official video",
    "duration_seconds": 212,
    "published_at": "2009-10-25T08:57:31Z",
    "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/default.jpg",
    "playlist_id": "PLabc123",
    "playlist_position": 25
  },
  "routing": {
    "pipeline": "youtube_tutorial_explainer",
    "priority": "normal",
    "tags": ["ai", "tutorial", "watchlist"]
  },
  "metadata": {
    "source_adapter": "youtube_playlist_api_v1",
    "source_ref": "PLabc123",
    "quality_score": 1.0
  }
}
```

### A.2 YouTube Channel RSS Event

```json
{
  "event_id": "src:yt:rss:dQw4w9WgXcQ:1708600000",
  "dedupe_key": "youtube:video:dQw4w9WgXcQ",
  "source": "youtube_channel_rss",
  "event_type": "channel_new_upload",
  "occurred_at": "2026-02-22T11:55:00Z",
  "received_at": "2026-02-22T12:10:00Z",
  "emitted_at": "2026-02-22T12:10:02Z",
  "subject": {
    "platform": "youtube",
    "video_id": "dQw4w9WgXcQ",
    "channel_id": "UCxxxxxxxxxxxxxx",
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "title": "NEW: Advanced AI Tutorial",
    "published_at": "2026-02-22T11:55:00Z"
  },
  "routing": {
    "pipeline": "creator_watchlist_handler",
    "priority": "low",
    "tags": ["watchlist", "notification"]
  },
  "metadata": {
    "source_adapter": "youtube_channel_rss_v1",
    "source_ref": "UCxxxxxxxxxxxxxx"
  }
}
```

---

## 15. Appendix B: Migration Plan

### B.1 From Current Scripts to CSI

| Current Script | CSI Component |
|----------------|---------------|
| `youtube_playlist_poll_to_manual_hook.py` | YouTube Playlist Adapter |
| `run_youtube_playlist_poller.sh` | CSI Scheduler |
| `systemd/universal-agent-youtube-playlist-poller.timer` | CSI internal scheduler |
| `systemd/universal-agent-youtube-playlist-poller.service` | CSI systemd service |
| `composio_youtube_transform.py` | UA Consumer (retained) |

### B.2 Cutover Steps

1. Deploy CSI alongside existing scripts (parallel run)
2. Configure CSI to emit events to UA
3. Validate CSI events match script events
4. Stop old poller scripts
5. Remove old systemd units
6. Update documentation

```bash
# Parallel run validation
systemctl start csi-ingester
# Monitor for 24 hours, compare event counts

# Cutover
systemctl stop universal-agent-youtube-playlist-poller.timer
systemctl disable universal-agent-youtube-playlist-poller.timer
systemctl stop universal-agent-youtube-playlist-poller.service
```

### B.3 Rollback Plan

If CSI v1 has critical issues:
1. Stop CSI: `systemctl stop csi-ingester`
2. Start old scripts: `systemctl start universal-agent-youtube-playlist-poller.timer`
3. Investigate CSI logs: `journalctl -u csi-ingester -n 500`
4. Fix and redeploy CSI

---

## 16. Appendix C: Environment Variables

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `CSI_UA_ENDPOINT` | Yes | UA ingest endpoint | `http://localhost:8000/api/v1/signals/ingest` |
| `CSI_UA_SHARED_SECRET` | Yes | Shared secret for HMAC | `your-secret-here` |
| `UA_SIGNALS_INGEST_ENABLED` | No | Enable CSI ingest endpoint in UA (`1`/`0`) | `1` |
| `UA_SIGNALS_INGEST_SHARED_SECRET` | Yes (UA) | UA-side secret for CSI HMAC verification | `your-secret-here` |
| `UA_SIGNALS_INGEST_ALLOWED_INSTANCES` | No | Optional CSV allowlist of CSI instance IDs | `csi-vps-01` |
| `YOUTUBE_API_KEY` | Yes | YouTube Data API key | `AIzaSy...` |
| `CSI_CONFIG_PATH` | No | Config file path | `/etc/csi/config.yaml` |
| `CSI_DB_PATH` | No | SQLite database path | `/var/lib/csi/csi.db` |
| `CSI_LOG_LEVEL` | No | Log level | `INFO` |
| `CSI_INSTANCE_ID` | No | Instance identifier | `csi-vps-01` |

---

**End of PRD**

For questions or clarifications during implementation, refer to:
- `01_Document_01_CSI_Strategy_2026-02-22.md` - Architectural decisions
- `02_Context_For_PRD_CSI_Ingester_And_UA_Consumer_2026-02-22.md` - Original handoff context

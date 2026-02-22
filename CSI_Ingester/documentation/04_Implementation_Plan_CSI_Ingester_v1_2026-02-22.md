# Document 04. CSI Ingester v1 Implementation Plan (2026-02-22)

Source references:

1. `CSI_Ingester/documentation/01_Document_01_CSI_Strategy_2026-02-22.md`
2. `CSI_Ingester/documentation/02_Context_For_PRD_CSI_Ingester_And_UA_Consumer_2026-02-22.md`
3. `CSI_Ingester/documentation/03_PRD_CSI_Ingester_v1_2026-02-22.md`

## 1. Plan Objective

Deliver `CSI Ingester v1` in phased increments with production-safe rollout:

1. Standalone ingestion service for YouTube playlist + channel RSS.
2. Contract-based delivery to UA (`POST /api/v1/signals/ingest`).
3. Durable event log + dedupe + DLQ + replay tooling.
4. Operational readiness (systemd, health, metrics, runbook).

## 1.1 Scope Lock (Confirmed 2026-02-22)

The following scope decisions are finalized for v1:

1. CSI will be built as an isolated Python project under `CSI_Ingester/development`.
2. UA ingest endpoint (`POST /api/v1/signals/ingest`) will be implemented as part of v1.
3. Reddit is deferred from v1 implementation and remains post-v1 scope.
4. UA route wiring will be added in `src/universal_agent/gateway_server.py`, while request handling/validation logic lives in a dedicated module (`src/universal_agent/signals_ingest.py`).
5. CSI->UA auth will use dedicated secrets/env vars and remain separate from existing hook secrets (`UA_HOOKS_TOKEN`, `COMPOSIO_WEBHOOK_SECRET`).

## 2. Execution Model

Use a two-track build to reduce rework:

1. Track A (`CSI Service`): build ingester, adapters, store, emitter.
2. Track B (`UA Consumer Boundary`): add/validate ingest endpoint, auth contract, routing handoff.

Both tracks must converge before full end-to-end validation.

## 3. Repo Layout For Implementation

Implement CSI in the new project directory created for this work:

1. `CSI_Ingester/development/`
   1. `csi_ingester/` (runtime package)
   2. `tests/` (CSI-specific tests)
   3. `config/` (example configs)
   4. `scripts/` (ops/migration scripts)
2. `CSI_Ingester/testing/`
   1. test plans, soak reports, rollout verification artifacts

Proposed package skeleton:

```text
CSI_Ingester/development/
├── pyproject.toml
├── csi_ingester/
│   ├── __init__.py
│   ├── app.py
│   ├── config.py
│   ├── contract.py
│   ├── signature.py
│   ├── scheduler.py
│   ├── logging.py
│   ├── metrics.py
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── youtube_playlist.py
│   │   └── youtube_channel_rss.py
│   ├── store/
│   │   ├── __init__.py
│   │   ├── sqlite.py
│   │   ├── events.py
│   │   ├── dedupe.py
│   │   └── dlq.py
│   └── emitter/
│       ├── __init__.py
│       ├── ua_client.py
│       └── retry.py
├── scripts/
│   ├── csi_migrate_from_legacy.sh
│   └── csi_replay_dlq.py
├── deployment/
│   └── systemd/
│       ├── csi-ingester.service
│       └── csi-ingester.env.example
└── tests/
    ├── unit/
    ├── contract/
    └── integration/
```

## 4. Milestones And Phases

## Phase 0: Contract And Service Foundation (Week 1)

Goals:

1. Lock event contract and request envelope.
2. Stand up base runtime and persistence schema.

Deliverables:

1. `CreatorSignalEvent` model (`contract.py`).
2. `CSIIngestRequest` envelope model.
3. SQLite schema + migrations.
4. FastAPI service shell with:
   1. `/healthz`
   2. `/readyz`
   3. `/metrics`
5. YAML config loader with env interpolation.
6. HMAC signing utility aligned with the v1 signature lock in Phase 0.5.
7. Sandbox-safe execution helpers:
   1. `scripts/csi_dev_env.sh`
   2. `scripts/csi_run.sh`
   3. `scripts/csi_preflight.sh`

Acceptance gate:

1. Unit tests for schema validation and signature generation pass.
2. Service starts locally and returns healthy status.

## Phase 0.5: UA Boundary Contract Freeze (Week 1)

Goals:

1. Remove integration ambiguity before adapter work scales.

Deliverables:

1. Confirm UA route exists or implement route:
   1. `POST /api/v1/signals/ingest`
   2. Route wiring in `src/universal_agent/gateway_server.py`
   3. Handler logic in `src/universal_agent/signals_ingest.py`
2. UA verifies bearer + HMAC headers.
3. UA response semantics implemented per PRD:
   1. `200`, `207`, `429`, `5xx`
4. Contract test fixture for CSI->UA payloads.
5. Signature format lock for v1:
   1. Header: `X-CSI-Signature: sha256=<hmac_hex>`
   2. Signing string: `<timestamp>.<request_id>.<canonical_json_body>`
   3. Canonical JSON: `json.dumps(body, separators=(',', ':'), sort_keys=True)`
6. Dedicated auth env var lock:
   1. CSI side: `CSI_UA_SHARED_SECRET`
   2. UA side: `UA_SIGNALS_INGEST_SHARED_SECRET`
   3. Optional UA toggle: `UA_SIGNALS_INGEST_ENABLED=1`
   4. Optional UA allowlist: `UA_SIGNALS_INGEST_ALLOWED_INSTANCES`

Acceptance gate:

1. CSI test client can send signed sample payload and receive deterministic response.

## Phase 1: YouTube Playlist Adapter (Week 2)

Goals:

1. Reliable playlist-add detection with quota-safe behavior.

Deliverables:

1. Playlist adapter using `playlistItems.list`.
2. Adaptive polling:
   1. active interval
   2. idle interval
   3. activity threshold
   4. quota buffer guard
3. Event normalization mapping for playlist events.
4. Dedupe key strategy for playlist additions.
5. Persistent adapter source state:
   1. one-time seeding markers
   2. restart-safe seen ID cache
   3. persisted poll cadence state

Acceptance gate:

1. New playlist item triggers exactly one stored normalized event.
2. Duplicate poll cycles do not produce duplicate events.
3. Quota guard shifts to idle mode when threshold reached.
4. Restart does not re-seed repeatedly or miss post-seed additions.

## Phase 2: YouTube Channel RSS Adapter (Week 2-3)

Goals:

1. Low-cost watchlist feed ingestion.

Deliverables:

1. RSS adapter with parser for `yt:videoId`, `yt:channelId`, metadata.
2. Conditional request support (`ETag`, `If-Modified-Since`) when available.
3. Event normalization mapping for channel upload events.
4. Persistent adapter source state:
   1. one-time seeding markers
   2. restart-safe seen ID cache
   3. persisted ETag/Last-Modified cache

Acceptance gate:

1. New channel upload creates normalized event.
2. Existing items are not replayed after initial seed.
3. Restart preserves seed/checkpoint behavior.

## Phase 3: Emitter, Retry, DLQ, Replay CLI (Week 3)

Goals:

1. Reliable delivery to UA with observable failure handling.

Deliverables:

1. UA emitter with signed batched POST requests.
2. Retry/backoff strategy per PRD Section 8.2.
3. DLQ table + writer.
4. Replay CLI with filters (event id, time window, count).

Acceptance gate:

1. Forced UA downtime leads to retries then DLQ entry.
2. Replay CLI successfully re-delivers recovered events.

## Phase 4: End-to-End Integration And Cutover (Week 4)

Goals:

1. Replace legacy YouTube poller path with CSI service.

Deliverables:

1. `csi_migrate_from_legacy.sh` migration script.
2. systemd service and env example files:
   1. `CSI_Ingester/development/deployment/systemd/csi-ingester.service`
   2. `CSI_Ingester/development/deployment/systemd/csi-ingester.env.example`
3. Parallel-run validation report (24h).
   1. generated via `CSI_Ingester/development/scripts/csi_parallel_validate.py`
4. Cutover + rollback runbook.

Acceptance gate:

1. Parallel-run event counts are within agreed tolerance.
2. Legacy timer/service can be disabled with rollback path tested.

## Phase 5: Hardening And Launch Readiness (Week 5)

Goals:

1. Meet PRD SLO targets and operational readiness.

Deliverables:

1. Structured logs with source, event_type, dedupe, retry, delivery status.
2. Prometheus metrics aligned to PRD Section 11.4.
3. Alert rules and thresholds.
4. 7-day soak test report.
5. Sandbox/exception operations profile adopted from:
   1. `CSI_Ingester/documentation/05_CSI_Sandbox_Permissions_And_Exceptions_2026-02-22.md`

Acceptance gate:

1. 95%+ delivery success.
2. <5% duplicate rate.
3. Playlist P95 latency <30s.
4. Channel RSS P95 latency <30m.

## 5. Work Breakdown By Epic

## Epic A: Contract + Persistence

Tasks:

1. Implement contract models.
2. Implement schema migration manager (`schema_migrations` + versioned SQL).
3. Implement event repository and dedupe repository.

Definition of done:

1. Contract tests pass.
2. Event persistence round-trip validated.

## Epic B: Ingestion Adapters

Tasks:

1. Implement adapter base interface.
2. Implement playlist adapter.
3. Implement RSS adapter.
4. Implement adapter scheduler loop.

Definition of done:

1. Both adapters produce valid `CreatorSignalEvent` objects.
2. Adapter failures do not crash service loop.

## Epic C: UA Delivery Pipeline

Tasks:

1. Implement signature and request headers.
2. Implement HTTP emitter.
3. Implement retry policy.
4. Implement DLQ fallback.

Definition of done:

1. Delivery semantics match PRD table.
2. DLQ replay recovers failed events.

## Epic D: Operability

Tasks:

1. Add health/readiness/metrics.
2. Add structured logging.
3. Add deployment units and runbook.

Definition of done:

1. Service can be deployed and monitored without interactive debugging.

## 6. Test Plan By Stage

1. Unit:
   1. contract validation
   2. dedupe behavior
   3. signature verification
   4. polling state transitions
2. Contract:
   1. CSI request body/header compatibility with UA endpoint
   2. retry path semantics by status code
3. Integration:
   1. playlist simulation -> event store -> emitter -> UA stub
   2. rss feed simulation -> normalized event -> UA stub
4. Failure injection:
   1. UA 503/timeout
   2. invalid signature
   3. quota exhaustion branch
   4. malformed source payload
5. Soak:
   1. 24-hour pre-cutover
   2. 7-day launch readiness window

## 7. Key Dependencies And Blockers

1. UA endpoint implementation status:
   1. `POST /api/v1/signals/ingest` is currently specified in PRD but not present in runtime code.
2. UA-side response contract must match PRD semantics (`200/207/429/5xx`) for CSI retry logic correctness.
3. CSI runtime secrets delivery mechanism for production should default to systemd env file in v1.
4. Dedicated CSI auth secrets must be provisioned separately from current hook/composio secrets.

## 8. Risks And Mitigations

1. Risk: API quota burn from aggressive playlist polling.
   1. Mitigation: adaptive polling + quota guard + metrics alarm.
2. Risk: Duplicate events from mixed sources (future Composio + poller).
   1. Mitigation: stable dedupe key at CSI boundary.
3. Risk: UA downtime causing data loss.
   1. Mitigation: retry + DLQ + replay CLI.
4. Risk: Contract drift between CSI and UA.
   1. Mitigation: versioned contract tests in CI.

## 9. Recommended Immediate Start Sequence (Next 3 Execution Days)

1. Day 1:
   1. Scaffold CSI package and tests.
   2. Implement contract + schema + signature.
   3. Add UA stub receiver for local contract tests.
2. Day 2:
   1. Implement playlist adapter with deterministic state/dedupe.
   2. Implement event repository and scheduler.
3. Day 3:
   1. Implement emitter + retry + DLQ.
   2. Run first end-to-end local validation and publish test report.

## 10. Finalized Build Decisions

1. Build CSI as an isolated Python project under `CSI_Ingester/development`.
2. Implement UA ingest endpoint in the same v1 implementation cycle.
3. Keep Reddit deferred from v1 scope.
4. Implement UA route in `src/universal_agent/gateway_server.py` and keep ingest logic in `src/universal_agent/signals_ingest.py`.
5. Use dedicated CSI->UA secrets/env vars separate from existing hook/composio secret paths.

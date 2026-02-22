# Document 02. Context For PRD: CSI Ingester And UA Consumer (2026-02-22)

## 1. Purpose Of This Document

This document is a handoff context package for another AI programmer to produce a full Product Requirements Document (PRD) for the new `CSI Ingester` capability, with clear integration boundaries to `UA Consumer/Analysis`.

Primary focus for the immediate PRD: `CSI Ingester` only.

## 2. Background And Problem Statement

The team wants a durable, reusable way to ingest creator signals and trend signals without building one-off trigger pipelines for each source.

Current trigger goals started from YouTube:

1. If a video is added to a target playlist, automatically trigger the YouTube tutorial explainer workflow.
2. Build a watchlist of creators/channels and detect new uploads quickly.
3. Feed brief summaries and trend signals into daily reporting (for example, 7:00 AM report).

Strategic expansion goals:

1. Reuse the same architecture for Reddit.
2. Reuse the same architecture for Threads.
3. Later add X and broader multi-source trend intelligence.

Core concern from stakeholder:

1. Avoid ad hoc source-by-source systems.
2. Define a scalable boundary now so future source onboarding is mostly adapter work.

## 3. Current State (As Of 2026-02-22)

## 3.1 Existing Universal Agent hook infrastructure

The UA runtime already has a generalized hook ingestion surface:

1. `src/universal_agent/gateway_server.py`
   1. `GET /api/v1/hooks/readyz`
   2. `POST /api/v1/hooks/{subpath:path}`
2. `src/universal_agent/hooks_service.py`
   1. Auth strategies include `token`, `composio_hmac`, and `none`.
   2. Existing mappings include:
      1. `composio-youtube-trigger`
      2. `youtube-manual-url`

## 3.2 Existing YouTube trigger transforms

1. `webhook_transforms/composio_youtube_transform.py`
2. `webhook_transforms/manual_youtube_transform.py`

These already route incoming events toward the YouTube explainer/learning path.

## 3.3 Existing operational scripts and scaffolding

1. `scripts/bootstrap_composio_youtube_hooks.py`
2. `scripts/register_composio_webhook_subscription.py`
3. `scripts/youtube_playlist_poll_to_manual_hook.py`
4. `scripts/run_youtube_playlist_poller.sh`
5. `scripts/install_youtube_playlist_poller_timer.sh`
6. `deployment/systemd/universal-agent-youtube-playlist-poller.service`
7. `deployment/systemd/universal-agent-youtube-playlist-poller.timer`

## 3.4 Validation outcomes from recent troubleshooting

1. Hook path and HMAC verification pipeline can work end-to-end when payload/signature is correct.
2. Playlist RSS reliability is insufficient for core automation in this environment (observed 404 for tested playlist feeds).
3. YouTube playlist automation should rely on API polling rather than playlist RSS.
4. Channel RSS remains useful for low-cost watchlist upload detection.

## 4. Architectural Direction Already Agreed

Use a hybrid architecture with explicit boundary:

1. `CSI Ingester` (separate VPS service/application):
   1. Ingests source data.
   2. Handles source auth, polling/subscription, retries, rate limits.
   3. Normalizes events to common contract.
   4. Emits normalized events to UA.
2. `UA Consumer/Analysis` (inside Universal Agent):
   1. Validates incoming normalized events.
   2. Routes events to skills/agents/reporting.
   3. Performs synthesis and trend reasoning.

Reference strategy document:

1. `CSI_Ingester/documentation/01_Document_01_CSI_Strategy_2026-02-22.md`

## 5. Why PRD Is Needed Now

The next implementation should not start from scripts alone. We need a product-level specification to avoid rework and ensure future adapter scale-out.

The PRD should:

1. Define CSI as a product capability, not a YouTube utility.
2. Lock the contract between Ingestor and UA before coding deeper.
3. Establish reliability, observability, and operability expectations.
4. Make Reddit/Threads onboarding predictable through the same framework.

## 6. Scope Recommendation For PRD (Initial)

In-scope for first PRD iteration:

1. CSI Ingester core service skeleton.
2. Creator Signal Contract v1.
3. CSI-to-UA ingest interface.
4. YouTube adapters:
   1. Playlist API polling adapter.
   2. Channel RSS watchlist adapter.
5. Event dedupe, retries, dead-letter, and metrics.
6. Minimal admin/config model for watchlists and routes.

Out-of-scope for first iteration:

1. Full Reddit implementation.
2. Full Threads implementation.
3. Complex cross-source trend scoring logic.
4. UI-heavy control plane beyond essential ops endpoints.

## 7. CSI Ingester Product Definition (Working Draft)

## 7.1 Product mission

Continuously ingest high-value creator/platform signals from configured sources, normalize them into one event schema, and deliver them reliably to UA for downstream analysis and action.

## 7.2 Core jobs of CSI Ingester

1. Source connectivity and auth lifecycle.
2. Change detection (new content/events).
3. Normalization to contract.
4. Dedupe and idempotent delivery.
5. Retries, backoff, dead-letter handling.
6. Metrics and operational introspection.

## 7.3 What CSI Ingester must not own

1. Long-form reasoning/synthesis.
2. Agent orchestration logic.
3. Skill-specific artifact generation.
4. Report authoring beyond raw signal stats.

Those remain UA responsibilities.

## 8. PRD Elements Required (Checklist For Next AI Programmer)

The PRD should explicitly include the following sections and decisions.

## 8.1 Problem and user outcomes

1. Who uses CSI outputs (UA pipelines, VP General, Simone, daily report systems).
2. What outcomes matter:
   1. timely detection,
   2. reliable delivery,
   3. low duplicate/noise rate,
   4. low operational burden.

## 8.2 Personas and consumers

1. Operator/DevOps managing ingestion service.
2. UA automation pipelines consuming normalized events.
3. Strategy user consuming trend summaries and notifications.

## 8.3 Functional requirements

1. Source adapter framework (plugin-style or module registry).
2. Scheduler/poller framework with per-source cadence control.
3. Signal normalization pipeline.
4. Event store + dedupe key management.
5. Reliable emitter to UA endpoint with signed/authenticated requests.
6. Dead-letter queue and replay tooling.
7. Config management:
   1. watchlists,
   2. route profiles,
   3. source credentials references.

## 8.4 Non-functional requirements

1. Availability target.
2. Ingestion latency targets by source type.
3. Throughput expectations.
4. Durability of event logs and DLQ.
5. Security requirements for secrets and request signing.
6. Observability requirements:
   1. metrics,
   2. structured logs,
   3. health/readiness.

## 8.5 Data contract requirements

1. `Creator Signal Contract v1` schema.
2. Versioning strategy for future fields.
3. Required and optional fields.
4. Dedupe key rules per source/event type.
5. Traceability fields:
   1. source metadata,
   2. event timestamps,
   3. raw payload reference.

## 8.6 API and integration requirements

1. CSI -> UA ingest endpoint contract.
2. Auth model (HMAC token strategy, replay protection, clock tolerance).
3. Response and retry semantics.
4. Backpressure behavior when UA is degraded/unavailable.

## 8.7 Source onboarding template

For every source (YouTube, Reddit, Threads, X):

1. Adapter implementation requirements.
2. Mapping to contract.
3. Source-specific rate and quota policy.
4. Source-specific quality/noise controls.
5. Test plan and acceptance gates.

## 8.8 Testing requirements

1. Unit tests for normalization and dedupe.
2. Contract tests for CSI->UA payload compatibility.
3. Integration tests per adapter.
4. Failure-injection tests:
   1. UA downtime,
   2. source timeouts,
   3. malformed payloads,
   4. duplicate bursts.

## 8.9 Operational requirements

1. Runtime deployment model (systemd container service, etc.).
2. Config and secret rollout process.
3. Runbook requirements for common failure modes.
4. Alert thresholds and escalation paths.

## 8.10 Release and rollout requirements

1. Phased rollout plan:
   1. YouTube first,
   2. Reddit second,
   3. Threads third.
2. Migration strategy from current scripts to managed service.
3. Cutover and rollback plan.

## 9. Suggested CSI PRD Structure (Template)

1. Executive summary.
2. Problem statement and goals.
3. Scope and non-goals.
4. Personas and user journeys.
5. System architecture and boundaries.
6. Functional requirements.
7. Non-functional requirements.
8. Data model and event contract.
9. API and integration contracts.
10. Source adapter framework and onboarding process.
11. Security and compliance.
12. Observability and operations.
13. Testing strategy and acceptance criteria.
14. Phased roadmap and milestones.
15. Risks, assumptions, open questions.
16. Appendices (schema examples, sample payloads, runbooks).

## 10. Key Open Questions The PRD Must Resolve

1. Event persistence backend choice for CSI (SQLite/Postgres/other).
2. Delivery mode to UA:
   1. HTTP push only,
   2. queue plus pull,
   3. hybrid.
3. Exactly-once vs at-least-once handling strategy.
4. Where dedupe source of truth lives (CSI only or shared with UA).
5. How route policies are configured and versioned.
6. Whether to include an operator API for watchlist/rule edits in v1.
7. SLO targets for detection latency and delivery success.

## 11. Interview Guide For The Next AI Planner (Questions To Ask Stakeholder)

The next AI should run a structured interview before finalizing PRD:

1. Which sources are mandatory in first 90 days, and in what exact order?
2. What is the acceptable delay from event occurrence to UA action per source?
3. What is the maximum tolerable duplicate rate?
4. What are hard quota or budget constraints per source?
5. Which actions are mandatory at launch:
   1. explainer trigger,
   2. quick summary,
   3. notification,
   4. daily report insertion?
6. Who is on-call for CSI failures and what alert channels are required?
7. Do we need manual approval gates for certain trigger types?
8. Which event fields are considered required for trust/traceability?
9. What retention policy is needed for raw events, normalized events, and DLQ?
10. Which success KPIs determine v1 launch readiness?

## 12. Implementation Starting Point In This Repository

New project location created for CSI work:

1. `CSI_Ingester/`
   1. `documentation/`
   2. `development/`
   3. `testing/`

Current canonical strategy docs in CSI project:

1. `CSI_Ingester/documentation/01_Document_01_CSI_Strategy_2026-02-22.md`
2. `CSI_Ingester/documentation/02_Context_For_PRD_CSI_Ingester_And_UA_Consumer_2026-02-22.md` (this file)

## 13. Immediate Next Step

Use this file as context input for another AI programmer and request:

1. A full PRD for `CSI Ingester v1` (with explicit UA interface contract).
2. A stakeholder interview flow to close open questions before implementation lock.
3. A milestone-based implementation plan that can be executed incrementally.

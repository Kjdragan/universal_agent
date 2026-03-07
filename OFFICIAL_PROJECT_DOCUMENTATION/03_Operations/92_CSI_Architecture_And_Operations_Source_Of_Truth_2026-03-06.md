# 92. CSI Architecture and Operations Source of Truth (2026-03-06)

## Purpose

This document is the canonical source of truth for the CSI subsystem as it exists today in this repository and on the VPS.

It covers:
- the CSI Ingester runtime and storage model
- CSI source adapters and polling behavior
- signed CSI -> UA delivery and UA-side ingest handling
- the current VPS deployment model for CSI and CSI timer-driven jobs
- the current boundary between CSI responsibilities and responsibilities that have moved into core UA

## Executive Summary

CSI is currently a **separate subsystem** that runs alongside the main Universal Agent stack.

Its core role is:
- collect creator and trend signals from external sources
- normalize and dedupe those signals
- persist them in CSI-local SQLite
- emit them into UA through a signed ingest endpoint
- run a growing set of CSI-native analytics, digest, reporting, and follow-up jobs on timers

The most important architecture rule today is:
- **CSI is no longer the sole owner of tutorial-playlist polling**
- the old CSI `youtube_playlist` source exists in config but is disabled by default
- the active tutorial playlist watcher has been moved into native UA via `services/youtube_playlist_watcher.py`

So CSI remains important, but it is not the entire creator-signal pipeline anymore.

## Current Production Architecture

## 1. CSI Runtime Shape

Primary implementation:
- `CSI_Ingester/development/csi_ingester/app.py`
- `CSI_Ingester/development/csi_ingester/service.py`
- `CSI_Ingester/development/csi_ingester/config.py`
- `CSI_Ingester/development/csi_ingester/scheduler.py`

CSI currently runs as a FastAPI app with its own startup lifecycle.

On startup it:
1. loads config
2. resolves the CSI SQLite database path
3. connects to the DB and ensures schema
4. builds a `CSIService`
5. starts scheduler-driven adapter polling

Current service entrypoint on VPS:
- `uv run uvicorn csi_ingester.app:app --host 127.0.0.1 --port 8091`

This means CSI is a long-running service, not just a collection of cron scripts.

## 2. CSI Storage Model

Primary implementation:
- `CSI_Ingester/development/csi_ingester/config.py`
- `CSI_Ingester/development/csi_ingester/store/`

CSI maintains its own SQLite database.

Current DB resolution:
- `CSI_DB_PATH` if set
- else `storage.db_path` from config
- default local development path: `var/csi.db`

On VPS, the example env uses:
- `/var/lib/universal-agent/csi/csi.db`

CSI stores at least the following categories of state:
- raw/normalized events
- dedupe keys
- delivery attempts
- dead-letter queue entries
- source-state / adapter health state
- analysis tasks and related execution state
- reporting/analytics support data

This is a CSI-local operational data plane, separate from UA runtime state.

## 3. Config Loading and Environment Expansion

Primary implementation:
- `CSI_Ingester/development/csi_ingester/config.py`
- `CSI_Ingester/development/config/config.yaml`

CSI config is YAML-backed with `${ENV_VAR}` expansion.

Current top-level config areas include:
- `csi`
- `storage`
- `sources`
- `delivery`

This allows the subsystem to keep a stable structural config while sourcing secrets and deployment specifics from env.

## 4. Source Adapter Model

Primary implementation:
- `CSI_Ingester/development/csi_ingester/service.py`
- `CSI_Ingester/development/csi_ingester/adapters/`

CSI currently supports multiple adapter families, enabled or disabled through config.

Current sources visible in code/config include:
- `youtube_playlist`
- `youtube_channel_rss`
- `reddit_discovery`
- `threads_owned`
- `threads_trends_seeded`
- `threads_trends_broad`

### Current Enabled/Disabled Defaults in `config.yaml`

Current checked-in defaults are:
- `youtube_playlist`: disabled
- `youtube_channel_rss`: enabled
- `reddit_discovery`: enabled
- `threads_owned`: enabled
- `threads_trends_seeded`: enabled
- `threads_trends_broad`: enabled

### Polling Model

CSI uses a cooperative async scheduler.

Each enabled adapter is registered with a per-source poll interval.

Current scheduler behavior:
- each job runs in its own async task
- failures are logged but do not stop the whole service
- stop behavior cancels all scheduled jobs cleanly

This makes CSI a multi-source polling service rather than a single-source daemon.

## 5. Event Processing Pipeline Inside CSI

Primary implementation:
- `CSI_Ingester/development/csi_ingester/service.py`
- `CSI_Ingester/development/csi_ingester/emitter/ua_client.py`

For each adapter poll cycle, CSI currently:
1. fetches raw events from the source adapter
2. normalizes each event into the CSI contract shape
3. dedupes by dedupe key
4. stores accepted events in CSI SQLite
5. attempts delivery to UA if emitter is configured
6. records delivery attempt history
7. marks delivered events when successful
8. enqueues failed events into CSI DLQ when necessary
9. updates per-adapter health state and totals

### Current Failure Handling

CSI distinguishes:
- transient network/server failures
- permanent client/auth/config failures
- maintenance-mode deferrals
- delivery-disabled conditions

If UA delivery is not configured, CSI does not silently drop the signal.

Instead it records a failed attempt and pushes the event into DLQ with `ua_delivery_not_configured` semantics.

## 6. CSI -> UA Delivery Contract

Primary implementation:
- `CSI_Ingester/development/csi_ingester/emitter/ua_client.py`
- `src/universal_agent/signals_ingest.py`
- `src/universal_agent/gateway_server.py`

CSI delivers to UA over HTTP using a signed JSON batch envelope.

Current CSI delivery envelope fields:
- `csi_version`
- `csi_instance_id`
- `batch_id`
- `events`

Current request auth/signing headers:
- `Authorization: Bearer <shared_secret>`
- `X-CSI-Signature: sha256=<signature>`
- `X-CSI-Timestamp`
- `X-CSI-Request-ID`

Current default VPS endpoint:
- `http://127.0.0.1:8002/api/v1/signals/ingest`

### UA-Side Controls

Current UA env gates include:
- `UA_SIGNALS_INGEST_ENABLED`
- `UA_SIGNALS_INGEST_SHARED_SECRET`
- `UA_SIGNALS_INGEST_ALLOWED_INSTANCES`
- `UA_SIGNALS_INGEST_TIMESTAMP_TOLERANCE_SECONDS`

UA validates:
- feature enablement
- shared secret bearer auth
- request signature
- request timestamp tolerance
- allowed CSI instance ids

## 7. How UA Consumes CSI Events

Primary implementation:
- `src/universal_agent/signals_ingest.py`
- `src/universal_agent/gateway_server.py`

UA currently validates the CSI event contract and then fans valid events into internal UA actions.

### Current CSI -> UA Paths

#### Manual YouTube tutorial dispatch

Current mapping:
- `youtube_playlist` events can map to internal `youtube/manual` hook payloads
- this path builds a manual YouTube tutorial payload for the UA hooks pipeline

#### CSI analytics and analyst events

Current mapping:
- `csi_analytics` and `csi_analyst` sourced events map to internal UA agent actions
- current routes include `csi-trend-analyst` and `data-analyst` depending on event type
- these actions use durable CSI-oriented session keys so UA does not create a lane explosion for each event type

This is an important current design choice:
- CSI is not only forwarding raw content discovery events
- it is also a producer of higher-level analytics events for UA consumption

## 8. Important Boundary: Playlist Polling Moved into UA

Primary implementation:
- `src/universal_agent/services/youtube_playlist_watcher.py`
- checked-in CSI config at `CSI_Ingester/development/config/config.yaml`

The repository explicitly states that native UA playlist watching replaces CSI’s playlist polling source.

Current state:
- CSI config keeps `youtube_playlist` present but disabled
- UA runs `YouTubePlaylistWatcher` natively for `YT_TUTORIALS_PLAYLIST_ID`
- rationale in code: the tutorial pipeline should be self-contained in UA and not depend on the CSI VPS process being alive

This is one of the most important subsystem-boundary truths for current operations.

CSI still matters for creator-signal ingestion and analytics, but tutorial playlist polling is no longer CSI’s canonical responsibility.

## 9. CSI HTTP Surface

Primary implementation:
- `CSI_Ingester/development/csi_ingester/app.py`

Current CSI service exposes at least:
- `GET /healthz`
- `GET /readyz`
- `GET /metrics`
- `GET /webhooks/threads`
- `POST /webhooks/threads`
- `POST /analysis/tasks`
- `GET /analysis/tasks`
- `GET /analysis/tasks/{task_id}`
- `POST /analysis/tasks/{task_id}/cancel`

### Health and Readiness

Current health behavior:
- `/healthz` returns `{"status": "ok"}`
- `/readyz` returns readiness based on config, DB, and service initialization

This is the primary liveness/readiness surface for CSI on VPS.

### Metrics

CSI exposes Prometheus-style metrics via `/metrics`.

### Threads Webhook Surface

Threads webhook support currently exists, but it is feature-gated.

If disabled, `POST /webhooks/threads` returns an ignored status rather than processing payloads.

## 10. VPS Deployment Model

Primary operational references:
- `CSI_Ingester/development/deployment/systemd/csi-ingester.service`
- `CSI_Ingester/development/deployment/systemd/csi-ingester.env.example`
- `CSI_Ingester/documentation/06_CSI_VPS_Deployment_Runbook_v1_2026-02-22.md`
- `scripts/vpsctl.sh`
- `scripts/deploy_vps.sh`

CSI runs on VPS as its own systemd service:
- `csi-ingester.service`

Current service characteristics include:
- working directory under `/opt/universal_agent/CSI_Ingester/development`
- env file under `deployment/systemd/csi-ingester.env`
- loopback bind on port `8091`
- restart always
- explicit memory/task limits in the service unit

CSI is also part of the broader VPS operator tooling:
- `scripts/vpsctl.sh` includes `csi` service aliasing
- broader deploy/status flows reference CSI alongside core UA services

## 11. CSI Timer and Batch-Job Model

Primary implementation:
- `CSI_Ingester/development/deployment/systemd/`
- `CSI_Ingester/development/scripts/csi_install_systemd_extras.sh`
- `CSI_Ingester/development/README.md`

CSI is more than the always-on ingester.

A large amount of CSI functionality is currently delivered through systemd timer jobs.

Current job families include:
- Telegram digests
- semantic enrichment
- trend reports
- insight analyst loops
- category reclassification and quality loops
- analysis task runner/bootstrap
- DLQ replay
- report-product finalization
- daily summaries
- token usage reporting
- delivery health canaries and remediation
- Threads token refresh / rollout verification / publish verification
- DB backup and global brief workflows

This means CSI is operationally a **service + timer fleet**, not only one daemon.

## 12. CSI Telegram and Reporting Surfaces

Primary implementation:
- `CSI_Ingester/development/scripts/csi_rss_telegram_digest.py`
- `CSI_Ingester/development/scripts/csi_reddit_telegram_digest.py`
- `CSI_Ingester/development/scripts/csi_playlist_tutorial_digest.py`
- `CSI_Ingester/development/deployment/systemd/csi-ingester.env.example`
- `CSI_Ingester/development/README.md`

CSI uses Telegram as an outbound notification/digest channel.

Current capabilities include:
- RSS digest delivery
- Reddit digest delivery
- playlist tutorial update delivery
- per-stream chat routing
- per-stream thread/topic routing
- strict stream routing to prevent cross-posting mistakes
- pending tutorial artifact follow-up notifications
- stalled tutorial turn alerts

This is a major part of CSI’s operator-facing output surface.

## 13. Verification and Test Surface

Primary verification references:
- `CSI_Ingester/development/tests/integration/test_csi_to_ua_local_smoke.py`
- `tests/contract/test_csi_ua_contract.py`
- `tests/gateway/test_signals_ingest_endpoint.py`
- `tests/unit/test_signals_ingest.py`
- `CSI_Ingester/documentation/06_CSI_VPS_Deployment_Runbook_v1_2026-02-22.md`
- `CSI_Ingester/development/README.md`

Current verification model includes:
- local signed CSI -> UA smoke test
- UA-side contract validation tests
- gateway ingest endpoint tests
- CSI unit/integration tests
- runbook-driven VPS preflight and cutover validation

The local smoke test specifically verifies:
- shared secret auth
- CSI signature generation/validation
- gateway acceptance of the payload
- internal UA dispatch into `youtube/manual`

## 14. Canonical Environment Controls

CSI service/runtime:
- `CSI_CONFIG_PATH`
- `CSI_DB_PATH`
- `CSI_LOG_LEVEL`
- `CSI_INSTANCE_ID`

CSI -> UA delivery:
- `CSI_UA_ENDPOINT`
- `CSI_UA_SHARED_SECRET`
- `CSI_UA_EMIT_TIMEOUT_SECONDS`
- `CSI_UA_MAINTENANCE_MODE`
- `CSI_UA_MAINTENANCE_FLAG`

UA-side ingest controls:
- `UA_SIGNALS_INGEST_ENABLED`
- `UA_SIGNALS_INGEST_SHARED_SECRET`
- `UA_SIGNALS_INGEST_ALLOWED_INSTANCES`
- `UA_SIGNALS_INGEST_TIMESTAMP_TOLERANCE_SECONDS`

Source/auth controls commonly used by adapters:
- `YOUTUBE_API_KEY`
- `THREADS_APP_ID`
- `THREADS_APP_SECRET`
- `THREADS_USER_ID`
- `THREADS_ACCESS_TOKEN`
- `THREADS_TOKEN_EXPIRES_AT`
- `INFISICAL_CLIENT_ID`
- `INFISICAL_CLIENT_SECRET`
- `INFISICAL_PROJECT_ID`
- `INFISICAL_ENVIRONMENT`
- `INFISICAL_SECRET_PATH`

CSI Telegram/reporting controls:
- `CSI_RSS_TELEGRAM_CHAT_ID`
- `CSI_REDDIT_TELEGRAM_CHAT_ID`
- `CSI_TUTORIAL_TELEGRAM_CHAT_ID`
- `CSI_RSS_TELEGRAM_THREAD_ID`
- `CSI_REDDIT_TELEGRAM_THREAD_ID`
- `CSI_TUTORIAL_TELEGRAM_THREAD_ID`
- `CSI_TELEGRAM_STRICT_STREAM_ROUTING`
- `CSI_REDDIT_TELEGRAM_STRICT_STREAM_ROUTING`
- `CSI_TUTORIAL_TELEGRAM_STRICT_STREAM_ROUTING`
- `CSI_RSS_TELEGRAM_BOT_TOKEN`
- `CSI_REDDIT_TELEGRAM_BOT_TOKEN`
- `CSI_TUTORIAL_TELEGRAM_BOT_TOKEN`

UA playlist watcher boundary controls:
- `YT_TUTORIALS_PLAYLIST_ID`
- `UA_YT_PLAYLIST_WATCHER_ENABLED`
- `YT_TUTORIALS_POLL_INTERVAL_SECONDS`

## What Is Actually Implemented Today

### Implemented and Current

- standalone CSI FastAPI ingester service
- CSI-local SQLite event and delivery state
- adapter-driven multi-source polling
- signed CSI -> UA batch delivery
- UA-side validation and internal dispatch for signals
- CSI timer fleet for analytics, reports, digests, and operational loops
- Threads ingestion and webhook scaffolding behind feature gates

### Current Architectural Split

- CSI owns creator-signal ingestion, enrichment, and many analytics/reporting workflows
- UA owns the native tutorial playlist watcher
- CSI can still emit playlist-related events/notifications, but playlist polling itself is no longer CSI’s core canonical runtime responsibility

### Important Operational Interpretation

CSI is not just “the YouTube playlist ingester.”

It is now a broader signal and analytics subsystem with its own runtime, persistence, reporting, and delivery loops.

## Current Gaps and Cleanup Opportunities

1. **Subsystem boundary drift exists in older docs and assumptions**
   - older material may still imply CSI owns tutorial playlist polling end-to-end
   - current code says that responsibility moved into UA

2. **CSI has grown into a timer fleet**
   - the service itself is straightforward, but the number of auxiliary timers/services is now large enough that operational discoverability and tiering matter

3. **Feature maturity is mixed across sources**
   - some sources and Threads/webhook/publish features are gated or phase-oriented rather than equally production-hardened

4. **CSI operational complexity now spans two systems**
   - successful behavior depends on both CSI health and UA ingest health/config/auth

5. **Secrets and env surface are large**
   - CSI uses a significant number of env variables for adapters, Telegram, Threads, delivery, and maintenance modes

## Source Files That Define Current Truth

Primary CSI runtime:
- `CSI_Ingester/development/csi_ingester/app.py`
- `CSI_Ingester/development/csi_ingester/service.py`
- `CSI_Ingester/development/csi_ingester/config.py`
- `CSI_Ingester/development/csi_ingester/scheduler.py`
- `CSI_Ingester/development/csi_ingester/emitter/ua_client.py`
- `CSI_Ingester/development/config/config.yaml`

Primary deployment/operations:
- `CSI_Ingester/development/deployment/systemd/csi-ingester.service`
- `CSI_Ingester/development/deployment/systemd/csi-ingester.env.example`
- `CSI_Ingester/development/scripts/csi_install_systemd_extras.sh`
- `CSI_Ingester/documentation/06_CSI_VPS_Deployment_Runbook_v1_2026-02-22.md`
- `CSI_Ingester/development/README.md`

UA-side CSI ingest contract:
- `src/universal_agent/signals_ingest.py`
- `src/universal_agent/gateway_server.py`

Important subsystem boundary file:
- `src/universal_agent/services/youtube_playlist_watcher.py`

Verification surface:
- `CSI_Ingester/development/tests/integration/test_csi_to_ua_local_smoke.py`
- `tests/contract/test_csi_ua_contract.py`
- `tests/gateway/test_signals_ingest_endpoint.py`
- `tests/unit/test_signals_ingest.py`

## Bottom Line

The canonical current CSI model is:
- **a standalone creator-signal ingester and analytics subsystem**
- **its own SQLite-backed runtime and timer fleet**
- **signed HTTP delivery into UA’s signals-ingest endpoint**
- **UA-side dispatch of valid CSI events into hooks and analyst lanes**
- **a narrowed subsystem boundary where tutorial playlist polling now lives natively in UA, not CSI**

That is the current implementation truth for CSI on the VPS and in this repository.

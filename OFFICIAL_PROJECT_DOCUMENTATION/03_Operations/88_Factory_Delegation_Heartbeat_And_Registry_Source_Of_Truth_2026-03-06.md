# 88. Factory Delegation, Heartbeat, and Registry Source of Truth (2026-03-06)

## Purpose

This document is the canonical source of truth for the current cross-machine delegation model: how headquarters publishes work, how local factories consume it, how mission results return, how factory presence is tracked, and how operator controls such as pause, resume, and self-update work.

## Executive Summary

The current implementation is a **hybrid delegation architecture**:
- cross-machine transport uses Redis Streams
- local execution uses VP SQLite mission storage and the VP worker loop
- factory liveness is maintained through periodic HQ registration heartbeats
- fleet presence is persisted in a SQLite-backed factory registry on HQ

This means the current factory system is not a remote RPC layer.

It is a **bus + local queue + heartbeat + registry** model.

## Current Production Architecture

## 1. Inbound Delegation Path

Primary implementation:
- `src/universal_agent/delegation/redis_bus.py`
- `src/universal_agent/delegation/redis_vp_bridge.py`
- `src/universal_agent/durable/state.py`
- local VP worker runtime under `src/universal_agent/vp/`

Current flow:
1. HQ publishes a mission envelope to the Redis delegation stream
2. a factory bridge consumes the Redis message
3. the bridge maps mission kind to a VP lane
4. the bridge inserts a mission row into local VP SQLite
5. the existing VP worker loop claims and executes that local mission

This is the central design decision of the current system:
- **Redis is transport**
- **SQLite is execution queue/state**

### Current Mission Routing

Current built-in mission kind routing in the inbound bridge:
- `coding_task` -> `vp.coder.primary`
- `general_task` -> `vp.general.primary`
- `research_task` -> `vp.general.primary`

Certain kinds such as tutorial bootstrap are explicitly skipped because they are handled elsewhere.

## 2. Outbound Result Path

Primary implementation:
- `src/universal_agent/delegation/redis_vp_result_bridge.py`

Current outbound behavior:
- poll local VP SQLite for finalized missions
- only consider bridge-sourced missions where `source='redis_bridge'`
- publish a `MissionResultEnvelope` back to the Redis results stream
- mark `result_published=1` after successful publish

Important current rule:
- gateway-local missions are not published back through this bridge
- only missions that originated from Redis delegation are bridged back outward

## 3. Bridge Process Lifecycle

Primary implementation:
- `src/universal_agent/delegation/bridge_main.py`

The standalone bridge process currently does all of the following:
- connects to Redis mission bus
- connects to the local runtime database
- ensures runtime schema exists
- starts inbound bridge
- starts outbound result bridge
- starts periodic heartbeat task
- exits cleanly for system-triggered restart workflows

The bridge is therefore the main **factory control-plane adapter**.

## 4. Factory Heartbeat Model

Primary implementation:
- `src/universal_agent/delegation/heartbeat.py`

Factories periodically POST presence information to:
- `{UA_HQ_BASE_URL}/api/v1/factory/registrations`

Resolved HQ base URL sources:
- `UA_HQ_BASE_URL`
- `UA_BASE_URL`
- `UA_GATEWAY_URL`

Current heartbeat payload includes:
- `factory_id`
- `factory_role`
- `registration_status`
- `heartbeat_latency_ms`
- `capabilities`
- `metadata`

Metadata currently includes:
- hostname
- pid
- uptime
- platform
- deployment profile
- `heartbeat_source=bridge_heartbeat`

### Heartbeat Status Model

Current status behavior:
- normal bridge -> `online`
- paused bridge -> `paused`

Current heartbeat defaults:
- nominal interval `60s`
- minimum interval clamp `10s`
- timeout `15s`
- exponential backoff on consecutive failures
- backoff cap `300s`

### Capabilities Derived from Env

Heartbeat capability reporting currently derives from env such as:
- `UA_DELEGATION_REDIS_ENABLED`
- `ENABLE_VP_CODER`
- `ENABLE_VP_GENERAL`

It also appends capability markers like:
- `delegation_mode:listen_only`
- `heartbeat_scope:local`

## 5. HQ Registry Model

Primary implementation:
- `src/universal_agent/delegation/factory_registry.py`
- HQ integration in `src/universal_agent/gateway_server.py`

HQ persists registrations in SQLite rather than keeping them only in memory.

Default database path:
- `AGENT_RUN_WORKSPACES/factory_registry.db`

Override env:
- `UA_FACTORY_REGISTRY_DB_PATH`

Current schema stores:
- `factory_id`
- `factory_role`
- `deployment_profile`
- `source`
- `registration_status`
- `heartbeat_latency_ms`
- `capabilities`
- `metadata`
- `first_seen_at`
- `last_seen_at`
- `updated_at`

### Staleness Enforcement

Current thresholds:
- `5 minutes` -> `stale`
- `15 minutes` -> `offline`

Current behavior:
- HQ runs enforcement in the background
- factories automatically revive to `online` when a new heartbeat arrives

## 6. HQ Self-Heartbeat

Primary implementation:
- `src/universal_agent/gateway_server.py`

HQ also heartbeats itself into the registry.

Purpose:
- keep headquarters visible as an online factory in fleet views
- avoid HQ appearing stale simply because no external factory posted on its behalf

Current HQ self-heartbeat interval:
- `60s`

## 7. Fleet APIs and Operator Surfaces

Primary implementation:
- `src/universal_agent/gateway_server.py`
- `web-ui/app/dashboard/corporation/page.tsx`

Current HQ-only fleet endpoints include:
- `GET /api/v1/factory/capabilities`
- `POST /api/v1/factory/registrations`
- `GET /api/v1/factory/registrations`
- `GET /api/v1/ops/delegation/history`
- `POST /api/v1/ops/factory/update`
- `POST /api/v1/ops/factory/control`

### Role Gating

These fleet surfaces are currently only available when:
- `FACTORY_ROLE=HEADQUARTERS`

Current enforcement behavior:
- non-HQ roles receive `403`
- `LOCAL_WORKER` role exposes only a health-oriented gateway surface
- WebSocket API is disabled for `LOCAL_WORKER`

## 8. Factory Control Missions

Primary implementation:
- `src/universal_agent/delegation/system_handlers.py`
- control publishing in `src/universal_agent/gateway_server.py`

Supported system mission kinds:
- `system:update_factory`
- `system:pause_factory`
- `system:resume_factory`

### `system:update_factory`

Behavior:
- handled inline by the bridge, not inserted into VP queue
- runs `scripts/update_factory.sh`
- returns success with `restart_requested=True`
- bridge exits cleanly so systemd can restart with new code

### `system:pause_factory`

Behavior:
- bridge stops consuming new Redis missions
- heartbeat continues
- status is reported as paused

### `system:resume_factory`

Behavior:
- bridge resumes mission consumption

## Canonical Environment Controls

Delegation transport and bridge:
- `UA_DELEGATION_REDIS_ENABLED`
- `UA_REDIS_URL`
- `UA_REDIS_HOST`
- `UA_REDIS_PORT`
- `UA_REDIS_DB`
- `REDIS_PASSWORD`
- `UA_DELEGATION_STREAM_NAME`
- `UA_DELEGATION_CONSUMER_GROUP`
- `UA_DELEGATION_DLQ_STREAM`
- `UA_BRIDGE_POLL_SECONDS`

Factory identity and HQ communication:
- `UA_HQ_BASE_URL`
- `UA_BASE_URL`
- `UA_GATEWAY_URL`
- `UA_OPS_TOKEN`
- `UA_FACTORY_ID`
- `FACTORY_ROLE`
- `UA_DEPLOYMENT_PROFILE`
- `UA_HEARTBEAT_INTERVAL_SECONDS`

Registry persistence:
- `UA_FACTORY_REGISTRY_DB_PATH`

VP capability surface:
- `ENABLE_VP_CODER`
- `ENABLE_VP_GENERAL`

## Current Health Signals

Healthy indicators:
- bridge process connected to Redis and runtime DB
- heartbeats accepted with 200/201 responses
- factory appears `online` in registrations list
- delegation history shows missions and status transitions
- result publishing increments for bridge-sourced missions

Common failure signatures:
- missing HQ URL causes heartbeat task to remain idle
- ops token mismatch causes registration rejection
- Redis connection failure prevents mission transport
- runtime DB/schema issues prevent local queue insertion
- result bridge errors leave mission results unpublished

## Current Gaps and Follow-Up Items

1. **Capability reporting is partly declarative**
   - some capability values are env-derived tags rather than dynamic runtime probes

2. **Mission routing is intentionally narrow**
   - only a few mission kinds currently map directly to VP ids in the bridge

3. **Registry and historical mission state are split**
   - presence lives in factory registry SQLite, while mission lifecycle truth lives in VP mission tables and Redis transport state

4. **HQ-only surface is correct but concentrated**
   - fleet controls depend heavily on HQ role gating and ops-token correctness

## Source Files That Define Current Truth

Primary implementation:
- `src/universal_agent/delegation/redis_vp_bridge.py`
- `src/universal_agent/delegation/redis_vp_result_bridge.py`
- `src/universal_agent/delegation/heartbeat.py`
- `src/universal_agent/delegation/factory_registry.py`
- `src/universal_agent/delegation/system_handlers.py`
- `src/universal_agent/delegation/bridge_main.py`
- `src/universal_agent/gateway_server.py`

Relevant supporting docs/evidence:
- `corporation/status.md`
- `deployment/systemd-user/universal-agent-local-factory.service`
- `scripts/deploy_local_factory.sh`
- `scripts/update_factory.sh`
- `tests/delegation/`

## Bottom Line

The canonical current factory model is:
- **Redis transport for cross-machine delegation**
- **SQLite mission state for local execution**
- **periodic HQ registration heartbeat**
- **persistent HQ registry with stale/offline enforcement**
- **HQ-only operator controls for fleet update, pause, resume, and history**

It is a durable, operations-first delegation control plane rather than a generic distributed job platform.

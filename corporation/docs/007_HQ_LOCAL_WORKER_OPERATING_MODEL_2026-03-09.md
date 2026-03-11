# HQ + Local Worker Operating Model (2026-03-09)

This document explains, in plain language, how Headquarters (VPS) and Kevin Desktop (local worker) are expected to work together right now, what is already implemented, and what to improve next.

## 1. Default State (Now)

### Headquarters (`dev` / VPS)
- Role: `HEADQUARTERS`
- Mode: full gateway
- Delegation: publish + listen
- CSI ingest: enabled
- Purpose: primary command center

### Kevin Desktop (`kevins-desktop` / local)
- Role: `LOCAL_WORKER`
- Mode: local-worker posture
- Delegation: currently configured to be dispatch-capable at env level, but role policy is still worker-first
- CSI ingest: explicitly disabled (`UA_SIGNALS_INGEST_ENABLED=0`, `UA_CAPABILITY_CSI_INGEST=0`)
- Purpose: execute delegated work, report status/results to HQ, avoid duplicate CSI pipeline load

## 2. How Communication Works

## Control plane
1. You talk to Simone at Headquarters.
2. HQ decides whether to keep work local or delegate.
3. HQ publishes missions on Redis delegation bus.
4. Local worker consumes missions and executes them.

## Worker telemetry plane
1. Local worker sends periodic heartbeat registration to HQ (`/api/v1/factory/registrations`).
2. HQ stores registrations in factory registry.
3. Corporate tab reads the registry and delegation history from HQ APIs.

## Result plane
1. Local worker writes mission outcomes to local SQLite runtime.
2. Bridge publishes result events back through Redis.
3. HQ shows mission history and status.

## 3. What Corporate Tab Should Show (and now has better support for)

For each factory row:
- role, deployment profile, status, latency, freshness
- capability labels (including CSI ingest on/off)
- expanded operational posture:
  - gateway mode
  - delegation mode
  - heartbeat scope
  - CSI ingest enabled/disabled
  - AgentMail enabled/disabled
  - publish/listen delegation permissions
  - selected runtime toggles (GWS CLI, signals ingest, heartbeat, cron)

This is intended to answer: “What is this factory currently allowed to do?”

## 4. Simone-Centric Operating Model

Recommended control model:
1. HQ Simone is the single operator interface.
2. Local worker should not be manually micro-managed during normal operation.
3. Worker reports up via heartbeat + mission outcomes; HQ summarizes fleet state in Corporate tab.

In simple terms:
- HQ Simone assigns work.
- Local worker executes.
- HQ Simone observes and decides next actions.

## 5. Specializations for Kevin Desktop (Recommended)

Start with these worker-specialized responsibilities:
1. Code execution and long-running build/test jobs.
2. Local filesystem-heavy tasks (bulk transforms, indexing, report generation).
3. Parallelizable automation jobs (where HQ delegates and monitors).
4. Experiment/sandbox tasks that should not load the HQ runtime.

Keep on HQ (for now):
1. CSI ingestion and CSI triage pipeline ownership.
2. Fleet-wide orchestration decisions.
3. Centralized policy and approval gates.

## 6. Suggested Next Improvement

Add an HQ “Factory Liaison” view/agent pattern:
1. Periodically summarize worker posture and mission throughput.
2. Flag worker drift (stale, disconnected bus, queue backlog).
3. Emit a short natural-language “Factory Status Brief” to HQ dashboard/events.

This gives you a direct answer to: “What is the second factory doing for us right now?”

## 7. Guardrail Rule (Explicit)

Do not enable duplicate CSI ingestion on local worker unless intentionally testing CSI failover.

Current enforcement:
- `UA_SIGNALS_INGEST_ENABLED=0`
- `UA_CAPABILITY_CSI_INGEST=0`


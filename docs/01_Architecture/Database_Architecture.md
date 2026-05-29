# Universal Agent Database Architecture

**Last updated:** 2026-05-29

This document serves as the absolute source of truth for the database paradigms, table schemas, and pruning logic across the Universal Agent architecture.

## Overview

Universal Agent handles thousands of execution steps, high-throughput telemetry streams, and vector graph operations. Instead of a naive monolith, the database strategy relies on **domain-segregated SQLite files** combined with `WAL` journaling and `autocommit` to eradicate lock contention entirely.

If you are modifying, adding, or debugging any data state in Universal Agent, consult this architecture first to ensure you are respecting isolation paradigms.

---

## 0. Canonical DB Paths & Resolvers — READ FIRST ⚠️

> [!CAUTION]
> **Never hard-code a database path in a diagnostic, script, or query. Always resolve it through the canonical resolver function below.** Multiple copies of similarly-named DBs (`csi.db`, dev vs prod) exist on disk; querying the wrong one yields false conclusions (e.g. "the pipeline is dead" when it is healthy). This section is the authoritative map of *which file is real* and *who writes it*.

### 0.1 Resolver registry

Every UA database is addressed through a resolver that honors an env override and falls back to a default. On the VPS, `repo_root` resolves to `/opt/universal_agent`, so `AGENT_RUN_WORKSPACES/` = `/opt/universal_agent/AGENT_RUN_WORKSPACES/`.

| Logical DB | Canonical resolver (file:line) | Env override | Default path |
|---|---|---|---|
| **CSI events** (`csi.db`) | `gateway_server._csi_default_db_path()` (`gateway_server.py:17450`) | `CSI_DB_PATH` | `/var/lib/universal-agent/csi/csi.db` |
| **Runtime / execution queue** (`runtime_state.db`) | `durable.db.get_runtime_db_path()` (`durable/db.py:22`) | `UA_RUNTIME_DB_PATH` | `<repo_root>/AGENT_RUN_WORKSPACES/runtime_state.db` |
| **Activity / CSI telemetry / convergence tasks** (`activity_state.db`) | `durable.db.get_activity_db_path()` (`durable/db.py:69`) | `UA_ACTIVITY_DB_PATH` | `<repo_root>/AGENT_RUN_WORKSPACES/activity_state.db` |
| **Coder VP state** (`coder_vp_state.db`) | `durable.db.get_coder_vp_db_path()` (`durable/db.py:43`) | `UA_CODER_VP_DB_PATH` | `<repo_root>/AGENT_RUN_WORKSPACES/coder_vp_state.db` |
| **General VP state** (`vp_state.db`) | `durable.db.get_vp_db_path()` (`durable/db.py:56`) | `UA_VP_DB_PATH` | `<repo_root>/AGENT_RUN_WORKSPACES/vp_state.db` |
| **CSI watchlist config** (`channels_watchlist.json`) | `api/routers/csi_watchlist.py:16` (`_DEFAULT_WATCHLIST_FILE`) | — | `/var/lib/universal-agent/csi/channels_watchlist.json` |

**Who writes `csi.db`:** the `csi-ingester.service` (uvicorn on `127.0.0.1:8091`, `WorkingDirectory=/opt/universal_agent/CSI_Ingester/development`). Its `config.yaml` ships `db_path: "var/csi.db"` (a relative dev default), but the systemd `EnvironmentFile` (`CSI_Ingester/development/deployment/systemd/csi-ingester.env`) sets **`CSI_DB_PATH=/var/lib/universal-agent/csi/csi.db`**, which overrides the config so the ingester and the UA gateway share one DB.

### 0.2 Runtime_state vs activity_state — which holds task rows?

This trips people up repeatedly. Two different task populations live in two different files:

- **`runtime_state.db`** — execution-engine state: the durable run queue, leases, checkpoints, replay metadata. Hot path. Resolver: `get_runtime_db_path()`.
- **`activity_state.db`** — the **CSI / proactive-intelligence Task Hub** population (`task_hub_items` for `source_kind` in `convergence_candidate`, `insight_detection`, `proactive_signal`, `csi_digests`, etc.) plus dashboard telemetry. The heartbeat/watchdog and convergence pipeline read **this** file. Resolver: `get_activity_db_path()`.

When a count "doesn't match," confirm **which resolver produced the path** before drawing conclusions. (Historical incident: a 2026-05-22 audit read `runtime_state.db` and concluded assignments were empty; the canonical `activity_state.db` had 1,149.)

### 0.3 Stale / relic copies — DO NOT QUERY ❌

These are NOT canonical. Querying them produces wrong answers:

| Stale path | Why it exists | Status |
|---|---|---|
| `CSI_Ingester/development/var/csi.db` | Pre-2026-05-20 split-brain: before `CSI_DB_PATH` was set, the ingester wrote here (relative to `WorkingDirectory`). Froze at **2026-05-20 21:01** — the moment the override landed. | **Deleted 2026-05-29.** Do not recreate by running the ingester without `CSI_DB_PATH`. |
| `CSI_Ingester/development/var/csi_events.db`, `csi_ingester.db` | 0-byte abandoned scaffolding. | **Deleted 2026-05-29.** |
| Any `*.db` under a repo working tree | local dev runs create them; never the production source of truth. | ignore for prod diagnostics |

> [!WARNING]
> **The split-brain footgun is latent, not gone.** `CSI_Ingester/development/config/config.yaml` still defaults `db_path: "var/csi.db"`. Production is safe *only* because `csi-ingester.env` sets `CSI_DB_PATH`. If that env override is ever dropped, the ingester silently resumes writing the dev relic and the live `/var/lib` DB goes stale — reproducing exactly the failure this section exists to prevent. Treat `CSI_DB_PATH` in `csi-ingester.env` as load-bearing.

## 1. Concurrency Paradigms

All database connection factories throughout the system (e.g. `src/universal_agent/durable/db.py`) inject the following configuration:

```sql
PRAGMA journal_mode=WAL;
PRAGMA auto_vacuum=INCREMENTAL;
```

> [!IMPORTANT]
> Because SQLite allows asynchronous read/write through WAL, the connections utilize `isolation_level=None` (autocommit). This commits directly at the statement boundary. Background `pruning` loops also utilize `PRAGMA incremental_vacuum;` rather than a full blocking `VACUUM` scan to prevent locking the application.

---

## 2. Segregated Database Files

> [!TIP]
> The Entity-Relationship (ER) Diagram below represents the physical and logical boundaries of the isolated databases across the architecture. Notice that `csi.db` is also listed here as part of the overall topology, connecting to the Gateway.

```mermaid
erDiagram
    %% runtime_state.db
    TASK_HUB_ITEMS {
        string id PK
        string status
        string agent_ready
        string project_key
    }
    TASK_HUB_ASSIGNMENTS {
        string id PK
        string task_id FK
        timestamp start_time
    }
    TASK_HUB_EVALUATIONS {
        string id PK
        string task_id FK
    }
    TASK_HUB_DISPATCH_QUEUE {
        string id PK
        string task_id FK
    }

    TASK_HUB_ITEMS ||--o{ TASK_HUB_ASSIGNMENTS : "has"
    TASK_HUB_ITEMS ||--o{ TASK_HUB_EVALUATIONS : "evaluated via"
    TASK_HUB_ITEMS ||--o{ TASK_HUB_DISPATCH_QUEUE : "queued as"

    %% activity_state.db
    ACTIVITY_EVENTS {
        string id PK
        json metadata
    }
    ACTIVITY_EVENT_STREAM {
        string id PK
    }
    CSI_DIGESTS {
        string id PK
    }

    %% lossless_memory/db.py
    LCM_CONVERSATIONS {
        string id PK
    }
    LCM_MESSAGES {
        string id PK
        string conversation_id FK
    }
    LCM_SUMMARIES {
        string id PK
    }
    LCM_CONTEXT_ITEMS {
        string id PK
        string summary_id FK
    }
    
    LCM_CONVERSATIONS ||--o{ LCM_MESSAGES : "contains"
    LCM_SUMMARIES ||--o{ LCM_CONTEXT_ITEMS : "mapped by"

    %% CSI Database
    CSI_DB_CHANNELS {
        string channel_id PK
    }
    CSI_DB_REDDIT {
        string subreddit PK
    }
```

Databases are strictly segregated to avoid "cross battles" (lock contention) between high-priority agent execution states and background tasks.

### `runtime_state.db`

**Purpose**: Primary hot-path database. Stores queue dispatches, unassigned tasks, execution claims, assignments, and results.

- **Module Source**: `src/universal_agent/task_hub.py`
- **Schema Lifecycle**: Handled via `ensure_schema()`
- **Pruning**: Settled/terminal tasks (`completed`, `parked`) are hard-deleted automatically via `prune_settled_tasks()` after 21 days by the heartbeat background worker.

**Core Tables**:

- `task_hub_items`: Primary queue. Tracks metadata and routing intents (`agent_ready`, `project_key`).
- `task_hub_assignments`: Tracks session leases by agent processes (start/end boundaries).
- `task_hub_evaluations`: Judging or routing scores.
- `task_hub_dispatch_queue`: Snapshots ranked priority queries.

### `activity_state.db`

**Purpose**: Dedicated telemetry. Stores streaming dashboards, CSI metrics, debug events, and background agent diagnostics.

- **Module Source**: `src/universal_agent/gateway_server.py`
- **Schema Lifecycle**: `_ensure_activity_schema()`
- **Pruning and lifecycle**: Actively runs `_activity_prune_old()` during activity writes. Expiration length is configured dynamically. Non-actionable `info`/`success` notification rows older than `UA_ACTIVITY_NOTIFICATION_AUTO_READ_HOURS` (default 24) are marked `read`, not left as unread operator work. Uses `PRAGMA incremental_vacuum()` after lifecycle maintenance.

**Core Tables**:

- `activity_events`: Long-form string/json data. `event_class='notification'` is reserved for dashboard/operator lifecycle rows; routine non-actionable telemetry is auto-read so health checks measure real unconsumed work.
- `activity_event_stream`: High-velocity firehose cache.
- `csi_digests`: Compressed metrics over rolling windows.

### `lossless_memory/db.py`

**Purpose**: Long-term memory logic for Semantic RAG (Directed Acyclic Graph compression mapping).

- **Module Source**: `src/universal_agent/lossless_memory/db.py`
- **Pruning**: Decays over a prolonged period (Default = 180 days) via `prune_decayed_nodes()`. Evaluates and clears `lcm_messages` and corresponding `lcm_context_items`.

**Core Tables**:

- `lcm_conversations`: Tracking UUIDs to interaction boundaries.
- `lcm_messages`: Raw textual ingestion tokens.
- `lcm_summaries`: Compressed branches representing historical roll-ups.
- `lcm_context_items`: Edges in the directed graph mapping summaries back to their origination.

### VP Workspace DBs

There are two dynamically provisioned DBs:

- `coder_vp_state.db`
- `vp_state.db`

**Purpose**: External VP subagents use these files to coordinate. This isolates primary Gateway processes from misbehaving subagents that might lock up a database thread running deeply recursive AST evaluations or planning routines.

---

## 3. General Requirements for Database Modifications

When adding new tables, columns, or querying strategies:

1. **Beware the JSON blob**: Queries deserializing high-volume JSON text in Python (`metadata_json`) degrade significantly when rows scale. If filtering against a JSON field becomes necessary, promote that field to a SQL column and index it.
2. **Never query unbounded states in an open loop**: Tables like `task_hub_items` and `activity_events` process millions of rows. Ensure terminal states (e.g. `status = 'completed'`) either filter explicitly, or are included in the rolling background expiration loops.
3. **Connection Handlers**: Always employ `with lock:` and rely on `sqlite_busy_timeout_ms = 15000`. Fast, localized retries outperform slow centralized queue layers. Do not artificially throttle SQL connections.

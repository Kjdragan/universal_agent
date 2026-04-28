# Heartbeat-Safe Proactive Pipeline Operations (2026-04-28)

**Last Updated:** 2026-04-28

This document is the canonical operational handoff and architecture record for the isolation of the proactive curation pipeline from the system heartbeat and the resolution of local branch synchronization following ship operations.

## 1. Context and Problem

Previously, autonomous cron-driven curation tasks were inadvertently triggering recursive heartbeat investigations. Because the system heartbeat is designed to monitor and intervene during anomalous behavior, autonomous agents executing proactive work generated "check the checker" recursion, leading to execution loops, redundant noise, and log pollution.

## 2. Heartbeat Isolation Architecture

To solve this, strict isolation between the "Heartbeat" (monitoring/health) and "Task Hub" (mission execution/ToDo dispatch) runtimes has been implemented using a metadata-driven approach.

### 2.1 Metadata Propagation (`skip_heartbeat`)
- **Canonical Mechanism:** The system now uses `metadata: {"skip_heartbeat": True}` as the standardized mechanism to exclude autonomous cron/curation sessions from primary heartbeat monitoring.
- **Cron Service (`cron_service.py`):** Automatically injects this metadata into all generated cron session objects.
- **Gateway Server (`gateway_server.py`):** `_session_skip_heartbeat` logic was updated to explicitly honor the `skip_heartbeat` flag. Sessions possessing this flag bypass standard heartbeat registration entirely.
- **Mission Dispatch (`vp/dispatcher.py` & `tools/vp_orchestration.py`):** The `MissionDispatchRequest` model and `upsert_vp_session` were updated to ingest and propagate session metadata dictionaries through the dispatch chain. This ensures that any sub-processes or external VPs also honor the heartbeat-exclusion policy.

### 2.2 Guardrail Overrides (`_curation_dispatched`)
- **Heartbeat Service (`heartbeat_service.py`):** The `_run_heartbeat` cycle was modified to incorporate a `_curation_dispatched` tracker. This safely overrides standard mission guard policies during proactive curation events, allowing proactive work to proceed without waking the primary heartbeat loop or triggering redundant health interventions.

## 3. Branch Synchronization and `/ship` Recovery

During the deployment of these fixes, the local `feature/latest2` branch became stalled in a broken rebase state following a `/ship` operation executed while checked out on a secondary feature branch (`feature/csi-events-visibility`).

**Resolution and Future Guardrails:**
- The stalled rebase was aborted.
- The local environment was resynchronized by directly merging the latest `main` branch (containing all deployed production code) into `feature/latest2`.
- **Operator Rule:** Always ensure the local repository is checked out to the primary development branch (e.g., `feature/latest2`) *before* executing the `/ship` pipeline. This prevents local tracking branches from falling behind the production deployment.

## 4. Subsystem Impact
- **Heartbeat Service:** Now operates purely as a supervisor, strictly ignoring sessions explicitly flagged with `skip_heartbeat`.
- **Cron Service / Curation:** Operates completely autonomously, preventing recursive loop contention.
- **Task Hub / Dispatch:** Successfully isolates background autonomous operations while maintaining visibility on the dashboard.

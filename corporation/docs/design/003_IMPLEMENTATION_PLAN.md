# The Universal Agent Corporation: Master Implementation Plan

## 1. Vision & Context

The Universal Agent Corporation represents a shift from a monolithic application (Headquarters) to a **Symmetrical Distributed Factory** architecture. By deploying identical Universal Agent software across multiple environments (e.g., VPS and local desktops) and aggressively pruning capabilities via `.env` parameterization, we build a scalable fleet of autonomous agents.

This document serves as the master implementation plan, expanding on the concepts outlined in the [PRD](./001_PRD.md) and the initial handoff documentation. It outlines how we progress from our foundational deployment to a fully distributed, observable, and autonomous corporate fleet.

## 2. Core Concepts

- **Symmetrical Parameterized Factories (Ghost Factories):** All nodes run the identical Universal Agent codebase. Their specific roles (Headquarters vs. Branch Factory), active agents (`ENABLE_VP_CODER=True`), and external networking logic are pruned and defined via environment variables.
- **Hierarchical Personas:**
  - **Headquarters (HQ):** Primary orchestrator (e.g., Simone). Interacts with the user, delegates global tasks, holds the canonical Global State.
  - **Local Factories:** Subordinate nodes handling specialized local execution (e.g., Homer). Processes background tasks or executes high-priority delegated work from HQ silently.
- **Independent LLM Configurations:** Configurable inference providers on a per-factory basis, managing localized costs and API routes (e.g., Premium ZAI for HQ, cheaper models for local polling).
- **Stateless Delegations:** Moving from direct API polling to a centralized Message Bus (Redis Streams). Headquarters drops a structured JSON payload onto the stream; a Local Factory pulls, executes securely, and returns the artifact.
- **Threshold-Based Memory Promotion:** Local factories summarize their autonomous work into memos and selectively promote high-value insights to the HQ Global Knowledge Base, preventing log pollution.

## 3. Implementation Phasing

### Phase 1: Evergreen Headquarters (Complete / Ongoing)

Solidifying the single-node architecture while ensuring the deployment is stable and functional.

- [x] Establish the Universal Agent Core routing and internal API structure.
- [x] Configure Base Autonomous Agents (Simone) and specialized VP Coders.
- [x] Build the Web UI Dashboard (Tasks, Logs, Tutorials backlog).
- [x] **Outcome:** A robust single Headquarters node running on the VPS.

### Phase 2: Foundation & Parameterization (Current)

Moving off legacy hardcoded secrets and laying the groundwork for safe distributed execution.

- [x] **Centralized Secrets Security (Infisical):** Replace local `.env` sprawl with Infisical Machine Identities. Strict fail-closed policies for production servers, optional dotenv fallback for local development. Handled exclusively at runtime bootstrap to avoid credential leakage or side-effects.
- [x] **Local Worker Bridging & UX:** Establish local systemd user services for workers executing specialized local workflows (e.g., "Create Repo" bootstrap). Upgrade frontend UX to support queueing, idempotency, and "Open Folder" interactions.
- [x] **Security Constraints:** Utilize zero-trust short-lived Ops Tokens and sandbox execution directories.
- [ ] **Implement Capability Parameterization:** Inject `.env` flags (`FACTORY_ROLE`, `ENABLE_VP_CODER`, `LLM_PROVIDER`) throughout the python services so that agents can dynamically generate valid `capabilities.md` profiles.

### Phase 3: Message Bus & Stateless Delegation (In Progress)

The structural shift to uncouple headquarters from direct endpoint communication with workers.

- [x] **Deploy Redis Infrastructure:** Run a Redis instance (via Docker) on the Hostinger VPS to act as the corporate Message Bus.
- [ ] **Implement Stream Consumers:** Replace point-to-point ad-hoc polling scripts (e.g., the current tutorial worker) with generalized Universal Agent Stream Consumer tasks.
- [ ] **Stateless Delegation Payloads:** Standardize the JSON schema for mission briefs. HQ publishes a Mission; a Local Factory pulls it, routes it to its active VP Agents, and publishes back the completed result.

### Phase 4: Corporation Observability UX (In Progress)

Providing the CEO (User) a single pane of glass over the entire fleet.

- [x] **The "Corporation View" Dashboard:** A new tab in the Next.js UI visible exclusively when `FACTORY_ROLE=HEADQUARTERS`.
- [x] **Real-Time Fleet Status:** Display all connected factories, their heartbeat latency, and active `.env` capabilities.
- [ ] **Cost Analytics:** Aggregate ZAI API telemetry across all participating factories to monitor burn rate and execution efficiency.

## 5. Acceptance Checklist Snapshot (2026-03-01)

- [x] **Gateway role/auth surface tests pass:** `uv run pytest tests/gateway/test_ops_auth_role_surface.py -q` => `5 passed`.
- [x] **Fleet endpoint and Redis dispatch tests pass:** `uv run pytest tests/gateway/test_ops_api.py -k "factory_capabilities_and_registration_endpoints or local_redis_dispatch" -q` => `2 passed`.
- [x] **Dashboard build includes Corporation View route:** `cd web-ui && npm run build` includes `/dashboard/corporation`.
- [x] **Live VPS Redis delegation loop validated:**
  - enqueue via `POST /api/v1/dashboard/tutorials/bootstrap-repo` with `dispatch_backend=redis_stream`
  - consume via `scripts/tutorial_local_bootstrap_worker.py --transport redis --once`
  - final state observed: `completed` with `repo_dir` and `repo_open_uri`
- [x] **Live HQ fleet endpoints validated on VPS (authenticated):**
  - `GET /api/v1/factory/capabilities`
  - `GET /api/v1/factory/registrations`

### Phase 5: Organizational Memory Sync (Future)

- [ ] **Local Scratchpads:** Ensure local factories manage their own SQLite `vp_state.db` independent of HQ.
- [ ] **Memo Promotion Pipeline:** Design the threshold criteria for local agents to author executive summary memos, and build the endpoint for HQ to ingest, index, and apply these memos globally.

## 4. Security Posture & Operations

- **Machine Identities:** Every factory receives a unique Infisical Machine Identity. If compromised, credentials are rotated centrally without code deployment.
- **Zero-Trust Communication:** Operations spanning boundaries (like Local workers talking to HQ) strictly require Ops Tokens.
- **Fail-Closed Execution:** In `vps` or `standalone_node` deployment profiles, application startup aborts entirely if central policies or secrets cannot be loaded.

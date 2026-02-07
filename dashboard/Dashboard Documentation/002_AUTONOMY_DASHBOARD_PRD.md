# 002 - Autonomy Dashboard PRD (Security-First)

## Document Control

| Field | Value |
|---|---|
| Status | Draft for review |
| Owner | Universal Agent team |
| Last Updated | 2026-02-07 |
| Scope | Web dashboard, autonomy governance, mission queue, control plane, deployment model, persona/account model |

## 1) Problem Statement

The current product experience is strong for interactive chat execution, but weak for system operations:

1. Operators cannot manage multi-session autonomous activity from a single control surface.
2. Control-plane capabilities exist in backend APIs but are not presented as a coherent dashboard UX.
3. Autonomy controls are not yet formalized as policies, approval gates, and dispatch rules.
4. Security posture is not consistently enforced across autonomous workflows and session isolation.

Result: the system can execute tasks, but cannot yet be safely and efficiently operated at scale.

## 2) Product Vision

Build a unified **Operations Dashboard** that treats each session as an autonomous unit:

1. One session = one primary coordinator agent + delegated sub-agents.
2. Many sessions may run in parallel across channels (web, Telegram, future Slack/WhatsApp).
3. Operators can observe, govern, and intervene in real time.
4. Security and policy controls are first-class, not bolt-ons.
5. Default operating posture is **autonomous execution until a risk/approval gate is reached**.

## 3) Goals and Non-Goals

### Goals

1. Provide a left-nav dashboard UX with tabbed operational domains.
2. Formalize autonomy governance per session (limits, approvals, tool policy).
3. Introduce mission queue orchestration with deterministic dispatch rules.
4. Preserve and extend existing gateway APIs wherever possible.
5. Add security controls for multi-session autonomy and control-plane operations.
6. Ensure background completion updates and operator notifications are first-class workflow outcomes.

### Non-Goals (V1)

1. Fully autonomous self-directed agents without policy constraints.
2. Replacing all existing chat workflows.
3. Enterprise multi-tenant RBAC beyond a strong single-operator/small-team model.
4. Direct deep integration with every channel provider in V1.
5. Full telephony execution layer (phone call voice loop) in V1; this is roadmap-scoped.

## 4) Primary Users

1. **Operator (you)**: launches and supervises sessions, approves sensitive actions, tunes policy.
2. **System (primary agent)**: coordinates delegated work under defined governance.
3. **Channel user**: initiates missions from external channels (Telegram first, others later).

## 5) Core Product Model

### Session Model

1. Session is the unit of execution and isolation.
2. A session owns:
   1. Primary coordinator agent.
   2. Delegated sub-agent activity.
   3. Workspace artifacts and logs.
   4. Runtime policy and budgets.

### Mission Model

1. Mission is a queued request with priority, risk, and routing metadata.
2. Mission can:
   1. Attach to an existing session.
   2. Spawn a new session.
   3. Require approval before execution depending on risk profile.

### Autonomy Execution Pattern

1. Default mission behavior is autonomous execution with minimal restrictions under normal policy.
2. The system pauses only at defined gates:
   1. preflight approval gate (if required by risk)
   2. in-flight approval checkpoint (side-effect threshold crossed)
   3. assistance-required gate (missing data, ambiguity, blocked dependency)
3. After pause resolution, execution resumes autonomously.
4. Completion is written to queue/task status and optionally notifies operator.

### Notification and Escalation Model

1. Notify on:
   1. mission completion/failure
   2. approval required
   3. assistance required
2. Channels:
   1. in-app dashboard
   2. Telegram
   3. email/SMS/push (provider-dependent)
   4. telephony escalation (roadmap, out-of-scope for V1)
3. Operator can respond from channel or dashboard to unblock gates.
4. V1 default routing: approval-required, assistance-required, and mission-failed all notify via Telegram + email (+ dashboard inbox).

### Persona and Account Strategy

1. Sessions run with stable agent personas (for consistent external behavior and memory continuity).
2. External actions support two account paths:
   1. persona-owned accounts (default for autonomous outward activity)
   2. operator proxy accounts (explicitly requested on-behalf-of actions)
3. Every external action must carry explicit attribution metadata and policy checks.
4. Default external identity mode should be `persona`; `operator_proxy` must be explicit per mission/action.
5. The system should minimize unnecessary linkage of operator identity in outward workflows while respecting legal/platform constraints.

## 6) Dashboard Information Architecture

Target left-nav structure:

1. Dashboard
2. Chat
3. DNA
4. Memory
5. Skills
6. Cron Jobs
7. Schedule
8. Goals
9. To-Do List
10. Mission Queue
11. Channels
12. Settings

### Tab Intent and Backend Mapping

1. **Dashboard**
   1. Intent: system health, active sessions, run rates, errors, queue depth.
   2. Existing backend: `/api/v1/health`, `/api/v1/sessions`, `/api/v1/system/presence`, ops logs endpoints.
   3. Gap: aggregate summary endpoint for low-latency rendering.

2. **Chat**
   1. Intent: existing interactive interface per session.
   2. Existing backend: websocket `/api/v1/sessions/{session_id}/stream`.
   3. Gap: route-based embedding inside dashboard shell.

3. **DNA**
   1. Intent: controlled editing of identity/soul/behavior profile.
   2. Existing backend: config APIs can store policy; direct DNA schema not formalized.
   3. Gap: define `dna` schema and validation rules.

4. **Memory**
   1. Intent: inspect memory health, indexes, recent writes, flush state.
   2. Existing backend: memory subsystems and heartbeat state files exist.
   3. Gap: dedicated memory ops endpoints and metrics.

5. **Skills**
   1. Intent: catalog, enabled/disabled, requirement status.
   2. Existing backend: `/api/v1/ops/skills`, patch per skill.

6. **Cron Jobs**
   1. Intent: create, edit, run, inspect jobs and runs.
   2. Existing backend: `/api/v1/cron/jobs*`, `/api/v1/cron/runs`.

7. **Schedule**
   1. Intent: timeline view combining cron + planned missions + optional calendar events.
   2. Existing backend: cron endpoints + mission queue (new).
   3. Gap: unified schedule feed endpoint + timeline mutation APIs.
   4. UX Requirement: allow moving/rescheduling items directly in schedule UI (drag/drop or edit controls) with policy/audit tracking.
   5. V1 Integration Requirement: Google Calendar read/write through existing Composio/OAuth-connected toolchain.

8. **Goals**
   1. Intent: short/long-term goal hierarchy and priority linkage.
   2. Existing backend: none formalized.
   3. Gap: new goals service + persistence.

9. **To-Do List**
   1. Intent: actionable task inventory aligned with goals.
   2. Existing backend: partial task concepts exist; no explicit first-class API.
   3. Gap: task CRUD and status transitions.

10. **Mission Queue**
   1. Intent: incoming requests across channels, queue state, dispatch outcomes.
   2. Existing backend: system events + sessions; queue engine not formalized.
   3. Gap: mission queue service + dispatch policy engine.

11. **Channels**
   1. Intent: status/probe/config for Telegram and future channels.
   2. Existing backend: `/api/v1/ops/channels`, probe/logout endpoints.

12. **Settings**
   1. Intent: global runtime config, model defaults, approvals, guardrails.
   2. Existing backend: `/api/v1/ops/config*`, `/api/v1/ops/approvals`, `/api/v1/ops/models`.

## 7) Security Requirements (Mandatory)

### 7.1 Security Principles

1. Deny by default for sensitive actions.
2. Explicit policy per session before autonomous execution.
3. Strong auditability of every control-plane mutation.
4. Least privilege for tool usage and file access.
5. Redaction-first telemetry and logging.

### 7.2 Threat Model Focus Areas

1. Prompt injection leading to unsafe tool calls.
2. Cross-session data leakage.
3. Unauthorized control-plane changes.
4. Secrets exposure in logs/traces/payloads.
5. Unbounded autonomous actions (cost, external side effects).
6. Channel spoofing or misuse.

### 7.3 Required Controls

1. **Session isolation hardening**
   1. Remove or rework process-wide stdout/stderr redirection in multi-session mode.
   2. Ensure per-session log streams and file boundaries.

2. **Filesystem/tool boundary enforcement**
   1. Enforce workspace path allowlist for read/write/edit/bash-sensitive calls.
   2. Keep absolute-path policy and add containment checks.

3. **Approval framework**
   1. Policy-based HITL for side-effect classes: communications, external write, code exec, finance, account actions.
   2. Configurable thresholds by mission risk and session trust mode.

4. **Payload security**
   1. Full payload mode only when explicitly enabled.
   2. Structured redaction for secrets, tokens, emails, and PII.
   3. Mark and protect high-sensitivity traces.

5. **Ops authentication and authorization**
   1. Require ops token for `/api/v1/ops/*`.
   2. Add role scopes (viewer/operator/admin) in V2.

6. **Audit trail**
   1. Immutable record of config changes, approvals, dispatch decisions, and overrides.

7. **Identity and persona boundaries**
   1. Support persona-owned accounts and operator-proxy accounts as separate modes.
   2. Enforce explicit attribution mode per action (`persona`, `operator_proxy`, `system`).
   3. Require policy checks before any external action that could misrepresent identity or violate platform/legal rules.

## 8) Scalability and Reliability Requirements

1. Handle multiple concurrent sessions with bounded resource usage.
2. Keep UI responsive with polling/stream cadence and server-side aggregation.
3. Mission queue must support prioritization and retries.
4. Cron and heartbeat must coexist without starvation.
5. Degrade gracefully when channel connectors are unavailable.

## 8.1) Deployment Topology Requirements

1. Support profile A: local workstation deployment (current mode).
2. Support profile B: dedicated standalone small-computer node (target near-term).
3. Support profile C: VPS/server deployment (future).
4. Runtime profile must define:
   1. max sessions
   2. CPU/memory limits
   3. allowed host-control capabilities
   4. recovery policy (restart/backoff/checkpoint restore)

### Access Control Defaults (Interview-Locked)

1. Auth posture: auth off for local development, token-gated for remote surfaces.
2. Remote web dashboard access should require VPN/Tailscale in addition to token-based auth.
3. Single operator identity model for now (same user across desktop, phone, and channel interactions).

### Deployment Recommendation (Initial)

1. Near-term production target: dedicated standalone small-computer node (single-purpose host).
2. Local workstation remains development/staging profile.
3. VPS profile is optional for remote operation, but not required for initial autonomy rollout.
4. Standalone-node hardening baseline:
   1. run under dedicated non-root OS user
   2. full-disk encryption + automatic updates
   3. minimal exposed network surface (reverse proxy/VPN, no open admin ports by default)
   4. secrets in environment/config vault, not plaintext docs/logs
   5. controlled startup policy with watchdog and restart budget

## 9) Session Governance Schema (Proposed)

```json
{
  "session_id": "string",
  "mode": "interactive|autonomous|supervised",
  "policy": {
    "approval_mode": "none|on_risk|always",
    "tool_profile": "strict|standard|extended",
    "allowed_tool_namespaces": ["mcp__internal__", "mcp__composio__"],
    "blocked_tools": ["..."],
    "filesystem_scope": {
      "workspace_only": true,
      "allow_absolute_within_workspace": true
    },
    "network_policy": {
      "allow_web_search": true,
      "allowed_domains": []
    },
    "identity_policy": {
      "action_identity_mode": "persona|operator_proxy|system",
      "allow_persona_external_actions": true,
      "allow_operator_proxy_actions": true
    },
    "approval_checkpoints": {
      "require_preflight_for_risk": ["high", "critical"],
      "in_flight_gate_classes": ["external_send", "account_change", "high_cost"]
    },
    "public_posting": {
      "enabled": false,
      "requires_global_toggle": true
    }
  },
  "limits": {
    "max_subagents": 4,
    "max_parallel_tools": 6,
    "max_runtime_minutes": 180,
    "max_tool_calls": 300,
    "max_budget_usd": 25
  },
  "observability": {
    "full_payload_mode": false,
    "redaction_policy": "strict|balanced|debug"
  },
  "notifications": {
    "routing": {
      "approval_required": ["dashboard", "telegram", "email"],
      "assistance_required": ["dashboard", "telegram", "email"],
      "mission_failed": ["dashboard", "telegram", "email"]
    },
    "approval_timeout_hours": 111,
    "approval_reminder_count": 1
  }
}
```

## 10) Mission Queue Model and Dispatch Rules (Proposed)

### Mission Record

```json
{
  "mission_id": "string",
  "source_channel": "web|telegram|slack|whatsapp|api",
  "request_text": "string",
  "priority": "P0|P1|P2|P3",
  "risk_level": "low|medium|high|critical",
  "requires_preflight_approval": false,
  "checkpoint_policy": "default|strict|custom",
  "goal_id": "optional",
  "session_strategy": "reuse|spawn_new|auto",
  "target_session_id": "optional",
  "status": "queued|triaged|awaiting_approval|running|blocked|completed|failed|cancelled",
  "created_at": "iso8601"
}
```

### Dispatch Rules

1. If `target_session_id` exists and is healthy, reuse unless policy conflict.
2. If active session has conflicting policy/limits, spawn new session.
3. High-risk missions enter `awaiting_approval` by default.
4. Priority order: P0 > P1 > P2 > P3 with aging to prevent starvation.
5. Per-session and global concurrency caps must be enforced.
6. Cancellation and preemption rules:
   1. P0 can preempt queued lower-priority missions.
   2. Running missions need safe interruption points.
7. Execute autonomously until checkpoint; emit gate event and pause only when policy requires approval or assistance.
8. On completion, publish status update to mission queue and configured notification channels.

### Capacity and Rate-Limit Strategy (Provider-Constrained)

1. Baseline provider assumption: high token capacity, tight concurrency limits (ZAI/GLM profile).
2. Global model-call concurrency budget should be centrally enforced (initial target: `2-4`, default `3`).
3. Session scheduler must degrade to queued execution when global concurrency budget is exhausted.
4. Dispatch policy under saturation:
   1. reserve capacity for active in-flight operations
   2. queue additional missions by priority with aging
   3. default fairness policy: oldest-ready-first within priority band
5. Per-session execution should avoid over-parallelization when global budget is constrained.
6. Research/report pipelines should retain existing batching and retry/backoff patterns.

### Governance Defaults (Interview-Locked)

1. Development posture: YOLO mode with major gating infrastructure present but mostly disabled by default.
2. Hard-stop categories that remain enforced even in YOLO:
   1. no payments
   2. no outbound email outside whitelist
   3. no account deletion/password reset/public posting (until explicit global toggle)
   4. no destructive local/system operations outside allowed workspace/sandbox scope
3. Queue policy under load: global priority first, then oldest-ready within priority band.
4. Session dispatch policy: auto reuse for related work, spawn new sessions for unrelated/high-priority clean-slate work.
5. Schedule conflict policy: block conflicting moves and require manual resolution.
6. Approval timeout: 111 hours, one reminder, then mark blocked.
7. Dashboard UX requirement: blocked approvals must surface explicit actions (`Approve now`, `Dismiss/Delete`).

## 11) Functional Requirements by Phase

### Phase 1 (Dashboard V1 - Existing APIs)

1. Build route-based shell with left-nav.
2. Wire tabs: Dashboard, Chat, Skills, Cron Jobs, Channels, Settings.
3. Add summary cards for sessions, cron, heartbeat, queue placeholder, channel health.
4. Add ops logs tail viewer (session-scoped).

### Phase 2 (Governance Foundation)

1. Implement session policy schema and persistence.
2. Add policy editor UI and policy validation.
3. Integrate approvals workflow into execution entry points.
4. Add kill-switch controls per session.
5. Add in-flight checkpoint gating and resume semantics.

### Phase 3 (Mission Queue + Goals)

1. Implement mission queue storage and APIs.
2. Implement dispatch engine with explicit rules.
3. Add Goals and To-Do services, link to mission generation.
4. Add mission timeline and operator interventions.
5. Add global concurrency-aware dispatch and queue drain policy.

### Phase 4 (Schedule + Autonomy Controls)

1. Unified schedule feed combining cron + missions + heartbeat events.
2. Add proactive planning triggers from goals/todos.
3. Introduce supervised autonomy modes with guardrails.
4. Add schedule edit interactions (move/re-time/re-prioritize) with audit trail.
5. Add proactive planner cadence controls (default every 30 minutes).

### Phase 5 (Scale and Hardening)

1. Worker/process model for higher session concurrency.
2. Performance optimization and backpressure.
3. Security review, penetration checklist, chaos tests.
4. Add communication escalation architecture for multi-channel gating, including telephony design (out-of-scope implementation in this phase).
5. Add provider profile tuning (`ZAI_MAX_CONCURRENT`, backoff floor/cap) with runtime observability.

## 12) Non-Functional Requirements

1. P95 dashboard API latency < 400ms for summary endpoints.
2. Websocket reconnect and resume support.
3. Full audit log coverage for control actions.
4. Deterministic config versioning with optimistic concurrency.
5. Automated tests for policy and dispatch logic.

## 13) API Plan

### Reuse Existing

1. Sessions: `/api/v1/sessions`, `/api/v1/sessions/{id}`, websocket stream endpoint.
2. Heartbeat: `/api/v1/heartbeat/*`, `/api/v1/system/presence`.
3. Cron: `/api/v1/cron/*`.
4. Ops: `/api/v1/ops/skills`, `/api/v1/ops/channels`, `/api/v1/ops/config*`, `/api/v1/ops/approvals`, `/api/v1/ops/logs/tail`.

### Add New

1. `/api/v1/dashboard/summary`
2. `/api/v1/goals/*`
3. `/api/v1/todos/*`
4. `/api/v1/missions/*`
5. `/api/v1/sessions/{id}/policy`
6. `/api/v1/schedule/feed`
7. `/api/v1/dna`
8. `/api/v1/missions/{id}/approve`
9. `/api/v1/missions/{id}/resume`
10. `/api/v1/notifications/test`
11. `/api/v1/schedule/items/{id}` (reschedule/update)

## 14) Delivery Plan (Implementation Plan)

### Milestone A: Planning and Security Baseline (1-2 weeks)

1. Finalize PRD and schema decisions.
2. Implement session isolation fixes for logging/output handling.
3. Implement workspace containment enforcement at tool boundary.
4. Define redaction levels and default policies.

### Milestone B: Dashboard Shell V1 (1-2 weeks)

1. Build left-nav app shell and route system.
2. Connect existing endpoints for Dashboard, Chat, Skills, Cron, Channels, Settings.
3. Add role-free ops token gate in frontend flow.

### Milestone C: Governance and Queue (2-3 weeks)

1. Implement session policy API + UI.
2. Implement mission queue API + storage + dispatcher.
3. Integrate approvals in dispatch flow.
4. Implement checkpoint pause/resume and assistance-required gates.

### Milestone D: Goals/To-Do/Schedule (2 weeks)

1. Implement goals and to-do CRUD with priority semantics.
2. Build schedule aggregation and visualization.
3. Tie proactive mission creation to goals and heartbeat cadence.
4. Add schedule mutation actions (drag/drop reschedule + conflict validation).

### Milestone E: Hardening and Release (1-2 weeks)

1. Security test pass and threat model validation.
2. Load/performance tests for parallel sessions.
3. Runbook and operator documentation.
4. Publish channel escalation architecture including telephony roadmap.

## 15) Testing Strategy

1. Unit tests:
   1. policy evaluation
   2. risk classification
   3. dispatch decisions
2. Integration tests:
   1. session lifecycle and websocket events
   2. approval-required mission flows
   3. cron-to-mission-trigger flow
3. Security tests:
   1. prompt injection simulation in mission payloads
   2. path traversal and cross-session access
   3. redaction validation for full payload mode
   4. identity-mode policy enforcement and attribution correctness
4. Load tests:
   1. N concurrent sessions with mission throughput thresholds

## 16) Decision Register

### Locked Decisions (Interview - 2026-02-07)

1. Runtime profile: local desktop now, with remote channel interaction; near-term transition target is dedicated standalone node.
2. Hard-stop categories enforced now: no payments, no non-whitelist outbound email, no public posting/account-destructive actions, no destructive local/system operations outside workspace/sandbox policy.
3. Concurrency policy: universal rate limiter with initial target around 3-4 concurrent model actions, tuned downward for direct ZAI API saturation.
4. Queue policy: global priority first, then oldest-ready fairness.
5. Identity default: `persona`; `operator_proxy` requires explicit operator intent.
6. Notifications in V1: Telegram + email for approval-required, assistance-required, and mission-failed.
7. Auto-executable proactive classes in V1: safe intel and internal housekeeping classes (expandable whitelist).
8. Approval timeout policy: 111 hours, one reminder, then blocked.
9. Session dispatch strategy: auto (reuse related sessions, spawn new for unrelated/high-priority clean-slate work).
10. Dashboard access security: auth off locally, token-gated remotely, remote web access via VPN/Tailscale.
11. Email whitelist management: editable in dashboard settings with audit trail.
12. Public posting policy: disabled until explicit global toggle; revisit when persona-owned accounts are operational.
13. Persona strategy: single primary persona shared across sessions at launch.
14. Proactive planner cadence: every 30 minutes by default.
15. Calendar mode in V1: internal schedule + full Google Calendar read/write via Composio-connected capabilities.
16. DNA apply semantics: apply to new sessions by default (no forced live mutation of active sessions).
17. Telephony roadmap: design in V1, implementation target in V2.

### Remaining Open Items

1. Goals scope model: global vs per-session vs hybrid.
2. Telephony escalation activation criteria (while implementation remains targeted for V2).

## 17) Immediate Next Step

Use the locked decisions in Section 16 to execute Milestone A/B immediately, and resolve the remaining open items before Milestone C scope lock.

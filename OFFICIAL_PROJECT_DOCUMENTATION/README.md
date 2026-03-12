# Universal Agent Documentation

Welcome to the official technical documentation for the **Universal Agent**. This documentation is designed for junior developers to quickly understand the project's architecture, core logic, and operational workflows.

## Deployment Notice

The canonical deployment contract is maintained in `docs/deployment/`, not in older VPS runbooks under `03_Operations/`.

- `docs/deployment/architecture_overview.md`
- `docs/deployment/ci_cd_pipeline.md`
- `docs/deployment/infisical_factories.md`

## 📚 Table of Contents

### 1. [Architecture](01_Architecture)

- **[System Overview](01_Architecture/System_Overview.md)**: High-level component map.
- **[Core Classes](01_Architecture/Core_Classes.md)**: `UniversalAgent`, `HeartbeatService`, and `Gateway`.
- **[Soul Architecture](01_Architecture/Soul_Architecture.md)**: Identity and persona injection.
- **[UI Architecture](01_Architecture/UI_Architecture.md)**: Next.js, Zustand, and WebSocket flows.

### 2. [Subsystems](02_Subsystems)

- **[Memory System](02_Subsystems/Memory_System.md)**: Tiered memory & Auto-Flush.
- **[Heartbeat Service](02_Subsystems/Heartbeat_Service.md)**: Autonomic cycle.
- **[Durable Execution](02_Subsystems/Durable_Execution.md)**: Resilience features.
- **[URW Orchestration](02_Subsystems/URW_Orchestration.md)**: Multi-phase tasks.

### 3. [Flows](02_Flows)

- **[Event Streaming](02_Flows/Event_Streaming_Flow.md)**: Turn lifecycle.
- **[Resource Guardrails](02_Flows/Resource_Guardrails.md)**: Workspace security.
- **[WebSocket Architecture and Operations Source of Truth (2026-03-06)](02_Flows/07_WebSocket_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md)**: Canonical reference for gateway/UI streaming, AgentMail inbound WebSockets, shared transport tuning, and legacy WebSocket surfaces.
- **[Gateway and Web UI Auth and Session Security Source of Truth (2026-03-06)](02_Flows/08_Gateway_And_Web_UI_Auth_And_Session_Security_Source_Of_Truth_2026-03-06.md)**: Canonical reference for dashboard login, owner-lane session authorization, internal token bypasses, and API/WebSocket session access enforcement.
- **[Chat Panel Communication Layer](02_Flows/04_Chat_Panel_Communication_Layer.md)**: Full pipeline from SDK to chat UI — deduplication, sub-agent attribution, stream coalescing, rendering.
- **[Activity Log Communication Layer](02_Flows/05_Activity_Log_Communication_Layer.md)**: Full pipeline for operational observability — tool calls, hook activity, stdout capture, MCP detail.

### 4. [Operations](03_Operations)

- **[Configuration Guide](03_Operations/Configuration_Guide.md)**: Env vars & flags.
- **[Running the Agent](03_Operations/46_Running_The_Agent.md)**: CLI, Web, Telegram.
- **[Skill Development](03_Operations/Skill_Development.md)**: Developing tools.
- **[Testing Strategy](03_Operations/Testing_Strategy.md)**: QA and CI.
- **[Email Architecture and AgentMail Source of Truth (2026-03-06)](03_Operations/82_Email_Architecture_And_AgentMail_Source_Of_Truth_2026-03-06.md)**: Canonical reference for Simone vs Kevin email identity routing, AgentMail inbound/outbound architecture, reply extraction, and operations.
- **[Webhook Architecture and Operations Source of Truth (2026-03-06)](03_Operations/83_Webhook_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md)**: Canonical reference for hook ingress, auth strategies, transforms, dispatch queueing, and public versus trusted internal webhook paths.
- **[Infisical Secrets Architecture and Operations Source of Truth (2026-03-06)](03_Operations/85_Infisical_Secrets_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md)**: Canonical reference for Infisical-first runtime secret bootstrap, strict/fallback modes, provisioning, and secure operational use.
- **[Residential Proxy Architecture and Usage Policy Source of Truth (2026-03-06)](03_Operations/86_Residential_Proxy_Architecture_And_Usage_Policy_Source_Of_Truth_2026-03-06.md)**: Canonical reference for Webshare residential proxy usage, approved/disallowed paths, YouTube guardrails, and failure alerts.
- **[Tailscale Architecture and Operations Source of Truth (2026-03-06)](03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md)**: Canonical reference for tailnet-first VPS access, MagicDNS host standards, tailnet preflight checks, `tailscale serve` staging, and SSH auth mode rollout.
- **[Factory Delegation, Heartbeat, and Registry Source of Truth (2026-03-06)](03_Operations/88_Factory_Delegation_Heartbeat_And_Registry_Source_Of_Truth_2026-03-06.md)**: Canonical reference for Redis transport, local VP SQLite execution, HQ registration heartbeat, persistent fleet registry, and factory control missions.
- **[Runtime Bootstrap, Deployment Profiles, and Factory Role Source of Truth (2026-03-06)](03_Operations/89_Runtime_Bootstrap_Deployment_Profiles_And_Factory_Role_Source_Of_Truth_2026-03-06.md)**: Canonical reference for Infisical-first startup, deployment profile strictness, factory-role policy shaping, and shared runtime bootstrap behavior.
- **[Artifacts, Workspaces, and Remote Sync Source of Truth (2026-03-06)](03_Operations/90_Artifacts_Workspaces_And_Remote_Sync_Source_Of_Truth_2026-03-06.md)**: Canonical reference for session workspace roots, durable artifacts roots, mirrored VPS storage, ready-marker-gated sync, and local-vs-mirror storage APIs.
- **[Telegram Architecture and Operations Source of Truth (2026-03-06)](03_Operations/91_Telegram_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md)**: Canonical reference for the polling-based Telegram bot, gateway/in-process execution modes, allowlist and task semantics, CSI Telegram delivery paths, and legacy webhook-era remnants.
- **[CSI Architecture and Operations Source of Truth (2026-03-06)](03_Operations/92_CSI_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md)**: Canonical reference for the CSI ingester runtime, signed CSI->UA delivery contract, adapter and timer fleet model, VPS deployment, and the current boundary between CSI and native UA playlist watching.
- **[Prioritized Cleanup Plan From Canonical Review (2026-03-06)](03_Operations/93_Prioritized_Cleanup_Plan_From_Canonical_Review_2026-03-06.md)**: Handoff-ready prioritized cleanup plan (17 items, P0-P2) derived from the 12-doc canonical review, with files-to-investigate and expected fix scope for each item.
- **[Architectural and Integration Review From Canonical Review (2026-03-06)](03_Operations/94_Architectural_Integration_Review_From_Canonical_Review_2026-03-06.md)**: Strategic cross-system architectural review identifying integration coherence, incongruities, missed integrations, and structural improvement opportunities across all subsystems.
- **[Heartbeat Issue Mediation and Auto-Triage (2026-03-12)](03_Operations/95_Heartbeat_Issue_Mediation_And_Auto_Triage_2026-03-12.md)**: Canonical operations note for structured heartbeat findings, Simone auto-investigation, operator review escalation, and the no-auto-remediation safety boundary.
- **[OpenCLAW Release Parity Assessment (2026-02-06)](03_Operations/03_OpenCLAW_Release_Parity_Assessment_2026-02-06.md)**: Security and feature gap triage against recent OpenCLAW releases.
- **[VPS Host Security Hardening Runbook (2026-02-12)](03_Operations/26_VPS_Host_Security_Hardening_Runbook_2026-02-12.md)**: Solo-dev-safe VPS hardening steps with validation and rollback.
- **[Bowser Integration: Strategic Capability Expansion (2026-02-16)](03_Operations/40_Bowser_Integration_Strategic_Capability_Expansion_2026-02-16.md)**: How Bowser's layered browser automation stack expands UA from report-centric flows into browser-native execution, validation, and orchestration.
- **[Todoist Heartbeat and Triage Operational Runbook (2026-02-16)](03_Operations/41_Todoist_Heartbeat_And_Triage_Operational_Runbook_2026-02-16.md)**: Daily operating cadence for Todoist-backed heartbeat inputs, manual brainstorming triage, and guarded verification checks.
- **[Hybrid Local+VPS YouTube Webhook Operations Source of Truth (2026-02-18)](03_Operations/42_Hybrid_Local_VPS_Webhook_Operations_Source_Of_Truth_2026-02-18.md)**: Canonical runbook for hybrid ingress architecture, readiness checks, failure signatures, and recovery procedures.
- **[VPS WebUI Long-Running Query Evaluation (2026-02-18)](03_Operations/43_VPS_WebUI_Long_Running_Query_Evaluation_2026-02-18.md)**: End-to-end execution evidence, artifact inventory, transcript/log analysis, and reliability findings for a production-style long-running mission.
- **[CSI Todoist Sync Debugging Lessons (2026-02-26)](03_Operations/77_CSI_Todoist_Sync_Debugging_Lessons_2026-02-26.md)**: Root-cause findings and hardening changes for CSI -> Todoist sync failures, including credential diagnostics and repeatable verification steps.
- **[Daily Autonomous Briefing Reliability and Input Diagnostics (2026-02-26)](03_Operations/78_Daily_Autonomous_Briefing_Reliability_And_Input_Diagnostics_2026-02-26.md)**: Root-cause analysis and hardening changes that prevent empty/ambiguous briefings after resets and upstream ingest failures.

### 4A. [Deployment and Environments](06_Deployment_And_Environments)

- **[Architecture Overview](06_Deployment_And_Environments/01_Architecture_Overview.md)**: Continuity note pointing to the canonical deployment architecture docs.
- **[Infisical Factories](06_Deployment_And_Environments/02_Infisical_Factories.md)**: Continuity note for current factory/secret environment references.
- **[CI/CD Automated Pipelines](06_Deployment_And_Environments/03_Automated_Pipelines.md)**: Continuity note for branch-driven GitHub Actions deployment.
- **[Branching and Release Workflow](06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md)**: Current branch policy for feature work, staging promotion, and production release.
- **[Local Runtime Modes](06_Deployment_And_Environments/05_Local_Runtime_Modes.md)**: Canonical split between HQ dev and the separate desktop worker lane.
- **[Production Deploy Incident (2026-03-12)](06_Deployment_And_Environments/06_Production_Deploy_Incident_2026-03-12.md)**: Root cause, fix, verification, and prevention notes for the March 12 production `.venv` deployment regression.

### 5. [API Reference](04_API_Reference)

- **[Gateway Ops API](04_API_Reference/Ops_API.md)**: Session and log management endpoints.

### 6. [Archive](05_Archive)

- **[Decisions](05_Archive/Decisions)**: Critical architectural decision records (ADRs).
- **[Glossary.md](Glossary.md)**: Project terminology.

---

## 🚀 Recommended Path for New Developers

1. Start with the **[System Overview](01_Architecture/System_Overview.md)** to understand the big picture.
2. Read the **[Core Classes](01_Architecture/Core_Classes.md)** to see how the main objects interact.
3. Check the **[Glossary](Glossary.md)** whenever you encounter a project-specific term.
4. Try running the agent in CLI mode using the **[Running the Agent](03_Operations/46_Running_The_Agent.md)** guide.
5. Explore **[Skill Development](03_Operations/Skill_Development.md)** to see how tools are integrated.

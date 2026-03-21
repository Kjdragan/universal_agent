# Universal Agent Documentation

Welcome to the official technical documentation for the **Universal Agent**. This documentation is designed for junior developers to quickly understand the project's architecture, core logic, and operational workflows.

## ⚠️ MANDATORY: Documentation Rules

This `README.md` and `Documentation_Status.md` serve as the **authoritative indexes** for all project documentation.

- All documentation MUST be located within the `docs/` directory.
- Before creating a new document, search these indexes to ensure the topic isn't already covered. Always prefer updating an existing document over creating a new one.
- **Rule:** If you create a new documentation file, you MUST add a link to it in this `README.md` index and in `Documentation_Status.md`.

## Deployment Notice

The canonical deployment contract is maintained in `docs/deployment/`, not in older VPS runbooks under `03_Operations/`.

- `docs/deployment/architecture_overview.md`
- `docs/deployment/ci_cd_pipeline.md`
- `docs/deployment/infisical_factories.md`
- `docs/deployment/secrets_and_environments.md` — **Canonical guide for Infisical secrets and environment management**

## 📚 Table of Contents

### 1. [Architecture](01_Architecture)

- **[System Architecture Overview](01_Architecture/01_System_Architecture_Overview.md)**: Component map, services, data stores, deployment topology.
- **[Gateway, Sessions & Execution](01_Architecture/02_Gateway_Sessions_And_Execution.md)**: Session model, auth surfaces, execution engine, background services.
- **[VP Workers & Delegation](01_Architecture/03_VP_Workers_And_Delegation.md)**: Mission lifecycle, cross-machine delegation, factory heartbeat.
- **[Dual Factory and Capability Expansion Brainstorm](01_Architecture/04_Dual_Factory_And_Capability_Expansion_Brainstorm.md)**: Factory expansion concepts.
- **[Agent Architecture: agent_core.py vs main.py](001_AGENT_ARCHITECTURE.md)**: Entry point comparison, AgentSetup synchronization, URW session management.
- **[SDK Permissions, Hooks & Subagents](002_SDK_PERMISSIONS_HOOKS_SUBAGENTS.md)**: Permission model, hooks architecture, subagent patterns.
- **[Regression Control and Golden Runs](003_REGRESSION_CONTROL_AND_GOLDEN_RUNS.md)**: Testing stability, golden run preservation.

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

- **[Email Architecture and AgentMail Source of Truth (2026-03-06)](03_Operations/82_Email_Architecture_And_AgentMail_Source_Of_Truth_2026-03-06.md)**: Canonical reference for AgentMail email identity, triage→Simone inbound architecture, HTML-aware reply extraction, prompt injection defense, queue lifecycle and crash detection, triage helper CLI, and outbound policy.
- **[Webhook Architecture and Operations Source of Truth (2026-03-06)](03_Operations/83_Webhook_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md)**: Canonical reference for hook ingress, auth strategies, transforms, dispatch queueing, and public versus trusted internal webhook paths.
- **[Infisical Secrets Architecture and Operations Source of Truth (2026-03-06)](03_Operations/85_Infisical_Secrets_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md)**: Canonical reference for Infisical-first runtime secret bootstrap, strict/fallback modes, provisioning, and secure operational use.
- **[Residential Proxy Architecture and Usage Policy Source of Truth (2026-03-06)](03_Operations/86_Residential_Proxy_Architecture_And_Usage_Policy_Source_Of_Truth_2026-03-06.md)**: Canonical reference for Webshare residential proxy usage, approved/disallowed paths, YouTube guardrails, and failure alerts.
- **[Tailscale Architecture and Operations Source of Truth (2026-03-06)](03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md)**: Canonical reference for tailnet-first VPS access, MagicDNS host standards, tailnet preflight checks, `tailscale serve` staging, and SSH auth mode rollout.
- **[Factory Delegation, Heartbeat, and Registry Source of Truth (2026-03-06)](03_Operations/88_Factory_Delegation_Heartbeat_And_Registry_Source_Of_Truth_2026-03-06.md)**: Canonical reference for Redis transport, local VP SQLite execution, HQ registration heartbeat, persistent fleet registry, and factory control missions.
- **[Runtime Bootstrap, Deployment Profiles, and Factory Role Source of Truth (2026-03-06)](03_Operations/89_Runtime_Bootstrap_Deployment_Profiles_And_Factory_Role_Source_Of_Truth_2026-03-06.md)**: Canonical reference for Infisical-first startup, deployment profile strictness, factory-role policy shaping, and shared runtime bootstrap behavior.
- **[Artifacts, Workspaces, and Remote Sync Source of Truth (2026-03-06)](03_Operations/90_Artifacts_Workspaces_And_Remote_Sync_Source_Of_Truth_2026-03-06.md)**: Canonical reference for session workspace roots, durable artifacts roots, mirrored VPS storage, ready-marker-gated sync, and local-vs-mirror storage APIs.
- **[Telegram Architecture and Operations Source of Truth (2026-03-06)](03_Operations/91_Telegram_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md)**: Canonical reference for the polling-based Telegram bot, gateway/in-process execution modes, allowlist and task semantics, CSI Telegram delivery paths, and legacy webhook-era remnants.
- **[CSI Architecture and Operations Source of Truth (2026-03-06)](03_Operations/92_CSI_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md)**: Canonical reference for the CSI ingester runtime, signed CSI->UA delivery contract, adapter and timer fleet model, VPS deployment, and the current boundary between CSI and native UA playlist watching.
- **[NotebookLM Integration and Research Pipeline (2026-03-14)](03_Operations/96_NotebookLM_Integration_And_Research_Pipeline_2026-03-14.md)**: Canonical reference for NotebookLM integration — capabilities, delegation architecture, auth flow, research pipeline, artifact generation, circuit breaker tuning, and lessons learned.
- **[Infisical CLI Reference and Lessons Learned (2026-03-14)](03_Operations/97_Infisical_CLI_Reference_And_Lessons_Learned_2026-03-14.md)**: Source of truth for using Infisical CLI — authentication, secret management, environment setup, agent integration patterns, and lessons learned.
- **[Prioritized Cleanup Plan From Canonical Review (2026-03-06)](03_Operations/93_Prioritized_Cleanup_Plan_From_Canonical_Review_2026-03-06.md)**: Handoff-ready prioritized cleanup plan (17 items, P0-P2) derived from the 12-doc canonical review, with files-to-investigate and expected fix scope for each item.
- **[Architectural and Integration Review From Canonical Review (2026-03-06)](03_Operations/94_Architectural_Integration_Review_From_Canonical_Review_2026-03-06.md)**: Strategic cross-system architectural review identifying integration coherence, incongruities, missed integrations, and structural improvement opportunities across all subsystems.
- **[Heartbeat Issue Mediation and Auto-Triage (2026-03-12)](03_Operations/95_Heartbeat_Issue_Mediation_And_Auto_Triage_2026-03-12.md)**: Canonical operations note for structured heartbeat findings, Simone auto-investigation, operator review escalation, and the no-auto-remediation safety boundary.
- **[VPS Host Security Hardening Runbook (2026-02-12)](03_Operations/26_VPS_Host_Security_Hardening_Runbook_2026-02-12.md)**: Solo-dev-safe VPS hardening steps with validation and rollback.
- **[Todoist Heartbeat and Triage Operational Runbook (2026-02-16)](03_Operations/41_Todoist_Heartbeat_And_Triage_Operational_Runbook_2026-02-16.md)**: Daily operating cadence for Todoist-backed heartbeat inputs, manual brainstorming triage, and guarded verification checks.
- **[CSI Todoist Sync Debugging Lessons (2026-02-26)](03_Operations/77_CSI_Todoist_Sync_Debugging_Lessons_2026-02-26.md)**: Root-cause findings and hardening changes for CSI -> Todoist sync failures, including credential diagnostics and repeatable verification steps.
- **[Daily Autonomous Briefing Reliability and Input Diagnostics (2026-02-26)](03_Operations/78_Daily_Autonomous_Briefing_Reliability_And_Input_Diagnostics_2026-02-26.md)**: Root-cause analysis and hardening changes that prevent empty/ambiguous briefings after resets and upstream ingest failures.
- **[Agent Skills Directory (2026-03-17)](03_Operations/98_Agent_Skills_Directory.md)**: Documentation for the `.agents/skills/` directory containing clean-code, agentmail, skill-judge, systematic-debugging, and vp-orchestration skills.
- **[Documentation Drift Maintenance Pipeline (2026-03-19)](03_Operations/99_Documentation_Drift_Maintenance_Pipeline.md)**: Canonical reference for the automated documentation maintenance system — Stage 1 drift auditor, Stage 2 maintenance agent, VP mission dispatch, issue batching, and verify-before-fix rules.
- **[OpenClaw Release Sync Pipeline (2026-03-20)](03_Operations/100_OpenClaw_Release_Sync_Pipeline.md)**: Biweekly automated pipeline — Stage 1 release scanner fetches OpenClaw releases, Stage 2 VP sync agent analyzes features for adoption. Reports saved to `Openclaw Sync Discoveries/`.

### 4A. [Deployment and Environments](06_Deployment_And_Environments)

- **[Branching and Release Workflow](06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md)**: Current branch policy for feature work, staging promotion, and production release.
- **[Local Runtime Modes](06_Deployment_And_Environments/05_Local_Runtime_Modes.md)**: Canonical split between HQ dev and the separate desktop worker lane.
- **[Production Deploy Incident (2026-03-12)](06_Deployment_And_Environments/06_Production_Deploy_Incident_2026-03-12.md)**: Root cause, fix, verification, and prevention notes for the March 12 production `.venv` deployment regression.
- **[Stage-Based Infisical and Machine Bootstrap Migration Plan (2026-03-12)](06_Deployment_And_Environments/07_Stage_Based_Infisical_And_Machine_Bootstrap_Migration_Plan_2026-03-12.md)**: Living migration record for stage environments, machine-local bootstrap identity, and CI/CD runtime validation.

### 4B. [Deployment Canonical Docs](deployment)

- **[Secrets and Environments](deployment/secrets_and_environments.md)**: Single entry-point for Infisical secrets management, environment configuration, and deploy workflow secrets contract.
- **[Architecture Overview](deployment/architecture_overview.md)**: Git branching, environmental mapping, and service topology.
- **[CI/CD Pipeline](deployment/ci_cd_pipeline.md)**: Workflow details, timing, and pipeline structure.
- **[Infisical Factories](deployment/infisical_factories.md)**: Stage naming and machine bootstrap detail (superseded by Secrets and Environments).

### 4C. [CSI Subsystem](04_CSI)

- **[CSI Master Architecture & Design](04_CSI/CSI_Master_Architecture.md)**: Canonical living document for the Creator Signal Intelligence subsystem — domain taxonomy, source management (SQLite), quality scoring, adapter architecture, batch processing, delivery contracts, and subsystem boundaries. Supersedes scattered CSI docs for architecture reference.

### 5. [API Reference](04_API_Reference)

- **[Gateway Ops API](04_API_Reference/Ops_API.md)**: Session and log management endpoints.

### 6. [Archive](05_Archive)

- **[Decisions](05_Archive/Decisions)**: Critical architectural decision records (ADRs).
- **[Glossary.md](Glossary.md)**: Project terminology.

### 7. [Run Reviews](03_Run_Reviews)

- **[Session: Russia Ukraine Report (2026-02-06)](03_Run_Reviews/01_Session_20260206_160425_Russia_Ukraine_Report.md)**: First major report session.
- **[Session: Cron Breakthrough Briefing (2026-02-08)](03_Run_Reviews/02_Session_20260208_Cron_Breakthrough_Briefing.md)**: Cron scheduling breakthrough.
- **[Scheduling Runtime V2 Short Soak Readiness (2026-02-08)](03_Run_Reviews/03_Scheduling_Runtime_V2_Short_Soak_Readiness_2026-02-08.md)**: Short soak prep.
- **[Scheduling Runtime V2 24h Soak In Progress (2026-02-08)](03_Run_Reviews/04_Scheduling_Runtime_V2_24h_Soak_In_Progress_2026-02-08.md)**: 24h soak status.
- **[Self-Improving Agent Notes (2026-02-13)](03_Run_Reviews/05_Self_Improving_Agent_Notes_2026-02-13.md)**: Agent self-improvement patterns.
- **[Final Integration Sprint Review (2026-02-14)](03_Run_Reviews/06_Final_Integration_Sprint_Review_2026-02-14.md)**: Integration sprint close.
- **[Happy Path Review and Markdown Remediation (2026-02-19)](03_Run_Reviews/07_Session_20260218_232750_Happy_Path_Review_and_Markdown_Remediation_2026-02-19.md)**: Happy path validation.

### 8. Tutorial Pipeline

- **[Tutorial Pipeline Architecture & Operations](03_Operations/99_Tutorial_Pipeline_Architecture_And_Operations.md)**: Canonical single-source-of-truth for the YouTube tutorial pipeline — pipeline flow, notification kinds, 3-layer dedup, proxy config, env vars, API endpoints.

### 8. [CSI Rebuild](csi-rebuild)

- **[Master Plan](csi-rebuild/00_master_plan.md)**: CSI rebuild master plan.
- **[Architecture v2](csi-rebuild/01_architecture_v2.md)**: Revised CSI architecture.
- **[Interfaces and Schemas](csi-rebuild/02_interfaces_and_schemas.md)**: CSI interfaces and schemas.
- **[Rollout and Runbooks](csi-rebuild/03_rollout_and_runbooks.md)**: Rollout plan and runbook index.
- **[Validation Matrix](csi-rebuild/04_validation_matrix.md)**: Validation test matrix.
- **[Incident Log](csi-rebuild/05_incident_log.md)**: Incident tracking log.
- **[Packet Handoff](csi-rebuild/06_packet_handoff.md)**: Packet handoff documentation.
- **[Post Packet 10 Work Phases](csi-rebuild/07_post_packet10_work_phases.md)**: Post-packet work phases.
- **[RC Soak GA Gate](csi-rebuild/08_rc_soak_ga_gate.md)**: Release candidate soak gate.
- **[Status](csi-rebuild/status.md)**: CSI rebuild status tracker.
- **[Kevin Handoff for CSI](csi-rebuild/kevin_handoff_for CSI.md)**: Handoff documentation.

#### CSI Rebuild Runbooks

- **[Incident Triage](csi-rebuild/runbooks/01_incident_triage.md)**: Incident triage runbook.
- **[Remediation Escalation](csi-rebuild/runbooks/02_remediation_escalation.md)**: Remediation escalation runbook.
- **[Rollback](csi-rebuild/runbooks/03_rollback.md)**: Rollback runbook.
- **[Data Repair](csi-rebuild/runbooks/04_data_repair.md)**: Data repair runbook.
- **[Quick Commands](csi-rebuild/runbooks/05_quick_commands.md)**: Quick command reference.

### 9. [SDK Phases and Integration Docs](.)

- **[Threads Infisical Sync Workflow](004_THREADS_INFISICAL_SYNC_WORKFLOW.md)**: Infisical sync workflow.
- **[CSI YouTube Proxy Usage Audit](005_CSI_YOUTUBE_PROXY_USAGE_AUDIT.md)**: Proxy usage audit.
- **[CSI Trend Analysis Functional Review and Plan](006_CSI_TREND_ANALYSIS_FUNCTIONAL_REVIEW_AND_PLAN.md)**: Trend analysis review.
- **[CSI Persistence Briefing and Reminder Operations](007_CSI_PERSISTENCE_BRIEFING_AND_REMINDER_OPERATIONS.md)**: Persistence operations.
- **[Threads Rollout Next Phases](008_THREADS_ROLLOUT_NEXT_PHASES.md)**: Threads rollout phases.
- **[Threads Phase 2 Permission Readiness Runbook](009_THREADS_PHASE2_PERMISSION_READINESS_RUNBOOK.md)**: Permission readiness.
- **[Threads Phase 2/3 Closeout Go/No-Go Report](010_THREADS_PHASE2_3_CLOSEOUT_GO_NO_GO_REPORT.md)**: Phase closeout report.
- **[SDK Phase 5 Accelerated Canary Rollout](011_SDK_PHASE5_ACCELERATED_CANARY_ROLLOUT.md)**: Canary rollout.
- **[NotebookLM Integration](012_NOTEBOOKLM_INTEGRATION.md)**: NotebookLM integration notes.

### 10. [Reference Docs](.)

- **[ZAI OpenAI Compatible Setup](ZAI_OPENAI_COMPATIBLE_SETUP.md)**: ZAI API setup guide.
- **[Durability Test Matrix](durability_test_matrix.md)**: Durability testing matrix.
- **[NotebookLM VPS Infisical Runbook](notebooklm_vps_infisical_runbook.md)**: NotebookLM VPS setup.

### 11. [Reports](reports)

- **[Todoist Task Pipeline Audit (2026-03-11)](reports/todolist-task-pipeline-audit-2026-03-11.md)**: Todoist pipeline audit.

---

## 🚀 Recommended Path for New Developers

1. Check the **[Glossary](Glossary.md)** whenever you encounter a project-specific term.

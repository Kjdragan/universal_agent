# Documentation Status

**Last updated:** 2026-04-05

## ⚠️ MANDATORY: Documentation Rules

This file and `docs/README.md` serve as the **authoritative indexes** for all project documentation.

- All documentation MUST be located strictly within the `docs/` directory.
- Always check this index to update existing documents before creating new ones.
- **Rule:** If you create a new documentation file, you MUST log it and link it in this file and in `docs/README.md`.

## Subsystems (02_Subsystems/)

| Doc | Subject |
|-----|---------| 
| Heartbeat_Service.md | **Canonical source of truth** — heartbeat supervision cycle, health findings contract, mediation flow, and the separation between heartbeat runtimes and dedicated ToDo execution |
| Proactive_Pipeline.md | **Canonical source of truth** — end-to-end proactive pipeline: trusted email triage, Task Hub scoring, dedicated ToDo dispatch lifecycle, delivery-mode heuristics, brainstorm refinement, morning report, roadmap, and test coverage |
| Task_Hub_Dashboard.md | **Canonical source of truth** — frontend design system (`kcd-*` palette, glassmorphism), Kanban component architecture, embedded Agent Flow widget, lightweight spotlight persistence contract, dispatcher health, forensic task history, and derived board-lane UX |
| Memory_System.md | Tiered memory & auto-flush |
| Lossless_Memory.md | Opt-in DAG-based context compression and SQLite history store |
| Durable_Execution.md | Resilience features |
| URW_Orchestration.md | Multi-phase tasks |
| LLM_Wiki_System.md | **Canonical source of truth** — shared wiki engine, external knowledge vault, derived internal memory vault, runtime surfaces, provenance, and integrity/query workflows |

## Architecture (01_Architecture/)

Written from source code review — these describe the system as it actually exists.

| Doc | Subject |
|-----|---------|
| 01 | System Architecture Overview — component map, services, data stores, deployment topology |
| 02 | Gateway, Sessions & Execution — session model, auth surfaces, execution engine, background services |
| 03 | VP Workers & Delegation — VP lanes (CODIE & ATLAS), mission lifecycle, cross-machine delegation, factory heartbeat |
| 04 | Dual Factory and Capability Expansion Brainstorm |
| 05 | Simone-First Orchestration — batch triage, VP delegation lifecycle, two-layer email response, `/btw` sidebar |
| 06 | Comparison: Background Workers vs Simone Orchestration — Deep-dive evaluation of pull-based agent workers vs the event-driven Simone-First orchestration model |
| 07 | Database Architecture — absolute source of truth for database paradigms, schema structure, segregation boundaries, and lifecycle pruning logic |
| 10 | Model Choice and Resolution — Anthropic proxy emulation map, inference fallbacks, and the Capacity Governor loud-failure hook |

## Root Architecture Docs

| Doc | Subject |
|-----|---------|
| 000 | Pipeline Masterpiece — holistic end-to-end view of input ingress, task hub state machine, Simone orchestration, and the autonomous reflection engines |
| 001 | Agent Architecture (agent_core.py vs main.py) — entry points, AgentSetup sync, URW session reuse |
| 002 | SDK Permissions, Hooks & Subagents — permission model, hooks architecture, subagent patterns |
| 003 | Regression Control and Golden Runs — testing stability, golden run preservation |

## Canonical Source-of-Truth Documents (03_Operations/)

These are the authoritative references for each subsystem. When any other document conflicts, **the canonical doc wins**.

| # | Subject |
|---|---------|
| 07 | WebSocket Architecture (`02_Flows/`) |
| 08 | Auth & Session Security (`02_Flows/`) |
| 82 | Email / AgentMail |
| 83 | Webhooks |
| 85 | Infisical Secrets |
| 86 | Residential Proxy |
| 87 | Tailscale |
| 88 | Factory Delegation, Heartbeat & Registry |
| 89 | Runtime Bootstrap, Deployment Profiles & Factory Role |
| 90 | Artifacts, Workspaces & Remote Sync |
| 91 | Telegram |
| 92 | CSI Architecture |
| 96 | NotebookLM Integration & Research Pipeline |
| 97 | Infisical CLI Reference & Lessons Learned |
| 98 | Agent Skills Directory (`.agents/skills/`) |
| 99 | Documentation Drift Maintenance Pipeline — two-stage nightly audit (heuristic drift detection → VP remediation dispatch), PR persistence, issue batching, verify-before-fix rules |
| 100 | OpenClaw Release Sync Pipeline |
| 101 | VP Agent Identity & Prompt Architecture — CODIE/ATLAS souls, VP system prompt, mission briefing injection
| 102 | E2BIG Kernel Limits and Prompt Architecture — 128KB execution limit constraints and prompt optimization strategy
| 104 | Run/Attempt Lifecycle and Nomenclature Migration Plan — canonical migration plan for run/attempt terminology, packet rollout, rollback scope, and CSI boundary handling |
| 105 | YouTube Tutorial Pipeline Run/Attempt Triage — notification-center audit of the tutorial hook flow after the refactor, plus the closure update that migrated tutorial hooks and cron admission onto durable run/attempt lifecycle handling |
| 107 | Task Hub and Multi-Channel Execution Master Reference — central code-verified map of ingress channels, To Do execution, run workspaces, specialist delegation, delivery, lifecycle events, and Session Explorer file resolution |
| 109 | LLM Wiki Implementation Status — living tracker for subsystem progress, blockers, verified tests, and current implementation state |
| 108 | Gateway Test Hardening And Regression Follow-Up — operational handoff for the April gateway regression pass, including what was fixed, what remains, and how to continue pruning stale tests while development resumes |

## CSI Subsystem (04_CSI/)

| Doc | Subject |
|-----|----|
| CSI_Master_Architecture.md | **Canonical living doc** — domain taxonomy, SQLite source management, quality scoring, adapter architecture, batch processing, delivery, subsystem boundaries. Supersedes scattered CSI docs for architecture reference |

## Deployment Canonical Docs (deployment/)

| Doc | Subject |
|-----|---------|
| secrets_and_environments.md | **Canonical entry-point** — Infisical secrets, environments, deploy workflow secrets contract |
| architecture_overview.md | Git branching, environmental mapping, service topology, and SHA-based release verification |
| ci_cd_pipeline.md | Workflow details, timing, pipeline structure, and SHA-first post-release verification |
| infisical_factories.md | Stage naming and machine bootstrap (superseded by secrets_and_environments.md) |

## Review & Decision Documents

| # | Subject |
|---|---------|
| 93 | Prioritized Cleanup Plan from Canonical Review |
| 94 | Architectural Integration Review |
| 95 | Heartbeat Issue Mediation and Auto-Triage |

## Deployment and Environment Continuity Docs (06_Deployment_And_Environments/)

| Doc | Subject |
|-----|---------|
| 04 | Branching and release workflow, including return-to-feature-branch operator rule and SHA-based release confirmation |
| 05 | Local HQ dev vs desktop worker runtime modes |
| 06 | Production deploy incident |
| 07 | Stage-based Infisical and machine bootstrap migration plan |
| 08 | VPS deployment profile stuck at `local_workstation` — final incident record covering the wrong-host false lead and the gateway env-sanitization fix |

## Run Reviews (03_Run_Reviews/)

| # | Subject |
|---|---------|
| 01 | Russia Ukraine Report Session (2026-02-06) |
| 02 | Cron Breakthrough Briefing (2026-02-08) |
| 03 | Scheduling Runtime V2 Short Soak Readiness (2026-02-08) |
| 04 | Scheduling Runtime V2 24h Soak In Progress (2026-02-08) |
| 05 | Self-Improving Agent Notes (2026-02-13) |
| 06 | Final Integration Sprint Review (2026-02-14) |
| 07 | Happy Path Review and Markdown Remediation (2026-02-19) |

## CSI Rebuild (csi-rebuild/)

| Doc | Subject |
|-----|---------|
| 00_master_plan.md | CSI rebuild master plan |
| 01_architecture_v2.md | Revised CSI architecture |
| 02_interfaces_and_schemas.md | CSI interfaces and schemas |
| 03_rollout_and_runbooks.md | Rollout plan and runbook index |
| 04_validation_matrix.md | Validation test matrix |
| 05_incident_log.md | Incident tracking log |
| 06_packet_handoff.md | Packet handoff documentation |
| 07_post_packet10_work_phases.md | Post-packet work phases |
| 08_rc_soak_ga_gate.md | Release candidate soak gate |
| status.md | CSI rebuild status tracker |
| kevin_handoff_for CSI.md | Handoff documentation |

## CSI Rebuild Runbooks (csi-rebuild/runbooks/)

| Doc | Subject |
|-----|---------|
| 01_incident_triage.md | Incident triage runbook |
| 02_remediation_escalation.md | Remediation escalation runbook |
| 03_rollback.md | Rollback runbook |
| 04_data_repair.md | Data repair runbook |
| 05_quick_commands.md | Quick command reference |

## SDK Phases and Integration Docs

| Doc | Subject |
|-----|---------|
| 004 | Threads Infisical Sync Workflow |
| 005 | CSI YouTube Proxy Usage Audit |
| 006 | CSI Trend Analysis Functional Review and Plan |
| 007 | CSI Persistence Briefing and Reminder Operations |
| 008 | Threads Rollout Next Phases |
| 009 | Threads Phase 2 Permission Readiness Runbook |
| 010 | Threads Phase 2/3 Closeout Go/No-Go Report |
| 011 | SDK Phase 5 Accelerated Canary Rollout |
| 012 | NotebookLM Integration |

## Reference Docs

| Doc | Subject |
|-----|---------|
| ZAI_OPENAI_COMPATIBLE_SETUP.md | ZAI API setup guide |
| durability_test_matrix.md | Durability testing matrix |
| notebooklm_vps_infisical_runbook.md | NotebookLM VPS setup |
| Glossary.md | Project terminology |

## Reports (reports/)

| Doc | Subject |
|-----|---------|
| todolist-task-pipeline-audit-2026-03-11.md | Historical audit of the early `/dashboard/todolist` pipeline before the dedicated ToDo dispatcher refactor |

## Handoffs

| Doc | Subject |
|-----|---------|
| coding_handoff.md | Code-verified implementation handoff for the run-per-task workspace refactor across tracked chat, trusted email, dispatcher claims, run/attempt lineage, Session Explorer resolution, acceptance criteria, and validation steps |

## Remaining Operational References (03_Operations/)

| Doc | Subject |
|-----|---------|
| 01 | Heartbeat Debug Fixes — historical debug reference |
| 02 | Browser Debugging Lessons — debugging patterns, browser-profile comparison, and targeted storage-key resets |
| 11 | Scheduling Runtime V2 Operational Runbook — cron operations |
| 13 | Skill Dependency Setup Guide — skill installation |
| 14 | Session Runtime Behavior And Recovery Model |
| 15 | Execution Lock Concurrency Architecture |
| 16 | Concurrency Conflict Root Cause — VP general interim path |
| 19 | VPS App/API/Telegram Deployment Explainer |
| 20 | VPS Daily Ops Quickstart |
| 22 | VPS Remote Dev, Deploy And File Transfer Runbook |
| 23 | Agent Workspace Inspector Skill |
| 24 | VPS Service Recovery System Runbook |
| 26 | VPS Host Security Hardening Runbook |
| 27 | Deployment Runbook |
| 30 | Memory System Architecture And Health |
| 31 | VPS Deployment Decision Tree |
| 32 | VPS FileBrowser Setup And Access |
| 76 | Sandbox Permissioning And Exception Profile |
| 78 | Daily Autonomous Briefing Reliability |
| 79 | Golden Run Research Report Pipeline Reference |
| 80 | Google Workspace Integration Retrospective Memo |
| 99 | **Documentation Drift Maintenance Pipeline** — canonical source-of-truth |
| 103 | Debugging Lessons Living Document — reusable debugging lessons from complex production incidents, including the April 5 dashboard browser-state incident and SHA-first deploy verification |
| 106 | TaskStop Guardrails and Task Hub Execution Hardening — explainer for the March 31 hardening work: SDK task control versus Task Hub lifecycle, run-aware `TaskStop` blocking, corrective guidance, and the resulting pipeline reliability improvements |

## Cleanup Summary

**72 outdated documents** were deleted on 2026-03-06:
- 6 stale architecture docs (01_Architecture/) — replaced by 3 new docs written from source code
- 46 implementation plans, verifications, assessments, and handoffs for deployed systems
- 20 superseded operational docs covered by canonical source-of-truth documents

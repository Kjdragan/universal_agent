# Documentation Status

**Last updated:** 2026-03-12

## ⚠️ MANDATORY: Documentation Rules

This file and `docs/README.md` serve as the **authoritative indexes** for all project documentation.

- All documentation MUST be located strictly within the `docs/` directory.
- Always check this index to update existing documents before creating new ones.
- **Rule:** If you create a new documentation file, you MUST log it and link it in this file and in `docs/README.md`.

## Architecture (01_Architecture/)

Written from source code review — these describe the system as it actually exists.

| Doc | Subject |
|-----|---------|
| 01 | System Architecture Overview — component map, services, data stores, deployment topology |
| 02 | Gateway, Sessions & Execution — session model, auth surfaces, execution engine, background services |
| 03 | VP Workers & Delegation — mission lifecycle, cross-machine delegation, factory heartbeat |

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

## Review & Decision Documents

| # | Subject |
|---|---------|
| 93 | Prioritized Cleanup Plan from Canonical Review |
| 94 | Architectural Integration Review |
| 95 | Integration Architectural Decisions (ADRs) |

## Deployment and Environment Continuity Docs

| Doc | Subject |
|-----|---------|
| 01 | Deployment architecture continuity pointer |
| 02 | Stage-based Infisical environment and factory bootstrap continuity |
| 03 | Branch-driven CI/CD continuity |
| 04 | Branching and release workflow |
| 05 | Local HQ dev vs desktop worker runtime modes |
| 06 | Production deploy incident |
| 07 | Stage-based Infisical and machine bootstrap migration plan |

## Remaining Operational References

| Doc | Subject |
|-----|---------|
| 01 | Heartbeat Debug Fixes — historical debug reference |
| 02 | Browser Debugging Lessons — debugging patterns |
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
| 41 | Todoist Heartbeat And Triage Operational Runbook |
| 76 | Sandbox Permissioning And Exception Profile |
| 77 | CSI Todoist Sync Debugging Lessons |
| 78 | Daily Autonomous Briefing Reliability |
| 79 | Golden Run Research Report Pipeline Reference |
| 80 | Google Workspace Integration Retrospective Memo |

## Cleanup Summary

**72 outdated documents** were deleted on 2026-03-06:
- 6 stale architecture docs (01_Architecture/) — replaced by 3 new docs written from source code
- 46 implementation plans, verifications, assessments, and handoffs for deployed systems
- 20 superseded operational docs covered by canonical source-of-truth documents

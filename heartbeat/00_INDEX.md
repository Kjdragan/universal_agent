# Heartbeat Project (Universal Agent)

## Purpose
This folder is the **single source of truth** for the Universal Agent “heartbeat” feature work.

Heartbeat (in this project) means: **periodic, context-aware agent turns** that can surface proactive alerts, do lightweight maintenance work, and optionally deliver a message (or stay quiet) based on an explicit suppression contract.

## How to use this folder (living docs)
These documents are **living**:
- Update them when design decisions change.
- Append progress notes as implementation proceeds.
- Keep the index current so we can quickly re-orient in future sessions.

## Documents
- `00_INDEX.md`
  - This file.
- `01_PROGRESS.md`
  - Current status, milestones, and next actions.
- `02_DECISIONS.md`
  - Architectural decisions and rationale.
- `03_UA_Heartbeat_Schema_and_Event_Model.md`
  - Proposed configuration schema and event model.
- `04_Gateway_Scheduler_Prototype_Design.md`
  - Minimal prototype design showing how the scheduler integrates into the gateway lifecycle.
- `05_Clawdbot_Heartbeat_System_Report.md`
  - Clawdbot heartbeat behavior analysis + what it implies for UA.
- `06_WebSocket_Broadcast_and_Option_C.md`
  - WebSocket broadcast implications + Option C comparison.
- `07_Telegram_UI_Investigation.md`
  - Review of current Telegram UI integration gaps.
- `08_Telegram_Revival_and_Enhancement_Plan.md`
  - Revival + enhancement plan for Telegram.
- `09_Deployment_Strategy_and_Railway.md`
  - Deployment options and Railway checklist.
- `10_Implementation_Plan.md`
  - Phased heartbeat + Telegram implementation plan.
- `11_Clawdbot_Memory_System_Report.md`
  - Clawdbot memory architecture and behavior.
- `12_UA_Memory_Feasibility_and_Implementation.md`
  - UA feasibility and adoption plan for file-based memory.
- `13_Memory_Migration_Checklist.md`
  - Letta → file-based memory migration checklist.

## Conventions
- **Numbered prefixes** are intentional so docs have stable ordering.
- Prefer **explicit references** to code/files (paths, key types) when relevant.
- When proposing behavior that requires code changes, clearly label it as:
  - **Prototype** (minimum viable)
  - **Target** (intended mature design)


# Unified Todoist + System Command UI Implementation Plan

Last updated: 2026-02-25
Owner: UA platform
Status: Phase implementation in progress (through Phase 5 baseline)

## 1) Objectives

1. Make Todoist the canonical scheduling and backlog ledger for system work.
2. Add a shared natural-language system command input for all non-chat tabs.
3. Keep chat-tab input session-scoped by default to avoid accidental config/scheduler actions.
4. Let heartbeat proactively execute eligible Todoist work when idle.
5. Produce a daily 7:00 AM briefing summarizing autonomous work completed without direct user prompting.
6. Emit UI notifications whenever Simone/UA completes an independent task.
7. Support multiple proactive drivers: user-issued system commands, Todoist due tasks, Chron schedules, and heartbeat opportunistic execution.

## 2) UX and Routing Contract

1. Chat tab:
- Input is session lane by default.
- Session guidance and in-flight task direction happen here.
- System actions can be supported later via explicit confirmation.

2. Non-chat tabs:
- Shared system command component (single natural-language input).
- Interprets scheduling/config/ops intents.
- No calendar-style forms required for baseline workflow.

3. Routing lanes:
- `session_lane`: active run/session context.
- `system_lane`: Todoist/Chron/ops/config actions.

## 3) Core Architecture

1. Intent interpreter:
- Accept free-form text.
- Produce deterministic action envelope with `intent`, `target`, `schedule`, `priority`, `metadata`.

2. Todoist canonical model:
- Store capture/schedule/progress state in Todoist projects/sections/labels.
- Persist execution metadata for round-trip status updates.

3. Chron bridge:
- Convert eligible Todoist tasks to Chron jobs.
- Keep idempotent mapping (`todoist_task_id` <-> `cron_job_id`).

4. Heartbeat orchestrator:
- Every heartbeat tick checks idle state + Todoist backlog.
- Picks one allowed task when safe and capacity exists.
- Executes autonomously and writes status/results back to Todoist + notifications.
- Heartbeat cadence remains independent of Todoist due-times. Heartbeat is a proactive evaluator, not a replacement scheduler.

5. Scheduling model split (explicit):
- Todoist scheduling: user/task-specific due intent (`run at 2am`, `in 1h`, recurring reminders).
- Chron scheduling: execution engine for concrete run windows and recurring jobs.
- Heartbeat scheduling: periodic health/proactivity cycle (e.g., every 30 minutes) that may opportunistically execute eligible Todoist work when no higher-priority activity is active.

6. Notification/reporting:
- Independent completion => dashboard notification with artifact links.
- 7:00 AM daily autonomous briefing from prior 24h activity.

## 4) Proactive Autonomy Policy (Heartbeat + Chron)

1. Eligibility gates for autonomous execution:
- Task is in allowed section/label (`agent-ready`, not blocked).
- Task is not marked `manual_gate`.
- Required credentials/capabilities are available.
- No conflicting active workload beyond configured threshold.

2. Selection policy:
- Priority first, then urgency/due time, then mission alignment.
- One task per heartbeat cycle initially (conservative rollout).

3. Safety:
- Hard skip destructive/high-risk actions unless explicitly approved.
- Clear failure reason written to Todoist and UA notification stream.

4. Mission alignment:
- Heartbeat prompt/rules updated so “work through Todoist backlog” is explicit mission behavior.

## 5) 7:00 AM Daily Briefing Requirement

1. Create a daily Chron job at 07:00 local timezone.
2. Job compiles:
- Autonomous tasks started/completed/failed in previous 24 hours.
- Artifact links and short outcome summaries.
- Any blocked items requiring user decision.
3. Emit:
- Dashboard notification (`autonomous_daily_brief_ready`).
- Durable report artifact under UA artifacts.

## 6) Phase Plan

## Phase 0 - Planning and spec lock
Deliverables:
1. This plan set and decision log.
2. Data model for task metadata and routing envelope.

Acceptance:
1. Scope boundaries agreed (chat lane vs system lane).
2. Proactive execution and briefing policy agreed.

## Phase 1 - Shared system command UI (non-chat tabs)
Deliverables:
1. Reusable command component.
2. Context injection from active page.
3. Basic action feedback panel.

Acceptance:
1. Works across non-chat tabs with same backend contract.

## Phase 2 - Intent interpreter + Todoist routing
Deliverables:
1. Natural-language intent parser for system lane.
2. Todoist create/update/quick-add routing and validation.

Acceptance:
1. Scheduling text works without manual forms.

## Phase 3 - Todoist -> Chron bridge
Deliverables:
1. Deterministic conversion + idempotency mapping.
2. Status write-back from Chron runs to Todoist.

Acceptance:
1. Scheduled tasks run from Todoist state reliably.

## Phase 4 - Heartbeat proactive worker integration
Deliverables:
1. Idle-aware task pickup logic.
2. Mission-aware task selection.
3. Autonomous completion/failure notifications.

Acceptance:
1. Heartbeat can complete safe eligible tasks autonomously.

## Phase 5 - 7:00 AM autonomous briefing
Deliverables:
1. Daily Chron report job.
2. Notification + report artifact links.

Acceptance:
1. Daily briefing appears at 7:00 AM and reflects previous 24h autonomous activity.

## 7) Non-goals (initial rollout)

1. Voice transcription implementation (handled by external service).
2. Fully unified single input across chat + system without lane safeguards.
3. Advanced calendar UI forms.

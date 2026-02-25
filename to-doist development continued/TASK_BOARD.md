# Task Board (Dynamic)

Last updated: 2026-02-25

## Phase 0 - Planning

- [x] Confirm existing Todoist integration surfaces
- [x] Confirm heartbeat/Chron integration surfaces
- [x] Lock UX lane strategy (chat=session, non-chat=system)
- [x] Add proactive heartbeat + 7:00 AM briefing requirements into plan
- [x] Finalize action envelope schema + validation rules
- [x] Finalize autonomous eligibility matrix

## Phase 1 - Shared System Command UI

- [x] Create reusable non-chat command component
- [x] Add to dashboard layout (non-chat tabs)
- [x] Wire endpoint + success/error feedback
- [x] Add context injection by page

## Phase 2 - Interpreter + Todoist Routing

- [x] Implement interpreter endpoint
- [x] Add Todoist structured routing
- [x] Add deterministic response summaries
- [x] Add tests for schedule parsing and intent mapping

## Phase 3 - Todoist <-> Chron Bridge

- [x] Add baseline mapping metadata (`todoist_task_id` in Chron metadata)
- [x] Add persisted idempotent upsert mapping store
- [x] Add run-result visibility for autonomous runs via notifications
- [x] Add reconciliation pass for drift

## Phase 4 - Heartbeat Proactive Work

- [x] Add mission directive for Todoist backlog advancement
- [x] Add idle/capacity guard
- [x] Add single-task-per-cycle execution policy
- [x] Emit independent completion/failure notifications (baseline)

## Phase 5 - Daily Autonomous Briefing

- [x] Implement deterministic briefing aggregator service
- [x] Add 07:00 daily Chron job bootstrap/update
- [x] Write report artifact + notification link
- [ ] Validate previous-24h coverage and edge cases

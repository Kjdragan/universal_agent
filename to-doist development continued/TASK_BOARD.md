# Task Board (Dynamic)

Last updated: 2026-02-24

## Phase 0 - Planning

- [x] Confirm existing Todoist integration surfaces
- [x] Confirm heartbeat/Chron integration surfaces
- [x] Lock UX lane strategy (chat=session, non-chat=system)
- [x] Add proactive heartbeat + 7:00 AM briefing requirements into plan
- [ ] Finalize action envelope schema + validation rules
- [ ] Finalize autonomous eligibility matrix

## Phase 1 - Shared System Command UI

- [ ] Create reusable non-chat command component
- [ ] Add to Dashboard/Cron/Tutorial/Storage tabs
- [ ] Wire endpoint + success/error feedback
- [ ] Add context injection by page

## Phase 2 - Interpreter + Todoist Routing

- [ ] Implement interpreter endpoint
- [ ] Add Todoist quick-add/structured routing
- [ ] Add deterministic response summaries
- [ ] Add tests for schedule parsing and intent mapping

## Phase 3 - Todoist <-> Chron Bridge

- [ ] Add mapping persistence (`todoist_task_id` -> `cron_job_id`)
- [ ] Add idempotent upsert behavior
- [ ] Add run result write-back to Todoist
- [ ] Add reconciliation pass for drift

## Phase 4 - Heartbeat Proactive Work

- [ ] Add mission directive for Todoist backlog advancement
- [ ] Add idle/capacity guard
- [ ] Add single-task-per-cycle execution policy
- [ ] Emit independent completion/failure notifications

## Phase 5 - Daily Autonomous Briefing

- [ ] Implement briefing aggregator service
- [ ] Add 07:00 daily Chron job bootstrap/update
- [ ] Write report artifact + notification link
- [ ] Validate previous-24h coverage and edge cases


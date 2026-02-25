# Build Status

Last updated: 2026-02-25

## Progress Dot

Current phase: Phase 5 (Daily briefing + guard hardening baseline complete)
Overall completion: 75%
Health: On track

## Completed This Session

1. Confirmed existing Todoist tool/service integration points in runtime.
2. Confirmed existing heartbeat pre-step Todoist summary/candidate behavior.
3. Confirmed Chron supports natural schedule parsing (`in 1h`, `tomorrow 2am`, etc.).
4. Locked UX boundary:
- Chat tab => session lane.
- Non-chat tabs => shared system command lane.
5. Added explicit requirements:
- Proactive heartbeat task pickup from Todoist.
- 7:00 AM daily autonomous briefing.
- Independent-run notifications in dashboard.
6. Clarified scheduling split:
- Todoist due-intent scheduling is separate from heartbeat periodic proactivity cadence.
7. Completed Phase 0 artifacts:
- `SCHEMA.md`
- `ELIGIBILITY_MATRIX.md`
8. Implemented shared non-chat system command bar in dashboard layout.
9. Implemented baseline `/api/v1/dashboard/system/commands` routing (status, idea capture, task capture/schedule).
10. Added baseline autonomous completion notifications for Chron + heartbeat significant completions.
11. Added bootstrap/update logic for 7:00 AM autonomous daily briefing Chron job.
12. Added regression tests for command parsing/routing and autonomous notification classification.
13. Added persisted Todoist<->Chron mapping store (`AGENT_RUN_WORKSPACES/todoist_chron_mappings.json`) and idempotent reuse/update behavior.
14. Added deterministic autonomous daily briefing artifact generator (`UA_ARTIFACTS_DIR/autonomous-briefings/<day>/DAILY_BRIEFING.md`) with JSON companion.
15. Added report links in autonomous daily briefing notifications metadata (`report_api_url`, `report_storage_href`).
16. Added deeper source context normalization + capture from dashboard system command bar (route/query/selection/timezone).
17. Added heartbeat guard policy enforcement in code (autonomous enable/disable, actionable capacity cap, system-event cap, one-task-per-cycle shortlist).
18. Added regression tests for idempotent mapping reuse, briefing link emission, and heartbeat guard policy.
19. Validation run: targeted gateway + heartbeat/todoist related tests passing.

## Next Actions

1. Add richer briefing sections for non-cron autonomous artifacts outside `artifacts/cron/<job_id>`.
2. Expand tests for end-to-end drift reconciliation behavior with live service wiring.

## Risks / Decisions Needed

1. Timezone source of truth for 7:00 AM report (per user profile vs system default).
2. Concurrency caps for autonomous execution when background workloads exist.
3. Scope of “independent” for briefing inclusion (heartbeat-only vs heartbeat+cron+hook initiated).

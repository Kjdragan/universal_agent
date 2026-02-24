# Decision Log

Last updated: 2026-02-24

## Accepted

1. Input modality:
- Natural-language text entry only (typed or externally transcribed voice text).

2. UI lane separation (initial rollout):
- Chat tab input is session-oriented.
- Shared system command component appears on non-chat tabs.

3. Todoist role:
- Canonical backlog + scheduling ledger for system work.

4. Execution role split:
- Todoist stores intent/schedule/state.
- Chron executes due jobs.
- Heartbeat performs proactive selection/execution when idle and allowed.

5. Proactive autonomy:
- Simone/UA may execute eligible Todoist tasks without direct user prompt, based on heartbeat policy.

6. Visibility requirements:
- Every independent completion emits a dashboard notification with links.
- Daily 7:00 AM briefing summarizes autonomous work.

7. Safety policy:
- Manual gates and blocked labels are respected before autonomous runs.

## Open

1. Whether chat tab can later optionally dispatch system-lane commands with a route confirmation.
2. Exact Todoist labels/metadata keys for `manual_gate`, `autonomous_ok`, and `requires_review`.
3. Whether 7:00 AM briefing should also be delivered to Telegram/email by default.


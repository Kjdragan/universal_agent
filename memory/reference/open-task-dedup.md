# Open-task dedup before anomaly deep-dives

Before deep-diving ANY anomaly (a wedge, a timeout, a failing pipeline), spend ONE query
checking whether a fix task already covers it:

```bash
sqlite3 /opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db \
  "SELECT task_id, status, title FROM task_hub_items
   WHERE status IN ('open','in_progress','blocked','needs_review','delegated','pending_review');"
```

Scan the titles for the subsystem in question. If a match exists: write findings that
reference the existing task_id and STOP. The diagnosis budget belongs to the task's
assignee (usually Cody), not the heartbeat.

Why this rule exists: on 2026-07-26 (02:38 UTC) a heartbeat spent its entire 1200s exec
budget re-diagnosing the cron-dispatch wedge that already had a queued fix task
(task_3773b30ae294, assigned to Cody), tripping the exec-timeout backstop before writing
findings. Simone review ntf_1785119900434_501 classified it known_rule_only and named
this directive as the fix. UA_HEARTBEAT_EXEC_TIMEOUT was deliberately NOT raised —
that would treat the symptom, not the behavior.

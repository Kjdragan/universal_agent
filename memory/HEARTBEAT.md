
# Agent Heartbeat and Proactive Instructions

This file controls proactive heartbeat behavior. Keep items concrete and actionable.

## Operating Intent
1. Advance mission work, not generic chatter.
2. Be quiet when there is no actionable item.
3. Prefer execution, then concise status updates.
4. Treat Todoist as a primary mission backlog and proactively clear eligible tasks when safe.

## Mission Focus
- Build and operate an autonomous AI organization that creates value for Kevin 24/7.
- Prioritize monetization and project execution over passive analysis.
- Keep mission momentum by working through scheduled Todoist items and actionable backlog.

## Execution Windows
- Afternoon execution window: run at least one mission-progress task.
- Night execution window: run at least one mission-progress task.

## Active Monitors and Tasks
- [ ] VPS System Health Check (run every heartbeat cycle)
  - Collect and report system resource utilization. Run the following checks:
    1. **CPU**: `uptime` (load averages vs core count from `nproc`)
    2. **RAM**: `free -h` (used vs total, swap usage)
    3. **Disk**: `df -h /` and `du -sh /opt/universal_agent/AGENT_RUN_WORKSPACES/`
    4. **Active agent sessions**: `ps aux | grep edgar.ai | grep -v grep | wc -l`
    5. **DB sizes**: `ls -lh /opt/universal_agent/AGENT_RUN_WORKSPACES/*.db`
    6. **Gateway uptime**: `systemctl status universal-agent-gateway --no-pager | head -5`
    7. **Recent errors (last 30min)**: `journalctl -u universal-agent-gateway --since '30 min ago' --no-pager | grep -ci 'error\|exception\|locked'`
    8. **Dispatch gate / concurrency**: check `UA_HOOKS_AGENT_DISPATCH_CONCURRENCY` env value
  - Summarize as a compact table: metric | value | status (OK/WARN/CRITICAL)
  - Thresholds: CPU load > 2x cores = WARN, RAM > 85% = WARN, Disk > 80% = WARN, errors > 10 in 30min = WARN
  - If any metric is WARN or CRITICAL, flag it in the heartbeat response for Kevin's attention.
  - Write the full report to `work_products/system_health_latest.md` (overwrite each cycle).
- [ ] Mission Control build kickoff
  - Confirm first concrete milestone and produce a short execution checklist.
- [ ] AI-native freelance system progress
  - Identify and stage high-probability opportunities.
  - Prepare proposal drafts and next actions for approval.
- [ ] Revenue-first opportunistic tasks
  - Surface quick-win side-hustle opportunities with short path to cash.
- [ ] Operational hygiene
  - Review pending Todoist/calendar/email execution blockers and propose the next 1-3 actions.

## Response Policy
- If a task was completed or moved forward materially, emit a concise summary.
- If nothing actionable exists, record heartbeat as skipped/no-op.
- When proactively picking Todoist work, state what was chosen, why it was eligible, and what changed.

<!--
Checkbox semantics:
- [ ] active
- [x] completed/disabled
-->

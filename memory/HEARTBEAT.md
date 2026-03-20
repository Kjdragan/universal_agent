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
## execution windows
- Afternoon execution window: run at least one mission-progress task.
- Night execution window: run at least one mission-progress task
## Active Monitors and Tasks
<!-- scope:hq -->
- [ ] VPS System Health Check (run every heartbeat cycle — target: `srv1360701.taildcc090.ts.net` via Tailscale SSH as user `ua`, hosted on Hostinger VPS `srv1360701.hstgr.cloud`) - Collect and report system resource utilization. Run the following checks:
    1. **CPU**: `uptime` (load averages vs core count from `nproc`)
    2. **RAM**: `free -h` (used vs total, swap usage)
    3. **Disk**: `df -h /` and `du -sh /opt/universal_agent/AGENT_RUN_WORKSPACES/`
    4. **Active agent sessions**: `ps aux | grep edgar.ai | grep -v grep | wc -l`
    5. **DB sizes**: `ls -lh /opt/universal_agent/AGENT_RUN_WORKSPACES/*.db`
    6. **Gateway uptime**: `systemctl status universal-agent-gateway --no-pager | head -5`
    7. **Recent errors (last 30min)**: `journalctl -u universal-agent-gateway --since '30 min ago' --no-pager | grep -ci 'error|exception|locked'`
    8. **Dispatch gate / concurrency**: check `UA_HOOKS_AGENT_DISPATCH_CONCURRENCY` env value
  - Summarize as a compact table: metric | value | status (OK/WARN/CRITICAL)
  - Thresholds: CPU load > 2x cores = WARN, RAM > 85% = WARN, Disk > 80% = WARN, errors > 50 in 30min = WARN
  - If any metric is WARN or CRITICAL, flag it in the heartbeat response for Kevin's attention.
  - Write the full human-readable report to `work_products/system_health_latest.md` (overwrite each cycle).
    - Also write a machine-readable findings contract to `work_products/heartbeat_findings_latest.json` (overwrite each cycle)
    - The JSON contract must use this schema:
    ```json
    {
      "version": 1,
      "overall_status": "ok|warn|critical",
      "generated_at_utc": "ISO-8601 UTC timestamp",
      "source": "vps_system_health_check",
      "summary": "Short one-paragraph summary of the most important finding set.",
      "findings": [
        {
          "finding_id": "stable_snake_case_id",
          "category": "gateway|system|disk|memory|cpu|dispatch|database|unknown",
          "severity": "ok|warn|critical",
          "metric_key": "recent_errors_30m",
          "observed_value": 67,
          "threshold_text": ">50",
          "known_rule_match": true,
          "confidence": "low|medium|high",
          "title": "Gateway Errors Elevated",
          "recommendation": "Inspect gateway logs for root cause.",
          "runbook_command": "journalctl -u universal-agent-gateway --since '30 min ago' --no-pager",
          "metadata": {
            "service": "universal-agent-gateway"
          }
        }
      ]
    }
    ```
  - Include at least one `findings[]` entry whenever `overall_status` is `warn` or `critical`.
  - Use `known_rule_match=true` only when the issue clearly maps to a stable runbookable condition. Unknown edge cases should still be emitted with `known_rule_match=false`.
<!-- scope:local -->
- [ ] Local Desktop Health Check (run every heartbeat cycle) - Monitor the local machine running this agent instance:
    1. **CPU**: `uptime` (load averages vs core count from `nproc`)
    2. **RAM**: `free -h` (used vs total, swap usage)
    3. **Disk**: `df -h /` and `du -sh AGENT_RUN_WORKSPACES/`
    4. **Gateway process**: `ps aux | grep gateway_server | grep -v grep | wc -l`
    5. **DB sizes**: `ls -lh AGENT_RUN_WORKSPACES/*.db`
  - Summarize as a compact table: metric | value | status (OK/WARN/CRITICAL)
  - Thresholds: CPU load > 2x cores = WARN, RAM > 85% = WARN, Disk > 80% = WARN
  - Write the report to `work_products/system_health_latest.md` (overwrite each cycle).
<!-- scope:all -->
- [ ] Mission Control build kickoff
  - Confirm first concrete milestone and produce a short execution checklist.
- [ ] AI-native freelance system progress
  - Identify and stage high-probability opportunities.
  - prepare proposal drafts and next actions for approval.
- [ ] Revenue-first opportunistic tasks
  - Surface quick-win side-hustle opportunities with short path to cash.
- [ ] Operational hygiene
  - review pending Todoist/calendar/email execution blockers and propose the next 1-3 actions.
## Response Policy
- If a task was completed or moved forward materially, emit a concise summary.
- If nothing actionable exists, record heartbeat as skipped/no-op.
- When proactively picking Todoist work, state what was chosen, why it was eligible, and what changed.
<!--
Checkbox semantics:
- [ ] active
- [x] completed/disabled
-->
## Kevin's Working Style Preferences (2025-03-12)
**Proactive Improvement Suggestions:**
Kevin explicitly stated: "I love this type of interaction. More for other elements of our project for anything that you you see that needs improvement or suggestions etc. this is a great way to work together."
**Key Takeaway:**
- Kevin WANTS agents to proactively identify improvement opportunities
- He appreciates specific, actionable suggestions with rationale
- This applies across ALL project elements, not just CSI
- Agents should not wait for permission to suggest optimizations
- When you see something that could be better, speak up!
**Examples of what he likes:**
- Noise reduction in notifications (quality over quantity)
- Threshold adjustments based on observed patterns
- Operational efficiency improvements
- Any optimization that reduces friction while maintaining effectiveness
**Action:** When working on any part of the system, actively look for improvement opportunities and present them with clear reasoning.
## Recent communications log
### 2026-03-14: NotebookLM Integration Announcement
**From:** Kevin Dragan <kevinjdragan@gmail.com>
**subject:** New Capability: NotebookLM Integration — Research & Artifact Engine
**thread ID:** 0b2eab4c-b779-4645-800e-f0b62f8e8355
**message ID:** <CAEi7pTm_XBcjnN1AmOUFMVCFGcxkVWGgHUg0+VZKh+pTZXVvsQ@mail.gmail.com>
**classification:** Capability Announcement / Configuration Update
**action taken:** acknowledged receipt, reviewed documentation at docs/03_Operations/96_NotebookLM_Integration_And_Research_Pipeline_2026-03-14.md, drafted professional confirmation reply
**status:** complete
**Key capabilities added:**
- web research (fast ~30s, deep ~5min)
- artifact generation: written, audio, visual, interactive, data, video
- delegation model: main agent -> nlm-operator sub-agent -> MCP tools
- latency tradeoffs documented: default to fast research
- delivery separation: NLM sub-agent produces artifacts, main agent handles delivery via AgentMail
**operational impact:**
- can now produce high-quality research deliverables with superior output quality
- must be mindful of latency (deep research + artifacts = 8-15min total)
- use for important deliverables where quality matters, not routine tasks

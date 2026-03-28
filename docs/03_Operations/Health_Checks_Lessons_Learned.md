# Health Checks Lessons Learned

> **Maintainer Note:** Keep entries in this document extremely concise. This file is directly injected into Simone's context during the heartbeat health check. Bloat here translates to wasted tokens and diluted focus for the agent.

## False Positives (Ignore)
- **Stale Brainstorms Awaiting External Input**: If a brainstorm task has an email or slack message out and the human hasn't replied, it is NOT stuck. Do not alert. Simply DEFER.
- **Scheduled Background Syncs**: Tasks labeled `[System/Sync]` staying in `in_progress` are operating normally. Do not alert.

## Known Incident Mitigations (For Simone)
- **VP DB Locked (`vp_db_locked`)**: If you see this error when routing or checking missions, it is transient SQLite contention. Automatically retry rather than abandoning the route. 
- **Stale Review Sign-offs**: If a delegated VP mission finished over 4 hours ago but the corresponding task is still in `in_progress`, it likely got trapped due to a prompt parsing error. Manually read the `workspace://` artifacts via `vp_read_result_artifacts` and synthesize a completion note to close it.

## Escalation Criteria (Human Intervention Required)
- **Hard Cloud Outage**: If LLM API boundaries or infisical secrets service drop consistently over 3+ retries, stop and escalate.
- **Runaway Deletions**: If you identify a task rapidly deleting files or database records unexpectedly, freeze it and escalate immediately.

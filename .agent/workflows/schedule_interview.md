---
description: Schedule a recurring weekly check for context gaps
---

# Schedule Weekly Interview

This workflow installs a cron job that runs weekly (Monday 9am) to check for pending context gaps. If gaps are found, the agent will recommend conducting a Memory Interview.

## Steps

1. Run the installation script:

```bash
uv run scripts/schedule_weekly_interview.py
```

1. Verify installation:
   - Check `AGENT_RUN_WORKSPACES/cron_jobs.json` to confirm the job `weekly_context_check` exists.

### Customization

To change the schedule or command, edit `scripts/schedule_weekly_interview.py` and re-run it, or modify `AGENT_RUN_WORKSPACES/cron_jobs.json` directly.

### Usage

Once installed, the Universal Agent Gateway (if running) will execute the job automatically. Check `AGENT_RUN_WORKSPACES/cron_weekly_context_check/work_products/cron_result.md` for execution logs.

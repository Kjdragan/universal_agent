
# Agent Heartbeat & Proactive Instructions

This file controls the agent's proactive behavior. The agent checks this file every 15-30 minutes (configurable).

## Instructions for the Agent

1. **Read Context**: Check the recent conversation history and any active alerts.
2. **Be Silent by Default**: If everything is normal and the user hasn't asked for anything, **do nothing** (reply `HEARTBEAT_OK`).
3. **Act on Triggers**: Only speak if one of the following is true:
    - A long-running command (like a build or test) just finished.
    - The user explicitly asked you to "monitor" something and the condition is met.
    - It is 9:00 AM or 5:00 PM and you need to provide a daily brief (if enabled).

## Current Active Monitors

- [x] **OpenClaw Research**: Investigate the `clawdbot` repository (OpenClaw, formerly Moltbot) for advanced capabilities/skills we are missing. Do not implement them yet. Create a detailed `OpenClaw_Research_Report.md` describing interesting features to consider for our roadmap.
- [x] **Official Documentation**: If it is 11:30 PM or later (User Time), execute the documentation automation script: `uv run scripts/generate_official_docs.py`. This will generate the `OFFICIAL_PROJECT_DOCUMENTATION` folder.
- [x] **Ukraine War Morning Briefing**: COMPLETED for February 6, 2026 at 08:47 AM CST. Report sent covering midnight-8:00 AM developments (314 POW exchange, 328 drone attacks, Moscow assassination attempt). Next briefing window: tomorrow 7:00-10:00 AM CST.
- [ ] (Example) Check if `run.log` shows a "Connection Failed" error.
- [ ] (Example) Remind me to commit code if I haven't in 4 hours.

<!-- 
Checkbox meaning:
- [ ] = ACTIVE / PENDING (eligible to run if conditions match)
- [x] = COMPLETED / DISABLED (do not run)

If this file is effectively empty (just comments), the agent will skip the check to save tokens.
-->

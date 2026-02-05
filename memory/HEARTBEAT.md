
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

- [ ] **Official Documentation**: At 9:45 PM (User Time), execute the documentation automation script: `uv run scripts/generate_official_docs.py`. This will generate the `OFFICIAL_PROJECT_DOCUMENTATION` folder.
- [ ] **Morning Briefing**: If it is between 8:00 AM and 10:00 AM (User Time), check if you have already sent a briefing today. If not, generate a concise summary of any overnight alerts, system status, and pending tasks, then mark this done for the day.
- [ ] (Example) Check if `run.log` shows a "Connection Failed" error.
- [ ] (Example) Remind me to commit code if I haven't in 4 hours.

<!-- 
To enable these, change "[ ]" to "[x]" or add new lines.
If this file is effectively empty (just comments), the agent will skip the check to save tokens.
-->

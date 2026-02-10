
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

- [ ] 

<!--
Checkbox meaning:
- [ ] = ACTIVE / PENDING (eligible to run if conditions match)
- [x] = COMPLETED / DISABLED (do not run)

If this file is effectively empty (just comments), the agent will skip the check to save tokens.
-->

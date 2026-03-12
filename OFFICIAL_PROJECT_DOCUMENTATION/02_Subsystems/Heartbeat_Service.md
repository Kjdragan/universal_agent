# Heartbeat Service

The **Heartbeat Service** is the "autonomic nervous system" of the Universal Agent. It allows the agent to function without direct user interaction.

## 1. Purpose

Most chatbots are passive—they only speak when spoken to. The Heartbeat Service changes this by:

- **Periodic Wakefulness**: Prompting the agent to re-evaluate its state every few minutes.
- **Monitoring Background Tasks**: Checking if long-running operations (like research or compilation) are finished.
- **Time Sensitivity**: Informing the agent of the current time, approaching deadlines, or scheduled events.

## 2. Heartbeat Cycle

```mermaid
graph TD
    Start((Timer Start)) --> Wait[Wait Interval (e.g. 300s)]
    Wait --> Check{Active Hours?}
    Check -- No --> Wait
    Check -- Yes --> Trigger[Collect System Events]
    Trigger --> Inject[Inject Prompt to Agent]
    Inject --> Stream[Agent Thinking Turn]
    Stream --> Final{OK Token?}
    Final -- Yes --> Stop((Cycle End))
    Final -- No --> Action[Perform Autonomous Action]
    Action --> Stop
```

## 3. Configuration & Scheduling

The heartbeat is highly configurable via environment variables or a `heartbeat_config.json` in the workspace.

- **`every_seconds`**: Interval between runs.
- **`active_start` / `active_end`**: Time window during which the heartbeat is allowed to run (e.g., "08:00" to "20:00").
- **`timezone`**: User-specific timezone for consistent scheduling.
- **`ok_tokens`**: Special strings (like `HEARTBEAT_OK`) that the agent can say to indicate it has nothing to do, ending the turn without further noise.

## 4. Visibility (Stealth Mode)

The agent can choose to perform "stealth heartbeats" where its thoughts are logged internally but not displayed to the user. This is useful for minor background tasks that don't require user attention.

## 5. Non-OK Heartbeat Mediation

Non-OK heartbeats no longer stop at a passive notification.

Current behavior:

- The heartbeat still writes a human-readable report to `work_products/system_health_latest.md`.
- It should also write a machine-readable findings contract to `work_products/heartbeat_findings_latest.json`.
- The gateway classifies those findings and turns the resulting `autonomous_heartbeat_completed` notification into an actionable item.
- Non-OK heartbeat findings are automatically routed to Simone for investigation through the hook system.
- Simone is instructed to investigate and recommend next steps only. She is not allowed to auto-edit code, auto-run remediation shell commands, or auto-deploy in this flow.
- If the finding is unknown or otherwise requires operator judgment, the system raises an additional review-required notification and sends Kevin an AgentMail summary.

This creates a tiered flow:

1. heartbeat detects the issue
2. gateway classifies and persists it
3. Simone investigates automatically
4. Kevin is notified when explicit operator review is needed

## 6. Implementation Files

- `src/universal_agent/heartbeat_service.py`: The main service class and event collection logic.
- `memory/HEARTBEAT.md`: Operating instructions for heartbeat-authored reports and findings artifacts.
- `src/universal_agent/gateway_server.py`: Notification classification, mediation dispatch, and operator notification.
- `src/universal_agent/hooks_service.py`: Hook completion handling for heartbeat investigations.
- `src/universal_agent/main.py`: Bootstraps the service during agent initialization.

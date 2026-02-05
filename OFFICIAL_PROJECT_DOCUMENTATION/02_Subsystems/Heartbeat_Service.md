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

## 5. Implementation Files

- `src/universal_agent/heartbeat_service.py`: The main service class and event collection logic.
- `src/universal_agent/main.py`: Bootstraps the service during agent initialization.

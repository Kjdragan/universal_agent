# Proactive Design Phase 1: The Heartbeat System

## Context
Standard agents are reactive: they wait for a user message before doing anything. To build a truly "Universal" agent that can handle long-running tasks, reminders, and self-checks, we need a mechanism to wake the agent up without user intervention.

## Architectural Design

### 1. The `HeartbeatScheduler`
A background component (likely using `asyncio` or a dedicated thread) that manages:
*   **Cron Jobs**: Scheduled tasks (e.g., "Every Friday at 5 PM").
*   **Intervals**: Periodic checks (e.g., "Every 5 minutes").

**File:** `src/universal_agent/proactive/scheduler.py`
**Key Types:**
*   `CronJob`: `{ id: string, schedule: string, payload: any }`
*   `SystemEvent`: `{ type: "WAKE" | "REMINDER", message: string }`

### 2. Gateway Integration
The `Gateway` (specifically `ExternalGateway` or `InProcessGateway`) acts as the event router.
*   **Listener**: It listens for events from the `HeartbeatScheduler`.
*   **Injection**: When an event fires, it injects it into the Agent's event loop as if it were a user message, but marked as a "System Event".
*   **Prompting**: The event appears in the context as:
    > [SYSTEM EVENT] HEARTBEAT: It is now 14:00. 1 active task.

### 3. Implementation Plan
1.  **Definitions**: Create `src/universal_agent/proactive/types.py`.
2.  **Scheduler**: Implement the `HeartbeatScheduler` class.
3.  **Wiring**:
    *   In `gateway.py`, instantiate the scheduler.
    *   Add `start_scheduler()` and `stop_scheduler()` to `HarnessOrchestrator` to control it during tests.

## Why This Matters
*   **Stuck Detection**: The agent can wake up after 10 minutes of silence to ask "Am I still working on this?"
*   **Reminders**: "Remind me to check the logs in an hour."
*   **Health Checks**: Periodically verify database connectivity or API tokens.

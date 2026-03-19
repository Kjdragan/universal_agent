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

The heartbeat is highly configurable via environment variables.

### Primary Settings

| Environment Variable | Default | Description |
| --- | --- | --- |
| `UA_HEARTBEAT_INTERVAL` | 1800 (30 min) | Primary interval between heartbeat runs (in seconds). Also accepts legacy `UA_HEARTBEAT_EVERY`. |
| `UA_HEARTBEAT_MIN_INTERVAL_SECONDS` | Dynamic | Minimum allowed interval; resolved after Infisical bootstrap. |
| `UA_HEARTBEAT_ACTIVE_START` | None | Start of active hours window (e.g., "08:00"). Heartbeat skips runs outside this window. |
| `UA_HEARTBEAT_ACTIVE_END` | None | End of active hours window (e.g., "20:00"). |
| `UA_HEARTBEAT_EXEC_TIMEOUT` | 600 | Maximum execution time for a single heartbeat turn (in seconds). |
| `UA_HEARTBEAT_AUTONOMOUS_ENABLED` | 1 | Set to "0" to disable autonomous heartbeat actions entirely. |

### Retry & Continuation

| Environment Variable | Default | Description |
| --- | --- | --- |
| `UA_HEARTBEAT_RETRY_BASE_SECONDS` | 10 | Base delay for exponential backoff retries. |
| `UA_HEARTBEAT_MAX_RETRY_BACKOFF_SECONDS` | 300 | Maximum retry backoff delay. |
| `UA_HEARTBEAT_CONTINUATION_DELAY_SECONDS` | 1 | Short delay after actionable runs for quick re-check. |
| `UA_HEARTBEAT_FOREGROUND_COOLDOWN_SECONDS` | 1800 | Cooldown after foreground (user) activity before heartbeat resumes. |

### Limits & Tuning

| Environment Variable | Default | Description |
| --- | --- | --- |
| `UA_HEARTBEAT_MAX_PROACTIVE_PER_CYCLE` | 1 | Maximum proactive items to process per heartbeat cycle. |
| `UA_HEARTBEAT_MAX_ACTIONABLE` | 50 | Maximum actionable items to surface in a single run. |
| `UA_HEARTBEAT_MAX_SYSTEM_EVENTS` | 25 | Maximum system events to include per heartbeat. |
| `UA_HEARTBEAT_INVESTIGATION_ONLY` | None | If set, heartbeat runs in investigation-only mode (no mutations). |

### OK Tokens

The agent can emit special strings (like `HEARTBEAT_OK` or `UA_HEARTBEAT_OK`) to indicate it has nothing to do, ending the turn cleanly.

### Retry Queue and Continuation Passes

Heartbeat scheduling now includes a persisted retry queue in `heartbeat_state.json`.

- **Busy or foreground-locked runs** do not silently vanish. They schedule a retry with exponential backoff:
  - `delay = min(base_retry_seconds * 2^(attempt - 1), max_retry_backoff_seconds)`
- **Failure-driven retries** use the same exponential backoff pattern.
- **Successful actionable runs** schedule a short continuation re-check (default 1 second) so the heartbeat can quickly pick up the next eligible proactive item instead of waiting for the full interval.
- Retry metadata is persisted with:
  - `retry_kind`
  - `retry_attempt`
  - `retry_reason`
  - `next_retry_at`
  - `last_retry_delay_seconds`

Current defaults:

- `UA_HEARTBEAT_RETRY_BASE_SECONDS=10`
- `UA_HEARTBEAT_MAX_RETRY_BACKOFF_SECONDS=300`
- `UA_HEARTBEAT_CONTINUATION_DELAY_SECONDS=1`

The normal schedule still remains the long-run cadence. The retry queue only accelerates recovery or continuation after a blocked, failed, or recently successful actionable run.

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

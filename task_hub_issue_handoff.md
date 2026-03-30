# Task Hub Dispatch Handoff Context

## Overview
A bug currently exists preventing background worker agents (specifically `daemon_simone`) from seizing available tasks off the "ToDo" queue. Tasks remain stuck in the backlog ("FUTURE" status on the UI dashboard) and never transition to "IN PROGRESS".

## Reported Symptom
Three sub-tasks that were successfully parsed and decomposed from an incoming email are sitting in the `open` state. The expected behavior is that the background agent should claim these tasks, moving them to an `in_progress` or `seized` state and actually executing them. 

## Factual Observations from Live Logs & Database

1. **Task Database Status**:
   - Polling `task_hub_items` in `activity_state.db` confirms three tasks are sitting correctly with `status='open'`, `agent_ready=1`, and `score=6.0`.
   - Polling `task_hub_dispatch_queue` in `activity_state.db` confirms these tasks are actively placed in the sorted priority queue. They show `eligible=1` and possess ranks 1, 2, and 3.

2. **Dispatcher Service Logs**:
   - The primary backend (`universal-agent-gateway`) is online and functioning. 
   - `idle_dispatch_loop.py` is actively logging that it detects an idle daemon worker: `🔄 Idle dispatch: woke session=daemon_simone (idle=..., busy=..., trigger=poll)`.
   - `todo_dispatch_service.py` acknowledges this by logging: `INFO:universal_agent.services.todo_dispatch_service:ToDo dispatch requested for daemon_simone`.

3. **Execution Blockage**:
   - Despite being successfully poked by the loop and the tasks being completely eligible in the database view, the tasks never move out of `status='open'`.
   - The queue claiming logic (`claim_next_dispatch_tasks` and `dispatch_sweep`) operates without throwing any critical stack traces or `Exception` logs into the `universal-agent-gateway` systemd journal.

## Path Information
- Backend SQLite Database: `/opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db`
- Dispatcher loops and services: `/home/kjdragan/lrepos/universal_agent/src/universal_agent/services/`
- Task hub core logic: `/home/kjdragan/lrepos/universal_agent/src/universal_agent/task_hub.py`
- Service logs: `journalctl -u universal-agent-gateway`

Please investigate why the active `todo_dispatch_service` and `claim_next_dispatch_tasks` sequence is failing to formally claim these `open` items for the `daemon_simone` workspace.

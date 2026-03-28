# Universal Agent Pipeline Masterpiece

## 1. Introduction
This document provides a holistic, end-to-end view of the **Universal Agent Pipeline**. It maps the life cycle of a task from its initial trigger (an email, a calendar event, or an autonomous thought) through orchestration and delegation by the primary agent (Simone), and finally to completion, review, or reflection.

The pipeline is designed to be Simone-first, meaning Simone acts as the central router and orchestrator. She evaluates all inbound work and makes intelligent delegation decisions based on current context and load.

---

## 2. Input Triggers & Ingress

Tasks enter the Universal Agent ecosystem through various asynchronous and synchronous ingress points. Regardless of origin, all roads lead to the **Task Hub**.

### 2.1 E-mail Ingress
- **Mechanism**: The `AgentMailService` listens via WebSocket for inbound emails.
- **Processing**: The payload is processed by `email_task_bridge.py`. It strips quotes to isolate the reply text and maps the email thread to a specific task. Thread-level deduplication ensures that an ongoing conversation updates an existing task (or creates a subtask) rather than spawning disparate new tasks.

### 2.2 Chat / Web UI & API
- **Mechanism**: Human interaction via the dashboard routes through `gateway_server.py`.
- **Processing**: Users can trigger "Start Now" (`dispatch_immediate`) or "Approve" (`dispatch_on_approval`) commands, which explicitly claim and prioritize the target task, skipping the standard queue wait time.

### 2.3 Webhooks & Integrations
- **Mechanism**: External triggers (e.g., Google Calendar updates, YouTube ingest) are captured via endpoints defined in `hooks_service.py`. 
- **Processing**: These hooks transform external payloads into standardized `Task Hub` items. For example, Calendar hooks can schedule tasks with `due_at` timestamps.

### 2.4 Internal Timers & Heartbeat
- **Process Heartbeat**: An OS-level daemon (`process_heartbeat.py`) writes a file constantly to signal the process is alive.
- **Heartbeat Service**: An application-level scheduler (`heartbeat_service.py`) runs periodically on the event loop (roughly every 30 minutes, or as dynamically scheduled). This service wakes up the agent to perform sweeps of the Task Hub. It integrates directly with the global `CapacityGovernor` to safely shed load or pause dispatching if inference APIs return 429 rate limit errors.

---

## 3. The Task Hub & Life Cycle

The **Task Hub** (`task_hub.py`) serves as the central nervous system, backed by a SQLite database (`runtime_state.db`). It enforces state machine rules and prevents race conditions.

### 3.1 Task States
A task moves through defined states to coordinate interactions between Simone, the human operator, and sub-agents (VPs):

- **Active States**:
  - `open`: Ready to be seized.
  - `in_progress`: Currently being worked on.
  - `scheduled`: Waiting for a specific `due_at` time trigger.
  - `delegated`: Handed off to a VP sub-agent.
  - `pending_review`: A VP has finished, waiting for Simone's validation.
  - `needs_review`: Waiting for human input or approval.
  - `blocked`: Unable to proceed due to missing dependencies.

- **Terminal States**:
  - `completed`: Successfully finished. Protected from re-processing via a `completion_token`.
  - `parked`: Shelved indefinitely.

### 3.2 Loop Prevention & Deduplication
The pipeline employs multiple safeguards against infinite loops and redundant task execution:
- **Locking & Seizure**: The `seizure_state` and `stale_state` columns govern who currently owns the task and prevent double-dispatching.
- **Thread Deduplication**: Emails update existing tasks based on mapping tables (`email_task_bridge.py`).
- **Completion Tokens**: Completed tasks are assigned an idempotency token to prevent them from inadvertently re-entering the `open` state.

---

## 4. Simone Orchestration & Delegation

Historically, tasks were routed programmatically by LLM classifiers. However, the system has evolved into a **Simone-First Orchestration** model (`agent_router.py`).

### 4.1 Orchestration Flow
1. **Sweep**: During a heartbeat cycle or manual trigger, `dispatch_sweep` queries the Task Hub for eligible tasks.
2. **Assignment**: `route_all_to_simone` assigns 100% of these tasks directly to `simone`.
3. **Triage**: Simone evaluates the batch. Using her context, tools, and instructions, she decides the approach.

### 4.2 Delegation
If a task requires specialized coding or general extended processing, Simone can delegate the work.
- **VPs (Virtual Professionals)**: Overflow capacity agents (`vp.coder.primary` and `vp.general.primary`).
- **Delegated State**: When Simone delegates a task, its status transitions to `delegated`.
- **Handoff**: Once the VP finishes its mission, the task moves to `pending_review`, prompting Simone to sign off on the results in her next heartbeat sweep before calling it `completed`. If the VP worker loop encounters API rate limits during execution, it intercepts the 429 errors and reports them back to the `CapacityGovernor` to enforce global exponential backpressure across all agent pipelines.

---

## 5. Autonomy & Proactive Work

The Universal Agent isn't strictly reactive; it manages its own workload proactively. This is split into two distinct operational modes: Daytime and Overnight.

### 5.1 The Morning Report (Daytime)
- **Engine**: `proactive_advisor.py`
- **Function**: During the standard heartbeat cycle, the system builds a deterministic context snapshot without incurring LLM costs.
- **Content**: It highlights stale brainstorms, tasks lingering in `in_progress` for too long, and expiring or unanswered questions. This injection tells Simone to proactively seek clarification from the human operator or wrap up lingering tasks.

### 5.2 The Reflection Engine (Overnight)
- **Engine**: `reflection_engine.py`
- **Function**: Operates autonomously during optimal reflection hours (e.g., 10 PM to 7 AM local time).
- **Trigger**: Activates only when the standard dispatch queue is empty.
- **Workflow**:
  1. Grants the agent a "nightly budget" of tasks (default max: 10).
  2. Injects a rich prompt combining recent task completions, stalled brainstorms, and high-level goals retrieved from memory orchestrator search.
  3. Encourages the agent to advance brainstorm refinement stages (e.g., from `raw_idea` to `decomposing`), formulate actionable steps, or write research documents.
  4. Records generated insights straight back into the Task Hub to feature in the human’s Morning Report context.

---

## 6. Holistic System Review

The integration of `agentmail`, explicit task states, and pure-Python proactive context builders effectively scales the Universal Agent.

### Strengths
- **Centralized Routing**: Avoiding parallel programmatic LLM routers in favor of Simone-centric dispatch dramatically simplifies failure tracing.
- **Idempotency**: WebSocket reconnections and repetitive emails are absorbed safely by Task Hub deduplication layers.

### Areas for Future Optimization
1. **Delegation Granularity**: While Simone handles hand-offs well, VP status reporting remains relatively coarse (`delegated` -> `pending_review`). More granular checkpointing between VPs and Simone could improve trace visibility for very long-running coder tasks.
2. **Proactive Queue Saturation**: If the Reflection Engine maxes out the nightly budget consistently, the queue might become front-loaded with brainstorm items rather than high-priority actionable tasks. Prioritization heuristics might need tuning as the number of active projects scales.

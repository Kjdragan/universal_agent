# Harness System Overview

The **Harness System** extends the Universal Agent to support long-running tasks that exceed a single LLM context window (typically 1-24 hours). It implements the "Ralph Wiggum" pattern: iterative execution where the agent's memory is periodically reset ("I'm a brick!"), but context is preserved via persistent storage and prompt reinjection.

## Architecture

### Ralph Wiggum Pattern
1. **Agent Runs**: The agent performs work until a threshold (token limit, natural break, or explicit stop) is reached.
2. **Context Clear**: The agent's `client.history` is completely cleared.
3. **Reinjection**: A "Continuation Prompt" is injected, summarizing the:
   - Original Objective
   - Current Iteration Count
   - Required Completion Artifact (Promise)
   - Instructions to inspect the workspace.

### Key Components

#### 1. Database Schema (`runs` table)
New columns track the harness state:
- `iteration_count` (INTEGER): How many times the loop has reset.
- `max_iterations` (INTEGER): Guardrail to prevent infinite loops (def: 10).
- `completion_promise` (TEXT): A specific string (e.g., "TASK_COMPLETE") the agent MUST output to finish.

#### 2. Hooks (`main.py`)
- `on_agent_stop`: Fires at the end of every turn.
  - Checks if `completion_promise` is in the output.
  - If YES -> Marks run complete.
  - If NO -> Checks `max_iterations`.
  - If NO + Under Limit -> Triggers `action="restart"`.

#### 3. Restart Loop (`main.py`)
The main event loop handles the `restart` action by:
- Setting `pending_prompt` to the Continuation Prompt (returned by the hook).
- Clearing `client.history`.
- Continuing the `while True` loop immediately.

## Handing Off Context
The system relies on **Artifacts as Memory**. Since the context window is wiped, the agent must write its progress to files (e.g., `checklist.md`, `run_state.json`, or the file system itself). The Continuation Prompt explicitly instructs the agent to "Review your workspace files".

## Future Roadmap
- **Token Monitoring**: Real-time token counting to trigger handoff before context overflow (currently manual or hook-driven).
- **Phase Gates**: Structured phases (Plan -> Research -> Build -> Verify) enforced by the harness.

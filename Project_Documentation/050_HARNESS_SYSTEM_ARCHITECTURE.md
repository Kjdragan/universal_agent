# Harness System Architecture

**Status:** Implementation Complete & Tested
**Date:** January 6, 2026
**Based on:** 040_HARNESS_IMPLEMENTATION_HANDOFF.md (Executed)

## 1. Overview
The Harness System enables the Universal Agent to perform **Long-Running Tasks** that exceed a single context window. It acts as a supervisor, monitoring the agent's promise to complete a task and looping the session (with context clearing) until that promise is met.

## 2. Core Components

### 2.1 The Ralph Wiggum Loop (**"I'm a loop!"**)
The `main.py` execution loop has been modified to support infinite restarts while preserving the "Mission" but clearing the "Memory".

**How it works:**
1.  **Harness Activation:** Triggered via `/harness [objective]` or CLI arguments (`--max-iterations`, `--completion-promise`).
2.  **Promise Monitoring:** The `on_agent_stop` hook checks the final output of every turn.
    *   **Success:** Agent outputs `TASK_COMPLETE` -> Harness sets `action="complete"` -> Session Ends.
    *   **Incomplete:** Agent stops *without* the promise -> Harness sets `action="restart"`.
3.  **The Restart:**
    *   `client.history` is cleared (Token bloat removed).
    *   `iteration_count` is incremented in DB.
    *   **Continuation Prompt** is injected: "You are continuing a long-running task... Review your workspace files...".

### 2.2 Database Persistence
We store harness state in the `runs` table to survive process restarts/crashes.
*   `iteration_count` (INTEGER): Tracks progress.
*   `max_iterations` (INTEGER): Failsafe limit (default: 10).
*   `completion_promise` (TEXT): The magic string (e.g., "TASK_COMPLETE") the agent *must* say.

### 2.3 The "Inbox Pattern" (Research Isolation)
To decouple the harness from context bloat, research is now **File-Based**.
1.  **Inbox:** `search_results/` receives raw JSONs from search tools.
2.  **Processing:** `finalize_research` moves files to `search_results/processed_json/` and generates a summary (`research_overview.md`).
3.  **Task Artifacts:** Summary and filtered content are stored in `tasks/{task_id}/`.
4.  **Harness Benefit:** The "New Agent" in Iteration 2 reads the `research_overview.md` from disk to understand what happened in Iteration 1, without needing the chat history.

## 3. Usage

### Slash Command
```bash
/harness Research topic and write report
```
*   Sets `max_iterations=10`.
*   Sets `completion_promise="TASK_COMPLETE"`.
*   Starts the first prompt immediately.

### CLI
```bash
python -m universal_agent.main --resume --run-id <UUID> --max-iterations 20 --completion-promise "DONE"
```

## 4. Robustness Features
*   **Empty Output Handling:** If the agent executes a tool but outputs no text, the Harness *ignores* the turn (does not restart), allowing necessary intermediate steps.
*   **Promise Validation:** The promise string must be an exact match in the final text response.
*   **Iteration Limits:** Hard stop if `max_iterations` is reached to prevent infinite billing loops.

## 5. Future Extensions (Planned)
*   **Dynamic Summarization:** Instead of just "Review files", use a cheaper model to summarize the previous transcript into the System Prompt for the next iteration.
*   **Budget Awareness:** harness triggers based on dollar cost, not just iteration count.

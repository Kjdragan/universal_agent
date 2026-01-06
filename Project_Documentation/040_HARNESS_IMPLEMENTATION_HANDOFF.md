# Handoff: Harness Implementation & Context Limit Strategy

**Date**: 2026-01-05
**Status**: Ready for Implementation (Phase 6)
**Previous Agent**: Antigravity

---

## 1. Current State & Recent Wins
We have successfully **hardened** the Universal Agent against individual tool call failures and massive data loads.
- **Scholar Normalization**: `COMPOSIO_SEARCH_SCHOLAR` results are now normalized to the `web` schema for consistent processing.
- **Parallel Batching**: Sub-agents now read research files in batches of 5, enabling processing of 30+ sources (140k+ chars) without context overflow.
- **Hook Recovery**: We verified (via L5 Stress Test) that the `tool_output_validator_hook` successfully catches and recovers from 0-byte write errors caused by context exhaustion.
- **Token Analysis**: A full "L5 Heavy Report" consumes ~82k tokens total, but only **~30k tokens** in the Primary Agent's context (due to sub-agent isolation).

## 2. The Next Objective: "The Harness"
We need to support **long-running tasks (1-24 hours)** that exceed a single session's context window.
**Design Doc**: `Project_Documentation/030_LONG_RUNNING_TASK_EXTENSIONS_DESIGN.md`

### Selected Strategy: "Hybrid Phase A Harness"
Instead of the inefficient "Anthropic Style" (reset after every task), we chose a **Hybrid Trigger** strategy:

1.  **Efficiency**: Run multiple sub-tasks in a single session to maximize context utility.
2.  **Safety Trigger**: Force a Session Handoff (Restart with fresh context) ONLY when:
    *   **Context Usage > 75%** (approx 150k tokens).
    *   **AND** the current sub-task completes (Natural Break).
3.  **Persistence**:
    *   State is saved to `feature_list.json` / `progress.txt` equivalents (or just the Ledger).
    *   The Harness Loop (`main.py`) restarts the agent with a "Continuation Prompt".

## 3. Implementation Roadmap (Immediate Next Steps)

The next agent should immediately pick up **Phase 6** from the Task List.

### A. Infrastructure Updates
Modify `src/universal_agent/durable/state.py`:
- Add tracking columns to the `runs` table:
    ```sql
    ALTER TABLE runs ADD COLUMN iteration_count INTEGER DEFAULT 0;
    ALTER TABLE runs ADD COLUMN max_iterations INTEGER DEFAULT 10;
    ALTER TABLE runs ADD COLUMN completion_promise TEXT;
    ```

### B. Harness Logic
Modify `src/universal_agent/main.py` (The Loop):
1.  **Implement `check_harness_threshold()`**:
    - Check if `iteration_count < max_iterations`.
    - Check token usage (if available via `usage` metrics or `MessageHistory`).
2.  **Implement `on_agent_stop` Hook**:
    - Instead of exiting on "Stop", check if the *Overall Objective* is done (via `completion_promise`).
    - If NOT done:
        - Save Checkpoint.
        - **Restart Agent** (Clear `messages` list).
        - **Re-Prompt**: Inject a summary of previous work ("You are continuing iteration X...").

### C. Verification
- Run a "Double Report" test: Ask the agent to generate two distinct reports in sequence that would push the context > 75% (or simulate it by lowering the threshold to 50% for testing), verifying the handoff occurs and memory is cleared.

## 4. Key Files
- `src/universal_agent/main.py`: The entry point and loop controller.
- `src/universal_agent/durable/state.py`: Database schema.
- `Project_Documentation/030_LONG_RUNNING_TASK_EXTENSIONS_DESIGN.md`: Detailed architectural reference.
- `anthropics/claude-quickstarts` (Reference): We are adopting their "Session loop" pattern but optimizing the trigger.

# Autonomous Stress Test Protocol: "Run-Eval-Fix-Loop"

## Overview
This protocol defines the recursive workflow for stress testing the Universal Agent's harness architecture using the "Fortune 100 Research" prompt. The goal is to reach a state where the harness can autonomously manage context windows for 100+ sequential sub-tasks without crashing.

## The Protocol Loop
This is a recursive cycle. Upon waking or being invoked, the agent must determine the current state and execute the next phase.

### Phase 1: EXECUTION (Cycle N)
**Objective**: Run the agent to exhaustion or completion.
*   **Prompt**: "Research each of the 100 Fortune 100 companies and create a profile report for a potential investor to get up to speed on the company. Include: Business Overview, Key Financials, Growth Drivers, and Risks."
*   **Startup Sequence**:
    1.  `./local_dev.sh`
    2.  Wait for CLI.
    3.  `/harness [Objective]`
    4.  **Autonomous Interview**:
        *   Q1 (Detail Level): `1` (Detailed deep-dives)
        *   Q2 (Processing Order): `1` (By Fortune ranking)
        *   Q3 (Update Frequency): `2` (Final notification only)
    5.  **Plan Approval**: `Yes`
*   **State Transition**: Move to Phase 2 once the run crashes, hangs, or completes.

### Phase 2: EVALUATION
**Objective**: Analyze the run artifacts to identify failure points.
*   **Inputs**: `trace.json`, `run.log`, `agent_college.log`.
*   **Key Metrics**:
    *   `token_usage` at crash vs. `TRUNCATION_THRESHOLD` (180k).
    *   Harness iteration count (did it reset context?).
    *   `HarnessError` occurrences (did it self-heal?).
    *   Database constraint errors (e.g., `UNIQUE constraint failed`).
*   **Output**: Create/Update `evaluation_report_cycle_[N].md`.

### Phase 3: FIX / IMPROVEMENT
**Objective**: Address the specific root cause found in Phase 2.
*   **Action**: Edit `src/universal_agent` code to fix bugs or optimize harness logic.
*   **Verification**: Ensure unit tests or specific verification scripts pass.

### Phase 4: RESTART (Loop)
**Objective**: Start Cycle N+1.
*   **Action**: Kill previous process. Return to Phase 1.

## Current State Tracking
Use `task.md` or a specific status file to track which Cycle is active.

## Handling "User Absence"
Since the user is away:
1.  **Do not wait for input**. Aggressively automate inputs (pre-defined answers).
2.  **Self-Correction**. If a run fails immediately, fix and restart immediately.
3.  **Documentation**. Log every cycle's result clearly so the user can review the progress upon return.

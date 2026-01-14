# Handoff Context: Harness Debugging & Context Verification

## 1. Project Status
We have **Solved** the critical "Context Exhaustion" (Zero-Byte Write) issue by implementing a **Two-Phase Sub-Agent Architecture** ("Context Refresh").
The system can now successfully generate comprehensive reports from large-scale research.

Our CURRENT BLOCKER is the **Harness Verification System**. While the agent logic works perfectly, the harness tooling has race conditions and buffering issues that cause false negatives (restart loops).

## 2. Recent Accomplishments
1.  **Context Refresh Strategy**: Implemented `research-specialist` (Gatherer) and `report-writer` (Author) sub-agents.
    *   Result: Zero-byte writes eliminated. 30KB+ reports generated successfully.
2.  **Double Session Bug Fix**: Fixed a bug in `main.py` where `UniversalAgent()` was instantiated without arguments, creating a duplicate "ghost" session directory.
3.  **Harness Verification**: Confirmed via `run.log` that the agent outputs `<promise>TASK_COMPLETE</promise>`, but the harness tooling reads an empty string and restarts.

---

## 3. Priority A: ✅ FIXED - Harness Tooling Race Conditions
**The Issue**: The `verifier.py` or stdout capture mechanism reads the output stream too early, missing the completion tag.
**The Symptoms**:
*   Agent log: `<promise>TASK_COMPLETE</promise>`
*   Harness log: `Checking Output (Length: 0): ''`
*   Result: Infinite restart loop despite success.

**The Fix (2026-01-13)**:
Implemented a fallback mechanism in `on_agent_stop()` that reads from `run.log` when `result.response_text` is empty.
*   Added `_extract_promise_from_log()` helper function
*   Integrated fallback into harness output checking
*   When fallback triggers, logs show: `🔄 Primary output capture empty, checking run.log fallback...`

## 4. Priority B: Sequential Execution & "The Dump"
**The Issue**: The agent sees all tasks in `mission.json` and tries to do them all at once.
**The Solution**: Refactor the harness loop to strict **Sequential Execution**:
1.  Inject *only* the current PENDING task into the context.
2.  Hide future tasks or mark them clearly as "LOCKED".

---

## 5. Key Artifacts
*   `030_CONTEXT_EXHAUSTION_FIX_SUMMARY.md`: Details of the recently fixed architecture.
*   `src/universal_agent/main.py`: Core entry point (recently modified).
*   `.claude/agents/`: Definitions for `research-specialist` and `report-writer`.
*   `AGENT_RUN_WORKSPACES/session_20260113_161008/run.log`: The "Golden Run" proving the fix and exposing the harness bug.

## 6. Next Steps for New Agent
1.  Start a new chat with `000_CURRENT_CONTEXT.md` and `000_CHAT_CONTEXT_HANDOFF.md`.
2.  **IMMEDIATE GOAL**: Fix the Harness stdout capture race condition.
3.  **SECONDARY GOAL**: Implement strict sequential task injection in `process_harness_phase`.

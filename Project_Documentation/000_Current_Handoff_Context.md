# Handoff Context: Harness Reliability & Robustness Refactor

**Date:** 2026-01-30
**Status:** Harness Robust; Phase Transitions Verified; Report Generation Hardened; UI Observability Restored

---

## 📍 Where We Are (Current Achievements)

We have significantly improved the **Harness Reliability** and the **Report Generation Pipeline** after identifying critical failure modes during real-world stress tests.

1.  **Harness Stability**: 
    - Resolved `TypeError` and `IndentationError` in `main.py` that caused execution crashes.
    - Improved **Plan Robustness** in `interview.py` by normalizing LLM-generated plan labels (case-insensitivity, snake_case vs camelCase).
2.  **Report Generation Hardening**:
    - **Robust JSON Extraction**: Implemented regex-based JSON extraction in `generate_outline.py` to recover from "dirty" LLM outputs (e.g., HTML wrapping or conversational noise).
    - **Granular Tooling**: Exposed Step-by-Step tools for report generation (Outline -> Draft -> Cleanup -> Compile) via the MCP server to allow subagent recovery.
3.  **Real-time Observability**:
    - Restored **Event Streaming** to the Web UI using `ContextVar` to track run identity across async contexts.
    - Verified that "Activity & Logs" panels now populate correctly with research progress and thinking blocks.
4.  **Subagent Guardrails**:
    - Added "Bash Hygiene" rules to `research-specialist` and `report-writer` to prevent empty command errors.
    - Implemented **Recovery Protocols** in subagent instructions to handle composite tool failures gracefully.

---

## 🎯 Immediate Goals (Next Session)

1.  **Vertical Task Decomposition**:
    - Refine the Planning Specialist to group tasks by **vertical outcome** rather than **process layer**.
2.  **Gateway-Engine Convergence**:
    - Finish converging CLI and Web UI on the unified canonical execution engine (CLI `process_turn` path) to eliminate timeout/path divergences.
3.  **Harness Performance Benchmarking**:
    - Use the new granular tools to profile which report-generation phases are most token-intensive/slow and optimize.

---

## 🔑 Key Files & Paths
*   **Recent Documentation**:
    - `Project_Documentation/034_Harness_Stability_and_Robustness_Refactor.md` (Detailed Fix Record)
    - `Project_Documentation/033_Harness_Success_Case_Study_20260130.md` (Success Record)
*   **Robust Tools**:
    - `src/universal_agent/scripts/generate_outline.py` (Robust JSON Extraction)
    - `src/universal_agent/hooks.py` (Unified Event Lifecycle)
*   **Subagents**:
    - `.claude/agents/report-writer.md` (Granular Tool Usage instructions)

---

## 💡 Usage Notes
*   **Harness Reliability**: The harness now survives LLM "hallucinations" in the outline phase. If the high-level `run_report_generation` fails, tell the `report-writer` to "run the outline step manually".
*   **Logging**: Check the "Internal Logs" for real-time `StdoutToEventStream` output if you suspect a tool is hanging locally.

---

## 🧪 Recent Findings (2026-01-30)

1. **LLM Output Fragility**
   - Even high-capability models (GLM-4, Sonnet) can return malformed JSON when generating complex report outlines. 
   - **Lesson**: Never use `json.loads` directly on AI text without a regex-based extraction layer and "healing" logic.

2. **Subagent Error Recovery**
   - Subagents "wonder" when a black-box tool fails without granular feedback.
   - **Lesson**: Exposing the internal steps of a pipeline as individual tools allows the agent to self-correct at the exact point of failure.

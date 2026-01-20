# Evaluation: Harness vs. Manual Workflow

**Date:** January 14, 2026
**Manual Run ID:** session_20260114_120517
**Harness Run ID:** session_20260114_122354

## 1. Executive Summary
We compared a manual "One-Shot" run against a run executed within the `agent_harness` (Ralph Loop). Both runs successfully produced the target report and emailed it. However, the Harness run induced significantly different behaviors, demonstrating higher robustness but increased overhead.

## 2. Behavioral Differences

| Feature | Manual One-Shot | Harness Run |
|---------|-----------------|-------------|
| **Strategy** | Direct Execution | **Deep Analysis** (Evidence Ledger) |
| **Protocol** | Implicit (Do the task) | **Explicit** (Update `mission.json`) |
| **Error Handling** | None needed | **Self-Correction** (Symlink fix) |
| **Complexity** | 15 Steps | 36+ Steps |
| **Outcome** | Success | Success (after Retry) |

### Key Observation 1: Skill Activation
The Harness run context seemingly triggered the `massive-report-writing` skill, leading the agent to build an **Evidence Ledger** (`mcp__local_toolkit__build_evidence_ledger`). This is a more rigorous process than the standard "read and write" approach used in the manual run.
*   **Manual:** Read Files -> Write Report.
*   **Harness:** Read Files -> Build Evidence Ledger -> Write Report.

### Key Observation 2: Verification & Recovery
The Harness's rigid verification logic flagged a failure: it expected `search_results/*.json` at the root, but `finalize_research` moves them to `processed_json/`.
*   **Agent Response:** The agent correctly diagnosed the issue from the harness error feedback and executed a fix (creating symlinks) to satisfy the verification criteria.
*   **Value:** This proves the agent's **durability** and sufficiency in self-correcting environmental mismatches.

## 3. Overhead Analysis
The Harness introduces "Bureaucratic Overhead":
1.  **Mission Tracking:** The agent spent multiple turns updating `mission.json` to mark tasks as `IN_PROGRESS` or `COMPLETED`.
2.  **Verification Loops:** The failure/retry cycle added latency (~23 seconds for the fix).

## 4. Conclusion
The Harness is an excellent tool for **Durability Testing** and **Regression Testing**. It forces the agent to prove its work and adhere to strict protocols.
*   **For Development:** Use Manual runs for speed/iteration.
*   **For Validation:** Use Harness runs to ensure the agent can handle edge cases (like file location mismatches) and strictly follow complex multi-step workflows.

**Final Verdict:** The Universal Agent is fully compatible with the Harness, and the Harness successfully revealed the agent's capability to recover from both verification failures *and* execution timeouts (switching to Skills).

## 5. Deep Evaluation (Artifacts & Traces)

### 5.1 Artifact Inspector
We examined the generated output files to assess content quality vs. the manual run.
*   **Report (`russia_ukraine_war_report.html`)**:
    *   **Size:** 29KB (vs Manual 33KB). The Harness report was slightly more concise but equally comprehensive.
    *   **Quality:** Excellent. It successfully filtered out noise found in the raw data (see below).
    *   **Citations:** High density of `[EVID-XXX]` citations, linking claims to specific ledger entries.
*   **Evidence Ledger (`evidence_ledger.md`)**:
    *   **Content:** 29 Entries.
    *   **Noise Handling:** The ledger included some low-quality "YouTube Description" artifacts (e.g., `EVID-006`, `EVID-018` containing merch store spam).
    *   **Resilience:** Crucially, the **Report Generation** step successfully ignored this noise. No "T-Shirt prices" appeared in the final PDF. This demonstrates the robustness of the synthesis prompt even when input data is imperfect.

### 5.2 Trace & Performance Analysis
Using **Logfire** (Trace ID: `019bbdbffc180c5976cbe1128dabbb61`), we uncovered the mechanism of the run's complexity:
1.  **Likely Timeout Trigger:** The logs show a `Request timed out` event at T+669s.
2.  **Skill Activation:** Immediately after the timeout, the agent switched strategies, reading `SKILL.md` for `massive-report-writing`.
3.  **Hook Verification:** Logfire spans confirmed `write_hook_fired` at `src/universal_agent/main.py:723`. This proves that the Harness execution correctly loaded and triggered the agent's durability hooks (`on_pre_tool_use_ledger`), which are essential for long-running task reliability.

**Conclusion:** The Harness run took longer (13.5m vs 5m) not just because of "bureaucracy", but because it **encountered a failure (timeout)** and **successfully recovered** using advanced Skills. This makes the Harness run a far superior validation of the agent's *Tier 2* (Resilience) capabilities.

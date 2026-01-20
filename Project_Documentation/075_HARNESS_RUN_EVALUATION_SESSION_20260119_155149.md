# 075: Harness Run Evaluation - Session 20260119_155149

**Date:** 2026-01-19  
**Status:** Draft (for review)  
**Source Log:** `AGENT_RUN_WORKSPACES/session_20260119_155149/run.log`

---

## 1) Executive Summary
The harness completed the research, report generation, PDF conversion, and email delivery multiple times, but verification failed on every attempt. The failure is not due to missing work; it is primarily due to the evaluation pipeline using an empty `agent_output` and not inspecting artifacts, plus workspace path mismatches that prevent the verifier from seeing produced files. As a result, the qualitative judge scores the tasks below threshold and no actionable feedback is generated.

---

## 2) What Completed Successfully (Evidence)
1. **Research completed** with finalized corpora in the base session workspace (Phase 1/2) and phase workspace (Phase 3):
   - `AGENT_RUN_WORKSPACES/session_20260119_155149/tasks/solar_energy_benefits/refined_corpus.md`
   - `AGENT_RUN_WORKSPACES/session_20260119_155149/tasks/solar_energy_benefits_v2/refined_corpus.md`
   - `AGENT_RUN_WORKSPACES/harness_20260119_155205/session_phase_3/tasks/solar_energy_benefits/refined_corpus.md`
2. **HTML report created** in phase workspaces:
   - `AGENT_RUN_WORKSPACES/harness_20260119_155205/session_phase_1/work_products/solar_energy_benefits_report.html`
   - `AGENT_RUN_WORKSPACES/harness_20260119_155205/session_phase_2/work_products/solar_energy_benefits_report.html`
   - `AGENT_RUN_WORKSPACES/harness_20260119_155205/session_phase_4/work_products/solar_energy_benefits_report.html`
3. **PDFs generated** by Chrome headless in phase workspaces (Phase 1/2/4).
4. **Email send succeeded** (GMAIL_SEND_EMAIL returned success for phases 1/2/4).

---

## 3) Why Verification Failed (Root Causes)
1. **Qualitative evaluation runs with empty content.**
   - In `_evaluate_phase`, `agent_output` is always passed as an empty string.
   - Tasks are converted to qualitative evaluation via `HarnessAdapter.atomic_task_to_state_task` because the success criteria do not use `file_exists:` or other binary prefixes.
   - `LLMJudgeEvaluator` only sees empty output, so it scores the task near zero; the overall score becomes ~0.67 (average of 1.0 binary + 1.0 constraint + 0.0 qualitative).
   - Result: tasks fail even when artifacts exist.

2. **Verifier ignores qualitative reasoning and produces empty feedback.**
   - `CompositeEvaluator` does not include `qualitative_reasoning` in `missing_elements`.
   - The harness logs show `Missing: ["Task 'X': "]` with empty detail.
   - Feedback passed to retries is therefore blank, so the agent cannot self-correct even if it wanted to.

3. **Workspace mismatch hides artifacts from the verifier.**
   - Search results and some draft outputs were saved to the base session workspace (`session_20260119_155149`) rather than the harness phase workspace (`harness_.../session_phase_X`).
   - `finalize_research` initially failed because it looked in the phase workspace, which had no `search_results`.
   - `draft_report_parallel` looked for `outline.json` under the base session workspace and failed until the agent manually wrote there.
   - `_scan_session_artifacts` only scans `session_phase_X/work_products`, so anything saved in the base session workspace is invisible to verification.

4. **Phase 3 timeout interrupted report creation.**
   - Phase 3 shows `Request timed out` during report writing; the report file never existed in that phase's work_products.
   - Even if evaluation were fixed, Phase 3 would still fail binary checks (if they existed) due to missing report/PDF/email.

---

## 4) Threshold Methodology Review
1. **Current qualitative gating is too strict for how it is implemented.**
   - The qualitative judge is scoring an empty string, not the artifacts. This yields false negatives.
   - A score of ~0.67 is likely an artifact of averaging; it does not reflect report quality.
2. **Binary pass conditions are absent for key tasks.**
   - "Convert HTML to PDF" and "Email Report" should be binary checks (file exists + side effect recorded).
   - Without binary checks, qualitative scoring is the only gate, which is not realistic for these tasks.
3. **Recommendation:** Use qualitative scoring only for the report content task, and only after giving the judge the actual report content (or a short excerpt). Treat research, PDF conversion, and email tasks as binary.

---

## 5) Feedback and Retry Behavior
1. **No usable feedback was produced.**
   - The verifier logged `Missing: ["Task 'X': "]` with no details.
   - The retry feedback block is therefore empty.
2. **Impact:** The agent had no actionable guidance to improve or correct issues across retries.
3. **Recommendation:** Include qualitative reasoning in `missing_elements` or explicitly append it in the harness feedback. Also log the qualitative score and reasoning per task.

---

## 6) Suggested Corrections (No Code Changes Yet; Design Guidance Only)
1. **Fix evaluator inputs:**
   - Pass a non-empty `agent_output` that includes a short summary of produced artifacts and key paths.
   - Or use `SubAgentEvaluator` to read artifacts directly.
2. **Fix qualitative feedback:**
   - Append `qualitative_reasoning` into `missing_elements` when qualitative fails.
   - Include `qualitative_score` per task in the harness log output.
3. **Add binary checks to success criteria:**
   - Research: `file_exists: tasks/<task_name>/refined_corpus.md`
   - HTML: `file_exists: work_products/solar_energy_benefits_report.html`
   - PDF: `file_exists: work_products/solar_energy_benefits_report.pdf`
   - Email: `side_effect:GMAIL_SEND_EMAIL` or similar recorded effect.
4. **Normalize workspace paths:**
   - Ensure COMPOSIO search outputs and observer saves honor the phase workspace.
   - If tools cannot be redirected, copy/symlink into the phase workspace before evaluation.
5. **Adjust thresholds:**
   - Set `qualitative_min_score` lower (0.5-0.6) for research/report tasks until the judge reads actual artifacts.
   - For binary tasks, disable qualitative evaluation entirely.

---

## 7) Final Assessment
This run produced a valid report, PDF, and email. The harness failed due to evaluation mechanics (empty judge input, missing feedback propagation, and workspace misalignment), not because the deliverables were missing or low quality. The most direct fix is to ensure the evaluator can see artifacts and to add binary checks for tasks that are inherently binary.

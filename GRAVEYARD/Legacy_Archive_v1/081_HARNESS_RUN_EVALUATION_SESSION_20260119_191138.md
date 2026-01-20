# 081: Harness Run Evaluation - Session 20260119_191138

**Date:** 2026-01-19  
**Status:** Draft (for review)  
**Source Log:** `AGENT_RUN_WORKSPACES/session_20260119_191138/run.log`

---

## 1) Executive Summary
The harness run completed research, report generation, PDF conversion, and email sending actions across multiple retries, but failed verification due to (a) research finalize path mismatch, (b) email task rubric mismatches (separate emails or HTML embedded instead of attached), and (c) evaluator visibility gaps caused by truncated HTML previews. The run also restarted the full workflow on each retry because the harness uses a fresh phase workspace and does not automatically reuse completed artifacts unless the agent copies them manually.

---

## 2) Key Outcomes and Evidence
1. **Search results saved to phase workspace** (correct):
   - `AGENT_RUN_WORKSPACES/harness_20260119_191155/session_phase_1/search_results/*.json`
2. **HTML report generated**:
   - `AGENT_RUN_WORKSPACES/harness_20260119_191155/session_phase_1/work_products/solar_energy_benefits_report.html`
   - Copied into `session_phase_2`, `session_phase_3`, `session_phase_4`
3. **PDF generated**:
   - `AGENT_RUN_WORKSPACES/harness_20260119_191155/session_phase_1/work_products/solar_energy_benefits_report.pdf`
4. **Email send attempts (Gmail IDs present)**:
   - Phase 1: two emails sent (PDF + HTML separate)  
     IDs: `19bd8f8e791b6536`, `19bd8f8e76722c61`
   - Phase 2: one email sent with PDF + HTML embedded  
     ID: `19bd8fada881ae66`
   - Phase 3: one email sent with PDF + HTML embedded  
     ID: `19bd8fe437ca3f3c`
5. **Email evidence artifacts created (Phase 2 only)**:
   - `session_phase_2/work_products/email_verification/email_send_log.md`
   - `session_phase_2/work_products/email_verification/email_artifact.json`

---

## 3) Root Causes of Verification Failure
1. **Finalize research called with wrong workspace**:
   - `finalize_research` was called with `session_dir=/home/kjdragan/.../AGENT_RUN_WORKSPACES/session_20260119_191138` (base workspace), while search results were saved under the harness phase workspace (`harness_20260119_191155/session_phase_1/search_results`).  
   - Result: `Search results directory not found` error; research corpus was not built in the phase workspace, so verification relied on narrative summaries instead of artifacts.

2. **Email task failed rubric requirements (Phase 1 and Phase 3)**:
   - Phase 1: **two separate emails** were sent (PDF + HTML). The task demanded a **single email with both attachments**.
   - Phase 2/3: HTML was **embedded in the email body**, not attached as a file. The rubric requires **HTML attachment**.
   - Phase 3: Judge also noted missing explicit subject/body evidence in the verifier-visible output.

3. **HTML report verification failed due to truncated artifact preview**:
   - The judge repeatedly said the HTML preview was truncated in the “Environmental Benefits” section and couldn’t verify Economic/Social/References.  
   - This is a **visibility issue**: the verifier sees only a short preview rather than the full HTML file.

4. **Retries re-run full pipeline**:
   - Each retry creates a new `session_phase_*` workspace. There is no automatic “skip completed tasks” or “reuse artifacts” logic.  
   - The agent manually copied artifacts forward in Phase 2 and Phase 3, but this is not enforced by the harness.

5. **Phase 4 incomplete email delivery**:
   - Phase 4 creates a ZIP and uploads it, but there is **no final email send** recorded in the log after the upload.  
   - The run log ends after `upload_to_composio` for the ZIP attachment; no `COMPOSIO_MULTI_EXECUTE_TOOL` send is shown.

---

## 4) Answers to Your Specific Questions
1. **Did we get the Gmail ID?**  
   Yes, multiple message IDs are present in the log (see Section 2). The issue is not missing IDs, but rubric mismatch and evidence presentation.

2. **Did we finally get judge evaluation feedback?**  
   Yes. The judge provided explicit reasoning in Phase 1 and Phase 3. Failures are specifically about:
   - email format vs rubric,
   - missing attachment evidence,
   - truncated HTML preview,
   - truncated or unverifiable research content.

3. **Why did it rerun everything instead of resuming?**  
   The harness creates a new session workspace for every retry. It does not auto-detect completed tasks or carry artifacts forward. The agent copied files manually in Phase 2 and Phase 3, but this is not a built-in resume mechanism.

---

## 5) Suggested Fixes (No Code Changes Yet)
1. **Enforce correct `session_dir` for `finalize_research`**  
   Ensure research tools always use the **phase workspace** (the one with `search_results/`).

2. **Adjust email behavior to match rubric**  
   The rubric expects **one email** with **both HTML and PDF attached**.  
   Avoid embedding HTML in the body as a substitute for an attachment.

3. **Provide full HTML evidence to the judge**  
   Ensure the verifier can read enough of the HTML to confirm all sections exist (Economic, Social, References).  
   The current short preview causes false negatives.

4. **Implement artifact reuse on retry**  
   If a phase fails only on a single task, reuse prior artifacts for completed tasks instead of re-running all tasks.

5. **Record email delivery evidence as an artifact**  
   Store the Gmail send response (message ID, subject, recipient, body, attachment list) in a standard artifact location the evaluator can check.

---

## 6) Final Assessment
The run is very close to success. The primary blockers are rubric alignment (single email with both attachments), evidence visibility (HTML preview truncation), and consistent use of the phase workspace for research finalization. Once those are addressed, the verification loop should pass without needing repeated full reruns.

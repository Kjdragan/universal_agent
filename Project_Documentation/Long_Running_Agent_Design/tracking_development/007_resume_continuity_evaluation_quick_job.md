# 007: Resume Continuity Evaluation (quick_resume_job v2)

Date: 2026-01-02
Run ID: deb3de4f-734e-472a-9914-77b74feabe9b
Workspace: /home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260102_104332
Job Spec: tmp/quick_resume_job.json (sleep 30s + file write + ls)

## Sources Reviewed
- Terminal output (job start + resume)
- run.log
- resume_packet_deb3de4f-734e-472a-9914-77b74feabe9b.md
- job_completion_deb3de4f-734e-472a-9914-77b74feabe9b.md
- transcript.md
- trace.json
- Logfire traces via MCP
- runtime_state.db (runs + tool_calls)

## Trace IDs (Logfire)
- Initial run: 019b7f97c9ec972a80292791d79aeef4
- Interrupt checkpoints: 019b7f98476cccbe8a78fcf40f8e31a9, 019b7f9848383b262a81a8c76cea5a48
- Resume run: 019b7f986570af50fdc524a39e4e098f

## Outcome (High Level)
The run resumed without prompting, completed the remaining steps, and produced the expected work product (resume_test.txt) and completion summary. The system did not re-run the interrupted sleep; instead, it checked for a running sleep process and moved on. This is functional recovery but not strict replay of the original in-flight tool call.

## Evidence of Expected Behavior
1) Auto-resume triggered without prompt
   - run.log shows "✅ Resume checkpoint loaded" followed by "✅ Resume packet constructed" and immediate tool execution.
2) Completion summary persisted
   - job_completion_...md lists status succeeded and side-effect receipts.
3) Resume packet persisted
   - resume_packet_...md captures the interrupted sleep tool call as running.
4) Work product created exactly once
   - work_products/resume_test.txt exists and contains a single timestamp + marker.

## Deviations / Non-Ideal Behavior
1) In-flight sleep not resumed
   - The interrupted tool call was `sleep 30` and remained in "running" state in the ledger.
   - On resume, the agent ran:
     - `ps aux | grep -E "sleep.*30"` (reported a sleep 300 process), then
     - proceeded with file creation and listing.
   - The original 30-second sleep was not completed post-resume.

2) Duplicate interrupt checkpoints
   - Logfire shows two interrupt checkpoints within ~200 ms.
   - Likely caused by double SIGINT handling in the timeout-based test harness.

3) Provider session resume not used (this run)
   - Resume output did not show "✅ Using provider session resume".
   - The initial run was interrupted before a ResultMessage, so provider_session_id could not be stored until the resume run completed.

## Tool Ledger (runtime_state.db)
Tool calls for run_id=deb3de4f-734e-472a-9914-77b74feabe9b:
- bash | running | ded186d80c0555d851c862d4873146e159298e964c96249bb1a218682f687ec1 (sleep 30, interrupted)
- bash | succeeded | f2352da3617e9c0e4ad4afa54ee7338a0309ef2d4364176695dd77e387e3a1fd (ps aux grep sleep)
- bash | succeeded | f4171ff34526428b44f92e5328ae1de8b7fdfbd80d03e5072c7aa1cd0a449dcf (date -Iseconds)
- bash | succeeded | 60155b4586bd8fcaf2ce4bcc96d4c8328d9c534163eb4351bd84df37d8402599 (mkdir -p work_products)
- Write | succeeded | a162ae8a2a227f877d86ddad9b9b5f4841f495c7f5ff477006c38429f0929d48 (resume_test.txt)
- bash | succeeded | a7b3a373da86456461e2acd65ad248d9b541d202e9db1cd4d06a0c1e8fd4b492 (ls -la)

No duplicate idempotency keys were observed for succeeded tool calls.

## Logfire Findings (Selected)
- interrupt checkpoints recorded in traces 019b7f98476c... and 019b7f984838... with checkpoint_type=interrupt.
- resume trace 019b7f986570... shows tool_use + ledger_mark_succeeded for the post-resume commands.

## Conclusion
Continuity/resume worked end-to-end for this test: the run resumed automatically and completed all remaining steps. The main gap is that in-flight tool calls are not resumed or replayed; the system relies on the agent to recover, which can skip the exact original step (sleep 30) and continue. This is acceptable for many workflows but weak for strict deterministic replay.

## Recommendations / Improvements
1) In-flight tool reconciliation
   - On resume, detect running tool calls and either re-run the exact command or mark as failed and require explicit policy for skipping.
2) Capture provider_session_id earlier
   - If SDK exposes session_id before ResultMessage, store it pre-emptively to enable provider resume even on early interrupts.
3) Resume audit trail
   - Record a structured resume decision in the summary (e.g., "in-flight tool skipped", "in-flight tool re-run") so behavior is auditable.


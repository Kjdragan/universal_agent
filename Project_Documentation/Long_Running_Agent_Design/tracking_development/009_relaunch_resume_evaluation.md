# 009: Relaunch Resume Evaluation (relaunch_resume_job.json)

Date: 2026-01-02
Run ID: a1a63c8f-9738-4d2c-9c8b-aa0ec3156dac
Workspace: /home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260102_135815
Job Spec: tmp/relaunch_resume_job.json (Task + HTML->PDF + sleep + email)

## Sources Reviewed
- Terminal output (start + resume)
- run.log
- resume_packet_a1a63c8f-9738-4d2c-9c8b-aa0ec3156dac.md
- job_completion_a1a63c8f-9738-4d2c-9c8b-aa0ec3156dac.md
- transcript.md
- trace.json
- Logfire traces via MCP
- runtime_state.db (runs + run_steps + tool_calls)

## Trace IDs (Logfire)
- Initial run: 019b804a0c20c62ca877188e2b4e457e
- Resume run: 019b804b102e23b569e22f33a15980c6

## Outcome (High Level)
The resume path successfully replayed the in-flight sleep tool call and continued execution. However, after finishing the resume continuation, the job prompt was run a second time in the same resume session, resulting in a duplicate email send. This indicates the durability system is replaying in-flight tools correctly, but the job-run flow re-enters the job prompt after recovery and causes duplicated side effects.

## Evidence of Expected Behavior
1) In-flight tool replay executed on resume
   - resume_packet shows `bash | running` (sleep 30) as the only in-flight tool.
   - Logfire shows `replay_mark_running` and `replay_mark_succeeded` for the sleep tool.
   - runtime_state.db tool_calls shows replay_status=succeeded for the sleep tool (idempotency_key 25c10c...).

2) Work products were created once
   - work_products/relaunch_report.html and work_products/relaunch_report.pdf exist and have single timestamps.
   - PDF size is consistent (161330 bytes).

## Evidence of Duplicate Side Effects
1) Two Gmail sends occurred
   - tool_calls contains two `COMPOSIO_MULTI_EXECUTE_TOOL` entries with different Gmail IDs:
     - call_f06f4f6e... => Gmail id 19b804c0fd3b339d
     - call_8354166d... => Gmail id 19b804c816e3fa6e
   - run.log shows two separate blocks of `COMPOSIO_MULTI_EXECUTE_TOOL` for GMAIL_SEND_EMAIL.

2) Resume session re-ran the job prompt
   - run.log shows the full job prompt starting again after the first “DONE” section.
   - transcript.md includes two sequences of “Step 4/Step 5” actions and two email sends.
   - run_steps contains two completed step rows with step_index=1 (step_id 7aa... and f771...).

## Tool Ledger (runtime_state.db)
Tool calls for run_id=a1a63c8f-9738-4d2c-9c8b-aa0ec3156dac (ordered by created_at):
- Task | succeeded | call_e833c4... (subagent report creation)
- write_local_file | succeeded | call_ca330c...
- bash | succeeded | call_2acea6... (chrome PDF conversion)
- bash | succeeded | call_9f5355... (sleep 30 replayed)
- bash | succeeded | call_c72a4f... (echo UA_TEST_EMAIL_TO)
- upload_to_composio | succeeded | call_f9158b...
- COMPOSIO_MULTI_EXECUTE_TOOL | succeeded | call_f06f4f... (email #1)
- bash | succeeded | call_1a21c7... (wc -c)
- COMPOSIO_SEARCH_TOOLS | succeeded | call_64ee2e...
- COMPOSIO_MULTI_EXECUTE_TOOL | succeeded | call_835416... (email #2)

## Logfire Findings (Selected)
- Replay flow uses a recovery query (“re-run the in-flight tool calls”) and completes the sleep replay before continuing.
- A new durable step is started after the replay step and proceeds to re-run parts of the job prompt (including another email send).
- The two email sends are visible in the same resume trace (019b804b...).

## Conclusion
The replay mechanism for in-flight tool calls is working (sleep is replayed deterministically), but the resume continuation runs the full job prompt again after completing recovery, leading to duplicate side effects (two emails). This is not expected for the durability invariant (“no duplicate external actions”) and should be addressed.

## Recommendations / Next Fix
1) Prevent re-running the job prompt after recovery completes
   - If the resume path already continues the job, do not auto-run the job prompt again in the same session.
   - Verify run_steps for the same step_index to avoid creating a second step.

2) Strengthen side-effect dedupe for multi-tool wrappers
   - Consider marking COMPOSIO_MULTI_EXECUTE_TOOL as REPLAY_IDEMPOTENT and/or dedupe on inner tool slug + args when tool_slug is GMAIL_SEND_EMAIL.
   - Ensure idempotency keys or receipts prevent a second send when resuming.

3) Update completion summary
   - Flag duplicate side effects in job_completion summary when multiple external tool calls with same logical action are observed.

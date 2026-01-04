# Durability Evaluation Report (Post Fix)

Date: 2026-01-04

## Scope
Validate a full complex run (search -> crawl -> report -> PDF -> upload -> email) after the Crawl4AI regression fix and confirm durability outputs, evidence classification, and trace visibility.

## Run Configuration
- Command: `./local_dev.sh` (non-interactive input via stdin)
- Query: "Get the latest information from the Russia-Ukraine war over the last three days. Create a detailed and comprehensive report. Save that report as a PDF and then Gmail that report to me at kevin.dragan@outlook.com."
- Env:
  - `UA_PRIMARY_EMAIL=kevin.dragan@outlook.com`
  - `UA_ENFORCE_IDENTITY_RECIPIENTS=1`
- Logs:
  - `/tmp/durability_eval_full_after_fix.log`
  - `/tmp/durability_eval_full_after_fix_resume.log`
  - `/tmp/durability_eval_resume_crash.log`
  - `/tmp/durability_eval_resume_crash_resume.log`

## Primary Run Summary
- Run ID: `c46670b6-7351-4494-83af-d0558229ac42`
- Main Trace ID: `019b8aaa2fbafba6b9ad86ca1b91ffb7`
- Workspace: `/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260104_141928`
- Search: `COMPOSIO_SEARCH_NEWS` + `COMPOSIO_SEARCH_WEB`
- Crawl + corpus: `mcp__local_toolkit__finalize_research` succeeded
  - Summary: 18 URLs extracted, 17 after blacklist, 17 successful crawls, 0 failed
  - Filtered corpus: 3 files used for report
- Report output:
  - HTML: `/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260104_141928/work_products/russia-ukraine-war-report-jan4-2026.html`
  - PDF: `/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260104_141928/work_products/russia-ukraine-war-report-jan4-2026.pdf` (660 KB)
- Upload: `mcp__local_toolkit__upload_to_composio` succeeded
- Email: `COMPOSIO_MULTI_EXECUTE_TOOL` succeeded with `GMAIL_SEND_EMAIL`

## Resume / Job Completion Summary
A resume run was used to emit the job completion summary and evidence classification:
- Resume Trace ID: `019b8aadd463965880fc2ae1ce7758d7`
- Job completion summary:
  - `/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260104_141928/job_completion_c46670b6-7351-4494-83af-d0558229ac42.md`
- Evidence summary (from job completion output):
  - Confirmed (receipts): Email sent, Upload to Composio/S3
  - Inferred (response claims): Email sent
  - Missing evidence: none

## Trace Visibility
- Local-toolkit trace IDs are now emitted in tool outputs and summarized in job completion:
  - Example list from job completion output:
    - `019b8aaaca5527d3b153d570391482cc`
    - `019b8aab1cdcb5256ff141fd001a5cfe`
    - `019b8aab1d41b71bee139489deab78fc`
    - `019b8aab2a73b58e22770f405514b9e5`
    - `019b8aab2b48a36cc0532fdd65b9bb00`
    - `019b8aab4147dba79f6aad3cc90b4bbb`
    - `019b8aab419bc2a1218baa7ea4586b3d`
    - `019b8aac23d949d3a6a3ef1d7ad9b1e2`
    - `019b8aad01ffc6af4a508b20ec0dd539`
    - `019b8aae1009c57d895e7f28be35f94c`

## Resumption Test (Crash + Resume)
- Crash run:
  - Run ID: `c34456a1-b4df-4688-aa55-35dd118dbca5`
  - Trace ID: `019b8ab58e36c6abe783646cef87c1cb`
  - Workspace: `/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260104_143153`
  - Crash hook: `UA_TEST_CRASH_AFTER_TOOL=GMAIL_SEND_EMAIL` with `UA_TEST_CRASH_MATCH=slug`
  - Crash point: after `mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL` (email send) before ledger commit
- Resume run:
  - Trace ID: `019b8ab8532bf277dcbfb1e8a1b4cb15`
  - In-flight tool replay succeeded; `COMPOSIO_MULTI_EXECUTE_TOOL` replayed successfully
  - Job completion summary emitted with evidence classification
- Evidence summary (resume output):
  - Confirmed (receipts): Email sent, Upload to Composio/S3
  - Inferred (response claims): Email sent, Upload to Composio/S3
  - Missing evidence: none

## Findings
- ✅ The Crawl4AI regression is resolved. `finalize_research` returned a valid JSON payload and completed 17/17 crawls with no coroutine validation failures.
- ✅ Full report creation, PDF generation, upload, and email succeeded in one run.
- ✅ Evidence classification printed in the job completion summary and correctly labeled receipts vs inferred claims.
- ✅ Local-toolkit trace IDs are now visible in output and summarized in the completion report.
 - ✅ Crash + resume path succeeds: after fault injection on the email send step, the resume replay completes and emits a correct job completion summary.

## Observations / Minor Issues
- Chrome headless still logs dbus warnings when generating PDFs (non-fatal).
- A warning appeared once during `finalize_research`: "Failed to save conversation turn" (non-blocking, no visible impact).
- Filtered corpus was only 3 files from 17 crawled items; not necessarily incorrect, but worth noting for report completeness checks.
 - Crash run emitted "Tool permission stream closed before response received" during shutdown after the forced crash; no impact on resume outcome.

## Conclusion
The durability pipeline now completes end-to-end on a complex task with proper output artifacts, evidence summary classification, and trace visibility. Crash + resume works on the email send step, and the prior Crawl4AI regression (async tool wrapper returning coroutine objects) is fixed and no longer forces fallback to local crawling.

If you want, I can append this report to the long-running progress doc or add Logfire trace links for the main + local-toolkit traces.

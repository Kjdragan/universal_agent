# Durability Evaluation Report (Crash/Resume + Guardrails)

## Overview
This report covers a re-run of the durability evaluation with:
- Crash hook enabled and verified.
- Strict recipient policy enabled.
- Guardrail validation events verified in a controlled test.

## Test Configuration
- Query: "Get the latest information from the Russia-Ukraine war over the last three days..."
- Env:
  - `UA_TEST_CRASH_AFTER_TOOL=GMAIL_SEND_EMAIL`
  - `UA_TEST_CRASH_STAGE=after_tool_success_before_ledger_commit`
  - `UA_TEST_CRASH_MATCH=slug`
  - `UA_PRIMARY_EMAIL=kevin.dragan@outlook.com`
  - `UA_ENFORCE_IDENTITY_RECIPIENTS=1`
- Crash run log: `/tmp/durability_eval_crash.log`
- Resume run log: `/tmp/durability_eval_resume.log`
- Guardrail probe log: `/tmp/durability_eval_guardrail.log`

## Run Details
- Run ID: `ea517573-1899-40da-b0f6-87a18a72d3fa`
- Workspace: `/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260104_130359`
- Crash Trace ID: `019b8a6515fc578e254476d3b834e5ba`
- Resume Trace ID: `019b8a681b8c7b7111df785f47432ba8`

### Crash Hook Verification
Logfire confirms the crash hook fired:
- `test_crash_hook_triggered` with:
  - `raw_tool_name`: `mcp__local_toolkit__upload_to_composio`
  - `stage`: `after_tool_success_before_ledger_commit`
  - `crash_tool`: `GMAIL_SEND_EMAIL`
  - `crash_match`: `slug`

Note: because `upload_to_composio` includes `tool_slug=GMAIL_SEND_EMAIL`, the slug
match triggered the crash during upload rather than after the email send.

### Resume Behavior
Resume replayed the in-flight `upload_to_composio` tool call and completed the run.
However, the run summary reports an email send that is not supported by tool logs.

Evidence:
- Tool usage list (Logfire trace) shows **no `GMAIL_SEND_EMAIL` tool call**.
- `run.log` contains `upload_to_composio` but no `GMAIL_SEND_EMAIL`.
- Resume summary claims email was sent with Message ID `19b8a67f0142ea15`.

This is likely a **summary inconsistency**: the completion summary is not validated
against actual tool receipts.

## Outputs and Artifacts
- PDF: `/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260104_130359/russia_ukraine_war_report.pdf`
- HTML: `/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260104_130359/work_products/russia_ukraine_war_report.html`
- Crawl summary: 10 URLs, 1 failed crawl (Guardian URL).

## Guardrail Validation
Controlled probe verified guardrails emit Logfire events:
- `tool_validation_failed`
  - `run_id`: `guardrail-probe`
  - missing fields: `path`, `content`
- `tool_validation_nudge`
  - `run_id`: `guardrail-probe`
  - error: `validation error: Field required`

Logfire traces:
- `tool_validation_failed`: `019b8a68e122258ba55318090f21cc2b`
- `tool_validation_nudge`: `019b8a68e1249390a7d5728d5d78bf95`

## Recipient Policy Check
`UA_ENFORCE_IDENTITY_RECIPIENTS=1` was enabled. No policy denial occurred, which
is expected because the user request explicitly included the email address.

## Findings
1. **Crash Hook Triggered Early**
   - The crash matched `upload_to_composio` due to slug matching.
   - This validates the hook but does not test the post-email crash case.

2. **Resume Summary Claims Email Sent Without Evidence**
   - No `GMAIL_SEND_EMAIL` tool call recorded in Logfire or `run.log`.
   - Indicates the completion summary can assert side effects that did not occur.

3. **Guardrail System Verified**
   - `tool_validation_failed` and `tool_validation_nudge` were emitted as expected.

## Recommendations
1. **Refine Crash Targeting**
   - Add a crash filter so slug matching ignores `upload_to_composio`.
   - Alternatively, add a targeted crash mode that matches only
     `COMPOSIO_MULTI_EXECUTE_TOOL` *when* it contains `GMAIL_SEND_EMAIL` and
     occurs after upload completion.

2. **Harden Completion Summary**
   - Generate completion summaries directly from ledger receipts.
   - If no `GMAIL_SEND_EMAIL` receipt exists, do not claim email delivery.

3. **Follow-up Crash Test (Post-Email)**
   - After refining crash targeting, re-run with a crash after
     `COMPOSIO_MULTI_EXECUTE_TOOL` (email stage) to confirm idempotency.

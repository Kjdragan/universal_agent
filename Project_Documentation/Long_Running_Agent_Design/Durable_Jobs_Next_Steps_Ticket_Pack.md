# Durable Jobs — Ticket Pack (Next Steps After Phases 0–2 + Resume/Fork Wiring)

Date: 2026-01-02  
Owner: AI coder (implementation)  
Goal: **Harden durability** under worst-case crash timing, prove “no duplicate side effects”, and make tool classification maintainable — **without breaking the working resume system.**

## Guardrails (do this before coding)
- **Do not refactor** the runner architecture broadly. Keep changes localized.
- Add/extend tests **first** where possible (or alongside changes).
- Every ticket must include:
  - a *repro command*
  - a *pass/fail signal*
  - a *regression check* (must not break existing relaunch/job tests)

---

## Ticket 1 — Failure Injection Hooks (Deterministic Crash at the Worst Moment)

### Why
The “double-send trap” happens when the process dies **after an external tool succeeded** but **before the ledger marks it succeeded**. You need a deterministic way to crash at exact points.

### Scope
Add **test-only** crash knobs:
- `UA_TEST_CRASH_AFTER_TOOL=<normalized_tool_name>` (e.g., `gmail_send_email`, `upload_to_composio`, `bash`)
- `UA_TEST_CRASH_AFTER_TOOL_CALL_ID=<tool_call_id>`
- Optional: `UA_TEST_CRASH_AFTER_PHASE=<phase>` and/or `UA_TEST_CRASH_AFTER_STEP=<step_id>`

Crash behavior:
- Raise a hard exception *after* receiving the tool result but *before* marking `SUCCEEDED` in ledger/DB.

### Implementation Notes
- Put the hook in the narrowest place: right after tool execution returns, right before ledger finalize/update.
- Ensure the crash hook is **no-op** unless env var is set.
- Log a very explicit line like:
  - `💥 UA_TEST_CRASH_AFTER_TOOL triggered: <tool_name> <tool_call_id>`

### Acceptance Criteria
- Running with the env var reliably crashes at the intended point.
- Resume does **not** duplicate side effects.
- No behavior change when env vars are unset.

### Tests
- New test run:
  - `UA_TEST_CRASH_AFTER_TOOL=gmail_send_email PYTHONPATH=src uv run python -m universal_agent.main --job tmp/relaunch_resume_job.json`
  - Resume:
  - `PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>`
- Pass: only **one** email in inbox; ledger shows one succeeded send; no duplicates by idempotency key.

---

## Ticket 2 — Enforce “PREPARED Before Execute” Invariant Everywhere

### Why
Durability depends on this invariant:
1) Write `PREPARED` (or equivalent) row to DB  
2) Execute tool  
3) Write `SUCCEEDED` + receipt/result  
If any path does “execute then write”, a crash can cause double-send.

### Scope
Audit and fix all tool execution pathways:
- Composio tools
- MCP tools
- Bash tool
- Task (subagent) relaunch path

### Implementation Notes
- Add a lightweight helper / assert:
  - `ledger.assert_prepared(tool_call_id)` right before execute
- If missing prepared entry, create it immediately (but ideally that never happens).

### Acceptance Criteria
- For every tool call, DB shows a `PREPARED` row **before** the external call begins.
- Crash injection (Ticket 1) never causes a second external side effect after resume.

### Tests
- Add a DB check helper used by tests:
  - Query tool_calls ordered by `created_at` vs `executed_at` (or your timestamps) to prove ordering.
- Regression: run the existing successful resume job and ensure it still passes.

---

## Ticket 3 — “Unknown side_effect_class” Must Fail Safe

### Why
If `side_effect_class` is missing/invalid, treating it as “don’t dedupe” is unsafe. Unknown must default to conservative behavior.

### Scope
Update dedupe decision logic (on_pre_tool_use_ledger) to:
- If `replay_policy` exists: obey it.
- Else if `side_effect_class` is missing/invalid: treat as **external/unknown** → dedupe/idempotent protection.
- Only treat as read-only when explicitly classified.

### Implementation Notes
- Introduce a canonical enum set:
  - `{"external","memory","local","read_only"}`
- Anything else → `external` (or `unknown_external`) at runtime.
- Log a warning once per run:  
  `⚠️ Invalid side_effect_class '<x>' for tool '<tool>'; defaulting to external`

### Acceptance Criteria
- No crash path can ever “accidentally” re-run an external call due to missing classification.
- Existing tool_policies.yaml behavior unchanged for valid entries.

### Tests
- Unit test: create a ledger entry with invalid class and verify dedupe path triggers.
- Integration: temporarily remove a policy line for a known external tool and ensure it still dedupes.

---

## Ticket 4 — Tool Policy System Hardening + Minimal Maintainer UX

### Why
You want maintainable classification without hardcoding everything in main.py.

### Scope
Improve YAML-based policies (tool_policies.yaml):
- Validate schema at startup.
- Support:
  - `tool_name_regex`
  - `namespace` (mcp/composio/claude_code)
  - `side_effect_class`
  - `replay_policy` (e.g., `REPLAY_EXACT`, `REPLAY_IDEMPOTENT`, `RELAUNCH`)
- Add an “explain” CLI mode:
  - `--explain-tool-policy "<raw_tool_name>"` prints resolved classification/policy.

### Implementation Notes
- Keep defaults conservative.
- Allow override via `UA_TOOL_POLICIES_PATH`.
- Load base policies + optional overlay policies.

### Acceptance Criteria
- Bad YAML fails fast with a clear error.
- Explain mode works and matches runtime classification.

### Tests
- Unit tests for policy matching + precedence rules.
- Regression: existing jobs run unchanged with current YAML.

---

## Ticket 5 — Subagent Durability: Make RELAUNCH Explicit + Record Subagent Outputs as Artifacts

### Why
Claude Agent SDK doesn’t resume in-flight TaskOutput across restart. Your strategy is correct: RELAUNCH Task.  
But you should (a) make it explicit in policies, and (b) capture subagent output deterministically so relaunch is not too expensive.

### Scope
- Ensure `Task` tool is always classified:
  - `replay_policy: RELAUNCH`
- Persist subagent outputs at boundaries:
  - When subagent finishes, save:
    - `subagent_output.json` (structured)
    - `subagent_summary.md`
    - optionally `subagent_trace.md`
- On resume:
  - if output exists and validated, skip relaunch (treat as completed step)
  - else relaunch

### Implementation Notes
- Key: define a deterministic `task_key` per step so relaunch is consistent.
- Output validation can be minimal: file exists + non-empty + matches expected schema keys.

### Acceptance Criteria
- Killing during subagent work: resume relaunches and completes.
- Killing after subagent completion: resume **does not** redo subagent work.

### Tests
- Failure matrix:
  - Kill during Task → resume → completes.
  - Kill after Task completes but before next step → resume → uses persisted output.

---

## Ticket 6 — Provider Session Persistence in Runtime DB (Resilience + Less Prompt Injection)

### Why
Your session continuity probe showed server-side session state exists and supports resume/fork. Persist it to reduce reliance on resume packet injection.

### Scope
- Persist `provider_session_id` (from ResultMessage.session_id) into runtime DB for:
  - run-level metadata
  - optionally checkpoint-level metadata
- On resume:
  - attempt provider resume with `continue_conversation=True` and `resume=<provider_session_id>`
  - if invalid/expired: invalidate and fall back to fresh session (already implemented — ensure it’s robust)

### Implementation Notes
- Make sure capture happens on all paths (classify/simple/complex).
- Keep the resume packet as fallback; do not remove it.

### Acceptance Criteria
- Resume works even with no transcript re-injection (provider session recovers).
- Negative control: clearing provider session id forces fallback to fresh and does not crash.

### Tests
- Add a small probe test in-repo (or keep script) that:
  - runs initial → resume without history → recall passes

---

## Ticket 7 — Runbook + “One Command” Durability Smoke Test Script

### Why
You’ll keep doing this forever. Make it dead simple.

### Scope
Add `scripts/durability_smoke.py` (or bash) that:
- starts the job
- prints run_id + resume command
- optionally auto-kills at a configured point (uses Ticket 1 env vars)
- resumes and verifies DB invariants

### Verification
- Query `tool_calls` for duplicates by idempotency key.
- Verify artifacts exist in workspace.
- Optionally check email sent count (if you can query Gmail via tool; otherwise manual step).

### Acceptance Criteria
- A single script can demonstrate:
  - crash → resume → complete
  - no duplicate side effects (DB query returns 0 rows)

---

## Ticket 8 — Expand the Durability Test Matrix (Documented, Repeatable)

### Why
You need coverage across the sharp edges.

### Scope
Add a markdown doc `docs/durability_test_matrix.md` with:
- test cases
- kill points (A/B/C)
- expected outcomes
- commands
- DB queries to confirm dedupe/no-duplication

Minimum matrix:
- Kill during subagent Task (RELAUNCH)
- Kill after PDF render but before upload
- Kill after upload but before email
- Kill after email success but before ledger mark (Ticket 1)
- Kill during read-only step (should just replay/exact or rerun)

### Acceptance Criteria
- Anyone can run the matrix and interpret pass/fail quickly.

---

## Deliverables Checklist
- [ ] Crash injection env vars + docs
- [ ] Prepared-before-execute invariant verified and enforced
- [ ] Fail-safe handling for unknown/missing side_effect_class
- [ ] Hardened tool policy YAML loading + explain mode
- [ ] Subagent output artifact persistence + RELAUNCH semantics
- [ ] Provider session id persisted + robust resume/fallback
- [ ] Smoke test script
- [ ] Durability test matrix doc

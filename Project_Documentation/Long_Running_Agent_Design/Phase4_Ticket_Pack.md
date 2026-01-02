# Phase 4 Ticket Pack — Durable Jobs (Operator + Background Worker + Policy Audit + Receipts + Triggers)
Date: 2026-01-02  
Owner: AI Coder (implementation), Kevin (review/approval)  
Scope: Add **operator UX** + **background execution** + **policy hardening** + **receipt visibility** + **trigger skeletons** on top of the already-working Phases 0–3 (RunSpec, ledger, checkpoints, resume continuity, RELAUNCH subagent handling, provider session capture).

## Non‑negotiables (don’t break what works)
1) **Existing CLI job-mode resume must keep working** (the relaunch/resume tests should still pass).
2) **Idempotency invariant stays true**: PREPARED → EXECUTE → SUCCEEDED (or FAILED) with “no double side-effects on crash” as the prime directive.
3) **No new hard dependency on always-available server-side conversation**: provider session is an optimization; resume packet + ledger remains the fallback.
4) **Observability stays**: keep Logfire/OTEL hooks intact; do not regress trace output.

---

## Ticket 1 — Operator CLI (runs list/status/tail/cancel)
### Goal
Add an operator-facing CLI that makes the system usable like a job runner, not only an interactive agent script.

### User stories
- “Show me my recent runs and their status.”
- “Show me details for run <id> (mode, job spec, last checkpoint, last tool calls).”
- “Tail logs for a run workspace (and optionally DB tool_calls).”
- “Cancel a run safely.”

### Deliverables
- New command group: `ua` (or `python -m universal_agent.operator`) with subcommands:
  - `runs list [--limit N] [--status ...]`
  - `runs show --run-id <id>`
  - `runs tail --run-id <id> [--follow] [--source log|db|both]`
  - `runs cancel --run-id <id> [--reason "..."]`
- Cancelling is **durable**: sets run status to `cancel_requested` and causes worker/runner to stop at the next safe boundary.

### Acceptance criteria
- `runs list` shows at least: run_id, created_at, status, mode(job/interactive), workspace path.
- `runs show` prints:
  - run metadata (mode/job spec path)
  - provider session id (if any)
  - last checkpoint id/time
  - last 10 tool calls (name, status, idempotency key)
- `tail` works against:
  - workspace `run.log` (or current logging file)
  - DB tool_calls view (updates as tools execute)
- `cancel`:
  - sets cancel flag in DB
  - runner checks flag between steps/tools and stops cleanly
  - final status becomes `cancelled` (not `failed`)

### Suggested implementation notes
- Add a small `operator/` module:
  - `operator_cli.py` using `argparse` or `typer`
  - `operator_db.py` querying runtime_state.db
- Cancel mechanism:
  - new DB field on `runs`: `cancel_requested_at`, `cancel_reason`
  - runner checks `should_cancel(run_id)` at safe points:
    - before/after each tool call
    - before/after each step transition
- Keep commands read-only unless explicitly canceling.

---

## Ticket 2 — Worker Mode (background run execution + leasing/heartbeat)
### Goal
Enable runs to execute in the background and survive shell/session closure. This is the “durable job runner” mode.

### User stories
- “Start a job and detach; it keeps running.”
- “If worker dies, another worker can pick up the run.”
- “Worker indicates liveness, so stuck leases can be reclaimed.”

### Deliverables
- A worker entrypoint:
  - `python -m universal_agent.worker --poll` (or `ua worker start`)
- DB-backed leasing:
  - runs acquire a lease: `lease_owner`, `lease_expires_at`
- Heartbeat:
  - worker updates lease expiry periodically
- Runner integration:
  - worker executes eligible runs (status `queued` or `running` with expired lease)
- Optional: `--once` mode processes a single run then exits.

### Acceptance criteria
- Two workers can run concurrently without double-processing the same run.
- If worker A is killed, and its lease expires, worker B can safely continue the run via `--resume` logic.
- Heartbeat interval and lease TTL are configurable (env vars):
  - `UA_WORKER_HEARTBEAT_SEC`
  - `UA_WORKER_LEASE_TTL_SEC`

### Suggested implementation notes
- DB fields on `runs`:
  - `status` (queued/running/succeeded/failed/cancel_requested/cancelled)
  - `lease_owner` (string)
  - `lease_expires_at` (timestamp)
  - `last_heartbeat_at`
- Acquire lease transaction:
  - `UPDATE runs SET lease_owner=?, lease_expires_at=? WHERE run_id=? AND (lease_expires_at IS NULL OR lease_expires_at < now)`
- Worker loop:
  - poll for eligible runs
  - acquire lease
  - call same runner logic used by CLI resume/job mode
- **Important**: worker must preserve idempotency behavior; all tool calls still go through ledger/gateway.

---

## Ticket 3 — Tool policy audit + unknown-tool detection
### Goal
Prevent “silent footguns” where a new tool appears and gets misclassified (e.g., treated as read-only when it is not, or vice versa). Also produce a policy audit report.

### Deliverables
- Unknown tool detection:
  - on first-seen tool identity, classify conservatively as side-effect (`external`) unless policy says otherwise
  - log a structured warning with a stable code (e.g., `UA_POLICY_UNKNOWN_TOOL`)
  - optionally write to `policy_audit/unknown_tools.jsonl`
- Policy audit command:
  - `ua policy audit` outputs:
    - counts by `side_effect_class`
    - list of tools without explicit policy matches
    - list of tools whose observed inputs changed across run_id (helps detect nondeterminism)

### Acceptance criteria
- Any tool identity not matched by `tool_policies.yaml` is detected and reported.
- Default behavior remains safe: unknown == side-effect (dedupe/idempotent protection).
- Audit report saved to workspace or a standard output location.

### Suggested implementation notes
- Extend classification code to return:
  - `matched_policy: bool`
  - `policy_rule_id: Optional[str]`
- Store this in DB on tool_calls for later inspection.

---

## Ticket 4 — Side-effect receipt summary view + export
### Goal
Make it easy to prove “no duplicate external actions happened,” and to share evidence.

### Deliverables
- Run receipt summary view:
  - `ua runs receipts --run-id <id> [--format md|json]`
- Receipt includes for each side-effect tool call:
  - timestamp, tool_name, namespace, status
  - idempotency_key
  - replay_policy / side_effect_class
  - external receipt ids (e.g., Gmail message id, upload key) if available from tool results

### Acceptance criteria
- Produces a concise “what happened” artifact suitable for attaching to issues/PRs.
- Includes enough info to demonstrate idempotency correctness after resume.

### Suggested implementation notes
- Tool results aren’t in the ledger today—only ToolUse. If you want external receipt ids:
  - store a small `result_summary` field for side-effect tools (safe subset) in DB when marking SUCCEEDED
  - e.g., `{"gmail_message_id":"...","thread_id":"..."}`
- Keep result summaries bounded in size; avoid storing full payloads.

---

## Ticket 5 — Schedule/trigger skeleton (cron → create_run; webhook → append_to_run)
### Goal
Lay down the smallest viable structure for triggers without committing to full infra yet.

### Deliverables
1) Cron skeleton
   - A script: `scripts/cron_create_run.py`
   - Reads a RunSpec JSON path and creates a new run in DB as `queued`
   - Minimal env var interface:
     - `UA_RUNSPEC_PATH`
     - `UA_TRIGGER_NAME`
2) Webhook skeleton
   - FastAPI app (optional minimal):
     - `POST /trigger/<name>` creates run OR appends message to existing run thread
   - Authentication: simple shared secret header (for now)

### Acceptance criteria
- Cron script can be executed locally and results in a queued run that worker can pick up.
- Webhook can be started locally and successfully enqueues a run.
- No production deployment required in this ticket; this is structural scaffolding.

### Suggested implementation notes
- “append_to_run” can be postponed; start with create_run only.
- For append semantics, you’ll likely want:
  - a `run_events` table (run_id, event_type, payload, created_at)
  - runner consumes events if in “long-running thread” mode
- Keep it simple and don’t overbuild.

---

## Recommended sequencing
1) Ticket 1 (Operator CLI) — unlocks easier debugging/visibility
2) Ticket 2 (Worker Mode) — enables real “long-running” usage
3) Ticket 4 (Receipts) — makes correctness auditable
4) Ticket 3 (Policy audit) — prevents drift and surprises
5) Ticket 5 (Triggers) — scaffolding after the runner is stable

---

## Regression test checklist (must run after each ticket)
- `relaunch_resume_job.json` — kill during Task, resume, ensure success + no duplicate email.
- Crash injection (if available): crash after email success but before DB SUCCEEDED → resume must not re-send.
- `ua runs list/show/tail` works with existing runtime_state.db.
- Worker lease test: start worker A, kill it, wait TTL, start worker B → run continues once.


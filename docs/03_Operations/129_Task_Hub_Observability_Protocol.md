# Task Hub Observability Protocol

**Last updated:** 2026-05-11 PM (Ship 4 — LLM cron path wired, 4 housekeeping crons flipped opt-in, canonical doc minted)

## Purpose

This protocol is the **repository-wide standard** for the observability + recovery wiring that every new async unit of work in `src/universal_agent/` MUST follow.

The operator's stated principle is: **"centralize everything through Task Hub."** Phase F of the Hermes adaptation shipped the schema, helpers, classifier, and three spawn-site wirings (cron `!script`, VP CLI, demo workspace) that make this real. Ship 4 closed the gap on the LLM cron path AND flipped the four housekeeping crons that had been opt-out, then codified the resulting six-rule protocol in this doc so future contributors apply it by default.

If you are adding a new cron job, scheduled GHA workflow that produces persistent state, webhook handler, on-demand operator-triggered async action, or any other async unit of work — **read this doc, apply the protocol, and add tests that pin the wiring.** Don't skip it because "this one's simple" — the value compounds when every async unit is observable through the same dashboard and the same recovery verbs.

## Scope

**This protocol applies to:**

- Cron jobs (both `!script` subprocess and in-process LLM execution paths)
- VP missions (CLI subprocess and SDK in-process)
- Demo workspace subprocesses (`run_in_workspace`)
- Webhook handlers that fire async work (e.g., Composio webhook handler enqueues a task)
- Scheduled GitHub Actions workflows that produce persistent task state (e.g., the openclaw release sync writes vault entries)
- On-demand operator actions that produce a `task_hub_items` row (e.g., dashboard "rerun" buttons)
- Any new async unit of work added to `src/universal_agent/` going forward

**This protocol does NOT apply to:**

- Pure event handlers that produce no persistent state (e.g., HUD event ingestion)
- Single-shot CLI scripts that exit and write no `task_hub_*` rows
- Frontend-only UI code
- Read-only health endpoints (`/api/v1/version`, `/api/v1/health`)

When in doubt: **if the unit of work produces a row downstream consumers can act on later, apply the protocol.** Even if the work is "trivial" today, having identity + run history means tomorrow's operator dashboard can surface it without a schema change.

## The six rules

These rules map 1:1 to the helpers shipped in PRs #230, #238, #239, #240. Each rule has an explicit helper API; do NOT bypass.

### Rule 1 — Identity

Every unit of work has a row in `task_hub_items`. The row is the unit's identity in the system.

- **Stable identity (cron-style, perpetual):** Use `ensure_cron_task_link(conn, system_job=...)`. Returns `task_id="cron:<system_job>"`. The row is created on first call and reused on every subsequent tick — there is exactly one row per cron source.
- **Per-mission identity:** Use the producer-side `task_hub.upsert_item(...)` to insert a row with a fresh `task_id` (e.g., `vp_mission:<uuid>`). One row per mission instance.
- **Operator-triggered identity:** Same pattern as per-mission — the operator action's handler inserts a fresh row.

**Why:** without a `task_hub_items` row, the work is invisible to the dashboard, dispatch sweep, recovery verbs, and the operator's Task Hub view. Identity is the precondition for everything else.

### Rule 2 — Claim ledger

When the work is claimed (cron tick fires, VP claims a mission, demo workspace dispatches), append a `task_hub_assignments` row.

- **Cron pattern:** `ensure_cron_task_link` does this for you automatically — it inserts both the task row (if missing) AND a fresh assignment row per tick.
- **Dispatch pattern:** `task_hub.claim_next_dispatch_tasks(limit=N)` does this — every claimed task gets an assignment row.
- **Manual pattern:** if you're writing custom claim logic, `task_hub` exposes lower-level helpers. Prefer composing `claim_next_dispatch_tasks` over hand-rolling.

**Why:** the assignment row is "who's working on this right now." Without it, two workers can claim the same task. The state machine (`running` → `completed`/`failed`) is the operator's view of liveness.

### Rule 3 — Run history

Each attempt (each tick, each mission run, each subprocess execution) opens and closes a row in `task_hub_runs`.

- **Open at attempt start:** `task_hub._open_run(conn, task_id=..., assignment_id=..., agent_id=...)`. Returns `run_id`. Records `started_at`. (For the cron pattern, `ensure_cron_task_link` already opens the run for you — don't double-open.)
- **Close at attempt end:** `task_hub._close_run(conn, assignment_id=..., outcome=..., summary=..., error=..., metadata={...})`. Records `ended_at` and the outcome bucket.

The five outcome buckets (from `classify_worker_exit`):

| Outcome | Meaning |
|---|---|
| `clean_exit_zero` | rc=0 AND the linked task transitioned off `in_progress` — happy path. |
| `clean_exit_zero_no_disposition` | rc=0 BUT the linked task is still `in_progress` — protocol violation (Rule 5). |
| `nonzero_exit` | rc != 0 (subprocess returned an error, or in-process coroutine raised). |
| `signaled` | Process killed by a signal (SIGKILL, SIGTERM, SIGSEGV, etc.). |
| `timeout_killed` | Timeout watchdog killed the process. |

**Why:** runs are the "what happened?" view per attempt. Identity says "this is the work," assignments say "this worker has it," runs say "this attempt produced this outcome." Without runs, the dashboard can only show present-tense state; with runs, it can show history.

### Rule 4 — Subprocess identity (if applicable)

If the work spawns a subprocess:

- **Record the PID immediately after spawn:** `task_hub.record_worker_pid(conn, assignment_id=..., worker_pid=proc.pid)`. This stamps the `task_hub_assignments.worker_pid` column.
- **Resolve the timeout from the task row:** `task_hub.resolve_max_runtime_seconds(task)`. Returns the per-task override OR the gateway default. Use this for `asyncio.wait_for(..., timeout=...)` so operators can tune per-task without code changes.

For in-process work (LLM coroutine, SDK call), `worker_pid` stays NULL — the schema explicitly allows it. Do NOT synthesize a fake PID.

**Why:** when a task is stuck, the operator needs to know the OS PID to attach `strace` or kill it directly. The PID column on the assignment row is the dashboard's escape hatch.

### Rule 5 — Protocol violation routing

When the worker exits cleanly (`rc=0` or the coroutine completes normally) BUT the linked task is still `in_progress`, the worker has violated the disposition protocol — it ran successfully but failed to mark the work done.

**Required action:** route the task to `needs_review` via:

```python
from universal_agent.services.worker_exit_classifier import (
    classify_worker_exit, park_task_for_protocol_violation, PROTOCOL_VIOLATION_REASONS,
)

exit_class = classify_worker_exit(
    return_code=rc, was_signaled=signaled,
    was_timeout_killed=timed_out, task_closed_normally=task_was_closed_normally(conn, task_id),
)
if exit_class.is_protocol_violation:
    park_task_for_protocol_violation(
        conn, task_id=task_id, site="cron" | "vp_cli" | "demo" | "<new_site>",
        summary="clean exit but task not closed",
    )
```

The `site` argument keys into `PROTOCOL_VIOLATION_REASONS` (currently `{"cron", "vp_cli", "demo"}`). Adding a new site requires extending the dict — that's intentional so a new site can't silently use a wrong reason string.

**Why:** clean-exit-no-disposition is the most insidious failure mode. The subprocess returned success, but the work isn't actually done. Without F.3 routing, those tasks stay `in_progress` forever and the next tick re-claims them, producing endless retries. Routing to `needs_review` makes them visible to the operator dashboard and stops the loop.

### Rule 6 — Standard recovery verbs

When a task lands in `needs_review` (or otherwise gets stuck), use the canonical Simone-callable recovery verbs. Do NOT write per-site recovery logic.

The four verbs:

| Verb | When to use |
|---|---|
| `task_rehydrate` | Restart the task from scratch (clean slate; new assignment, new run). |
| `task_re_evaluate` | Re-run with the same inputs; useful when transient errors are suspected. |
| `task_request_revision` | Send the task back to the producer with feedback (e.g., "scaffold was missing a config field"). |
| `task_redirect_to` | Reassign to a different agent/handler (e.g., from Cody to Atlas). |

These verbs are exposed both as Simone's `@tool` wrappers (in `src/universal_agent/tools/task_hub_simone_verbs.py`) AND as operator dashboard buttons (Task Hub UI). They flip the task's `status` and emit the right event chain so the dispatch sweep picks it up correctly.

**Why:** centralization of recovery semantics means the operator learns ONE workflow that applies to every task type. No per-site special cases.

## Helper API reference

```python
from universal_agent import task_hub
from universal_agent.services.worker_exit_classifier import (
    classify_worker_exit,
    park_task_for_protocol_violation,
    PROTOCOL_VIOLATION_REASONS,  # {"cron", "vp_cli", "demo"} → reason strings
    find_active_assignment_for_task,
    task_was_closed_normally,
)
from universal_agent.services.cron_task_hub_link import (
    ensure_cron_task_link,
    close_cron_task_link,
)

# ── Cron pattern (recommended for any tick-based perpetual task) ──
task_id_and_assignment = ensure_cron_task_link(
    conn,
    job_id=job.job_id,
    job_metadata=job.metadata,   # {"system_job": "...", "skip_task_hub_link": False}
    description="One-line description shown in the dashboard.",
)
# Returns {"task_id": "cron:<system_job>", "assignment_id": "asg_cron_..."}
# OR None if skip_task_hub_link=True OR no task_id derivable.

# ── General pattern (when the task already exists) ──
assignment = task_hub.claim_next_dispatch_tasks(conn, limit=1)
# ... then ...
run_id = task_hub._open_run(
    conn, task_id=task_id, assignment_id=assignment_id, agent_id=agent_id,
)

# ── Subprocess identity (Rule 4) ──
proc = await asyncio.create_subprocess_exec(...)
task_hub.record_worker_pid(
    conn, assignment_id=assignment_id, worker_pid=proc.pid,
)
timeout_seconds = task_hub.resolve_max_runtime_seconds(task_row)

# ── Close-out (Rules 3 + 5) ──
exit_class = classify_worker_exit(
    return_code=rc,
    was_signaled=signaled,
    was_timeout_killed=timed_out,
    task_closed_normally=task_was_closed_normally(conn, task_id),
)
task_hub._close_run(
    conn,
    assignment_id=assignment_id,
    outcome=exit_class.outcome,
    summary=summary_text,
    error=error_text,
    metadata={"worker_exit": exit_class.to_dict(), "site": "cron"},
)
if exit_class.is_protocol_violation:
    park_task_for_protocol_violation(
        conn,
        task_id=task_id,
        site="cron",  # or "vp_cli" / "demo" / new site
        summary="clean exit but task not closed",
    )

# ── Cron lifecycle reset (cron pattern only) ──
close_cron_task_link(
    conn, task_id=task_id, success=(rc == 0),
)
# Flips perpetual cron task back to "open" on success so the next tick
# can re-claim it. Leaves "needs_review" rows untouched (F.3 took
# precedence). No-op on non-cron tasks.
```

## Per-task-shape patterns

| Task shape | Pattern |
|---|---|
| Cron job spawning a `!script` subprocess | Auto-link via `ensure_cron_task_link`; subprocess flow records PID + classifies exit. **Already wired** in `cron_service.py` line ~1200. |
| Cron job running an in-process LLM call | Auto-link via `ensure_cron_task_link`; in-process flow records run with `worker_pid=NULL` + classifies exit (rc derived from coroutine success/exception). **Already wired** in `cron_service.py` line ~1287 (Ship 4). |
| VP mission via CLI subprocess | Caller passes `task_id` in `payload.metadata.task_id` → `run_mission` records PID + classifies exit. **Already wired** in `vp/clients/claude_cli_client.py`. |
| VP mission via SDK in-process | Same as VP CLI but `worker_pid=NULL`; classification derived from `MissionOutcome.status`. |
| Demo workspace subprocess | Caller passes `assignment_id` to `run_in_workspace`; PID recorded; classification on `RunResult`. **Already wired** in `services/cody_implementation.run_in_workspace`. |
| Webhook handler / on-demand operator action | Apply the six rules. Producer enqueues a `task_hub_items` row; consumer claims via `claim_next_dispatch_tasks`; the rest follows the pattern. |
| Scheduled GHA workflow producing persistent state | The workflow body POSTs to a gateway endpoint that opens a `task_hub_items` row. From the gateway's perspective it's an on-demand operator action. |
| **A NEW kind of work this table doesn't cover** | Apply the six rules at the natural boundaries. Don't invent a new dispatch primitive — extend the existing helpers if needed. |

## Worked example — adding a new cron job

```python
# In gateway_server.py:
def _ensure_my_new_cron_job() -> Optional[dict[str, Any]]:
    if not _cron_service or not _my_new_cron_enabled():
        return None
    return _register_system_cron_job(
        system_job="my_new_job",
        # Stays inside dormancy window (6 AM – 9 PM Houston) unless
        # exception applies — see docs/operations/operating_hours_dormancy.md
        default_cron="5 9 * * *",
        default_timezone="America/Chicago",
        command="!script universal_agent.scripts.my_new_job",
        description="One-line description shown in the dashboard.",
        timeout_seconds=600,
        enabled=True,
        cron_env_var="UA_MY_NEW_CRON",
        timezone_env_var="UA_MY_NEW_TIMEZONE",
        # Default: skip_task_hub_link=False (opt IN to F observability).
        # Only opt OUT for pure event handlers with no persistent
        # state — see the docstring of _register_system_cron_job for
        # the contract.
    )
```

That's it. The `cron_service.py` `!script` branch handles identity, claim ledger, run history, PID stamping, classification, and F.3 routing automatically. The protocol is enforced by composition, not boilerplate.

For an LLM-cron registration (using the metadata-dict pattern, like `autonomous_daily_briefing`), the same applies — just don't set `metadata["skip_task_hub_link"] = True`.

## Worked example — adding a new VP mission consumer

If you're adding a new agent that consumes a Task Hub task type (e.g., a new "Atlas" variant that handles `task_type=X`):

```python
# 1. The producer-side already creates the task_hub_items row (e.g.,
#    Simone enqueues task_type=X with appropriate metadata).
# 2. Your consumer claims via the dispatch sweep (no manual SQL).
# 3. When you spawn a subprocess to do the work:

import asyncio
from universal_agent import task_hub
from universal_agent.services.worker_exit_classifier import (
    classify_worker_exit, park_task_for_protocol_violation,
    task_was_closed_normally,
)

async def my_consumer(task_row, assignment_id):
    timeout_seconds = task_hub.resolve_max_runtime_seconds(task_row)
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "my.script.module",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    conn = open_task_hub_conn()
    try:
        task_hub.record_worker_pid(
            conn, assignment_id=assignment_id, worker_pid=proc.pid,
        )
        conn.commit()
    finally:
        conn.close()

    was_timeout = False
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        was_timeout = True
        proc.kill()
        stdout, stderr = b"", b""

    rc = proc.returncode
    conn = open_task_hub_conn()
    try:
        exit_class = classify_worker_exit(
            return_code=rc,
            was_signaled=bool(rc is not None and rc < 0 and not was_timeout),
            was_timeout_killed=was_timeout,
            task_closed_normally=task_was_closed_normally(
                conn, task_id=task_row["task_id"],
            ),
        )
        task_hub._close_run(
            conn,
            assignment_id=assignment_id,
            outcome="completed" if rc == 0 else "failed",
            summary=f"my_consumer {task_row['task_id']}",
            error=stderr.decode(errors="replace")[:500],
            metadata={"worker_exit": exit_class.to_dict(), "site": "my_consumer"},
        )
        if exit_class.is_protocol_violation:
            # Extend PROTOCOL_VIOLATION_REASONS in worker_exit_classifier.py
            # with a "my_consumer" entry before doing this — site is
            # validated.
            park_task_for_protocol_violation(
                conn,
                task_id=task_row["task_id"],
                site="my_consumer",
                summary="my_consumer clean exit but task not closed",
            )
        conn.commit()
    finally:
        conn.close()
```

If the work is in-process instead of a subprocess: skip `record_worker_pid` and synthesize `rc = 0 if no exception else 1`. The pattern is otherwise identical.

## Worked example — webhook handler (hypothetical)

A new Composio webhook arrives — it should enqueue a Cody task. The webhook handler is the **producer** in the protocol's terms. It only needs to:

```python
async def composio_webhook_handler(payload):
    conn = open_task_hub_conn()
    try:
        task_hub.upsert_item(
            conn,
            {
                "task_id": f"composio_webhook:{payload['event_id']}",
                "source_kind": "composio_webhook",
                "title": payload.get("summary", "")[:200],
                "status": task_hub.TASK_STATUS_OPEN,
                "metadata_json": json.dumps(payload),
            },
        )
        conn.commit()
    finally:
        conn.close()
```

Rules 1 (identity) and 2 (claim ledger when dispatched) apply automatically because the row will be picked up by the standard dispatch sweep. Rules 3–6 fire when the **consumer** runs.

The webhook handler itself is not a long-running task. It's an event ingestion point. The work it enqueues is the task that matters.

## Checklist for adding a new task type

Before merging a PR that introduces a new async unit of work:

1. ☐ Identity: producer inserts a `task_hub_items` row.
2. ☐ Claim ledger: consumer creates a `task_hub_assignments` row (via `claim_next_dispatch_tasks` OR `ensure_cron_task_link` OR manually if needed).
3. ☐ Run history: consumer calls `_open_run` at attempt start, `_close_run` at attempt end with one of the five outcomes.
4. ☐ Subprocess identity: if spawning a process, `record_worker_pid` is called immediately after spawn; `resolve_max_runtime_seconds` is used for the timeout.
5. ☐ Protocol violation routing: `classify_worker_exit` is called; if `is_protocol_violation`, `park_task_for_protocol_violation` is called with the appropriate `site`.
6. ☐ Standard recovery verbs: the new task type works with `task_rehydrate / task_re_evaluate / task_request_revision / task_redirect_to`. (Smoke test this — set up a row in `needs_review` and verify each verb behaves sanely.)
7. ☐ Test coverage: at least one test pins the wiring (e.g., a `test_<new_site>_clean_exit_no_disposition_triggers_protocol_violation` in the style of `test_hermes_phase_f_site_wiring.py`).
8. ☐ Site key registered: if adding a new `site`, extend `PROTOCOL_VIOLATION_REASONS` in `worker_exit_classifier.py`.
9. ☐ Dashboard surface: the new row appears in the operator's Task Hub view (no schema change should be needed — it inherits the existing dashboard plumbing).
10. ☐ Doc trace: add a row to the per-task-shape table above OR explain in the PR description why this work doesn't fit any existing shape.

## What this protocol does NOT do

- **Does not enforce business logic.** The protocol is concerned with observability + recovery, not with what the work itself produces. A cron that runs cleanly but produces wrong data is a separate bug class.
- **Does not replace per-site retry semantics.** Retries are configured per-task via `task_hub_items.max_retries`. The protocol provides outcome buckets that the retry logic acts on; it doesn't override the retry counts.
- **Does not auto-instrument existing code.** Every new spawn site or in-process loop must be explicitly wired. A lint guard (deferred to Ship 5) will flag PRs that introduce new spawn calls without importing from `worker_exit_classifier`.
- **Does not impose a heartbeat schedule.** Crons still register via the cron service; the dormancy default still applies (see `docs/operations/operating_hours_dormancy.md`). This protocol is orthogonal to scheduling policy.
- **Does not unify with the activity-event bus.** Activity events are a separate concern (UI-facing notifications, dashboard chrome). The Task Hub Observability Protocol is about durable state.

## Cross-references

- [`107_Task_Hub_And_Multi_Channel_Execution_Master_Reference_2026-03-31.md`](107_Task_Hub_And_Multi_Channel_Execution_Master_Reference_2026-03-31.md) — Task Hub master reference: schema, lifecycle, dispatch semantics.
- [`cron_job_registration.md`](cron_job_registration.md) — Cron registration patterns, dormancy default, `_register_system_cron_job` helper docstring.
- [`../reports/hermes-adaptation-phased-plan-2026-05-10.md`](../reports/hermes-adaptation-phased-plan-2026-05-10.md) — The Hermes adaptation phased plan that produced this protocol.
- [`../reports/hermes-ship-4-task-observability-protocol-plan.md`](../reports/hermes-ship-4-task-observability-protocol-plan.md) — Ship 4's implementation plan; the PR that minted this doc.
- [`../operations/operating_hours_dormancy.md`](../operations/operating_hours_dormancy.md) — Dormancy default; scope distinction between content-generation and infrastructure-event handlers.

## Future hardening

**Deferred to Ship 5 (lint guard):** a CI check that flags any new `subprocess.run / subprocess.Popen / asyncio.create_subprocess_exec` call in `src/universal_agent/` that doesn't import from `worker_exit_classifier`. The allowlist (`tests/unit/task_observability_coverage_allowlist.txt`) freezes the current set of "legitimate" spawn sites; new spawns must either be wired through the protocol OR explicitly added to the allowlist with a rationale.

This is "ratchet" semantics — the guard prevents drift but doesn't retroactively force every legacy spawn through the protocol. Over time, the allowlist shrinks as legacy sites get migrated.

**Other deferred work:** a parallel guard for in-process LLM coroutines (harder to detect statically; probably needs a runtime decorator pattern). Future PR.

## Revision history

- **2026-05-11 PM** — Ship 4: LLM cron path wiring + 4 housekeeping crons flipped opt-in + this doc minted. The protocol is now the standard for all new async work.
- **2026-05-11 (earlier)** — PR #240: cron `!script` task-link backfill; 8 work-producing crons opt-in to the F observability path. 4 housekeeping crons (`codie_proactive_cleanup`, `vp_coder_workspace_pruning`, `atlas_direct_dispatch`, `csi_demo_triage_rank`) opted out for "no work-product" rationale — later reversed in Ship 4 on the meta-observability principle.
- **2026-05-10** — Phase F initial implementation: classifier, helpers, three spawn-site wirings (cron `!script`, VP CLI, demo workspace). PRs #230, #238, #239.

# Hermes Ship 4 — Task Hub Observability Protocol: Detailed Implementation Plan

**Created:** 2026-05-11 PM
**Operator approval:** confirmed in session — Ship 4 + doc + CLAUDE.md row; lint guard deferred to future hardening.
**Purpose:** This is the operational source of truth for completing Phase F observability coverage AND codifying the Task Hub Observability Protocol as the standard for all new tasks in this repo. The plan is intentionally self-contained so a fresh- or compacted-context Claude session can execute it without re-reading prior conversation.

---

## Context anchor — what's already in production as of 2026-05-11 PM

Production SHA: `ca50b563` (PR #240 — cron task_hub linkage backfill).

The following Hermes phases are MERGED and LIVE. Do not re-implement any of this — only build on top:

| Layer | What's live | Key files |
|---|---|---|
| Schema | `task_hub_runs` table (per-attempt history); `cody_token_usage` table; `task_hub_assignments.worker_pid`; `task_hub_items.cody_mode / max_runtime_seconds / max_retries`; `task_hub_settings.cody_default_mode`; `task_hub_settings.cody_token_tracking_window` | `src/universal_agent/task_hub.py` § `ensure_schema` |
| Helpers | `task_hub.record_worker_pid(conn, *, assignment_id, worker_pid)`; `task_hub.resolve_max_runtime_seconds(task)`; `task_hub._open_run(...)`; `task_hub._close_run(...)`; `task_hub.list_runs_for_task(...)`; `task_hub._get_setting / _set_setting` | `src/universal_agent/task_hub.py` |
| Exit classifier | `classify_worker_exit(...)` returning `WorkerExit(outcome, is_protocol_violation, is_failure)`; `PROTOCOL_VIOLATION_REASONS` dict keyed by site (`cron`, `vp_cli`, `demo`); `park_task_for_protocol_violation(conn, *, task_id, site, summary)`; `find_active_assignment_for_task(conn, task_id)`; `task_was_closed_normally(conn, task_id)` | `src/universal_agent/services/worker_exit_classifier.py` |
| Cron auto-link | `ensure_cron_task_link(conn, *, system_job)` → returns `task_id="cron:<system_job>"`; `close_cron_task_link(conn, *, task_id, outcome, error)` | `src/universal_agent/services/cron_task_hub_link.py` |
| Cron site wiring | The `!script` spawn branch in `cron_service.py` already calls `ensure_cron_task_link` (when `metadata.skip_task_hub_link` is falsy), records `worker_pid`, classifies exit, routes F.3 protocol violations | `src/universal_agent/cron_service.py` around line 1200 |
| VP CLI site wiring | `_classify_and_route_cli_exit` helper; `run_mission` calls it; F.3 routing when linked task is in_progress | `src/universal_agent/vp/clients/claude_cli_client.py` |
| Demo workspace site wiring | `run_in_workspace` uses `Popen`; new kwargs `assignment_id` + `task_id`; `RunResult.exit_classification` + `RunResult.worker_pid` | `src/universal_agent/services/cody_implementation.py` |
| Simone-callable verb tools | `task_re_evaluate / task_redirect_to / task_request_revision` registered in `internal_registry.get_core_internal_tools()` | `src/universal_agent/tools/task_hub_simone_verbs.py` |
| Cody anthropic-by-default + UI toggle | `resolve_cody_mode(task, conn=...)`, `set_default_mode`, `get_default_mode_state`; resolution order: per-task → DB setting → env → hardcoded `"anthropic"` | `src/universal_agent/services/cody_mode.py` |
| Cody token tracking | `record_token_usage`, `get_window_state`, `reset_window`, `summarize_window` | `src/universal_agent/services/cody_token_tracking.py` |
| Endpoints | `GET/POST /api/v1/cody/mode-setting`, `GET /api/v1/cody/anthropic-token-tracking?mode=...`, `POST /api/v1/cody/anthropic-token-tracking/reset` | `src/universal_agent/gateway_server.py` |
| Dashboard tile | `/dashboard/cody` page with mode toggle + token usage tile + refresh button | `web-ui/app/dashboard/cody/page.tsx` |
| Auto-merge → deploy chain | `pr-auto-merge.yml` uses `secrets.AUTO_MERGE_PAT` so squash-merge `push` events fire `deploy.yml` automatically; `deploy.yml` has `concurrency: { group: deploy-production, cancel-in-progress: false }` | `.github/workflows/pr-auto-merge.yml`, `.github/workflows/deploy.yml` |

**What's NOT wired yet (Ship 4's scope):**
- 4 housekeeping crons currently opted out via `skip_task_hub_link=True`: `codie_proactive_cleanup`, `vp_coder_workspace_pruning`, `atlas_direct_dispatch`, `csi_demo_triage_rank`.
- 2 LLM crons that use the non-`!script` execution path: `autonomous_daily_briefing`, `paper_to_podcast_daily`. Their cron execution branch in `cron_service.py` (the `else` after the `!script` branch, around line 1287) has no F observability wiring.
- No canonical doc exists yet that codifies the "Task Hub Observability Protocol" as a repository-wide standard for new task types.

---

## The Task Hub Observability Protocol (the standard)

This is the protocol that Ship 4 codifies. It is the **standard for every new asynchronous unit of work added to this repository going forward.**

### The six rules

1. **Identity** — Have a `task_hub_items` row. The unit of work has an identity in the system.
2. **Claim ledger** — Create a `task_hub_assignments` row when claimed (or auto-link via `ensure_cron_task_link(conn, system_job=...)` for cron-style perpetual tasks).
3. **Run history** — Open a `task_hub_runs` row at attempt start with `task_hub._open_run(...)`; close it at attempt end with `task_hub._close_run(...)` carrying one of the five outcomes from `classify_worker_exit`.
4. **Subprocess identity (if applicable)** — If the work spawns a subprocess: record `worker_pid` via `task_hub.record_worker_pid(...)` immediately after spawn; resolve `max_runtime_seconds` via `task_hub.resolve_max_runtime_seconds(task)` for the timeout.
5. **Protocol violation routing** — If the worker exits cleanly (`rc=0` or coroutine completes normally) but the linked task is still `in_progress`: route to `needs_review` via `park_task_for_protocol_violation(conn, task_id=..., site=...)` with the site-specific reason from `PROTOCOL_VIOLATION_REASONS`.
6. **Standard recovery verbs** — Use the canonical recovery verbs for stuck tasks: `rehydrate / re_evaluate / request_revision / redirect_to` (operator dashboard buttons or Simone-callable tools). NO per-site recovery logic.

### Helper API reference (already shipped)

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

# At work start (cron pattern):
task_id = ensure_cron_task_link(conn, system_job=cron_name)
# OR (general pattern when the task already exists):
assignment = task_hub.claim_next_dispatch_tasks(...)
run_id = task_hub._open_run(conn, task_id=task_id, assignment_id=assignment_id, agent_id=agent_id)

# If subprocess:
proc = await asyncio.create_subprocess_exec(...)
task_hub.record_worker_pid(conn, assignment_id=assignment_id, worker_pid=proc.pid)
timeout_seconds = task_hub.resolve_max_runtime_seconds(task_row)

# At work end:
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
)

# F.3 protocol violation routing:
if exit_class.is_protocol_violation:
    park_task_for_protocol_violation(
        conn,
        task_id=task_id,
        site="cron" | "vp_cli" | "demo" | <new_site>,
        summary="clean exit but task not closed",
    )
```

### When to use which pattern

| Task shape | Pattern |
|---|---|
| Cron job spawning a `!script` subprocess | Auto-link via `ensure_cron_task_link`; subprocess flow records PID + classifies exit |
| Cron job running an in-process LLM call | Auto-link via `ensure_cron_task_link`; in-process flow records run with `worker_pid=NULL` + classifies exit (rc derived from coroutine success/exception) |
| VP mission via CLI subprocess | Existing wiring in `claude_cli_client.run_mission` — caller passes `task_id` in `payload.metadata.task_id` to enable F.3 |
| VP mission via SDK in-process | Same as VP CLI but `worker_pid=NULL`; exit classification derived from `MissionOutcome.status` |
| Demo workspace subprocess | Caller passes `assignment_id` to `run_in_workspace`; PID recorded; classification on `RunResult` |
| Webhook handler / on-demand operator action / scheduled GitHub Actions task / any new async unit | Apply the six rules. Open `task_hub_items` row, open run, classify exit, close run, F.3 on protocol violation. |

---

## Ship 4 implementation plan

### Branch and identifiers

- **Branch name:** `claude/hermes-f-task-observability-protocol`
- **PR title:** `feat(hermes-f-final): LLM crons + housekeeping opt-in + Task Hub Observability Protocol doc`
- **Commit message:** see template at the bottom of this plan.

### Part A — Flip 4 housekeeping crons to opt-in

**File:** `src/universal_agent/gateway_server.py`

Find each of these four `_register_system_cron_job` calls and remove the `skip_task_hub_link=True` kwarg (or set it to `False` explicitly — the default is `False` post-PR #240):

1. `codie_proactive_cleanup` — at the `_ensure_codie_proactive_cleanup_cron_job` function (or similar — grep for `"codie_proactive_cleanup"` in `gateway_server.py`)
2. `vp_coder_workspace_pruning` — `_ensure_vp_coder_workspace_pruning_cron_job`
3. `atlas_direct_dispatch` — `_ensure_atlas_direct_dispatch_cron_job`
4. `csi_demo_triage_rank` — `_ensure_csi_demo_triage_rank_cron_job`

**Verification per cron:** after the flip, `_register_system_cron_job(system_job=<name>, ...)` should have either no `skip_task_hub_link` kwarg OR `skip_task_hub_link=False`. The cron service's `!script` spawn path will then auto-link via `ensure_cron_task_link` on each tick.

**Why this is safe:** the auto-link is idempotent (uses `task_id=f"cron:{system_job}"` as the dedupe key), so flipping mid-flight just starts populating observability rows on the next tick. No backfill needed.

### Part B — Wire the LLM cron path

**File:** `src/universal_agent/cron_service.py`

**Anchor:** the `else:` branch around line 1287 that runs LLM crons via `asyncio.wait_for(run_coro, timeout=timeout_seconds)`. Read the area `1180–1320` to understand both branches and what state the `!script` branch records.

**Changes required:**

1. **Before** `asyncio.wait_for(run_coro, ...)`:
   - Resolve whether the job opted out (`job.metadata.get("skip_task_hub_link")` — bool). Skip the entire wiring if `True`.
   - If opted in: call `ensure_cron_task_link(conn, system_job=job.system_job_name)` to ensure the `cron:<name>` task exists, get `task_id`.
   - Open an assignment + run: `assignment_id = uuid4().hex` (or however the !script branch does it — match that pattern); `_open_run(conn, task_id=task_id, assignment_id=assignment_id, agent_id="cron_runner")`.
   - **Do NOT** call `record_worker_pid` — this is in-process, no separate PID. The schema already accepts NULL for `worker_pid`.
   - Set up local vars `was_timeout = False`, `error_text = ""`, `summary_text = ""`.

2. **Inside the try/except around `asyncio.wait_for`:**
   - On success: capture `summary_text` from the result (or use a generic "LLM cron run completed").
   - On `asyncio.TimeoutError`: `was_timeout = True`; `error_text = f"cron timed out after {timeout_seconds}s"`.
   - On other exceptions: `error_text = str(exc)[:1000]`.

3. **After the LLM call:**
   - Determine return-code equivalent: `rc_equiv = 0 if (not was_timeout and not error_text) else 1`.
   - Determine `task_closed_normally`: call `task_was_closed_normally(conn, task_id)`. For LLM crons that mark their own work, this will be False if the task is still `in_progress` after the LLM returns.
   - Call `classify_worker_exit(return_code=rc_equiv, was_signaled=False, was_timeout_killed=was_timeout, task_closed_normally=task_closed_normally)`.
   - Call `_close_run(conn, assignment_id=assignment_id, outcome=classification.outcome, summary=summary_text, error=error_text)`.
   - Call `close_cron_task_link(conn, task_id=task_id, outcome=classification.outcome, error=error_text)`.
   - If `classification.is_protocol_violation`: call `park_task_for_protocol_violation(conn, task_id=task_id, site="cron", summary="LLM cron clean exit no disposition")`.

4. **Wrap everything in try/except**: F observability must not break LLM cron execution. Mirror the defensive style from the `!script` branch — log on failure, continue.

**Code style:** match the existing branch's variable names, log prefixes, and error-handling discipline. Look at how the `!script` branch wraps its F wiring (probably in helper functions in the same file) and reuse those helpers where possible. Do not duplicate logic.

### Part C — Tests

#### New test file: `tests/unit/test_cron_llm_path_f_observability.py`

Mirror the style of `tests/unit/test_hermes_phase_f_site_wiring.py` (existing). Tests:

1. `test_llm_cron_success_records_clean_exit_zero` — Mock the LLM coroutine to succeed; verify `task_hub_runs` row has `outcome="clean_exit_zero"`; verify cron task closed.
2. `test_llm_cron_timeout_records_timeout_killed` — Mock the coroutine to take longer than `timeout_seconds`; verify outcome is `"timeout_killed"`; verify `_close_run` is called with the right error.
3. `test_llm_cron_exception_records_nonzero_exit` — Mock the coroutine to raise; verify outcome is `"nonzero_exit"`; verify error text captured.
4. `test_llm_cron_clean_exit_no_disposition_triggers_protocol_violation` — Mock the coroutine to succeed but leave the task in_progress; verify `park_task_for_protocol_violation` is called.
5. `test_llm_cron_opt_out_skips_wiring` — Set `metadata.skip_task_hub_link=True`; verify no `task_hub_runs` row appears and no F.3 routing fires.
6. `test_llm_cron_observability_failure_does_not_break_execution` — Mock `_open_run` to raise; verify the LLM cron still runs and returns.

Use `pytest.fixture` for the SQLite conn and the cron service instance. Mock at the coroutine level (don't actually invoke real LLM calls).

#### Extend existing test file: `tests/unit/test_cron_task_hub_linkage.py`

Add 4 tests, one per flipped housekeeping cron, that verify the registration call no longer carries `skip_task_hub_link=True`. Pattern:

```python
def test_codie_proactive_cleanup_is_observed(monkeypatch):
    """Post-Ship-4: codie_proactive_cleanup auto-links via ensure_cron_task_link."""
    from universal_agent.gateway_server import _ensure_codie_proactive_cleanup_cron_job
    # Inspect the call args by mocking _register_system_cron_job and capturing them.
    ...
    assert "skip_task_hub_link" not in captured_kwargs or captured_kwargs["skip_task_hub_link"] is False
```

(If `_register_system_cron_job` is hard to mock at this layer, an alternative is to grep the source of the registration helper for `skip_task_hub_link=True` and assert it's not present. Choose whichever is cleaner.)

### Part D — Canonical doc + index updates

#### New file: `docs/03_Operations/108_Task_Hub_Observability_Protocol.md`

Structure:

1. **Title + last-updated stamp** — `# Task Hub Observability Protocol` ; `**Last updated:** 2026-05-11 PM`.
2. **Purpose** — Why this protocol exists; the centralization principle ("every task is the same, all managed by Task Hub"); links to the centralization rationale in CLAUDE.md.
3. **Scope** — Applies to: any new async unit of work in `src/universal_agent/`. Includes: crons, VP missions, demo workspaces, webhook handlers, scheduled GHA workflows that produce a task_hub item, manual operator actions that fire async work. Excludes: pure event handlers that produce no persistent state.
4. **The six rules** — Verbatim from above. Each rule with the WHY.
5. **Helper API reference** — The code block from above with all imports and helper signatures.
6. **Per-task-shape patterns table** — From above.
7. **Worked examples**:
   - Adding a new cron job (full code with the wiring)
   - Adding a new VP mission consumer
   - Adding a webhook handler (hypothetical — illustrates the general case)
8. **Checklist for adding a new task type** — copy/paste checklist (10 items max).
9. **What this protocol does NOT do** — Doesn't enforce business logic; doesn't replace per-site retry semantics (those are in `task_hub` already); doesn't auto-instrument existing code (must be explicitly wired).
10. **Cross-refs**:
    - `docs/03_Operations/107_Task_Hub_And_Multi_Channel_Execution_Master_Reference_2026-03-31.md` (Task Hub master reference)
    - `docs/03_Operations/cron_job_registration.md` (cron job registration patterns)
    - `docs/reports/hermes-adaptation-phased-plan-2026-05-10.md` (Hermes plan that produced this)
11. **Future hardening** — Mention the deferred lint guard (CI check that flags new `subprocess.run / Popen / asyncio.create_subprocess_exec` calls in `src/universal_agent/` that don't import from `worker_exit_classifier`).

Length target: 500-800 lines. Detailed enough to be a real reference; not so verbose that it gets ignored.

#### CLAUDE.md update

In the "Pre-Implementation Reading — DO NOT SKIP" table (current location: `CLAUDE.md` § "Pre-Implementation Reading"), add a new row:

| If you're about to propose | Read first |
|---|---|
| ... existing rows ... | ... |
| **A new cron job, scheduled task, webhook handler, or any new async unit of work** | **`docs/03_Operations/108_Task_Hub_Observability_Protocol.md` — the protocol is mandatory for new tasks. Use the helper API; don't bypass.** |

Place it logically near the other "task hub" rows.

#### `docs/Documentation_Status.md` update

Prepend a new entry to the rolling "Last updated" block following the existing pattern (2-3 paragraphs covering: Ship 4 LLM cron wiring, 4 housekeeping crons flipped, new 108 canonical doc + checklist + worked examples, CLAUDE.md row addition, test counts).

#### `docs/README.md` update

Find the "3. Operations" or "03_Operations" section and add a link to the new doc:
- `**[Task Hub Observability Protocol](03_Operations/108_Task_Hub_Observability_Protocol.md)**: Canonical standard for the observability + recovery wiring that every async unit of work must follow.`

### Part E — Verification + ship

1. **Local tests:**
   ```
   uv run pytest tests/unit/test_cron_llm_path_f_observability.py tests/unit/test_cron_task_hub_linkage.py tests/unit/test_hermes_phase_f_site_wiring.py tests/unit/test_hermes_phase_f_foundation.py -x -q --no-header
   ```
   All must pass. Then broader sanity:
   ```
   uv run pytest tests/unit/test_task_hub_runs.py tests/unit/test_task_hub_unstick_verbs.py tests/unit/test_dashboard_failure_context_endpoint.py tests/unit/test_cody_mode.py tests/unit/test_cody_token_tracking.py tests/unit/test_task_hub_simone_verbs.py tests/unit/test_task_hub_lifecycle.py tests/unit/test_task_hub_schema_extensions.py tests/unit/test_task_hub_max_retries_override.py -q --no-header
   ```

2. **Static checks:**
   ```
   python -m py_compile src/universal_agent/cron_service.py src/universal_agent/gateway_server.py tests/unit/test_cron_llm_path_f_observability.py
   uv run ruff check --select E9,F --ignore E402,F401,F541,F811,F841 --no-cache src/universal_agent/cron_service.py src/universal_agent/gateway_server.py tests/unit/test_cron_llm_path_f_observability.py
   ```

3. **Commit:**
   ```
   git add src/universal_agent/cron_service.py src/universal_agent/gateway_server.py \
           tests/unit/test_cron_llm_path_f_observability.py tests/unit/test_cron_task_hub_linkage.py \
           docs/03_Operations/108_Task_Hub_Observability_Protocol.md \
           docs/Documentation_Status.md docs/README.md CLAUDE.md
   git commit -m "feat(hermes-f-final): LLM crons + housekeeping opt-in + Task Hub Observability Protocol doc"
   ```
   Use the detailed commit template at the bottom of this plan.

4. **Push + PR:**
   ```
   git push -u origin claude/hermes-f-task-observability-protocol
   gh pr create --base main --head claude/hermes-f-task-observability-protocol --title "..." --body "..."
   ```
   The PAT-based auto-merge chain will handle the rest.

5. **Post-deploy verification on `ua@uaonvps`:**
   ```
   # Wait ~2 minutes for deploy, then:
   ssh ua@uaonvps "curl -s http://127.0.0.1:8002/api/v1/version" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['short_sha'], '-', d['commit_subject'])"
   # Should show the new SHA.
   
   # After a cron tick has fired (worst case 30 min for hackernews_snapshot):
   ssh ua@uaonvps 'sqlite3 /opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db "SELECT task_id, source_kind, status, COUNT(*) FROM task_hub_items WHERE source_kind = '\''cron_run'\'' GROUP BY task_id ORDER BY task_id"'
   # Should show rows for the auto-linked crons (8 work-producing + 4 newly-opted-in = 12 expected).
   ```

---

## Commit / PR template

### Commit message

```
feat(hermes-f-final): LLM crons + housekeeping opt-in + Task Hub Observability Protocol doc

Closes Phase F observability coverage and codifies the Task Hub
Observability Protocol as the repository-wide standard for any new
async unit of work.

Three pieces:

1. **LLM cron path wiring** — `cron_service.py` LLM execution branch
   (the non-`!script` path around line 1287) now creates a cron task
   link, opens a `task_hub_runs` row, classifies the outcome of the
   coroutine via `classify_worker_exit`, closes the run + cron task
   link, and routes protocol violations via
   `park_task_for_protocol_violation`. `worker_pid` stays NULL — these
   are in-process LLM calls and don't have separate PIDs. Mirrors the
   `!script` branch wiring from PR #238.

2. **4 housekeeping crons flipped to opt-in** — removed
   `skip_task_hub_link=True` from `codie_proactive_cleanup`,
   `vp_coder_workspace_pruning`, `atlas_direct_dispatch`, and
   `csi_demo_triage_rank`. These are dispatcher/GC crons; the
   "they don't produce work product" rationale was the wrong frame —
   meta-observability ("is the dispatcher itself alive?") matters too.

3. **Canonical doc**: `docs/03_Operations/108_Task_Hub_Observability_Protocol.md`
   — formalizes the six-rule standard for ALL new async work in this
   repo: identity, claim ledger, run history, subprocess identity,
   protocol violation routing, standard recovery verbs. Includes
   helper API reference, per-task-shape patterns table, worked
   examples (cron / VP mission / webhook), checklist for adding a
   new task type, and cross-refs to existing canonical docs.

   `CLAUDE.md` Pre-Implementation Reading table gains a row pointing
   at the new 108 doc as MANDATORY reading before proposing any new
   async unit. Indexes updated in `docs/README.md` and
   `docs/Documentation_Status.md`.

Tests: 6 new in `test_cron_llm_path_f_observability.py` + 4 extending
`test_cron_task_hub_linkage.py`. Cross-Hermes sanity: 200+ Hermes-
related tests still pass.

After this PR the 4 standalone cron sources audit results from PR #240
become:
* Auto-linked: 8 work-producing + 4 newly-opted-in = 12 cron sources
* N/A: 2 LLM crons → now wired via the LLM-path observability
* Total: 14/14 cron sources covered by Phase F observability

Future hardening (deferred): a CI lint guard that flags new
`asyncio.create_subprocess_exec / subprocess.run / Popen` calls in
`src/universal_agent/` not importing from `worker_exit_classifier`,
to enforce the protocol at PR time.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

### PR body

```
## Summary

Closes Phase F observability coverage AND codifies the Task Hub Observability Protocol as the standard for all new async work in this repo.

Three pieces — see commit message for full detail:
1. LLM cron path wiring (mirrors `!script` branch from PR #238)
2. 4 housekeeping crons flipped to opt-in
3. New canonical doc `108_Task_Hub_Observability_Protocol.md` + CLAUDE.md row + index updates

## Why this matters

The user's existing principle is "centralize everything through Task Hub." Phase F shipped the observability layer but only wired 3 spawn sites + 8 cron sources. This PR closes that gap (14/14 cron sources covered) AND documents the protocol so future contributors apply it by default.

## Test plan

- [x] 6 new LLM-path observability tests pass locally
- [x] 4 new housekeeping-cron opt-in tests pass
- [x] Cross-Hermes sanity 200+ tests still pass
- [x] py_compile + ruff clean
- [ ] CI PR-Validate
- [ ] Post-deploy: confirm new SHA via `/api/v1/version`; observe `cron_run` rows in production after first tick of a newly-opted-in cron

## Future hardening (deferred)

Lint guard that flags new subprocess spawns not importing from `worker_exit_classifier`. Tracked in commit message.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

---

## Critical context for compaction-resilience

If the context window has been compacted before execution, the following identifiers are LIVE on `main` as of SHA `ca50b563`. Do not assume they need re-creation:

- **Module path:** `src/universal_agent/services/worker_exit_classifier.py` exists and exports `classify_worker_exit`, `WorkerExit`, `PROTOCOL_VIOLATION_REASONS` (dict with keys `cron`, `vp_cli`, `demo`), `park_task_for_protocol_violation(conn, *, task_id, site, summary="")`, `find_active_assignment_for_task(conn, task_id)`, `task_was_closed_normally(conn, task_id)`.
- **Module path:** `src/universal_agent/services/cron_task_hub_link.py` exists and exports `ensure_cron_task_link(conn, *, system_job)` returning `task_id="cron:<system_job>"`, and `close_cron_task_link(conn, *, task_id, outcome, error)`.
- **Schema:** `task_hub_assignments.worker_pid INTEGER NULL`; `task_hub_items.max_runtime_seconds INTEGER NULL`; `task_hub_items.cody_mode TEXT`; `task_hub_runs` table with `(run_id, task_id, assignment_id, agent_id, started_at, ended_at, outcome, summary, metadata_json, error)`; `cody_token_usage` table.
- **PR auto-merge chain:** branch name `claude/<anything>` → PR open → `pr-auto-merge.yml` enables auto-merge via PAT → `pr-validate.yml` runs → squash-merge to main → `deploy.yml` fires on push → production deploy → SHA visible on `/api/v1/version`. Concurrency guard ensures simultaneous merges queue.
- **Verification command:** `ssh ua@uaonvps "curl -s http://127.0.0.1:8002/api/v1/version" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['short_sha'])"` returns the live production SHA.
- **DB path on production:** `/opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db`.

If any of these have drifted (e.g. a subsequent PR moved files), grep before assuming. Most likely they have not — the deployed SHA at plan-creation time is `ca50b563` and the next PR built on this plan should ideally branch off `ca50b563` or its descendant.

---

## Open questions / decisions baked in

| Decision | Why |
|---|---|
| Lint guard deferred to future PR | User explicit: "Skip the lint guard for now." High value but needs allowlist design. |
| LLM cron path gets observability with NULL `worker_pid` | The schema explicitly allows NULL for in-process work. Don't try to synthesize a fake PID. |
| 4 housekeeping crons get flipped, not redesigned | Meta-observability principle: even dispatchers and GC tasks benefit from "did they run?" visibility. Simpler than restructuring them. |
| LLM-cron protocol violation routing matches `!script` branch | Same `PROTOCOL_VIOLATION_REASONS["cron"]` reason; treat all crons uniformly regardless of execution mode. |
| Six-rule standard is the canonical framing | Concise; covers identity, claim, history, subprocess identity, protocol violation, recovery. Maps 1:1 to the helpers shipped in PRs #230 / #238 / #239 / #240. |

---

## Execution sequence (when resuming after compaction)

1. Open this plan document (`docs/reports/hermes-ship-4-task-observability-protocol-plan.md`) — it's the operational source of truth.
2. Branch off latest `origin/main` (verify SHA via `git log origin/main --oneline -1`).
3. Execute Part A → Part D → Part C tests → Part E ship in order.
4. Each step has a verification gate; don't skip.
5. Report back to operator only at ship completion (PR URL, test counts, production verification result).

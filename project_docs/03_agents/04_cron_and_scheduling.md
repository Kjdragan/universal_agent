---
title: Cron & Scheduling
status: active
canonical: true
subsystem: agents-cron
code_paths:
  - src/universal_agent/cron_service.py
  - src/universal_agent/gateway_server.py
  - src/universal_agent/systemd_migrated_jobs.py
  - src/universal_agent/task_hub.py
  - src/universal_agent/services/paper_to_podcast_guard.py
  - src/universal_agent/services/paper_to_podcast_workspace.py
  - src/universal_agent/arxiv_runtime.py
  - src/universal_agent/services/arxiv_local_index.py
  - deployment/systemd/
  - scripts/install_vps_phase_a_batch1_timers.sh
  - scripts/install_vps_phase_a_batch2_timers.sh
last_verified: 2026-07-24
---

# Cron & Scheduling

> **Live status / inventory:** the canonical two-scheduler inventory (which jobs
> run as systemd timers vs in-process, and which are tombstones) lives in the
> [Platform Status Registry](../00_PLATFORM_STATUS_REGISTRY.md) § 4. This doc
> documents the *mechanism*; the registry is the *roster*.

The cron subsystem is an in-process scheduler that runs inside the gateway
process. It owns three kinds of work: **LLM crons** (a Claude session runs a
natural-language prompt), **`!script` crons** (a Python module runs as a
subprocess), and **one-shot reminders** (`run_at`). It is implemented in a
single module, `cron_service.py`, plus a set of idempotent
`_ensure_<job>_cron_job()` registration helpers in `gateway_server.py`.

Most jobs tick off an asyncio loop in the gateway — there is no OS-level
crontab for them and no separate scheduler process. That is the single most
important fact about this subsystem: a cron tick that does heavyweight
synchronous work can stall the gateway event loop, which is why the
deploy-window, lightweight, and `to_thread` mitigations described below exist.

**Exception — jobs migrated to systemd timers.** A bounded set of slot-critical
deterministic jobs has been migrated OFF the in-process loop onto
deploy-independent `systemd` `OnCalendar`+`Persistent` timers (ADR
`project_docs/06_platform/08_scheduling_substrate_adr.md`, Decision 1 / Phase A).
Those do not tick off the gateway loop — see
[Systemd timers vs in-process crons](#systemd-timers-vs-in-process-crons) below.

## Systemd timers vs in-process crons

The in-process loop loses 17–49% of fires to the ~19 daily deploy restarts; a
daily/weekly/monthly slot landing inside a deploy window is silently dropped.
For slot-critical deterministic jobs that is unacceptable, so they run as
`systemd` timers instead — the OS replays a slot missed inside a deploy window
(`Persistent=true`) and the per-deploy `daemon-reload` re-arms them
(`OnCalendar` wall-clock anchor; a monotonic timer would go
`NextElapse=infinity`).

**The migrated set is the
`systemd_migrated_jobs.py::SYSTEMD_MIGRATED_SYSTEM_JOBS` frozenset — the single
SOURCE OF TRUTH** (currently **20 jobs**, batched 1 → A4: maintenance/audit,
content dailies, hourly active-window producers, and the secret-bearing
YouTube/OAuth/briefing jobs). Do **not** maintain a hand-list here — it drifts
against the frozenset. The full enumerated roster (with per-job gate mechanism
and status) is in the
[Platform Status Registry](../00_PLATFORM_STATUS_REGISTRY.md) § 4a; the predicate
that resolves a job against it is `systemd_migrated_jobs.py::is_migrated_to_systemd`.
`insight_scoring_health` was **retired 2026-06-21** (zombie monitor; its producer
`hourly_insight_email` was deregistered in #745, so it emailed a false "0 briefs
scored / 0 delivered" every Sunday off a frozen `proactive_brief_scoring_log` —
units + registration removed, dropped from the frozenset).

**Still in-process (NOT migrated):** the minute/15m/30m loops and live-agent
prompt jobs stay on the gateway tick. Of the persisted `cron_jobs.json` roster,
exactly **5 are enabled and genuinely fire in-process** (the rest are migration
tombstones, see § 4c): `simone_chat_auto_complete` (`*/1`, housekeeping),
`vp_mission_pr_reconciler` (`*/15` active-window, housekeeping),
`paper_to_podcast_daily` (`0 21 * * *` CT, `UA_PAPER_TO_PODCAST_ENABLED`), and
`morning_ideation_report` (`30 6 * * *` CT, `UA_IDEATION_REPORT_ENABLED`), and
`stale_proposal_reaper` (`40 6 * * 0` CT weekly, `UA_STALE_PROPOSAL_REAPER_ENABLED` — parks OPEN reflection/brainstorm proposals >14d via `task_hub.py::reap_stale_proposals`; HARD GATE spares priority>=2 and `human-only`). See
[Platform Status Registry](../00_PLATFORM_STATUS_REGISTRY.md) § 4b.

Units are `deployment/systemd/universal-agent-<job>.{timer,service}`; the
installers (`scripts/install_vps_phase_a_batch1_timers.sh` /
`scripts/install_vps_phase_a_batch2_timers.sh`) are wired into
`scripts/deploy/remote_deploy.sh`.

**No double-fire.** A migrated job's in-process registration is forced disabled
so the timer is the sole firer. `gateway_server.py::_is_migrated_to_systemd`
(backed by the `gateway_server.py::_SYSTEMD_MIGRATED_SYSTEM_JOBS` frozenset) is
ANDed into each standard `_ensure_*_cron_job()`'s `enabled=` arg, so
`gateway_server.py::_register_system_cron_job` flips the persisted `cron_jobs.json`
row to disabled on **every** gateway boot (it does not silently re-enable). The
one job that registers through a bespoke `_cron_service.add_job/update_job` path
(`codie_proactive_cleanup`) carries its own disable inside
`gateway_server.py::_ensure_codie_proactive_cleanup_cron_job` (flip-to-disabled
when migrated). Rollback without a redeploy: set
`UA_SYSTEMD_TIMER_MIGRATION_DISABLED=1` and restart the gateway (then disable the
timers); per-job rollback = remove the job from the frozenset. Stay-in-process
(NOT migrated): the minute/15m/30m loops and the live-agent prompt jobs.

**`TimeoutStartSec`.** A `Type=oneshot` defaults to `TimeoutStartSec=infinity`,
so a hung network/LLM run blocks its own timer's next fire forever. The
network/LLM units bound it to their old in-process budget
(`proactive-report` 600, `proactive-artifact-digest` 300); pure-FS/SQLite units
keep `infinity`.

### Is this scheduled job actually running? (diagnostic — read this before concluding "disabled")

**The trap:** for a job that has been migrated to a systemd timer, the two
artifacts you instinctively check both point the *wrong* way:

- **`cron_jobs.json` shows `"enabled": false`** — this is a **tombstone, not "off."**
  `gateway_server.py::_register_system_cron_job` deliberately flips the persisted
  in-process row to disabled on every boot so the systemd timer is the sole firer
  (no double-fire). The row stays in the file forever, looking dead.
  **Breadcrumb:** for jobs that disable through this helper, the row now carries
  `metadata.disabled_reason = "migrated_to_systemd:universal-agent-<job>.timer"`
  naming the unit that actually fires it — so the JSON itself tells you it's a
  tombstone, not a dead cron. (Jobs disabled through a bespoke `_ensure_*` path —
  `codie_proactive_cleanup`, `csi_convergence_sync`, `youtube_daily_digest`,
  `youtube_gold_channel_poller` — do not carry the breadcrumb yet; for those the
  `systemd_migrated_jobs.py::SYSTEMD_MIGRATED_SYSTEM_JOBS` check below is
  authoritative. A *missing* `disabled_reason` therefore does **not** prove the
  job is non-migrated — confirm against the frozenset.)
- **The in-process workspace log is stale** —
  `AGENT_RUN_WORKSPACES/cron_<job>/run.log` stops advancing at the migration date,
  because the in-process cron no longer fires. The job *is* running; its real
  output is in the systemd journal, not here.

Concluding "this cron is disabled" from either artifact alone is wrong, and it is
an easy mistake (it was made during the 2026-06 nightly-wiki investigation). The
correct order of checks:

1. **Is the job migrated?** Look it up in
   `systemd_migrated_jobs.py::SYSTEMD_MIGRATED_SYSTEM_JOBS` (the SOURCE OF TRUTH;
   `systemd_migrated_jobs.py::is_migrated_to_systemd` is the predicate). If it's
   there, the systemd timer is the firer — ignore the `cron_jobs.json` row.
2. **Check the timer, not the JSON:**
   ```bash
   systemctl list-timers 'universal-agent-*' --all      # NEXT / LAST / unit, all migrated jobs
   systemctl is-enabled universal-agent-<job>.timer     # enabled?
   systemctl is-active  universal-agent-<job>.timer     # active (armed)?
   systemctl show universal-agent-<job>.timer -p LastTriggerUSec -p NextElapseUSecRealtime
   ```
3. **Read the real run output from the journal**, not the stale workspace log:
   ```bash
   sudo journalctl -u universal-agent-<job>.service --since '3 days ago' --no-pager
   ```

A non-migrated job (not in the frozenset) is the opposite: `cron_jobs.json`
`enabled` + its `run.log` ARE authoritative, and there is no systemd unit.
Decide which world you're in (step 1) before trusting either artifact.

## Components

| Symbol | Role |
|---|---|
| `cron_service.py::CronService` | The scheduler. Holds the job registry, the tick loop, dispatch, retry, and finalization. |
| `cron_service.py::CronJob` | Dataclass for a single job (schedule, command, metadata). |
| `cron_service.py::CronRunRecord` | One run's outcome, appended to `cron_runs.jsonl`. |
| `cron_service.py::CronStore` | JSON persistence: `cron_jobs.json` (registry) + `cron_runs.jsonl` (run log). |
| `gateway_server.py::_register_system_cron_job` | Idempotent boot-time registration of system crons. |
| `gateway_server.py::_ensure_*_cron_job` | One helper per system cron; calls the registrar with that job's schedule/command. |
| `services/cron_task_hub_link.py::ensure_cron_task_link` | Hermes Phase F: auto-links each cron to a `cron:<name>` Task Hub row for observability. |
| `workflow_admission.py::WorkflowAdmissionService` | De-dup, admission, retry queuing, and lifecycle marking for each cron attempt. |

## Process model & startup

`CronService` is constructed once during gateway startup (see the
`CronService(...)` construction in `gateway_server.py`) with four callbacks:
`event_sink` (UI/event bus), `wake_callback` (heartbeat wake), a
`system_event_callback`, and an `agent_event_sink`. Then:

1. If a deploy window is active (`gateway_server.py::_deployment_window_active`),
   startup is deferred via `_run_after_deployment_window` so the scheduler
   doesn't begin ticking mid-restart. Otherwise `CronService.start` runs
   immediately.
2. If `should_run_loop("cron_registration", prod_default=True)` is true, the
   long list of `_ensure_*_cron_job()` helpers run to register/refresh all
   system crons. In **development** this gate is off — the ticker may run but
   no schedules are registered (trigger jobs manually via the admin API).

`CronService.__init__` also has a **dev belt-and-suspenders guard**: if
`loop_control.is_development_runtime()` is true it refuses to load the
persisted `cron_jobs.json`, so the ~50+ production cron jobs never tick on a
developer's desktop even if the service is somehow constructed.

```mermaid
flowchart TD
    boot[Gateway startup] --> defer{deploy window<br/>active?}
    defer -- yes --> wait[_run_after_deployment_window] --> start
    defer -- no --> start[CronService.start]
    start --> loop[_scheduler_loop: tick every 1s]
    boot --> reg{should_run_loop<br/>cron_registration?}
    reg -- yes --> ensure[_ensure_*_cron_job → _register_system_cron_job]
    reg -- no --> skip[ticker runs, no schedules]
    loop --> due{job due &<br/>not running?}
    due -- yes --> run[_run_job via asyncio.create_task]
    run --> kind{command kind}
    kind -- "!script + lightweight" --> lw[subprocess, no agent bootstrap]
    kind -- "!script" --> sc[subprocess + Claude session bootstrap]
    kind -- "LLM" --> llm[gateway.run_query in-process]
```

## Scheduling models

`CronJob` supports three mutually-evaluated schedule types, resolved by
`cron_service.py::CronJob.schedule_next`:

1. **`run_at`** (one-shot absolute timestamp). Runs once; `next_run_at` becomes
   `None` after the first run. Pair with `delete_after_run=True` to self-delete
   on success.
2. **`cron_expr`** (5-field cron string + `timezone`, via `croniter`). Takes
   precedence over the interval. If the expression is invalid at tick time it
   falls back to `every_seconds`.
3. **`every_seconds`** (simple interval). Minimum enforced interval is
   `MIN_CRON_INTERVAL_SECONDS = 60`; sub-minute intervals raise `ValueError`
   and are told to use `cron_expr`.

`add_job` validates that at least one of the three is provided, validates the
cron expression and timezone, then calls `schedule_next`.

### `run_at` natural-language parsing

`cron_service.py::parse_run_at` accepts a float (absolute), a relative
duration (`"20m"`, `"2h"`, `"1d"`), an ISO timestamp, a bare unix timestamp,
or natural phrases — `"now"`, `"in 90 minutes"`, `"tomorrow 9:15am"`,
`"tonight"` (defaults to 8:00pm). The timezone for natural phrases is supplied
by the caller; absent that it is UTC.

## The tick loop

`cron_service.py::_scheduler_loop` wakes every 1 second, scans
`self.jobs.values()`, and for each enabled job that is not already in
`self.running_jobs` and whose `next_run_at <= now`:

- It captures `scheduled_at`, reschedules (`schedule_next`) or, for one-shot
  `run_at` jobs, pushes `next_run_at` 5s out so it isn't re-fired.
- It **marks the job running BEFORE dispatching** (`self.running_jobs.add`) to
  prevent a duplicate task on the next tick while `_run_job` is still
  acquiring the concurrency semaphore.
- It launches `_run_job` as a detached `asyncio.create_task`.

Concurrency is bounded by an `asyncio.Semaphore(self.max_concurrency)` where
`max_concurrency` comes from `UA_CRON_MAX_CONCURRENCY` (default **2**).

## `_run_job`: the execution body

`cron_service.py::_run_job` is the heart of the subsystem. Order of operations:

1. **Deleted-job guard.** If `job.job_id` is no longer in `self.jobs`, short-
   circuit with a `skipped` record. This breaks the orphan-retry storm that
   otherwise happens when a job is deleted while a retry chain holds the
   `CronJob` in its closure (observed live 2026-05-11, cron `2df80b6f95`,
   90+ minutes of retry-storm emails).
2. **Workflow admission.** Acquire the semaphore, build a `WorkflowTrigger`
   (`_build_workflow_trigger`, `run_kind="cron_job_dispatch"`,
   `run_policy="automation_ephemeral"`,
   `interrupt_policy="attach_if_same_dedup_key"`), and call
   `WorkflowAdmissionService.admit`. Decisions of
   `attach_to_existing_run` / `defer` / `skip_duplicate` produce a `skipped`
   record; `escalate_review` produces `needs_review`. The `dedup_key` is
   `scheduled:<job_id>:<int(scheduled_at)>` for scheduled runs — so two ticks
   for the same scheduled instant de-dup.
3. **Pre-flight required-secrets check** (`_find_missing_required_secrets`).
   If `metadata.required_secrets` lists env vars that resolve to empty, the run
   fails fast with a structured `missing_required_secrets` error rather than
   the script dying obscurely.
4. **Dispatch by command kind** (below).
5. **Finalize** via `_finalize_workflow_attempt` and the `finally` block.

### Command kinds

- **Mock** (`UA_CRON_MOCK_RESPONSE` truthy): records `success`/`CRON_OK`
  without doing work. Test seam.
- **Lightweight `!script`** (`metadata.lightweight == True`): spawns
  `python -m <module>` directly with `asyncio.create_subprocess_exec`,
  **skipping the heavyweight Claude-session bootstrap** (Composio session
  creation, ~54 KB capability-snapshot injection, SOUL load, dossier
  registration). Only `!script` commands are valid here. Rationale: the
  bootstrap synchronously stalls the gateway event loop for several seconds per
  tick, blowing past the dashboard's 4s `/api/v1/version` timeout and surfacing
  the red "Gateway unreachable" banner.
- **Standard `!script`**: same subprocess execution but inside the full agent
  session bootstrap path, with Hermes Phase F Task-Hub linking + worker-PID
  stamping (below).
- **LivenessWatchdog-governed spawn (both `!script` paths, 2026-07-24).**
  Both paths run through `cron_service.py::_spawn_script_with_timeout`, which
  wraps the WHOLE subprocess lifecycle (fork/exec + stdout/stderr drain) in the
  shared `timeout_policy.py::LivenessWatchdog` — the same idle/no-progress
  policy the in-process `execution_engine.py::ProcessTurnAdapter` and the VP
  `claude` CLI lane use. The per-job `timeout_seconds` survives only as the
  watchdog's absolute backstop (a last-resort ceiling). This replaces a prior
  bespoke `asyncio.wait_for(proc.communicate(), …)` wall-clock cap that wedged
  the single-threaded cron dispatch loop for up to ~60 minutes at a time
  (recurring nightly 2026-06→2026-07): `communicate()` blocks until the child
  exits and emits no incremental signal, so a worker that spawned fine then hung
  silently (a sqlite lock, a blocked import) — or whose fork/exec itself stalled
  — left the loop with no heartbeat and no timely kill, and only the 60-minute
  stuck-run reaper freed it (29 reaped runs Jun–Jul, all the every-minute
  `simone_chat_auto_complete` job; VP diagnosis task_32c02c29e190). The
  watchdog now drains stdout + stderr incrementally and treats each chunk as a
  heartbeat (`note_activity`); a spawn or worker that produces no output for
  `idle_kill_seconds` is reaped in seconds instead of an hour.
  - **Lightweight `!script`** (`metadata.lightweight == True`, e.g.
    `simone_chat_auto_complete`) arms the idle kill via
    `timeout_policy.py::cron_script_idle_kill_seconds` (env
    `UA_CRON_SCRIPT_IDLE_KILL_SECONDS`, default 60 s) — a wedged housekeeping
    worker is reaped in ~60 s (a 60× reduction vs the reaper). A run that keeps
    emitting output runs freely up to the backstop.
  - **Heavyweight `!script`** (real Claude sessions) stays **backstop-only**
    (`idle_kill_seconds=0`): a real agent turn legitimately goes silent for
    minutes during inference, so an idle kill there would murder live work —
    the same 2026-06-14 lesson that gave us this watchdog. Its effective
    behaviour is unchanged by the refactor.
  The kill path tolerates the process object never having been assigned (spawn
  stall). The Phase F.1 worker-PID stamp runs as the helper's `on_spawned`
  hook, inside the watchdog window. The 60-minute stuck-run reaper remains the
  genuine last-resort backstop for the deepest stalls (e.g. a fork that
  hard-blocks the event loop), exactly as designed.
- **LLM cron**: builds a `GatewayRequest` (prepending a
  `[SYSTEM CONTEXT: UA_ARTIFACTS_DIR=...]` header to the command) and runs it
  in-process via `gateway.run_query`. `force_complex` is resolved per-job by
  `_force_complex_for_job` (below).

### Per-job model tier (`force_complex`)

`cron_service.py::_force_complex_for_job` reads `metadata.model_tier`. Default
(unset) and `"high"`/`"opus"` → `force_complex=True` (Opus-tier reasoning, on
ZAI that is `glm-5.1`). `"low"`/`"sonnet"`/`"haiku"` → `False`. Unknown values
fall back to `True` so a typo never silently downgrades a critical cron. This
exists so low-complexity content crons (cleanup, demo-triage-rank, artifact
digest) stop wastefully driving Opus-tier inference and exacerbating ZAI
Fair-Usage 429s.

> Note: this helper only affects the **LLM cron** path. When a cron enqueues a
> Cody mission, Cody's model selection lives in
> `vp/clients/claude_cli_client.py`, not here.

### Timeouts

`_resolve_job_timeout_seconds` resolves the per-job wall-clock budget from
`job.timeout_seconds` (or `metadata.timeout_seconds`), clamped to
`[MIN_CRON_TIMEOUT_SECONDS=1, MAX_CRON_TIMEOUT_SECONDS=7200]`. The budget is
applied two ways: the outer `asyncio.wait_for` around the subprocess/coroutine,
**and** plumbed into the LLM request as `turn_timeout_seconds` so the execution
engine's own deadline matches (without this, a long cron is killed at the tier
default even though `asyncio.wait_for` allows more). A timeout writes a
`daemon_timeout_crash.json` report (`_write_timeout_crash_report`) and is
finalized as `execution_timeout`, `retryable=True`.

## Failure classification & retry

`_finalize_workflow_attempt` is the single finalizer for every terminal path.
Default `max_attempts` is **3** (`_max_attempts_for_job`, overridable via
`metadata.max_attempts`). The status → action mapping:

| Run status | Action |
|---|---|
| `success` | `mark_completed`; emits a success intelligence event (unless `mission_control_silent`). |
| `auth_required` | `mark_needs_review` (no retry) — surfaces a Composio connect link. |
| `error`, retryable | `queue_retry`; if a new attempt is granted, status becomes `retry_queued` and `_schedule_retry_run` fires a fresh `_run_job` with `skip_workflow_admission=True`. `_schedule_retry_run` is **loop-agnostic** (see below) — it must be, because the lightweight finalize path reaches it from a worker thread. |
| `cancelled` | `failure_class="cancelled"`, **never retried** (see deploy-window below). |
| rate-limited error | `_is_rate_limit_exception` matches Vercel/429/"too many requests" bodies → `retryable=False`; the cron's natural schedule is the backoff. |

The rate-limit carve-out exists because the 3-attempt retry tripled the call
rate into Composio's edge during a 429 window on 2026-05-23 — exactly the wrong
behavior. Matching is on the error **body text**, because the upstream SDK
discards the HTTP 429 status code.

### Pre-run workspace hygiene (paper_to_podcast_daily) — the root-cause fix

`paper_to_podcast_daily` reuses ONE fixed workspace
(``AGENT_RUN_WORKSPACES/cron_paper_to_podcast``) across every daily run, and
historically nothing cleaned it — so each run's deliverables piled up beside
prior runs'. That reused-and-never-cleaned directory was the root cause of a
recurring class of false success/failure calls: every downstream component (the
post-run guard, the artifact notifier) had to re-derive "is this file from THIS
run?" via mtime-vs-run-start heuristics, and those heuristics mis-fired in both
directions (2026-06-10 a stale manifest emailed as tonight's podcast; 2026-07-09
a real podcast produced but stale sidecars → false "zero usable papers" no-op).

The fix eliminates the ambiguity at its source:
``services/paper_to_podcast_workspace.py::prepare_run_workspace`` clears
``work_products/paper_to_podcast/`` **before** the run's LLM session is created.
It is called once per run from ``cron_service.py`` in the main LLM-execution path
(gated on ``metadata.system_job == "paper_to_podcast_daily"``, right before the
DB-lock retry loop that wraps ``create_session``). With a clean output dir, "does
``podcast_audio.m4a`` exist?" is a true binary check rather than a freshness
puzzle. It clears only the output subdir — the workspace ROOT (and its
``.nlm_resume.json`` deploy-restart checkpoint) is left intact so an interrupted
run can still adopt its in-flight notebook and re-download into the cleared dir.
The wipe is best-effort and never raises, so a cleanup failure cannot block a
run. Because of this, the mtime-freshness checks in the guard and notifier are
now a **backstop** (they catch a wipe that ever fails) rather than the primary
line of defence.

### Per-job post-run fail-loud guard (paper_to_podcast_daily)

Some cron runs can return rc=0 (the LLM coroutine completed and closed its task
normally) while producing zero useful work — the classic silent no-op. The
``paper_to_podcast_daily`` cron hit this on 2026-06-22: every ``download_paper``
either errored or was mis-checked, the agent's LLM narrative gracefully
concluded "cache empty", and the run exited ``clean_exit_zero / status=success``
in 5 minutes with no email and no podcast. The email-gap watchdog only caught
it ~32h later.

The mechanical fix lives in ``cron_service.py`` in the Phase F.1 close block
(right after ``_f_rc_equiv_llm`` is computed): for a job whose
``metadata.system_job == "paper_to_podcast_daily"`` and whose rc_equiv is 0,
``services/paper_to_podcast_guard.py::evaluate_paper_to_podcast_run`` inspects
the run's ``work_products/paper_to_podcast/`` artifacts for evidence that the
run really produced its deliverable. It searches BOTH the daemon-root
``work_products/paper_to_podcast/`` dir AND every per-attempt
``attempts/<NNN>/work_products/paper_to_podcast/`` dir (the LLM cron run
executes with CWD = the per-attempt subdir, so the skill's relative
deliverable writes land there), resolving each evidence file to its freshest
instance (greatest mtime). The ``_is_fresh(run_started_at)`` gate still
excludes stale files from prior runs. Success evidence, in priority order:

1. **The headline deliverable itself** — a fresh, real ``podcast_audio.m4a``
   (mtime at/after ``run_started_at``, size >= ``_MIN_PODCAST_AUDIO_BYTES`` =
   100 KB). This is ground truth: you cannot make a NotebookLM audio overview
   from zero sources, so a produced podcast proves the run succeeded regardless
   of whether the agent also wrote the JSON sidecars.
2. ``manifest.json`` with a non-empty ``papers`` list (fresh).
3. ``papers_metadata.json`` with >=1 entry (fresh).

If none of these is evidenced (or the skill's explicit ``FAILURE.txt`` sentinel
is present and fresh), the guard flips ``_f_rc_equiv_llm`` from 0 to 1 and
stamps a descriptive error on the run record — so the
``cron_consecutive_failures`` invariant, the dashboard, and journalctl all
surface a real failure instead of a silent no-op. The guard is gated on the
``paper_to_podcast_daily`` system_job id and is best-effort (any guard
exception is swallowed so it cannot mask the original outcome); a
timeout/cancel/exception keeps its real classification.

Why the podcast is checked first (2026-07-09): the guard previously keyed
success *only* on the two JSON sidecars. A run downloaded a real 38 MB
``podcast_audio.m4a`` + quiz + flashcards, published a report to the
scratchpad, and self-reported success — but never re-wrote ``manifest.json`` /
``papers_metadata.json`` (both left stale from the prior day). The freshness
gate correctly treated the stale sidecars as absent, so the guard misread a
real success as "zero usable papers (run no-op'd)" and, because the notifier
only runs on the rc=0 path (``cron_artifact_notifier``), suppressed the podcast
email and sent an ``[ERROR]`` instead. Accepting the fresh ``.m4a`` as
first-class evidence fixes both the false failure and delivery, without
reopening the 2026-06-22 no-op class (a genuine no-op leaves no fresh podcast).

Why both layouts are searched (2026-07-15): the guard previously inspected
only the daemon-root ``work_products/paper_to_podcast/`` dir. But the LLM cron
run writes its deliverables under ``attempts/<NNN>/work_products/paper_to_podcast/``
(its per-attempt CWD), leaving the daemon-root dir empty — so for two nights a
real ~40 MB ``podcast_audio.m4a`` was discarded as a "zero usable papers" no-op
and the podcast email suppressed. Searching the per-attempt dirs (freshest
instance, still freshness-gated) fixes the false failure without reopening the
2026-06-22 class.

The cache-path half of the same RCA was fixed in
``arxiv_runtime.py::canonical_arxiv_storage_path`` /
``arxiv_runtime.py::is_paper_cached``, which resolve the ONE directory the
arxiv-mcp-server writes ``.md`` files to (HTML-source AND PDF-source; the
server converts PDFs to markdown and deletes the intermediate PDF, so the
pipeline cache check must look for ``.md``, never ``.pdf``).

### Local arXiv metadata index (2026-07-11 — the HTTP-429 discovery fix)

The 2026-07-10 run (`run_id 78c38721000a`) died at step one: the single live
``mcp__arxiv-mcp-server__search_papers`` call returned HTTP 429 because arXiv
throttles the VPS IP **server-side**, keyed to cumulative traffic from every
arXiv consumer on the box. Client-side pacing (what the third-party MCP server
provides) cannot prevent that, and any hand-rolled client against the same
``export.arxiv.org/api/query`` endpoint would hit the identical limit — so the
fix changes the *access pattern*, not the client:

- ``services/arxiv_local_index.py`` maintains a **local SQLite FTS5 metadata
  index** (``~/.arxiv-local-index/arxiv_index.db``, override
  ``UA_ARXIV_INDEX_DB`` — resolver
  ``arxiv_local_index.py::canonical_index_db_path``) harvested in bulk via
  arXiv's sanctioned OAI-PMH interface (``export.arxiv.org/oai2``,
  ``metadataPrefix=arXiv``, sets ``cs,stat,eess``).
- The systemd timer ``universal-agent-arxiv-index-harvest.timer`` (04:40
  America/Chicago daily, installer
  ``scripts/install_vps_arxiv_index_harvest_timer.sh``, hooked into
  ``remote_deploy.sh``) runs ``harvest --days 3`` — a handful of polite OAI
  page requests; upsert-by-id makes the 3-day overlap idempotent. A failed
  harvest just leaves the index a day stale; the pipeline still works.
- One-time bootstrap after first deploy: ``harvest --backfill-months 12``
  (month-granularity windows, resumable per set — see
  ``arxiv_local_index.py::harvest_window``).
- Discovery becomes a **pure local read**: ``search`` (bm25-ranked FTS over
  title+abstract with a published-within-N-months cutoff,
  ``arxiv_local_index.py::search_index``) — zero live arXiv calls. Only the
  ~5 selected papers are then downloaded live via ``download_paper``.
- Last-resort offline path: ``cache-fallback``
  (``arxiv_local_index.py::cache_fallback_candidates``) deterministically
  ranks the already-downloaded full-text cache
  (``arxiv_runtime.py::canonical_arxiv_storage_path``) by topic-term overlap,
  so a run that cannot reach arXiv at all still assembles topic-relevant
  papers instead of no-op'ing.

All subcommands print one JSON object to stdout and exit 0 even when the
index is unavailable — agent callers branch on ``status``
(``ok`` / ``no_matches`` / ``unavailable``), not exit codes.

## Deploy-window detection (suppress restart noise)

This is the most operationally important gotcha. A cron subprocess that is
SIGTERM'd because the gateway is being restarted by a deploy is **not a
failure** — but without special handling it generated an
`[ERROR] Autonomous Task Failed` + `[WARNING] Retrying` email pair every time.

`cron_service.py::_is_deploy_window_active` returns true on either of two
OR'd signals:

1. The deploy-marker file `/tmp/ua-deployment-window` exists. `deploy.yml`
   creates it before `systemctl restart` and removes it on EXIT (or after a
   25-minute safety timer).
2. This gateway process started within the last 60 seconds
   (`_DEPLOY_WINDOW_FALLBACK_UPTIME_SEC`), computed from `/proc/self/stat`
   (`_process_start_time`). Covers the rare race where the flag's cleanup ran
   before the cron's failure handler, and operator-initiated restarts.

When a subprocess exits with a **negative** return code (signal-killed) inside
this window, the run is marked `cancelled`, `next_run_at` is advanced by
`_DEPLOY_CANCEL_BACKFILL_OFFSET_SEC = 5s`, and the retry chain is skipped. On
next gateway boot the startup pass re-fires it (and, for
`catch_up_on_restart` jobs, optionally backfills). Both `!script` paths and the
lightweight path implement this; the LLM path has two layers:

1. The `asyncio.CancelledError` handler covers the gateway's own coroutine
   being cancelled at shutdown.
2. The **deploy-kill signature** (`cron_service.py::_is_llm_deploy_kill_result`)
   covers two shapes of the same failure — the SDK's `claude` CLI subprocess
   being SIGTERM'd (exit 143) by a deploy restart, surfaced differently
   depending on how much work preceded the kill:

   - **Cold kill** (observed live 2026-06-09/10): the SIGTERM lands before the
     subprocess produced anything. The SDK swallows the message-reader fatal
     internally and `gateway.run_query` returns an *empty* result (no text,
     zero tool calls, no errors) without raising. The detector matches this
     via the empty-result surface signature.
   - **Mid-flight kill** (observed live 2026-06-16, `paper_to_podcast`): the
     SIGTERM lands AFTER the run did real work (notebook + sources + audio
     created, mid-poll), so the result has NON-empty text and `tool_calls > 0`
     and the empty-result signature would miss it. The engine
     (`execution_engine.ProcessTurnAdapter`) catches the terminated-process
     exception (`_is_terminated_process_error`) and surfaces a
     `metadata["subprocess_terminated"]=True` marker on the `GatewayResult`
     (collected by `InProcessGateway.run_query`); the detector keys off that
     marker first. This mirrors the `!script` branch's `exit_code != 0`
     robustness — the subprocess-exit signal itself is never a field on
     `GatewayResult`, so the marker is its surfaced proxy.

   Previously the Phase F.1 close computed `rc_equiv=0`, classified the kill
   as `clean_exit_zero`, marked the task completed, and the artifact notifier
   disclosed stale workspace leftovers as fresh output (or, for the mid-flight
   shape, suppressed the email because no fresh artifacts were ever
   downloaded) — zero operator signal for ~20h on 2026-06-16. Now, when either
   signature coincides with `_is_deploy_window_active()`, the run is marked
   `cancelled` (`failure_class="cancelled"`, never retried), `next_run_at`
   advances by the same 5s offset, the in-flight marker is kept for next-boot
   recovery (below), and the artifact notifier does not fire (`rc_equiv=1`).

> Both signals only ever **widen** the "treat as deploy cancellation" window —
> they never narrow it. A real crash (OOM, code error) outside the window still
> surfaces loudly, and an empty LLM result *outside* a deploy window keeps its
> pre-existing classification.

## Catch-up / backfill on restart

A job with `catch_up_on_restart=True` whose `next_run_at` was in the past at
construction time gets queued for backfill (if the miss is within the last 24h,
`_backfill_max_age`). **But backfill firing is OFF by default**: in
`CronService.start`, the queued backfills only dispatch when
`UA_CRON_BACKFILL_ON_RESTART` is truthy. Otherwise they are skipped with a log
line and resume on the next normal tick.

This default-off was a deliberate fix (2026-05-16 incident): firing every
missed heavyweight cron simultaneously at gateway boot starved the asyncio loop
(each does HTTP + in-process LLM work), the gateway couldn't answer
`/api/v1/health`, and `deploy.yml`'s 8-minute health check timed out → restart
loop. `_register_system_cron_job` still sets `catch_up_on_restart=True` on all
system crons; the *queue* is built but not *fired* unless the env var is set.

### In-flight marker recovery (deploy-interrupted runs)

The startup backfill above only sees jobs whose *persisted* `next_run_at` is
in the past — but `_scheduler_loop` persists `last_run_at`/`next_run_at`
**before** creating the `_run_job` task, so a run hard-killed mid-flight by a
deploy restart leaves no `cron_runs.jsonl` record and looks, on disk, like it
already ran. Two consecutive 9 PM `paper_to_podcast_daily` slots were lost
this way (2026-06-09/10).

The fix is a durable in-flight marker sidecar, `cron_inflight.json`, next to
`cron_jobs.json` (`CronStore.load_inflight` / `CronStore.save_inflight`):

- `CronService._mark_inflight` persists `{job_id: {scheduled_at, marked_at}}`
  at scheduler dispatch, before the `_run_job` task is created.
- `CronService._clear_inflight` removes it when the run finalizes — **except**
  for `cancelled` runs (deploy-restart collateral) and `retry_queued` runs,
  which keep their marker on purpose.
- On construction, `CronService.__init__` consumes any leftover markers:
  markers for enabled `catch_up_on_restart=True` jobs younger than
  `_backfill_max_age` are queued for recovery; everything else is dropped.
- `CronService.start` dispatches those recovery runs **even when
  `UA_CRON_BACKFILL_ON_RESTART=0`** — the global gate exists to prevent a
  startup stampede of *every* missed slot, while interrupted in-flight runs
  of explicitly opted-in jobs are a bounded set (at most
  `UA_CRON_MAX_CONCURRENCY` were in flight at the restart). The recovery
  dispatch key is `inflight:<job_id>:<scheduled_at>:<nonce>`, deliberately
  NOT the original `scheduled:` dedup key — the interrupted attempt's
  workflow run may still sit in `status=running`, and re-admitting under the
  same key would `attach_to_existing_run` and silently skip the recovery.

## System cron registration

`gateway_server.py::_register_system_cron_job` is the idempotent registrar
every `_ensure_*_cron_job` helper calls. Do not hand-roll cron creation — this
helper handles: lookup-by-`metadata.system_job`, update-vs-create, catch-up,
required-secrets plumbing, and the disable-propagation case. Key parameters:

- `default_cron` + `cron_env_var` / `default_timezone` + `timezone_env_var`:
  schedule defaults overridable by env.
- `required_secrets`: feeds the pre-flight check.
- `skip_task_hub_link=True`: opt out of Hermes Phase F auto-linking. Use for
  housekeeping sweeps (dispatcher sweeps, GC, re-rank) that produce no tracked
  work-product. Artifact-producing crons (briefings, digests, snapshots) leave
  it `False` to get F.1/F.3 observability.
- `lightweight=True`: see the lightweight `!script` path above. Validated to
  require a `!script` command — a misconfiguration raises at startup.
- `enabled=False` **with an existing enabled DB row**: the helper actively
  flips the persisted row to disabled via `update_job` (rather than silently
  no-op'ing), so turning a cron's env gate off in code actually disables the
  persisted job on next boot. Fixes the PR #534 hourly-insight regression where
  a disabled-by-default cron kept firing because its row stayed enabled.

The full system-cron roster is the block of `_ensure_*_cron_job()` calls in the
gateway startup (briefings, CSI convergence, ClaudeDevs intel, YouTube digests,
nightly wiki, proactive reports, Atlas direct dispatch, vault lint, etc.).

### Autonomous-briefing telemetry (housekeeping exclusion + split-aware run count)

Every system cron is stamped `metadata.autonomous=True`, so each successful run
emits a `kind="autonomous_run_completed"` notification (`gateway_server.py::_emit_cron_event`).
The autonomous daily-briefing collectors (`gateway_server.py::_collect_autonomous_activity_rows`
and its cron backfill `gateway_server.py::_collect_autonomous_runs_from_cron`) tally
those into the operator-facing "completed" count. Two corrections live here:

- **Housekeeping exclusion (ROOT A).** Lightweight bookkeeping crons fire far too
  often to be "proactive work" — `simone_chat_auto_complete` (`*/1`) alone is
  ~1382 runs/24h. They are listed in `gateway_server.py::HOUSEKEEPING_SYSTEM_JOBS`,
  stamped `metadata.housekeeping=True` at registration, and both collectors skip
  them from the `completed`/`failed` buckets (the notification path matches the
  `system_job` name; the cron-backfill path matches the flag **or** the name set, so
  pre-flag persisted rows are still excluded). Real proactive completions
  (briefings, digests, proactive reports) are unaffected.
- **Split-aware run count (ROOT B).** Under `UA_AUTONOMOUS_RUNTIME_MODE=split` the
  in-process `_cron_service` lives only in the `autonomous_worker` process, so a
  briefing composed elsewhere saw `_cron_service is None` and reported
  `cron_runs_in_window=0` despite thousands of real runs. `_collect_autonomous_runs_from_cron`
  now falls back to `gateway_server.py::_count_cron_runs_in_window_from_jsonl`, which
  reads the durable `WORKSPACES_DIR/cron_runs.jsonl` (the same file `CronStore.append_run`
  writes) and counts records in the 24h window. The completed/failed backfill buckets
  stay empty on that path (the jsonl carries no job metadata to classify by), but the
  count diagnostic is truthful.

### Failure-notification severity (retry-queued is not a failure)

`gateway_server.py::_emit_cron_event` classifies a run's terminal disposition
into a notification kind + severity. Only `error`-severity rows route out-of-band
(email/Telegram) via `_list_undelivered_high_severity_notifications`; `info` rows
are dashboard/event-bus only. The branch order matters:

- `cancelled` → `cron_run_cancelled`, **info** (deploy-restart collateral).
- `retry_queued` → `cron_run_retry_queued`, **info**. A failed-but-retryable run
  is emitted by `cron_service` as a `cron_run_completed` event carrying
  `status="retry_queued"`; without an explicit branch it fell through to the
  terminal-failure `else` and emailed an `[ERROR] Autonomous Task Failed` for
  **every self-healing transient** — e.g. `claude_code_intel_sync` hitting the
  X-API HTTP 402 cooldown (fails attempt 1, "succeeds" on the cooldown
  short-circuit), or any job that fails attempt 1/N then succeeds. The dedicated
  info-severity `cron_run_retry_queued` event is published separately; this branch
  stops the duplicate error email.
- terminal failure (retries exhausted → `status="failed"/"error"`) → `else` →
  `autonomous_run_failed`/`cron_run_failed`, **error** (still alerts). The
  `_should_suppress_upstream_outage_alert` dedup then collapses repeats of a known
  transient-service signature within the dedup window.

## Hermes Phase F: Task Hub linking

Unless `metadata.skip_task_hub_link` is set, every `!script` and LLM cron tick
auto-ensures a stable `cron:<system_job>` (or `cron:<job_id>`) Task Hub row via
`services/cron_task_hub_link.py::ensure_cron_task_link`, then opens an
assignment, stamps the subprocess PID (`record_worker_pid`, NULL for in-process
LLM crons), and on exit runs `classify_worker_exit` →
`_close_run`/`park_task_for_protocol_violation`. Auto-linked tasks are pre-
closed to `completed` on a clean rc=0 exit so the exit classifier sees
`clean_exit_zero` rather than a false protocol violation, then flipped back to
`open` so the next tick can reuse the perpetual task. The email-scheduler path
(which supplies its own `metadata.task_id`) keeps its own lifecycle.

Phase F SQL runs synchronously inside async coroutines; `_phase_f_start` /
`_phase_f_done` instrument each step (WARNING >5s, INFO >500ms) so a future
event-loop freeze can be pinpointed to the hanging step. The `mark_completed`
evidence-sync (`shutil.copytree`) on the lightweight path is wrapped in
`asyncio.to_thread` for the same reason (hot-patch 2026-05-26, confirmed by
py-spy) — `_run_job` calls `await asyncio.to_thread(self._finalize_workflow_attempt, …)`.

> **Loop-affinity gotcha (fixed 2026-05-31).** Moving the *whole*
> `_finalize_workflow_attempt` onto a worker thread silently broke the retry
> path: on a non-zero-exit lightweight run that helper calls
> `_schedule_retry_run`, which originally used a bare
> `asyncio.create_task(...)`. `create_task` is loop-affine and the worker
> thread has no running loop, so it raised `RuntimeError: no running event
> loop` and orphaned the `_run_job` coroutine — the intermittent "no running
> event loop" cron failures on hackernews_snapshot / atlas_direct_dispatch
> (only firing when a lightweight script exited non-zero *and* a retry was
> queued). The fix makes `_schedule_retry_run` loop-agnostic: `CronService.start`
> captures the scheduler loop in `self._loop`; the helper uses
> `asyncio.get_running_loop().create_task(...)` when already on a loop and
> `asyncio.run_coroutine_threadsafe(coro, self._loop)` when called from a worker
> thread. The other seven `_finalize_workflow_attempt` call sites run on-loop
> and were unaffected. Regression test:
> `tests/unit/test_cron_retry_offloop_scheduling.py`.
>
> *M3 update (2026-06-15): `atlas_direct_dispatch` is **retired**. Its
> ensure-function `gateway_server.py::_ensure_atlas_direct_dispatch_cron_job` now
> DELETEs the persisted cron row (`_cron_service.delete_job`) rather than
> registering a `*/1` job — the prefer-ATLAS lane is taken over by the M2
> `services/priority_dispatcher.py` (`classify_task` / `dispatch_claimed`, still
> flag-gated and default-OFF). The gotcha's general point stands for the other
> lightweight crons (e.g. `hackernews_snapshot`).*

> **Phantom-reap gotcha (fixed 2026-06-03).** In-process LLM crons run inside the
> daemon and their Task Hub assignment carries **no `provider_session_id`** (the
> PID/session-stamping in `ensure_cron_task_link` only applies to `!script`
> subprocess crons), so `task_hub.py::reconcile_task_lifecycle` — which protects a
> running assignment only when its session id is in the live-session set — cannot
> recognise an in-process cron as alive. That reconcile is invoked on **every
> dashboard read** of the agent queue
> (`gateway_server.py::dashboard_todolist_agent_queue`), so simply *opening Task
> Hub* while `paper_to_podcast_daily` was mid-run (these poll NotebookLM for
> 10–20 min) false-orphaned the live run: assignment → `failed`
> (`reconciled_orphaned_assignment`), the task bounced back to the unassigned
> column, while the in-process worker kept running underneath. Fix: the on-demand
> caller passes `cron_live_grace_seconds`
> (`gateway_server.py::_cron_reconcile_grace_seconds`,
> `UA_CRON_RECONCILE_GRACE_SECONDS`, default 3600s) and `reconcile_task_lifecycle`
> skips reaping a cron-owned assignment younger than that window. **Startup
> recovery deliberately keeps `cron_live_grace_seconds=0`** so a genuinely
> crash-orphaned cron is still reaped immediately. Regression test:
> `tests/unit/test_cron_reconcile_grace.py`.

## Outputs, persistence & wake

- Run records append to `cron_runs.jsonl`; jobs persist to `cron_jobs.json`
  (`CronStore`).
- `_persist_cron_run_output` writes full subprocess stdout+stderr to
  `<workspace>/run.log` (the `output_preview` field is capped at 400 chars and
  would otherwise truncate tracebacks).
- `_persist_run_output` writes `cron_result.md` into the workspace's
  `work_products/` and mirrors it to `<artifacts>/cron/<job_id>/`.
- `_organize_workspace_outputs` moves root-level deliverables into
  `work_products/` (and `work_products/media/`), keeping `run.log`,
  `transcript.md`, `trace.json`, `trace_catalog.md`, `MEMORY.md` at the root.
- On success, a session rollover is captured to shared memory (if memory is
  enabled).
- `_maybe_wake_heartbeat` wakes a target session's heartbeat (`now`/`next`)
  when `metadata.wake_heartbeat` and a session id are present. Since M4 the
  selective cron→heartbeat coupling closes the back door here: an **autonomous**
  system cron's `next`-mode wake is gated by the same default-deny allowlist
  (`coupling_wake_allowed_jobs` / `coupling_wake_selective_enabled`) used by
  `gateway_server.py::_maybe_wake_heartbeat_after_autonomous_cron`. Non-autonomous
  (user/email-scheduled) session wakes and explicit `wake_mode="now"` urgent wakes
  are unaffected. (The autonomous-cron coupling lane itself lives in
  `gateway_server` — see `03_heartbeat_service.md`.)
- `_emit_cron_success_intelligence` surfaces successful runs as Mission Control
  cards unless `metadata.mission_control_silent` is true.
- Email-scheduler crons (`metadata.source == "email_task_scheduler"`) mark
  their originating Task Hub item `complete` on success.

Sessions are deliberately **not** closed at run end — they keep their admin TTL
(default ~10 min) so the operator can click "Open" in the UI to rehydrate and
view the transcript; the gateway's session reaper cleans them up later.

## Environment flags

| Var | Default | Effect |
|---|---|---|
| `UA_CRON_MAX_CONCURRENCY` | `2` | Max concurrent cron runs (semaphore). |
| `UA_CRON_BACKFILL_ON_RESTART` | off | If truthy, fire queued backfills at startup (see incident note — leave off). In-flight marker recovery for `catch_up_on_restart` jobs runs regardless of this gate. |
| `UA_CRON_REGISTRATION_ENABLED` / `should_run_loop("cron_registration")` | prod on, dev off | Master gate for registering system crons. |
| `UA_CRON_DB_LOCK_RETRIES` | `2` | Retries on "database is locked" inside an LLM-cron attempt (clamped 0–5). |
| `UA_CRON_MOCK_RESPONSE` | off | Test seam: record `success`/`CRON_OK` without executing. |
| `UA_ARTIFACTS_DIR` | repo `artifacts/` | Where `cron_result.md` is mirrored; also injected as system context into LLM crons. |
| `metadata.model_tier` | `high` | Per-job Opus-vs-Sonnet reasoning tier (LLM path). |
| `metadata.max_attempts` | `3` | Per-job retry ceiling. |
| `metadata.lightweight` | `False` | Skip agent bootstrap for `!script` housekeeping crons. |
| `metadata.skip_task_hub_link` | `False` | Opt out of Phase F Task Hub linking. |
| `metadata.housekeeping` | `False` | Stamped by `_register_system_cron_job` for jobs in `HOUSEKEEPING_SYSTEM_JOBS` (`simone_chat_auto_complete`, `vp_mission_pr_reconciler`). Excludes their high-frequency success runs from the autonomous-briefing "completed" tally — see "Autonomous-briefing telemetry" below. |
| `metadata.required_secrets` | — | Env vars verified pre-flight. |

## Dormancy & timezone gotchas

Cron *scheduling policy* (which crons may fire when) is governed by the
operating-hours/dormancy rules, not by `cron_service.py` mechanics. **Interval**
content-generation crons (`*/N` or hourly ranges) should fire only inside the
6 AM–10 PM Houston (America/Chicago) active window; **fixed-time** crons run as
scheduled, and infrastructure-event handlers are exempt. The guard test
`tests/unit/test_cron_dormancy_defaults.py` enforces the window on interval crons
(unless in `DOCUMENTED_EXCEPTIONS` or carrying a `UA_<JOB>_24_7` runtime opt-out);
fixed-time crons get only an informational FYI. See
`08_operations/03_dormancy_and_operating_hours.md` for the full policy.

> [VERIFY: operational, code-external] Z.AI (the LLM proxy for all UA
> autonomous loops, including LLM crons) is capacity-limited during Greater-
> China peak hours (Beijing business hours, roughly 05:00–15:00 UTC =
> 00:00–10:00 US Central). This is the *inverse* of "run heavy batch overnight"
> intuition: heavy LLM crons scheduled for US night hit ZAI Fair-Usage 429s.
> Pick cron windows with this in mind. (Source: legacy
> `docs/operations/2026-05-08_zai_peak_time_scheduling.md`.)

## Code-observed gotchas

- **In-process scheduler = event-loop sensitivity.** Synchronous work in a tick
  (agent bootstrap, Phase F SQL, `copytree`) blocks the whole gateway. The
  lightweight path, `to_thread` wrap, and Phase-F timing instrumentation all
  exist because of this.
- **`to_thread` cuts both ways — audit the callee for loop-affine calls.**
  Wrapping a callable in `asyncio.to_thread` to unblock the loop runs it on a
  worker thread with **no running loop**, so any transitive
  `asyncio.create_task` / `get_running_loop` / `ensure_future` inside it raises
  `RuntimeError: no running event loop`. This is exactly how
  `_finalize_workflow_attempt → _schedule_retry_run` broke (see Phase F above).
  The standard fix is the loop-agnostic pattern: fast-path `create_task` when a
  loop is running, else `run_coroutine_threadsafe` onto a loop captured up front.
- **Backfill is off by default** — do not assume missed crons replay at boot.
- **`cancelled` is terminal, not retryable.** Deploy-window kills and session-
  reaper cancellations both land here; the explicit `asyncio.CancelledError`
  handler exists because `CancelledError` is a `BaseException` and would
  otherwise skip the generic `except Exception` finalizer, leaving a run un-
  finalized and producing a phantom failure on next boot.
- **Deleted job + in-flight retry** can storm without the top-of-`_run_job`
  guard; both the guard and `delete_job`'s `running_jobs.discard` are needed.
- **Disable must propagate to the DB.** Setting `enabled=False` in code only
  disables a previously-enabled persisted row because `_register_system_cron_job`
  explicitly calls `update_job`; a registrar that just returned `None` would
  leave the row firing.

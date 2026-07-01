---
title: "ADR: Resumable External Jobs (the requeue must have something to adopt)"
status: active
canonical: true
subsystem: plat-resumable-external-jobs
code_paths:
  - .claude/skills/paper-to-podcast-tf/SKILL.md
  - src/universal_agent/gateway_server.py
  - src/universal_agent/cron_service.py
  - tests/gateway/test_cron_ensure_jobs.py
  - tests/unit/test_cron_deploy_cancellation.py
last_verified: 2026-07-01
---

# ADR: Resumable External Jobs

> **Status: ACCEPTED — 2026-06-16.** Implemented for `paper_to_podcast`. Extends
> [ADR-12 Deploy-Restart Resilience](./12_deploy_restart_resilience_adr.md): the
> generic in-flight requeue is *necessary but not sufficient* for crons that
> hold state on an external service. `nightly_wiki_agent` has the same shape and
> is tracked as a follow-up (§6).

## 1. Context — the 2026-06-16 incident

The daily `paper_to_podcast` cron fires at 02:00 UTC, builds a NotebookLM
notebook, requests a deep-dive **audio overview**, and then polls Google for
~5-15 minutes until the audio finishes before downloading and emailing it.

On 2026-06-16 a gateway deploy at 02:19 UTC SIGTERM'd the run **while it was
polling**. The audio kept generating on Google's side and finished — but the
local run was dead, so nothing downloaded or emailed it. Simone recovered the
orphaned artifacts by hand.

The deploy-restart recovery (ADR-12) *did* engage: the durable in-flight marker
written before the task (`cron_service.py` `_mark_inflight`, before
`asyncio.create_task(self._run_job(...))`) survives SIGTERM and is kept on a
cancelled status, and the boot-time requeue re-dispatches the slot
(`catch_up_on_restart=True` for this job). **But it could not recover this run.**

### 1.1 Second orphan trigger (2026-06-30): an in-session yield, not a deploy kill

A scheduled run on 2026-06-30 reproduced the *same symptom* — audio finished on
Google's side, nothing downloaded it ("no audio delivered") — with a **different
trigger**: no deploy restart was involved. The cron agent launched the audio
poll as a `run_in_background: true` Bash task and then **yielded its turn**,
expecting the harness to wake it when the background task exited (true in an
interactive session, false here). In an autonomous cron session, the instant the
agent's turn ends the run is classified complete and torn down — orphaning the
poll. The `deep_dive` audio actually completed ~2 minutes later.

The §3 checkpoint is the *backstop* (the next daily run adopts the notebook and
downloads it); the primary fix closes the trigger. The poll instruction in
`_paper_to_podcast_command` and the skill (Phase B.5) now mandate a **single
FOREGROUND (blocking) Bash call** that keeps the agent's turn alive until the
audio is `completed`/`failed`, and **explicitly forbid** `run_in_background:
true` + yielding. That morning's *other* scheduled attempt died differently — the
arXiv search burned the entire turn-budget backstop retrying `download_paper`
into a transient HTTP 429 on the shared VPS IP — so the prompt also gained an
**arXiv-resilience** clause: reuse cached papers, never retry into a 429, proceed
with ≥3 papers, and cap paper discovery at ~5 minutes (fail fast and let the
daily cadence recover).

### 1.2 Third orphan trigger (2026-07-01): an ops `systemctl restart`, not a GHA deploy

The `paper_to_podcast_daily` cron started at 02:01 UTC and was killed ~90s later,
in Phase A. At `02:02:57 UTC` a `sudo systemctl restart
universal-agent-autonomous-runtime` (an ops action — the `/tmp/ua-deployment-window`
flag GHA's `deploy.yml` writes was **absent**) SIGTERM'd the claude CLI subprocess
mid-flight. `execution_engine._is_terminated_process_error` correctly caught the
death and the engine surfaced `metadata["subprocess_terminated"]=True` on the
`GatewayResult` exactly as designed (`cron_service._is_llm_deploy_kill_result`
matched). But `cron_service._is_deploy_window_active()` — at the time — only
recognised (1) the GHA flag file and (2) a gateway-uptime-under-60s fallback.
Neither was true (no GHA deploy; the autonomous-runtime process had been up for
hours), so the `_is_llm_deploy_kill_result(result) and _is_deploy_window_active()`
guard in `cron_service._run_job` never engaged. The run fell through to the plain
`success` classification (Task Hub's `_f_rc_equiv_llm=0`, i.e. `clean_exit_zero`),
which **cleared the in-flight marker** and skipped the boot-time
`catch_up_on_restart` requeue — the daily podcast was silently lost with zero
operator signal.

**Fix — cause-agnostic shutdown-interruption recovery**, not another
deploy-specific special case:

- `cron_service.mark_shutdown_requested()` sets a module-level
  `_SHUTDOWN_REQUESTED` flag, called from **both** `CronService.stop()` (top of
  the method) and `gateway_server.lifespan`'s shutdown path (the very start of
  the post-`yield` cleanup, before the heartbeat drain and before
  `_cron_service.stop()` — so it is visible to any in-flight cron run still
  finalizing during the drain; per §1.4 in the sibling
  [ADR-12](./12_deploy_restart_resilience_adr.md), `CronService.stop()` only
  cancels the scheduler loop task, not individual in-flight `_run_job` tasks, so
  those keep finalizing independently during the drain window).
- `cron_service._is_deploy_window_active()` now ORs in `_SHUTDOWN_REQUESTED` as a
  **third** signal alongside the GHA flag file and the uptime fallback. This
  makes the existing deploy-kill classification branch (§1) engage for **any**
  graceful shutdown/restart — a GHA deploy, the VPS installer, or an ops
  `systemctl restart` alike — not only GHA-driven ones. The name
  `_is_deploy_window_active` is kept for call-site continuity; its scope is now
  "graceful shutdown/restart", documented in its docstring.
- As a cause-agnostic backstop, the outer catch-all `except Exception` in
  `cron_service._run_job` (which previously marked **any** escaping exception
  `error` and cleared the in-flight marker, with zero deploy/shutdown awareness —
  unlike its sibling `except asyncio.CancelledError` branch right above it, which
  already special-cased shutdown-time cancellation) now checks
  `_is_deploy_window_active()` too: an exception that escapes the more specific
  `_is_llm_deploy_kill_result` signature check (e.g. one whose message doesn't
  match `execution_engine._is_terminated_process_error`'s token list) during an
  active deploy/shutdown window is classified `cancelled` (marker preserved)
  instead of `error` (marker cleared), mirroring the `CancelledError` handling.
  Outside a deploy/shutdown window, behavior is unchanged — a real crash/OOM
  still surfaces loudly.
- `paper_to_podcast_daily` already carries `catch_up_on_restart=True` on both the
  create and update paths of `gateway_server._ensure_paper_to_podcast_cron_job`
  (shipped with the 2026-06-16 fix in §1) — no change needed there; it's what
  lets the now-preserved marker actually requeue on next boot.

Regression coverage: `tests/unit/test_cron_deploy_cancellation.py` reproduces the
exact live precondition (no GHA flag file, gateway uptime `> 60s`) and proves
`mark_shutdown_requested()` alone flips `_is_deploy_window_active()` to `True`
and that a mid-flight `subprocess_terminated` result classifies `cancelled` with
the marker kept once the shutdown flag is set.

## 2. Root cause — the requeue had nothing to adopt

Two facts combine:

1. **The requeue re-runs the stored command verbatim.**
   `_build_workflow_trigger` carries `command=job.command`. That command (and the
   skill it loads) unconditionally said "create a NotebookLM notebook" — there
   was no resume branch. So the requeued run started a *brand-new* notebook and
   audio, never adopting the finished one. Worse, the topic is day-of-year based
   and `_ensure_paper_to_podcast_cron_job` rewrites the command on every boot, so
   a cross-midnight reboot could regenerate a **different** podcast.

2. **The only handle to the finished audio — the `notebook_id` — was never
   persisted before the kill.** The skill wrote `notebook_id` only into
   `manifest.json`, which it produced *last*, after download. A mid-poll kill
   happens before any manifest exists, so the recovery run has nothing on disk to
   re-attach to.

Net: the generic resilience layer treated a heavy, non-idempotent,
externally-stateful job as a cheap rerunnable command. The actuator fired and
had nothing to act on.

## 3. Decision

**Externally-stateful cron steps must (a) persist the external job handle the
instant it exists, and (b) expose a resume/adopt re-entry the requeue can take —
not just a create-from-scratch path.** The generic `catch_up_on_restart` requeue
stays the recovery *actuator*; this ADR makes the *job* recoverable.

Implementation for `paper_to_podcast` (no shared-code changes — see §5):

- **Resume checkpoint.** The skill writes `.nlm_resume.json` to the stable cron
  workspace root the moment `nlm notebook create` returns
  (`{notebook_id, topic, run_started_at, status}`; `status`:
  `creating → polling → done`), updates it after `nlm audio create`, and deletes
  it after a verified download. It is a **dotfile** so
  `_organize_workspace_outputs` (skips `.`-prefixed files) leaves it at the root
  and the artifact notifier never lists it.
- **Resume-first re-entry.** Both the command template
  (`_paper_to_podcast_command`) and the skill (Phase B.0) check for a *fresh*
  checkpoint (`status != "done"` and `run_started_at` within 24h, mirroring the
  requeue's own `_backfill_max_age`) before creating anything. If
  `nlm studio status <notebook_id>` shows the audio completed or still
  generating, the run **adopts** that notebook (re-poll → download → email) and
  uses the checkpoint's `topic`. Otherwise it falls through to a normal
  from-scratch run. Resume is strictly additive: a missing/stale/unreadable
  checkpoint yields exactly today's behavior.

## 4. Why not the obvious bandaids

- **Widen the deploy/drain window for crons.** Can't bound an external job — a
  5-15 min NotebookLM render never fits a sane `TimeoutStopSec`, and it does
  nothing for a hard kill/OOM. ADR-12 deliberately drains only the heartbeat.
- **Just re-fire the cron on boot.** That is already what happened; it is the
  proximate failure (rebuilds a new notebook, wastes quota, can switch topics).
- **Lower the poll interval / email earlier.** Shrinks the window, doesn't close
  it; the next mid-poll kill is equally unrecoverable.

## 5. Blast radius — kept narrow on purpose

- **Other `catch_up_on_restart` crons** (`autonomous_daily_briefing`,
  `youtube_daily_digest`, …) are untouched: all resume logic lives in the
  paper-to-podcast command + SKILL.md + checkpoint file. The shared requeue path
  is unchanged; other crons never read `.nlm_resume.json`.
- **The artifact-disclosure notifier / freshness gate** are untouched. This job
  emails itself as its final command step and does not route through
  `cron_artifact_notifier`, so the 2026-06-10 `mtime >= run_started_at` gate
  never applies here.
- **The deploy-casualty / cancelled severity path** is untouched. No change to
  rc classification or the deploy-window error suppression.
- **Workspace day-to-day bleed** is prevented by the 24h + `status != "done"`
  freshness check plus delete-on-success.

### Deliberately NOT done: flipping `notify_on_artifact`

The original design sketch suggested opting `paper_to_podcast` into the
artifact-disclosure notifier as a silence backstop. We did **not**, because the
notifier always sends an email, and `paper_to_podcast` already emails the
operator as its final command step — enabling it would add a **second email on
every normal day**, a regression. The resume fix restores delivery (the resumed
run reaches its own email step). The residual "interrupted and not yet
recovered" observability gap belongs to the deploy-cancellation notification
matrix (see the 2026-05-14 cron-deploy-cancellation plan and ADR-12), not the
success-disclosure rail.

## 6. Follow-ups (not in this change)

- **`nightly_wiki_agent`** (`scripts/nightly_wiki_agent.py`) creates NotebookLM
  notebooks from scratch with the identical anti-pattern. Apply the same
  checkpoint/adopt pattern. Tracked here; not silently assumed fixed.
- **Interruption observability** (a dashboard tile / single `[INFO]` for
  "interrupted → recovering / could not recover") — the proper home for the
  silence gap, per the 2026-05-14 plan.
- **Stronger idempotency** — the from-scratch path could reuse an existing
  same-topic notebook via `nlm notebook list` to cap quota waste on a
  false-negative checkpoint read. Larger surface; out of scope.

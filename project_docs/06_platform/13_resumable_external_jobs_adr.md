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
last_verified: 2026-06-16
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

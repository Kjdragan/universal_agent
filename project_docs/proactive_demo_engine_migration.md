# Proactive demo engine migration + volume governance

**Status:** design locked, implementing. Production is PAUSED while this lands
(`UA_PROACTIVE_TUTORIAL_AUTO_ROUTE=0`, `UA_PRIORITY_DISPATCHER_ENABLED=0`; 7 queued
demo missions cancelled). Re-enable only after PR-1/PR-2 verify green.

## Why

The `UA_PROACTIVE_DEMO_ENGINE` flip was wired only into the **`cody_demo_task`**
lane (`services/cody_dispatch.py:89-113`), which is effectively dead (5 builds
all-time, 0 today). All real proactive demos run through the **`tutorial_build`**
lane (`services/proactive_tutorial_builds.py`), which builds its objective in
`_build_task_description` (lines 656-699) — pure bespoke `/goal` instructions on
ZAI/GLM, **no** `build_demo.py`, **no** flag check. Result: the engine flip is a
no-op for ~99% of proactive demos (confirmed: completed `tutorial_build` rows carry
`build_engine = None`), AND there is no real per-day BUILD cap on that lane, so it
ran ~10 demos/day. A completion-bridge defect also re-surfaces finished tasks.

## Decisions (operator-confirmed)

- **Naming** (the on-disk repo dir + GitHub backup name):
  - operator `/demo` → `demo-<title>` (unchanged)
  - proactive **working** demo → `demo-proactive-<title>`
  - proactive **conceptual / no-working-demo** outcome → `demo-undemoable-<title>`
    (renamed at finalize; an honest "this run produced a briefing, not a runnable
    demo" marker — NOT a failure marker; the proactive pipeline legitimately yields
    conceptual outcomes sometimes)
- **Daily cap:** 3 proactive demo builds/day through the normal flow.
- **Golden-nuggets cron:** an end-of-day judge critically scores the day's REMAINING
  un-built candidates and builds 0–2 extra (hard ceiling 5/day), emailing those — so
  strong candidates that arrive after the first 3 aren't lost to timing. Dark-factory:
  it may build none if nothing clears the bar.

## Components

### A. Engine routing — `tutorial_build` → demo_factory `build_demo.py`
`services/proactive_tutorial_builds.py`. Gate on `proactive_use_demo_factory()`
(import from `universal_agent.feature_flags`; currently absent). In/after
`_build_task_description` (the objective the VP coder runs), when the flag is on,
emit a DEMO ENGINE OVERRIDE block mirroring `cody_dispatch.py:91-108`:
```
python3 {proactive_demo_factory_script()} "<one-line seed from video>" \
  --demo-id {demo_id} --slug proactive-{video_slug} --title "{video_title}" \
  --workspace-root /home/ua/lrepos --seed-url {video_url} \
  --endpoint-required any --promote --skill-tier library
```
(NO `--build-only` → full land: vault entry + EXHIBIT + GitHub repo backup, exactly
like `/demo`.) Output dir: `/home/ua/lrepos/demo-proactive-<video_slug>`.
`priority_dispatcher.py:528` forwards the description verbatim as the mission
objective, so no dispatcher change is needed — the override rides along.

### B. Save/finalize unification + naming
`services/tutorial_demo_finalize.py` + `vp/worker_loop.py:908-924`.
- Inject the demo_factory output dir as the FIRST `workspace_candidate` (compute
  `/home/ua/lrepos/demo-proactive-<slug>` from task meta), so `finalize` resolves the
  real built repo (not the mission workspace) → `demo_finalize.ok=True` (required by
  the completion gate `task_hub.py:5462`, else → needs_review).
- `manifest.json` is already written by `land_demo.py:168-179` — `_synthesize_manifest`
  keeps it (no clobber). Add a key-alias so the gateway walker reads it (`ts`→`timestamp`,
  add `marker_verified`) for dashboard fidelity (`gateway_server.py:21657+`).
- **Undemoable rename:** if the land outcome is `un-demoable` (read the vault
  status / manifest `status`), rename the dir `demo-proactive-<slug>` →
  `demo-undemoable-<slug>` before registering the symlink, and point the
  `/opt/ua_demos` symlink + `proactive_artifacts` row at the renamed dir.
- **Email parity:** wire `demo_built_notifier.notify_demo_built` into the
  `tutorial_build` terminal path (today only `cody_demo_task` calls it,
  `worker_loop.py:1003-1021`); the email already prefers a playable explainer-video
  link then exhibit/workspace.

### C. Real daily cap (3/day)
New flag `UA_PROACTIVE_DEMO_DAILY_CAP` (default 3). Enforce at the delegation point
for `source_kind == "tutorial_build"`: count `tutorial_build` missions DISPATCHED
today (vp_missions created_at = today) and skip-leave-queued beyond the cap. This is
the missing OUTFLOW control — `UA_DEMO_BUILD_DAILY_CEILING` only gated the auto-route
INFLOW (and was bypassed). Likely site: `priority_dispatcher` classification/dispatch
for the coder lane, or a guard in the tutorial_build delegation.

### D. End-of-day golden-nuggets cron judge
New `scripts/proactive_demo_nuggets_cron.py` + cron entry (e.g. `50 23 * * *` local).
- Pull the day's remaining un-built `tutorial_build` candidates (pending-approval /
  agent_ready not yet built).
- LLM critical-eye judge (real model) scores them; pick the top survivors.
- Build up to `min(2, 5 - built_today)` via the SAME `build_demo.py` engine path (A),
  named `demo-proactive-<slug>`; may build 0.
- Email each extra (the §B notifier). Log what was dropped (no silent truncation).

### E. Completion-bridge fix (kills re-surfacing)
`vp/worker_loop.py` + `task_hub.py`. `tutorial_build` has no dedicated terminal
branch — it falls to the default `else` where `_th_status_map` maps
`vp.mission.failed → OPEN` (re-claimable → re-dispatch), and the demo-lane close
never stamps `completion_token`, so the head-of-line guard (`task_hub.py:~1797`)
never engages and a retry/exhaustion sweep reopens finished tasks (→ "blocked" /
re-surface). Fix: add an explicit `tutorial_build` terminal branch (mirroring
`cody_demo_task`, `worker_loop.py:140-176`) that, on `demo_finalize.ok`, closes the
source task to `completed` AND stamps `completion_token`.

## Rollout (PR sequence)

- **PR-1 (safety, independent, low-risk):** C (real 3/day cap) + E (completion-bridge
  fix). Makes the lane safe to re-enable even on the current bespoke engine. Unit
  tests + byte-compile + ruff.
- **PR-2 (the engine switch):** A + B. Verify with a live VPS proactive build that
  lands `demo-proactive-<slug>` in `~/lrepos`, vault entry written, email fired,
  `demo_finalize.ok=True`, dashboard shows it.
- **PR-3:** D (golden-nuggets cron).
- **Re-enable** (flip the two pause flags back to `1` + restart) only after PR-1 and
  PR-2 verify green.

## Deploy
PR → `main` → `deploy.yml` (push to main; `git reset --hard origin/main` + `uv sync`
+ restart on the VPS). `src/**` deploys; `project_docs/**` won't restart prod.

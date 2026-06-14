---
title: "ADR: YouTube Brief / Tutorial / Demo Pipeline Redesign"
status: draft
canonical: true
subsystem: demo-tutorial-pipeline
code_paths:
  - src/universal_agent/scripts/youtube_daily_digest.py
  - src/universal_agent/services/proactive_tutorial_builds.py
  - src/universal_agent/services/tutorial_demo_finalize.py
  - src/universal_agent/services/self_briefing.py
  - src/universal_agent/vp/clients/claude_cli_client.py
  - src/universal_agent/tools/vp_orchestration.py
  - src/universal_agent/proactive_signals.py
  - src/universal_agent/hooks_service.py
  - src/universal_agent/gateway_server.py
  - src/universal_agent/services/todo_dispatch_service.py
  - src/universal_agent/services/dispatch_service.py
  - src/universal_agent/services/capacity_governor.py
  - src/universal_agent/vp/profiles.py
  - src/universal_agent/vp/worker_loop.py
  - .claude/skills/youtube-tutorial-creation/
  - .claude/skills/cody-implements-from-brief/
  - deployment/systemd/
  - web-ui/app/dashboard/tutorials/
  - web-ui/app/dashboard/claude-code-intel/
last_verified: 2026-06-11
---

# ADR: YouTube Brief / Tutorial / Demo Pipeline Redesign

> **STATUS: DESIGN APPROVED — IMPLEMENTATION IN PROGRESS (P0–P6 built; P0–P2 shipped on PR #887, P3–P5 shipped separately, P6 in this PR).** This ADR
> records the operator-ratified target design from the 2026-06-10 grilling session. It is the canonical
> spec for the work; phases P0–P5 below carry their own status markers. Sections describing *current*
> behavior are code-verified (2026-06-09/10); sections describing the *target* are marked **TARGET**.
> Cross-session handoff: a new session should read this doc, then implement the next unstarted phase,
> one branch→PR per phase.

## Context — what exists today (code-verified 2026-06-09/10)

Two **independent** YouTube → code-artifact pipelines run today, drawing from **different corpora**, and they
overlap on videos (double-processing confirmed: `Dk4MD6TNiWE`, `j6hnjNhx_MM` each ran through both on
2026-06-09).

1. **Daily Digest lane** — `youtube_daily_digest.py::process_daily_digest` (systemd timer
   `universal-agent-youtube-daily-digest`, 06:00 America/Chicago) reads the operator's curated day-of-week
   playlists (`MONDAY_YT_PLAYLIST` … from Infisical) plus gold-channel feeds. It emits the digest email +
   scratchpad (the **Brief**), and for demo-worthy videos dispatches via
   `youtube_daily_digest.py::_dispatch_tutorial_candidate` → HTTP POST `/api/v1/hooks/youtube/manual`
   (`source=manual`) → `hooks_service.py::build_manual_youtube_action` → the `youtube-tutorial-creation`
   skill, which writes a **tutorial artifact** (`CONCEPT.md`/`IMPLEMENTATION.md` + a runnable
   `implementation/` folder) under `artifacts/youtube-tutorial-creation/`. **This lane never builds a
   `/opt/ua_demos` demo.**

2. **Proactive-signals lane** — `proactive_tutorial_builds.py::sync_build_oriented_csi_videos` reads the
   *broad* CSI feed (`events WHERE source='youtube_channel_rss'`), LLM-judges buildability
   (`is_video_buildable_with_judge`), and enqueues `tutorial_build` Task Hub rows
   (`queue_tutorial_build_task`, `source=csi_auto_route`). The per-video judge has an optional
   **batched pre-pass** (`_judge_buildable_ids` → `classify_tutorial_buildability_batched`, knob
   `UA_TUTORIAL_BUILDABILITY_BATCH_SIZE`, default **1 = legacy per-video**): cache-read FIRST so
   steady-state cache hits never reach the LLM, then ONE structured-output call per chunk of uncached
   videos (haiku tier; `method='fallback'` ⇒ not cached / retried — byte-identical cache rules). The win
   is concentrated on cold-cache/backfill; **HIGH-precision ⇒ default-OFF until a live A/B holds**
   (`python -m universal_agent.scripts.zai_batch_triage_ab`). See
   [`06_platform/10_zai_rate_limiter.md`](../06_platform/10_zai_rate_limiter.md) §7.1. `todo_dispatch_service.py` routes `tutorial_build`
   → Cody (`vp.coder.primary`), which builds the runnable demo in `/opt/ua_demos/<id>`. **This lane is driven
   solely by the dedicated systemd timer** `universal-agent-proactive-demo-build-sweep`
   (`scripts/proactive_demo_build_sweep.py`, 3×/day), which calls `sync_build_oriented_csi_videos` directly
   (P1). The original dashboard-only trigger — `proactive_signals.py::sync_generated_cards` (run by
   `gateway_server.py::_run_proactive_signal_sync_background`, sole call site `GET
   /api/v1/dashboard/proactive-signals`) — was a redundant second invoker of the same producer; its
   `sync_build_oriented_csi_videos` call was **removed 2026-06-10**, so opening the dashboard no longer queues
   builds. (The `tutorial-build:<sha256>` dedup made the overlap harmless, but the timer is now the single
   producer.)

The Tutorial Backlog tab (`web-ui/app/dashboard/tutorials/`, backed by
`gateway_server.py::_list_tutorial_runs` + `::dashboard_tutorial_notifications`) lists artifacts from **both**
lanes. Its "Create Repo" button (`gateway_server.py::dashboard_tutorial_bootstrap_repo`) runs
`implementation/create_new_repo.sh` to copy a runnable `implementation/` into a fresh repo under
`private_repos` — it is **not** required for runnability (the `implementation/` folder already carries a
working `.venv`/`uv.lock`; self-test passes in place), and the script is **missing on recent artifacts**.

**Problems:** double-build of the same video; the only runnable-demo lane is unscheduled; the two corpora
overlap with no dedupe; demos aren't linked to their build session; and the demo build reproduces the
video's native stack with no gate, quota, or framework policy.

## Decision — the target design (TARGET)

### Glossary (canonical)

| Tier | Definition | Job | Home |
|---|---|---|---|
| **Conceptual Brief** | A summary of the video. | Understanding; no code. | Daily Digest (exists) |
| **Tutorial** | How to *use* the feature/capability **as presented in the video** (often Claude Code / terminal usage). | Teach the tool. | `artifacts/youtube-tutorial-creation/` → Tutorial Backlog tab |
| **Demo** | A standalone **runnable mini-app that demonstrates the video's capability**, built in **whichever stack best teaches it**. | A hands-on, functionally-complete code example. | `/opt/ua_demos/<id>` (verified · git · session-linked) |

A video climbs only as far as its content warrants: every video → Brief; teaching-worthy → Tutorial;
capability-bearing **and** gated → Demo.

### Demo build contract (the behavioral change)

The Demo is a runnable mini-app of the video's **capability**, not a reproduction of the video's tutorial.
**Framework selection (per video):**

| Video is about… | Demo is built in… |
|---|---|
| a specific SDK/stack (Google ADK, Gemini, LangGraph…) | **that native stack** — first-class, not a fallback |
| a Claude Code / Anthropic feature (e.g. `/goal`) | the **Claude Agent SDK** |
| a stack-agnostic concept ("memory pipelines") | **default to the Claude Agent SDK** (north star) |
| cross-framework integration (Claude Agent SDK ↔ ADK) | **holy grail — only on explicit operator direction**, never default |

- **North star:** we ultimately develop on the Claude Agent SDK, so demos build transferable capability —
  but native-stack demos are fully wanted for the learning.
- **Scope = broad:** any video capability worth a hands-on code example qualifies.
- **Purpose = operator learning/reference library:** **simple UI (no design-polish effort), but functionally
  sophisticated enough to fully exercise the capability.** Acceptance = functional completeness, not looks.
- **Worthiness ⊥ approach:** stack choice never blocks demo-worthiness; an ambiguous "how to build this one"
  only triggers a pause for operator input.
- **Model & API currency (verify, never recall):** Cody's training cutoff predates live model ids / API
  surfaces, and the source material names the *product* (e.g. "Nano Banana"), not the wire model id. The
  contract forbids hardcoding a model id / endpoint / SDK method / version from memory — Cody must resolve
  the *current* identifier from an authoritative source (`gemini-api-dev` skill, Context7, provider docs, or
  a minimal authenticated probe) or pause. A demo that ships a deprecated/invalid model id (a 404 on
  generate) is a FAILED demo. Added after the AI-Studio "Julia's Plushie Palace" demo shipped a dead
  `gemini-2.0-flash-exp-image-generation`. Pinned on both contract surfaces by
  `tests/unit/test_demo_build_contract.py`.

### Gate, throttle & sources

- **Two sources both feed the ladder:** the Daily Digest (curated playlists) and the Proactive-signals lane
  (broad CSI `youtube_channel_rss`). Dedupe by `video_id` across both.
- **Gate (Tutorial → Demo):** auto score-threshold **OR** the operator's one-click button.
- **Throttle:** auto-build the top-ranked (by `value_score`) up to **~10 Demos/day** (a safety/volume guard,
  **not** a cost limit); the rest queue for the button. **The button launches demos beyond the 10 (uncapped
  manual).** Classification (Brief/Tutorial/Demo) = existing worthiness signals
  (`youtube_daily_digest.py::_is_demo_worthy`, `value_score`, `code_implementation_prospect`) + an LLM
  tier-judge.
- **Scan cadence:** curated lane piggybacks the 06:00 digest; broad-RSS lane scans **3×/day** on a systemd
  timer (decoupled from the dashboard).
- **Sweep counts (`queue_tutorial_builds_with_ceiling` return / `latest_sync.json`):** the daily ceiling
  counts builds *created per America/Chicago day* across both lanes, so a later sweep sees only
  `remaining = ceiling − today_count` slots. The reported auto-dispatch total splits into **`auto_new`**
  (this run's genuinely-new dispatches, always `≤ remaining`, the number that consumes budget) and
  **`auto_reaffirmed`** (prior-run rows the no-churn invariant re-confirms, consuming *no* new budget).
  `auto_queued` (= `auto_new + auto_reaffirmed`) is kept for back-compat but reads as an apparent ceiling
  violation when carry-over re-confirmations inflate it — read `auto_new` against `remaining`.

### Inference, concurrency & cost

- **Cody builds demos on ZAI/GLM** — already in effect (`vp/profiles.py` `vp.coder.primary inference_mode="zai"`,
  no env/DB override). Claude-Agent-SDK + Claude-Max inference is currently broken, so **Claude-Agent-SDK
  demos must be wired to ZAI inference** (`ANTHROPIC_BASE_URL` → ZAI/GLM) to be runnable.
- **Token cost is a non-issue** (ample ZAI capacity).
- **Concurrency** is bounded by existing controls only — `services/capacity_governor.py`
  `asyncio.Semaphore(UA_CAPACITY_MAX_CONCURRENT, default 2)` + the Task Hub queue
  (`dispatch_service.py::claim_next_dispatch_tasks(limit=1)`). Demo builds are tracked Task Hub tasks, so
  queueing many is **safe** — they drain ~2-concurrent sequentially. No special rate-limit handling; the
  operator monitors after the fact for ZAI rate-limit trips.

### Cross-cutting requirement — implemented (P5)

Every Tutorial / Demo links to the **agent session that created it**, openable in the dashboard's
3-panel session viewer. Tutorial manifests are code-stamped post-run
(`hooks_service.py::_stamp_tutorial_manifest_build_session` → `build_session_id` / `build_run_id` /
`build_workspace_dir`); demo manifests are code-stamped at VP-mission terminal
(`vp/worker_loop.py::_stamp_demo_manifest_build_session` → `build_mission_id` / `build_session_id`).
`gateway_server.py::_list_tutorial_runs` and `gateway_server.py::_claude_code_intel_demos` surface
`session_id` / `run_id` / `session_url` (built by `gateway_server.py::_session_viewer_url`, mirroring
`web-ui/lib/viewer/openViewer.ts`); the Tutorial Backlog tab and the Demos panel render the link.
Pre-P5 manifests carry no stamp and simply render no link. The Brief tier is produced by the
deterministic digest script (no agent session exists to link). Residual: a `tutorial_build` mission
that builds into a fresh `/opt/ua_demos/<id>` dir referenced by neither the source task's
`workspace_dir` nor the mission outcome (`cli_workspace_dir` / `result_ref`) is not stamped.

### /goal build loop + deterministic finalize — implemented (P6)

`tutorial_build` demo builds run the real **/goal loop on ZAI** (verified live 2026-06-10), with a
deterministic finalize step that lands every completed build on the dashboard demo surface:

- **Goal scoping (no global flag flip):** `proactive_tutorial_builds.py::queue_tutorial_build_task`
  stamps `metadata.use_goal_loop=True` on every `tutorial_build` row; the flag-independent per-task
  override path in `self_briefing.py::is_goal_eligible_mission` (checked before
  `self_briefing.py::vp_goal_enabled`) makes the lane goal-driven while `UA_VP_GOAL_ENABLED` stays OFF
  globally. `dispatch_direct_demo.py` stamps the same flag for `cody_demo_task` direct builds.
- **Dispatch routing:** `vp_orchestration.py::_vp_dispatch_mission_impl` forces `execution_mode="cli"`
  for any goal-eligible mission even when `cody_mode="zai"` (previously only `cody_mode="anthropic"`
  reached the CLI client; the /goal harness only exists in the spawned `claude` CLI). It evaluates the
  SAME predicate (`self_briefing.py::is_goal_eligible_mission`) the worker-side routing + wall-clock
  default use.
- **The two-phase runner:** `claude_cli_client.py::_run_goal_loop_mission` — turn 1 (briefing,
  `self_briefing.py::build_self_briefing_prompt` → BRIEF.md + ACCEPTANCE.md + goal_condition.txt, the
  self-brief-and-attest skill's "Card mode" for tutorial_build cards), turn 2 (the work):
  `claude -p "/goal <condition>"` with the condition as an **argv** argument plus
  `--dangerously-skip-permissions` (the only empirically verified slash-command dispatch form;
  non-goal missions keep the stdin prompt path bit-for-bit). Goal-eligible missions skip the outer
  retry loop — the /goal evaluator is the retry mechanism. Missing/invalid goal_condition.txt
  degrades to the legacy single-pass prompt (`payload.goal_condition_missing=true`).
  The work turn is handed **only the condition Cody authored** — no UA-appended clauses (2026-06-11
  de-interference: the former COMPLETION.md attestation AND-clause and its paired
  `worker_loop.py::_execute_mission_logic` demotion guard were both removed to rely on vanilla
  `/goal`; the condition is the sole acceptance, and a missing COMPLETION.md no longer demotes a
  completed mission). Goal-eligible missions also get a **higher wall-clock backstop**
  (`claude_cli_client.py::GOAL_DEFAULT_CLI_TIMEOUT_SECONDS`=6000s vs the 30-min
  `DEFAULT_CLI_TIMEOUT_SECONDS`) so `/goal`'s own self-bounding clause ("stop after N minutes", up to
  ~90 min in authored conditions) — not UA's timer — terminates a healthy run; the 10-min idle-kill
  (`feature_flags.py::vp_no_progress_kill_seconds`, idle-based, fills the within-turn liveness gap
  vanilla `/goal` lacks) handles genuinely hung turns.
- **Stronger `/goal` evaluator on ZAI:** the built-in `/goal` completion evaluator runs Claude Code's
  "small fast model" — current CC reads that from `ANTHROPIC_DEFAULT_HAIKU_MODEL`
  (`ANTHROPIC_SMALL_FAST_MODEL` is deprecated), which on ZAI is the operator-locked `glm-4.5-air` (too
  weak to adjudicate acceptance reliably). `model_resolution.py::resolve_goal_eval_model` upgrades the
  work turn's evaluator to the sonnet tier (`glm-5-turbo`): `claude_cli_client.py::_run_goal_loop_mission`
  resolves it and threads `goal_eval_model` to `claude_cli_client.py::_execute_cli_session`, which sets
  `ANTHROPIC_DEFAULT_HAIKU_MODEL` **on that one subprocess's env dict only** — never `os.environ`, never
  `ZAI_MODEL_MAP` (the global haiku operator-lock is untouched), recorded as `payload.goal_eval_model`.
  `cody_mode="anthropic"` → no override (the Claude-Max session keeps the real Haiku evaluator; never
  inject a ZAI id into an `api.anthropic.com` session); `UA_GOAL_EVAL_MODEL=off` opts out. This **tightens
  the gate the outcome already relies on** — the `/goal` Stop hook blocks the subprocess from exiting until
  the evaluator returns `ok:true`, and `claude_cli_client.py::_monitor_cli_output` keys completion on that
  exit code — but it does **not** capture the structured met/cap verdict nor close the "OR stop after N
  turns" cap-clause escape; those remain tracked follow-ons. Rationale + precedence:
  [Model Choice & Resolution](../01_architecture/04_model_choice_and_resolution.md).
- **Deterministic finalize:** `tutorial_demo_finalize.py::finalize_tutorial_build_demo` runs in the
  worker's terminal sync (`worker_loop.py::_execute_mission_logic`, before the P5 stamp — so the
  previously no-op'ing `worker_loop.py::_stamp_demo_manifest_build_session` now finds a manifest):
  synthesizes a `DemoManifest`-compatible `manifest.json` when Cody didn't author one (never
  clobbers an authored one), runs existence-only mechanical checks (uv-managed env + README run
  instructions), and registers the demo by symlinking the workspace into `UA_DEMOS_ROOT` — the
  `gateway_server.py::_claude_code_intel_demos` walker follows symlinks with zero changes. The
  finalize result is recorded on the source task as `metadata.demo_finalize` (non-gating evidence;
  acceptance enforcement stays with the built-in `/goal` evaluator + the demo-lane
  completion-evidence gate `task_hub.py::DEMO_LANE_COMPLETION_GATED_SOURCE_KINDS`, which still
  requires `demo_finalize.ok`).
- **Live inference required, no mock pass-state (2026-06-11):** the `DEMO_BUILD_CONTRACT`
  (`proactive_tutorial_builds.py::DEMO_BUILD_CONTRACT`) and `_build_task_description` were tightened to
  demand the demo run against LIVE inference and forbid mocking the demonstrated capability —
  `endpoint_hit="mock"` is no longer an acceptable pass state (if a required key is unavailable the
  demo is NOT done; report it). This removes the false-pass at its source (Cody no longer authors
  mock-satisfiable conditions) by subtraction, not by adding a judge. The stale "Claude-Max OAuth is
  BROKEN" claim was also removed (Max OAuth verified working 2026-06-11; the ZAI routing for
  Claude-SDK demos is a cost choice, not a fallback).
- **Prompt↔tool-surface fix:** `todo_dispatch_service.py::TODO_DISPATCH_PROMPT` now instructs
  `delegate` (bridge-exposed, guardrail-accepted, sets `delegated`) instead of `redirect_to`, which
  `task_hub_bridge.py::task_hub_task_action` rejects; pinned by
  `tests/unit/test_todo_dispatch_prompt_actions.py`.

### Out of scope (parked)

- The Claude-Code-education **X-intel** demo track (`cody_scaffold_request` + `cody_demo_task`) stays as-is
  (dormant, X-gated); its own old/new dedupe is a later cleanup. See `06_demo_triage.md`.
- The **knowledge-wiki-LM** (NotebookLM) capability is a separate evaluation (universal vs Claude-Code-specific).
  See `07_llm_wiki.md`.

## Consequences

- One full build per video (the double-build is removed); one runnable home (`/opt/ua_demos`).
- The runnable-demo lane gets a real schedule; the dashboard page-view stops being a production trigger.
- "Create Repo" is demoted to an optional "export to standalone repo".
- More Cody build volume per day, bounded by the ~10/day auto cap + the concurrency semaphore.

## Implementation phases (each = its own branch→PR, VPS-smoke-verified)

| Phase | Status | Goal | Key touch-points (`file::symbol`) |
|---|---|---|---|
| **P0** | **done** (commit b2fc3bb0) | This ADR + glossary; fix the drift in `05_youtube_csi_flow.md` | `project_docs/04_intelligence/` |
| **P1** | **done** (commit 3564c4ce) | Schedule + dedup the broad lane (fix the "no cadence" bug) | new `deployment/systemd` timer (3×/day) → `proactive_tutorial_builds.py::sync_build_oriented_csi_videos`; decouple from `gateway_server.py::_schedule_proactive_signal_sync`; dedupe by `video_id` vs digest |
| **P2** | **done** (P2a 6d899b55 + P2b) | The gate + quota (rank → auto-build top-N/day → rest to button; manual approvals uncapped) | `proactive_tutorial_builds.py::sync_build_oriented_csi_videos` (ceiling + pending overflow), `proactive_tutorial_builds.py::approve_pending_tutorial_build` + `gateway_server.py::dashboard_tutorial_pending_build_approve` (approve endpoint), Pending Approval section in `web-ui/app/dashboard/tutorials/` |
| **P3** | **done** | Kill the double-build; stage the tiers (Tutorial = teaching-doc only) | `hooks_service.py::build_manual_youtube_action` + `webhook_transforms/manual_youtube_transform.py::transform` (teaching-doc-only prompts), `.claude/skills/youtube-tutorial-creation/SKILL.md` (implementation build removed), `youtube_daily_digest.py::_queue_demo_builds` → `proactive_tutorial_builds.py::queue_tutorial_builds_with_ceiling` (both sources, one ceiling, `video_id` dedupe); "Create Repo" demoted to optional "Export to Repo" |
| **P4** | **done** | Rewrite the Demo build contract | `proactive_tutorial_builds.py::DEMO_BUILD_CONTRACT` (framework-per-video rule + functional-completeness acceptance + ZAI inference wiring) embedded in every `tutorial_build` BRIEF via `proactive_tutorial_builds.py::_build_task_description`; same contract mirrored in `.claude/skills/cody-implements-from-brief/` (stale scrub/endpoint guidance fixed); `templates/ua_demos_scaffold/` README env-wiring invariant; `vp/profiles.py` stale-comment fix |
| **P5** | **done** | Session link (3-panel view) | `hooks_service.py::_stamp_tutorial_manifest_build_session` (tutorial manifests, success + recovered validation paths in `_dispatch_action`); `vp/worker_loop.py::_stamp_demo_manifest_build_session` (demo manifests at mission terminal, CLI + SDK lanes); `gateway_server.py::_list_tutorial_runs` + `gateway_server.py::_claude_code_intel_demos` + `gateway_server.py::_session_viewer_url` surface `session_id`/`run_id`/`session_url`; Tutorial Backlog tab + Demos panel render the link |
| **P6** | **done** | Real /goal build loop on ZAI + deterministic finalize | `proactive_tutorial_builds.py::queue_tutorial_build_task` (stamps `use_goal_loop`) + `proactive_tutorial_builds.py::DEMO_BUILD_CONTRACT` (runnable + manifest requirements); `vp_orchestration.py::_vp_dispatch_mission_impl` (goal-eligible → `execution_mode="cli"` on ZAI); `claude_cli_client.py::_run_goal_loop_mission` (briefing turn → `claude -p "/goal <condition>"` argv turn); `tutorial_demo_finalize.py::finalize_tutorial_build_demo` (manifest synthesis + mechanical checks + `UA_DEMOS_ROOT` symlink) wired in `vp/worker_loop.py::_execute_mission_logic`; `todo_dispatch_service.py::TODO_DISPATCH_PROMPT` `redirect_to`→`delegate` fix; self-brief-and-attest "Card mode" |

**Post-P2 tuning (operator-required):** validate the worthiness scoring produces the right *volume* (not too
few, not a flood) and the right *type* of candidates; tune the threshold/judge to surface the demos the
operator actually wants.

## Housekeeping (fold into the phase that touches each)

- `vp/profiles.py` carries a stale comment ("(coder) defaults to anthropic") while the code sets
  `inference_mode="zai"` — **fixed in P4** (the `VpProfile` dataclass comment now matches the code).
- `create_new_repo.sh` missing on recent artifacts — **moot as of P3**: new Tutorial runs never produce `implementation/`; the demoted "Export to Repo" button applies only to legacy runs that still carry one.

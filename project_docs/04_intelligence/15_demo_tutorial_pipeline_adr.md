---
title: "ADR: YouTube Brief / Tutorial / Demo Pipeline Redesign"
status: draft
canonical: true
subsystem: demo-tutorial-pipeline
code_paths:
  - src/universal_agent/scripts/youtube_daily_digest.py
  - src/universal_agent/services/proactive_tutorial_builds.py
  - src/universal_agent/proactive_signals.py
  - src/universal_agent/hooks_service.py
  - src/universal_agent/gateway_server.py
  - src/universal_agent/services/todo_dispatch_service.py
  - src/universal_agent/services/dispatch_service.py
  - src/universal_agent/services/capacity_governor.py
  - src/universal_agent/vp/profiles.py
  - .claude/skills/youtube-tutorial-creation/
  - .claude/skills/cody-implements-from-brief/
  - deployment/systemd/
  - web-ui/app/dashboard/tutorials/
last_verified: 2026-06-10
---

# ADR: YouTube Brief / Tutorial / Demo Pipeline Redesign

> **STATUS: DESIGN APPROVED — IMPLEMENTATION IN PROGRESS (P0–P3 built; P0–P2 shipped on PR #887, P3 in this PR).** This ADR
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
   (`queue_tutorial_build_task`, `source=csi_auto_route`). `todo_dispatch_service.py` routes `tutorial_build`
   → Cody (`vp.coder.primary`), which builds the runnable demo in `/opt/ua_demos/<id>`. **This lane has no
   schedule** — its only caller is `proactive_signals.py::sync_generated_cards`, run only by
   `gateway_server.py::_run_proactive_signal_sync_background`, scheduled only by
   `_schedule_proactive_signal_sync`, whose sole call site is `GET /api/v1/dashboard/proactive-signals`. It
   therefore fires **only when a human opens the Proactive Signals dashboard** (1:1 confirmed over 7 days).

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

### Cross-cutting requirement

Every Brief / Tutorial / Demo links to the **agent session that created it**, openable in the dashboard's
3-panel session viewer. Today only `source_task_id` is stamped (not the session/run id) — net-new wiring.

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
| **P4** | proposed | Rewrite the Demo build contract | framework-per-video rule + simple-UI/functionally-complete acceptance into `.claude/skills/cody-implements-from-brief/` / the `tutorial_build`→Cody dispatch BRIEF; Claude-Agent-SDK demos wired to ZAI inference |
| **P5** | proposed | Session link (3-panel view) | stamp `session_id`/`run_id` on tutorial + demo manifests at build time; `gateway_server.py::_list_tutorial_runs` + demo view + `web-ui` render the link |

**Post-P2 tuning (operator-required):** validate the worthiness scoring produces the right *volume* (not too
few, not a flood) and the right *type* of candidates; tune the threshold/judge to surface the demos the
operator actually wants.

## Housekeeping (fold into the phase that touches each)

- `vp/profiles.py` carries a stale comment ("(coder) defaults to anthropic") while the code sets
  `inference_mode="zai"` — fix in P1 or P4.
- `create_new_repo.sh` missing on recent artifacts — **moot as of P3**: new Tutorial runs never produce `implementation/`; the demoted "Export to Repo" button applies only to legacy runs that still carry one.

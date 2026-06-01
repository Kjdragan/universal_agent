# Handoff — Implement Stage 1: relevance gate on the ideation/insight sweep

**Repo:** `/home/kjdragan/lrepos/universal_agent` (branch `main`)
**Next-session goal:** Implement **Stage 1** of the agreed recommendation — a deterministic category-based relevance gate on the CSI ideation/convergence sweep so low-value non-domain videos (politics, geopolitics, war, cooking, health, noise) never become "ideation insight" candidates. This stops the political/geopolitical Atlas VP missions the operator was seeing fail/park.

This is **plan-approved, not yet coded.** Just build it.

---

## What this is and why (1-paragraph context)

The CSI → convergence → Atlas pipeline keeps generating low-value "ideation insight" candidates (e.g. "UK policy / arms embargo to Israel," "Trump AG scandals," "geopolitical friction + agentic AI"). Each becomes an `evaluate_and_author_intel_brief` VP mission that Atlas either fails on `claim_expired` or Simone parks citing the operator's `-1.00` preference on proactive insight detection. Root cause: **there is no domain-relevance filter anywhere** from CSI ingest → ideation candidate → dispatch. The operator runs an AI-agent platform and wants AI/agents/software/dev/SaaS insights, not geopolitics. The first per-video inference already emits a `category` enum that distinguishes domain from non-domain content; nothing enforces it. Stage 1 enforces it (code gates an already-LLM-produced judgment — the repo's sanctioned pattern).

Full investigation map (flow, file:line for every hop, the preference-key bug, claim-expiry mechanism, all kill-switches) is in this session's transcript and summarized in the findings doc — **do not re-investigate**; read:
- `docs/03_Operations/135_Code_Demo_Production_Checkup_2026-05-29.md` (the findings doc; PR #571).

## The exact Stage 1 change (verified citations)

**File:** `src/universal_agent/services/proactive_convergence.py`

1. Add a non-domain category denylist + gate the ingest query in `sync_topic_signatures_from_csi` (the SELECT at approx **`proactive_convergence.py:237-252`**, joining CSI `events` source=`youtube_channel_rss` with `rss_event_analysis a`). Add a filter so rows whose `a.category` is non-domain are excluded from becoming `proactive_topic_signatures`. Denylist (matches the CSI taxonomy emitted by the first inference):
   ```
   geopolitics_and_conflict, cooking, personal_health, noise, other_signal, longform_interviews
   ```
   (Domain-relevant categories to KEEP: `ai_coding_and_agents`, `ai_models_and_research`, `ai_news_and_business`, `software_engineering`.)
   - The category enum is defined/produced by the first inference: `CSI_Ingester/development/scripts/csi_rss_semantic_enrich.py:239` (prompt) and persisted to `rss_event_analysis.category` via `_upsert_analysis` (`:307`).
2. Because `_load_recent_signatures` (`proactive_convergence.py:638`) reads from `proactive_topic_signatures`, gating at ingest automatically keeps non-domain videos out of the ideation sweep corpus too — no second change needed there. (Confirm this by reading both functions.)
3. Make the denylist overridable via env (e.g. `UA_IDEATION_RELEVANCE_DENYLIST` / `UA_RELEVANCE_GATE_ENABLED`, default ON) so it's tunable without a deploy, per repo `.env`-clobbered-by-deploy constraints (use a code default, not a VPS `.env` edit).

**Scope discipline:** Stage 1 is `src/`-only (one deploy unit), no CSI-ingester change, no schema change, no backfill. Target ~30 lines + tests.

## Mandatory repo workflow (learned the hard way this session)

- **Work in a worktree** (background-job rule + the Edit tool is gated to the worktree). Use `EnterWorktree`.
- **TDD:** write the failing test first. Extend/add tests near `tests/unit/test_proactive_*` (there are existing convergence/ideation tests — grep `sync_topic_signatures_from_csi`, `_load_recent_signatures`, `proactive_convergence` under `tests/`).
- **Run the FULL `tests/unit` suite** (`uv run pytest tests/unit -q`), NOT just your file — a sibling PR this session went red in CI because a *different* pre-existing test pinned old behavior. Also `uv run ruff check .` + `py_compile`.
- **Doc update is part of the PR** (repo rule): update the canonical doc for this subsystem — likely `docs/02_Subsystems/Proactive_Pipeline.md` and/or the convergence section; bump `Last updated`. Read `docs/README.md` + `docs/Documentation_Status.md` to find the canonical doc first.
- **Branch → PR → main.** Never push to `main`. `worktree-*` / `claude/*` branches auto-merge after CI. After pushing, poll `gh pr checks <n>` AND `gh issue list --label ci-failure` (the CI watchdog auto-files issues — authoritative for headless sessions).
- **Verify-then-claim:** merged ≠ deployed. Confirm live via `curl localhost:8002/api/v1/version` SHA on `ssh ua@uaonvps` after deploy. Convert any timestamps to Houston (America/Chicago).
- VPS autonomy: `ssh ua@uaonvps` is available; run diagnostics yourself, don't hand the operator commands.

## Verification for Stage 1 (prod smoke after deploy)
- New `proactive_topic_signatures` rows post-deploy contain no denylisted-category videos.
- `convergence_candidates` generated post-deploy are all domain-relevant (no political/geopolitics narratives). Query the CSI / activity DBs via the canonical resolvers (`get_activity_db_path()` → `/opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db`; CSI = `/var/lib/universal-agent/csi/csi.db`). Do NOT query `CSI_Ingester/development/var/*.db` (stale).
- Atlas ideation-mission volume drops over the next active-window hours (`csi_convergence_sync` runs `0 6-21 * * *` America/Chicago).
- Instant mitigation if needed before ship: `UA_IDEATION_SWEEP_ENABLED=0`.

## Already DONE this session — do NOT redo
- **PR #572 (merged + deployed):** Path 2 fix — `proactive_tutorial_builds.py` no longer caches `no_summary` as a terminal judge verdict (unstuck the 534-row sticky cache).
- **PR #574 (merged + deployed, live SHA `aea635ce`):** deploy-window cron false-alarm fix — `cron_service.py` deploy-window branch now suppresses positive-rc (not just SIGTERM) cron failures during a deploy window.
- **PR #571 (open, auto-merging):** the findings doc (`docs/03_Operations/135_...`), incl. Path 3 verified-working correction.
- **Path 3 (demo OAuth):** verified a NON-bug (live demo `run_demo.sh` authenticated: `LIVE_HOOKS_OK`, `api.anthropic.com`). No fix needed.

## Deferred follow-ups (reference, do NOT bundle into Stage 1)
- **Stage 2:** add explicit `demo_kind` (`software_coding|ai_tooling|feature_functionality|none`) + `domain_relevant` structured fields to the first inference (`csi_rss_semantic_enrich.py:225` `_analyze_with_claude`, prompt at `:236`), persist on `rss_event_analysis` (new column via `ensure_schema`), backfill existing ~937 rows from `category`, and have the tutorial-build judge (`proactive_tutorial_builds.is_video_buildable_with_judge`) read the flag instead of its own 2nd LLM call. Touches the **CSI Ingester** (separate deploy unit). Only do this if Stage 1's coarse category gate proves insufficient.
- **Preference-key bug:** the `-1.00` "proactive insight detection" preference can't gate this lane — gate checks `type:convergence_candidate` while feedback writes `source:convergence_candidate`/`type:intel_brief` (`proactive_preferences.py:375` vs the gate `:309-321`), AND the brief thumbs-down endpoint (`gateway_server.py:20696`) never calls `record_artifact_feedback_signal`. Optional cleanup; the relevance gate is the more robust fix.
- **Claim-expiry reliability:** `stale_running_reconciled`/`claim_expired` VP failures are loop-starvation/deploy-restart collateral (renewal logic is correct, `worker_loop.py:509`; renewer shares the starved in-process asyncio loop). Stage 1 reduces frequency for free; durable fix is separate (kill-switch `UA_DAEMON_SESSIONS_ENABLED=0`).

## Suggested skills for the next session
- `superpowers:test-driven-development` (or the `tdd` skill) — Stage 1 is a tight TDD task.
- `/ship` — for the branch→PR→auto-merge handoff.
- `verification-before-completion` — enforce the full-suite + live-SHA verification before declaring done.
- (Optional) `oh-my-claudecode:explore` / `Explore` agent if you want to re-confirm the exact line numbers before editing — they may have drifted slightly.

## First moves
1. `EnterWorktree`.
2. Read `proactive_convergence.py:204-300` (`sync_topic_signatures_from_csi`) + `:638` (`_load_recent_signatures`) to confirm exact line numbers + the SELECT shape.
3. Write a failing unit test (denylisted category excluded from signatures; domain category kept).
4. Implement the gate + env override; run full `tests/unit` + ruff; update the canonical doc.
5. PR to main; watch CI + ci-failure issues; verify live SHA after deploy.

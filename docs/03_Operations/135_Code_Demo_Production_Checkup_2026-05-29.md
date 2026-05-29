# UA Code-Demo Production Checkup — Findings

**Investigation:** Does the UA YouTube/insight pipeline actually produce runnable code demos, and are they surfaced to the operator or generated silently?
**Date:** 2026-05-29 (Houston / America-Chicago time throughout)
**Method:** Source read first (file:line cited), then verified against live VPS state — `activity_state.db` (canonical, confirmed via gateway open file handles), `csi.db`, `/opt/ua_demos`, `/opt/universal_agent/artifacts`, Infisical metadata, a live headless auth probe. Read-only; no code changed.
**Two of my own early inferences were disproven during verification and are corrected below — flagged explicitly.**

---

## 1. Path inventory (the headline table)

| # | Path | Status | Producer | Consumer | Cap / gate | Notification (create / complete / fail) |
|---|------|--------|----------|----------|-----------|------------------------------------------|
| 1 | **Daily YouTube Digest → tutorial pipeline** | **(a) Alive, wired, producing daily** | `scripts/youtube_daily_digest.py` step 7 → POST `/api/v1/hooks/youtube/manual` (`:1872-1940`, `:2567`) | `hooks_service` → `youtube-tutorial-creation` skill → writes `artifacts/youtube-tutorial-creation/<id>/implementation/` | top-N **4** (`UA_YOUTUBE_DIGEST_AUTO_TUTORIAL_TOP_N`, `:2534`) + demo-worthiness gate score≥**70**, tier∉{low,unknown}, evidence≠metadata_only (`:1785-1809`) | create: dashboard-only (info) · complete: **dashboard-only** (`youtube_tutorial_ready`, success → not emailed) · fail: **email+Telegram+dashboard** (error) |
| 2 | **CSI RSS → `tutorial_build` → Cody** | **(b) Dead-end — wired both ends, 0 output** | `proactive_tutorial_builds.sync_build_oriented_csi_videos` → `queue_tutorial_build_task` (`:58-189`), driven by `_run_proactive_signal_sync_background` (`gateway_server.py:17497`) | routed to **Cody** by source_kind (`todo_dispatch_service.py:397`; `task_hub.py:53`; `HEARTBEAT.md:36`) | LLM buildability judge (`:420-485`); flags `UA_PROACTIVE_TUTORIAL_AUTO_ROUTE`/`UA_TUTORIAL_BUILD_JUDGE_ENABLED` both **ON** | create: **SILENT** · complete/park: batched next-morning digest only |
| 3 | **CSI demo-triage → `cody_demo_task` → `/opt/ua_demos`** | **(a) Wired & consumed, but ~1 real completion** | operator-approved triage drawer → `cody_scaffold_request`; Simone scaffolds → `cody_dispatch.dispatch_cody_demo_task` (`:45-150`) | **Cody** builds in `/opt/ua_demos/<id>/`; Simone evaluates (`cody_evaluation.py`) per `HEARTBEAT.md:216-242` | "claim ≤1 per cycle"; endpoint discipline `manifest.endpoint_hit==endpoint_required` | create: **SILENT** · complete: vault `## Demos` bullet + dashboard drawer + batched digest · fail: **SILENT** (real-time) |
| — | **Unify Path 1 into Path 3's workspace/eval/vault machinery** | **(c) Planned, deferred** | — | — | — | Trigger: "≥ CSI v2 live-verified for a meaningful period" (operator pref 2026-05-16); `docs/proactive_signals/youtube_demo_unification_plan.md` status 🟡 Deferred |

---

## 2. Per-path narrative + production evidence

### Path 1 — Daily YouTube Digest → tutorial pipeline (the working one)
The 6 AM digest ranks videos, applies a deterministic demo-worthiness gate (`_is_demo_worthy`, `youtube_daily_digest.py:1790-1809`), selects top-4 (`:2534`), and POSTs each to `/api/v1/hooks/youtube/manual` with `mode=explainer_plus_code` (`_dispatch_tutorial_candidate :1883-1940`). The hook runs the `youtube-tutorial-creation` skill, which writes a runnable `implementation/` repo.

**Verified on disk:** `/opt/universal_agent/artifacts/youtube-tutorial-creation/` = **155 video dirs, 80 with an `implementation/` subdir** of real runnable code (`agent.py`, `pyproject.toml`, `.ts`, `run_pipeline.py`), most recent **2026-05-29**. `youtube_daily_digest` cron `clean_exit_zero` on 05-27/28/29 (~11 AM CDT). **This path produces runnable code daily.**

### Path 2 — CSI RSS → `tutorial_build` → Cody (dead-end) — ROOT CAUSE CORRECTED
Both ends are wired: producer runs in the background proactive-signal sweep (`gateway_server.py:17483-17500`); consumer routing `source_kind=tutorial_build → Cody` exists (`todo_dispatch_service.py:397`). Both feature flags default ON (verified — no env override in live gateway).

**Production evidence:**
- `tutorial_build_judge` cache (activity_state.db) = **534 rows, ALL `buildable=0`, ALL `method=no_summary`**, judged 2026-05-18 → 05-26. Zero `buildable=True` ever.
- `tutorial_build` Task Hub rows = **93, all `parked`**, created in a ~1-second burst on **2026-05-16 ~6:53 PM CDT**; titles are *news headlines* ("Smoke cloud rises from central Bangkok…") — created **before** the judge existed (cache starts 05-18).
- `tutorial_build_task` proactive_artifacts = **286, frozen at `candidate` since 2026-05-16**.

**~~Early inference: "CSI RSS lacks summaries, so the judge starves."~~ WRONG — corrected:**
- Prod `csi.db` `rss_event_analysis` = **937 rows, 937 with non-empty `summary_text`** (795 `transcript_status=ok`), `max(analyzed_at)=2026-05-29 14:28 CDT`. Summaries exist and are current.
- All 5 sampled `no_summary` video_ids (`PENAJqmCSNI`, `OyIPXWn6MI8`, `eyvGw_ladjQ`, `x6iNGQ_J1Dw`, `QE4R61DirwY`) **now carry ~950–986-char summaries** (analyzed 2026-05-22), yet their verdicts (05-18→05-26) are `no_summary=False`.

**Real root cause — sticky negative cache + news-feed prefilter:** In `is_video_buildable_with_judge` (`proactive_tutorial_builds.py:434-447`), when the LEFT JOIN finds no summary yet (normal ingestion→analysis lag), it caches `method="no_summary", buildable=False` and returns. On a later sweep the summary now exists, so it skips that branch — but `_get_cached_judge_verdict` returns the **stale `no_summary` row before any LLM call**, so the video is **never re-judged**. Compounded by a recent RSS feed dominated by news/politics channels (MeidasTouch, The Enforcer) that the prefilter `_looks_build_oriented` (`:326-349`) correctly drops. Net: **`buildable=True` has never once been produced in prod.** One-line fix: don't cache `no_summary` as a terminal verdict (the code already declines to cache the `_judge_disabled` case for the same "re-judge later" reason at `:449-453`; the same was not applied to `no_summary`).
- *Related context (separate CSI issue, fixed today):* the CSI YouTube classifier was LLM-dark / keyword-fallback from 5/17 until PR #569 today — degrading `category` quality during the judge window. Complementary to, not the cause of, the sticky-cache bug.

### Path 3 — CSI demo workspace → `/opt/ua_demos` — ROOT CAUSE CORRECTED (twice)
Wired and operator-gated. Real code present: 14 workspace dirs, 10 with manifests, genuine scaffolds + `SOURCES/` + deterministic test harnesses.

**Production evidence:**
- Exactly **1 `cody_demo_task` reached `completed`** — **2026-05-29 ~10:54 AM CDT** (claude-code-hooks). **8 are `parked`.**
- Every Anthropic-endpoint demo fails its live-run step with **401**; only the **Gemini (API-key) demo reached `built`**.
- claude-code-hooks manifest: `endpoint_required: anthropic_native`, `status: layer_a_only`, `live_status: skipped_auth_unavailable`, `live_reason: "Failed to authenticate. API Error: 401 Invalid authentication credentials"`. Built **2026-05-29 11:14 AM CDT**.

**~~Inference A: "token not in gateway env (per /proc check)."~~ WRONG — invalid method.** `/proc/PID/environ` is frozen at `exec()`; `initialize_runtime_secrets` injects via `os.environ[k]=v` at runtime (`infisical_loader.py:144`), which never updates `/proc/environ`. Retracted.

**~~Inference B: "the Infisical OAuth token is expired."~~ WRONG — disproven by probe.**
- **Live probe (5:16 PM CDT):** with `ANTHROPIC_*` scrubbed (count=0) and `CLAUDE_CODE_OAUTH_TOKEN` present, `claude --print` → **`OK`, exit 0**. Token authenticates right now. (Value never printed.)
- **Token metadata:** prod `CLAUDE_CODE_OAUTH_TOKEN` `updatedAt=2026-05-26 10:12 AM CDT`, version 2 — **unchanged for 3 days**, i.e. the *same* token that was live at 11:14 AM today, and it's valid. Not a rotation/expiry artifact.

**Real root cause — credential precedence in the child process (not expiry):**
- `initialize_runtime_secrets` injects **all** Infisical secrets incl. the token with `overwrite=True`, no exclude for UA services (`infisical_loader.py:444-456`) → the gateway's `os.environ` *has* the valid token. `_scrubbed_env` preserves it (only `ANTHROPIC_*` stripped, `cody_implementation.py:291-294`).
- Yet the demo's child `claude` process reads the **stale on-disk `/home/ua/.claude/.credentials.json`** (mtime **2026-05-15**, ~14 days → past the ~7-day "Testing-mode" OAuth refresh window) instead of the env token → 401.
- Confirmed by the demo's own `BUILD_NOTES.md` (written by Cody): *"the parent Cody process authenticates fine via the Max-plan OAuth session, but the child process cannot reuse those credentials… the on-disk credential the child reads is not the live token the parent holds."*
- My probe (normal HOME, direct `claude --print`) used the env token and passed; the demo (hooks sandbox / project-local `.claude/`) hit the expired on-disk credential — same valid token, different credential resolution.

**Fix is credential-precedence, not secret rotation:** force the child `claude` to use `CLAUDE_CODE_OAUTH_TOKEN`, or refresh/remove the stale on-disk `.credentials.json`, or publish the OAuth app out of "Testing" so the on-disk credential stops expiring (note: operator has chosen to defer "publish to production" per the YouTube-OAuth strategy memory).

### Insight pipeline → demos (was a sub-question)
The CSI convergence → Atlas → digest insight pipeline does **not** autonomously escalate an authored brief into a code-demo. The only insight→demo bridge is **Path 3**, triggered by the **operator-approved tier-3 triage drawer** (`claude_code_intel.queue_follow_up_tasks` → `cody_scaffold_request`), largely superseded by the manual approve button (`claude_code_intel.py:1132-1133`). Human-gated, not autonomous; 1 completed demo total.

---

## 3. Silent vs. surfaced (the operator's core concern)

**Notifications that EXIST:**
- Path 1 **failure** → email + Telegram + dashboard (`youtube_tutorial_failed`, error, `hooks_service.py:2925/2932`).
- Path 1 lifecycle (started/progress/ready) → dashboard rows, Tutorials page (`tutorials/page.tsx:183-187`).
- Paths 2 & 3 **completion** → batched daily `[UA Build Review]` email via `proactive_artifact_digest` cron (08:35 CDT, `gateway_server.py:19467-19482`; `intelligence_reporter.py:246-248,381-424`) — *if* the artifact clears the top-12 review ranking that day (runtime-dependent).
- Path 3 completion → vault `## Demos` bullet (`cody_evaluation.py:380`) + claude-code-intel dashboard Demos drawer (`gateway_server.py:20277` ↔ `claude-code-intel/page.tsx:875-926`).

**Notifications that are MISSING (silent background work):**
1. **All three paths are silent on CREATION.** `queue_tutorial_build_task`, `dispatch_cody_demo_task`, scaffold-request builder — only Task Hub + proactive_artifacts row; no email/Slack/Discord/notification row. The operator is never told a demo build started.
2. **Path 1 SUCCESS is dashboard-only by design.** `youtube_tutorial_ready` is `severity="success"`; the `NotificationDispatcher` gate (`notification_dispatcher.py:17`) emails/Telegrams only `error`/`warning`. Fresh runnable code lands with no real-time inbox ping.
3. **Path 3 completion has no real-time push.** `complete_demo_task` (`cody_evaluation.py:314-337`) writes to no channel; surfacing depends on Simone separately running `vault-demo-attach` + the dashboard drawer / next-morning digest.
4. **The digest's own tutorial-dispatch summary is buried in the email attachment**, not the scannable inline body (`youtube_daily_digest.py:2591-2593` + `_split_email_body_and_attachment :1246-1254`).

---

## 4. Open questions / what verification couldn't settle
- **Does forcing the env token into the child `claude` make a fresh demo's live-run pass end-to-end?** Needs triggering one real `cody_demo_task` under the current gateway (action beyond read-only scope).
- **Does any completed demo actually clear the top-12 proactive-digest ranking** to get emailed? Plumbing verified; with only 1 completed demo ever, couldn't confirm one has appeared in a sent digest.
- **Stale `task_hub.db`** — Simone's heartbeat sometimes queries the legacy 57-row `task_hub.db` instead of canonical `activity_state.db`; possible effect on demo-review claim logic not assessed.
- Whether the Path-2 sticky-cache fix alone unblocks builds, or the news-heavy feed means few buildable candidates even with a correct judge.

## 5. Verified-in-code/data vs inferred
- All file:line citations: verified in source. All counts/timestamps: verified against live VPS DBs/filesystem/Infisical metadata/live probe.
- Both root-cause corrections (Path 2 sticky cache; Path 3 credential precedence, token valid) are evidence-backed, not inferred.
- Explicitly inferred: that a given completed artifact reaches the digest email on a given day (ranking is runtime-dependent).

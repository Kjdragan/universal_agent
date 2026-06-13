---
title: Insight Pipeline Build Plan (Phases 0.5/4/5/6)
status: active
canonical: false
subsystem: intel-proactive
code_paths:
  - src/universal_agent/services/hourly_intel_digest.py
  - src/universal_agent/scripts/hourly_intel_digest_cron.py
  - src/universal_agent/services/recent_briefs_index.py
  - src/universal_agent/services/proactive_convergence.py
last_verified: 2026-06-12
---

# Build Plan — Insight Pipeline Phases 0.5 / 4 / 5 / 6

**Status:** Phases 0.5 / 4 / 5 SHIPPED + deployed. Phase 6 GATED (≥24h stable + operator sign-off).
**Author:** Claude (Opus 4.8), 2026-06-02 (Houston: 2026-06-01 late evening).
**Grounding:** live prod recon `ua@uaonvps` 2026-06-02 ~02:55 UTC + canonical
`project_docs/04_intelligence/10_proactive_pipeline.md` (last_verified 2026-06-02)
+ phase catalog `docs/proactive_signals/insight_pipeline_completion_spec_2026-05-29.md`
§§9–12. Trust order: origin/main code → live prod → canonical docs.

> **Scope discipline.** This plan COMPLETES and HARDENS the settled architecture
> (Atlas evaluates+authors in background → Simone batches one hourly digest). It
> does not redesign. Every lever is env-flagged with a safe default; every phase
> ends with a real prod-artifact check, not just unit tests (Doc 130). Ask the
> operator before: raising email volume, raising worker concurrency >2, or the
> Phase-6 deletion.

---

## 1. Current verified state (live, 2026-06-02 — supersedes the spec's 2026-05-29 snapshot)

| Component | State | Evidence (live) |
|---|---|---|
| Convergence/ideation candidate generation | ✅ Working | `convergence_candidates`: 279 awaiting-author, 54 defer, **5 ship**, 285 skip |
| Atlas claims + authors `intel_brief` | ✅ Working | 5+ `intel_brief` artifacts; newest pa_7d3a3f50 (06-01 18:00), pa_a68d4fbc (06-01 16:23) |
| `daemon_simone_todo` dispatch | ✅ Healthy | 1103 completed, 94 failed, 20 abandoned; last completed 06-01 18:12 |
| `recent_briefs_index.md` (Atlas prior-verdict memory) | ✅ Written, **bounded (2026-06-04)** | `append_verdict_to_index` self-prunes to `UA_RECENT_BRIEFS_INDEX_MAX_ENTRIES` (default 60) on over-budget append; previously grew unbounded (~400 KB / ~100K tokens) |
| **Brief → digest EMAIL leg** | ✅ **FIXED (Phase 0.5, 2026-06-02)** | was: 2 ship briefs orphaned `not_surfaced` by a current-clock-hour selector gate (~40% of ship briefs); now a lookback window (`UA_DIGEST_BRIEF_LOOKBACK_HOURS`) surfaces undelivered ship briefs |
| Digest dedup | ✅ **SHIPPED (Phase 4, 2026-06-02)** | `hourly_intel_digest.py::dedup_near_duplicate_briefs` (Jaccard backstop, `UA_DIGEST_DEDUP_JACCARD`) + `::mark_superseded` for durable suppression |
| Operator feedback loop | ✅ **SHIPPED (Phase 5, 2026-06-02)** | was: built but **dead in-email** — `_render_card` read `metadata.feedback_url_up/down` but nothing populated them (Atlas authors thesis/entities, not URLs), so the digest emailed only a "Read full brief →" link and the 👍/👎 buttons never rendered (`proactive_artifact_feedback`: **1 row** total). Now `hourly_intel_digest.py::_attach_feedback_urls` mints fresh HMAC-signed per-brief links at send time (`UA_DIGEST_INLINE_FEEDBACK_LINKS`, default on). Endpoint→DB→index→Atlas legs verified wired. |
| `csi_convergence_sync` cron health | ✅ **Fixed 2026-06-02 (PR #665)** | was 900s-timeout flood; now parallelized + time-boxed (separate work) |
| **Digest actually EMAILING** | ✅ **FIXED (deterministic cron, 2026-06-02)** | was: no digest emailed since 2026-05-30 — Simone's heartbeat LLM silently stopped invoking `/hourly-intel-digest` (the selector/secret/scope were all healthy; `compose_send_payload` returns `ready`). Root-caused to the LLM-driven delivery path, not the digest code. Fix: new deterministic `hourly_intel_digest` cron (`hourly_intel_digest_cron.py::run_once`, `0 6-21 * * *` Houston, `UA_INTEL_DIGEST_CRON_ENABLED`) runs the same pipeline + sends via `AgentMailService` without an LLM. Heartbeat directive stays as harmless backup (throttle dedupes). |
| Legacy Track A/B + hand-trigger endpoints | ⚠️ Dead but present | Phase 6 deletion target |

**The dominant remaining gap is delivery, not detection.** Detection, authoring,
dispatch, and the recent-briefs index all work. Two freshly-authored briefs are
sitting `not_surfaced` — the hourly digest is not picking them up. **Phase 0.5
(close the email leg) is the prerequisite for everything else** and the single
highest-value fix for the operator's actual experience ("I get a good digest").

### What's already DONE/DECIDED (do not re-plan — reconciled from spec §12)
- **Phase 2 (ZAI 1301 resilience):** DONE — decision C = accept the drop; the
  drop is logged-not-silent (`logger.warning("convergence LLM refine failed …")`
  in `proactive_convergence.py::_refine_cluster_with_llm`). No code.
- **Phase 3 (cadence/dormancy):** DECIDED — active-window `0 6-21 * * *` is
  intentional (decision A). No code.
- **Phase 1 (worker concurrency=2):** RECLASSIFIED-deferred (spec §1.1). The VP
  worker loop is structurally serial; "concurrency=2" is a loop rewrite that
  risks the event-loop-starvation incident. Candidate volume is low, so the
  serial worker is likely adequate. **Re-measure claim latency before taking the
  risk** — included here only as an optional parallel verification (§6).

---

## 2. End-to-end flow (where each phase intervenes)

```mermaid
flowchart TD
    CSI[("csi.db rss_event_analysis")] -->|"csi_convergence_sync cron"| SYNC["sync_topic_signatures_from_csi"]
    SYNC --> SIG[("proactive_topic_signatures")]
    SYNC -->|"_detect_clusters_llm parallel budgeted"| CAND[("convergence_candidates")]
    SYNC -->|"_run_ideation_sweep"| CAND
    CAND -->|"inline triage PR628"| TASK["Task Hub convergence-candidate"]
    TASK -->|"daemon_simone_todo claims"| DISP["Simone delegates vp_dispatch_mission"]
    DISP -->|"Atlas mission"| AUTH["evaluate-and-author-intel-brief"]
    AUTH -->|"ship"| BRIEF["intel_brief artifact - not_surfaced"]
    AUTH -.->|"append"| IDX["recent_briefs_index.md"]
    BRIEF -->|"P0.5 select_candidates_for_current_hour"| DIGEST["hourly_intel_digest"]
    DIGEST -->|"P4 dedup + render"| EMAIL["one collated email"]
    EMAIL -->|"stamp emailed delivered_at"| BRIEF
    EMAIL -->|"per-brief thumbs link"| FB["briefs feedback endpoint"]
    FB -->|"P5 operator_rating"| FBT[("proactive_artifact_feedback")]
    FBT -.->|"surface prior verdict"| IDX
    IDX -.->|"prior-verdict awareness"| AUTH

    style BRIEF fill:#fff3cd
    style DIGEST fill:#e9f7ee
    style EMAIL fill:#e9f7ee
```

- **Phase 0.5** = the `BRIEF → DIGEST` edge (briefs stuck `not_surfaced`).
- **Phase 4** = the `DIGEST → EMAIL` edge (dedup + render).
- **Phase 5** = the `EMAIL → FB → IDX` loop (feedback closes).
- **Phase 6** = delete the dead legacy producers (not on this happy path).

---

## 3. Phase 0.5 (PRE-REQ) — Close the brief→digest email gap

**Problem (live):** `pa_7d3a3f50` and `pa_a68d4fbc` are `produced` but
`not_surfaced`; the digest emailed fine in late May. Something in
`hourly_intel_digest.py::select_candidates_for_current_hour` (or the heartbeat
cadence that invokes it) is no longer selecting them.

**RESOLVED — real bug (2026-06-02): SHIPPED.** Both stuck briefs are
**`verdict='ship'`** but `not_surfaced` / `delivered_at IS NULL`. The intel_brief
matrix: **3 ship `emailed`, 2 ship `not_surfaced`** — i.e. **~40% of ship briefs
were authored but never emailed**, so this was a real bug, not a no-op.

**Root cause:** `hourly_intel_digest.py::select_candidates_for_current_hour`
gated on the *exact current clock hour*:
`strftime('%Y-%m-%d %H', created_at) = strftime('%Y-%m-%d %H', 'now')`. A ship
brief authored at HH:MM is eligible only during clock-hour HH; if no digest run
catches it that hour (timing — e.g. the hour's digest already ran before the
brief landed), it is orphaned forever (next hour `created_at`-hour ≠ now-hour).
`delivered_at IS NULL` already prevents re-sends, so the hour-gate was
over-restrictive.

**Fix:** replaced the clock-hour equality with a lookback window —
`julianday(created_at) >= julianday('now', '-N hours')`, N = `_brief_lookback_hours()`
(`UA_DIGEST_BRIEF_LOOKBACK_HOURS`, default 24). Any recent undelivered ship brief
is now surfaced on the next digest run and emailed exactly once
(`delivered_at IS NULL` gates re-delivery; the per-hour delivery throttle still
caps one email/hour).

> **Follow-up correctness fix (2026-06-02):** the comparison was originally a
> raw string `created_at >= datetime('now', ?)`. The stored `created_at` is
> ISO-8601 (`...T...+00:00`) while `datetime('now', ?)` is space-separated, so
> the string compare mis-ordered them (`'T'` > `' '`) and **leaked briefs older
> than the lookback** whenever both timestamps fell on the same calendar date
> (also making `test_skips_briefs_older_than_lookback` flaky — it only passed
> when the 24h cutoff crossed a day boundary). Now both sides go through
> `julianday()` (numeric, format-agnostic). This matters for the deterministic
> delivery cron below, which emails whatever the selector returns.

| Field | Value |
|---|---|
| Files | `services/hourly_intel_digest.py::select_candidates_for_current_hour` + `::_brief_lookback_hours` |
| Acceptance | a ship brief authored in any of the last N hours, undelivered, is surfaced once and stamped `emailed`/`hourly_digest`/`delivered_at`; an already-delivered brief and a too-old (>N h) brief are excluded |
| Tests | `tests/unit/test_hourly_intel_digest_skill.py::test_picks_up_recent_undelivered_ship_briefs` + `::test_skips_briefs_older_than_lookback` (orphan recovery + bound); existing skip-delivered / skip-non-ship still green (27 passed) |
| Verify (prod) | after deploy, the 2 orphaned ship briefs (pa_7d3a3f50, pa_a68d4fbc) are picked up by the next digest run and stamped `emailed` |
| Env flag / rollback | `UA_DIGEST_BRIEF_LOOKBACK_HOURS` (set to `1` ≈ old current-hour-ish behavior); revert = one commit |
| Risk | LOW — `delivered_at IS NULL` prevents double-emailing; lookback bounds staleness |

---

## 4. Phase 4 — Digest dedup + template (decision D: both)

**4.1 Near-duplicate suppression — SHIPPED (2026-06-02).**
- **Index side (primary):** verified — `recent_briefs_index.py` builds the
  ship + skip/defer index Atlas reads; the "index-primary" dedup is Atlas's LLM
  judgment grounded in that index (it caught the two Google I/O clusters on
  2026-05-29). No code-level similarity function — and none needed. No regression.
- **Digest side (deterministic backstop):** implemented in
  `hourly_intel_digest.py::dedup_near_duplicate_briefs`, called from
  `::compose_send_payload` after selection (the selector stays pure). Jaccard over
  a normalized token set of `{title + thesis + key_entities}` (alnum tokens,
  len>2); threshold `UA_DIGEST_DEDUP_JACCARD` (default **0.6**, intentionally
  conservative — a false collapse hides a distinct brief, worse than letting a
  near-dup through; `1.0` disables). Input is pre-sorted best-first
  (needs_attention pinned, then composite_score desc), so the higher-priority
  brief survives. **Fail-open:** always returns ≥1.
- **Durable suppression:** `compose_send_payload` marks each dropped near-dup via
  `::mark_superseded` (`delivered_at` stamped so the selector stops surfacing it;
  `delivery_state='superseded'`, NOT `'emailed'`, so `is_throttled` doesn't count
  it). Without this the dropped dup would re-appear in the next hour's digest once
  its kept twin was delivered — the per-batch collapse alone is insufficient.

| Field | Value |
|---|---|
| Files | `services/hourly_intel_digest.py::dedup_near_duplicate_briefs` / `::mark_superseded` / `::compose_send_payload` |
| Acceptance | two near-identical briefs collapse to one in the email AND the dropped one is durably suppressed (`superseded`, not re-selected next hour); two distinct briefs both ship |
| Tests | `test_hourly_intel_digest_skill.py::DigestDedupTests` (collapse-keeps-first / keeps-distinct / disabled-at-1.0 / fail-open / `compose_durably_supersedes_dropped_near_duplicate`) — 32 passed |
| Env flag / rollback | `UA_DIGEST_DEDUP_JACCARD=1.0` disables the backstop |
| Risk | MED — over-aggressive dedup hides distinct briefs → conservative 0.6 default + fail-open (always ≥1) |

**4.2 Render review.** `render_digest_html` reviewed (code): per-card title +
thesis + why + tags, "Read full brief →" button (`{base_url}/briefs/{id}`),
footer (prefs / pause-24h / "why these?"), Gmail-safe inline CSS — no rendering
defects found. **The per-card 👍/👎 buttons were present in the template but
never rendered** because `_render_card` reads them from `metadata.feedback_url_up/down`
and nothing populated those fields — fixed in Phase 5 below. The live eyeball
(links resolve in a real inbox) happens naturally on the next scheduled digest;
**not force-sent** (would increase operator email volume — needs operator OK).

---

## 5. Phase 5 — Feedback loop + recent-briefs index verification — SHIPPED (2026-06-02)

**State (before):** `proactive_artifact_feedback` had **1 row** — the loop was
built but essentially unexercised. Verification found all four legs wired except
the **send-time link mint**, which was the dead leg.

**The four legs (all verified on `origin/main`):**

- **5.1 Endpoint → DB.** `gateway_server.py::briefs_feedback_get` verifies the
  HMAC token (`cron_artifact_notifier.py::verify_feedback_token`), writes the
  rating to `proactive_artifact_feedback` via
  `proactive_artifacts.py::record_feedback`, updates the most-recent
  `proactive_brief_scoring_log` row's `operator_rating`, and calls
  `recent_briefs_index.py::update_operator_rating_in_index`. ✅ wired.
- **5.2 Index reflects it.** `update_operator_rating_in_index` surgically
  rewrites the `operator_rating:` line on the matching `[SHIP]` block; a full
  rebuild (`_ship_block_from_artifact`) independently derives the rating from
  `feedback_json.last_score`. Index inclusion is gated on `verdict='ship'`, NOT
  on artifact `status`, and `update_artifact_state` never touches `verdict` — so
  a thumbs-down brief stays in the index carrying `operator_rating: 1`. ✅ wired.
- **5.3 Atlas reads it.** `evaluate-and-author-intel-brief/SKILL.md` rule 1
  (same-thesis-as-recent-ship → skip) and rule 4 (≥2 recent `operator_rating: 1`
  hits on matching `key_entities` → stricter bar, tilt to skip). ✅ wired.
- **5.4 The dead leg (GAP-FILLED).** `_render_card` reads
  `metadata.feedback_url_up/down` to emit the in-email 👍/👎 buttons, but the
  authoring pipeline never populated them (Atlas writes thesis/entities; the URL
  base + HMAC secret are send-time concerns), and `sign_feedback_token` was
  called only in the `/briefs/{id}` web viewer — so the **digest email's
  one-click feedback buttons never rendered**, which is exactly why the table had
  1 row. Fix: `hourly_intel_digest.py::_attach_feedback_urls` mints fresh
  HMAC-signed per-brief links at send time inside `::compose_send_payload`
  (mirroring the viewer's per-request mint), gated by `_inline_feedback_enabled`.

**CRITICAL boundary (held):** the feedback path writes ONLY to
`proactive_artifact_feedback` + the index — it never writes a
`proactive_preference_signal`, so it cannot re-introduce an implicit-outcome
preference path. Atlas's preference snapshot (`get_preference_snapshot`) remains
`explicit_feedback`-only (the 2026-05-24 poison-gate lesson).

> **Noted, out of scope:** `record_feedback` maps score `{1,5}→ACCEPTED` and
> `{3,4}→REJECTED` (a thumbs-down marks the artifact "accepted"). This is
> pre-existing, test-pinned behavior on the shared **email-reply** feedback path
> (`test_proactive_intelligence_phase1.py`) and does NOT affect the index/Atlas
> payoff (which keys off `score`/`last_score`→`operator_rating`, gated on
> `verdict` not `status`). Left untouched to avoid conflating code paths.

| Field | Value |
|---|---|
| Files | `services/hourly_intel_digest.py::_attach_feedback_urls` / `::_inline_feedback_enabled` / `::compose_send_payload` |
| Acceptance | the digest email renders a signed per-brief `/api/v1/briefs/{id}/feedback?v=up\|down&t=<hmac>` link that verifies against the endpoint secret; a thumbs-down is recorded + surfaced to the index + Atlas's rule 4 |
| Tests | `test_hourly_intel_digest_skill.py::InlineFeedbackLinkTests` (mint-when-absent / token-verifies-against-endpoint / disabled-by-flag / no-secret-degrades) — 36 passed |
| Env flag / rollback | `UA_DIGEST_INLINE_FEEDBACK_LINKS=0` disables the in-email mint (briefs still ship; rate via the `/briefs/{id}` viewer) |
| Verify (prod) | read-only: deployed `compose_send_payload` probe vs prod DB shows signed feedback URLs in the HTML (no force-send, no rating mutation) — confirmed 4 signed links render for the 2 ship briefs and round-trip through `verify_feedback_token` |
| Risk | LOW — additive, fail-open (no secret → no buttons), `explicit_feedback`-scoped |

### 5.5 Deterministic delivery cron — SHIPPED (2026-06-02)

**Problem (diagnosed live):** the digest stopped EMAILING after 2026-05-30. The
digest code was healthy — the deployed `select_candidates_for_current_hour`
returns `ready` with the orphaned ship briefs, not paused, not throttled, secret
resolves, scope filter keeps the `scope:hq` directive. The break was the
**delivery path**: the digest is invoked by Simone's `/hourly-intel-digest`
heartbeat directive, and the heartbeat LLM silently stopped invoking it (the
~hourly heartbeat tick emits `UA_HEARTBEAT_OK` with no tool calls; last actual
`compose_send_payload` execution ~2026-05-30 15:52 UTC).

**Fix:** a deterministic cron removes the LLM dependency.
`scripts/hourly_intel_digest_cron.py::run_once` runs the same
`compose_send_payload` pipeline, sends via `services/agentmail_service.py::AgentMailService`,
and stamps `mark_all_delivered` + `record_email_delivery` — no agent session.
Registered by `gateway_server.py::_ensure_hourly_intel_digest_cron_job`
(`0 6-21 * * *` America/Chicago, content-generation so dormancy-compliant). The
heartbeat directive stays as a harmless backup — `compose_send_payload` is
idempotent per clock hour (`is_throttled` + `delivered_at IS NULL`), so whichever
path fires first sends and the other sees `throttled`.

| Field | Value |
|---|---|
| Files | `scripts/hourly_intel_digest_cron.py::run_once` + `gateway_server.py::_ensure_hourly_intel_digest_cron_job` |
| Acceptance | a `ready` payload is emailed + stamped without any LLM; non-ready/disabled/send-failure paths send nothing and leave briefs eligible |
| Tests | `test_hourly_intel_digest_cron.py::RunOnceTests` (ready-sends-and-marks / not-ready-skips / disabled-flag / send-failure-no-stamp) |
| Env flag / rollback | `UA_INTEL_DIGEST_CRON_ENABLED=0` (default on) composes but does not send |
| Risk | LOW — idempotent throttle prevents double-send with the heartbeat backup; fail-open (send error → no stamp → retried next hour) |

> **Distinct from the legacy `hourly_insight_email` cron** (disabled,
> Phase-6 deletion target) — that is the OLD per-insight scorer, not this
> convergence-brief digest.

---

## 6. Phase 6 — Legacy deletion (GATED: ≥24h stable new path)

**Gate:** new path stable ≥24h. Clock the stability window from the most recent
pipeline-affecting deploy (the convergence-timeout fix PR #665 deploying
2026-06-02). Verify before deleting: sustained `daemon_simone_todo` claims,
briefs authoring, digest emailing (Phase 0.5 closed), no event-loop regressions.

> **Gate cleared 2026-06-03 + operator sign-off.** Delivery proven (digest sent
> 06-02 07:01 UTC; deterministic cron firing hourly `clean_exit_zero`, correctly
> idle on `no_candidates`). Read-only dead-symbol audit done — it **corrects two
> stale spec claims**:
> 1. `track_a_concrete_convergence`, `create_insight_brief_task`,
>    `create_convergence_brief_task`, `_detect_and_queue_convergence_async` are
>    **already gone** (no `def`, no callers — removed 2026-05); only a historical
>    docstring remains. Nothing to delete.
> 2. `proactive_brief_scoring_log` is **NOT dead** — written by the Phase-5
>    feedback endpoint (`gateway_server.py::briefs_feedback_get`), owned by
>    `proactive_scoring_log.py`, read by the (enabled) `insight_scoring_health`
>    weekly email + `briefings_agent`. **Do not drop.**
>    `insight_brief_task` / `convergence_brief_task` are **artifact_type strings**,
>    not tables. The "two gateway hand-trigger endpoints" no longer exist on main.
>
> **DONE (this PR):** dropped the genuinely-dead `proactive_convergence_events`
> table — removed its `CREATE TABLE`/index from `proactive_convergence.py::ensure_schema`,
> archived the prod table (2,525 historical rows, zero writers/readers) to a dump,
> and `DROP`-ped it. **DEFERRED (separate, operator-scoped):** retiring the legacy
> per-insight scorer cluster — `hourly_insight_email` (disabled) + `insight_scoring_health`
> (ENABLED weekly email) + the `briefings_agent` "ATLAS insight briefs" block +
> the legacy artifact_type strings. **NEVER:** `track_b_ideation_synthesis`,
> `proactive_brief_scoring_log`.

**⚠️ VERIFY-EACH-SYMBOL-STILL-DEAD on origin/main before planning deletion** —
`#568` already removed some legacy; grep live call sites (incl. the web UI) for
each before deleting. Candidate targets (confirm dead first):

| Symbol / surface | File | Pre-delete check |
|---|---|---|
| `track_a_concrete_convergence` | `proactive_convergence.py` | grep callers (spec notes it may already be gone — "no single function") |
| `track_b_ideation_synthesis` | `proactive_convergence.py` | ⚠️ **STILL LIVE** — driven by `_run_ideation_sweep`. **Do NOT delete** unless ideation is being retired. Reconcile with spec §10 6.1 (which lists it) — the spec is stale here. |
| `_detect_and_queue_convergence_async`, `create_convergence_brief_task`, `create_insight_brief_task` | `proactive_convergence.py` | grep callers |
| two gateway hand-trigger endpoints | `gateway_server.py` | grep web-ui + curl callers |
| ~~`proactive_convergence_events`~~ table | DB | ✅ **DROPPED 2026-06-03** (archived 2,525 rows first; zero writers/readers). `insight_brief_task`/`convergence_brief_task` = artifact_type strings, not tables. `proactive_brief_scoring_log` = **NOT dead, keep** (Phase-5 writer + enabled readers). |
| dead surfaces: `hourly_insight_email` cron, `insight_scoring_health` weekly email, `briefings_agent` "ATLAS insight briefs" block | various | confirm unscheduled/unreferenced |

| Field | Value |
|---|---|
| Files | `services/proactive_convergence.py`, `gateway_server.py`, test updates, dead-table migration |
| Acceptance | dead code/tables removed; `grep` shows no live callers; `uv run pytest tests/unit -q` green |
| Verify | grep call sites incl. web-ui before delete; prod smoke after |
| Risk | MED — deleting a hand-trigger the dashboard still calls → grep web-ui first; **`track_b_ideation_synthesis` is NOT dead** |

---

## 7. Sequencing, risks, and the real lever

```mermaid
flowchart LR
    P05["Phase 0.5 - email gap PRE-REQ"] --> P4["Phase 4 - dedup + render"]
    P4 --> P5["Phase 5 - feedback + index"]
    P5 --> P6["Phase 6 - gated deletion 24h+"]
    P1["Phase 1 concurrency - OPTIONAL re-measure"] -.->|"parallel"| P4
    style P05 fill:#fff3cd
    style P6 fill:#fee2e2
```

**Order: 0.5 → 4 → 5 → 6** (6 gated on ≥24h stable). Phase 1 only if a claim-
latency re-measurement shows the serial worker is inadequate.

| Risk | Mitigation |
|---|---|
| 0.5 turns out a no-op (briefs are `skip`/`defer`, `not_surfaced` is correct) | Treat 0.5 as a discovery gate; if so, pivot to the source/lane-mix follow-on (§ below) — that's the real lever |
| Dedup hides distinct briefs | conservative Jaccard default + fail-open (always ≥1) |
| Phase-6 deletes a live symbol (`track_b_…`) | grep-each-symbol-live gate; the spec's deletion list is stale |
| Feedback re-poisons preference | keep everything `explicit_feedback`-scoped (2026-05-24 lesson) |
| event-loop starvation if concurrency raised | leave Phase 1 deferred; re-measure first; keep `UA_DAEMON_SESSIONS_ENABLED` kill-switch |

**The real lever (follow-on, not in 4/5/6):** even with delivery fixed, ship
rate is ~1.5% (5 ship / 344 evaluated) because the CSI source/topic mix yields
mostly low-value convergences (Atlas's skips are *sound*). Surfacing genuinely
novel, operator-relevant convergences needs **source/lane tuning**, tracked
separately — it's the difference between "a digest arrives" and "a *great*
digest arrives."

---

## 8. Definition of done (per the spec §8, reconciled)
1. One collated digest email per active-window hour containing ≥1 brief, links
   resolve, briefs stamped `emailed`/`hourly_digest`/`delivered_at`. (Phase 0.5/4)
2. Near-identical convergences collapse to one in a digest. (Phase 4)
3. Thumbs feedback recorded + surfaced to Atlas (explicit-scoped). (Phase 5)
4. Dead legacy code/tables removed, tests green, `track_b` preserved. (Phase 6)
5. No Atlas per-insight direct emails; no empty digests; every lever env-flagged;
   docs updated in the same PR; deploy-verified (SHA + restart). (Boundaries)

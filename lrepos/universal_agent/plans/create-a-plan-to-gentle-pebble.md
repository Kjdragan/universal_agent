# Proactive Activity Health — Holistic Audit & Restoration Plan

**Last updated:** 2026-05-20
**Owner:** Claude (coding agent)
**Supersedes:** the WS1–WS4 close-out plan (kept in §A below for history)

---

## §0 — Why this plan exists

The watchdog we shipped in WS1–WS4 is correctly running on every heartbeat — but a holistic audit on 2026-05-20 shows the system has **three structural problems on top of the obvious coverage gap**. We have been treating each red light as a one-off, missing the pattern that the watchdog itself is partially blind and the notification loop never closes back to Simone. This plan stops that.

**The goal is no longer "ship more invariants." It is: every proactive process in UA either runs, or its silence is detected and routed to Simone within one heartbeat, with no parallel watchdog systems and no silent skips.**

---

## §1 — Ground-truth inventory (snapshot 2026-05-20 17:36 UTC)

**30+ proactive systems** identified across 5 surfaces. Full table in §1.5; summary here.

| Surface | Count | Watchdog-covered today |
|---|---|---|
| Gateway crons (`gateway_server.py`) | 20 | 10 |
| CSI adapters (`CSI_Ingester/.../adapters/`) | 6 | 1 (youtube only) |
| CSI polling tasks (`service.py`) | 2 | 0 |
| GitHub Actions scheduled workflows | 3 | 0 |
| Heartbeat-driven activities | 4 | 2 (morning_briefing + csi_convergence_sync) |
| AgentMail polling | 1 | 0 |
| **TOTAL** | **36** | **13** |

**Per-source CSI event activity (last 7d, from `csi.db`):**

| Source | 7d events | Last event UTC | Status |
|---|---|---|---|
| hackernews | 736 | 2026-05-20 17:30 | ✅ healthy |
| csi_analytics | 18 | 2026-05-20 12:25 | ✅ healthy |
| youtube_channel_rss | 349 | 2026-05-18 23:31 | ❌ **42h stale** |
| threads_trends_broad | 3 | 2026-05-18 20:47 | ❌ **45h stale** |
| youtube_playlist | 0 | — | ❌ silent ≥7d |
| reddit_discovery | 0 | — | ❌ silent ≥8d |
| threads_owned | 0 | — | ❌ silent ≥7d |
| threads_trends_seeded | 0 | — | ❌ silent ≥7d |

**Only youtube_channel_rss has an invariant. Four adapters dead, three of them invisible to the watchdog.**

---

## §2 — Structural problems found in the watchdog itself

These are bugs in the framework we already shipped, not just missing invariants. **All must be fixed before adding new invariants — otherwise new invariants land on broken plumbing.**

### P0a — Sidecar `crons: []` is always empty

Latest sidecar (`/opt/universal_agent/AGENT_RUN_WORKSPACES/run_daemon_simone_heartbeat_*/work_products/proactive_health_latest.json`) shows:

```json
"crons": []
```

`build_proactive_health_payload` accepts `cron_jobs: Optional[Iterable[Any]] = None`. The handler in `gateway_server.py:16019-16060` apparently passes nothing (or an iterable that resolves empty). Cron last-run state is the entire Layer-1 watchdog — invisible right now.

**Impact:** if any of the 20 gateway crons stops firing tomorrow, the watchdog will not detect it. Layer 2 (invariants) only catches a subset of those crons by checking their downstream artifacts.

### P0b — Invariants read from the empty `runtime_state.db`

PR #392 wired the aggregator to open `connect_runtime_db(get_runtime_db_path())` and pass it as `runtime_conn` to invariants. The path resolves to `/opt/universal_agent/runtime_state.db`. **That DB has no `proactive_artifacts` table.**

Actual production data lives in:
- `/opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db` — 266 insight_briefs, 23 claude_code_intel_operator_reports, 41 claude_code_intel_packets in last 24h
- `/opt/universal_agent/workspaces/runtime_state.db` — 13 daily_digest rows
- `/opt/universal_agent/AGENT_RUN_WORKSPACES/runtime_state.db` — older insight/cluster data

**Impact:** the following invariants are silently no-op'ing (passing always, no findings emitted): `proactive_artifact_digest_delivery`, `proactive_reports_daily_trio`, `claude_code_intel_packet_freshness`, `csi_demo_triage_rank_artifact`, `paper_to_podcast_email_delivery`. That's 5 of the 10 invariants we shipped — half the Layer 2 surface.

This is the **same class of bug** as PR #392 fixed (wrong DB for the proactive tables) — but PR #392 picked the wrong target DB. The real prod data is in `AGENT_RUN_WORKSPACES/activity_state.db`.

### P0c — Notification loop is critical-only and Simone consumption unverified

`proactive_health_notifier.py` emails only on `severity == "critical"`. Warns sit in the sidecar forever and nobody acts. The morning_briefing has been firing a `warn` finding for 12 hours and we wouldn't know unless we manually opened the JSON.

Simone's HEARTBEAT.md path of "read sidecar → emit Task Hub items for findings" is not verified in this audit. Need to confirm and (if missing) wire it.

---

## §3 — Coverage gap (Layer 2)

After §2 is fixed, these 23 systems still need invariants. **The plan does NOT add 23 individual invariants — it adds 2 universal sweep invariants that cover the majority, plus targeted invariants only where a universal sweep can't see the pipeline.**

### P1a — Universal CSI adapter freshness invariant (1 invariant, covers 6 adapters)

```
for each enabled CSI source in csi.db events:
  last = max(occurred_at) where source = $src
  expected_max_silence = lookup(intel_lanes.yaml / source config)
  if now() - last > expected_max_silence: emit critical finding for that source
```

Catches all 6 adapters in one probe. Replaces the existing two YouTube-specific invariants OR runs alongside them with different signal (coverage vs. liveness).

### P1b — Universal cron staleness invariant (1 invariant, covers 20 crons)

After P0a populates `crons[]`:

```
for each cron in crons[]:
  if not enabled: skip
  interval = parse_cron_expr(cron_expr)  # smallest expected gap
  if now() - last_run_at > 2 * interval: emit warn finding
  if last_outcome != "success": emit warn finding
```

One invariant catches every cron that stops firing or starts erroring.

### Targeted invariants (kept, but rebased on correct DB)

After P0b fixes the DB path, the 5 already-shipped invariants for proactive_artifacts/emails will start firing meaningfully. No new code needed — just the DB-path fix.

### Deliberately skipped (documented in §1.5 + canonical doc)

- `architecture_canvas_drift` — silent-success cron; covered by P1b (cron-staleness) instead
- `atlas_direct_dispatch`, `simone_chat_auto_complete` — housekeeping crons, covered by P1b
- GHA scheduled workflows — separate notification surface (GH email), out of scope for proactive_health
- AgentMail polling — covered by the existing `email_handler` flow + Task Hub stale-task detector

---

## §4 — Sequence of work (strict order — no skipping)

```
P0a → P0b → P0c          (fix the framework — 1 PR)
        ↓
P1a + P1b                 (universal invariants — 1 PR, depends on P0a/b)
        ↓
P2 (dead adapters)        (operational — investigate after watchdog can see them properly)
        ↓
P3 (warn escalation)      (close the loop — 1 small PR)
```

**Why strict order:**
- P0 fixes the framework so new invariants will actually fire and be seen.
- P1 adds the coverage that turns 23 invisible systems into 23 visible ones.
- Only then is P2 worth doing — once we drill into the dead adapters, the watchdog will hold the new ground and tell us if our fix regresses tomorrow.
- P3 closes the loop so the next failure surfaces *via the system*, not via the operator noticing 40h later.

---

## §5 — Integration with existing watchdog (no parallel system)

**No new aggregator. No new sidecar. No new notifier.** Everything extends what we already have:

- `src/universal_agent/services/proactive_health.py` — gets the DB path fix (P0b) and the cron-jobs wire-in (P0a). No new aggregator file.
- `src/universal_agent/services/invariants/` — gets two new files (`csi_source_liveness.py`, `cron_staleness.py`) registered via the existing `__init__.py` discovery. Same pattern as the 5 existing invariant modules.
- `src/universal_agent/services/proactive_health_notifier.py` — gains warn-escalation logic (P3). Same module, same dedup, same channel.
- Sidecar path stays exactly where it is (per-heartbeat work_products dir) so Simone keeps reading it from the same place.
- `gateway_server.py:16019-16060` — minor edit to pass `CronService.list_jobs()` into the aggregator (P0a fix).

Nothing in this plan creates a second watchdog process or a parallel telemetry pipe. The user's concern about duplication is explicitly addressed: **we close gaps in the existing system, we do not stand up a new one.**

---

## §6 — Verification gates per phase

| Phase | Gate |
|---|---|
| P0a | `curl /api/v1/ops/proactive_health` → `crons` array has ≥20 rows with `last_run_at` populated. |
| P0b | Same endpoint shows invariant findings for `proactive_artifact_digest_delivery` etc. firing OR passing with non-zero observed values (proves they're reading the right DB). |
| P0c | HEARTBEAT.md directive grep shows reference to `proactive_health_latest.json`. Force-trigger a critical via `POST /api/v1/ops/proactive_health/email_test?confirm=true`, observe a Task Hub item or email or both. |
| P1a | Sidecar shows one finding per dead CSI source (expect 4–5 today). |
| P1b | Sidecar shows zero false positives across crons that ran in the last hour, AND finds any that didn't. |
| P2 | YouTube + threads + reddit adapters resume producing events; sidecar findings auto-clear within one heartbeat. |
| P3 | A persistent warn finding (e.g. one of the new CSI source findings before P2 fixes it) escalates within 3 heartbeats — either email or Task Hub row. |

---

## §1.5 — Full system inventory (reference)

| System Name | Type | Definition | Expected Cadence | Output Evidence | Covered? |
|---|---|---|---|---|---|
| morning_briefing | cron | gateway_server.py:19010 | 6:30 AM CDT | artifacts/autonomous-briefings/YYYY-MM-DD/DAILY_BRIEFING.md | ✅ |
| nightly_wiki | cron | gateway_server.py:18989 | 3:15 AM CDT | artifacts/autonomous-wikis/YYYY-MM-DD/NIGHTLY_WIKI.md | ✅ |
| paper_to_podcast | cron | gateway_server.py:18751 | 9:00 PM CDT | artifacts/paper_to_podcast/YYYYMMDD.* | ✅ (broken — P0b) |
| youtube_daily_digest | cron | gateway_server.py:18812 | 6:00 AM CDT | email + task_hub row | ⚠ partial |
| vault_lint_contradictions | cron | gateway_server.py:19028 | 7 AM 1st of month | artifacts/vault-contradictions/ | ✅ |
| hackernews_snapshot | cron | gateway_server.py:19047 | 0,30 min 6–21 CDT | artifacts/hn-snapshots/ | ✅ |
| atlas_direct_dispatch | cron | gateway_server.py:19072 | every 1 min UTC | task_hub rows | ❌ → P1b |
| simone_chat_auto_complete | cron | gateway_server.py:19102 | every 1 min UTC | task_hub status→completed | ❌ → P1b |
| proactive_report_morning | cron | gateway_server.py:19129 | 7:05 AM CDT | proactive-reports + email | ✅ (broken — P0b) |
| proactive_report_midday | cron | gateway_server.py:19143 | 12:05 PM CDT | proactive-reports + email | ✅ (broken — P0b) |
| proactive_report_afternoon | cron | gateway_server.py:19157 | 4:05 PM CDT | proactive-reports + email | ✅ (broken — P0b) |
| proactive_artifact_digest | cron | gateway_server.py:19171 | 8:35 AM CDT | email | ✅ (broken — P0b) |
| csi_convergence_sync | cron | gateway_server.py:19269 | every 30 min UTC | proactive_convergence_events | ✅ |
| csi_demo_triage_rank | cron | gateway_server.py:19335 | 10:05, 15:05 CDT | csi_demo_triage_candidates | ✅ (broken — P0b) |
| claude_code_intel_sync | cron | gateway_server.py:19362 | 8 AM/4 PM/10 PM CDT | claude-code-intel/ + email | ✅ (broken — P0b) |
| codie_proactive_cleanup | cron | gateway_server.py:18527 | 1:30 AM CDT | task_hub + PR | ❌ → P1b |
| vp_mission_pr_reconciler | cron | gateway_server.py:18582 | every 15 min 6–20 CDT | task_hub sync | ❌ → P1b |
| vp_coder_workspace_pruning | cron | gateway_server.py:18614 | 5:05 PM CDT Sundays | workspace dirs purged | ❌ → P1b |
| architecture_canvas_drift | cron | gateway_server.py:18641 | 6:30 AM CDT Mondays | architecture-canvas-drift/ | ❌ → P1b |
| youtube_channel_rss | csi_adapter | adapters/youtube_channel_rss.py | 60s poll | events source=youtube_channel_rss | ✅ (covered, currently red) |
| youtube_playlist | csi_adapter | adapters/youtube_playlist.py | 60s poll | events source=youtube_playlist | ❌ → P1a |
| reddit_discovery | csi_adapter | adapters/reddit_discovery.py | 60s poll | events source=reddit_discovery | ❌ → P1a |
| threads_owned | csi_adapter | adapters/threads_owned.py | 60s poll | events source=threads_owned | ❌ → P1a |
| threads_trends_seeded | csi_adapter | adapters/threads_trends_seeded.py | 60s poll | events source=threads_trends_seeded | ❌ → P1a |
| threads_trends_broad | csi_adapter | adapters/threads_trends_broad.py | 60s poll | events source=threads_trends_broad | ❌ → P1a |
| batch_brief | csi_polling | csi_ingester/service.py:59 | per config | proactive_convergence_events | ❌ defer |
| dedupe_cleanup | csi_polling | csi_ingester/service.py:66 | 3600s | dedupe_store purge | ❌ defer (housekeeping) |
| pr_rebase_watchdog | GHA | .github/workflows/pr-rebase-watchdog.yml | every 15 min UTC | workflow log | ❌ out-of-scope |
| nightly_doc_drift_audit | GHA | .github/workflows/nightly-doc-drift-audit.yml | 18:35 UTC | workflow log | ❌ out-of-scope |
| openclaw_release_sync | GHA | .github/workflows/openclaw-release-sync.yml | Tue/Fri 20:35 UTC | workflow log | ❌ out-of-scope |
| dispatch_sweep | heartbeat | heartbeat_service.py | per heartbeat | task_hub rows dispatched | ⚠ via stale_tasks |
| agentmail_polling | continuous | gateway_server.py:8277 | always-on | inbox polling | ⚠ via email_handler |

---

## §7 — P2 Investigation findings (2026-05-20 19:00 UTC)

After P0a–P1b shipped, diagnosed the three currently-dead adapters via SSH/SQL on prod (no sudo for csi-ingester logs):

**Adapter state (last event UTC):**
| Adapter | Last event | Silence | Cadence config |
|---|---|---|---|
| youtube_channel_rss | 2026-05-18 23:31 | ~43h | Time-gated CT hours [2,6,8,10,12,14,16,18,20], 90 min min interval |
| threads_trends_broad | 2026-05-18 20:47 | ~46h | 1800s poll |
| reddit_discovery | 2026-05-12 06:30 | ~8 days | 28800s poll (3x/day) |
| youtube_playlist | (never seen) | n/a | `enabled: false` in config — deliberate, not a failure |
| threads_owned, threads_trends_seeded | (never seen) | n/a | Config enabled but no recent events; needs investigation post-restart |

**csi-ingester service state:**
- Up since 2026-05-19 02:24 UTC (1d 16h) — never restarted during the silence
- Memory 103 MB, CPU 5min/40h — VERY low utilization (smoking gun: adapters not actually polling)
- 0 dead_letter rows in csi.db (adapters aren't erroring INTO the dead-letter queue)
- 6 csi_poll_errors total over the lifetime (low)
- HTTP /metrics shows polling alive (498 cycles, 3459 events) — but those are aggregate, hackernews is doing the work
- 444 YouTube channels all active in DB; no config drift

**Diagnosis:** the three silent adapters appear to have entered a state where the polling scheduler skips them, but no exception was thrown into dead_letter. Could be:
- (a) Per-adapter loop task died silently; remaining adapters continued
- (b) Adapter-internal time-gate or rate-limit state got confused and stays skipped
- (c) Shared upstream (proxy, rate-limit cache) is broken silently

**Operator action required (blocked on sudo):**
```
sudo systemctl restart csi-ingester
```
`ua` user does NOT have sudo for csi-ingester (only staging-* services). The restart should clear in-memory adapter state and recover three adapters. If silence persists post-restart, deeper investigation needs csi-ingester journal access (add `ua` to `systemd-journal` group, or get sudo for `journalctl -u csi-ingester`).

**Watchdog coverage going forward:** PRs #398 (P1a csi_source_liveness) + #397 (P0c task_hub emission) means this exact failure mode will auto-surface on every heartbeat once deployed:
- csi_source_liveness will fire as `critical` listing the stale sources
- A `proactive_health:invariant:csi_source_liveness` row will park in Task Hub
- The row's metadata will include the runbook command Kevin can paste

So P2's structural fix is shipped via P1a; the *current* dead adapters are an operational restart away.

---

## §8 — P4 (added 2026-05-20 PM): ZAI inference health invariant

Operator surfaced a new concern after P0-P3 shipped: as background processes multiply, we risk ZAI rate-limit / Fair-Use-Policy trips that could throttle or even ban the subscription. The watchdog had zero visibility into this — `ZAIRateLimiter` tracked 429s in singleton memory only, FUP signals weren't classified as a distinct event class, and the heartbeat daemon (subprocess) couldn't see the rate-limiter state at all.

**P4 (PR #402)** closes the gap:

- **Rate limiter** (`rate_limiter.py`): persistent JSON snapshot at `AGENT_RUN_WORKSPACES/zai_inference_state.json` written atomically after every `record_429` / `record_success` / `record_fup_signal`. New `record_fup_signal(context, error_snippet)` distinct from `record_429`. Retry wrapper detects FUP keywords first and bails (no retry — retrying worsens ban risk).
- **New invariant** `zai_inference_health` reads the snapshot + counts UA Python processes via `pgrep`. Strict thresholds per operator direction:
  - 3+ consecutive 429s → CRITICAL
  - ANY FUP event in last 30 min → CRITICAL (sharp window; alert clears once danger cools)
  - `backoff_floor` saturated → CRITICAL
  - UA Python proc count > 30 → WARN
- **Framework change** (`pipeline_invariants.py`): `_build_finding` respects per-finding `severity_override`. Lets one invariant emit critical for dangerous conditions and warn for the lighter process-count condition without splitting into multiple invariants.
- **Watchdog cost**: 1 KB JSON read + 1 `pgrep` call per heartbeat. No AI inference, no DB writes. The watchdog cannot contribute to the load it monitors.

**Follow-up tied to first real FUP observation**: ship a regression test pinning the exact ZAI FUP error text and refine `FUP_KEYWORDS` if needed.

---

## §A — Archived: Original close-out plan (WS1–WS4)

(Kept for traceability. All WS1–WS4 items shipped in PRs #367, #370, #372, #374, #376, #389, #390, #392. The plumbing bugs found in §2 above are gaps in those PRs, not unshipped work.)

| WS | Subject | Status |
|---|---|---|
| WS1 | Email-skip diagnosis + manual test endpoint | ✅ shipped PR #389 |
| WS1.5 | Lifespan re-order (conditional) | deferred — fallback worked |
| WS2 | Investigate 4 live findings | ✅ PRs #390 (briefing/wiki probe fixes), YouTube backfill confirmed real |
| WS3 | 5 new invariants | ✅ shipped PR #392 (but reading wrong DB — see §2 P0b) |
| WS4 | Doc refresh | ✅ shipped in PR #392 |

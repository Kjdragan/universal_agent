# UA Scheduling/Health Overhaul — Operator Assessment

**Question scope:** 2026-06-04→06 "Scheduling/Health Overhaul" records. Verified against clean `origin/main` (`universal_agent-wt-truth`), live prod gateway (`uaonvps:8002`), and git history. Four independent facet investigations (S-track, Phase-track, doc-accuracy, live-prod) cross-corroborate the findings below.

---

## 1. Are these documents hallucinated or accurate?

**Verdict: ACCURATE. Not hallucinated.** The stale-checkout failure mode that broke the prior inventory does **not** recur here — every load-bearing claim was checked against `origin/main` + live prod.

The weight of evidence:

- **Every PR cited landed on `origin/main`.** All of #726–#766 (and the S-track #737/#738/#739/#740/#741/#743/#744/#745) confirmed via `git log --grep="(#NNN)" origin/main`. The merged code matches the handoff prescriptions on a **per-symbol** basis (not just per-PR).
- **Live prod corroborates the structural claims.** `/api/v1/cron/jobs`, `/version`, `/dashboard/mission-control/*` all confirm the as-built state independently of the docs.
- **The numbers are measured, not invented.** The 17–49% fire-loss range traces to real runtime counts (simone 5967/~7200 = 17% lost; atlas 3692/~7200 = 49%). The "18 orphaned briefs" is corroborated in the #756 commit body + backfill worklog (103→122 rows). The "5/7 false alarms" maps to real monitoring-bug fix PRs (#726/#730/#731/#735).

**Per-source reliability ranking:**

| Document | Verdict | Notes |
|---|---|---|
| `HOLISTIC_REVIEW_findings.html` | **Most trustworthy / most current** | Self-correcting; honestly flags its own drift (ADR staleness L3, map drift). Trust this over the others when they conflict. |
| S1–S5 + Phase handoffs (`handoffs/*.md`) | **Accurate** | Per-symbol verified. Notably document their own premise corrections during execution. |
| `08_scheduling_substrate_adr.md` | **Accurate, now `active`** | Was design-only/`draft` at #738; current `origin/main` shows it upgraded to `status: active` with "As-built" notes after Phases A–D landed. |
| `UA_overhaul_project_record.html` | **Accurate but STALE** | Point-in-time 06-05 snapshot. Under-reports progress (see §3). |
| `scheduling_substrate_map.html` (section body A–F) | **Historical snapshot** | Pre-migration state by design; only the "AS-BUILT UPDATE 2026-06-06" banner is meant as current, and even that lags batch3/4. |

**No genuine hallucination or inflation was found.** The only inaccuracies are (a) **staleness** from mid-flight snapshotting in a repo that deploys ~19×/day, and (b) trivial **PR-number drift** in one doc's "~20 PRs" round-down (actual ledger is ~24–30). Both are categorically different from fabrication. Notably, the docs **under-report** completed work rather than over-claiming it — the opposite of the hallucination risk Kevin worried about.

---

## 2. What the revised project was actually building (authoritative as-built)

A five-part "get the scheduling house in order" effort. The core problem: UA had **5 distinct schedulers** (asyncio heartbeat, in-process gateway cron, Mission Control sweeper, systemd timers, OS crontab), and the in-process gateway cron was **lossy** — it dropped 17–49% of scheduled fires because the gateway restarts ~19×/day on deploy and in-process timers don't survive restarts. Monitoring was also lying (5/7 Mission Control alarms were false-positive monitoring bugs, not real outages).

**As-built today (`origin/main` HEAD `0e35152a`, deployed in prod):**

**S-track (the foundation fixes):**
- **S1 (#739/#740/#741)** — Restored outbound email. Killed the dead `MailService`/`_DummyMail` import, routed through `AgentMailService()` + `startup()`, guarded `email_sent` on a real `message_id`, turned on the AgentMail→Gmail 429 fallback in the **deploy bootstrap** (durable, not a VPS-only `.env` edit), and operator-locked the haiku tier to `glm-4.5-air`.
- **S2 (#737)** — Repaired OS-level systemd timers. Re-armed the dead watchdog/oom timers (both were `NextElapse=infinity` — watchdog dark since 04-11, oom since 05-16) with `OnCalendar`+`Persistent=true`, wired their installers into `remote_deploy.sh`, fixed two always-failing CSI units.
- **S3 (#743/#744)** — Unfroze the Mission Control Chief-of-Staff readout by decoupling last-**FIRE** from last-**ATTEMPT** (`advance_fire_ts` param), and stopped `__tierN_meta__` sentinel rows leaking into tier-1 evidence.
- **S4 (#745)** — Deleted scheduling dead weight: deregistered the `hourly_insight_email` cron (function definition removed entirely), purged orphan cron rows, fixed `feature/latest2`→`origin/main` stale branch defaults, dropped the deprecated `UA_HEARTBEAT_EVERY` fallback. Verified live: orphan dup + `hourly_insight_email` are absent from `/api/v1/cron/jobs`.
- **S5 (#738)** — The architectural anchor ADR (`08_scheduling_substrate_adr.md`), design-only at first, then honored by the Phase A–D implementation wave.

**Phase-track (the S5 ADR, implemented):**
- **Phase A (#753/#754/#755/#759/#762)** — Migrated **20 deterministic/content/secret-bearing crons** off the lossy in-process gateway onto deploy-independent `OnCalendar`+`Persistent` systemd timers. Gated by `systemd_migrated_jobs.py::SYSTEMD_MIGRATED_SYSTEM_JOBS` (the 20-job frozenset) which forces the in-process row `enabled=False` so the timer is the **sole firer** — no double-fire. Global rollback via `UA_SYSTEMD_TIMER_MIGRATION_DISABLED=1`.
- **Phase B (#749)** — Moved the Mission Control sweeper out of the gateway lifespan into its own long-lived systemd service (`mission_control_sweeper_main.py` / `universal-agent-mission-control-sweeper.service`) for process isolation.
- **Phase C (#750/#751)** — Moved the ~19 `proactive_health` invariant probes off the skip-prone heartbeat tick onto a deploy-independent systemd timer (`proactive_health_timer_main.py`, `OnCalendar=*:0/10`) that writes a durable snapshot + emails an operator digest. The heartbeat now **reads** the snapshot instead of computing it. Added a sweeper-liveness invariant.
- **Phase D (#756)** — Root-caused **18 intel briefs (2026-06-04→05) forked into orphan cwd-relative DBs** because an LLM followed a **placeholder DB path** in `evaluate-and-author-intel-brief/SKILL.md`. Repointed the skill (and other hardcoded paths) to canonical `__file__`-based resolvers (`durable/db.py::get_activity_db_path`, `connect_runtime_db`).
- **FU1 (#747/#748)** — Routed the CSI Threads token Infisical sync-back through `infisical_loader.upsert_infisical_secret` instead of the uninstalled raw `infisical_client` SDK.

**Monitoring now tells the truth** (the headline goal). Live `/dashboard/mission-control/tiles`: **9/9 GREEN, 0 false alarms**. The old gateway/heartbeat "silent/RED" critical cards are **retired** (retired_count 278). Only 3 live cards, all `informational`. `/api/v1/health` = `healthy`, db `connected`, profile `vps`.

**Live structural proof:** `/api/v1/cron/jobs` = 29 jobs, 6 enabled / 23 disabled. The 23 disabled = exactly the 20 migration-registry jobs (disabled **because** migrated → running on timers) + 3 disabled for unrelated reasons (`hackernews_snapshot` operator-off via #734, `freelance_scout`, `claude_code_intel_sync`). The 6 still-enabled in-process rows are the minute control-plane loops (atlas/simone) + a few that stay in-process by design.

---

## 3. Drift / open items

**Drift = staleness only, all in `origin/main`'s favor (forward progress beyond the docs):**

- **`UA_overhaul_project_record.html` and `scheduling_substrate_map.html` say Phase A batches 3+4 are "queued / still in-process (lossy)."** Ground truth: **#759 (batch3) and #762 (batch4, 7 secret-bearing jobs) both landed** after those docs were authored. All 20 jobs are migrated, not just 11. The docs are pinned to intermediate HEAD `e7014bba` (#756); prod is at `0e35152a` (#773).
- **ADR/holistic-review cite "11 migrated rows / 15 enabled UA timers / 26 UA+CSI timers."** Live shows 20 migrated. Same cause — grounded at the pre-A3/A4 commit.
- **Three handoff PREMISES were proven wrong and self-corrected during execution** (a sign of real verification, not invention), documented in the #756 commit body: (a) "reports 3→2 / midday dropped" — operator **kept all 3**, midday is migrated and live; (b) "`UA_ARTIFACTS_DIR` literal-string fallback is an active writer" — code was already correct, the on-disk dir is stale leftover; (c) "#757 hackernews-park" — premise was wrong, **reverted by #765**.

**Genuinely open / deferred (operator-gated, NOT bugs):**

- **Orphan-DB cleanup + recovery of the 18 briefs** was **deferred** in #756 — only the root-cause writer fix shipped. Cleanup requires a write to the fenced-off 2.5GB live `activity_state.db`; orphans are snapshotted at `/home/ua/phaseD_orphan_snapshots_2026-06-05/`. (The doc-accuracy facet notes the holistic worklog records a backfill of 19 briefs into canonical 103→122 — confirm whether this fully closed the recovery or whether it's still pending.)
- `feature/latest2` references **still exist** in `services/dependency_upgrade.py` — outside S4's scope, so not an S4 miss, but a lingering stale-default the next sweep could clean.

**Could NOT independently verify (mark unverified, not wrong):** exact live runtime values asserted in the holistic review — `timers-at-infinity=0`, sweeper PID 2288450, SES `message_id`, journal `loaded=286 profile=vps`, and the "~19 deploys/day" count. The prod read-API doesn't expose `systemctl`/`journalctl`/SES, and `/api/v1/ops/*` is 401. These rest on the doc's own ssh-probe evidence, which is internally consistent.

---

## 4. Reconciliation with your Task Type Registry (PR #772)

**Yes — #772 should defer to the canonical ADR + scheduling map, not stand up a second source of truth.** The reasons are concrete:

- The **`systemd_migrated_jobs.py::SYSTEMD_MIGRATED_SYSTEM_JOBS` frozenset is already the machine-readable source of truth** for which jobs run on timers vs. in-process. It was deliberately extracted from `gateway_server.py` (2026-06-06, Phase A follow-up) precisely so non-gateway surfaces could query migration status. #766 already made the Chief-of-Staff readout migration-aware by consuming it. Any task-type registry should **reference that frozenset + the ADR**, not re-enumerate jobs (re-enumeration is exactly how the docs drifted within a day).
- The ADR (`08_scheduling_substrate_adr.md`, now `status: active`) is the **architectural** source of truth for the substrate model (5 schedulers, what belongs on timers vs. minute-loops in-process). A task-type taxonomy is a **different cut** of the same system and should cite the ADR for substrate facts rather than restate them.
- The recurring lesson across all four facets: **anything that hardcodes a job list or substrate count goes stale in this repo within hours.** #772 should encode *pointers* (frozenset, ADR, live `/api/v1/cron/jobs`) and only own the *task-type semantics* that aren't expressed elsewhere.

**I did not independently read PR #772's contents** in this investigation — recommend a quick diff-check that it consumes the frozenset/ADR rather than duplicating the job inventory before merge.

---

## 5. Recommendation

**Nothing to fix in the overhaul itself — it landed, holds in prod, and the records are accurate.** Confirmed, not hallucinated. Concrete next steps, in priority order:

1. **Refresh the two stale snapshots** (low effort, removes the only real drift): update `UA_overhaul_project_record.html` and the `scheduling_substrate_map.html` banner to reflect batch3/4 done (20 jobs migrated, not 11) and HN un-parked. Or simply add a one-line "SUPERSEDED — see HOLISTIC_REVIEW_findings.html" pointer at the top of each and treat the holistic review as canonical.
2. **Reconcile PR #772 against the ADR + frozenset before merge** — verify it references `systemd_migrated_jobs.py` + `08_scheduling_substrate_adr.md` rather than creating a parallel job inventory. This is the one place a second source of truth could creep in.
3. **Close the deferred PART-2 item:** confirm whether the 18-brief recovery is fully done (worklog says 103→122 backfilled) or still pending, and whether the orphan-DB files at `/home/ua/phaseD_orphan_snapshots_2026-06-05/` should now be cleaned up (operator-gated destructive step — your call).
4. **Optional:** sweep the lingering `feature/latest2` reference in `dependency_upgrade.py`.

Files of record for the authoritative state: `/home/kjdragan/lrepos/universal_agent-wt-truth/src/universal_agent/systemd_migrated_jobs.py`, `/home/kjdragan/lrepos/universal_agent-wt-truth/project_docs/06_platform/08_scheduling_substrate_adr.md`, `/home/kjdragan/health_system_report_2026-06-04/HOLISTIC_REVIEW_findings.html`, and live `http://uaonvps:8002/api/v1/cron/jobs`.
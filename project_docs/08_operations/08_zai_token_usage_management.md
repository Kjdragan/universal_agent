---
title: ZAI Token Usage Management
status: active
canonical: true
subsystem: ops-zai-token-management
code_paths:
  - src/universal_agent/services/zai_observability.py
  - src/universal_agent/services/principal_token_tracking.py
  - src/universal_agent/services/cody_token_tracking.py
  - src/universal_agent/services/token_consolidation.py
  - src/universal_agent/services/zai_status.py
  - src/universal_agent/services/zai_control.py
  - src/universal_agent/services/zai_weekly_budget.py
  - src/universal_agent/services/invariants/zai_inference_health.py
  - src/universal_agent/rate_limiter.py
  - src/universal_agent/scripts/zai_token_report.py
last_verified: 2026-08-08
---

# ZAI Token Usage Management

**Purpose.** The operator-facing guide to *who burns the ZAI weekly token budget and what to do
about it*. This doc distills the 2026-07-18 token-usage analysis (run after the first-ever
**weekly-limit exhaustion**, error code **1310**, which took the stack down 2026-07-17 →
2026-07-18) and tracks the remediation recommendations. The rate limiter's *mechanism* is owned
by [`06_platform/10_zai_rate_limiter.md`](../06_platform/10_zai_rate_limiter.md); per-call-site
model tiers by [`04_intelligence/14_model_tiering_by_process.md`](../04_intelligence/14_model_tiering_by_process.md).
This doc owns the **budget view**: measurement lanes, how to re-run the analysis, and the
recommendation tracker.

**Full rendered analysis (read this when returning to the effort):**
- Live exhibit: <https://uaonvps.taildcc090.ts.net/scratch/zai-token-usage-analysis/zai_token_usage_analysis.html>
- Archived copy (git-tracked): `scratch_archive/2026-07-18/133737__zai-token-usage-analysis__zai_token_usage_analysis.html`

## 1. The four measurement lanes (all production-active)

| Lane | Code | Data lands in | Covers |
|---|---|---|---|
| Direct httpx calls to `api.z.ai` | `services/zai_observability.py::install_zai_observability` | `AGENT_RUN_WORKSPACES/zai_inference_events.jsonl` | Every direct API caller (mission-control, classifiers, digests) with caller, model, status, per-call tokens |
| In-process SDK principals | `services/principal_token_tracking.py::record_session_token_usage` | `AGENT_RUN_WORKSPACES/activity_state.db` table `token_usage_events` | Simone heartbeat, VP, vp-coder, interactive turns (per-turn deltas incl. cache reads) |
| CLI subprocess missions | `services/cody_token_tracking.py::record_token_usage` | `activity_state.db` table `cody_token_usage` | Cody `claude --print` missions, split `cody_mode` zai/anthropic |
| CSI ingester (external writer) | read via `services/token_consolidation.py::read_csi_token_usage` | `csi.db` table `token_usage` | CSI's own inference |

Consolidated view: `services/zai_status.py::build_token_usage` (gateway `GET /api/v1/ops/zai/token-usage`,
rendered on the ZAI-Control dashboard) and the terminal report
`python -m universal_agent.scripts.zai_token_report --hours N` (httpx lane only).

**Accounting basis — state it whenever you quote a token number from here.** The consolidated
total (and the R3 weekly meter built on it) is **cache-INCLUSIVE across all four lanes**:
`input + output + cache_creation + cache_read`. A single-lane, cache-exclusive query — the
tempting `SELECT SUM(input_tokens + output_tokens) FROM token_usage_events` — reports roughly
**10%** of the same mass and is a different quantity, not a contradiction (measured 2026-07-24:
37.1M vs 343.6M over identical rows, plus ~10% more from the csi.db and httpx lanes). Quoting the
two side by side without saying which is which is how the 2026-07-24 "the meter is overcounting
9.4×" false alarm started (§4.1).

**Known blind spots:** Gemini call sites (auto-investigator, embeddings) are invisible to every
lane; non-gateway `process_turn` paths are uncaptured; the in-memory governors
(`capacity_governor`, `session_budget`) are gates, not ledgers.

## 2. What the exhausted week measured (2026-07-11 → 07-17)

~**489M observable ZAI tokens**: ~127M input+output plus ~**362M cache-read** (74% of
everything). Consumers, ranked:

| # | Consumer | Week (M) | Driver |
|---|---|---|---|
| 1 | VP missions (glm-5.2) | 197 | 101 turns × ~1.7M cache-read each |
| 2 | Simone heartbeats (glm-5.2) | 145 | 116 turns × ~1.1M cache-read (`memory/HEARTBEAT.md` + briefing context re-read per turn) |
| 3 | `mission_control_chief_of_staff.py::synthesize_readout` (glm-4.7) | 47 | ~89 calls/day × ~104k tokens, continuous sweeper, viewership-independent |
| 4 | `mission_control_tier1.py::discover_tier1_cards` (glm-4.7) | 30 | ~36 calls/day × ~149k evidence payload |
| 5 | vp-coder in-process (glm-5.2) | 59 | 14 heavy turns |
| 6 | Everything else combined | ~11 | hourly crons/classifiers/digests — noise |

Key structural finding: **429 pressure and token burn have different owners** — the Discord
relevance filter and `llm_classifier` cause most 429s but near-zero spend; the principals'
cache reads dominate spend. And the sharpest defect: `rate_limiter.py::FUP_KEYWORDS` matches
`"weekly limit"` and `"1313"` but the real weekly-exhaustion body says
`[1310][Weekly/Monthly Limit Exhausted…]` — matched by neither — so the stack retried into a
dead account for ~2 days until manually paused (the global pause lever in
`services/zai_control.py::set_global_pause` existed the whole time but nothing pulled it).

## 3. Re-running the analysis (read-only, on the VPS)

```bash
# Per-principal totals for a window (SDK lane):
sqlite3 "file:/opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db?mode=ro" \
  "SELECT source, principal, model, COUNT(*), SUM(input_tokens), SUM(output_tokens),
          SUM(cache_read_input_tokens)
   FROM token_usage_events WHERE recorded_at >= '2026-07-11'
   GROUP BY source, principal, model;"

# Direct-caller totals (httpx lane): aggregate zai_inference_events.jsonl by caller/caller_fn,
# summing input_tokens/output_tokens; filter on ts epoch bounds.

# Terminal quick view:
cd /opt/universal_agent && PYTHONPATH=src .venv/bin/python -m universal_agent.scripts.zai_token_report --hours 168
```

## 4. Recommendation tracker

Full rationale for each item lives in the exhibit. Status values: `todo`, `in-progress`,
`shipped (PR #n)`, `deferred`.

| ID | Recommendation | Status (2026-07-18) |
|---|---|---|
| R1 | Recognize error code 1310 as *weekly exhaustion* (not FUP, not gradient-429): stop retry ladders, auto-set a pause-only global pause (no tier preset) with TTL parsed from the reset timestamp in the error body, alert once | **shipped (PR #1448, deployed `046833a8`)** (mechanism detail in [`06_platform/10_zai_rate_limiter.md` §9.6](../06_platform/10_zai_rate_limiter.md#96-the-1310-weeklymonthly-quota-exhaustion-auto-pause-r1-2026-07-18)) |
| R2 | Mission-Control intelligence: delta-gate (stable evidence signature) + ~60-min readout floor | **shipped (PR #1449, deployed)** |
| R3 | Self-calibrating weekly budget meter over the four lanes: week-to-date rollup, observed-cap learned from each 1310 sighting (no fixed cap number needed), dashboard tile, auto-escalate `zai_control` levels at % thresholds | **shipped (PR #1451, deployed; meter live — see [`06_platform/10_zai_rate_limiter.md` §9.7](../06_platform/10_zai_rate_limiter.md))**, **amended 2026-07-24** (see §4.1) |
| R4 | Context diet for principals (conservative): slim `memory/HEARTBEAT.md` (45.8KB → 19.4KB core + `memory/reference/*.md` lazy-loaded sections) and activate the existing-but-dead task-focused lean tick behind `UA_HEARTBEAT_TASK_FOCUSED` (default on). **`force_complex` deliberately left untouched** — investigation found `metadata["source"]="heartbeat"` (unconditional) already forces `ROUTE_SYSTEM` via the Tier-1 env-signal heuristic before `force_complex` is ever consulted, so it was near-inert; touching it would have been a wasted-effort risk, not a savings lever. VP prompt boilerplate audit found `_build_cli_prompt` itself is small (~0.5-1.5KB) — the real per-mission constant cost is suspected to be `CLAUDE.md`/`.claude/agents`/skill-catalog reload on each `claude --print` subprocess spawn, unconfirmed and deferred to an R4b follow-up (measure first). | **shipped (PR #1450, deployed; task-focused tick is a wired no-op until dispatch claims are threaded back — see `03_agents/03_heartbeat_service.md`)** |
| R5 | Thinking hygiene on glm-5.2 call sites (stale "5.1 has no thinking" comments; `/goal` evaluator per-turn thinking; cap or disable + measure) | todo |
| R6 | De-cluster hourly jobs (`:00` pile-up), batch the Discord relevance filter, lower retry ceilings for sub-1k-token classifiers | todo |

Open questions (answers refine R2–R4 priorities): does the weekly cap count cache-read tokens
and at what weight; what is the actual cap (the meter learns it from 1310 sightings); do
thinking tokens bill beyond reported output.

### 4.1 R3 amendment — the shipped seed cap was falsified and had to stop moving levers (2026-07-24)

R3 shipped with `zai_weekly_budget.py::DEFAULT_SEED_CAP_TOKENS = 400_000_000`, documented as "the
2026-07 exhausted-week estimate", and with the design claim that no fixed cap number is hardcoded
because a real 1310 recalibrates it. In practice **the only calibration trigger was the exact
event the meter exists to prevent**, no 1310 occurred after the meter shipped (no
`weekly_exhaustion` stamp has ever been written to the control file), so `calibrated_from` sat on
`"seed_estimate"` for the meter's entire life. Consumption on the meter's own basis is
structurally 2–4× that seed, so the meter hit ≥95% every week — and on 2026-07-24 07:30:21 UTC it
auto-applied `zai_control` **level 3** ("cheap-only": every tier serialized to concurrency 1,
opus + mid hard-stopped) in live production.

What the 2026-07-24 investigation established (all read-only against prod):

- **The meter is not overcounting.** Its persisted `week_to_date_tokens` was reconstructed from
  the raw lanes to the exact token. Zero duplicate rows; zero 429/retry events in the window.
  The reported "9.4× disagreement with the ledger" was ~9.26× **cache accounting** (the meter is
  cache-INCLUSIVE; the "ledger" figure was `SUM(input_tokens + output_tokens)` over the *same*
  rows, dropping the cache_read that is 89% of the mass) × ~1.10× **lane scope** (the meter also
  sums the csi.db and httpx-JSONL lanes). Both numbers were right; nothing labelled the basis.
  Both are now carried side by side — see §1 and the `accounting_basis` /
  `week_to_date_tokens_excl_cache` fields.
- **The 400M seed is falsified by the meter's own history.** On the meter's exact basis, the four
  anchored weeks before the one real 1310 ran **1.74B / 1.17B / 931M / 911M** tokens with no
  quota wall, while the week that DID trip 1310 (2026-07-11 → 07-18) was the second-smallest at
  **431M** — 2.3×–4.3× *below* weeks that passed clean. Note §2 above puts that same exhausted
  week at ~489M on a partly different basis; neither figure matches the 400M the code claimed to
  derive from it.

Changes (see [`06_platform/10_zai_rate_limiter.md` §9.7](../06_platform/10_zai_rate_limiter.md)
for the mechanism):

1. **Cap ladder**: real 1310 (`1310@<iso>`) > `zai_weekly_budget.py::derive_cap_from_history`
   (`history_max@<k>w` — rolling max of the last 4 complete weeks, floored at 400M, needs ≥2
   weeks) > `seed_estimate` fallback (raised to 1.0B, the order of magnitude of a normal heavy
   week on this basis).
2. **An uncalibrated seed never throttles and never alerts.** `maybe_escalate` refuses,
   `zai_inference_health` suppresses `weekly_budget_high`/`weekly_budget_critical`, and a level
   already applied off a stale guess self-releases on the next meter pass.
3. **Both accounting bases surfaced** in the snapshot, the invariant `observed_value`, and the
   alert headlines.

**Operational consequence — a ~2-week ramp with no LEADING signal.** `zai_weekly_budget_state`
held exactly one row (the current week) as of 2026-07-24, so the meter needs two complete weeks
before `derive_cap_from_history` can return anything; until then the cap stays on
`seed_estimate` and the meter neither escalates nor alerts. That is deliberate — it is strictly
better than the standing false alarm it replaces — but it means R1
(`zai_control.py::handle_weekly_exhaustion`, the reactive 1310 pause) is the ONLY protection in
that window, and R1's own detection has never fired in production. If the 1310 keyword path is
ever found not to match the real `[1310][Weekly/Monthly Limit Exhausted…]` body, fix that first.

**Read a history-derived cap as a burn-rate anomaly detector ("this week is heavier than any of
the last four"), NOT as a distance-to-wall gauge.** We cannot read ZAI's quota accounting, and the
1310 body says `Weekly/Monthly Limit Exhausted` without distinguishing the two — a small week
tripping after four large ones is consistent with a MONTHLY rollover, which would mean the
week-anchored model measures the wrong period entirely. Still-open, now sharper: was the
2026-07-17 wall weekly or monthly; is the plan denominated in tokens at all or in prompts/requests
(the lanes have the request counts too: 207 turns / 2,849 inner iterations / 2,220 httpx calls in
the sample week); and does the quota count cache_read (which moves true consumption ~9×).

Known-but-unfixed at the same site, deliberately out of scope of the above:
`token_consolidation.py::analyze_cody_token_usage` groups by `cody_mode` but sums **all** rows
into `totals` with no `WHERE cody_mode = 'zai'`, so Anthropic-Max-billed subprocess tokens
(1,044,624,479 historically, all before 2026-06-07) would count against the ZAI budget. Zero
impact today — that lane has had no rows at all since 2026-07-10T22:15 — but the lane's silence
is itself unexplained and worth a look before it starts writing again.

## 5. Operating rules of thumb

- **Cache-read mass is the budget.** Context size × turn count dwarfs model choice. Before
  adding any recurring principal work, estimate its cache-read per turn, not just its output.
- **New recurring LLM consumers must land in a lane.** If a new process calls ZAI outside the
  patched httpx client or the SDK adapters, its spend is invisible — wire it through an
  existing lane or extend one in the same PR.
- **When a 1310 appears**, the week is over — do not retry, do not restart services to "fix"
  it. R1 now auto-detects this and trips a pause-only global pause (no tier preset) with a TTL
  parsed from the reset timestamp (Beijing time, UTC+8), gating both the httpx-hook lane and
  VP/Simone dispatch (see
  [`06_platform/10_zai_rate_limiter.md` §9.6](../06_platform/10_zai_rate_limiter.md#96-the-1310-weeklymonthly-quota-exhaustion-auto-pause-r1-2026-07-18)).
  It self-clears at the reset — no manual dashboard pause needed unless the auto-pause's
  fallback TTL undershot the real reset.

## Cadence map & per-element pacing knobs (2026-08-08)

The 2026-08-08 efficiency changes slowed exactly ONE thing: the heartbeat's
**idle-ideation pivot** (1h → 2h minimum interval). Everything work-shaped
kept its cadence, and every element has its own independent knob — pacing is
per-element by construction, never one global dial:

| Element | Cadence in prod | Knob | Changed 2026-08-08? |
|---|---|---|---|
| Task Hub EXECUTION (todo daemon) | ~60s poll + immediate nudge wake — deliberately decoupled from the heartbeat (`idle_dispatch_loop.py`) | `UA_IDLE_POLL_INTERVAL_SECONDS` (60), `UA_TODO_DISPATCH_MAX_PER_SWEEP` | no |
| Heartbeat tick (supervision/triage) | **10 min** — Infisical sets `UA_HEARTBEAT_INTERVAL=10m` (the code default 30m is NOT what runs; an on-disk grep won't find this) | `UA_HEARTBEAT_INTERVAL` | no |
| Actionable heartbeat turns (task claims, system events, exec completions, pending operator questions, demo reviews) | next tick, ≤10 min — guard policy NEVER idle-skips these wake signals | (same tick knob) | no — model tier only (sonnet) |
| Idle ideation (brainstorm proposals for the morning report) | ≥2h jittered, Houston 06:00–22:00 window, daily budgets | `UA_PROACTIVE_IDEATION_MIN_INTERVAL_SECONDS` (7200), `UA_PROACTIVE_IDEATION_TICK_BUDGET` (20), `UA_PROACTIVE_DAILY_BUDGET` (10) | **yes: 3600 → 7200 default** |
| Mission Control sweeper | 60s tick, delta-gated (LLM only on real state change) | `UA_MISSION_CONTROL_TIER1_FLOOR_S`/`_CEILING_S` | no |
| Heartbeat daemon MODEL | sonnet (`glm-5-turbo`) | `UA_HEARTBEAT_MODEL_TIER` (revert to `opus` without a deploy) | **yes: opus → sonnet** |

If a specific flow ever needs faster idle attention than 2h, the correct move
is that flow's own knob (or promoting its signal into the guard policy's
wake set) — never lowering the global ideation interval back for everyone.

## Trend history (the "is it actually improving?" ledger)

`universal-agent-zai-usage-report.timer` (Wed 09:00 CT) emails the weekly
5-lane snapshot AND appends its aggregates to
`AGENT_RUN_WORKSPACES/zai_usage_history.jsonl` (env
`UA_ZAI_USAGE_HISTORY_PATH`; survives deploys). One JSON row per run:
totals (cache-inclusive + fresh), top-12 flows, lane health. The report
renders the stored trend; the `/dragan:zai-usage-audit` skill reads the same
file first and appends its own `kind:"audit"` row with findings, so every
assessment builds on recorded history instead of memory.

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
  - src/universal_agent/rate_limiter.py
  - src/universal_agent/scripts/zai_token_report.py
last_verified: 2026-07-18
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
| R2 | Mission-Control intelligence: delta-gate (stable evidence signature) + ~60-min readout floor | **built (branch `claude/zai-r2-mc-delta-gate`, PR pending)** |
| R3 | Self-calibrating weekly budget meter over the four lanes: week-to-date rollup, observed-cap learned from each 1310 sighting (no fixed cap number needed), dashboard tile, auto-escalate `zai_control` levels at % thresholds | **in-progress** |
| R4 | Context diet for principals (conservative): slim `memory/HEARTBEAT.md` (45.8KB → 19.4KB core + `memory/reference/*.md` lazy-loaded sections) and activate the existing-but-dead task-focused lean tick behind `UA_HEARTBEAT_TASK_FOCUSED` (default on). **`force_complex` deliberately left untouched** — investigation found `metadata["source"]="heartbeat"` (unconditional) already forces `ROUTE_SYSTEM` via the Tier-1 env-signal heuristic before `force_complex` is ever consulted, so it was near-inert; touching it would have been a wasted-effort risk, not a savings lever. VP prompt boilerplate audit found `_build_cli_prompt` itself is small (~0.5-1.5KB) — the real per-mission constant cost is suspected to be `CLAUDE.md`/`.claude/agents`/skill-catalog reload on each `claude --print` subprocess spawn, unconfirmed and deferred to an R4b follow-up (measure first). | **built (branch `claude/zai-r4-context-diet`, PR pending)** — note: `_is_task_focused` still always resolves `False` in production because `task_hub_claimed` is hardcoded `[]` at the `_run_heartbeat` call site since the 2026-05-23 dispatch move to `todo_dispatch_service`; the activation is correct and unit-tested but a no-op until claims are threaded through again |
| R5 | Thinking hygiene on glm-5.2 call sites (stale "5.1 has no thinking" comments; `/goal` evaluator per-turn thinking; cap or disable + measure) | todo |
| R6 | De-cluster hourly jobs (`:00` pile-up), batch the Discord relevance filter, lower retry ceilings for sub-1k-token classifiers | todo |

Open questions (answers refine R2–R4 priorities): does the weekly cap count cache-read tokens
and at what weight; what is the actual cap (the meter learns it from 1310 sightings); do
thinking tokens bill beyond reported output.

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

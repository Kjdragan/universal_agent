# CSI V3 Backfill Restart — Handoff Context (2026-05-08)

> **Purpose:** Self-contained handoff for a new Claude Code conversation that
> picks up the Twitter / ClaudeDevs intelligence backfill work where the
> 2026-05-08 session left it. Paste this entire file as the opening prompt and
> the new session has everything it needs without referencing prior chat.
>
> **What "V3" means here:** V2 is the architecture rebuild that shipped through
> Phases A–F (LLM-driven entity extraction replacing the regex extractor;
> Memex CREATE/EXTEND/REVISE primitives; vault-as-canonical-product; etc.).
> V3 = V2 + a self-imposed rate-limiting / pacing layer added in this session
> after observed Z.AI proxy slowdowns during sustained-burst LLM calls. The
> entity-extraction pipeline itself is unchanged; what's new is the wrapper
> that paces calls through it.

---

## 1. Where things stand right now (deployed state)

**Production main as of this handoff: `153109ed`** (deployed 2026-05-08 evening, after the launcher/Cody/ship-hardening work landed via PRs #170–#173). The original draft of this doc cited `9d303438` — that was main earlier on 2026-05-08. The newer commits since then (interactive-claude `ANTHROPIC_*` exclusion, Cody subprocess scrub, /ship hardening, doc cleanups) are **unrelated to V2/V3 substance** — they don't touch any CSI / claudedevs intel code. V2's Phase A–F shipped state on main is unchanged. Current branch on the VPS dev tree (`/home/ua/dev/universal_agent`) is `feature/latest2`, in sync with main.

What's already shipped and live:

- **Phase F LLM-driven extraction** — `services/claude_code_intel_replay.py` calls `analyze_action()` and `apply_vault_delta_to_vault()`. Regex extractor deleted.
- **Phase D validation passed** — 50 LLM calls, 0 junk output, perfect canonical-name stability across packets.
- **Phase F production smoke** — at least one real backfill run completed end-to-end producing real vault entities at the expected paths.
- **CSI cron infrastructure** — `csi_convergence` (every 30 min) and `claude_code_intel` (08/16/22 CDT) both registered and active.
- **All today's docs and canonical guidance** — `docs/operations/2026-05-08_zai_peak_time_scheduling.md`, the MCP-credentials section in `docs/deployment/secrets_and_environments.md`, etc.

**What's NOT shipped (parked on a branch):** the pacing / rate-limiting module itself. Two access paths to the same code, pick whichever works for your environment:

| Access | Location | Use when |
|---|---|---|
| **Branch on origin** | `claude/csi-llm-pacing` (tip = `de0ebbe5`, pushed 2026-05-08) | You want a clean checkout from anywhere: `git fetch && git checkout claude/csi-llm-pacing` |
| **Existing worktree** | `/tmp/ua-wt-csi-pacing/` on the VPS | You want the same checkout the prior session used; the worktree's HEAD is the same `de0ebbe5` |

Files in this branch (4 changes, 634 insertions):

- `src/universal_agent/services/csi_llm_pacing.py`            (NEW, ~280 lines)
- `src/universal_agent/services/csi_url_judge.py`             (modified — `paced_llm_call` wrap)
- `src/universal_agent/services/csi_intelligence_pass.py`     (modified — `paced_llm_call` wrap)
- `tests/unit/test_csi_llm_pacing.py`                         (NEW, 15 tests, all passing in isolation)

The branch has not been merged into `feature/latest2`. **A new session should review the branch, decide whether to merge as-is or revise, and only then run the backfill.** See §4 for the merge decision criteria.

---

## 2. Why we built the pacing module — the throttling discovery

**Symptom observed during Phase G v2 vault validation backfill (2026-05-07):** sustained-burst LLM calls through the Z.AI proxy (`api.z.ai/api/anthropic`) slowed to ~20 calls per minute punctuated by 1.5–3 minute stalls. The pattern repeated across hundreds of calls and was operationally indistinguishable from rate-limiting.

**Root cause (only fully understood late in the session):** Z.AI is capacity-limited during Greater-China peak hours (Beijing UTC+8 daytime). Under our `America/Chicago` cron timezone, **US night maps to China business-day peak** — the inverse of the "run heavy batch overnight" intuition. Phase G fired in the middle of US night = middle of China business afternoon = peak load on Z.AI's infrastructure.

The full finding, current cron audit (9 of 12 system crons fire during China peak), and target off-peak windows (US 12:00–17:00 CDT = China deep-night) are documented in [`docs/operations/2026-05-08_zai_peak_time_scheduling.md`](../operations/2026-05-08_zai_peak_time_scheduling.md). **This is required reading before scheduling any new heavy LLM-bound run.**

**Strategic implication for V3:**

1. **Primary fix is scheduling**, not pacing. If we run the backfill during US lunch/afternoon (China deep-night off-peak), throttling largely doesn't happen.
2. **Secondary fix is pacing**, as a backstop for the cases where peak-window overlap is unavoidable (e.g., emergency reruns, sustained burst lanes that span multiple hours and inevitably cross the boundary).
3. The `csi_llm_pacing.py` module is the secondary fix. It exists to make us robust, not to replace good scheduling.

---

## 3. What the pacing module actually does

**File:** `/tmp/ua-wt-csi-pacing/src/universal_agent/services/csi_llm_pacing.py`

**Public surface:**

```python
from universal_agent.services.csi_llm_pacing import paced_llm_call

with paced_llm_call(stage="url_judge"):
    response = client.messages.create(...)  # actual LLM call
```

**Two layers:**

1. **Token bucket (rate cap).** `UA_CSI_LLM_RATE_LIMIT_PER_MIN=20` (default off; `0` = disabled). With a `UA_CSI_LLM_BURST_CAPACITY=60` reservoir. Sleeps before the LLM call when the bucket is empty.

2. **Adaptive backoff.** Tracks rolling latency (deque of last 10 calls). If 3 consecutive calls exceed `UA_CSI_LLM_ADAPTIVE_HIGH_MS=8000` (8s), escalates extra sleep starting from `UA_CSI_LLM_ADAPTIVE_INITIAL_SLEEP_MS=500` (500ms) and doubling up to `UA_CSI_LLM_ADAPTIVE_MAX_SLEEP_MS=30000` (30s). De-escalates on 3 consecutive fast calls (<`UA_CSI_LLM_ADAPTIVE_LOW_MS=4000`).

3. **Per-call latency log.** Each call emits one `csi_llm: stage=… call_idx=… latency_ms=… bucket_wait_ms=… adaptive_sleep_ms=… rate_cap=…` log line so we have ground truth on what's happening at runtime.

**Wrap sites (already done in the worktree):**

- `csi_url_judge._call_llm_structured` — `with paced_llm_call(stage="url_judge")`
- `csi_intelligence_pass.analyze_action` — `with paced_llm_call(stage="intelligence_pass")`

**Tests:** 15 unit tests covering config resolution, token bucket sustained-rate behavior, adaptive escalation/de-escalation, exception handling, log line format. **All pass in isolation.** See `/tmp/ua-wt-csi-pacing/tests/unit/test_csi_llm_pacing.py`.

---

## 4. ⚠️ Known unresolved issue with the pacing module

**The bug:** standalone tests show the per-call `csi_llm:` log lines correctly, but when invoked from inside the actual backfill subprocess (`uv run python -m universal_agent.scripts.claude_code_intel_backfill_v2`), **zero pacing log lines appear** even though:

- The env vars `UA_CSI_LLM_RATE_LIMIT_PER_MIN=20` etc. ARE set in the subprocess.
- The backfill cwd IS the worktree (`/tmp/ua-wt-csi-pacing`).
- `PYTHONPATH=src` IS set.
- The imported `csi_url_judge.py` resolves to the worktree path.
- `httpx` INFO logs and `infisical_loader` WARNING logs DO appear in the same backfill output, so it's not a global logging silence.
- A standalone repro of the same import chain (`from csi_llm_pacing import paced_llm_call; with paced_llm_call(...)`) DOES emit logs.

**What this means for the next session:** we don't actually know whether the pacing logic itself is firing in production-shape runs. Without the latency log, we can't validate rate limiting or adaptive backoff worked. The backfill PID 2468264 was alive and doing httpx POSTs to z.ai, but invisible from the pacing layer's perspective.

**Hypotheses to investigate (none confirmed):**

1. Logfire instrumentation in `universal_agent/__init__.py` (`_install_logfire_fail_open_stub`) might filter / route logs from new module names. Other `universal_agent.services.*` loggers work fine, but maybe csi_llm_pacing specifically gets caught.
2. Some import-time side effect resets the logger level on `universal_agent.services.csi_llm_pacing` after module import.
3. The backfill's `basicConfig(level=logging.INFO)` call happens AFTER the pacing module is imported and its logger is created — possibly the level isn't propagating.

**Cheap fix options to try:**

- Add a `print(..., file=sys.stderr, flush=True)` fallback alongside `logger.info(...)` in `paced_llm_call`'s finally block. This guarantees observability even if the logging hierarchy is broken. (I had started this but reverted before merging.) Worktree at `/tmp/ua-wt-csi-pacing/src/universal_agent/services/csi_llm_pacing.py` may already have a `_emit_observability` helper from this attempt — verify before adding more.
- Run the backfill with `PYTHONUNBUFFERED=1` and explicit root logger DEBUG.
- Bypass the logger entirely in pacing — switch to `logging.getLogger().info(...)` against the root logger (avoids any per-logger filter).

**Decision the new session must make BEFORE running the backfill:**

| Option | When to choose | Cost |
|---|---|---|
| **Merge as-is, run with stderr-print fallback** | If you accept "we'll see something even if logger.info is broken" and just want to get a run done | ~10 min — add `_emit_observability` if not present, push, run |
| **Investigate + fix the logger plumbing first** | If you want clean operational telemetry going forward | ~30–60 min — add fast-path repro, find the filter, fix |
| **Skip pacing entirely; rely on schedule shift** | If you're confident running during US 12:00–17:00 CDT (China deep-night) means throttling won't happen, and you want to defer pacing entirely | ~0 min for backfill, but you re-acquire the technical debt |

---

## 5. The actual V3 backfill goal

**What we're trying to do:** full historical packet replay through the V2 (LLM-driven) extraction pipeline into the parallel V2 vault, so we can diff V2-extracted entities against the live V1 vault before swapping. This is the "Phase G" item in the original V2 plan and Followup #6 in the 2026-05-07 handoff doc.

**Why it matters:** V2 extraction has been validated on 50 LLM calls (Phase D) but not at full corpus scale. Diffing the parallel vault against V1 is what gives us confidence to swap.

**Scope:** every historical packet for `@ClaudeDevs` and `@bcherny` that's already on disk under the canonical packet path. (Don't poll new packets during the backfill — that's the cron's job; the backfill is reprocessing existing data.)

**The artifact a successful run produces:** populated parallel V2 vault at the expected path (verify in code via `services/claude_code_intel_replay.py` + the V2 vault path resolver), with N entities and M concepts where N and M reflect the historical corpus scale. Diffable against the live V1 vault.

**The dial-in metric:** entities created vs entities-on-disk count, plus a sanity check that the LLM extraction produces zero junk (matching the Phase D quality bar at scale).

---

## 6. Concrete next-session checklist

In order, with the throttling-aware approach baked in:

1. **Read first** (don't skip — context matters):
   - This file (you're reading it).
   - [`docs/operations/2026-05-08_zai_peak_time_scheduling.md`](../operations/2026-05-08_zai_peak_time_scheduling.md) — the throttling finding.
   - [`docs/proactive_signals/claudedevs_intel_v2_design.md`](claudedevs_intel_v2_design.md) — the V2 architecture (sections 0–5 most relevant).
   - [`docs/proactive_signals/claudedevs_intel_v2_remaining_work.md`](claudedevs_intel_v2_remaining_work.md) — what's shipped vs what's left.

2. **Inventory the worktree state:**
   ```bash
   cd /tmp/ua-wt-csi-pacing
   git log --oneline -10
   git diff feature/latest2 -- src/universal_agent/services/
   ls tests/unit/test_csi_llm_pacing.py
   ```
   Confirm the four worktree files (§1) exist and the unit tests pass:
   ```bash
   cd /tmp/ua-wt-csi-pacing
   uv run pytest tests/unit/test_csi_llm_pacing.py -v
   ```

3. **Decide on the pacing-logs bug** (§4). Recommended for a first restart attempt: pick the **stderr-print fallback** option — append a `print(..., file=sys.stderr, flush=True)` line to `paced_llm_call`'s finally block alongside the existing `logger.info(...)`. That guarantees observability and unblocks the run; we can investigate the logger root cause later.

4. **Pick a launch window:**
   - **Best:** US 12:00–17:00 CDT (= China 01:00–06:00 deep-night off-peak).
   - **Acceptable:** US 14:00–17:00 CDT.
   - **Avoid:** US 22:00–10:00 CDT (= China business-day peak — what bit us last time).

5. **Pacing config to start with** (env vars in the launching shell):
   ```bash
   export UA_CSI_LLM_RATE_LIMIT_PER_MIN=20    # cap at proxy's observed sustained rate
   export UA_CSI_LLM_BURST_CAPACITY=60        # 3-minute reservoir
   export UA_CSI_LLM_ADAPTIVE_BACKOFF=1
   export UA_CSI_LLM_ADAPTIVE_HIGH_MS=8000
   export UA_CSI_LLM_ADAPTIVE_LOW_MS=4000
   export UA_CSI_LLM_ADAPTIVE_MAX_SLEEP_MS=30000
   ```
   These are conservative. Once the run completes successfully, the next iteration can raise the rate (`30/min`, `40/min`) and observe whether adaptive backoff fires.

6. **Run the backfill** (from the worktree, NOT the production checkout):
   ```bash
   cd /tmp/ua-wt-csi-pacing
   PYTHONPATH=src uv run python -m universal_agent.scripts.claude_code_intel_backfill_v2 \
       --profile vps \
       2>&1 | tee /tmp/v3_backfill_$(date +%Y%m%d_%H%M).log
   ```
   Watch for:
   - `csi_llm: stage=… call_idx=… latency_ms=…` lines (pacing telemetry — primary success signal).
   - z.ai POSTs and httpx GETs (URL fetches).
   - V2 vault writes confirming entity creation.

7. **If a stall happens:** look at `csi_llm:` line cadence vs httpx POSTs. Adaptive backoff should be escalating. If not, that's the bug to fix in iteration 2.

8. **On completion:** verify the parallel V2 vault, diff against V1, capture metrics in a session-log doc under `docs/proactive_signals/`.

9. **Post-run:** if the pacing module proves itself, merge the worktree to `feature/latest2`, /ship, retire the worktree.

---

## 7. Files referenced (paths)

**Worktree (unmerged):**
- `/tmp/ua-wt-csi-pacing/src/universal_agent/services/csi_llm_pacing.py`
- `/tmp/ua-wt-csi-pacing/src/universal_agent/services/csi_url_judge.py` (paced wrap)
- `/tmp/ua-wt-csi-pacing/src/universal_agent/services/csi_intelligence_pass.py` (paced wrap)
- `/tmp/ua-wt-csi-pacing/tests/unit/test_csi_llm_pacing.py`

**Already merged on main:**
- `scripts/dev/csi_throttle_probe.py` — three-mode rate characterization tool (baseline / saturation / step-up). Useful for empirically discovering the proxy's actual sustained-rate cap if we ever decide to tune `UA_CSI_LLM_RATE_LIMIT_PER_MIN` non-arbitrarily.
- `services/claude_code_intel_replay.py` — Phase F, calls `analyze_action()` + `apply_vault_delta_to_vault()`.
- `services/csi_intelligence_pass.py` — Phase F LLM extraction (current main, before the pacing wrap).
- `services/csi_intelligence_persistence.py` — vault-write layer.

**Docs to read for context:**
- [`docs/operations/2026-05-08_zai_peak_time_scheduling.md`](../operations/2026-05-08_zai_peak_time_scheduling.md)
- [`docs/proactive_signals/claudedevs_intel_v2_design.md`](claudedevs_intel_v2_design.md)
- [`docs/proactive_signals/claudedevs_intel_v2_remaining_work.md`](claudedevs_intel_v2_remaining_work.md)
- [`docs/proactive_signals/csi_intelligence_pass_implementation_plan_2026-05-07.md`](csi_intelligence_pass_implementation_plan_2026-05-07.md) — original Phase A–H plan
- [`docs/proactive_signals/knowledge_extraction_redesign_context_2026-05-07.md`](knowledge_extraction_redesign_context_2026-05-07.md) — architecture spec for the LLM extraction

---

## 8. Out of scope for the V3 backfill restart

These are real concerns but not blockers for getting a backfill run done:

- **Pacing-logs root cause investigation** — use stderr-print fallback for now; investigate later.
- **Cron schedule shift** to off-peak windows — separate operational ticket; affects daily crons not one-shot backfill runs.
- **Operator-idle-detection signal** for cron gating — separate feature.
- **`csi_convergence` peak-aware schedule swap** — separate ticket.
- **MCP / claude launcher work from this same session** — shipped 2026-05-08 evening as PRs #170 (interactive launcher `ANTHROPIC_*` exclusion), #171 (Cody subprocess scrub), #172 (post-deploy doc cleanup), #173 (/ship hardening). All deployed and verified live. Unrelated to the backfill; covered in `docs/deployment/secrets_and_environments.md` § "MCP Server Credentials" and `docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md`. Side benefit for V3: the `csi_url_judge.py` and `csi_intelligence_pass.py` LLM calls (which become subprocesses in the backfill flow) no longer risk being poisoned by an `ANTHROPIC_API_KEY` leak from the parent process — the Cody scrub fix also covers any UA service that spawns `claude`.

---

## 9. Sanity / acid tests before declaring V3 done

1. **Unit tests pass:** `uv run pytest tests/unit/test_csi_llm_pacing.py -v` → 15/15 PASS.
2. **Pacing telemetry visible:** the backfill log contains at least one `csi_llm: stage=…` line per LLM call.
3. **Rate cap respected:** total LLM calls in any 60-second sliding window ≤ `UA_CSI_LLM_RATE_LIMIT_PER_MIN`.
4. **Adaptive backoff fires under stress** (if you DO end up hitting peak hours): at least one `adaptive backoff INCREASE` warning in the log when latencies climb above 8s.
5. **Vault writes complete:** parallel V2 vault has the expected entity / concept counts; spot-check 5 random entities for canonical-name correctness.
6. **No regressions in Phase D quality:** zero junk extractions across the full corpus (matching the validated 50-call quality bar).

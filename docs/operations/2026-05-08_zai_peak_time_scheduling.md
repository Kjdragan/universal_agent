# 2026-05-08 — Z.AI Peak-Time Scheduling Finding & Remediation

> **Status:** Active architectural finding. Affects all proactive cron jobs and any
> autonomous lane that issues sustained Anthropic-compatible LLM calls through the
> Z.AI proxy (`api.z.ai/api/anthropic`).
>
> **TL;DR:** Z.AI's official position is "no throttling, just capacity-limited at
> peak times." Empirically that has the same effect as throttling: bursts of ~20
> calls/min punctuated by 1.5–3 minute stalls during peak demand. The single most
> impactful remediation is **stop running heavy cron jobs during US night, because
> US night = Greater-China business hours (Beijing daytime, 16:00–22:00 China time
> peak)**. The per-call pacing module (see [pacing module work](#pacing-module-status))
> is a backstop for windows where the overlap is unavoidable, not the primary fix.

---

## 1. The finding

### 1.1 Vendor position vs. observed behavior

Z.AI publicly states they **do not throttle** request speed; they describe peak-period
behavior as "capacity-limited" rather than rate-capped. In practice, customers
(including this project) experience sustained slowdowns during peak demand windows
that are operationally indistinguishable from throttling. The Phase G v2 vault
backfill on 2026-05-07 hit exactly this pattern: ~20 calls/min bursts followed by
1.5–3 minute stalls, repeating across hundreds of LLM calls.

### 1.2 The timezone mistake we made

We assumed running heavy cron jobs **overnight US time** would put them in a
low-load window. The opposite is true. The Z.AI customer base is concentrated in
Greater China; their peak demand follows Beijing business hours.

| Window | China Standard Time (UTC+8) | Demand state |
|---|---|---|
| China business morning | 09:00–12:00 | Peak |
| China business afternoon | 13:00–18:00 | Peak |
| China evening (consumer) | 18:00–22:00 | High |
| China night | 23:00–08:00 | Off-peak |

US Central Daylight Time (UTC−5) maps to China time as **CST = CDT + 13h** during
DST. That means:

| US Central (CDT) | UTC | China (UTC+8) | China demand |
|---|---|---|---|
| **00:00–10:00 CDT (US night → US morning)** | 05:00–15:00 UTC | **13:00–23:00 China** | **PEAK** |
| 10:00–14:00 CDT (US lunch) | 15:00–19:00 UTC | 23:00–03:00 China | OFF-PEAK ✓ |
| 14:00–18:00 CDT (US afternoon) | 19:00–23:00 UTC | 03:00–07:00 China | OFF-PEAK ✓ |
| 18:00–22:00 CDT (US evening) | 23:00–02:00 UTC | 07:00–10:00 China | RAMP-UP |

In other words, **everything from US midnight through US morning is China peak**.
Any heavy LLM-bound cron that fires in that window will see capacity contention.

### 1.3 Why we missed this

The intuition "run heavy work at night when nobody is using the system" is correct
for **single-tenant on-prem systems** and broadly correct for **US-region SaaS**
(AWS / OpenAI / Anthropic native), where US business hours dominate the load
curve. It is **inverted** for a Chinese-vendor proxy where the load curve follows
Asia-Pacific business hours. Every UA cron schedule was sized against the wrong
load curve.

---

## 2. Current cron schedule audit (2026-05-08)

All schedules below are sourced from `src/universal_agent/gateway_server.py`
(`_ensure_*_cron_job()` helpers, lines 17790–18230 and 18316–18430). All are
expressed in `America/Chicago` so they auto-shift between CST and CDT — the
China-time mapping shifts by 1h between summer and winter but the conclusion
(US night = China peak) holds year-round.

| `system_job` | CDT | UTC | China (CST_PRC, UTC+8) | China demand | Verdict |
|---|---|---|---|---|---|
| `nightly_wiki` | 03:15 | 08:15 | **16:15** | 🔴 Peak | Move |
| `morning_briefing` | 06:30 | 11:30 | **19:30** | 🔴 Peak | Move |
| `proactive_report_morning` | 07:00 | 12:00 | **20:00** | 🔴 Peak | Move |
| `autonomous_daily_briefing` | 07:00 | 12:00 | **20:00** | 🔴 Peak | Move |
| `youtube_daily_digest` | 06:00 | 11:00 | **19:00** | 🔴 Peak | Move |
| `proactive_artifact_digest` | 08:00 | 13:00 | **21:00** | 🔴 Late peak | Move |
| `paper_to_podcast` | 21:00 | 02:00 | **10:00** | 🔴 Early peak | Move |
| `vp_coder_workspace_pruning` | Sun 04:00 | Sun 09:00 | Sun 17:00 | 🔴 Peak | Move (low priority — weekly + light) |
| `claude_code_intel` (3 fires) | 08/16/22 | 13/21/03 | 21:00 / 05:00 / **11:00** | 🟡 Mixed | Drop the 22:00 CDT fire |
| `csi_convergence` | every 30m | every 30m | every 30m | 🟡 Mixed | Add peak-window backoff (see §4.3) |
| `proactive_report_midday` | 12:00 | 17:00 | 01:00 | ✅ Off-peak | Keep |
| `proactive_report_afternoon` | 16:00 | 21:00 | 05:00 | ✅ Off-peak | Keep |

**Counted differently:** 9 of 12 system crons currently fire during China peak.

---

## 3. Recommended target windows

### 3.1 The two CDT windows that are simultaneously off-peak in China AND likely user-idle

```
12:00–14:00 CDT  (US lunch, user often away from desk)  →  China 01:00–03:00  (off-peak ✓)
14:00–17:00 CDT  (US afternoon)                         →  China 03:00–06:00  (off-peak ✓)
```

The 12:00–14:00 CDT window is the cleanest: high probability the user is at lunch
or in meetings (so heavy LLM bursts won't compete with their interactive Claude
Code session) AND China is in the deep-night off-peak window. The 14:00–17:00
window is also off-peak in China but the user is more likely to be actively coding,
so we should keep heavy autonomous LLM bursts out of it unless we can detect
keyboard idle.

### 3.2 Anti-pattern: any time between 22:00 CDT and 09:00 CDT

Avoid. Entire window is China peak. This is the inversion of the "run heavy
batch overnight" intuition.

---

## 4. Remediation plan (prioritized)

### 4.1 P0 — Reschedule the cron offenders

Move the eight peak-overlap jobs into off-peak CDT windows. Concrete proposed
schedule (all `America/Chicago`, env var overrides shown so an operator can tune
without a redeploy):

| `system_job` | Current CDT | Proposed CDT | China after move | Env var |
|---|---|---|---|---|
| `paper_to_podcast` | 21:00 | **15:00** | 04:00 ✓ | `UA_PAPER_TO_PODCAST_CRON="0 15 * * *"` |
| `nightly_wiki` | 03:15 | **15:15** | 04:15 ✓ | `UA_NIGHTLY_WIKI_CRON="15 15 * * *"` (env var TBC, see §6) |
| `morning_briefing` | 06:30 | **12:30** | 01:30 ✓ | `UA_MORNING_BRIEFING_CRON="30 12 * * *"` |
| `proactive_report_morning` | 07:00 | **13:00** | 02:00 ✓ | `UA_PROACTIVE_REPORT_MORNING_CRON="0 13 * * *"` (rename misleading; see §6) |
| `autonomous_daily_briefing` | 07:00 | **13:30** | 02:30 ✓ | `UA_AUTONOMOUS_DAILY_BRIEFING_CRON="30 13 * * *"` |
| `youtube_daily_digest` | 06:00 | **14:00** | 03:00 ✓ | `UA_YOUTUBE_DAILY_DIGEST_CRON="0 14 * * *"` |
| `proactive_artifact_digest` | 08:00 | **14:30** | 03:30 ✓ | `UA_PROACTIVE_ARTIFACT_DIGEST_CRON="30 14 * * *"` |
| `vp_coder_workspace_pruning` | Sun 04:00 | **Sun 16:00** | Sun 05:00 ✓ | `UA_VP_CODER_WORKSPACE_PRUNING_CRON="0 16 * * 0"` |
| `claude_code_intel` (3 fires) | 08/16/22 | **13/16/19** | 02/05/08 ✓✓🟡 | `UA_CLAUDE_CODE_INTEL_CRON_EXPR="0 13,16,19 * * *"` |

Notes:

- The "report" naming becomes misleading after the shift — `proactive_report_morning`
  isn't morning anymore. Either rename the job IDs in a follow-up PR or accept the
  cosmetic drift. Renaming touches state-file migration and is **out of scope** for
  the immediate fix.
- The `claude_code_intel` 19:00 CDT fire (= 08:00 China) is at the very edge of
  China business-day ramp-up. Keep an eye on it; if we see contention, drop it.

### 4.2 P1 — Add an "operator-idle" signal and gate proactive heavy lanes on it

Goal: schedule heavy autonomous lanes during the user's known-idle workday windows
(lunch, meetings, away-from-keyboard) instead of fixed wall-clock times.

Possible signal sources, cheapest first:

1. **Mission Control activity timestamp** — the dashboard already records a
   "last interactive activity" stamp. If `now - last_activity > 30min` AND we're
   in 11:00–17:00 CDT, the operator is likely idle.
2. **Antigravity / Claude Code session telemetry** — the inversion plan in
   `docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md`
   already tracks active interactive sessions. We can read that signal directly.
3. **Calendar integration** — the operator's calendar is already integrated for
   the daily-briefing path; a "calendar-busy" check is essentially free.

Implementation sketch (deferred, not in this PR):

- New helper `services/operator_idle.py:operator_is_idle() -> bool`.
- Heavy proactive-lane crons (anything that issues > ~50 LLM calls per fire) gain
  an `if not operator_is_idle(): return` guard at the top of their handler. They
  reschedule themselves for +30min if not idle, with a cap of 4 retries.
- Lighter crons (≤ ~10 LLM calls) ignore the gate and fire on schedule.

This is a **separate work item**; do not bundle with the schedule shift in §4.1.

### 4.3 P2 — Peak-aware adaptive backoff in `csi_convergence` (high-frequency lane)

`csi_convergence` fires every 30 minutes regardless of time. During China peak
(US night/morning) it sees the same contention as the daily jobs but in a
sustained way. Two cheap mitigations:

- **Off-peak frequency boost:** during off-peak windows (US 11:00–17:00 CDT),
  drop the cron interval to `*/15 * * * *` to take advantage of the headroom,
  catching up on the work missed during peak. ENV: `UA_CSI_CONVERGENCE_OFFPEAK_CRON_EXPR`.
- **Peak frequency throttle:** during peak windows (US 22:00–10:00 CDT), drop to
  `0 */1 * * *` (hourly) to avoid wasting LLM credits on stalled calls. ENV:
  `UA_CSI_CONVERGENCE_PEAK_CRON_EXPR`.

This is a single-cron change; the cron service supports schedule swapping via
`_ensure_csi_convergence_cron_job()` already.

### 4.4 P3 — Pacing module status

The token-bucket + adaptive-backoff pacing module shipped in `csi_llm_pacing.py`
remains the right backstop for sustained-burst lanes (CSI Phase F backfill, deep
research runs). With the schedule shift in §4.1, those lanes can run at higher
sustained throughput because they'll fire during off-peak windows; the pacing
module's job becomes "absorb the rare cross-window run" rather than "save us from
overnight throttling." Recommended live config after the schedule shift:

```env
UA_CSI_LLM_RATE_LIMIT_PER_MIN=30   # was 20 — off-peak headroom is higher
UA_CSI_LLM_BURST_CAPACITY=60
UA_CSI_LLM_ADAPTIVE_BACKOFF=1
UA_CSI_LLM_ADAPTIVE_HIGH_MS=8000
UA_CSI_LLM_ADAPTIVE_LOW_MS=4000
UA_CSI_LLM_ADAPTIVE_MAX_SLEEP_MS=30000
```

Pacing-module observability bug (logs not surfacing in subprocess context, see
`/tmp/phase_g_paced_backfill.log` as captured 2026-05-08) is tracked separately
and does not block the schedule shift.

---

## 5. Implementation sequencing

| Step | Owner | Type | Risk |
|---|---|---|---|
| 1. Land this finding doc + index updates | Today (this commit) | Docs only | None |
| 2. Update `docs/03_Operations/cron_job_registration.md` to cite peak-time guidance for new crons | Today (this commit) | Docs only | None |
| 3. Update `docs/01_Architecture/10_Model_Choice_And_Resolution.md` Z.AI section with peak-time note | Today (this commit) | Docs only | None |
| 4. Schedule shift PR (§4.1) — change `default_cron` constants in `gateway_server.py` | Next session | Code change | Low (idempotent helpers + env var overrides preserve operator escape hatch) |
| 5. csi_convergence peak-aware schedule (§4.3) | Next session | Code change | Low |
| 6. Operator-idle signal + heavy-lane gate (§4.2) | Separate session | Feature | Medium (touches multiple lanes) |

Steps 1–3 are committed in this docs-only PR. Steps 4–6 are deliberately scoped
out so the docs land cleanly and a future session can pick up the code work with
the architectural decision already captured.

---

## 6. Open questions / followups

1. **Naming drift.** After the schedule shift, `proactive_report_morning` fires
   at midday. Decide: rename job IDs (touches state-file migration) or accept
   cosmetic drift. Default: accept; reconsider only if it confuses operators.
2. **`UA_NIGHTLY_WIKI_CRON` env var.** The `nightly_wiki` cron registration may
   not have a `cron_env_var` plumbed through `_register_system_cron_job`. Verify
   in step 4 of §5; if missing, add it as part of the shift PR.
3. **DST transitions.** `America/Chicago` shifts between CDT and CST, which
   shifts the China overlap by 1h. The recommended windows in §3 hold in both
   regimes (lunch and afternoon are off-peak in China year-round). No special
   handling needed.
4. **Z.AI vendor signals.** If Z.AI publishes a status / capacity API, route
   `csi_convergence`'s peak/off-peak decision through it instead of hardcoded
   wall-clock windows. Track as a low-priority enhancement.
5. **Anthropic-native fallback for emergencies.** If a phase-boundary backfill
   absolutely must run during China peak (e.g. an incident response), the
   `/opt/ua_demos/` Anthropic-native environment is unaffected by Z.AI capacity
   and can be used as an emergency override. Document the cost tradeoff before
   enabling.

---

## 7. Cross-references

- Pacing module: `src/universal_agent/services/csi_llm_pacing.py` (worktree:
  `/tmp/ua-wt-csi-pacing/...`, not yet merged as of 2026-05-08).
- Cron registration mechanics: [`docs/03_Operations/cron_job_registration.md`](../03_Operations/cron_job_registration.md).
- Z.AI proxy / model resolution: [`docs/01_Architecture/10_Model_Choice_And_Resolution.md`](../01_Architecture/10_Model_Choice_And_Resolution.md).
- CSI v2 design (the lane that surfaced the throttling pattern):
  [`docs/proactive_signals/claudedevs_intel_v2_design.md`](../proactive_signals/claudedevs_intel_v2_design.md).
- 2026-05-07 incident postmortem (where the slow-Phase-G observation originated):
  [`docs/operations/2026-05-07_codie_rogue_branch_recovery.md`](2026-05-07_codie_rogue_branch_recovery.md).

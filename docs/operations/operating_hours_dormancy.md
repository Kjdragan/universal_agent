# Operating Hours / Dormancy Default

**Last updated:** 2026-05-10

## Policy

By default, every cron job, polling loop, scheduled GitHub Actions workflow, or background service in this repo runs **only during waking hours** in Houston time:

> **Active window: 6:00 AM → 10:00 PM Houston time (CDT/CST).**
> **Dormant window: 10:00 PM → 6:00 AM Houston time.**

In UTC the active window translates to (DST-aware):
- **Summer (CDT, UTC-5):** 11:00–02:00 UTC
- **Winter (CST, UTC-6):** 12:00–03:00 UTC

The cron service supports `default_timezone="America/Chicago"`, which automatically handles DST. When registering a new cron job, **prefer Chicago-local time expressions** so DST doesn't have to be re-thought twice a year.

## Why this exists

The operator (Kevin) does not want infrastructure burning quota / firing emails / restarting processes / running LLM calls while he's asleep. Most "intelligence" surfaces are read in the morning anyway, so generating them at 3 AM provides zero operational value but adds:

- LLM quota burn for results nobody reads until 6 hours later
- Email/notification noise during sleep
- Heroic-feeling agentic activity ("the system worked all night!") that maps to no tangible output until the operator wakes

Default-dormant moves the operating tempo to the operator's actual day.

## Scope: this is a content-generation policy, not a global cron gate

The dormancy default exists to suppress **wasteful work** — crons that burn quota or tokens to produce material no human will read until morning. It is **not** a license to turn off production-health automation overnight.

**Subject to dormancy (default 6 AM – 10 PM Houston):**

- Content-generation crons: HackerNews snapshot, briefing materials, proactive reports, doc drift audit, openclaw release sync, intel pipeline material production
- Quota-burning polls: scheduled LLM runs that synthesize observations into briefings
- Anything that costs API calls / tokens / network calls to produce intelligence

**NOT subject to dormancy (24/7 by design):**

- Deploy workflow (`deploy.yml`) — a merge to main can happen at any wall-clock time and the resulting deploy must fire
- Auto-merge (`pr-auto-merge.yml`) — PRs land when CI completes, regardless of clock
- CI / PR failure handling (`ci-failure-issue.yml`) — a failed run at 3 AM should be surfaced for morning fix, not silently broken for 7 hours
- Error alerting, secret rotation alerts, security event handlers
- GitHub Actions workflows triggered by `push` / `pull_request` / `workflow_run` events — these are event-driven, not scheduled, so the policy doesn't apply mechanically

**Decision rule when registering a new cron:** if disabling the cron during sleep hours loses the operator nothing (intel will be regenerated in the morning anyway), it belongs in the active window. If disabling it during sleep hours means production is broken / merges are stuck / failures are silent until 6 AM, it must run 24/7 (add to `DOCUMENTED_EXCEPTIONS` with rationale citing Exception #3 below — latency-sensitive incident response).

## Exceptions

Some services genuinely need 24/7 operation. Adding a new cron requires this checklist:

1. **Does this service produce output that downstream services consume during dormancy?** If yes (e.g., morning briefing reads overnight wiki output), exception is justified. Document the dependency.
2. **Does this service collect transient data that is lost if not captured at the source?** If yes (e.g., webhook handlers, real-time event ingestion, market-hours feeds), exception is justified.
3. **Does latency between event and human response matter?** If yes (incident response, paging), exception is justified.

If none of (1)/(2)/(3) apply, the cron should be dormant. **Do not add 24/7 services by default.**

## Currently registered exceptions

These services run inside the dormancy window with documented justification:

| Service | Schedule | Rationale |
|---|---|---|
| `nightly_wiki` | 3:15 AM Houston | Generates overnight wiki output that `morning_briefing` (6:30 AM) reads. Dormancy-window run gives the briefing fresh material. Exception #1 (downstream consumer). See [`gateway_server.py:18217`](../../src/universal_agent/gateway_server.py#L18217). |
| `atlas_direct_dispatch` | Every 60s, 24/7, UTC | Hermes Phase C (PR #221) — independent dispatcher for tasks tagged `metadata.preferred_vp = "vp.general.primary"`. Exception #3 (latency-sensitive): Atlas-eligible tasks must dispatch within ~60s of being queued; waiting until 6 AM defeats the purpose. **Default OFF** via `UA_ATLAS_DIRECT_DISPATCH_ENABLED=0` — operator opts in after dry-run, so the 24/7 schedule has zero quota cost until enabled. The cron registration also goes through `_proactive_cron_enabled`, AND the script itself re-checks the env var before doing any work (belt-and-suspenders against accidental activation). See [`gateway_server.py:_ensure_atlas_direct_dispatch_cron_job`](../../src/universal_agent/gateway_server.py) and [`docs/reports/hermes-adaptation-phased-plan-2026-05-10.md`](../reports/hermes-adaptation-phased-plan-2026-05-10.md) § Phase C. |
| `simone_chat_auto_complete` | Every 60s, 24/7, UTC | Mission-control PR #255 — pure-SQLite housekeeping that promotes `simone_chat` Task Hub rows from `status="in_progress"` to `status="completed"` once Simone has proposed completion and the operator has been silent past `UA_SIMONE_CHAT_IDLE_MINUTES` (default 10). **No LLM tokens, no external API, no network egress** — the work is a `UPDATE ... WHERE` on the activity DB. Exception #3 (latency-sensitive operator-facing state): a chat started at 8:55 PM has its 10-minute silence window cross into the dormant period; running only in active hours leaves rows in `in_progress` overnight and pollutes the dashboard the operator opens at 6 AM. Registered with `skip_task_hub_link=True` (Observability Protocol exemption — re-emitting Task Hub events for a job that IS Task Hub state-management would be circular). See [`gateway_server.py:_ensure_simone_chat_autocomplete_cron_job`](../../src/universal_agent/gateway_server.py) and [`src/universal_agent/scripts/simone_chat_auto_complete.py`](../../src/universal_agent/scripts/simone_chat_auto_complete.py). |
| `heartbeat` + `proactive_health_watchdog` (pre-flight) | Every heartbeat tick, 24/7 | The Simone heartbeat is the runtime tick driver and is not a cron job; it fires on its own schedule (every ~30m) regardless of clock. Embedded in every tick — full, quick, and skip alike — is the `proactive_health_notifier.run_pre_flight_check` call ([`heartbeat_service.py:_run_heartbeat`](../../src/universal_agent/heartbeat_service.py) just after the "Heartbeat started" broadcast). This is a **health/infrastructure handler**, not content generation: it polls task_hub stale/parked counts, calls every registered pipeline invariant, writes `work_products/proactive_health_latest.json`, and emails Kevin on first occurrence of a new critical finding (with 6h per-finding-id cooldown). Exception #3 (latency-sensitive incident response): a pipeline breaking at 11 PM should email Kevin by 11:30 PM, not silently wait until 6 AM. Default ON; can be disabled per-knob via `UA_HEARTBEAT_PROACTIVE_HEALTH_ENABLED=0` (master), `UA_HEARTBEAT_PROACTIVE_HEALTH_EMAIL_CRITICAL=0` (mute email only — sidecar artifact still written). See [`src/universal_agent/services/proactive_health_notifier.py`](../../src/universal_agent/services/proactive_health_notifier.py) and the canonical doc [`docs/03_Operations/132_Proactive_Health_Watchdog.md`](../03_Operations/132_Proactive_Health_Watchdog.md). |

## Active-hour services (subject to dormancy default)

These run only during 6 AM – 10 PM Houston:

All times Houston (America/Chicago) unless noted. Schedules spread on 2026-05-11 — see "Cron spread (2026-05-11)" below for rationale.

| Service | Schedule | Source |
|---|---|---|
| `morning_briefing` | 6:30 AM daily | [`gateway_server.py`](../../src/universal_agent/gateway_server.py) |
| `proactive_report_morning` | 7:05 AM daily | [`gateway_server.py`](../../src/universal_agent/gateway_server.py) |
| `proactive_artifact_digest` | 8:35 AM daily | [`gateway_server.py`](../../src/universal_agent/gateway_server.py) |
| `csi_demo_triage_rank` | 10:05 AM, 3:05 PM daily | [`gateway_server.py`](../../src/universal_agent/gateway_server.py) |
| `proactive_report_midday` | 12:05 PM daily | [`gateway_server.py`](../../src/universal_agent/gateway_server.py) |
| `proactive_report_afternoon` | 4:05 PM daily | [`gateway_server.py`](../../src/universal_agent/gateway_server.py) |
| `vp_coder_workspace_pruning` | Sun 5:05 PM (weekly) | [`gateway_server.py`](../../src/universal_agent/gateway_server.py) |
| `hackernews_snapshot` | every 30m, 6 AM–10 PM (at :00 and :30) | [`gateway_server.py`](../../src/universal_agent/gateway_server.py) |

GitHub Actions schedules (UTC, no DST handling):

| Workflow | Schedule | Source |
|---|---|---|
| `nightly-doc-drift-audit` | 18:35 UTC daily ≈ 1:35 PM CDT / 12:35 PM CST | [`.github/workflows/nightly-doc-drift-audit.yml`](../../.github/workflows/nightly-doc-drift-audit.yml) |
| `openclaw-release-sync` | 20:35 UTC Tue/Fri ≈ 3:35 PM CDT / 2:35 PM CST | [`.github/workflows/openclaw-release-sync.yml`](../../.github/workflows/openclaw-release-sync.yml) |

## Cron spread (2026-05-11)

Before today, six cron jobs fired between 6:30 and 8:20 AM Houston (morning_briefing, proactive_report_morning, vp_coder_workspace_pruning, openclaw-release-sync, nightly-doc-drift-audit, proactive_artifact_digest, csi_demo_triage_rank #1), with hackernews_snapshot ticking at the :00 and :30 minute marks on top. That bunch caused contention — agents busy at the same time, jobs queueing behind each other, no headroom to add new proactive verbs without picking another saturated slot.

Today's spread uses two simple primitives:

1. **Minute offsets of `:05` and `:35`** to dodge the half-hourly `hackernews_snapshot` ticks.
2. **Hour spacing** so heavy proactive jobs have ≥1h of breathing room.

`morning_briefing` (6:30 AM) and `nightly_wiki` (3:15 AM, exception row) stay fixed because they have explicit consumption-time semantics (operator reads briefing on wake; nightly_wiki feeds briefing). Everything else moved to fill gaps.

## Adding a new cron job

When wiring up a new cron, follow this in `gateway_server.py`:

```python
def _ensure_my_new_job_cron() -> Optional[dict[str, Any]]:
    return _register_system_cron_job(
        system_job="my_new_job",
        # Default to a time INSIDE 6 AM – 9 PM Houston unless you have an
        # exception per docs/operations/operating_hours_dormancy.md
        default_cron="0 9 * * *",                 # 9:00 AM Houston
        default_timezone="America/Chicago",       # DST-aware
        command="!script universal_agent.scripts.my_new_job",
        description="One-line description.",
    )
```

For GitHub Actions schedules:

```yaml
on:
  schedule:
    # Always express in UTC. For Houston 7:00 AM:
    #   CDT (May–Nov): 12:00 UTC
    #   CST (Nov–Mar): 13:00 UTC
    # GitHub Actions doesn't support TZ= in cron strings, so pick
    # one and accept the 1h DST drift, OR run twice and dedupe.
    - cron: '0 12 * * *'  # 7:00 AM CDT / 6:00 AM CST
```

## Verification

`tests/unit/test_cron_dormancy_defaults.py` (added 2026-05-10) pins the active-hour cron schedules and asserts that registered times fall within 6:00–21:00 in `America/Chicago` (with the documented `nightly_wiki` exception). A future change that puts a new cron inside the dormancy window will fail that test, forcing the dev to either justify an exception (add to the exception list) or move the schedule.

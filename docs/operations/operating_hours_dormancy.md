# Operating Hours / Dormancy Default

**Last updated:** 2026-05-10

## Policy

By default, every cron job, polling loop, scheduled GitHub Actions workflow, or background service in this repo runs **only during waking hours** in Houston time:

> **Active window: 6:00 AM → 9:00 PM Houston time (CDT/CST).**
> **Dormant window: 9:00 PM → 6:00 AM Houston time.**

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

## Active-hour services (subject to dormancy default)

These run only during 6 AM – 9 PM Houston:

| Service | Schedule | Source |
|---|---|---|
| `vp_coder_workspace_pruning` | 7:00 AM Sun Houston (weekly) | [`gateway_server.py:17921`](../../src/universal_agent/gateway_server.py#L17921) |
| `morning_briefing` | 6:30 AM daily Houston | [`gateway_server.py:18238`](../../src/universal_agent/gateway_server.py#L18238) |
| `hackernews_snapshot` | every 30m, 6 AM–9 PM Houston | [`gateway_server.py:18256`](../../src/universal_agent/gateway_server.py#L18256) |
| `proactive_report_morning` | 7:00 AM daily Houston | [`gateway_server.py:18270`](../../src/universal_agent/gateway_server.py#L18270) |
| `proactive_report_midday` | 12:00 PM daily Houston | [`gateway_server.py:18284`](../../src/universal_agent/gateway_server.py#L18284) |
| `proactive_report_afternoon` | 4:00 PM daily Houston | [`gateway_server.py:18298`](../../src/universal_agent/gateway_server.py#L18298) |
| `proactive_artifact_digest` | 8:00 AM daily Houston | [`gateway_server.py:18312`](../../src/universal_agent/gateway_server.py#L18312) |
| `csi_demo_triage_rank` | 8:15 AM, 2:15 PM CDT (twice daily) | [`gateway_server.py:18480`](../../src/universal_agent/gateway_server.py#L18480) |

GitHub Actions schedules:

| Workflow | Schedule | Source |
|---|---|---|
| `nightly-doc-drift-audit` | 12:17 UTC daily ≈ 7:17 AM CDT | [`.github/workflows/nightly-doc-drift-audit.yml`](../../.github/workflows/nightly-doc-drift-audit.yml) |
| `openclaw-release-sync` | 12:13 UTC Tue/Fri ≈ 7:13 AM CDT | [`.github/workflows/openclaw-release-sync.yml`](../../.github/workflows/openclaw-release-sync.yml) |

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

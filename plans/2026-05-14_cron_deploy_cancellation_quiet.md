# Cron Deploy-Cancellation: Quiet by Default, Backfill by Default

**Date:** 2026-05-14
**Status:** PLAN — awaiting operator design decisions before implementation
**Trigger incident:** PR #273 (`session-baseline-cleanup`) merged to `main` at 03:04:19 UTC, deploy fired 03:04:22 UTC. The `claude_code_intel_sync` cron had fired at 03:00 UTC and was mid-run. Deploy's `systemctl restart` SIGTERM'd the subprocess at ~03:06:29. Operator received `[ERROR] Autonomous Task Failed` + `[WARNING] Autonomous Task Retrying` emails for what was a non-event (the cron will be retried; backfill will happen). Two scary emails for a normal deploy.

## Diagnosis (code-verified)

The cron service already handles deploy-cancellation **correctly for one shape and incorrectly for the other.** Same root cause, two code paths, only one fixed.

| Cron shape | Cancellation signal | Code path | Run status | Email |
|---|---|---|---|---|
| Coroutine cron (Python, in-process) | `asyncio.CancelledError` raised when the asyncio task is cancelled at service shutdown | `cron_service.py:2013-2036` | `record.status = "cancelled"` | `[INFO] Chron Run Cancelled (service restart)` — correct |
| **Subprocess cron** (`!script …` like `claude_code_intel_run_report`) | **SIGTERM → subprocess exits with `-15`** | falls through to the generic "non-zero exit" failure path | `record.status = "failed"` | **`[ERROR] Autonomous Task Failed` — wrong** |

The notifier (`gateway_server.py:7850-7872`) is well-designed: it already branches on `run_status` and routes `cancelled` to `severity="info"` with a clear "likely deploy restart" message. The deduplication for 429-style upstream outages (`gateway_server.py:7873-7891` via `_should_suppress_upstream_outage_alert`) is also in place. The gap is purely in the **classification** of subprocess SIGTERM as `failed` instead of `cancelled` when the kill is deploy-induced.

## Operator-stated requirements

From the 2026-05-14 conversation:

1. **A deploy-induced cron interruption should be a non-event.** No `[ERROR]` email. No `[WARNING]` retry email. Dashboard tile is fine.
2. **The cron must complete eventually**, via either the immediate retry or a clean backfill on next gateway boot. The operator should not have to remember a missed window.
3. **Retries must not storm.** No 429 cascades from re-firing during a recovery window. Hard caps + backoff already partially exist; verify they cover this path.
4. **A real failure** (manual intervention needed: OOM, code bug, exhausted retries, external service really down) should still produce a clear `[ERROR]`. Suppressing benign noise must not also suppress real signals.

## What I'm proposing (one PR, minimal surface)

### A. Classify subprocess SIGTERM-near-deploy as `cancelled`

In the subprocess-cron failure path of `cron_service.py`, add a branch that fires before the generic "non-zero exit → failed" path:

```python
# Pseudocode — exact location TBD during implementation
if return_code is not None and return_code < 0 and _was_killed_by_deploy_restart():
    record.status = "cancelled"
    record.error = f"subprocess killed by signal {-return_code} (likely deploy restart)"
    failure_class = "cancelled_for_deploy"
    retryable = False  # backfill will handle it; no immediate retry
    return
```

`_was_killed_by_deploy_restart()` returns True iff:
- Return code is negative (signal kill), AND
- One of the following deploy-proximity heuristics holds:
  1. **Service-uptime check:** the gateway's `process_started_at` (already exposed via `/api/v1/version`) is within the last 60 seconds. If the gateway restarted that recently, any subprocess child that died in the same window almost certainly died from `systemctl restart` cascading SIGTERMs.
  2. **Deploy-marker file:** `deploy.yml` writes `/opt/universal_agent/.last-deploy-ts` (epoch seconds) just before `systemctl restart`. If `mtime` of that file is < 60s ago, treat any signal kill as deploy-induced. Cheap to check; survives gateway restart since it lives on disk.

Option 2 is more precise (covers the case where the systemd restart isn't from a deploy — e.g., manual `systemctl restart` for ops reasons would correctly **not** be treated as deploy-induced). I'd default to option 2 with option 1 as fallback.

### B. Replace the "immediate retry" with a clean backfill

The existing `catch_up_on_restart=True` flag (`gateway_server.py:18863`) handles **missed scheduled fires** after a deploy. But a cron that was **mid-run when cancelled** is not the same as a missed schedule fire — the next scheduled fire might be 8 hours away, and we don't want to silently lose the work.

The fix:
- When `failure_class == "cancelled_for_deploy"`, record the interrupted run in a new `cron_pending_backfills` table (or reuse `task_hub_runs` if it already supports this shape).
- On gateway startup, after the existing missed-fires catch-up, **also** re-queue any pending backfills. This is a single replay, not a retry chain — if the replay itself fails, then the normal retry path takes over with all its existing protections.

This means:
- **No `_schedule_retry_run` chain** for deploy-cancelled runs (so no risk of retry-storm during recovery).
- The interrupted run **always** completes eventually, the next time the gateway is healthy.
- If the backfill itself fails on the next boot (e.g., the cron is actually broken), that failure goes through the normal failure path and produces a real `[ERROR]` — preserving signal for real problems.

### C. Notifier severity matrix (audit + small additions)

The notifier already has the right two-branch structure. Verify the matrix is complete:

| Outcome | Attempt # | Email | Dashboard |
|---|---|---|---|
| `success` | any | none (or success email if cron produces one) | green tile |
| `cancelled` (coroutine, `asyncio.CancelledError`) | any | `[INFO]` — current behavior, keep | "Cancelled" tile |
| `cancelled_for_deploy` (subprocess SIGTERM near deploy) — NEW | any | none, OR `[INFO]` daily roll-up | "Cancelled (deploy)" tile — counted in a 24h interruptions metric |
| `failed` (real failure) | 1..N-1 (retries available) | none (assume retry will work) | "Retrying" tile |
| `failed` (real failure) | N (retries exhausted) | `[ERROR]` | "Failed" tile |
| `timeout_killed` | 1..N-1 | none | "Retrying" tile |
| `timeout_killed` | N | `[ERROR]` | "Failed" tile |
| `upstream_outage` (5xx/429 from external service) | any | already deduplicated by `_should_suppress_upstream_outage_alert` | "Outage" tile |

The change from current: suppress the per-attempt `[WARNING] Retrying` email for transient failures (let the existing dedup handle storms; one email when retries are exhausted is enough). Today the notifier emits a retry-queued email after every retry — that's an extra notification per attempt that operators don't actually need.

### D. Retry-storm guards — verify present, don't reinvent

The cron service already has:
- `max_attempts` per job (`cron_service.py:893-911`, resolved from `metadata.max_attempts`)
- `_cancel_in_flight_retry_tasks` (`cron_service.py:796-810`) — when a job is disabled mid-retry-chain, the chain cancels
- `_should_suppress_upstream_outage_alert` (referenced from `gateway_server.py:7884`) — dedup window for known transient-service failures

These should be sufficient for the cancelled-for-deploy path because the **proposed design avoids creating a retry chain in the first place** (cancellation → backfill on next boot, not an immediate retry). No new guards needed for this case.

If a real failure happens AFTER backfill (i.e., the next boot's replay also fails), the existing retry path takes over with its existing guards.

### E. Observability: the dashboard tile is where the signal lives

Add a single 24-hour rolling tile: **"Cron interruptions (last 24h)"** with three counts and a small table:
- N cron runs were cancelled by a deploy → re-queued for backfill
- N cron runs hit a real failure → status (retried OK / still retrying / exhausted)
- N cron runs are pending backfill from a prior gateway lifecycle

Operator sees at a glance "3 deploy cancellations today, all completed on retry, nothing pending" without needing to read three `[INFO]` emails to reconstruct it.

## What I am NOT proposing (out of scope, by design)

1. **Pre-deploy quiesce / drain.** Having `deploy.yml` signal in-flight long crons to finish gracefully before `systemctl restart`. This is a much bigger lift (deploy.yml needs to send a signal, gateway needs a graceful-shutdown timer, with a timeout, with deploy-failure semantics if any cron refuses to finish in time). Post-hoc classification + clean backfill solves 95% of the pain at 10% of the complexity. Revisit only if backfill proves unreliable.
2. **Subprocess-level rate limiting.** Token bucket capping Claude API / ZAI calls across all crons. Real concern for 429s, but the proposed backfill design pushes interrupted work to **next gateway boot**, not to immediate retry — so the burst risk doesn't compound during deploy recovery. If 429s do show up, address as a separate layer.
3. **Restructuring the notifier into a policy module.** The current notifier code (`gateway_server.py:7850-7916`) is clear enough; adding a generic "severity-policy" abstraction would be premature. The matrix in section C is a small diff to the existing if/else, not a refactor.

## Open design decisions (need operator input)

These five questions decide the shape of the PR. Answers below would let me implement straight through.

### 1. Deploy-proximity detection — which heuristic?

- **(a) Deploy-marker file** written by `deploy.yml`: `touch /opt/universal_agent/.last-deploy-ts`. Read its mtime; if < 60s ago, treat signal kill as deploy-induced. Precise; survives gateway restart; survives even a manual `systemctl restart` (correctly NOT marked deploy-induced because no marker file was just touched).
- **(b) Gateway-uptime heuristic**: if `process_started_at < 60s ago`, treat signal kill as deploy-induced. Cheaper (no file I/O), less precise (a manual `systemctl restart` for ops reasons looks identical to a deploy restart, so it would also be classified as deploy-induced, which is mostly fine but not strictly accurate).
- **(c) Both** — marker file is primary signal, uptime is fallback if the file doesn't exist (e.g., very old deploy versions, or first deploy after this lands).

I lean **(c)**. It's the simplest correctness/cost tradeoff.

### 2. Backfill storage — new table or extend existing?

- **(a) New table**: `cron_pending_backfills` (job_id, scheduled_at, dispatch_key, payload). One row per interrupted run. Cleared on successful replay.
- **(b) Extend `task_hub_runs`**: add `outcome_replay_pending` or similar status. Per recent Hermes-F work, this table already tracks attempt history.

I lean **(b)** because Hermes-F just shipped `task_hub_runs` for attempt tracking and adding a third storage location for "things to redo" splits the operational mental model.

### 3. `[INFO]` emails for cancelled-for-deploy — yes, no, or daily roll-up?

- **(a) No email.** Dashboard tile only. The interruption is silent until the operator looks. Zero noise.
- **(b) One `[INFO]` per occurrence.** Same as the existing `cancelled` coroutine case. Visible in the inbox but clearly non-actionable.
- **(c) Daily roll-up.** One `[INFO]` per day at a fixed time listing all deploy cancellations + their replay status. Most digestible, but adds a small new dispatcher.

I lean **(a)** — true non-event behavior, the dashboard is the right surface. But (c) is a defensible choice if you want an inbox artifact.

### 4. Should backfill have a max wait?

If the gateway never restarts (e.g., production stays up for weeks), a backfill row will sit there waiting. Should the cron's normal next-scheduled fire trigger the backfill too?
- **(a) Backfill fires only on gateway restart.** Simpler. Acceptable because the gateway restarts on every deploy and we deploy often.
- **(b) Backfill fires at next scheduled tick OR gateway restart, whichever first.** More complex but bounded latency.

I lean **(a)**. We deploy often; long-pending backfill is unlikely to be an operational issue.

### 5. Scope of this PR — minimum viable, or full matrix?

- **(a) Minimum:** subprocess SIGTERM classification + backfill table + replay-on-startup. Notifier matrix audit but no behavior change to the "retry-queued" suppression. ~300 LOC.
- **(b) Full:** all of (a) + suppress the per-attempt `[WARNING] Retrying` emails for transient failures + add the dashboard tile. ~600 LOC.

I lean **(b)** — the dashboard tile and the `[WARNING]` suppression are the things that make this *feel* like a real solution to the operator, not just a code change.

## Recommendation if you approve all the leans

1. **A1 — Implement (c)**: marker-file primary + uptime fallback for deploy-proximity detection.
2. **A2 — Implement (b)**: extend `task_hub_runs` with `outcome_replay_pending` status.
3. **A3 — Implement (a)**: no email for `cancelled_for_deploy`; dashboard tile carries the signal.
4. **A4 — Implement (a)**: backfill on gateway restart only.
5. **A5 — Implement (b)**: full PR with notifier matrix changes + dashboard tile.

That's one focused PR, one branch off `main`, auto-merges through the standard loop. ~600 LOC including tests. Touches `deploy.yml` (one `touch` line), `cron_service.py` (classification branch + backfill table writer), `gateway_server.py` (startup replay + dashboard tile data + notifier matrix), `tests/unit/` (5-6 new tests). Doc update to Doc 04 § Session Baseline Cleanup (already touches the related area) + a new entry in Doc 03 Operations on cron lifecycle.

## Followup that does NOT belong in this PR

- The `test_dispatch_sweep_stale_release.py` `UA_RUNTIME_STAGE=development` test bug (separate hygiene PR, task #10).
- 429/rate-limit token bucket for cron subprocess work (only if 429s actually appear after this lands — premature otherwise).
- Pre-deploy drain orchestration (only if backfill proves unreliable in practice).

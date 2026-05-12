# Fix 2 — Lightweight cron path for pure-SQL housekeeping crons

**Status:** Filed as followup. Fix 1 (alert dedup) shipped separately and addresses the operator-noise surface of the 2026-05-12 incident. Fix 2 below remains worth doing for **isolation** — keeping pure-housekeeping crons decoupled from Composio / upstream services they don't need.

**Owner:** unassigned
**Priority:** P3 — quality improvement, no operator pain after Fix 1 ships
**Estimate:** ~80–150 lines + tests, 1 session of focused investigation + implementation

---

## What triggered this

On 2026-05-12 at 10:51–10:52 UTC, Composio's ToolRouterV2 returned HTTP 500 for ~30 seconds. Two cron jobs that fired in that window failed:

- `atlas_direct_dispatch` (Hermes Phase C, exists since PR #221) — **understandable**: this cron spawns Claude sessions to dispatch Atlas-tagged tasks, so it legitimately needs Composio.
- `simone_chat_auto_complete` (shipped in PR #255) — **surprising**: the script's actual work is a 5-line SQLite `UPDATE` to promote idle `simone_chat` Task Hub rows to `completed`. No LLM call. No external API. It should not have any dependency on Composio's tool router.

Yet the journal shows the cron-service path made a `POST https://backend.composio.dev/api/v3/tool_router/session` call for both jobs and got the 500. So **something in the cron-orchestration layer (in-process gateway code, not the script subprocess) was calling Composio on behalf of every cron run**, regardless of whether the script needed it.

## What we know

From `journalctl --since '6 hours ago'` on the VPS:

```
10:51:10  POST /api/v3/tool_router/session 500
10:51:10  INFO:composio:Retrying request to /api/v3/tool_router/session in 0.479967 seconds
10:51:21  POST /api/v3/tool_router/session 500 (retry 1)
10:51:21  INFO:composio:Retrying request to /api/v3/tool_router/session in 0.815516 seconds
10:51:32  POST /api/v3/tool_router/session 500 (retry 2, final)
10:51:32  ERROR:universal_agent.cron_service:Chron job cdbc052ed5 failed: Error code: 500 ...
10:51:32  POST /telemetry/errors 200    ← composio SDK telemetry
10:51:34  INFO:cron_service: moved 8 root output(s) into work_products
10:51:34  INFO:heartbeat_service:Heartbeat wake-next requested for cron_atlas_direct_dispatch
10:51:34  RuntimeWarning: coroutine 'to_thread' was never awaited   ← clue
10:51:34  INFO:sdk.runtime_info:Claude Agent SDK runtime versions
10:51:34  ⏳ Starting Composio Session initialization...            ← next cron starting
```

Important observations:

1. The Composio SDK does its own internal retries (3× with exponential backoff). The cron-service exception only fires after the SDK gives up.
2. The `RuntimeWarning: coroutine 'to_thread' was never awaited` strongly suggests an `asyncio.to_thread(self._composio.connected_accounts.list, ...)` from `agent_setup.py:174-178` was dropped on the floor — implying the cron pre-flight initializes Composio via the same path `AgentSetup.initialize()` uses.
3. After the failure, the next minute's cron starts and DOES print `Starting Composio Session initialization...` → `Discovering connected apps...` → the heavyweight `AgentSetup` path. So the gateway has a per-cron "session-warmup" that calls Composio's `tool_router.session.create()` (POST `/api/v3/tool_router/session`).

## Hypothesis (not fully traced)

For every cron `!script` run, the gateway pre-flight (somewhere between `cron_service.py` line 1219 — Phase F task-link block — and line 1262 — `asyncio.create_subprocess_exec`) registers a per-cron session in the gateway's session registry. That registry's session-creation path appears to call `Composio.create(user_id=..., toolkits=...)` to bootstrap a tool-router session for the cron's "agent" identity.

The `simone_chat_auto_complete` script doesn't actually USE that session (it's just a `python -m` subprocess). But the session is created anyway because the cron infrastructure is uniform: every cron gets a Claude/Simone-style session even if it's just running pure-Python housekeeping.

**Definitive trace requires:** stepping through `cron_service.py:1187..1262` with a debugger or adding logger.debug calls at every external-call boundary and re-running. Probably 1–2 hours of focused investigation.

## What "fixed" looks like

The intent is **isolation**: pure-SQL housekeeping crons (`simone_chat_auto_complete`, future similar ones) should:
- Spawn the subprocess
- Wait for it
- Log return code
- Done

No Composio session init. No `claude_agent_sdk` import. No Phase F linkage. No session-dossier registration. No heartbeat-wake-next requested.

### Implementation sketch

Add a `lightweight=True` parameter to `_register_system_cron_job(...)` in `gateway_server.py`. It sets `metadata.lightweight = True` on the cron job row.

In `cron_service.py`, inside the `!script` execution branch (line 1187+), check `job.metadata.get("lightweight")` BEFORE the Phase F linkage block. If True:

```python
# Lightweight path: pure subprocess + rc, no orchestration.
proc = await asyncio.create_subprocess_exec(
    sys.executable, "-m", script_path.replace("/", ".").replace(".py", ""),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    cwd=cwd_str, env=env,
)
stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
exit_code = proc.returncode
record.status = "success" if exit_code == 0 else "error"
record.output_preview = (stdout.decode(errors="replace") + "\n" + stderr.decode(errors="replace"))[:400]
if exit_code != 0:
    record.error = f"Script exited with {exit_code}"
# Skip: Phase F linkage, classify_worker_exit, _close_run, session-dossier registration, heartbeat-wake-next.
self.store.append_run(record)
self._emit_event({"type": "cron_run_completed", "run": record.to_dict(), "reason": reason})
return
```

Then register the existing crons:

```python
# In _ensure_simone_chat_autocomplete_cron_job:
return _register_system_cron_job(
    system_job="simone_chat_auto_complete",
    ...
    lightweight=True,  # ← NEW
    skip_task_hub_link=True,  # already set
)
```

`atlas_direct_dispatch` is NOT a candidate for lightweight — it really does spawn Claude sessions.

### Tradeoffs / open questions

1. **Loss of observability.** Skipping Phase F linkage means the cron doesn't get a `cron:simone_chat_auto_complete` Task Hub row tracking each run. Probably fine because the script's own work IS Task Hub state-management — the row would be circular. But worth confirming the dashboard's cron-status tile doesn't break for lightweight crons.
2. **Loss of dossier.** No `context_brief.md` is generated for the workspace. Probably fine — dossiers describe LLM session work, and a pure-SQL housekeeping run has nothing meaningful to summarize. We can revisit if operators miss them.
3. **Heartbeat wake-next.** Currently every cron run signals `heartbeat_service:Heartbeat wake-next requested for cron_X`. Skipping this for lightweight crons may affect heartbeat scheduling. Need to verify this doesn't break anything downstream.
4. **How many other crons would benefit?** Worth scanning all `_ensure_*_cron_job` registrations and flagging the ones whose scripts are pure stdlib + sqlite3.

### Tests to write

- `lightweight=True` cron registration round-trips through Task Hub correctly (metadata flag persists).
- Cron-service lightweight branch spawns the subprocess and records rc correctly.
- Cron-service lightweight branch does NOT call any of the heavyweight paths (mock + assert no_call).
- Lightweight cron's failure surfaces correctly (record.error populated, cron_run_failed event emitted, alert fanout still works for it — though Fix 1's dedup should kick in if upstream).

### What this followup does NOT need to do

- Trace the exact Composio call site. The lightweight path bypasses the WHOLE heavyweight branch, so we don't need to know which specific call inside it was hitting Composio. The bypass-everything approach is correct regardless.
- Touch `atlas_direct_dispatch`. It legitimately needs the heavyweight infrastructure.
- Touch the LLM cron path (`run_query` branch at line 1591+). Those crons are designed to use Composio.

## Why this can wait

Fix 1 (alert dedup) addresses the operator-noise surface. The remaining benefit of Fix 2 is **architectural cleanliness** — making the dependency surface honest: a housekeeping cron shouldn't depend on Composio's tool router. That's worth doing but not urgent.

Acceptance criteria are clear; the work just needs an unbroken focused session.

## References

- Fix 1 PR: TBD (will link when shipped)
- 2026-05-12 incident emails (operator inbox): three `[ERROR] Autonomous Task Failed` / `[WARNING] Autonomous Task Retrying` emails between 5:51 and 5:56 AM CDT
- Journal: `journalctl --since '2026-05-12 10:50' --until '2026-05-12 11:00'` on the VPS
- Affected code:
  - `src/universal_agent/cron_service.py:1187-1500` (the `!script` branch with Phase F linkage)
  - `src/universal_agent/agent_setup.py:159-220` (the `AgentSetup.initialize()` path that creates the Composio session)
  - `src/universal_agent/gateway_server.py:18286+` (`_register_system_cron_job` — where the `lightweight=True` parameter would land)
  - `src/universal_agent/scripts/simone_chat_auto_complete.py` (the pure-SQL housekeeping script)

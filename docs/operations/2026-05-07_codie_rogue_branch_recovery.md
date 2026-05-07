# 2026-05-07 — Codie Rogue-Branch Recovery Postmortem

**Audience:** Operator + future incident-response readers. Required reading before next time an autonomous-mission branch shows up on a production checkout.

**Status:** ✅ **Recovered.** `/opt/universal_agent` is back on `main` at `f4c793e2`, all UA services healthy, CSI cron path verified end-to-end with `cron_result_30.md` (status=success, no traceback) at 19:08 UTC. Three follow-ups recorded in [`2026-05-07_open_followups.md`](2026-05-07_open_followups.md).

**TL;DR:** An autonomous `vp_mission` titled *"Proactive code quality cleanup: add or improve missing docstrings on public functions and classes"* was claimed by Simone's heartbeat daemon and executed against the live production tree at `/opt/universal_agent`. The mission's branch (`codie/docstring-cleanup-task-hub`) was deployed without going through PR review, and a mid-flight edit to `src/universal_agent/durable/state.py` introduced a `SyntaxError` that crashed the 08:00 CDT CSI cron. Recovery required stopping all UA services, parking the live mission row at the Task Hub layer (a plain `cancel` was resurrected by an orphan-reconciler), `git reset --hard origin/main`, and a manual verification fire of `claude_code_intel_sync` via the Ops API.

---

## Timeline (UTC unless noted)

| Time | Event |
|---|---|
| **2026-05-07 13:00** (08:00 CDT) | Scheduled CSI cron `claude_code_intel_sync` fires. Both attempts (13:00:23, 13:00:32) crash with `SyntaxError: invalid syntax` at `src/universal_agent/durable/state.py:254` — a malformed mid-flight docstring edit. Captured in `cron_result_28.md` and `cron_result_29.md`. |
| **~15:03** (~10:03 CDT) | `state.py` is edited again on the running production tree. Subsequent `py_compile` is clean — the autonomous worker (Simone executing the docstring vp_mission) recovered her own bad edit. |
| **~15:54** | New Simone daemon session starts (`run_daemon_simone_heartbeat_20260507_155453_*`). Continues claiming and working the vp_mission. |
| **~17:00** | Operator session begins. Discovers `/opt/universal_agent` is checked out on `codie/docstring-cleanup-task-hub`, 5 commits ahead of `main`, with 6 actively-modified files in the working tree. Author of HEAD: `VP Analysis Agent`. |
| **17:55:41 – 17:55:49** | Last burst of file edits before recovery: Simone touches `tools/{csi_bridge,wiki_bridge,research_bridge,memory}.py`. After this point no more `.py` edits land in `/opt/universal_agent/src/`. |
| **~18:25** | Operator-driven diagnostics begin. Identify the writer process (PID 1725931 — Claude SDK subprocess of the gateway, prompted as Simone). Establish that the misnamed branch `codie/*` is actually executing as Simone, not as an autonomous "Codie" daemon. |
| **18:30:39** | First cancel SQL applied: `UPDATE task_hub_items SET status='cancelled', seizure_state='unseized', agent_ready=0` for `vp-mission-df2c39bb1e41c6f63d972894`. Both rows updated cleanly (changes()=1 for the task, changes()=1 for the assignment). |
| **18:31:37** | Cancel **resurrected**. An orphan reconciler ran ~58s after the COMMIT and flipped `status` back to `in_progress`. Smoking-gun evidence in `metadata_json.dispatch.last_disposition_reason = "reconciled_orphaned_in_progress"`. The `seizure_state='unseized'` and `agent_ready=0` survived; only `status` was clobbered. |
| **~18:50** | Operator runs `sudo systemctl stop universal-agent-gateway` from a real terminal (sudo requires a TTY; the Claude session can't run sudo non-interactively). Gateway stop kills PID 1725931 cleanly. Sibling UA services stopped next. |
| **18:52:27** | Park SQL applied: `UPDATE task_hub_items SET status='parked', stale_state='parked_manual', seizure_state='unseized', agent_ready=0` for the same task. Park survives — matches the existing `cody_scaffold_request:22f646904a5a3fd8` parked-scaffold pattern that's been stable for 24h+. |
| **18:53** | `git fetch origin && git reset --hard origin/main` on `/opt/universal_agent`. HEAD moves from `57c6d4e6` (capture commit on the codie branch) to `f4c793e2` (origin/main). Working tree clean modulo accumulated production runtime cruft (~150 untracked files: `memory/*.md`, `agent_capability_library/*`, `UA_ARTIFACTS_DIR/*`, etc. — intentionally preserved). |
| **18:53** | Parse check: every cron-path file (`durable/state.py`, `services/claude_code_intel.py`, `services/dispatch_service.py`, `services/todo_dispatch_service.py`, `task_hub.py`, `durable/db.py`) compiles clean on origin/main. |
| **~18:54** | Operator restarts gateway, then sibling services. PID 1830602 = new gateway. |
| **~18:55** | Local `main` branch fast-forwarded from `7a76762e` (97 commits behind!) to `f4c793e2` so the deployed checkout is on the right *branch label* and not on the renamed `codie/*` pointer. |
| **18:57** | Post-restart verification with a clean marker file: 0 new `.py` mtimes in `/opt/universal_agent/src/` over 15s; no `claude_agent_sdk` subprocesses; M-file count = 0; HEAD = origin/main. |
| **19:01:28** | Pre-fire snapshot for manual CSI verification. `task_hub_items` total = 238, `cody_scaffold_request` count = 3, `activity_state.db` mtime = 19:01:25. |
| **19:01:55** | Fire: `POST http://127.0.0.1:8002/api/v1/cron/jobs/claude_code_intel_sync/run` returns `{"run":{"run_id":"queued-13a0aa36","status":"queued"}}`. |
| **19:07:54** | `cron_result_30.md` written. **Status: success.** No traceback. `action_count: 0` (no new posts on either handle since the seen-watermark). `activity_state.db` mtime advanced to 19:08:21. |
| **19:08** | P5 declared clean. CSI v2 verified end-to-end on recovered production. |

---

## What broke

Three causally-linked failures, in order:

### 1. Simone executed a code-author vp_mission

`vp-mission-df2c39bb1e41c6f63d972894` was titled *"Proactive code quality cleanup: add or improve missing docstrings on public functions and classes in the Universal Agent repository at /opt/universal_agent."* It was claimed by `daemon_simone_todo` and executed by a Claude SDK subprocess running under Simone's identity prompt.

This is wrong by design. Simone is a **heartbeat orchestrator**, not a code author. Code-mutation missions belong to `vp.coder.primary` (or the autonomous-mission worktree contract documented in `docs/deployment/ai_coder_instructions.md`). Whatever produced this vp_mission and whatever routed it to Simone bypassed both the worktree-PR contract and the agent-capability gate.

The branch the work landed on was named `codie/docstring-cleanup-task-hub` — misleading: there is no autonomous Codie daemon driving this work. The `codie/` prefix was likely chosen by the task creator under the (wrong) assumption that Codie would execute it. Simone claimed the task; the prefix stuck. Commits were authored as `VP Analysis Agent`, not as Codie or Simone.

### 2. Branch deployed to production without PR

The deploy automation (`.github/workflows/deploy.yml` plus whatever sequenced this branch onto the VPS) put `codie/docstring-cleanup-task-hub` directly onto `/opt/universal_agent` instead of going through `feature/latest2 → develop → main` and the `pr-validate.yml` gate. That gate would have caught the SyntaxError before merge.

How exactly the branch arrived on `/opt/universal_agent` is unclear from this session's diagnostics — possibly Simone herself ran `git checkout` directly inside the production tree as part of executing the mission. Either way, the production checkout was *both* on a non-main branch *and* being live-edited by an agent in the same tree. That is the load-bearing failure.

### 3. Mid-flight SyntaxError crashed CSI cron

While editing `src/universal_agent/durable/state.py` Simone introduced a stray docstring (`"""Insert a new run_steps row and set it as the current step."""`) at a syntactically invalid position around line 254. The 08:00 CDT cron fire imported `state.py` via `from universal_agent.durable.db import ...`, which transitively loads `durable/__init__.py → worker_pool.py → state.py`. Import failed → cron crashed → `cron_result_28.md` and `cron_result_29.md` show the error.

Simone subsequently re-edited `state.py` at ~15:03 UTC and the file was syntactically clean by the time the operator's diagnostic session began. So when the operator first read the file at line 254 they saw valid code — only the `cron_result_*.md` traceback proved the file had been broken earlier.

---

## Diagnostic dead ends

These are the wrong turns the operator session worked through. Recording them so the next session doesn't repeat them.

### Dead end 1 — the prior handoff said "Phase 2 has never fired"

The 2026-05-06 handoff doc claimed `/opt/ua_demos/` had only `_smoke` and that no `cody_scaffold_request` rows had ever been produced on production. Both claims were wrong by the time the new session opened:

- `/opt/ua_demos/` had `_smoke` plus `webhooks__demo-1` and `e3rneinuzx__demo-1` (the rubric/define-outcomes demo). Both were artifacts of late-May-6 manual work *and* organic Phase 1→2→3 chain success.
- Three `cody_scaffold_request` rows existed in production: 2 completed, 1 parked.

The diagnostic session initially trusted the handoff and missed these signals. Lesson: the handoff doc is a snapshot, not authoritative — verify state directly before believing it.

### Dead end 2 — wrong Task Hub DB path

The handoff (and `heartbeat_service.py:320`) names `/opt/universal_agent/AGENT_RUN_WORKSPACES/task_hub.db` as the canonical Task Hub DB. The user's first directive in this session also named `/opt/universal_agent/state/task_hub.db`.

Neither is the file the running code actually opens. The CSI flow's producer (`scripts/claude_code_intel_run_report.py:212`) and consumer (`services/todo_dispatch_service.py:714`) both call `connect_runtime_db(get_activity_db_path())`, which resolves at runtime to `/opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db`. The `task_hub.db` files on disk are stale leftovers — most recent mtime was May 1.

Lesson: the path stamped in `_register_system_cron_job` prompts and prior handoffs is a comment field, not a path resolver. Always run the resolver function (`durable/db.py:get_activity_db_path()`) at runtime to confirm.

### Dead end 3 — wrong PID identified as Codie

The operator initially flagged `PID 1768352` as Codie. That PID was the operator's own Claude session running this very recovery. Later they re-flagged `PID 1437861` as Codie. That PID turned out to be an unrelated 12-hour-old orphan with a deleted TTY and no children — it had no editing capability.

The actual editor was `PID 1725931`: a `claude_agent_sdk._bundled/claude` subprocess of the gateway, prompted as Simone, executing the docstring vp_mission. Identified only via `pgrep -af 'claude_agent_sdk'` and reading the cmdline (which contained Simone's full system prompt verbatim).

Lesson: when the diagnosis says "this PID is the writer", verify by checking what files have recent mtimes, what the PID's cwd is, what its parent process is, and whether its cmdline contains the agent's identity prompt. Don't trust a process-name match.

### Dead end 4 — `cancel` SQL got resurrected

The first attempt to halt the mission used `UPDATE task_hub_items SET status='cancelled'`. The transaction committed cleanly (1 row changed); 58s later the row read `in_progress` again. An orphan reconciler had detected the row as orphaned (because the assignment was now cancelled) and "fixed" it by clobbering `status` back to `in_progress`. `seizure_state='unseized'` and `agent_ready=0` survived — only `status` was blasted.

`TASK_STATUS_CANCELLED` exists in `TERMINAL_STATUSES` but `task_hub.py`'s upsert/reconcile path has only an asymmetric guard: it protects `IN_PROGRESS/BLOCKED/REVIEW` from being clobbered to `OPEN`, but no symmetric guard protects `CANCELLED/COMPLETED/PARKED` from being clobbered to `IN_PROGRESS`.

The fix in this session was to use `status='parked'` instead. `PARKED` *did* survive — matches the existing `parked_manual` pattern that's been stable for 24h+. Tracked as Followup #1.

---

## Recovery sequence (commands actually used)

### Phase 1 — Diagnose & capture (gateway up)

```bash
# Locate the actual writer process
pgrep -af 'claude_agent_sdk'                      # → PID 1725931 (Simone)
ls -l /proc/1725931/cwd /proc/1725931/exe         # gateway-spawned Claude SDK subprocess
cat /proc/1725931/cmdline | tr '\0' ' ' | head    # cmdline contains Simone's full identity prompt

# Inventory codie branch state on /opt/universal_agent
cd /opt/universal_agent
git log --oneline origin/main..HEAD               # 5 commits, all docstring-only
git diff origin/main..HEAD --stat                 # 22 files, 196 insertions
git status --short | grep -v '^??'                # 6 modified-but-uncommitted files

# Capture the work on the remote BEFORE any reset
git push origin codie/docstring-cleanup-task-hub
git add src/universal_agent/tools/{csi_bridge,memory,research_bridge,task_hub_bridge,vp_orchestration,wiki_bridge}.py
git -c user.name="Simone (captured pre-reset)" \
    -c user.email="ops@universal-agent.local" \
    commit -m "docs: capture in-flight tools/* docstrings before /opt prod reset"
git push origin codie/docstring-cleanup-task-hub
```

After Phase 1 the remote `origin/codie/docstring-cleanup-task-hub` was at `57c6d4e6` — six commits, all docstring-only, fully captured. None of Simone's work was at risk.

### Phase 2 — Stop services (operator-only; sudo requires real TTY)

```bash
# In a real terminal owned by the operator (the Claude session can't run sudo)
sudo systemctl stop universal-agent-gateway
sleep 5
ps -p 1725931 || echo "gone"
sudo systemctl stop \
  universal-agent-api universal-agent-webui \
  universal-agent-vp-worker@vp.coder.primary \
  universal-agent-vp-worker@vp.general.primary \
  ua-discord-cc-bot ua-discord-intelligence \
  universal-agent-telegram
```

Why gateway-first: gateway is the parent of `daemon_simone_todo`, which is the parent of `PID 1725931`. Stopping gateway reaps the writer cleanly.

### Phase 3 — Park the live mission row (services down)

```bash
sqlite3 /opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db <<'SQL'
UPDATE task_hub_items
   SET status        = 'parked',
       stale_state   = 'parked_manual',
       seizure_state = 'unseized',
       agent_ready   = 0,
       updated_at    = strftime('%Y-%m-%dT%H:%M:%f','now') || '+00:00'
 WHERE task_id = 'vp-mission-df2c39bb1e41c6f63d972894';
SQL
```

Why `parked` not `cancelled`: see Dead End 4.

Why with services down: a running gateway will spawn the orphan reconciler periodically. With everything stopped, the SQL has no opposition; when the gateway comes back up later it sees a stable `parked / parked_manual / agent_ready=0 / seizure_state=unseized` row and leaves it alone.

### Phase 4 — Reset prod tree to origin/main (services down)

```bash
cd /opt/universal_agent
git fetch origin
git reset --hard origin/main
# Validate the reset code parses before letting services load it
python3 -c "
import ast
for f in ['src/universal_agent/durable/state.py',
          'src/universal_agent/services/claude_code_intel.py',
          'src/universal_agent/services/dispatch_service.py',
          'src/universal_agent/services/todo_dispatch_service.py',
          'src/universal_agent/task_hub.py',
          'src/universal_agent/durable/db.py']:
    ast.parse(open(f).read())
    print(f'{f}: OK')
"
```

Why services down: if the gateway is up and Python imports any of these modules during the reset window, it picks up the wrong version. The 1-2s window where `git reset` rewrites disk content is enough.

### Phase 5 — Operator restarts services, then verify

```bash
# Operator (real terminal):
sudo systemctl start universal-agent-gateway
# wait 10s, check is-active and journal for tracebacks
sudo systemctl start \
  universal-agent-api universal-agent-webui \
  universal-agent-vp-worker@vp.coder.primary \
  universal-agent-vp-worker@vp.general.primary \
  ua-discord-cc-bot ua-discord-intelligence \
  universal-agent-telegram
```

```bash
# Then back in the Claude session — branch hygiene + 30s observation
cd /opt/universal_agent
git checkout main                 # was on the renamed codie/* pointer
git pull --ff-only                # local main was 97 commits behind origin/main!
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"   # verify
```

### Phase 6 — Manual verification fire

```bash
# From inside the Claude session, hitting the gateway's Ops API
curl -X POST http://127.0.0.1:8002/api/v1/cron/jobs/claude_code_intel_sync/run
# → {"run":{"run_id":"queued-13a0aa36","status":"queued"}}

# Poll for completion (typical run is 4-8 min)
# Watch /opt/universal_agent/AGENT_RUN_WORKSPACES/cron_claude_code_intel_sync/work_products/cron_result_*.md

# Verify result
cat $(ls -t /opt/universal_agent/AGENT_RUN_WORKSPACES/cron_claude_code_intel_sync/work_products/cron_result_*.md | head -1)
# → Status: success, action_count: 0, no traceback
```

---

## Why each gate was needed

The recovery looked over-engineered in places. It wasn't — every gate prevented a specific failure mode the diagnostic process surfaced.

### Gate 1 — Capture-before-reset (Phase 1 before Phase 2)

If the recovery had gone `stop → reset → start` without capturing first, Simone's docstring work would have been lost permanently. The remote `origin/codie/docstring-cleanup-task-hub` only had 4 commits (`fd9dfd78` and earlier); the unpushed `105a85bf` plus the 6 uncommitted modified files would have evaporated.

By doing capture first, we ended up with a stable `origin/codie/docstring-cleanup-task-hub` at `57c6d4e6` containing all six docstring commits. Whoever wants to review or merge this work later can do so via a normal PR.

### Gate 2 — Gateway-stop before SQL (Phase 2 before Phase 3)

Phase 3's earlier-attempted variant ran with the gateway up, and the orphan reconciler resurrected the row 58s later. This was the costliest dead end of the recovery — about 30 minutes of false-start before realizing the system wouldn't accept a cancellation while live.

With the gateway down, the orphan reconciler can't run, and the SQL is durable.

### Gate 3 — Park, not Cancel (Phase 3)

The reconciler treats `cancelled` rows as buggy state ("no live assignment, must be a stuck-task to recover"). It treats `parked` rows as deliberate operator decisions and respects them. Verified empirically by the existing `cody_scaffold_request:22f646904a5a3fd8` parked row which had survived 24h+.

### Gate 4 — Parse check before service restart (Phase 4 before Phase 5)

If the reset somehow landed on a state where origin/main was syntactically broken (e.g., during an in-flight push that hadn't finished propagating), restarting services would crash them on import. Cheap to verify; catastrophic to skip.

### Gate 5 — Manual verification fire (Phase 6)

A passing parse check confirms code loads. It doesn't confirm the cron path executes end-to-end. Phase 6 — firing the cron via the same Ops API the scheduled fire uses (`POST /api/v1/cron/jobs/{job_id}/run` → `cron_service.run_job_now(reason="manual", background=True)`) — exercises the full pipeline: gateway dispatches the job, the script runs, both handles poll, packets are conditionally written, the rolling brief is rebuilt, the email policy fires, the task hub is updated. The `cron_result_30.md` `Status: success` is the proof.

---

## Production state at end of session

| Item | Value |
|---|---|
| `/opt/universal_agent` HEAD | `f4c793e2` |
| `/opt/universal_agent` branch | `main` (deployed checkout is now correctly named) |
| Captured Codie/Simone work | `origin/codie/docstring-cleanup-task-hub @ 57c6d4e6` (6 commits) |
| `vp-mission-df2c39bb` | `parked / parked_manual / agent_ready=0 / seizure_state=unseized` |
| UA services | All 9 active per operator's gate-3 confirmation (gateway, api, webui, 2× vp-worker, 2× discord, telegram, docs) |
| Last CSI cron fire | `cron_result_30.md` 19:07:54, Status=success, 0 actions, no traceback |
| Next scheduled CSI fire | 16:00 CDT / 21:00 UTC |

## Open follow-ups

See [`2026-05-07_open_followups.md`](2026-05-07_open_followups.md) for the three durable items this incident surfaced.

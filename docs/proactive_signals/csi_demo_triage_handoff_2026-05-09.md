# CSI Demo Triage — Handoff Context (2026-05-09)

> **Purpose:** Self-contained handoff for a new Claude Code session that picks
> up CSI demo triage work where the 2026-05-09 session left it. Paste this
> entire file as the opening prompt and the new session has everything it
> needs.

---

## 1. What just shipped today (2026-05-09)

Two commits landed on `main` today:

| SHA | Subject | What it does |
|---|---|---|
| `5a3a936a` | feat(csi): demo triage flyout — human approval gate before Task Hub | The full triage subsystem (DB + ranker + API + UI). 12 files, +2077 LOC. |
| `67534779` | fix(launcher): strip GH_TOKEN/GITHUB_TOKEN from interactive claude env | Fixes interactive `gh` so `/ship`'s in-script deploy watching works. |

Both went through `/ship`. Verify deploys at: https://github.com/Kjdragan/universal_agent/actions

---

## 2. What the demo triage subsystem does

**The problem it solves:** The CSI intel pipeline (cron `claude_code_intel_sync`
+ backfill `scripts/claude_code_intel_backfill_v2.py`) processes tweets from
@ClaudeDevs and @bcherny and tier-classifies actions 1-4. Tier 3+ actions are
"demo-worthy" or "intel-worthy" — they should generate work for Cody (tier 3)
or Atlas (tier 4). Previously these could be auto-queued into Task Hub via
`queue_follow_up_tasks` — which would create unsupervised work. **51 tier-3
and 67 tier-4 candidates exist in historical packets**; auto-queuing them
would flood Task Hub. Each Cody demo build is 5-30 min of agent time.

**The new flow:**
1. **Discovery (passive, runs as side-effect of intel pipeline):** After every
   packet write, `replay_packet()` calls `csi_demo_triage.sync_candidates_from_packet(packet_dir)`
   which `INSERT OR IGNORE`s tier 3+ actions into a SQLite triage DB at
   `artifacts/proactive/claude_code_intel/demo_triage.db`. Idempotent.

2. **Ranking (separate cron, every 8:15 / 14:15 CDT — both off-peak per
   Z.AI peak-time scheduling):** `csi_demo_triage_ranker.run_ranking()` selects
   pending candidates without scores (or with stale scores >24h old), makes one
   GLM call to score each 0-10 with rationale, persists results.

3. **UI (flyout drawer on the existing `/dashboard/claude-code-intel/` page):**
   - "Demo Triage" button next to "Knowledge Search" with a pending-count chip
   - Right-side drawer (560px) with two panels:
     - **★ Top 5 Recommended** — highest LLM-scored pending candidates
     - **All Candidates** — newest-first, with filter pills (Cody/Atlas/All) and "Show resolved" toggle for restoring dismissed items
   - Each card: score badge + tier badge + handle/date + summary + "Why this score" + linked-source count + Approve/Trash buttons
   - Approve → calls `task_hub.upsert_item` with the same payload `queue_follow_up_tasks` would have produced (shared via extracted `_build_followup_task_payload` helper)
   - Trash → state=dismissed (with Restore option in "Show resolved" view)

**The auto-queue path is dead:**
- `--queue-task-hub` flag removed from backfill CLI
- `claude_code_intel_run_report.py` cron hard-defaults `queue_task_hub=False` with no opt-in flag (belt-and-suspenders: forced 3x in the call chain)
- After this lands, the ONLY way a Cody/Atlas task gets created from claude-code intel is through the operator's approval click in the flyout

---

## 3. Where things are RIGHT NOW

**Live in production:**
- Both commits deployed
- New cron `csi_demo_triage_rank` registered (next fires at 13:15 or 19:15 UTC, whichever is sooner)
- 5 new endpoints live under `/api/v1/dashboard/claude-code-intel/triage[...]`
- Triage flyout button visible in dashboard

**Triage DB state:** **Empty / not yet auto-created.** The DB file at
`artifacts/proactive/claude_code_intel/demo_triage.db` is created on first
write — either by:
- The next `claude_code_intel_sync` cron run (08:00 / 16:00 / 22:00 CDT) producing a new packet, OR
- A manual replay of an existing packet, OR
- An explicit historical-packet backfill (see §4)

**As of right now there are 0 candidates in the triage DB.** The cron-era
packets from 5/8 onward only had 1 tier-3 action across them, so even after
a few cron cycles the drawer will be sparse.

---

## 4. Open decision: historical backfill

The 51 tier-3 + 67 tier-4 candidates from packets 2026-04-20 through
2026-05-09 are NOT yet in the triage DB. Two ways to get them in:

**Option A — Targeted replay (recommended for first test):** Run replay
against a small window first to validate end-to-end. Example:

```bash
cd /opt/universal_agent
PYTHONPATH=src uv run python -c "
from pathlib import Path
from universal_agent.services.claude_code_intel_replay import replay_packet, ClaudeCodeIntelReplayConfig
import sqlite3
from universal_agent.durable.db import get_activity_db_path
packet = Path('/opt/universal_agent/artifacts/proactive/claude_code_intel/packets/2026-04-27/224255__bcherny')
with sqlite3.connect(get_activity_db_path()) as conn:
    conn.row_factory = sqlite3.Row
    result = replay_packet(config=ClaudeCodeIntelReplayConfig(packet_dir=packet, queue_task_hub=False, write_vault=False), conn=conn)
    print('triage_inserted:', result.get('triage_inserted'))
"
```

That packet has 7 tier-4 actions per the backfill audit; should insert 7 rows.
Does NOT make LLM calls beyond grounding (it re-reads the existing
`actions_refined.json`).

**Option B — Full historical backfill:** Run the full backfill against all 43
packets. Expect ~4,000 LLM calls (mostly URL judging and intelligence pass —
this populates the vault AND the triage DB). Per the prior handoff
(`csi_v3_backfill_restart_handoff_2026-05-08.md`):
- Run during US 12:00–17:00 CDT (China deep-night) to avoid Z.AI throttling
- Use `tmux` or `nohup` so terminal-close doesn't kill the run
- The pacing module is on a parked branch (`claude/csi-llm-pacing` / worktree `/tmp/ua-wt-csi-pacing`), NOT yet merged to main — so a backfill from main runs without pacing. Last 5/9 attempt got 4/10 hours of throttling.

Recommendation: **Option A first** to confirm the discovery hook produces
expected DB rows and the UI renders them correctly. Then decide on Option B.

---

## 5. Verification checklist for the new session

Run these in order before doing anything else:

```bash
# 5a. Confirm latest deploy is live
git log --oneline origin/main | head -3
# Expect: 67534779 (launcher fix), 5a3a936a (triage flyout), 435b8ca9 (hackernews)

# 5b. Confirm gh works (proves the launcher fix is active in this session)
echo "GH_TOKEN: ${GH_TOKEN:-unset}"
gh auth status
# Expect: GH_TOKEN: unset, gh shows file-stored OAuth (gho_*) all green

# 5c. Confirm the gateway picked up the new endpoints (live on 127.0.0.1:???)
curl -s http://127.0.0.1:8090/api/v1/dashboard/claude-code-intel/triage | head -50
# Expect: JSON {"counts": {...}, "top5": [], "all": []} (status: ok, empty lists)
# If 404: gateway hasn't picked up the new code — restart needed
# If non-listening: check `systemctl --user status universal-agent` or wherever gateway lives

# 5d. Confirm the new cron is registered
python3 -c "
import json, time
d = json.load(open('/opt/universal_agent/AGENT_RUN_WORKSPACES/cron_jobs.json'))
for j in d['jobs']:
    if 'demo_triage' in j['job_id']:
        print(j['job_id'], '|', j['cron_expr'], '|', 'enabled='+str(j['enabled']))
"
# Expect: csi_demo_triage_rank | 15 13,19 * * * | enabled=True

# 5e. Confirm the triage DB does NOT yet exist (will be auto-created on first write)
ls -la /opt/universal_agent/artifacts/proactive/claude_code_intel/demo_triage.db 2>&1
# Expect: "No such file or directory" (correct — it auto-creates on first sync)
```

If all 5 pass, the system is healthy and ready for the historical backfill
decision.

---

## 6. Where things live

**Production code:**
- `src/universal_agent/services/csi_demo_triage.py` (519 LOC) — DB schema, candidate dataclass, sync/list/approve/dismiss/restore
- `src/universal_agent/services/csi_demo_triage_ranker.py` (264 LOC) — LLM scoring (single GLM call, parses one-JSON-per-line)
- `src/universal_agent/scripts/csi_demo_triage_rank.py` (60 LOC) — cron CLI entry
- `src/universal_agent/services/claude_code_intel.py` lines 901-986 — extracted `_build_followup_task_payload(handle, packet_dir, action, tier, post_id)` shared between `queue_follow_up_tasks` and triage approval
- `src/universal_agent/services/claude_code_intel_replay.py` lines ~195-210 — discovery hook in `replay_packet()`
- `src/universal_agent/gateway_server.py` lines 18395-18430 — `_ensure_csi_demo_triage_rank_cron_job()`
- `src/universal_agent/gateway_server.py` lines 18820-18900 — 5 new triage endpoints
- `web-ui/app/dashboard/claude-code-intel/page.tsx` lines 230-245 + 374-440 + 500-518 + 1019-1170 — drawer state, fetchers, button, drawer JSX, `TriageCard` component

**Tests:**
- `tests/unit/test_csi_demo_triage.py` (296 LOC, 13 tests) — schema, sync idempotence, listing, top-N, approve round-trip into real Task Hub, idempotency, dismiss/restore, refusal rules, counts
- `tests/unit/test_csi_demo_triage_ranker.py` (96 LOC, 3 tests) — parse+persist, no-pending short-circuit, LLM failure path
- `tests/unit/test_claude_launcher_strip.py` (now 11 tests) — covers GH_TOKEN strip alongside the existing ANTHROPIC_* prefix strip

**Data paths:**
- Triage DB: `artifacts/proactive/claude_code_intel/demo_triage.db`
- Source packets: `artifacts/proactive/claude_code_intel/packets/<YYYY-MM-DD>/<HHMMSS>__<handle>/`
- CSI vault (V2, populated): `artifacts/knowledge-vaults/claude-code-intelligence/` (770 files, 58 entities as of 2026-05-09)

**Cron registry (canonical, NOT the `workspaces/cron_jobs.json` file):**
- `AGENT_RUN_WORKSPACES/cron_jobs.json`

---

## 7. What to do first in the new session

Recommended sequence:

1. **Verify** (run the §5 checklist above, all 5 should pass)
2. **Open the dashboard** in a browser to confirm the "Demo Triage" button renders next to "Knowledge Search". Click it. Drawer should open with empty state ("0 pending").
3. **Decide on the backfill approach** — Option A (single packet) first to validate the flow, OR jump to Option B (full historical) if you want to commit to the longer run
4. **Run the chosen backfill** (commands in §4)
5. **Refresh the dashboard drawer** — it should now show candidates. Initially unranked (no scores). Wait for next 13:15 or 19:15 UTC for the ranker cron to score them, OR hit the "Rerank" button in the drawer to score them immediately.
6. **Approve a test candidate** — one click to verify the round-trip into Task Hub works end-to-end (check `AGENT_RUN_WORKSPACES/task_hub.db` for the new row)

---

## 8. Known gaps / followups (not blocking, just to be aware)

- **Pacing module not merged.** The `claude/csi-llm-pacing` branch (with the dual-channel observability fix) is still parked. Worktree at `/tmp/ua-wt-csi-pacing/`. The triage ranker imports `paced_llm_call` if available, falls back to a `nullcontext` if not — so it works either way, but heavy backfill runs would benefit from merging the pacing branch first. See `csi_v3_backfill_restart_handoff_2026-05-08.md` §4.
- **Phase 2 wiring still missing.** `memory/HEARTBEAT.md` has no directive telling Simone to handle approved `cody_scaffold_request` rows. So even when you Approve a candidate today and a row lands in Task Hub, Simone won't act on it without a manual nudge. Out of scope for this PR; tracked in CLAUDE.md caveats.
- **GitHub vulnerability nags.** Push output flagged 4 vulnerabilities in dependencies (2 high, 1 moderate, 1 low). Visible at https://github.com/Kjdragan/universal_agent/security/dependabot — separate housekeeping task.

---

## 9. Operator (Kevin) preferences captured today

- Wants demos to go through human approval, NOT auto-queue
- Wants the flyout panel on the intel tab specifically (not a separate page)
- Wants two panels: top-5 LLM-recommended + full chronological list
- Wants delete/trash icon on every candidate, hover-revealed
- Wants SQLite (not JSON) so we can analyze the corpus over time
- Wants both tier 3 (Cody/demos) and tier 4 (Atlas/intel) in the drawer with separate sections
- Defers auto-running the full backfill — wants to start small to validate first

---

End of handoff. Good luck. The hard part is done — this is just verification + a small backfill + watching it work.

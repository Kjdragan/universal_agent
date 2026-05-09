# CSI Demo Triage — Validation Plan & Handoff (2026-05-09)

> **You are a fresh Claude Code session. Read this entire doc first. Then
> execute the phases in order. Do not skip phases. Each phase has clear
> success criteria. Stop and ask the operator only if a success criterion
> fails — never if it passes.**

---

## 1. Big picture (what this system does)

We monitor the @ClaudeDevs and @bcherny X (Twitter) accounts for new Claude
Code features, releases, and discussion. The pipeline:

```
X API → packets (raw tweets + metadata)
      → LLM extraction → actions_refined.json (each action tier 1-4)
      → vault writes (knowledge base of entities/concepts)
      → triage DB (NEW — only tier 3+ actions, awaiting human review)
      → operator drawer on /dashboard/claude-code-intel
            ↓ approve
      → Task Hub (cody_scaffold_request for tier 3 / claude_code_kb_update for tier 4)
            ↓
      → Cody builds the demo / Atlas writes the analysis
```

**Tier meanings:**
- Tier 1: FYI digest (not actionable)
- Tier 2: worth a vault note (auto-handled)
- **Tier 3: demo-worthy** (build something to exercise the feature) → Cody
- **Tier 4: strategic intel** (analyze + write up) → Atlas

**The gate this PR adds:** Tier 3+ actions DO NOT auto-queue to Task Hub
anymore. They land in a triage DB, get LLM-ranked 0-10, and surface in a
flyout drawer where the operator clicks Approve or Trash. The Top 5
highest-ranked candidates appear at the top of the drawer for quick
high-confidence approvals; the full chronological list is below.

**Why a gate?** Each Cody demo build is 5-30 min of agent time. There are
~118 historical candidates. Auto-queueing them would flood Task Hub.

---

## 2. State of the world right now

✅ **Code deployed and live on `main`:**
- `5a3a936a` — triage flyout (DB + ranker + 5 endpoints + UI)
- `67534779` — launcher fix (strips bad `GH_TOKEN` from interactive sessions so `gh` works)

✅ **Subsystem autonomously working:**
- Triage DB exists at `artifacts/proactive/claude_code_intel/demo_triage.db`
- 5 candidates already populated by the most recent cron run (3 tier-3 + 2 tier-4, all from @ClaudeDevs, all `state=pending`, all unranked)
- Discovery hook fires on every replay — new packets from cron will keep adding candidates
- Ranking cron is registered (job_id `9ad58b493f`, schedule `15 13,19 * * *` UTC = 8:15/14:15 CDT — both in off-peak window). First firing missed today because gateway restart was after 19:15 UTC; next run tomorrow 13:15 UTC.

⚠️ **Gaps to address in this validation session:**
- Only 5 candidates in the DB; the historical 51 tier-3 + 67 tier-4 from packets back to 2026-04-20 are NOT yet in there. Sync them in.
- All 5 are unranked. Trigger the ranker so the Top-5 panel populates.
- Verify the dashboard drawer renders correctly with real data.
- Approve one test candidate end-to-end to prove the round-trip into Task Hub works.

❌ **Out of scope for this session (known, not blocking):**
- `memory/HEARTBEAT.md` has no directive telling Simone to act on approved `cody_scaffold_request` rows. So even after Approve creates a Task Hub row, Cody won't auto-build until that wiring lands. Documented in CLAUDE.md.
- The pacing module (`claude/csi-llm-pacing` branch) is parked unmerged. Not needed for this validation; only matters for heavy LLM backfills.

---

## 3. The plan — five phases, smallest to largest

### Phase 0 — Sanity (5 minutes)

**Goal:** Confirm you're in a fresh session and the deploy is live.

```bash
# Confirm gh works (proves you're in a NEW session post-launcher-fix)
echo "GH_TOKEN: ${GH_TOKEN:-unset}"
gh auth status
```

**Success:** `GH_TOKEN: unset`, `gh auth status` shows green file-stored OAuth.

**If GH_TOKEN is set:** You're in an OLD session. Exit, start a fresh one,
re-paste this doc.

```bash
# Confirm deploy
git log --oneline origin/main | head -3
```

**Success:** Top three are `67534779`, `5a3a936a`, and one earlier commit.

```bash
# Confirm gateway picked up new endpoints
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8002/api/v1/dashboard/claude-code-intel/triage
```

**Success:** Returns `401` (auth required). NOT `404` (endpoint missing) and
NOT connection refused.

---

### Phase 1 — See what's already there (2 minutes)

**Goal:** Look at the autonomously-populated triage DB. Confirm the
discovery hook is producing reasonable data.

```bash
python3 << 'EOF'
import sqlite3
c = sqlite3.connect('/opt/universal_agent/artifacts/proactive/claude_code_intel/demo_triage.db')
c.row_factory = sqlite3.Row
print("Counts:")
for r in c.execute('SELECT state, tier, COUNT(*) cnt FROM demo_triage_candidates GROUP BY state, tier'):
    print(f"  {r['state']:12s}  tier={r['tier']}  count={r['cnt']}")
print()
print("All candidates (newest first):")
for r in c.execute('SELECT post_id, handle, tier, action_type, first_seen_at, ranking_score, substr(post_text,1,90) snip FROM demo_triage_candidates ORDER BY first_seen_at DESC'):
    score = f"{r['ranking_score']:.1f}" if r['ranking_score'] else "—"
    print(f"  T{r['tier']} score={score:5s} {r['action_type']:25s} @{r['handle']:15s} {r['first_seen_at']}")
    print(f"     {r['snip']}")
EOF
```

**Success:** At least the 5 baseline candidates appear, all unranked.

**Note for the operator:** These came in autonomously from the most recent
cron — proof the discovery hook in `replay_packet` is firing. The dashboard
should already show them.

---

### Phase 2 — Sync the historical backlog (1 minute)

**Goal:** Get all ~118 historical tier 3+ candidates into the triage DB.
This is a pure DB operation — no LLM calls, no HTTP, no risk.

```bash
cd /opt/universal_agent
PYTHONPATH=src uv run python -c "
from pathlib import Path
import glob
from universal_agent.services.csi_demo_triage import sync_candidates_from_packet
total = {'inserted': 0, 'skipped': 0, 'packets': 0}
for p in sorted(glob.glob('/opt/universal_agent/artifacts/proactive/claude_code_intel/packets/*/*/actions_refined.json')):
    pdir = Path(p).parent
    r = sync_candidates_from_packet(packet_dir=pdir)
    total['inserted'] += r.get('inserted', 0)
    total['skipped'] += r.get('skipped', 0)
    total['packets'] += 1
print('TOTAL:', total)
"
```

**Success:** Roughly `{'inserted': ~110-118, 'skipped': ~5, 'packets': ~25-30}`.
The ~5 skipped are the candidates already in the DB from Phase 1.

Re-run the Phase 1 query — total should be ~118 across `state=pending`.

---

### Phase 3 — LLM rank everything (~30 seconds, single GLM call)

**Goal:** Run the LLM ranker so all candidates get scored 0-10 with a
rationale. This populates the "★ Top 5 Recommended" panel in the drawer.

We're in the off-peak window (US 12:00–17:00 CDT = China deep night), so
the call should return in 5-15 seconds with no throttling.

```bash
cd /opt/universal_agent
PYTHONPATH=src uv run python -m universal_agent.scripts.csi_demo_triage_rank
```

**Success:** Output shows JSON like
`{"candidates_scored": ~118, "candidates_skipped": 0, "error": null, ...}`.

Re-run the Phase 1 query — every row should now have a `ranking_score` and
the score column populated.

**If the ranker fails:** Check stderr. Most likely cause is the GLM call
returned malformed output or hit an unexpected throttle. Re-run; the script
is idempotent (only re-scores stale or unscored rows).

---

### Phase 4 — Verify the dashboard drawer (5 minutes, manual browser check)

**Goal:** Confirm the operator-facing UI works end-to-end with real data.

1. Open `https://<dashboard-host>/dashboard/claude-code-intel` in a browser.
2. Look at the header: there should be a **"Demo Triage"** button next to
   "Knowledge Search", with a count chip showing the pending total
   (~118 after Phase 2-3).
3. Click it. The right-side drawer (560px wide) should slide in.
4. Drawer should show:
   - **★ Top 5 Recommended** panel at top, with the 5 highest-scored cards.
     Each card has: score badge (e.g. "8.5/10"), tier badge ("Demo" or "Intel"),
     handle + relative date, summary text, "Why this score" expandable
     showing the LLM rationale, "Approve" button (primary), trash icon (right).
   - **All Candidates** section below, full chronological list, with filter
     pills (All / Cody (T3) / Atlas (T4)) and "Show resolved" toggle.
5. Verify the trash icon appears on hover (red tint).
6. Verify clicking a card body opens the source X post in a new tab (if linked).

**Success:** All 6 visual checks pass.

**If the drawer is empty or broken:** Hit the rerank button in the drawer
header (it calls `POST /triage/rerank`) — that fetches the latest data. If
still empty, check browser console for fetch errors and report the exact
error to the operator.

---

### Phase 5 — Approve one test candidate end-to-end (2 minutes)

**Goal:** Prove the Approve → Task Hub round-trip works.

Pick the highest-scored tier-3 candidate from the Top 5. Click **Approve**.

The drawer should:
- Optimistically remove the card from view
- Show a brief toast / confirmation
- Refresh; the card now appears under "Show resolved" with status `approved` and a Task Hub task_id link

Verify Task Hub got the row:

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('/opt/universal_agent/AGENT_RUN_WORKSPACES/task_hub.db')
for r in c.execute(\"SELECT task_id, source_kind, status, title, created_at FROM task_hub_items WHERE source_kind = 'cody_scaffold_request' ORDER BY created_at DESC LIMIT 3\"):
    print(r)
"
```

**Success:** At least one row with `source_kind='cody_scaffold_request'`,
`status='open'`, recent `created_at` timestamp, title matching the post you
approved.

**Note:** The task will sit `open` in Task Hub. **Cody won't auto-pick it up
until `memory/HEARTBEAT.md` gets the Phase 2 wiring directive added.** That's
a separate ticket — do NOT add it in this session. Just confirm the row
exists; that proves the triage→approval→queue path works.

---

## 4. Report back to operator

After Phase 5 succeeds, report to operator with:

1. **Phase results** — checkmark each phase that passed
2. **Final candidate counts** — pending / approved / dismissed by tier
3. **Top 5 list** — copy the post titles + scores so the operator can sanity-check the LLM ranking
4. **The one approved task_id** — so they can verify it in Task Hub if they want
5. **Any anomalies** — anything weird (low scores, missing fields, slow LLM calls)
6. **Recommended next step** — likely either: (a) wire HEARTBEAT.md so Cody picks up the approved task, or (b) approve a few more candidates to load test, or (c) tune the LLM ranking prompt if scores look off

---

## 5. If something blocks you

- **Phase 0 fails:** Don't try to fix the deploy. Stop and tell the operator.
- **Phase 2 fails (sync errors):** Inspect the failing packet's `actions_refined.json`. Most likely a malformed action object. Skip the bad packet and continue.
- **Phase 3 fails (LLM errors):** Try once more (off-peak should be reliable). If it still fails, check `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` are set and pointing at Z.AI. Don't fix Infisical from here.
- **Phase 4 fails (UI broken):** Don't dig into the React code. Take a screenshot, dump browser console, hand off to operator.
- **Phase 5 fails (no Task Hub row):** Check the Approve endpoint response payload — it should include `{ok: true, task_id: '...'}`. If `ok: false`, the reason is in the response.

---

## 6. Where things live (reference)

**New code (read if you need to debug):**
- `src/universal_agent/services/csi_demo_triage.py` — DB + candidate ops
- `src/universal_agent/services/csi_demo_triage_ranker.py` — LLM scoring
- `src/universal_agent/scripts/csi_demo_triage_rank.py` — cron CLI entry
- `src/universal_agent/services/claude_code_intel.py` — `_build_followup_task_payload` shared helper
- `src/universal_agent/services/claude_code_intel_replay.py` — discovery hook
- `src/universal_agent/gateway_server.py` — cron registration + 5 endpoints
- `web-ui/app/dashboard/claude-code-intel/page.tsx` — drawer + TriageCard

**Tests (16 + 11 = 27 tests, all passing):**
- `tests/unit/test_csi_demo_triage.py`
- `tests/unit/test_csi_demo_triage_ranker.py`
- `tests/unit/test_claude_launcher_strip.py`

**Endpoints (all under `/api/v1/dashboard/claude-code-intel/`):**
- `GET /triage` → counts + top5 + all
- `POST /triage/{post_id}/approve` → enqueues, returns refreshed
- `POST /triage/{post_id}/dismiss` → marks dismissed, returns refreshed
- `POST /triage/{post_id}/restore` → un-dismiss, returns refreshed
- `POST /triage/rerank` → triggers ranker, returns refreshed

**Data:**
- Triage DB: `artifacts/proactive/claude_code_intel/demo_triage.db`
- Source packets: `artifacts/proactive/claude_code_intel/packets/<YYYY-MM-DD>/<HHMMSS>__<handle>/`
- Task Hub: `AGENT_RUN_WORKSPACES/task_hub.db`
- Cron registry: `AGENT_RUN_WORKSPACES/cron_jobs.json`

---

## 7. Operator preferences (Kevin)

- Wants demos to go through human approval, NOT auto-queue
- Wants the flyout panel ON the existing intel tab (not a separate page)
- Wants two panels: top-5 LLM-recommended + full chronological list
- Wants delete/trash icon on every candidate, hover-revealed
- Wants SQLite (not JSON) so we can analyze the corpus over time
- Wants both tier 3 (Cody/demos) and tier 4 (Atlas/intel) in the drawer with separate sections
- Wants to test small first before doing anything heavy
- Off-peak window (US 12:00–17:00 CDT) is the right time for any LLM-bound work
- Doesn't want pedantic step-by-step narration — wants to see results

---

End of plan. Phases 0-5 should take ~15 minutes total. Execute in order,
report results.

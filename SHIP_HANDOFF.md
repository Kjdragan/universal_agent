# SHIP_HANDOFF

**Status:** Skill-gap remediation cycle shipped 2026-07-11 (see below). Prior: Phase 2 producer shipped 2026-05-06.

---

## Latest cycle: skill-gap finder remediation (2026-07-11 weekly report)

Branch `claude/skill-gap-remedies`. Executes the operator-approved remedies for the 2026-07-11 "[UA skill-gap] 8 candidate(s)" report:

**What changed for the system:**
- `services/nlm_resume_check.py` (NEW) — deterministic `.nlm_resume.json` adopt-vs-fresh verdict for the paper-to-podcast cron; `_paper_to_podcast_command` + skill Phase B.0 now tell the agent to run it and obey, instead of interpreting checkpoint prose (candidate #7, 16x). Tests: `tests/unit/test_nlm_resume_check.py` (5 green).
- `hooks.py` heredoc-guard denial now steers to `write_text_file` + `work_products/` — it used to recommend the native `Write` tool that the heartbeat File Write Rules forbid, a contradiction behind the 113x repeat violations (candidate #6).
- Both workspace path-guard denials (`hooks.py`, `guardrails/workspace_guard.py`) now teach where TO write (candidates #3/#4).
- `heartbeat_service.py` File Write Rules gain two standing lines: no orient.py / no /tmp scripts (orientation = self-brief-and-attest), and UA state DB queries go through the `activity-state-db-inspector` skill (candidates #1/#3/#4).
- `.claude/skills/activity-state-db-inspector/` (NEW, mirror; canonical in dragan-plugins) — read-only `asdb.py` CLI over `activity_state.db` + `coder_vp_state.db`; documents that `vp_missions` lives in `coder_vp_state.db` (candidates #1+#5, 47x).
- `skill_gap_finder.py` — candidates now carry a `remedy` field (`skill|code-fix|prompt-fix|error-message-fix|already-automated`), and the SYSTEM prompt flags machine-templated prompts as automation, not operator toil (the "Run research pass N" 50x false positive, candidate #8).

**Not done, deliberately:** candidate #2 (arxiv backoff) — already solved by arxiv-mcp-server routing; candidate #8 needs no build (the loop exists as `daily-eight-research-loop`).

**Verification honesty (8 rules):** unit tests + CI-scope ruff green locally; skill-deployed ≠ skill-invoked — the asdb mirror and the resume verdict have NOT yet been exercised by a live Simone/cron run. First real evidence arrives with the next paper-to-podcast cron run (verdict line in its transcript) and the next heartbeat that queries state DBs. `nlm_resume_check` was smoke-run on the VPS against a synthetic checkpoint pre-merge.

---

## Latest cycle: Phase 2 producer change

**SHA:** `5682fc5` — feat(csi): tier-3 actions enqueue cody_scaffold_request, not direct demo task
**Plus:** `f38075d` — docs: add Pre-Implementation Reading rules to CLAUDE.md

**What changed for the system:**
- `claude_code_intel.queue_follow_up_tasks` writes `cody_scaffold_request` (not `claude_code_demo_task`) for tier 3
- Task Hub's existing `dispatch_sweep` + Simone-first routing claim and route automatically — no HEARTBEAT.md edits needed
- `intended_task_identity` in replay updated to mirror the new routing (parallel-constant lockstep)
- 3 new tests + 1 updated existing test, 67/67 CSI tests green
- `UA_CSI_DIRECT_DEMO_FALLBACK=1` is the emergency lever to re-enable the legacy direct-to-Cody enqueue if Simone's scaffold pipeline is broken; off by default

**Operator-visible impact:** the next tier-3 CSI fire produces a `cody_scaffold_request` row visible in Mission Control, Simone's next heartbeat claims it, runs cody-scaffold-builder, and a new `/opt/ua_demos/<entity-slug>__<short-id>/` workspace appears.

---

## Smoke test plan (Phase 2 → Phase 3 first end-to-end run)

**Sequence:** wait ~5 min for deploy, trigger a manual CSI fire, wait ~30 min for Simone's heartbeat, then check three places.

**Step 1 — manual CSI fire:**
```
ssh ua@uaonvps 'curl -s -X POST http://localhost:8002/api/v1/cron/jobs/claude_code_intel_sync/run | python3 -m json.tool'
```

**Step 2 — verify scaffold request landed in Task Hub:**
```
ssh ua@uaonvps 'sqlite3 /opt/universal_agent/AGENT_RUN_WORKSPACES/task_hub.db "SELECT task_id, status, datetime(created_at, '\''unixepoch'\'') AS created, title FROM task_hub_items WHERE source_kind = '\''cody_scaffold_request'\'' ORDER BY created_at DESC LIMIT 10;"'
```

Expected: at least one row with status=`open` (or `in_progress` if Simone has already claimed) and a "Scaffold demo: ..." title.

**Step 3 — after Simone's next heartbeat (~30 min cadence), check `/opt/ua_demos/`:**
```
ssh ua@uaonvps 'ls -la /opt/ua_demos/ && find /opt/ua_demos -maxdepth 2 -name "manifest.json" -o -name "BRIEF.md" 2>/dev/null'
```

Expected: a new directory (other than `_smoke`) containing BRIEF.md, ACCEPTANCE.md, business_relevance.md, SOURCES/. This is **the artifact that proves Phase 2/3 is end-to-end functional** per CLAUDE.md verification rule #2.

**If Step 3 still shows only `_smoke` after 60 min:** the producer is working but Simone isn't claiming. Check her recent heartbeat session for the cody_scaffold_request task and any error. Likely paths to investigate: skill invocation failure, vault entity not found for the post_id, or task got claimed but skill threw.

---

## Scheduled-check (run-this-in-an-hour)

Schedule a one-shot smoke check 1 hour from now so you don't have to remember:

```
ssh ua@uaonvps 'cat > /tmp/csi_smoke.sh <<"BASH"
#!/bin/bash
exec > /tmp/csi_smoke_result.log 2>&1
echo "=== Phase 2 smoke test starting at $(date -u) ==="
echo
echo "=== Trigger manual CSI fire ==="
curl -s -X POST http://localhost:8002/api/v1/cron/jobs/claude_code_intel_sync/run | python3 -m json.tool
echo
echo "=== Wait 30 min for Simone heartbeat ==="
sleep 1800
echo
echo "=== Task Hub: cody_scaffold_request rows ==="
sqlite3 /opt/universal_agent/AGENT_RUN_WORKSPACES/task_hub.db "SELECT task_id, status, datetime(created_at, '\''unixepoch'\'') AS created, title FROM task_hub_items WHERE source_kind = '\''cody_scaffold_request'\'' ORDER BY created_at DESC LIMIT 10;"
echo
echo "=== /opt/ua_demos/ listing ==="
ls -la /opt/ua_demos/
echo
echo "=== BRIEF/ACCEPTANCE files in any new workspace ==="
find /opt/ua_demos -maxdepth 2 -name "BRIEF.md" -o -name "ACCEPTANCE.md" -o -name "manifest.json" 2>/dev/null
echo
echo "=== Done at $(date -u) ==="
BASH
chmod +x /tmp/csi_smoke.sh
nohup bash -c "sleep 3600 && /tmp/csi_smoke.sh" >/tmp/csi_smoke.bg.log 2>&1 &
echo "Scheduled. Output will appear at /tmp/csi_smoke_result.log on VPS in ~1 hour 30 min."'
```

**To pull the result tomorrow morning:**
```
ssh ua@uaonvps 'cat /tmp/csi_smoke_result.log'
```

If Step 2 shows scaffold rows AND Step 3 shows a new `/opt/ua_demos/<entity-slug>__*/` directory with BRIEF.md, the v2 system is end-to-end on production for the first time.

---

## Earlier in this branch (already shipped via prior /ship runs)

- `c312ea8`, `7e2aa71`, `0b22877` — Production Verification Rules in CLAUDE.md (with corrections)
- `f38075d` — Pre-Implementation Reading rules in CLAUDE.md
- `185e552` — trust_source bypass + claude.com allowlist widening
- `09d7ee2` — `catch_up_on_restart=True` for claude_code_intel_sync
- `6dc6f51`, `20bf032` — YouTube digest proxy retry + stale test cleanup
- `cc14d94` — 22:00 Central poll added to claude_code_intel_sync cron

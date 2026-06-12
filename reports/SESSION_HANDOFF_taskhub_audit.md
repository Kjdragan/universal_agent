# SESSION HANDOFF — UA Task-Hub Audit + Documentation Reconciliation

**Purpose:** let a FRESH session continue this investigation without the bloated context of the originating session. Self-contained. Written 2026-06-06.

---

## 0. What the operator (Kevin) actually wants

1. **Audit each Task Hub task type end-to-end** — confirm whether each process (VP missions, cron, CSI, convergence→digest, …) works as intended.
2. **A single canonical reference** so he stops getting lost about *which* system is the right one, and so that when an old system is turned off he can reconstruct *why* and *what replaced it* — not be misled by dead code.
3. **Confirmation of where the system stands — NOT unrequested changes.** Verify-don't-mutate. Operator-gate anything destructive or architectural. (He has said this repeatedly; honor it.)

He is (rightly) sensitive to **hallucinated / stale claims** — the originating session produced a wrong "registry" by reading a stale checkout. Every claim must be verified against ground truth before being stated.

---

## 1. HARD-WON ENVIRONMENT RULES — READ FIRST (these caused the session's one big mistake)

- **THE STALE-CHECKOUT TRAP (root cause of the session's error):** the bare `~/lrepos/universal_agent` checkout is a **divergent local branch ~59 commits behind `origin/main`, missing whole subsystems** (it predated the entire S5 systemd-scheduling migration). An inventory run against it produced a registry that mislabeled live systems as dead. **ALWAYS work from a fresh worktree off `origin/main`** (`git fetch origin && git worktree add --detach <path> origin/main`), never the bare checkout. Deployed prod = `origin/main`.
- **When hunting "who does X?" and `grep src/` is empty, search `.claude/skills/**/SKILL.md` + `.claude/agents/*.md`** — the actor is often an LLM following an instruction (the Phase-D orphan-DB bug was a skill placeholder path, not code).
- **Gateway read-API (no SSH needed):** `http://uaonvps:8002` (plain HTTP; curl needs `dangerouslyDisableSandbox:true`). Useful open routes: `/api/v1/version` (`commit_sha` = deploy truth, NOT `branch`), `/api/v1/cron/jobs`, `/api/v1/dashboard/mission-control/{cards,tiles,ledger}`, `/api/v1/dashboard/todolist/tasks/{id}[/history|/failure-context]`, `/api/files/{session_root}/{path}`, `/briefs/{id}`. `/api/v1/ops/*` → 401 (skip). For workspace files: `session_id=vp_general_primary_external` or `vp_coder_primary_external`, path relative to `AGENT_RUN_WORKSPACES`.
- **CI gate (`pr-validate.yml`), changed-files only:** `py_compile` + `ruff check --select E9,F --ignore E402,F401,F541,F811,F841` + `check_test_date_literals.py` + `pytest tests/unit -x`. F841/F401/import-sort/format are NOT gated. `tests/gateway/` NOT gated.
- **`claude/*` branches AUTO-MERGE on green CI and a merge to `main` AUTO-DEPLOYS** (gateway restart, ~19×/day). **Open work as `--draft`** to hold for review. `kevin/*`/`feature/*` = manual-merge. `deploy.yml` `paths-ignore: docs/**, **.md` — a docs/skills-only change does NOT deploy (a `.json` like the doc manifest DOES).
- **Full `tests/unit` HANGS locally** on `test_preference_gate_scoping` at `conn.commit()` (live gateway holds `activity_state.db`). Use `UA_ACTIVITY_DB_PATH=<tmpfile>` + targeted test sets. CI's clean runner is fine.
- **First `uv run` in a fresh worktree builds a per-worktree `.venv` (~10 min).** Budget for it.
- **`git add -A` in a worktree sweeps harness memory files** (`MEMORY.md`, `memory/*`) — stage explicit paths only.

---

## 2. GROUND-TRUTH LOCATIONS (the authoritative sources)

| What | Where |
|---|---|
| Clean origin/main code (=prod) | worktree `/home/kjdragan/lrepos/universal_agent-wt-truth` (`git fetch` + reset to origin/main before trusting) |
| **Scheduling SOURCE OF TRUTH (design)** | `project_docs/06_platform/08_scheduling_substrate_adr.md` (the S5 ADR — status `active`, 5 decisions, 4 phases, 9 operator decision points, with honest per-phase "As-built" notes) |
| **Migrated-job SOURCE OF TRUTH (machine)** | `src/universal_agent/systemd_migrated_jobs.py::SYSTEMD_MIGRATED_SYSTEM_JOBS` (the 20-job frozenset) + `is_migrated_to_systemd()` |
| Overhaul narrative + handoffs | `/home/kjdragan/health_system_report_2026-06-04/` — `*.html` reports + `handoffs/*.md` (S1–S5, FU1, PhaseA_batch1/2, PhaseB/C/D, HOLISTIC_REVIEW). **`HOLISTIC_REVIEW_findings.html` is the most current/trustworthy doc.** |
| Per-phase Claude session transcripts | `~/.claude/projects/-home-kjdragan-lrepos-universal-agent--claude-worktrees-<name>/*.jsonl` (e.g. `phaseb-mc-sweeper-service`, `phasec-health-timer`, `phaseD-canonical-store`, `s5-scheduling-backbone-adr`, `repair-systemd-timers`=S2, `unfreeze-cos-readout`=S3, `remove-sched`=S4, `csi-threads-infisical-sync-loader`=FU1). Mine summaries/decisions, don't dump. |
| Live prod | `http://uaonvps:8002` (see §1) |

---

## 3. ESTABLISHED FACTS (verified this session against origin/main + live prod)

- **The 2026-06-04→06 "Scheduling/Health Overhaul" is REAL and accurately documented — NOT hallucinated.** Every cited PR (#726–#766) is in `origin/main` and matches code per-symbol; live prod corroborates. Numbers are measured (17–49% fire-loss, 18 orphan briefs, 5/7 false alarms). Only flaw = staleness (project-record HTML + scheduling map predate batches 3/4 which shipped via #759/#762); docs UNDER-report progress. Full assessment: `reports/ua-overhaul-assessment.md`.
- **As-built scheduling (current):** 5 schedulers (asyncio heartbeat, in-process gateway cron remnant, systemd timers, MC sweeper service, GitHub Actions). **20 jobs migrated** to 25 `OnCalendar`+`Persistent` systemd timers (in-process row force-`enabled=False` = the timer is sole firer; `enabled=False` in `/api/v1/cron/jobs` means MIGRATED+RUNNING, not broken). MC sweeper + proactive-health are their own systemd services/timers. VP workers run as `universal-agent-vp-worker@{vp.general,vp.coder}.primary` template services. Live monitoring: 9/9 green tiles, false-alarm class retired.
- **Two task types audited end-to-end, both HEALTHY:** **Atlas** (`vp.general.primary`, intel-brief authoring) — worked; plumbing fixes shipped. **Cody** (`vp.coder.primary`, code-building) — worked (model run → real PR #767, /goal attestation, red-green TDD, populated `run.log`, real Anthropic cost, correct `completed_with_pr`). Reports: `reports/vp-mission-audit-handoff.md`, `reports/cody-mission-audit.md`.

---

## 4. WORK PRODUCTS & PR STATE

| PR / artifact | State | Notes |
|---|---|---|
| **#770** observability/cost/skill-fidelity | **MERGED + DEPLOYED** (`aa28c57e`) | built in a worktree off origin/main, verified, correct |
| **#771** lease-based reconciliation (false-orphan→dup-run fix, agent-agnostic Atlas+Cody) | **MERGED + DEPLOYED** (`b04dd02c`) | 75 tests incl. Cody cases |
| **#772** Task Type & Mission System Registry (`project_docs/01_architecture/07_task_type_registry.md`) | **DRAFT — DO NOT MERGE YET** | corrected against origin/main (15 re-verification fixes, doc_audit 0/0), branch merged up to current main. **OPEN DECISION — see §5.** |
| **#767** CODIE cleanup (`exc_info=True` ×7 + red-green test, +84/−6) | **OPEN, not draft, clean** | safe to merge; `codie/*` needs operator click. Operator's call. |
| Reports | written | `reports/{vp-mission-audit-handoff,cody-mission-audit,ua-overhaul-assessment,SESSION_HANDOFF_taskhub_audit}.md` |
| Worktrees created | live | `universal_agent-wt-vpfixes` (#770), `-wt-reconcile` (#771), `-wt-docs` (#772 branch `claude/task-type-registry-doc`), `-wt-truth` (read-only origin/main) |

---

## 5. THE KEY OPEN DECISION — PR #772 (operator-gated)

The registry (#772) overlaps the **already-canonical** ADR + scheduling map + `systemd_migrated_jobs.py` frozenset. Merging it as-is risks a **second source of truth that drifts within hours** (re-enumerating job lists is exactly how the project-record/map went stale in a day). **Recommendation: do NOT merge #772 as a parallel inventory.** Instead, one of:
- **(A)** Revise #772 so its scheduling section holds only POINTERS (cite the ADR + the frozenset + live `/api/v1/cron/jobs`) and owns only task-type *semantics* not expressed elsewhere; then merge.
- **(B)** Fold #772's genuinely-unique content into existing canonical docs (ADR / `02_task_hub.md` / `03_agents/01_vp_workers_and_delegation.md`) and close #772.
- **(C)** Keep #772 as a thin "index of task types → owning canonical doc" map only.

A fresh session should diff #772 and pick A/B/C **with operator approval** before any merge. Do NOT let it auto-merge (keep draft).

---

## 6. OPEN ITEMS (operator-gated / deferred — confirm, don't auto-do)

1. **18-brief recovery (Phase D deferral).** #756 shipped the root-cause fix but deferred recovering the 18 orphan-DB intel briefs (requires a write to the fenced 2.5GB live `activity_state.db`; idempotent via `make_artifact_id`). Orphan DBs snapshotted at `/home/ua/phaseD_orphan_snapshots_2026-06-05/`. The holistic worklog hints at a 103→122 backfill — **confirm whether recovery is done or still pending** before acting. Operator-gated.
2. **Stale snapshot docs.** `UA_overhaul_project_record.html` + `scheduling_substrate_map.html` say batches 3/4 pending — they shipped. Add a "SUPERSEDED — see HOLISTIC_REVIEW_findings.html" pointer, or refresh (20 migrated, not 11).
3. **Cody audit minor findings (not bugs):** `completed_without_pr` disposition is overloaded (success-no-PR like tutorial-repo builds vs real failure); CLI receipt telemetry partial (`tool_calls:0`, incomplete iterations). Candidate fixes if operator wants.
4. **Lingering `feature/latest2` ref** in `services/dependency_upgrade.py` (stale default; cosmetic).
5. **Next task types to audit** (campaign continues): cron/scheduled (now well-understood — see ADR), CSI ingestion, convergence→intel-brief→digest end-to-end delivery.

---

## 7. UNVERIFIABLE FROM THE READ-API (don't assert as fact)
Live `systemctl list-timers` / `journalctl` / SES `message_id` / exact "19 deploys/day" — the read-API can't reach these; `/ops/*` is 401. They rest on the docs' own ssh-probe evidence (internally consistent). Need Channel-3 SSH (`ssh ua@uaonvps`, operator must add the allow-rule) to verify directly.

---

## 8. AGENT MEMORY POINTERS (already saved, in `~/.claude/projects/-home-kjdragan-lrepos-contact-form-triage/memory/`)
`reference_ua_stale_checkout_trap`, `reference_ua_dev_workflow`, `reference_ua_vps_tailnet_access`, `project_ua_taskhub_audit`, `project_ua_intel_digest_delivery`, `feedback_automate_before_asking`, `feedback_autonomous_planning_first`.

**Bottom line for the next session:** the overhaul is real and landed; the ADR + frozenset are the scheduling source of truth; the system is healthy; the one live decision is how to reconcile #772 (don't merge a parallel inventory); everything else is operator-gated confirmation, not changes.

# Task-Hub Audit — Handoff & Reusable Process

**Date:** 2026-06-06 · **Auditor:** Claude Code (Opus 4.8) · **Scope so far:** one **VP mission** (ATLAS intel-brief authoring)
**Next up:** a different task type (recommended: a **Cody** code-building mission — see §6)

---

## 1. What was audited

VP mission `vp-mission-e646f5f51802bf658d3aa789` — ATLAS (`vp.general.primary`) authoring an intel brief for convergence candidate `cand_6d11787ce2116856` via `/evaluate-and-author-intel-brief`.

**Verdict: the mission worked end-to-end.** Brief authored, copied to durable storage (`/opt/universal_agent/artifacts/intel/pa_0dd017c0e526ea8f.html`), served live at `/briefs/pa_0dd017c0e526ea8f` (HTTP 200), DB consistent (candidate + `proactive_artifacts` rows), Task Hub item closed, no email sent (correct). One clean 182s turn, no tool errors. The problems were all in the **plumbing around** the mission, not the agent/skill logic.

## 2. Fixes shipped (two DRAFT PRs — hold for review; do NOT mark ready until reviewed → they auto-merge + deploy)

- **PR #770** — `claude/vp-mission-audit-fixes` — observability + telemetry + skill-fidelity bundle (8 low-risk fixes). 323 targeted regression tests pass.
  - #5 trace_id null (import-time Logfire gate vs runtime token) · #7 empty `run.log` link → `transcript.md`/`trace.json` fallback · #4 budget-alarm relabel (`context_window_warned_*`) · #8 ZAI cost "est." label · #6 narrative-provenance disclosure in the skill · #9 persist authoring/triage model · #10 `UA_GATEWAY_BASE_URL` default + doc · #12 ledger artifact-link evidence.
- **PR #771** — `claude/vp-reconciliation-liveness` — the **deep** fix: lease-based reconciliation. 63 + 12 reconcile tests pass.
  - Stops the gateway-startup reconciler from **false-orphaning healthy VP missions** (which caused confirmed **duplicate ATLAS runs**). Persists a live handle at mission start; teaches `reconcile_task_lifecycle` to honor the `vp_missions` `status='running'` + future `claim_expires_at` lease; makes convergence re-dispatch idempotent. **Agent-agnostic — protects both Atlas and Cody.**

## 3. Findings that turned out WRONG on source inspection (the #1 lesson)

The audit was built from **runtime traces** (run.log, transcript, trace.json, DB rows via the gateway read-API). Two findings did **not** survive source verification:

- **"Delivery via digest never happened" (was HIGH) — FALSE.** `hourly_intel_digest.select_candidates_for_current_hour` already reads `verdict='ship' AND artifact_type='intel_brief' AND delivered_at IS NULL` from `proactive_artifacts`, deployed as a systemd timer (#673). My "zero digests" was me grepping the **CSI digest** channel; delivery is a **separate AgentMail email**. *Open operational check only:* is the timer enabled on prod, and was `verdict='ship'` actually persisted on the row.
- **"Feedback URLs dead" (#10) — already mitigated.** PR #671 added a send-time backstop that rewrites feedback URLs from a hardcoded default. Residual was just the authoring side storing empties (one-line skill default).

> **Lesson → bake into every future audit:** a runtime symptom is a *hypothesis*, not a bug. Confirm against `main` source before fixing. The recon phase (read-only agents that locate + verify each issue) caught both of these and saved wasted work.

## 4. The reusable audit → fix PROCESS (what worked)

1. **Reach prod read-only** via the gateway API over the tailnet: `http://uaonvps:8002` (no SSH). Key open routes: `/api/v1/dashboard/todolist/tasks/{id}[/history|/failure-context|/goal-artifacts]`, `/api/files/{session_root}/{path}`, `/api/artifacts/files/{path}`, `/briefs/{id}`, `/api/v1/dashboard/mission-control/{cards,ledger}`, `/csi/digests`. `/api/v1/ops/*` → 401 (needs `UA_OPS_TOKEN`). For the file API, `session_id=vp_general_primary_external` + path relative to `AGENT_RUN_WORKSPACES`.
2. **Forensics fan-out (Workflow):** parallel agents over distinct dimensions (execution/trace, delivery, systemic-pattern, output-quality) → structured findings → synthesized report. The **systemic** agent (sampling *other* missions) is what proved the reconciler bug was platform-wide, not a one-off.
3. **Recon-before-fix (Workflow):** one read-only agent per issue cluster — *confirm the issue on `main`*, locate exact file:line, blueprint the minimal fix, assign a risk tier (safe/moderate/deep).
4. **Implement:** isolated **git worktree per PR** off `origin/main`; parallel editor agents on **disjoint file sets** (no merge conflicts); the deep fix in its own worktree/PR.
5. **Verify locally with the EXACT CI gates** (see §5), then **draft PR** so nothing auto-deploys.

## 5. Environment gotchas (will bite the next audit — read first)

- **CI gate = `pr-validate.yml`:** `py_compile` (changed files) + `ruff check --select E9,F --ignore E402,F401,F541,F811,F841` (changed files only) + `check_test_date_literals.py` + `pytest tests/unit -x` + shellcheck. **F841/F401/import-sort are NOT gated** — don't chase them. Format (`ruff format`) is **not** gated either (don't reformat whole files → huge diffs).
- **`claude/*` branches auto-merge** once non-draft + CI green, and a merge to `main` **auto-deploys** (gateway restart). **Always open as `--draft`** to hold for review; `kevin/*`/`feature/*` are the manual-merge prefixes.
- **Full `tests/unit` hangs locally** on `test_preference_gate_scoping.py::test_gate_ignores_implicit_park_burst` at `conn.commit()` — pre-existing, documented in `pyproject.toml`; it contends with the **live gateway** holding `activity_state.db` on this machine. CI's clean runner doesn't hit it. Workaround: `UA_ACTIVITY_DB_PATH=<tmp>` + run **targeted** test sets per changed module instead of the whole suite.
- **First `uv run` in a fresh worktree builds a per-worktree `.venv` (~10 min).** Budget for it; subsequent runs are fast.
- **`git add -A` in a worktree sweeps harness memory files** (`MEMORY.md`, `memory/*`) — stage explicit paths only.

## 6. Next audit: Cody (`vp.coder.primary`) — and why it's genuinely different

Kevin's steer: **Cody ≠ Atlas.** Cody is the code-building VP; they share `VpWorkerLoop` + the `vp_missions` lease (so PR #771's reconciler fix already covers Cody), but they differ where it matters for an audit:

| Dimension | Atlas (`vp.general.primary`) | Cody (`vp.coder.primary`) |
|---|---|---|
| Output of success | an artifact (HTML brief, no PR) | **a GitHub PR** |
| `terminal_disposition=completed_without_pr` | ✅ success | ⚠️ **likely a FAILURE signal** — a code mission that produced no PR |
| Execution path | SDK `ProcessTurnAdapter` (generalist) | **CLI mode** (claude-code); has the `is_coder`/`is_cli_mode` dispatch guard |
| `run.log` | 0 bytes (SDK path) | **populated** (#698 wired the Activity panel from it) |
| trace_id | fixed by PR #770 (SDK path) | **separate** — not covered by PR #770; verify independently |
| Source of work | convergence candidates | code tasks / `codie/*` PRs / `cody_demo_task` |

**What to check when auditing a Cody mission:** did it open a PR (and is the PR URL captured — `worker_loop.py:873`/`890` extract it via `proactive_codie._GITHUB_PR_RE`)? did CI pass on that PR? is `terminal_disposition` correctly `completed_with_pr` (and is `completed_without_pr` being treated as the failure it probably is)? does the cost/observability surface (`/dashboard/metrics/coder-vp`) read correctly? Start the same way: pull the card's task record + workspace files via the gateway, then forensics fan-out.

## 7. Open follow-ups (NOT in the two PRs — need a decision or are out of scope)

- **#11 dispatch latency (~5.5 min queue on a priority-1 mission):** root cause is **worker serialization** — one mission per `VpWorkerLoop` (`UA_VP_MAX_CONCURRENT_MISSIONS=1`), not poll cadence. **Needs Kevin's call:** run a 2nd `vp-worker@vp.general.primary` instance (deploy-config, low risk) vs. a concurrent-`_tick` refactor (deep). Not safe to pick unilaterally.
- **CSI Ingester `batch_brief` degraded-drop:** on LLM failure it emits a plain-summary and marks events delivered — a real defect, but a **separate package** (`CSI_Ingester/`) and never the intel-brief channel. Separate ticket.
- **#13 "Codeex" vs "Codex":** upstream **ingestion** transcription error in the source `key_claims`, not agent/code. Fix in ingestion, not here.
- **Worker-loop handle-write integration test (#2):** deferred — exercising `_execute_mission_logic`'s start path needs heavy setup; the reconciler tests cover the consuming side.

---
*Live artifacts: PR [#770](https://github.com/Kjdragan/universal_agent/pull/770), PR [#771](https://github.com/Kjdragan/universal_agent/pull/771). Branches off `origin/main` @ 9b918e6b.*

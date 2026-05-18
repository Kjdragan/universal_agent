# Architecture Canvas View

> **Status:** v1 shipping 2026-05-17 (Hero + 2 satellites). Full 9-exhibit plan tracked here so phases aren't lost.
> **Owner:** Kevin (operator), Claude (build & maintenance).
> **Surface:** Single self-contained HTML at `docs/architecture-view/output/architecture-map.html`, also copied to `web-ui/public/architecture-map.html` for dashboard linking.

## 1. Why this exists

UA has ~25 active subsystems documented across 200+ markdown files. The canonical *prose* view is [`docs/01_Architecture/000_PIPELINE_MASTERPIECE.md`](../01_Architecture/000_PIPELINE_MASTERPIECE.md). The canonical *runtime-state* view is Mission Control. Neither answers the question:

> _"Show me how the pieces actually fit together, in one picture I can explore in a window."_

The Architecture Canvas is that picture. It is **not** a dashboard (no live data), **not** a docs replacement (it links to docs), **not** a code browser (it links to code). It is a **navigable architecture map** with the following non-negotiables:

1. **Beautiful** — Excalidraw-style hero diagrams (via `rough.js`), Figma-canvas-feel widescreen layout, dense exhibit grid.
2. **Anti-rot** — every box has a `source:` pointer to a file/dir/doc. A build script verifies pointers exist, captures git-last-touched dates, and renders a freshness badge inline. Stale pointers fail the pre-commit hook.
3. **Portable** — single self-contained HTML file. Drag into any browser, works offline, share with anyone.
4. **Linkable** — also served at `/architecture-map.html` so Mission Control and the dashboard can link to it.

## 2. Locked design decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Gap addressed** | Architecture map | Mission Control = state; mkdocs = prose; gap = visual relationships |
| **Scope** | Tiered: overview + drill-downs | 25 subsystems can't fit one diagram readably |
| **Source of truth** | Hybrid — hand-authored boxes + verified `source:` pointers | Pure-generated misses intent; pure-authored rots |
| **Renderer split** | Excalidraw aesthetic via `rough.js` for hero; Mermaid for drill-downs | Hero is low-churn (quarterly), drill-downs are high-churn (per-PR) |
| **Mental model** | Flow-spine + constellation overlay | Captures narrative AND inventory |
| **Chassis** | HTML/CSS grid with embedded panels | True Figma-canvas feel; each exhibit independently editable |
| **Hosting** | Single self-contained HTML + dashboard link | Portable AND integrated |
| **Drill-down UX** | Right-rail side drawer (~40% width) | Canvas stays visible; spatial orientation preserved |
| **Build pipeline** | Python script + pre-commit + weekly cron + visible freshness badges | Anti-rot promise enforceable |
| **Freshness thresholds** | Green <30d, amber 30–90d, red >90d or missing | Matches typical PR cadence |

## 3. Exhibit inventory (full 9-exhibit plan)

```
┌─────────────────────────────────────────────────────────────────┐
│  E1 — TASK FLOW-SPINE (hero, rough.js)                          │
│  Ingress → Task Hub → Simone → VPs/Cody → Completion/Reflection │
├──────────────────────┬──────────────────────┬───────────────────┤
│  E2 — INTELLIGENCE   │  E3 — KNOWLEDGE      │  E4 — CLAUDE ENVS │
│  (CSI pipeline,      │  (LLM Wiki, Memory,  │  (ZAI / Max /     │
│   Mermaid)           │   Vault, Mermaid)    │   Cody per-task)  │
├──────────────────────┼──────────────────────┼───────────────────┤
│  E5 — INPUTS         │  E6 — OPERATING      │  E7 — OPS / SHIP  │
│  (email, discord,    │  SURFACES (Mission   │  (branches, CI,   │
│   webhooks, cron)    │  Control, dashboard) │  deploy, Infisical│
├──────────────────────┴──────────────────────┴───────────────────┤
│  E8 — GLOSSARY                     E9 — LEGEND & FRESHNESS      │
└─────────────────────────────────────────────────────────────────┘
```

| # | Exhibit | Renderer | Anchor source | Anchor canonical doc |
|---|---|---|---|---|
| E1 | Task Flow-Spine | rough.js + SVG | `src/universal_agent/task_hub.py` | [`01_Architecture/000_PIPELINE_MASTERPIECE.md`](../01_Architecture/000_PIPELINE_MASTERPIECE.md) |
| E2 | Intelligence Pipeline (CSI) | Mermaid | `src/universal_agent/services/claude_code_intel.py` | [`02_Subsystems/ClaudeDevs_X_Intelligence_System.md`](ClaudeDevs_X_Intelligence_System.md) |
| E3 | Knowledge Plane (Wiki + Memory) | Mermaid | `src/universal_agent/memory/` | [`02_Subsystems/LLM_Wiki_System.md`](LLM_Wiki_System.md), [`02_Subsystems/Memory_System.md`](Memory_System.md) |
| E4 | Claude Execution Environments | HTML banded panel | `src/universal_agent/services/cody_mode.py` | [`06_Deployment_And_Environments/10_Interactive_Coding_Environment.md`](../06_Deployment_And_Environments/10_Interactive_Coding_Environment.md) |
| E5 | Inputs Catalog | HTML badge grid | `src/universal_agent/hooks_service.py`, `agentmail_official.py` | [`01_Architecture/000_PIPELINE_MASTERPIECE.md`](../01_Architecture/000_PIPELINE_MASTERPIECE.md) §2 |
| E6 | Operating Surfaces | HTML list | `web-ui/`, `src/universal_agent/gateway_server.py` | [`02_Subsystems/Task_Hub_Dashboard.md`](Task_Hub_Dashboard.md), [`02_Subsystems/Mission_Control_Intelligence_System.md`](Mission_Control_Intelligence_System.md) |
| E7 | Ops / Ship Pipeline | Mermaid | `.github/workflows/deploy.yml`, `pr-validate.yml` | [`deployment/ci_cd_pipeline.md`](../deployment/ci_cd_pipeline.md), [`06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md`](../06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md) |
| E8 | Glossary | HTML rail | Distilled terms from `docs/Glossary.md` and `CLAUDE.md` | [`docs/Glossary.md`](../Glossary.md) |
| E9 | Legend & Freshness | HTML rail | Build script self-reports | (this doc) |

## 4. Phased delivery roadmap

### Phase 1 — v1 (this PR, 2026-05-17)

**Ships:** E1 (Task Flow-Spine, hero) + E4 (Claude Envs band) + E8/E9 (Glossary + Legend) + the full build pipeline.

**Why this cut:** smallest surface that proves all four non-negotiables — beautiful (E1), anti-rot (build script + freshness badges), portable (self-contained HTML), linkable (copied into `web-ui/public/`).

**Deliverables:**
- [x] `docs/architecture-view/sources/exhibit_01_flow_spine.yaml`
- [x] `docs/architecture-view/sources/exhibit_04_claude_envs.yaml`
- [x] `docs/architecture-view/sources/exhibit_08_glossary_legend.yaml`
- [x] `docs/architecture-view/drill_downs/{ingress,task_hub,simone,vps_cody,completion}.mmd`
- [x] `scripts/build_architecture_view.py`
- [x] `docs/architecture-view/output/architecture-map.html`
- [x] `web-ui/public/architecture-map.html`
- [x] This plan doc
- [x] Playwright screenshot verification

### Phase 2 — Intelligence + Knowledge (E2, E3) — **SHIPPED 2026-05-18**

**Scope:** CSI pipeline drill-down (Phase 0–5 of CSI v2) and Knowledge plane (Wiki + Memory + Vault relationships). Both rendered inline as Mermaid panels.

**Deliverables:**
- [x] `docs/architecture-view/sources/exhibit_02_intelligence.yaml` — CSI v2 flow (polling → enrichment → min-signal gate → Memex → demo workspace → ledger)
- [x] `docs/architecture-view/sources/exhibit_03_knowledge.yaml` — External vault + internal memory + auto-flush + vector index + query interface

### Phase 3 — Inputs + Surfaces + Ops (E5, E6, E7) — **SHIPPED 2026-05-18**

**Scope:** What feeds the system, what surfaces consume the system, how the system ships.

**Deliverables:**
- [x] `docs/architecture-view/sources/exhibit_05_inputs.yaml` — 11 input channels (email/discord/telegram/calendar/youtube/dashboard/heartbeat/cron/proactive/reflection/webhook), new `html_grid` renderer
- [x] `docs/architecture-view/sources/exhibit_06_surfaces.yaml` — 16 dashboard surfaces with route + purpose + HQ flag, new `html_list` renderer (includes the Architecture Canvas as a self-referential row marked ★)
- [x] `docs/architecture-view/sources/exhibit_07_ops.yaml` — Branch → PR → pr-validate → pr-auto-merge → squash → deploy.yml → systemd restart, with the codie/kevin/feature auto-merge exclusion list

**Renderer extensions:** new `mermaid_panel`, `html_grid`, `html_list` renderers in `scripts/build_architecture_view.py`. Layout migrated to 3-column grid for mid + lower rows.

### Phase 4 — Wiring (operational integration) — **COMPLETE 2026-05-18**

**Status:**
- [x] **Dashboard sidebar link** — `web-ui/components/dashboard/GlobalSidebar.tsx` exposes "Architecture Map" in the Operations group (shipped in PR #342).
- [x] **`just canvas` / `just canvas-verify` recipes** — added to `justfile`. `just canvas` rebuilds; `just canvas-verify` checks pointers without re-rendering.
- [x] **`just preship` chains `canvas-verify`** — the operator-side pre-ship gate now includes pointer verification alongside lint + unit tests.
- [x] **PR Validate CI step** — `.github/workflows/pr-validate.yml` runs `--verify-only` automatically on every PR that touches `docs/architecture-view/` or `scripts/build_architecture_view.py`. PRs that don't touch the canvas pay zero cost; PRs that do are blocked on missing pointers.
- [x] **Weekly drift cron** — registered via `_ensure_architecture_canvas_drift_cron_job` in `gateway_server.py`. Default schedule **Mondays 06:30 America/Chicago** (active hours; content-generation-adjacent under the dormancy default). Implementation: `src/universal_agent/scripts/architecture_canvas_drift_check.py` runs `--verify-only`, exits non-zero on **missing** pointers (surfaces as a failed cron tick on `/dashboard/cron-jobs`), writes `artifacts/architecture-canvas-drift/<date>.md` on **stale** pointers (exits 0; staleness is signal not failure), and is silent when everything is green. Env-controlled: `UA_ARCH_CANVAS_DRIFT_ENABLED`, `UA_ARCH_CANVAS_DRIFT_CRON`, `UA_ARCH_CANVAS_DRIFT_TIMEZONE`.

**Why these notification choices.** Missing pointers route through cron-health because `/dashboard/cron-jobs` is already the canonical "did this scheduled job fail?" surface — no new UI needed. Stale pointers route to a dated markdown artifact because (a) email is quarantine-prone for this account, (b) Mission Control narrative cards are LLM-curated and overkill for a deterministic per-week drift report, and (c) the artifact format is self-explanatory and a natural place for the next operator pass to start fixing. The operator opens `artifacts/architecture-canvas-drift/YYYY-MM-DD.md` directly when they see a recent run on the cron-jobs page; no new UI required.

### Phase 5 — Optional polish

- Search box across exhibits (Cmd/Ctrl+K).
- "Show only stale" filter.
- Color-coded "what changed in the last PR" overlay (read `git log --since` and highlight boxes whose `source:` was touched).

## 5. Pointer YAML schema

Every exhibit YAML follows this shape:

```yaml
id: e01_flow_spine
title: "Task Flow-Spine"
renderer: rough_svg          # rough_svg | mermaid | html_panel
description: |
  Ingress → Task Hub → Simone → VPs/Cody → Completion/Reflection.
  The dominant horizontal axis of the canvas.

nodes:
  - id: ingress
    label: "Ingress"
    source:
      - src/universal_agent/agentmail_official.py
      - src/universal_agent/hooks_service.py
      - src/universal_agent/heartbeat_service.py
    canonical_doc: docs/01_Architecture/000_PIPELINE_MASTERPIECE.md#2-input-triggers--ingress
    drilldown: docs/architecture-view/drill_downs/ingress.mmd
    blurb: "AgentMail websocket, hooks_service webhooks, and process_heartbeat timers funnel work into the Task Hub."

  - id: task_hub
    label: "Task Hub"
    source:
      - src/universal_agent/task_hub.py
    canonical_doc: docs/01_Architecture/000_PIPELINE_MASTERPIECE.md#3-the-task-hub--life-cycle
    drilldown: docs/architecture-view/drill_downs/task_hub.mmd
    blurb: "Central state machine (open → in_progress → delegated → pending_review → completed/parked) backed by runtime_state.db."

  # ... more nodes
```

Build script reads this, validates each `source:` path, captures `git log -1 --format=%cI`, computes age in days, emits a green/amber/red badge per node in the rendered HTML.

## 6. Build & verification

### Manual rebuild

```bash
uv run python scripts/build_architecture_view.py
```

Outputs:
- `docs/architecture-view/output/architecture-map.html` (canonical, ~750KB self-contained)
- `web-ui/public/architecture-map.html` (mirror for dashboard linking)

Exits non-zero if any `source:` path no longer exists.

### Pre-commit hook (Phase 4)

```yaml
# .pre-commit-config.yaml
- repo: local
  hooks:
    - id: architecture-view-build
      name: Rebuild architecture canvas
      entry: uv run python scripts/build_architecture_view.py --verify-only
      language: system
      files: ^docs/architecture-view/
      pass_filenames: false
```

### Weekly drift cron (Phase 4)

Registered via `gateway_server._register_system_cron_job` per CLAUDE.md rule. Runs Mondays 06:30 America/Chicago (within active hours; respects dormancy default — this is content-generation-adjacent, so it fires during waking hours). Reports any newly red pointers via `notification_dispatcher.py`.

## 6.5 Lessons learned (PR #350 close-out)

The Phase 4 close-out PR caught a quality-gate failure in CI that's worth recording so the same class of mistake doesn't recur.

**What happened.** The first revision of `src/universal_agent/scripts/architecture_canvas_drift_check.py` shelled out to the build script via `subprocess.run(["uv", "run", "scripts/build_architecture_view.py", "--verify-only"])`. That tripped `tests/unit/test_task_observability_coverage.py::test_subprocess_spawns_use_observability_protocol` — an AST-based ratchet that scans every file under `src/universal_agent/` for subprocess-spawn calls and requires either a compliant-helper import (from `services.worker_exit_classifier`, `services.cron_task_hub_link`, or `task_hub.record_worker_pid`) OR an entry in `tests/unit/task_observability_coverage_allowlist.txt`. The script had neither, so CI blocked merge of PR #350.

**Root cause — two layers.**
1. **Code:** the drift script reached for `subprocess.run` reflexively because the build script is "script-shaped." The build module is pure Python — there was no need to spawn anything. Importing the verification functions directly is the right pattern.
2. **Process:** `just preship` was advertised as "the same gates as pr-validate.yml" but the recipe chained `just lint` (whole-repo ruff scan) which surfaces ~8000 lines of pre-existing rot the CI gate explicitly carves out. Running preship locally before pushing would therefore have failed for unrelated reasons, masking the protocol-test signal.

**Durable fixes (this PR).**
- Drift script now uses `importlib.util.spec_from_file_location` to load `scripts/build_architecture_view.py` as a module and calls its `load_exhibits()` + `verify_pointers()` directly. No subprocess; protocol test passes.
- Important importlib gotcha captured in code comments: the loaded module **must** be registered in `sys.modules` BEFORE `exec_module` runs, otherwise `@dataclass` decoration fails on Python 3.13 with `AttributeError: 'NoneType' object has no attribute '__dict__'` inside `dataclasses._is_type`.
- `justfile` adds a `lint-pr-scope` recipe that mirrors `pr-validate.yml` exactly (errors-only rules, changed-file scope vs `origin/main`). `preship` now chains `lint-pr-scope + test + canvas-verify` instead of the whole-repo `lint`. The whole-repo `lint` recipe stays available for operators who want to audit accumulated rot, but the comment now warns against chaining it into preship.

**What to do for future cron-scripts.** Any new file under `src/universal_agent/scripts/` that wants to invoke another Python script should prefer `importlib.util` import over `subprocess.run` — the protocol test will fail every PR that spawns subprocesses without compliant-helper wiring, and 95% of the time the simpler answer is "don't spawn." Reach for `subprocess` only when you're actually invoking an external binary or running code in a different runtime.

## 7. Open follow-ups

- **Dashboard wiring** — Phase 4. The HTML exists in `web-ui/public/`; the dashboard link is not yet added. When wired, the link should sit in the global sidenav near "Mission Control."
- **Cron registration** — Phase 4. Until then, manual rebuild only.
- **E2/E3/E5/E6/E7 authoring** — Phases 2 & 3.
- **Pre-commit hook** — Phase 4.

## 8. References

- [`docs/01_Architecture/000_PIPELINE_MASTERPIECE.md`](../01_Architecture/000_PIPELINE_MASTERPIECE.md) — prose narrative of the task lifecycle (E1 is the visual companion).
- [`docs/01_Architecture/05_Simone_First_Orchestration.md`](../01_Architecture/05_Simone_First_Orchestration.md) — Simone routing model.
- [`docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md`](../06_Deployment_And_Environments/10_Interactive_Coding_Environment.md) — Claude environments (E4 is the visual companion).
- [`docs/02_Subsystems/Task_Hub_Dashboard.md`](Task_Hub_Dashboard.md) — Mission Control / Task Hub UI (E6 anchor).

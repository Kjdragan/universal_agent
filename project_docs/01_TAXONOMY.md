---
title: Documentation Taxonomy & Canonical Doc Set
status: active
canonical: true
subsystem: meta-documentation
code_paths: []
last_verified: 2026-05-29
---

# Documentation Taxonomy & Canonical Doc Set

> Phase 1 output. Synthesized from a code-first cartography of the codebase (~70 code-derived
> subsystems) reconciled against the legacy corpus (214 files). The machine-readable work-list is
> `_meta/doc_manifest.json` — it drives Phase 2 reconstruction. This file is the human-readable rationale.

## What changed vs the legacy structure

- **214 legacy files → 48 canonical docs.** The rest is consolidated, archived, or disposed.
- **No numbered-directory collisions, no parallel ungoverned hierarchies.** One scheme, eight categories.
- **One index** (`README.md`), not two mega-indexes. `Documentation_Status.md` is retired; "last updated"
  lives in each doc's `last_verified` frontmatter.
- **Every doc is born code-verified** with frontmatter `code_paths` + symbol-based citations (no line numbers).

## The eight categories

```
project_docs/
├── README.md                     # single index (Phase 4)
├── CLAUDE.md                     # scoped governance — lazy-loads under project_docs/ (Phase 3)
├── GLOSSARY.md                   # project-specific terms (Phase 4)
├── 00_DOCUMENTATION_REFACTOR_PLAN.md
├── 01_TAXONOMY.md                # this file
├── 02_GOTCHA_INVENTORY.md        # preserved operational/rationale facts
├── 01_architecture/   (6 docs)   # system-level design & cross-cutting models
├── 02_execution_core/ (5 docs)   # gateway, sessions, task hub, durable, URW, workspaces
├── 03_agents/         (6 docs)   # VP workers, Simone, heartbeat, cron, idle/goal, agent college
├── 04_intelligence/  (12 docs)   # CSI, URL judging, wiki, memory, proactive pipeline, discord intel
├── 05_channels/       (5 docs)   # email, webhooks, telegram, discord ops, web-ui
├── 06_platform/       (6 docs)   # secrets, runtime, identity/auth, deploy/CI, environments, networking
├── 07_tools/          (3 docs)   # MCP server, SDK, skills
├── 08_operations/     (5 docs)   # playbook, verification, dormancy, VPS recovery, incidents
└── _meta/doc_manifest.json       # machine-readable work-list
```

Tier mix (drives Phase 2 effort allocation): **22 tier-1** (load-bearing), **23 tier-2**, **3 tier-3**.

## Naming & placement rules (enforced in Phase 3 by `CLAUDE.md` + CI)

- Category dirs are `NN_snake_case`, sequential, no collisions.
- Files inside are `NN_snake_case.md`, sequential within their category. **No dates in canonical filenames**
  (dates belong in `last_verified` frontmatter; dated point-in-time reports go to the archive).
- Every doc carries the frontmatter schema from the refactor plan §2.2.
- One canonical doc per subsystem. Audits/handoffs/status updates fold into the canonical doc, they do not
  spawn new files.

## Legacy disposition summary

The Phase-1 harvest classified legacy topics as keep / merge / dispose / archive. Mapped to the rebuild:

- **keep / merge** → folded into one of the 48 canonical docs (see each doc's `legacy_refs` in the manifest —
  used for the post-draft *gap check only*, never copied).
- **dispose** (obsolete/vaporware): VP `/goal` PRD draft (`12_VP_Goal...`), the `/btw` sidebar-sessions
  section of Simone orchestration (no `/btw` handler exists in code), VPS-as-Dev fallback workflow, the
  deprecated CSI insight-detection firehose.
- **archive** (dated point-in-time reports, closeout/go-no-go, run reviews, completed-phase runbooks
  — THREADS phases, SDK canary, dual-factory brainstorm, deploy-incident reports): moved at cutover to the
  search-excluded archive (legacy `docs/` retained as `_archive/`, added to `.rgignore`).

## Known rebuild gotchas (carried into Phase 2 agent prompts)

From the Phase-0 audit + Phase-1 harvest, reconstruction agents are pre-warned about:
- **Task Hub**: canonical DB is `activity_state.db`, NOT `task_hub.db`; legacy line refs are 100s of lines off.
- **VP delegation**: priority tiers are `operator_daily/operator_signal/maintenance/background` (legacy doc wrong).
- **CSI URL judge**: uses `resolve_opus()` (legacy said sonnet); cron is 3x daily.
- **LLM Wiki**: `resolve_vault_path()` ignored `vault_kind` (verify if still a code bug; flag if so).
- **Deploy**: concurrency guard IS implemented; `develop` + `feature/latest2` are retired.
- **Email**: label is `agent-codie`, not `agent-cody`.
- **Tooling self-drift**: legacy `doc_maintenance_agent.py` / `99_Documentation_Drift_Maintenance_Pipeline.md`
  reference the retired `develop` branch — these get superseded by the Phase 3 engine.

See `02_GOTCHA_INVENTORY.md` for the full preserved operational/rationale knowledge.

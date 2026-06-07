# Build Docs Playbook (UA)

Read `principles.md` and `governance-files.md` first, then follow this flow. "Build" = authoring a new
doc or overhauling an existing canonical one.

## 1. Establish ground truth from code (before any prose)

- Identify the subsystem and the exact `code_paths` (globs) the doc will own.
- Open those source files and confirm the symbols you intend to cite **resolve** (`grep` the
  `file.py::symbol`). Never write a behavior claim you have not read in the source.
- Capture operational/rationale facts not visible in code (external service behavior, infra quirks, the
  *why*) — preserve them from `project_docs/02_GOTCHA_INVENTORY.md`. Mark anything you cannot verify
  inline as `> [VERIFY: ...]`.

## 2. Decide placement — fold vs. create

This is governed by `project_docs/CLAUDE.md` ("Create-vs-update", "Taxonomy & placement"):

- Check `project_docs/README.md` (the generated index) and `project_docs/_meta/doc_manifest.json`. If a
  canonical doc for this subsystem exists, **update it** — do not make a new file.
- An audit / handoff / status snapshot / incident note folds **into** the canonical doc, or into
  `project_docs/08_operations/05_incident_response_patterns.md` as a distilled pattern. It never becomes
  its own file.
- Only a genuinely new subsystem gets a new file: `NN_snake_case.md`, sequential within one of the eight
  categories (`01_architecture`, `02_execution_core`, `03_agents`, `04_intelligence`, `05_channels`,
  `06_platform`, `07_tools`, `08_operations`), **plus a README index entry in the same change**.
- **No dates in canonical filenames.** Dates live in `last_verified` frontmatter; dated point-in-time
  reports belong in the archive, not the canonical tree.

## 3. Write the required frontmatter (every doc)

```yaml
---
title: <human title>
status: active | draft | archived
canonical: true | false        # is this THE doc for its subsystem?
subsystem: <kebab-slug>
code_paths:                     # globs this doc documents — drives PR-time drift mapping
  - src/universal_agent/<...>
last_verified: YYYY-MM-DD       # date checked against code (today, since you just verified)
---
```

`code_paths` is load-bearing: PR-time drift mapping uses these globs to map changed code → docs that
claim to document it. Keep them accurate and tight.

## 4. Structure before prose

- Funnel: what/why → quickstart → next steps. Open each section with the takeaway sentence.
- Pick the Diataxis type (tutorial / how-to / reference / explanation) and don't mix purposes in one
  section.
- Keep headings informative and scannable; add concrete decision branches where choices matter.

## 5. Cite correctly (the rule CI enforces hardest)

- Cite `file.py::symbol` (e.g. `task_hub.py::claim_next_dispatch_tasks`, `mission_priority.py::TIERS`).
- To point inside a long function, name it + quote a token ("the `finally` block of
  `_execute_cli_session`").
- For exact wording, paste a 3–5 line fenced snippet of the real code.
- **Never** cite line numbers — `scripts/doc_audit.py` fails on `:NNN` / `line NNN` / `LNNN`.

## 6. Governance-file builds (CLAUDE.md / AGENTS.md)

See `governance-files.md`. Summary: `CLAUDE.md` is canonical policy; `AGENTS.md` is a thin alias/delta
pointing at it; `project_docs/CLAUDE.md` is the scoped doc-governance file. Keep policy DRY — one source,
aliases point back, never duplicate rule text.

## 7. The task-type registry is special — it DEFERS, it does not duplicate

`project_docs/01_architecture/07_task_type_registry.md` is the canonical map of every task type /
mission system. When editing it:

- Each row carries a lifecycle `status` (`canonical` / `active_secondary` / `deprecated` / `removed` /
  `unclear`) and an "owner" pointer to the subsystem doc that holds the detail. It is a **map, not a
  re-implementation** — update the owning doc and the row, not a copy of the logic.
- Its scheduling section **defers** to `src/universal_agent/systemd_migrated_jobs.py`
  (`SYSTEMD_MIGRATED_SYSTEM_JOBS` frozenset + `is_migrated_to_systemd()`). Do not re-enumerate migrated
  jobs in prose.
- `scripts/registry_drift_check.py` enforces status-vs-code invariants (a `removed` row must not cite
  live code unless marked dead residue; a `canonical`/`active_secondary`/`deprecated` row must cite
  symbols that resolve; the frozenset pointer must stay intact). Run it after any registry edit.

## 8. Brownfield mode (existing canonical doc / live IA)

- Match existing terminology, category placement, and section patterns.
- Preserve IA unless there's a documented migration plan; smallest safe change set that improves utility.
- Keep anchors and internal cross-links valid.

## 9. Evergreen mode (durable docs)

- Prefer stable concepts over release-tied narrative; isolate volatile details under clearly marked
  sections.
- The maintenance signal IS the frontmatter: keep `code_paths` accurate and `last_verified` current.
- Note deprecation/replacement paths where relevant (and reflect them as registry `status` where the doc
  is a task type).

## 10. Writing constraints

- Precise language, short imperative instructions, copy-ready self-contained examples.
- Include common failure modes and safe defaults; no placeholder guidance that can't be executed.
- Key facts in text (not image-only) so agents can read them.

## 11. Build verification (mandatory — defer to the tooling)

Run before claiming done (these mirror CI; see SKILL.md "Verification"):

```bash
python scripts/doc_audit.py                  # frontmatter, file::symbol, no line numbers, links/orphans
python scripts/registry_drift_check.py       # only relevant if you touched the registry
python scripts/gen_doc_index.py --check      # then run without --check to regenerate README if stale
```

If `--check` reports staleness, regenerate: `python scripts/gen_doc_index.py` (never hand-edit
`project_docs/README.md`). Record what passed and any `> [VERIFY: ...]` markers left for the human.

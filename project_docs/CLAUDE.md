# Documentation Governance (scoped to `project_docs/`)

This file lazy-loads when an agent works under `project_docs/`. It holds the **rules** for the rebuilt
documentation. The root `CLAUDE.md` keeps only a one-line pointer here. **These rules are guidance; the
mechanical enforcement is CI** (see `## Enforcement`). Full rationale: `00_DOCUMENTATION_REFACTOR_PLAN.md`.

## The one rule that matters

**Code is the source of truth.** A doc describes what the code *actually does now*. If a doc and the code
disagree, the code wins and the doc is wrong. When you change behavior, update the doc that owns it in the
same change — don't spawn a new doc.

## Find the docs you must update — reverse-lookup, not memory

"The doc that owns it" is discoverable from `code_paths:` frontmatter — use that link **at change time**;
don't rely on recall. When you add or change a feature, list the canonical docs that claim any file you
touched, confirm each against its `code_paths` globs, and update it in the **same** change:

```bash
git diff --name-only origin/main...HEAD | while read f; do
  grep -rl -e "$f" -e "$(dirname "$f")" project_docs --include='*.md'
done | sort -u
```

Every doc that owns changed behavior gets updated now — or gets a one-line "unaffected because…" in the PR.
The nightly accuracy sweep (`scripts/doc_accuracy_sweep.py`) is a **backstop to catch what slips through,
not the mechanism** — never defer a doc update to it.

## Citations — symbols, never line numbers

- Cite `file.py::symbol` (e.g. `task_hub.py::claim_next_dispatch_tasks`, `mission_priority.py::TIERS`).
- To point inside a long function: name the function + a quoted token ("the `finally` block of `_execute_cli_session`").
- When exact wording matters: paste a 3–5 line fenced snippet of the real code.
- **Never cite line numbers.** They rot on every unrelated edit and can't be cheaply validated. CI rejects them.

## Per-doc frontmatter (required on every doc)

```yaml
---
title: <human title>
status: active | draft | archived
canonical: true | false        # is this THE doc for its subsystem?
subsystem: <kebab-slug>
code_paths:                     # globs this doc documents — the link you reverse-look-up at change time
  - src/universal_agent/<...>
last_verified: YYYY-MM-DD        # date the doc was last checked against code
---
```

`code_paths` is load-bearing in two directions: **at change time you reverse-look-up** your changed files
against these globs to find the docs you must update in the same PR (see "Find the docs you must update"),
and **the nightly accuracy sweep reads them forward** to re-verify each doc against the code it claims. Keep
them accurate or both uses break. `last_verified` is stamped whenever the doc is reconstructed or passes an
accuracy audit.

## Taxonomy & placement

- Eight numbered categories, no collisions, no parallel ungoverned dirs: `01_architecture`,
  `02_execution_core`, `03_agents`, `04_intelligence`, `05_channels`, `06_platform`, `07_tools`,
  `08_operations`. (See `01_TAXONOMY.md`.)
- Files are `NN_snake_case.md`, sequential within their category.
- **No dates in canonical filenames.** Dates live in `last_verified`. Dated point-in-time reports do not
  belong in the canonical tree — they go to the archive.
- One canonical doc per subsystem. Audits, handoffs, status snapshots, and incident notes fold **into** the
  canonical doc (or into `08_operations/05_incident_response_patterns.md` as a distilled pattern); they do
  not create new files.

## Create-vs-update

Before creating any new file, check `README.md` (the single index) and the manifest. Prefer updating the
canonical doc. A genuinely new subsystem gets a new doc **and** an index entry in the same change.

## Operational / rationale facts not in code

Some true, important knowledge is environmental (external service behavior, infra quirks) or rationale (the
*why*). Preserve it when still valid — see `02_GOTCHA_INVENTORY.md`. Mark anything you can't verify inline
as `> [VERIFY: ...]`. Don't carry stale code-behavior claims; the code is the arbiter for those.

## Archive policy

The legacy corpus lives under `_archive/` (search-excluded via `.rgignore`). It is deep-search-only
reference. Never link to it from canonical docs as if it were current; never edit it.

## Enforcement (CI — the actual teeth)

`scripts/doc_audit.py` + CI run, independent of whether anyone read this file:
- **frontmatter validator** — every doc has the required schema; `code_paths` globs resolve.
- **symbol-reference check** — every `file::symbol` citation grep-resolves in its file.
- **no-line-number check** — fails on `:NNN` / `line NNN` / `LNNN` citation patterns.
- **orphan/link check** — every canonical doc is in `README.md`; no broken internal links.
- **non-`docs`-path tripwire** — any automated doc-fix PR that touches a non-doc path hard-fails.

The autonomous doc-fix agent receives these rules via explicit system-prompt injection — it does not rely
on having loaded this file.

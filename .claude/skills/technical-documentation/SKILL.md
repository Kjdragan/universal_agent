---
name: technical-documentation
description: "Author, overhaul, and review Universal Agent prose/project documentation under project_docs/ and the governance files (CLAUDE.md / AGENTS.md), code-first and CI-green. Use whenever the user wants to write docs, document a feature or subsystem, update documentation, overhaul/rewrite the README, review the docs, do a doc audit, fix doc drift, reconcile docs with code, update CLAUDE.md or AGENTS.md, add or move a doc, decide where a doc belongs, fold an audit/handoff/incident note into a canonical doc, fix frontmatter, fix file::symbol citations, regenerate the docs index, or update the task-type registry — even if they don't name a file. Covers build vs review and brownfield vs evergreen modes, the proactive high-confidence-fix sweep with a report-only mode, and verification via the repo's deterministic doc tooling. Does NOT cover skills-library health/quality (that is skill-audit-tf and skill-judge) or external-repo docs (that is zread-dependency-docs)."
user-invocable: true
risk: safe
source: "Adapted from vincentkoc/dotskills (MIT) — technical-documentation"
---

# Technical Documentation

Produce and review Universal Agent **prose / project documentation** (`project_docs/**`) and the
**governance files** (`CLAUDE.md`, `AGENTS.md`, `project_docs/CLAUDE.md`) so they are clear, accurate,
and pass the repo's deterministic CI doc gate. The one rule that overrides everything: **code is the
source of truth** — a doc describes what the code *actually does now*; if doc and code disagree, the
code wins and the doc is wrong.

## The non-negotiables (UA reality)

1. **Code-first.** Reconstruct/verify docs from the code they claim to document — never edit legacy
   prose in place and never invent behavior.
2. **One canonical doc per subsystem.** Audits, handoffs, status snapshots, and incident notes fold
   *into* the canonical doc (or into `project_docs/08_operations/05_incident_response_patterns.md` as a
   distilled pattern). They do **not** spawn new files.
3. **Cite `file.py::symbol`, never line numbers.** Line numbers rot and CI rejects them. To point
   inside a function, name it + quote a token; for exact wording paste a 3–5 line fenced snippet.
4. **Doc updates ship in the same change as the behavior change**, with required frontmatter and a
   README index entry.
5. **CI is the gate, not prose.** Your job is to drive the deterministic tooling
   (`scripts/doc_audit.py`, `scripts/registry_drift_check.py`, `scripts/gen_doc_index.py`) to green —
   never reimplement those checks.

Full contract: `references/governance-files.md` and the repo's `project_docs/CLAUDE.md`.

## When to use

- Documenting a new feature/subsystem, or overhauling an existing canonical doc.
- Reviewing a doc diff or running a full `project_docs/` audit (build or review).
- Updating `CLAUDE.md` / `AGENTS.md` / `project_docs/CLAUDE.md` to match current repo workflow.
- Fixing doc drift: stale commands, broken `file::symbol` citations, line-number citations, broken
  internal links, missing frontmatter, README index staleness, registry status-vs-code mismatch.
- Deciding *where* a doc goes, and *whether to fold vs. create*.

## When NOT to use (out of scope — defer)

- **Skills-library health/hygiene** (counts, duplicates, oversized/missing `SKILL.md`, symlink map):
  that is **`skill-audit-tf`**. Do not audit skill files here.
- **`SKILL.md` design-quality scoring** (the rubric): that is **`skill-judge`**. Defer all
  skill-quality grading there.
- **Reading external / open-source GitHub repo docs**: that is **`zread-dependency-docs`**.
- This skill is for **prose / project docs + governance only**.

## Workflow

1. **Classify.** Task = `build` | `review`. Context = `brownfield` (an existing canonical doc / live
   IA) | `evergreen` (durable wording + maintenance signals). Remediation = `apply-fixes` (default) |
   `report-only` (only when the user explicitly asks).
2. **Inventory scope** before touching anything — see `references/governance-files.md` for the discovery
   commands. Map the governance files and the relevant `project_docs/NN_*` category/doc(s). Read
   `project_docs/README.md` (the generated index) and `project_docs/_meta/doc_manifest.json` first.
3. **Read the rules:** `project_docs/CLAUDE.md` (scoped governance) and `references/principles.md`
   (quality ruleset). For any governance-file edit, also `references/governance-files.md`.
4. **Establish ground truth from code.** Open the `code_paths` the doc claims; confirm symbols resolve.
   Never write a behavior claim you have not read in the source.
5. Branch: build → `references/build.md`; review → `references/review.md`.
6. **Proactive sweep (both modes).** Find issues without waiting for repeated prompts: stale commands,
   dead `file::symbol` cites, line-number cites, broken links, missing/invalid frontmatter, registry
   status drift, README staleness. In `apply-fixes` mode, fix high-confidence defects in the same pass.
   In `report-only`, list them as findings.
7. **Fold, don't spawn.** If you find an audit/handoff/snapshot, integrate it into the canonical doc.
   Only a genuinely new subsystem gets a new `NN_snake_case.md` **and** an index entry in the same change.
8. **Verify (mandatory).** Run the repo's deterministic tooling — see "Verification".
9. **Regenerate the index** (never hand-edit `project_docs/README.md`): `python scripts/gen_doc_index.py`.
10. Return deliverables + verification notes (what passed, what remains) + remaining gaps.

## Verification (defer to the repo's tooling — do not reimplement)

These are exactly what PR-time and nightly CI run, so a clean local run predicts a green PR.

```bash
# Deterministic doc gate (frontmatter schema, file::symbol resolution, no line numbers,
# orphan/internal-link check). Errors here fail the PR.
python scripts/doc_audit.py

# Status-conditional invariants on the task-type registry (REMOVED-BUT-ALIVE, CANONICAL-BUT-GONE,
# and the SYSTEMD_MIGRATED_SYSTEM_JOBS / is_migrated_to_systemd() pointer). Don't re-invent drift logic.
python scripts/registry_drift_check.py

# README index must match disk (it is GENERATED — never hand-edit it):
python scripts/gen_doc_index.py --check    # exit 1 if stale → then run without --check to regenerate

# Report-only variants for a non-mutating audit pass:
python scripts/doc_audit.py --warn-only
python scripts/registry_drift_check.py --warn-only
```

The CI gates these feed:
- `.github/workflows/doc-audit.yml` — PR-time, fails on `doc_audit.py` errors, then runs
  `registry_drift_check.py`. A `docfix/*` head branch that touches any non-doc path hard-fails (the
  docfix tripwire). `.github/workflows/archive-write-guard.yml` blocks writes to the archived `docs/` tree.
- `.github/workflows/doc-nightly.yml` — full-corpus `doc_audit.py --warn-only`, README staleness check,
  and an LLM doc-vs-code accuracy sweep over the oldest-`last_verified` batch (routed through the
  ZAI/GLM proxy). Opens a GH issue on drift; never fails the run.

## NEVER

- Never cite line numbers (`:123`, `line 123`, `L123`) — `doc_audit.py` rejects them.
- Never hand-edit `project_docs/README.md` — it is generated by `scripts/gen_doc_index.py`.
- Never read, link, or write the archived legacy `docs/` tree as if it were current (it is
  search-excluded via `.rgignore`; the archive-write-guard blocks edits).
- Never spawn a parallel/audit/dated doc when a canonical one exists — fold into it.
- Never re-enumerate systemd-migrated jobs or rebuild drift logic in prose — the registry **defers** to
  `src/universal_agent/systemd_migrated_jobs.py` (`SYSTEMD_MIGRATED_SYSTEM_JOBS` + `is_migrated_to_systemd()`).
- Never reimplement frontmatter/symbol/line-number/index/registry checks — call the scripts.
- Never write a behavior claim you have not confirmed in the source.

## Optional: parallel sub-agents for large audits

For a repo-wide `project_docs/` audit, you MAY fan out with the **Task tool** (Claude Code subagents),
then merge into one deliverable. This is optional — degrade gracefully to single-agent for small scopes.

- **inventory** subagent — discovery only: governance files + `project_docs/NN_*` coverage map, broken
  links, missing files, dead `file::symbol` cites (exact paths).
- **content/code-truth** subagent(s) — per category, verify doc claims against `code_paths` symbols.
- **registry** subagent — `07_task_type_registry.md` status-vs-code (reproduce `registry_drift_check.py`'s
  findings, not its implementation).

Then synthesize: dedupe, order blockers first, apply high-confidence fixes (unless `report-only`), and
run the Verification block once on the merged result. Do not assign model tiers — let the harness pick.

## Outputs

- Updated/created docs (or review findings), folded into canonical docs, with valid frontmatter.
- Verification notes: which scripts ran and passed, what remains.
- Index status (regenerated, or confirmed in sync via `--check`).
- Governance-alignment note when `CLAUDE.md` / `AGENTS.md` / `project_docs/CLAUDE.md` were touched.
- Autodetected-issue list with applied fixes, or explicit `report-only` findings (blocking →
  non-blocking → verification notes).

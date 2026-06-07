# Review Docs Playbook (UA)

Read `principles.md` and `governance-files.md` first. "Review" = auditing a doc diff or the whole
`project_docs/` corpus for accuracy-to-code, structure, and CI-readiness.

## 1. Scope and classification

- Identify the doc(s) and their subsystem(s); confirm `build` vs `review`, `brownfield` vs `evergreen`.
- Confirm remediation mode: **`apply-fixes` is the default**; `report-only` only when the user explicitly
  asks for it.
- For a full-corpus review, include the governance files (`CLAUDE.md`, `AGENTS.md`,
  `project_docs/CLAUDE.md`) and all eight `project_docs/NN_*` categories.

## 2. Investigation behavior

- Proactively find issues without waiting for repeated prompts; continue past the first pass when signals
  warrant. Deep, longer investigations are fine when needed for confidence.
- Optionally fan out with the Task tool for a large corpus (see SKILL.md "Optional: parallel sub-agents"),
  then merge to one final issue set. Degrade gracefully to single-agent.
- When no issues are found, say so explicitly and call out residual risks / unverified claims.

## 3. Accuracy-to-code review (the highest-value check)

This is where UA docs rot. For each doc:

- Open its `code_paths` and confirm every `file.py::symbol` citation still **resolves** in the named file.
- Spot-check behavior claims against the real source — the code is the arbiter. Flag any claim that the
  code contradicts.
- Flag stale commands, stale flags, and module-scope mismatches (a root command that should be
  module-scoped, an install command that no longer exists).

## 4. Mechanical-defect sweep (defer the actual checks to the scripts)

Run the deterministic engine rather than eyeballing — it is the same logic CI runs:

```bash
python scripts/doc_audit.py --warn-only        # report-only: frontmatter, symbol refs, no line numbers,
                                                # orphan/internal-link check across the whole corpus
python scripts/registry_drift_check.py --warn-only
python scripts/gen_doc_index.py --check        # README index staleness
```

Then triage what it surfaces:
- **Missing/invalid frontmatter** — add/fix `title`, `status`, `canonical`, `subsystem`, `code_paths`,
  `last_verified`.
- **Dead `file::symbol` cites** — re-resolve to the current symbol or remove the claim.
- **Line-number cites** (`:NNN` / `line NNN` / `LNNN`) — convert to `file::symbol` + quoted token.
- **Broken internal links / orphaned canonical doc** — fix the link or add the README index entry.
- **README staleness** — regenerate with `python scripts/gen_doc_index.py` (never hand-edit it).

## 5. Registry status review

For `project_docs/01_architecture/07_task_type_registry.md`, reproduce what
`scripts/registry_drift_check.py` would flag — do not reimplement its logic:

- **REMOVED-BUT-ALIVE** — a `removed` row must not cite a `file::symbol` that still resolves as live
  code, unless the row text carries a dead-residue marker (`backfill`, `stub`, `dead module`, `no-op`).
- **CANONICAL-BUT-GONE** — a `canonical` / `active_secondary` / `deprecated` row must cite symbols that
  resolve.
- **FROZENSET POINTER** — `SYSTEMD_MIGRATED_SYSTEM_JOBS` must exist, be non-empty, and
  `is_migrated_to_systemd()` must be defined in `src/universal_agent/systemd_migrated_jobs.py`.

## 6. Governance-file review

Use `governance-files.md` as the source of truth. Check that `AGENTS.md` stays a thin delta pointing at
`CLAUDE.md` (no duplicated policy that can drift), that `CLAUDE.md`'s doc section still points to
`project_docs/CLAUDE.md`, and that command examples in any governance file match the real repo workflow.

## 7. Structural & writing review

- Funnel check (what/why → quickstart → next steps); Diataxis alignment; split mixed-purpose sections.
- Concise, scannable paragraphs; no ambiguous pronouns or undefined terms; executable examples.
- Flag critical content trapped in images or buried sections.

## 8. Fold, don't spawn

If the review surfaced an audit/handoff/snapshot living as its own file (or about to be created), fold it
into the canonical doc (or `08_operations/05_incident_response_patterns.md`). Removing a redundant doc
also means removing its index entry — regenerate the README.

## 9. Apply fixes vs. report

- `apply-fixes` (default): fix high-confidence defects in the same pass — dead cites, line-number cites,
  broken links, missing frontmatter, README staleness, and clear status drift. Don't stop at caveat-only
  notes when a low-risk fix is obvious. If a required canonical entry is missing, create a minimal
  actionable file + index entry.
- `report-only`: do not mutate; produce findings only.

## 10. Verification + output format

Re-run the deterministic block (without `--warn-only` if you applied fixes, to confirm a green gate):

```bash
python scripts/doc_audit.py
python scripts/registry_drift_check.py
python scripts/gen_doc_index.py --check
```

Output:
1. **Blocking issues** (file + required fix).
2. **Non-blocking improvements.**
3. **Verification notes** (scripts run, what passed, what remains / `> [VERIFY: ...]` markers).

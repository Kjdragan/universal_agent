# Documentation Principles

The governing ruleset for this skill. Read this before `build.md` / `review.md`. UA-specific governance
(taxonomy, frontmatter, citation, create-vs-update, archive, CI) lives in `governance-files.md` and is
the source of truth where it overlaps.

## The UA prime directive

**Code is the source of truth.** A doc describes what the code *actually does now*. If a doc and the
code disagree, the code wins and the doc is wrong. This is from `project_docs/CLAUDE.md` ("The one rule
that matters") and `project_docs/00_DOCUMENTATION_REFACTOR_PLAN.md`: as of the 2026-05-29 code-first
rebuild, ~214 legacy files (pervasive drift, caused by line-number citations and stale claims) collapsed
to ~48–55 canonical docs under one generated index. Honor that intent:

- Reconstruct docs from code, do not edit legacy prose in place.
- Enforcement is **mechanical CI, not prose**. A prompt-only "don't touch source" firewall was bypassed
  once (2026-04-24) and deleted production code — that is why the gate is now deterministic scripts +
  the `docfix/*` tripwire, not a polite instruction.

## General quality rules (Matt Palmer's 8 + OpenAI cookbook)

Default operating principles for clarity (provider-neutral; cited as sources only):

1. Write for humans, optimize for agents.
2. Start with a funnel: what/why → quickstart → next steps.
3. Use Diataxis to scaffold content (tutorial / how-to / reference / explanation).
4. Write with AI, but structure for agents (key facts in text, not images).
5. Prefer specific, accurate terminology over niche jargon.
6. Keep examples self-contained and copy-ready; minimize dependencies.
7. Prioritize high-value topics over edge-case depth.
8. Don't teach unsafe patterns (e.g. exposed secrets).
9. Open with context that orients the reader fast.
10. Make contribution easy and visible; automate quality with CI.

Sources: Matt Palmer, "8 rules for better docs"; OpenAI cookbook, "what makes documentation good".
These are illustrative external references with no behavioral dependency on any specific model provider.

## Practical merge policy (when rules conflict)

1. Preserve reader/agent task success first.
2. Preserve accuracy-to-code second (the prime directive wins over stylistic rules).
3. Preserve structural clarity third.
4. Preserve long-term maintainability fourth.
5. Add agent optimization only if it does not reduce human clarity.

## Execution policy for this skill

- Deep, longer investigations are allowed for build or review when needed to resolve cross-file drift or
  doc-vs-code ambiguity.
- `apply-fixes` is the default for high-confidence defects; `report-only` only on explicit request.
- Optional parallel subagents (Task tool) for large `project_docs/` audits, merged into one deliverable.
- Always finish by running the repo's deterministic verification (see SKILL.md "Verification"); never
  reimplement those checks in prose or in a new script.

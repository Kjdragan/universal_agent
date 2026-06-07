# Governance Files (CLAUDE.md / AGENTS.md / project_docs/CLAUDE.md)

How UA's agent-instruction and doc-governance files relate, and how to edit them without breaking the
DRY policy or the CI gate. This reflects the actual repository, not a generic agents.md convention.

## The canonical/alias relationship (UA reality)

- **`CLAUDE.md` (repo root) is the canonical working policy.** It holds the general rules
  (problem-solving philosophy, code-verified answers, git workflow, secrets, operating rules, etc.) and a
  short "Documentation Maintenance — MANDATORY" section that states the four non-negotiables and points to
  `project_docs/CLAUDE.md` for the full doc contract. It deliberately sheds doc detail to that scoped
  file rather than inlining it.
- **`AGENTS.md` is a thin alias/delta, not a replacement.** Its first line is *"Read `CLAUDE.md`
  first."* — all general rules apply to Codex/OpenAI/Antigravity agents exactly as to Claude Code. It adds
  only a Codex-specific delta (PR-review guidelines, browser-debugging rules, the tailnet scratchpad
  note). It explicitly states these are *"a delta on top of"* `CLAUDE.md`, not a replacement.
- **`project_docs/CLAUDE.md` is the scoped doc-governance file.** It lazy-loads when an agent works under
  `project_docs/` and holds the taxonomy, frontmatter spec, citation convention, create-vs-update rule,
  archive policy, and the `## Enforcement (CI — the actual teeth)` list.

**Canonical = `CLAUDE.md`. Alias = `AGENTS.md`.** (This is the opposite of the upstream agents.md default
where `AGENTS.md` is canonical — UA inverts it, and so do we.)

## DRY rule when editing governance files

- Keep one shared policy core. `AGENTS.md` must **point back** to `CLAUDE.md`, never re-state its rules
  (duplicated rules drift apart and create conflicts).
- A repo-wide policy change goes in `CLAUDE.md`. A doc-specific rule goes in `project_docs/CLAUDE.md`. A
  Codex-only behavior goes in `AGENTS.md` as a delta.
- When you add a Codex-only rule to `AGENTS.md`, confirm it does not contradict `CLAUDE.md`; if it would,
  the general rule belongs in `CLAUDE.md` instead.

## Discovery (run before editing any governance file)

```bash
# Governance + instruction surfaces actually present in this repo:
ls CLAUDE.md AGENTS.md project_docs/CLAUDE.md
# Any other nested CLAUDE.md / AGENTS.md scopes:
rg --files -g 'CLAUDE.md' -g 'AGENTS.md'
```

Read the root `CLAUDE.md` and the nearest-scope governance file before editing. Document any conflict and
the precedence decision (root general policy > scoped doc policy for doc matters > Codex delta).

## The four documentation non-negotiables (mirrored in root CLAUDE.md)

When you touch any governance file's doc guidance, keep these intact and consistent:

1. **Code is truth.** Docs describe what the code does now.
2. **Doc updates ship in the same PR as the behavior change.**
3. **Update the canonical doc — don't spawn a parallel one.**
4. **Cite with `file::symbol` (never line numbers) + add a README index entry.** All CI-enforced.

## Enforcement is CI, not these files

`project_docs/CLAUDE.md` states it directly: *"These rules are guidance; the mechanical enforcement is
CI."* The autonomous doc-fix agent receives these rules via explicit system-prompt injection — it does
not rely on having loaded the file. Practical consequences:

- Editing a governance file does **not** change what passes CI. The deterministic scripts
  (`scripts/doc_audit.py`, `scripts/registry_drift_check.py`, `scripts/gen_doc_index.py`) and the
  workflows (`.github/workflows/doc-audit.yml`, `doc-nightly.yml`, `archive-write-guard.yml`) are the
  teeth. Don't "fix" a CI failure by rewording a governance file.
- The `docfix/*` head-branch tripwire in `doc-audit.yml` hard-fails any automated doc PR that touches a
  non-doc path — the mechanical replacement for the prose firewall that was bypassed on 2026-04-24.

## Archive policy

The legacy corpus is the root `docs/` tree, search-excluded via `.rgignore`. It is deep-search-only
reference (`rg -u --no-ignore <pattern> docs/`). Never link to it from canonical docs as current, never
edit it — `.github/workflows/archive-write-guard.yml` blocks new writes/edits to `docs/` (exempt only via
the `archive-edit-approved` label).

## What this skill does NOT touch

- Skills-library governance/health → `skill-audit-tf`. `SKILL.md` quality scoring → `skill-judge`.
  External-repo docs → `zread-dependency-docs`. Stay on prose / project-docs + the three governance files
  above.

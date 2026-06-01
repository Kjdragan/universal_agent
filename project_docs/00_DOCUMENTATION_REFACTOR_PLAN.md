---
title: Documentation Refactor Plan
status: active
owner: Claude (autonomous, delegated 2026-05-29)
created: 2026-05-29
canonical: true
subsystem: meta-documentation
code_paths: []
last_verified: 2026-05-29
---

# Documentation Refactor Plan

> **This is the canonical, reviewable plan for the complete rebuild of Universal Agent's
> documentation.** It is the first artifact in `project_docs/` — the new home for the rebuilt
> corpus. The legacy `docs/` directory is being treated as a *reference and hazard source only*,
> not as material to edit in place.

## 0. Why we are doing this

A 15-agent read-only audit on 2026-05-29 (workflow `wf_ff5ceff7-6d6`) compared the legacy `docs/`
corpus (212–214 markdown files, ~7.9 MB) against the actual code. Findings:

- **Accuracy:** Of 10 load-bearing subsystems checked, **5 had major drift, 4 had minor drift, 1 was accurate.**
  The accurate one (Gateway/WebSocket) describes stable architecture; the drifted ones chased volatile
  implementation detail.
- **Root cause of measured drift = line-number citations.** `file#Lnnn` references were off by 13–559
  lines everywhere. The legacy CLAUDE.md *mandated* line-numbered citations — the mandate was itself
  manufacturing drift. Concepts survive; coordinates rot.
- **The nightly drift automation is aimed wrong.** It is a 24-hour git *co-change* heuristic with a
  hardcoded ~10-entry code→docs map (~1.5% of 469 Python files). It never reads a doc and compares it
  to code, so it structurally cannot detect accumulated staleness. ~75% of its output is low-value P2
  noise it then tells the fixer to skip. Its Stage 2 dispatch (Tailscale→VPS→gateway) is fragile, and a
  prompt-only "don't touch source code" firewall was bypassed once, deleting `executing_sessions` from
  production code (2026-04-24).
- **Sprawl:** ~46% of docs (97/212) are dated point-in-time reports that accumulate instead of folding
  into canonical docs. 11 "Source of Truth" docs were all stamped 2026-03-06. Subsystems scatter across
  10+ files. A dual mega-index (`README.md` 66 KB + `Documentation_Status.md` 130 KB) duplicates listings;
  34 docs are orphaned from both.
- **Taxonomy:** numbered-directory collisions (`02_*`, `03_*`, `04_*` each used twice) and parallel
  ungoverned hierarchies (`operations/` vs `03_Operations/`, `deployment/` vs `06_Deployment_And_Environments/`).

Conclusion: editing/reorganizing the legacy docs would polish drifted content and inherit its errors.
We rebuild from code instead.

## 1. Core principle — code-first reconstruction

**The code is the source of truth for the new docs. The legacy docs are not source material.**

For each topic the new corpus will cover:

1. **Candidate decision.** Legacy docs + code structure tell us *which topics deserve a doc*. We decide
   keep / dispose. The legacy doc contributes nothing else to scope.
2. **Independent code-only reconstruction.** Read the actual code and write the doc *from code reality*,
   as if the legacy doc did not exist.
3. **Reconcile against the legacy doc — two ways, both verified:**
   - **Gap check** — "did the code review miss anything the legacy doc covers?" Re-verify each candidate
     gap *in code* before including it.
   - **Gotcha / fact harvest** — the legacy doc's value is surfacing non-obvious landmines and important
     operational facts. See §4 for the verification rule.

### 1.1 Hard method safeguard — CODE FIRST, DOC SECOND

An investigating agent **must build its understanding from code and draft the new doc BEFORE reading the
legacy doc.** Reading the legacy doc first primes confirmation bias (the agent "verifies" the doc instead
of independently discovering reality), which defeats the entire intent. The legacy doc is opened only
afterward, as a gap/gotcha checklist. This ordering is baked into every reconstruction agent prompt.

## 2. Conventions for the new corpus

### 2.1 Citations — symbols, never line numbers
- Default: cite `file.py::symbol` (e.g. `task_hub.py::claim_next_dispatch_tasks`, `mission_priority.py::TIERS`).
- To point inside a long function: name the function + a quoted token ("in the `finally` block of `_execute_cli_session`").
- When exact wording matters (enum, default, flag set): paste a 3–5 line fenced snippet of the real code.
- **Never** cite line numbers. They rot on every unrelated edit and can't be cheaply validated.
- Rationale: symbol refs survive line shifts and break only on rename/delete — which is real drift,
  and CI can validate them with `grep -q "def <symbol>" file`.

### 2.2 Per-doc frontmatter (machine-readable, stays inline)
Every doc carries YAML frontmatter:
```yaml
---
title: <human title>
status: active | draft | archived
canonical: true | false        # is this THE doc for its subsystem?
subsystem: <slug>              # e.g. task-hub, csi, deployment
code_paths:                    # globs this doc documents (drives PR-time mapping + drift checks)
  - src/universal_agent/task_hub.py
  - src/universal_agent/services/dispatch_service.py
last_verified: YYYY-MM-DD       # last code-verification date
---
```
Frontmatter only enters context when its own doc is read (~a few lines) — it is **not** a context cost.
The bulk operations (orphan detection, code→doc mapping, PR-time lookup) are CI/script grep that run
outside any agent context.

### 2.3 Governance lives in a scoped `project_docs/CLAUDE.md`
The doc-system rules (taxonomy, frontmatter spec, citation convention, create-vs-update rule, archive
policy) live in a nested `CLAUDE.md` under the docs directory, which lazy-loads only when an agent works
there. The root `CLAUDE.md` keeps a one-line pointer and **sheds** its verbose "Documentation Maintenance"
block — shrinking always-on context.

### 2.4 Enforcement is mechanical (CI), not prose
**CLAUDE.md (root or scoped) is guidance, never the enforcement mechanism.** Nested-CLAUDE.md auto-load is
an interactive-Claude-Code convenience; CI and autonomous agents don't reliably get it, and prompt-only
rules already failed once (2026-04-24). Enforcement = CI teeth:
- frontmatter-schema validator,
- symbol-reference grep check,
- **non-`docs`-path tripwire** that hard-fails any drift-fix PR touching code.
The autonomous doc-fix agent receives rules via explicit system-prompt injection, not by hoping it loaded
a CLAUDE.md.

## 3. The new drift engine (replaces the nightly heuristic)

| Legacy piece | Action | Why |
|---|---|---|
| Internal link checker | keep | deterministic, zero false positives |
| Index orphan / dead-entry check | keep (simplify) | deterministic; trivial with one index + frontmatter |
| Glossary drift detector | **kill** | high FP; the fixer is told to skip it |
| 24h co-change `code_doc_crossref` + 10-entry map | **kill** | wrong sensor; 1.5% coverage; can't see real drift |
| Tailscale→VPS→gateway dispatch | **kill** | fragile and unnecessary — CI has the repo + LLM API |
| Prompt-only source-code firewall | **replace** | bypassed once; make it a CI tripwire |
| — | **add** | LLM accuracy auditor (reads doc + `code_paths` → structured drift verdict) |

**Architecture (code gates, LLM synthesizes — per repo philosophy):**
- *Deterministic layer (every run):* link check, orphan check, symbol-ref validation, frontmatter schema.
- *LLM layer (rotating):* pick the N docs with the oldest `last_verified`, read each doc + its `code_paths`,
  judge accuracy, list specific drifts (each citing exact symbol + doc claim), stamp `last_verified` on a
  clean pass. ~10 docs/night ⇒ full-corpus re-verification every ~2–3 weeks.

**Two triggers (both inside GHA, no VPS):**
1. **PR-time:** changed code → look up docs whose `code_paths` match → flag for verification in the PR.
2. **Rotating nightly sweep:** the LLM auditor, for accumulated staleness.

**Fix path:** docs-only auto-fix agent branches/fixes/PRs, **behind a mechanical pr-validate tripwire**
that hard-fails if the PR touches any non-docs path. Major/architectural findings require a confirmation
pass before escalation.

## 4. Gotcha / operational-fact preservation rule

Not all valuable knowledge lives in code. Rule:
- **Gotcha that asserts code behavior** → must be re-verified in current code. Stale → drop.
- **Operational/environmental fact not in the repo** (e.g. "Google OAuth is in Testing mode → refresh
  tokens expire ~weekly", "deploy wipes `/opt/.../.env` every run", "ZAI keyring backend must be `file` on
  the VPS") → judged for **current validity**; if clearly still true and important, **preserved** even
  though it isn't code-shaped. Genuinely-uncertain ones are flagged for operator confirmation.
- **Rationale / "why"** lives in legacy docs + git + ADRs → harvested as candidate context, marked as
  asserted (not code-verified).

Nothing important and true is dropped. Stale code-claims and dead-feature docs are.

## 5. Dated-report backlog (~97 files)

Kept but **out of the working flow** — deep-search-only emergency reference, **excluded from default
project searches** (`.rgignore`/`.ignore` + index) so stale docs never surface during code work.
Hybrid consolidation: fold the high-traffic subsystems' still-valid buried facts into the new canonical
docs first; wholesale-archive the long tail. The legacy `docs/` directory becomes the archive home
(renamed/retained as `docs/` archive with the dated reports), excluded from search.

## 6. Phased execution plan

Each phase is one or more workflows (or a code task), with a self-review checkpoint between phases. Because
ownership was delegated for autonomous completion (2026-05-29), Claude acts as the approver at each gate,
applying the principles above, and ships the whole effort on a branch via a single reviewable PR (never
direct-to-main). Docs-only changes are deploy-safe (`deploy.yml` paths-ignores `docs/**`, `**.md`).

- **Phase 0 — Audit** ✅ complete (this plan's basis).
- **Phase 1 — Scope & taxonomy** (workflow, read-only): code-first candidate topic list, proposed taxonomy,
  keep/dispose decisions, harvested gotcha/operational-fact inventory. Output: `01_TAXONOMY.md` +
  `02_TOPIC_INVENTORY.md` in `project_docs/`.
- **Phase 2 — Code-first reconstruction** (workflows): per approved topic, the §1 pipeline
  (code-only investigate → draft with frontmatter + symbol refs → adversarial verify vs code → reconcile
  vs legacy doc). Drafts written into `project_docs/` under the new taxonomy.
- **Phase 3 — Engine & governance** (code): new `doc_audit.py` (deterministic + rotating LLM auditor),
  `project_docs/CLAUDE.md`, CI checks (frontmatter validator + symbol-ref check + non-docs-path tripwire),
  PR-time trigger. Retire the heuristic auditor, the VPS dispatch, and the legacy drift pipeline doc.
- **Phase 4 — Archive & cutover** (workflow + PR): consolidate high-traffic subsystem facts; archive the
  dated-report long tail; exclude the archive from search; delete the `/btw` vaporware; fix the
  `develop`-branch tooling drift; finalize indexes. Ship via reviewed PR.

## 7. Progress log

- 2026-05-29 — Phase 0 audit complete; plan authored; `project_docs/` created on branch `worktree-doc-refactor`.
- *(subsequent phase outcomes appended here as they complete.)*

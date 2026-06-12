# Task-Hub Audit #2 — Cody (CODIE) code-building mission

**Date:** 2026-06-06 · **Auditor:** Claude Code (Opus 4.8) · **Scope:** the `vp.coder.primary` code-building task type
**Primary subject:** `vp-mission-f7b5d11e7f4997ad4045ae03` (proactive cleanup → **PR #767**) · **Companions:** `vp-mission-2a1973be…` (with_pr), `vp-mission-26155b18…` (UiPath tutorial build)

---

## 1. Verdict

**The Cody code-building flow works as intended — and it's the most rigorous path in the platform.** The audited mission triaged ~2,800 `except` clauses, isolated one coherent antipattern, made a 7-site fix in `hooks_service.py`, wrote a red-green test, passed the CI ruff gate, opened a real PR (**#767, +84/−6, open**), and emailed a VP status — all in ~19 min, in an isolated git worktree, touching no forbidden paths and never merging/deploying. `completed_with_pr` was correctly stamped. The `/goal` self-brief + completion-attestation loop (which Atlas skips) is working exactly as designed.

## 2. Correction to my own prior lead (the discipline that matters)

Last round I flagged "**15 `completed_without_pr` vs 0 `completed_with_pr`**" from grepping the mission-control ledger JSON. **That was a false signal** — the string match swept the whole ledger blob, not real disposition counts. Source-verifying the actual task records shows the opposite: of the 3 resolvable `vp.coder.primary` missions, **2 are `completed_with_pr`** and the 1 `completed_without_pr` was a **successful** tutorial-repo build. The disposition stamping is *not* broken. (Same lesson as Atlas: a runtime symptom is a hypothesis, confirm against source before believing it.)

## 3. What worked (confirmed, end-to-end)

- **Dispatch:** `proactive_codie:8f56763ba71c` → `vp.coder.primary`, **started 5s after creation** (vs Atlas's ~5.5 min — this lane is healthy on throughput).
- **`/goal` loop:** `BRIEF.md` (interpretation + triage) written first via the `self-brief-and-attest` skill; `COMPLETION.md` written before declaring done, with a full **BRIEF-vs-actual mapping table** and self-attestation. The worker enforces COMPLETION.md (missing → `failure_mode=missing_completion_attestation` → Simone). Working as designed.
- **Branch hygiene:** detected the production checkout was on a diverged branch, used an **isolated git worktree** from freshly-fetched `origin/main` (`8da85e70`); merge-base verified non-disjoint; diff lists only the 2 intended files. Exactly the brief's rule.
- **Engineering quality:** red-green TDD (`test_hooks_service_logging_context.py` RED→GREEN), matched house idiom (`exc_info=True`, 77 prior uses; avoided `logger.exception` to not bump to ERROR/trip alerting), and **proved 4 regression failures were pre-existing via `git stash`** on pristine `origin/main`.
- **PR #767 is real and reviewable** — base `main`, 2 files, OPEN, not draft; left for review because `codie/*` is non-auto-merge (correct per branch policy). No merge/push-to-main/deploy.
- **Observability surface (CLI path):** `run.log` is **populated** (18.7 KB, a clean USER/ASSISTANT/TOOL timeline) plus a 2.4 MB `cli_stream.log` — the opposite of Atlas's empty `run.log`. This is why PR #770's resolver fix prefers `run.log` when non-empty: it correctly serves Cody and only falls back for Atlas.
- **Cost is real, not estimated:** `cody_mode=anthropic` → genuine Max billing (cache_read 8.15M, output 52K, ~18.5 min). PR #770 correctly leaves Anthropic-model costs unlabeled (only ZAI/glm rows get the "est." mark), so the cost surface is accurate for Cody.

## 4. Issues & opportunities (all minor — the flow is healthy)

| # | Sev | Finding | Why it matters | Fix |
|---|-----|---------|----------------|-----|
| 1 | **MED** | **`completed_without_pr` is overloaded.** It means both "succeeded, no PR was ever expected" (the UiPath build produces a *repo*, not a PR) and "failed to ship a PR". The UiPath mission is `completed_without_pr` + card severity `success`. | An operator scanning dispositions can't tell a legit no-PR success from a real miss — the exact "is this broken or fine?" ambiguity this campaign is fighting. | Split the disposition (e.g. `completed_with_artifact` / `completed_no_change` / `completed_without_pr`=miss) or carry a reason; or key the failure signal off the card severity, not the disposition string. |
| 2 | **MED** | **CLI-path receipt telemetry is partial.** `outcome.payload.tool_calls: 0` despite many tool calls; the `iterations` array has one tiny entry (output 869) vs the 52,204 aggregate. | Anyone reading the receipt for tool/iteration counts gets wrong numbers (Cody analog of Atlas's null `trace_id`). | Populate `tool_calls` + per-iteration usage from the CLI stream, or drop the misleading fields. |
| 3 | **INFO (fixed)** | **The reconciler false-orphan hit Cody too** — both audited missions carry `dispatch.last_disposition_reason=reconciled_orphaned_in_progress`. | Confirms PR #771's lease guard needed to be **agent-agnostic** (it is). These missions predate the now-deployed fix. | Already shipped (#771, prod `b04dd02c`). Monitor that new Cody missions no longer get the reason. |
| 4 | **LOW** | **Cody output requires operator action to land** — `codie/*` PRs don't auto-merge, so PR #767 sits OPEN awaiting review. Correct by policy, but Cody's value is gated on a human merge. | Throughput/backlog risk if cleanup PRs accumulate unreviewed. | Consider a periodic "codie PR review" nudge, or the `ci-autofix`-style label for low-risk cleanup classes. (Policy decision, not a bug.) |
| 5 | **LOW** | **Email delivery is self-attested, not externally verified here.** COMPLETION.md + receipt claim the `[VP Status]` email sent (no 429); I could not independently confirm from the read-API. | Delivery is a load-bearing step (operator visibility). | A delivery-evidence record (message id) on the mission would make it verifiable, like intel-brief `delivered_at`. |

## 5. Atlas vs Cody — both audited, the registry captures the contract

| | Atlas (`vp.general.primary`) | Cody (`vp.coder.primary`) |
|---|---|---|
| Verdict | works; plumbing issues (fixed in #770/#771) | works; minor observability nuances only |
| `/goal` brief + attestation | skipped | **enforced** (BRIEF.md + COMPLETION.md) |
| Output | artifact (HTML brief) | **PR** (#767) — or a repo (tutorial) |
| `run.log` | 0 bytes (SDK path) | **populated** (CLI path) |
| trace_id | was null (fixed #770, SDK path) | n/a — CLI uses run.log + cli_stream.log |
| Cost | ZAI estimate (now labeled "est." #770) | **real Anthropic** (accurate) |
| `completed_without_pr` | normal | **ambiguous** (success-no-PR vs miss) — finding #1 |

This is exactly the Atlas≠Cody distinction now documented in `project_docs/01_architecture/07_task_type_registry.md` §1 (PR #772).

## 6. Recommendations

1. **Disambiguate `completed_without_pr` (finding #1)** — the highest-value fix; it's the same "can't tell working from broken" pain the registry exists to kill.
2. **Fix CLI receipt telemetry (finding #2)** — populate `tool_calls`/iterations from the stream or remove the misleading zeros.
3. **Verify post-#771** that fresh Cody missions stop carrying `reconciled_orphaned_in_progress` (confirms the deployed fix works in prod for the coder lane).
4. **Decide the codie-PR review cadence (finding #4)** — operator policy call, not code.

---
*Subjects via gateway read-API: `vp-mission-f7b5d11e7f4997ad4045ae03` workspace (BRIEF/COMPLETION/run.log/mission_receipt), PR [#767](https://github.com/Kjdragan/universal_agent/pull/767). Companion to [`vp-mission-audit-handoff.md`](vp-mission-audit-handoff.md).*

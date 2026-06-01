# CI/CD Ground-Up Evaluation & Redesign Proposal (2026-05-30)

**Status:** PROPOSAL — awaiting operator decision on the one "sweeping change" (deploy.yml decomposition) before implementation. The §2-B notifier (PR #581) and the quick caching win are low-risk and can ship without that decision.

**Author:** Claude (background CI/CD evaluation task)

**Mandate (operator, verbatim intent):** "I am just trying to have a system that pushes all your changes through a PR so that they can be validated by you and any other of our connected systems like GitGuardian. And then automatically deploy. It shouldn't be that complicated… dependable automation… something that would have been built from the ground up, rather than just a collection of Frankenstein requests." Also flagged: "I always see a bunch of stuff about regex etc — is that the best way… does that make it brittle?"

---

## 1. The target contract

```
any branch → PR → (CI: py_compile + ruff + pytest + GitGuardian) → auto-merge to main → auto-deploy → instant truthful notification
```

**Finding up front: the contract is already met end-to-end today.** The pipeline is not fundamentally broken or mis-designed. What the operator *feels* ("takes forever, often fails, regex everywhere") traces to **three specific, fixable hotspots**, not a systemic flaw. The redesign below is **targeted de-brittling, not a rewrite** — consistent with "I'm not looking for extra complexity; what I'm looking for is dependability."

---

## 2. What I measured (evidence, not impression)

Pulled the last 100 runs of each workflow via `gh run list` on 2026-05-30 and categorized failures by branch + trigger event.

| Workflow | Success | Failure | Cancelled | Skipped | Typical wall-clock |
|---|---|---|---|---|---|
| **PR Validate** | 89 | 7 | 4 | — | **~5–6 min** |
| **Deploy** | 69 | 15 | 1 | 15 | **~2–4 min** (up to 8 min on heavy gateway cold-start) |

### 2.1 The deploy "failure rate" is misleading — 10 of 15 were self-inflicted

Categorizing the 15 Deploy failures by branch:

| Cause | Count | Notes |
|---|---|---|
| `worktree-claude+bisect-deployyml` / `redo-deploy-improvements-v2` / PR #507 (`ci-cd-robustness`) | **~10** | A **single prior session** repeatedly editing `deploy.yml` to defeat the 2026-05-27 GHA-parser quirk (`displayTitle: "bisect(deploy): probe 20 — line C replaced with filler"`). Self-inflicted during one fix saga. |
| Organic failures on `main` | **~2** | e.g. `feat(intel): restore Track B ideation sweep` — real but isolated. |
| PR #507 merge landing the deploy.yml change | ~1 | The change itself failing as it merged. |

**Conclusion: steady-state Deploy reliability is ~98%.** The visible "Deploy fails a lot" pattern is almost entirely *the cost of editing `deploy.yml`*, which is exactly the brittleness to fix (see §3.1).

### 2.2 PR-Validate failures are the gate *working*

Of 7 PR-Validate failures, most are genuine test/lint failures that were fixed-forward (the gate catching real problems), plus a couple on deploy.yml-fix branches. The 4 "cancelled" are `concurrency: cancel-in-progress: true` doing its job (a new commit superseded an in-flight run) — they *look* alarming in the Actions tab but are correct behavior.

### 2.3 GitGuardian — already wired, operator was right

GitGuardian runs as a **GitHub App check** ("GitGuardian Security Checks", ~1s, passes on every PR), not as a workflow in our YAML. It is genuinely gating PRs. Nothing to build.

---

## 3. Brittleness assessment — the three real hotspots

### 3.1 HOTSPOT #1 — `deploy.yml` is a 516-line monolith (the actual source of pain)

`deploy.yml` is one job whose core is a **single ~420-line `ssh … <<'EOF' … EOF` heredoc** (`.github/workflows/deploy.yml:61`–~480). Everything — git pull, `uv sync`, systemd restarts, parallel health polling, discord baseline logic, crashloop detection, diagnostics — lives as bash *inside YAML inside a heredoc*.

Consequences:
- **Editing it is high-risk.** The 2026-05-27 parser quirk (memory `project_2026-05-27_deployyml_parser_quirk`): `actionlint` + `pyyaml` pass but GitHub's *real* workflow parser silently rejects certain edits. The bisect saga in §2.1 is the direct cost — ~10 failed deploys to land one change.
- **The logic is untestable.** Bash buried in a heredoc can't be `shellcheck`ed or run locally.
- **The 29 KB / 516-line size is itself the smell** the operator's instinct flagged.

This is the #1 dependability problem and the one worth a structural fix.

### 3.2 HOTSPOT #2 — PR-Validate has no dependency cache

`pr-validate.yml` does, on **every** run, with **no cache layer**:

```yaml
- name: Install uv
  run: pip install --quiet uv                       # cold pip install every run
- name: Install project dependencies
  run: uv sync --frozen --no-progress 2>&1 | tail -5  # cold full resolve+download every run
```

`actions/setup-python@v6` is configured **without** `cache:`, and there is no `actions/cache` / `astral-sh/setup-uv` cache. For a FastAPI + Agent-SDK project this is the dominant chunk of the ~5–6 min. This is the "takes forever" for the PR gate, and it's the **single cleanest speed win** — near-zero risk.

### 3.3 HOTSPOT #3 — branch policy is duplicated string-glob logic across workflows

The "which branches auto-merge / auto-rebase" decision is encoded as branch-prefix string globs in **two** places that must stay in sync:

- `pr-auto-merge.yml` — `!startsWith(head.ref, 'codie/') && !startsWith(head.ref,'kevin/') && !startsWith(head.ref,'feature/')`
- `pr-rebase-watchdog.yml` — `case "$head_ref" in claude/*|worktree-*) rebase ;; *) comment ;; esac`

This is the "regex" the operator notices. **Verdict on the regex question:** these are **globs/prefix-matches, not heavy regex**, and prefix-matching branch names is a reasonable primitive. The brittleness is **duplication** (two files, drift risk), not the technique. The genuine regex in the stack (`pr-validate`'s `.bak/.swp/.orig` tripwire, changed-file filters) is small, correct, and fine. **Recommendation: consolidate, don't regex-rewrite.**

### 3.4 Current pipeline (as-built)

```mermaid
flowchart TD
    A[Push to branch] --> B[Open PR to main]
    B --> C{PR Validate<br/>~5-6 min, NO dep cache}
    B --> G[GitGuardian App check ~1s]
    B --> M[PR Auto-Merge<br/>branch-glob allowlist]
    C -->|pass| AM[auto-merge waits]
    G -->|pass| AM
    M --> AM
    AM -->|squash via AUTO_MERGE_PAT| MAIN[main moves]
    MAIN --> D{Deploy<br/>516-line SSH heredoc monolith}
    MAIN --> W[PR Rebase Watchdog<br/>heals DIRTY PRs<br/>duplicate branch-glob logic]
    D -->|health OK| LIVE[Prod live]
    D -->|fail| EM[AgentMail email - failure only]
    D -.->|success: SILENT today| X[no signal]
    style D fill:#ffcccc
    style C fill:#fff0cc
    style X fill:#ffcccc
```

---

## 4. Proposed design — simple, dependable, ground-up where it counts

Three phases. **Phase 0 ships now (low-risk). Phase 1 is the one change needing your approval. Phase 2 is optional cleanup.**

### Phase 0 — ship immediately (no approval needed, near-zero risk)

**0a. §2-B Deploy Complete Notifier — DONE (PR #581, in flight).** `deploy-notify.yml` fires on every terminal Deploy state, confirms the **live** SHA via public `/api/v1/version` (Rule A), and pings the operator's Telegram with ✅/⚠️/❌. This is the "instant truthful notification" the contract was missing.

**0b. Cache dependencies in PR-Validate.** Replace the uncached install with cached uv. Concrete change to `pr-validate.yml`:

```yaml
      - name: Install uv (with cache)
        uses: astral-sh/setup-uv@v5
        with:
          enable-cache: true
          cache-dependency-glob: "uv.lock"
      # remove the separate "pip install uv" step
      - name: Install project dependencies
        run: uv sync --frozen --no-progress 2>&1 | tail -5
```

Expected: PR-Validate drops from ~5–6 min toward ~2–3 min on warm cache. **Verification plan:** ship it, compare the next 3 runs' wall-clock against the ~5–6 min baseline (don't claim the number until measured — per `feedback_verify_fix_before_shipping`).

### Phase 1 — THE sweeping change (needs your go-ahead): decompose `deploy.yml`

Move the ~420-line heredoc body into a **checked-in, shellcheck-able script** and shrink the workflow to a thin shell:

```
scripts/deploy/remote_deploy.sh   # the bash that currently lives in the heredoc
.github/workflows/deploy.yml      # shrinks to ~60 lines: Tailscale → scp script → ssh "bash remote_deploy.sh"
```

```mermaid
flowchart LR
    subgraph Before [Before: 516-line monolith]
      Y1[deploy.yml<br/>420-line heredoc<br/>untestable, parser-fragile]
    end
    subgraph After [After: thin YAML + tested script]
      Y2[deploy.yml ~60 lines<br/>Tailscale + scp + ssh] --> S[scripts/deploy/remote_deploy.sh<br/>shellcheck in PR-Validate<br/>runnable/diffable locally]
    end
    Before -->|decompose| After
```

Why this is the highest-leverage fix:
- **Kills the parser-quirk class of bug** — the YAML becomes too small to trip the GHA parser; logic edits happen in a `.sh` file the parser never sees.
- **Makes deploy logic testable** — `shellcheck scripts/deploy/remote_deploy.sh` runs in PR-Validate; the script can be dry-run-reasoned locally.
- **Eliminates the §2.1 failure cluster** — no more 10-attempt bisects to land a deploy change.
- **Pure refactor** — behavior identical; the same bash runs on the same VPS. Risk is contained to "did the extraction preserve the script byte-for-byte," which is verifiable by diffing the heredoc body against the new file.

**Rollback:** trivial — revert the PR; the old monolith is one `git revert` away. First post-merge deploy is the proof (watched via the new notifier).

**This is the only item I'm holding for your approval**, because deploy.yml is production-critical and you asked to see the plan before sweeping changes.

### Phase 2 — optional consolidation (low priority)

- **Single source of truth for branch policy.** Extract the auto-merge / auto-rebase branch classification into one place (a tiny composite action `.github/actions/branch-policy/` or a shared step that emits `should_automerge` / `rebase_action` outputs), consumed by both `pr-auto-merge.yml` and `pr-rebase-watchdog.yml`. Removes the drift risk in §3.3. Not urgent — current globs work; this is hygiene.
- **Add `shellcheck` + `actionlint` to PR-Validate** for `.github/` + `scripts/` changes. (Caveat: actionlint did **not** catch the 2026-05-27 parser quirk — so this is a helper, not the real defense. The real defense is Phase 1 keeping the YAML tiny.)

### What we explicitly KEEP (don't touch — it works)

- `concurrency: deploy-production` serial guard (prevents `.git/index.lock` races).
- GitGuardian App check.
- `ci-failure-issue.yml` (files/auto-closes GH issues on CI failure — working since the 2026-05-11 `--repo` fix).
- `pr-rebase-watchdog.yml` (heals DIRTY PRs — a real failure mode with no cheaper fix).
- The inverted auto-merge allowlist semantics (`feedback_automerge_policy`).
- AgentMail deploy-failure email (redundant with Telegram for the case that matters most).

---

## 5. Notification taxonomy — the "results to publish"

Every signal reflects **real state**, instantly, with no human-gate theater:

| Event | Signal | Channel | Truth basis (how we know it's real) |
|---|---|---|---|
| Deploy succeeded + code live | ✅ one-liner | Telegram (`deploy-notify`) | `/api/v1/version.commit_sha` == merge SHA (**Rule A**) |
| Deploy "succeeded" but code NOT live | ⚠️ one-liner w/ expected vs live SHA | Telegram | live `/api/v1/version` mismatch / endpoint down |
| Deploy failed / cancelled / timed out | ❌ one-liner + 📧 email | Telegram + AgentMail | workflow_run conclusion |
| CI (PR-Validate) failed | 📋 GH issue (`ci-failure` label), auto-closed on green | GitHub Issues (`ci-failure-issue`) | workflow conclusion + which check |
| Auto-merge stuck (branch DIRTY) | 🤖 auto-rebase, or 💬 PR comment | PR thread (`pr-rebase-watchdog`) | `mergeStateStatus == DIRTY` |
| Secret about to leak | 🔒 failing required check | GitGuardian App | secret scan on the diff |

No fabricated "operator review" gates — per your directive, every signal is actionable truth, and you never have to click "approve" on anything routine.

---

## 6. Proposed end-state flow

```mermaid
flowchart TD
    A[Push to branch] --> B[Open PR to main]
    B --> C{PR Validate<br/>CACHED deps ~2-3 min<br/>+shellcheck on scripts}
    B --> G[GitGuardian ~1s]
    B --> M[Auto-Merge<br/>shared branch-policy]
    C -->|pass| AM[auto-merge]
    G -->|pass| AM
    M --> AM
    AM -->|squash| MAIN[main moves]
    MAIN --> D{Deploy ~60-line YAML<br/>→ remote_deploy.sh<br/>shellcheck-tested}
    MAIN --> W[Rebase Watchdog<br/>shared branch-policy]
    D --> N{{deploy-notify:<br/>confirm live SHA}}
    N -->|✅ confirmed| TG[Telegram ✅]
    N -->|⚠️ mismatch| TG2[Telegram ⚠️]
    N -->|❌ failed| TG3[Telegram ❌ + email]
    style C fill:#ccffcc
    style D fill:#ccffcc
    style N fill:#cce5ff
```

---

## 7. The decision I need from you

**One question:** proceed with **Phase 1 (decompose `deploy.yml` into `scripts/deploy/remote_deploy.sh`)?**

- **Phase 0** (notifier #581 + dep caching) I will ship regardless — both are low-risk and directly serve the contract.
- **Phase 1** is the structural fix that actually kills the brittleness you feel, but it touches production deploy, so I'm bringing it to you first per your "show me the plan before sweeping changes."
- **Phase 2** I'll treat as backlog unless you want it bundled.

If you say go, Phase 1 is a mechanical extraction (byte-preserving) + a shellcheck gate, shipped as one reviewable PR, verified by watching the first post-merge deploy ping ✅ on Telegram.

---

## References (code-verified)

- `.github/workflows/deploy.yml` — 516 lines; SSH heredoc body `:61`–~`:480`; gateway health-wait `96×5s` (~`:420`); AgentMail failure notify `:481`+.
- `.github/workflows/pr-validate.yml` — uncached `pip install uv` + `uv sync --frozen`; `concurrency: cancel-in-progress: true`.
- `.github/workflows/pr-auto-merge.yml` — inverted branch-glob allowlist; `AUTO_MERGE_PAT`.
- `.github/workflows/pr-rebase-watchdog.yml` — duplicate `case` branch-glob classifier.
- `.github/workflows/ci-failure-issue.yml` — issue filer/closer.
- `.github/workflows/deploy-notify.yml` — §2-B notifier (PR #581).
- Public truth endpoint: `https://app.clearspringcg.com/api/v1/version`.
- Memory: `project_2026-05-27_deployyml_parser_quirk`, `feedback_automerge_policy`, `feedback_verify_fix_before_shipping`, `feedback_background_watch_not_poll`, `feedback_env_clobbered_by_deploy`.

# AI Coder Coordination Instructions

> **Audience:** Any AI coding agent (Claude Code, Codex, Cursor, etc.) working on this repository.
> **Last updated:** 2026-05-17 (`pr-auto-merge.yml` redesigned from allowlist to exclude-list — auto-merges all non-draft PRs except `codie/*`, `kevin/*`, `feature/*`. `codie/*` excluded for operator review; all other branch names including future AI-coder conventions auto-merge without requiring a workflow edit).

## Overview

This repository uses a **PR-driven, automated deployment pipeline**. There are two roles:

| Role | Responsibility |
|------|---------------|
| **AI Coder** (you) | Write code on a per-task branch off `main`, commit, push, open the PR |
| **Auto-Merge + Deploy** | `pr-auto-merge.yml` enables auto-merge for all non-draft PRs **except** `codie/*`, `kevin/*`, `feature/*`; CI gates; squash-merge fires `deploy.yml` |

You are the AI Coder. You do **not** deploy directly. You branch from `main`, write code, push, and open the PR (via `/ship` or `gh pr create --base main`). The path to production is **always through a PR to `main`**.

> **2026-05-14 simplification:** `feature/latest2` was retired (PR #273 deleted it locally + on origin). The post-2026-05-10 collapse went one step further — there is no longer a "pseudo-trunk" of any kind. Every PR branches from `main`, lands on `main`, deploys. The Session Baseline Cleanup (`scripts/claude_session_baseline.py`, invoked by `_claude_launcher.py`) auto-lands every new `claudereal` session on fresh `main` after PR auto-merge — see [`docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md` § Session Baseline Cleanup](../06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md#session-baseline-cleanup).
>
> **2026-05-10 simplification (prior):** The `develop` branch was retired. Earlier docs that describe a `feature/latest2 → develop → main` chain are stale.

---

## Git Workflow Rules

### 1. Branch model

```
<your-branch>  →  PR  →  pr-validate.yml CI  →  human merges  →  main moves  →  Deploy fires
```

- **Tier 1 (Kevin in Antigravity, Claude Code conversational):** branch fresh from `main` (`kevin/<task>` or `feature/<task>` by convention; the Session Baseline Cleanup ensures every new session starts on a fresh `main`). Run `/ship` when done; `/ship` opens a PR to `main`.
- **Tier 2 (autonomous bots — CODIE, Cody scaffold-builder, scheduled VP coder):** create a dedicated branch (`<bot>/<task-id>` convention; use `claude/<task>` for Claude Code work, `codie/<task>` for Codie — note `codie/*` requires manual operator review before merge), worktree-isolate the work, syntax-check + unit-test in the worktree, then open a PR to `main` directly.

Both paths arrive at the same gate: a PR to `main` with PR-Validate CI green. The `pr-auto-merge.yml` workflow enables auto-merge for all non-draft PRs except `codie/*`, `kevin/*`, and `feature/*`; tier-1 PRs from `/ship` enable auto-merge the same way.

### 2. Commit conventions

When your work is done:

```bash
git add .
git commit -m "feat(component): descriptive message"
git push -u origin <your-branch>
```

Use [conventional commit](https://www.conventionalcommits.org/) prefixes: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`.

### 3. Never push to `main`

- **Direct push to `main` is forbidden.** GitHub branch protection should reject it; if it doesn't, your push bypasses CI and risks shipping a SyntaxError to production.
- **No more pseudo-trunk.** `feature/latest2` was retired 2026-05-14 (PR #273). Each task gets its own per-task branch off `main`. Tier-1 conversational sessions: `kevin/<task>` or `feature/<task>`. Tier-2 bots: `<bot>/<task-id>` (e.g. `claude/<task>` for any Claude Code agent work — any prefix other than `codie/*`, `kevin/*`, `feature/*` triggers auto-merge in `pr-auto-merge.yml`).
- **`develop`** no longer exists. If you find yourself reaching for it, you're following stale documentation — read this file's "2026-05-10 simplification" note above.

### 4. Never run deployment commands

The following are **strictly prohibited**:

- `git push origin main` (use a PR instead)
- `git push --force` to `main` or any shared branch
- Any `ssh`, `rsync`, or manual VPS deployment commands
- Running CI/CD workflows manually except for legitimate `workflow_dispatch` reruns

The deploy pipeline is fully automated via GitHub Actions, triggered only by pushes to `main` (which arrive via merged PR).

### 5. Sync before pushing

If your branch has fallen behind:

```bash
git fetch origin <your-branch>
git pull --rebase origin <your-branch>   # idempotent if already current
git push -u origin <your-branch>
```

If the rebase surfaces conflicts, resolve them locally and push. Never `--force` push.

### 6. Handoff signal

When your coding session is complete, make sure your **last commit is pushed** AND record the exact SHA in your final reply to the user (e.g. *"ready to /ship; latest commit is `abc123de` on `<your-branch>`"*). That gives the ship operator a deterministic anchor.

If you want to leave a detailed handoff note, create or update `SHIP_HANDOFF.md` at the repo root with:
- Summary of changes made
- List of commits with one-line descriptions (include SHAs)
- Any post-deploy smoke test suggestions
- Known risks or behavior changes

### 7. Notes for the Ship Operator

If you are the agent running `/ship` (you may be reading this in a different session than the AI Coder who produced the commits), apply pre-push hygiene before opening the PR:

```bash
git fetch origin <current-branch> main
git checkout <current-branch>
git pull --rebase origin <current-branch>     # ensures local matches remote BEFORE pushing
# verify expected handoff SHA is in `git log` if the coder gave you one
# then proceed with /ship
```

If `/ship` (or `git push`) fails on push with a non-fast-forward / 403 rejection:

```bash
git pull --rebase origin <branch>
# re-run the failed step
```

Never `git push --force` to `main` or any shared branch.

---

## Agent-Type → Workflow Matrix

> **Audience:** anyone wiring up a new agent (CODIE-style cleanup, Cody scaffold-builder, scheduled VP coder mission, etc.) or asking "should this go through a PR?".
> **Codified after the 2026-05-07 import storm:** an autonomous patcher mutated `/opt/universal_agent/src/` directly, the patches were syntactically invalid, nothing checked them, and every cron that imports the durable package failed for 8+ hours.

| Tier | Who | Branch convention | Path to `main` |
|---|---|---|---|
| **1 — Human-supervised conversational coding** | Kevin + Claude Code (this conversation), Antigravity, Codex when Kevin is in the loop | per-task branch off `main` (`kevin/<task>` / `feature/<task>` / `claude/<task>` for agent work) | `/ship` opens a PR to `main`; `pr-auto-merge.yml` enables auto-merge; CI runs; squash-merge fires `deploy.yml` |
| **2 — Autonomous missions** (CODIE proactive cleanup, Cody scaffold-builder, demo-builder, scheduled VP coder, anything cron- or heartbeat-driven that mutates source) | Bots running unattended on the VPS | `<bot-name>/<task-id>` (e.g., `codie/cleanup-2026-05-10`) | Worktree → patch → syntax-check → unit tests → push to `<bot>/<task-id>` → open PR to `main` directly → CI passes → `pr-auto-merge.yml` squash-merges (all heads except `codie/*`, `kevin/*`, `feature/*`) |
| **3 — GitHub branch protection** | The safety net | N/A | Enforces "no direct push to `main`" at the push level — see [`ci_cd_pipeline.md`](ci_cd_pipeline.md) |

### When in doubt

If the change touches any of these, treat it like a tier-2 PR (real review, not a direct ff-merge through tier 1):

- `.github/workflows/*` (CI/CD)
- `.claude/commands/ship.md` or anything else `/ship` reads
- `src/universal_agent/durable/` (durable state schema or accessors)
- `src/universal_agent/services/dispatch_service.py` or `task_hub.py` (orchestration core)
- `gateway_server.py` cron registration helpers

The cost of a careful PR (~5 min CI + a glance) is far below the cost of a 30-cron-storm + production recovery.

---

## Autonomous Mission Workflow (Tier 2 in detail)

> **For agents that run unattended.** Cron-triggered (CODIE proactive cleanup), heartbeat-triggered (Cody scaffold-builder), or task-hub-triggered (VP coder missions). They share a single contract.

The core invariant: **an autonomous mission must never leave the deployed working tree (`/opt/universal_agent/`) in a state that doesn't match a committed SHA.**

### The 8-step pattern

```
1. Pick up task from Task Hub (or wherever the mission's input comes from)
2. Create a fresh worktree from main:
     git fetch origin main
     git worktree add /tmp/<bot>_<task_id> origin/main
3. Apply patches inside the worktree only — never touch /opt/universal_agent/src/
4. For every modified .py file, run a syntax check:
     python -c "compile(open(p).read(), p, 'exec')"
   On failure: revert the file (git checkout HEAD -- p), abort, mark mission failed.
5. Run a fast targeted test pass on the changed modules:
     uv run pytest tests/unit/<related> -x -q
   On failure: same revert + abort.
6. Commit + push to a dedicated branch:
     git -C /tmp/<bot>_<task_id> checkout -b <bot>/<task_id>
     git -C /tmp/<bot>_<task_id> commit -am "..."
     git -C /tmp/<bot>_<task_id> push origin <bot>/<task_id>
7. Open a PR via GitHub API targeting main.
   Title: "<bot>: <one-line task summary>"
   Body: includes link to the source task, list of changed files, syntax-check + test output.
8. On any failure in steps 3-7:
   git -C /tmp/<bot>_<task_id> checkout HEAD -- .
   git worktree remove /tmp/<bot>_<task_id>
   Mark mission failed in Task Hub with the actual error.
```

### What an autonomous mission MUST NEVER do

1. Edit files inside `/opt/universal_agent/src/` directly. The deployed working tree is **runtime production state**, not scratch space.
2. Skip the syntax check. `compile()` is one line and catches the entire SyntaxError class.
3. Leave `.bak` / `.swp` / `.orig` files in the working tree. Those are the fingerprint of a half-finished patch run; `/ship` and `pr-validate.yml` both refuse to merge a tree with these.
4. Commit invalid Python and rely on CI to catch it. Run the syntax check **before** committing.
5. Suppress errors. If the patch tool fails, the mission fails. Do not log-and-continue.
6. Open a PR to anything other than `main`. All PRs target `main` directly post-2026-05-10 (and post-2026-05-14 `feature/latest2` retirement).

### Reusable building blocks (shipped 2026-05-07)

The 8-step pattern is implemented as Python helpers under `src/universal_agent/vp/`. New autonomous-mission call sites should compose with these instead of hand-rolling git/subprocess sequences:

| Module | Public API | What to use it for |
|--------|------------|--------------------|
| [`vp/worktree_utils.py`](../../src/universal_agent/vp/worktree_utils.py) | `provision_worktree`, `teardown_worktree`, `syntax_check_changed_py`, `revert_changed_files`, `assert_no_artifacts`, `list_changed_py_files`, `detect_repo_root` | Steps 2, 3 (path discipline), 4 (compile check), 8 (revert + cleanup), and the `pr-validate.yml` artifact tripwire. |
| [`vp/autonomous_mission_executor.py`](../../src/universal_agent/vp/autonomous_mission_executor.py) | `execute_autonomous_mission(task, patch_fn, bot_name, ...)` returning `MissionResult` | The full 8-step orchestration. Side effects (test runner + PR creation) are dependency-injected so unit tests skip the network. The default PR creator now targets **`main`** (post-2026-05-10) and never auto-merges — human review is part of the contract. |

Test coverage lives in [`tests/unit/test_vp_worktree_utils.py`](../../tests/unit/test_vp_worktree_utils.py) and [`tests/unit/test_vp_autonomous_mission_executor.py`](../../tests/unit/test_vp_autonomous_mission_executor.py). The syntax-check test deliberately mirrors the 2026-05-07 docstring-in-arglist regression so we cannot weaken it without catching it in CI.

### What `pr-validate.yml` checks on every PR (the safety net)

Defined at [`.github/workflows/pr-validate.yml`](../../.github/workflows/pr-validate.yml). Runs on every `pull_request` against `main`:

1. `python -m py_compile` on every changed `.py` file.
2. `ruff check --select E9,F` (errors only, lint warnings allowed for now).
3. `pytest tests/unit -x -q` (fast unit-test subset).
4. Refuse merge if any `.py.bak` / `.swp` / `.orig` file is in the diff.

Tier 1 PRs go through the same workflow — there's no exemption. The autonomous-mission rules above are stricter (they MUST do steps 4-5 *before* opening the PR, in their own worktree) but the CI gate is identical.

### What `/ship` does at PR-open time (the last pre-CI gate)

[`/ship`](../../.claude/commands/ship.md) (post-2026-05-10 redesign) runs the same `compile()` check on every changed `.py` file before it commits + opens the PR — so even if you forget to run a local check, `/ship` catches the SyntaxError before the PR is opened. It also refuses to ship a working tree that contains `.py.bak` / `.swp` / `.orig` artifacts.

This means today's class of bug (broken docstring → SyntaxError → import storm) is now caught at three independent gates: agent's own pre-commit step, `/ship` at PR-open time, and `pr-validate.yml` on the PR itself. Any one of them would have prevented the 2026-05-07 incident.

### What `deploy.yml` does at merge time

The merge of a PR to `main` triggers `.github/workflows/deploy.yml`, which SSHs into the VPS, runs `git fetch + reset --hard origin/main`, runs `uv sync`, builds the web-ui, restarts services, and waits up to 8 minutes for the gateway `/api/v1/health` to return 200.

`deploy.yml`'s `on.push` trigger has a `paths-ignore` filter so docs-only commits (e.g. nightly drift reports, openclaw release sync state) merging to `main` don't restart the gateway. Mixed code+docs commits still deploy — that's the safe default.

---

## What NOT to Do

| ❌ Don't | ✅ Do Instead |
|----------|--------------|
| Push to a shared/legacy branch like `feature/latest2` | `feature/latest2` was retired 2026-05-14. Branch from `main` (`claude/<task>` for agent work, `kevin/<task>` or `feature/<task>` for operator-driven work), push that branch, open the PR. The Session Baseline Cleanup auto-lands every new session on fresh `main`, so re-using a stale local branch is never needed. |
| Push to `main` directly | Open a PR to `main`. Either via `/ship` (tier 1) or `gh pr create --base main` (tier 2). |
| Use the `develop` branch (it was retired 2026-05-10) | PR directly to `main`. |
| Run `ssh vps ...` to deploy | Let the operator merge the PR; deploy fires automatically. |
| Leave work on an unpushed local branch | Always push your branch and open the PR. |

---

## Deployment Architecture Reference

For full details on the CI/CD pipeline, branching strategy, and infrastructure:

- [Branching and Release Workflow](../06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md)
- [Architecture Overview](architecture_overview.md)
- [CI/CD Pipeline](ci_cd_pipeline.md)
- [Secrets and Environments](secrets_and_environments.md)

The canonical deployment contract is defined in `AGENTS.md` (symlinked from `CLAUDE.md`) at the repo root.

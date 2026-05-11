# AI Coder Coordination Instructions

> **Audience:** Any AI coding agent (Claude Code, Codex, Cursor, etc.) working on this repository.
> **Last updated:** 2026-05-01

## Overview

This repository uses a **branch-driven, automated deployment pipeline**. There are two roles in the workflow:

| Role | Responsibility |
|------|---------------|
| **AI Coder** (you) | Write code, commit, and push to the shared feature branch |
| **Ship Operator** | Runs `/ship` to promote the feature branch → `develop` → `main` and trigger CI/CD |

You are the AI Coder. You do **not** deploy. You write code and push it to the right place.

---

## Git Workflow Rules

### 1. Always work on `feature/latest2`

Before starting any work, ensure you are on the shared feature branch and up to date:

```bash
git checkout feature/latest2
git pull origin feature/latest2
```

**Do NOT create your own branches** (e.g., `claude/universal-agent-feature-*`, `codex/*`, `cursor/*`). All work goes directly on `feature/latest2`. Creating side branches causes merge conflicts and deployment delays.

### 2. Commit and push to `feature/latest2` only

When your work is done:

```bash
git add .
git commit -m "feat(component): descriptive message"
git push origin feature/latest2
```

Use [conventional commit](https://www.conventionalcommits.org/) prefixes: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`.

### 3. Never touch `develop` or `main`

You do not merge into, fast-forward, or push to these branches — ever. The `/ship` workflow handles the full promotion chain:

```
feature/latest2 → develop → main → GitHub Actions deploy
```

Someone else will run `/ship`. That is not your job.

### 4. Never run deployment commands

The following are **strictly prohibited**:

- `git push origin main`
- `git push origin develop`
- `git merge ... develop` or `git merge ... main`
- Any `ssh`, `rsync`, or manual VPS deployment commands
- Running CI/CD workflows manually

The deploy pipeline is fully automated via GitHub Actions, triggered by pushes to `main`.

### 5. Sync before starting new work

If your branch has fallen behind (e.g., after a `/ship` cycle synced everything):

```bash
git checkout feature/latest2
git fetch origin
git merge origin/feature/latest2
```

If there are conflicts, resolve them before writing new code.

### 5a. Always sync before *pushing*, not just before working

Multiple AI agents collaborate on `feature/latest2`. Your local checkout can go stale between commits — especially when an operator runs `/ship` or another coder pushes in parallel. Push rejections from this exact race used to require the ship operator to recover with a manual rebase.

**Mandatory pre-push sequence**, every push, no exceptions:

```bash
git fetch origin feature/latest2
git pull --rebase origin feature/latest2   # idempotent if already current
git push -u origin feature/latest2
```

If the rebase surfaces conflicts, resolve them locally and push. Never `--force` push.

### 6. Handoff signal

When your coding session is complete, make sure your **last commit is pushed to `origin/feature/latest2`** AND record the exact SHA in your final reply to the user (e.g. *"ready to /ship; latest commit is `abc123de`"*). That gives the ship operator a deterministic anchor — they verify the SHA is in `origin/feature/latest2` before promoting, which removes ambiguity when multiple agents have been touching the branch.

If you want to leave a detailed handoff note, create or update `SHIP_HANDOFF.md` at the repo root with:
- Summary of changes made
- List of commits with one-line descriptions (include SHAs)
- Any post-deploy smoke test suggestions
- Known risks or behavior changes

### 7. Notes for the Ship Operator

If you are the agent running `/ship` (you may be reading this in a different session than the AI Coder who produced the commits), apply the same pre-push hygiene before promoting:

```bash
git fetch origin feature/latest2 develop main
git checkout feature/latest2
git pull --rebase origin feature/latest2     # ensures local matches remote BEFORE you start
# verify expected handoff SHA is in `git log` if the coder gave you one
# then proceed with the standard /ship promotion chain
```

Never assume the local working copy is current. Always re-fetch and rebase first. This eliminates the failure mode where the operator's stale checkout collides with a remote commit a coder just pushed.

If `/ship` fails on push with a non-fast-forward / 403 rejection, the recovery is the same:

```bash
git pull --rebase origin <branch>
# re-run the failed step
```

Never `git push --force` to `feature/latest2`, `develop`, or `main`.

---

## Agent-Type → Workflow Matrix

> **Audience:** anyone wiring up a new agent (CODIE-style cleanup, Cody scaffold-builder, scheduled VP coder mission, etc.) or asking "should this go through a PR?".
> **Codified after the 2026-05-07 import storm:** an autonomous patcher mutated `/opt/universal_agent/src/` directly, the patches were syntactically invalid, nothing checked them, and every cron that imports the durable package failed for 8+ hours.

| Tier | Who | What goes through `/ship` directly | What MUST go through PR |
|---|---|---|---|
| **1 — Human-supervised conversational coding** | Kevin + Claude Code (you, this conversation), Antigravity, Codex when Kevin is in the loop | Routine fix-and-iterate, doc edits, docstring tweaks, conversational debug commits | CICD changes, deploy-pipeline edits, durable-state schema changes, autonomous-mission code |
| **2 — Autonomous missions** (CODIE proactive cleanup, Cody scaffold-builder, demo-builder, scheduled VP coder, anything cron- or heartbeat-driven that mutates source) | Bots running unattended on the VPS | **Nothing.** Tier 2 NEVER pushes directly to `feature/latest2`. | **Everything.** Worktree → patch → syntax-check → unit tests → push to `<bot-name>/<task-id>` branch → open PR → CI passes → [`pr-auto-merge.yml`](../../.github/workflows/pr-auto-merge.yml) auto-merges → [`auto-promote-to-prod.yml`](../../.github/workflows/auto-promote-to-prod.yml) deploys (no human merge needed for `claude/*` head branches). |
| **3 — GitHub branch protection** | The safety net | N/A | Enforces tier 1 vs. tier 2 at the push level — see [`ci_cd_pipeline.md`](ci_cd_pipeline.md). |

### When in doubt: PR

If the change touches any of these, open a PR even from tier 1:

- `.github/workflows/*` (CI/CD)
- `.claude/commands/ship.md` or anything else `/ship` reads
- `src/universal_agent/durable/` (durable state schema or accessors)
- `src/universal_agent/services/dispatch_service.py` or `task_hub.py` (orchestration core)
- `gateway_server.py` cron registration helpers

The cost of a PR (~5 min) is far below the cost of a 30-cron-storm + production recovery (today: ~6 hours of operator time).

---

## Autonomous Mission Workflow (Tier 2 in detail)

> **For agents that run unattended.** Cron-triggered (CODIE proactive cleanup), heartbeat-triggered (Cody scaffold-builder), or task-hub-triggered (VP coder missions). They share a single contract.

The core invariant: **an autonomous mission must never leave the deployed working tree (`/opt/universal_agent/`) in a state that doesn't match a committed SHA.**

### The 8-step pattern

```
1. Pick up task from Task Hub (or wherever the mission's input comes from)
2. Create a fresh worktree from feature/latest2:
     git worktree add /tmp/<bot>_<task_id> feature/latest2
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
7. Open a PR via GitHub API targeting feature/latest2.
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

### Reusable building blocks (shipped 2026-05-07)

The 8-step pattern is implemented as Python helpers under
`src/universal_agent/vp/`. New autonomous-mission call sites should
compose with these instead of hand-rolling git/subprocess sequences:

| Module | Public API | What to use it for |
|--------|------------|--------------------|
| [`vp/worktree_utils.py`](../../src/universal_agent/vp/worktree_utils.py) | `provision_worktree`, `teardown_worktree`, `syntax_check_changed_py`, `revert_changed_files`, `assert_no_artifacts`, `list_changed_py_files`, `detect_repo_root` | Steps 2, 3 (path discipline), 4 (compile check), 8 (revert + cleanup), and the `pr-validate.yml` artifact tripwire. |
| [`vp/autonomous_mission_executor.py`](../../src/universal_agent/vp/autonomous_mission_executor.py) | `execute_autonomous_mission(task, patch_fn, bot_name, ...)` returning `MissionResult` | The full 8-step orchestration. Side effects (test runner + PR creation) are dependency-injected so unit tests skip the network. The default PR creator targets `feature/latest2` and never auto-merges — human review is part of the contract. |

Test coverage lives in
[`tests/unit/test_vp_worktree_utils.py`](../../tests/unit/test_vp_worktree_utils.py)
and
[`tests/unit/test_vp_autonomous_mission_executor.py`](../../tests/unit/test_vp_autonomous_mission_executor.py).
The syntax-check test deliberately mirrors the 2026-05-07
docstring-in-arglist regression so we cannot weaken it without
catching it in CI.

`vp/worker_loop.py:_provision_workspace` (lines ~639-720) still has its
own inline worktree path for `vp.coder.primary` missions. That path is
correct for the running production loop; refactoring it to use
`worktree_utils.py` is intentionally a follow-up PR so the building
blocks land first and ship through the new tier-2 PR workflow as
their own first real test.

### What `pr-validate.yml` will check on every PR (the safety net)

Defined at [`.github/workflows/pr-validate.yml`](../../.github/workflows/pr-validate.yml). Runs on every `pull_request` against `feature/latest2`, `develop`, or `main`:

1. `python -m py_compile` on every changed `.py` file.
2. `ruff check --select E9,F` (errors only, lint warnings allowed for now).
3. `pytest tests/unit -x -q` (fast unit-test subset).
4. Refuse merge if any `.py.bak` / `.swp` / `.orig` file is in the diff.

Tier 1 PRs go through the same workflow — there's no exemption. The autonomous-mission rules above are stricter (they MUST do steps 4-5 *before* opening the PR, in their own worktree) but the CI gate is identical.

### What `/ship` does at deploy time (the last gate)

[`/ship`](../../.claude/commands/ship.md) (post-2026-05-07 update) runs the same `compile()` check on every changed `.py` file before it commits — so even if you forget to run a local check, `/ship` will catch a syntax error before pushing it to `develop`. It also refuses to ship a working tree that contains `.py.bak` / `.swp` / `.orig` artifacts.

This means today's class of bug (broken docstring → SyntaxError → import storm) is now caught at three independent gates: agent's own pre-commit step, `pr-validate.yml` on PR, `/ship` at deploy time. Any one of them would have prevented the 2026-05-07 incident.

> **As of 2026-05-11:** the standard `claude/*` PR flow is fully automated end-to-end via [`pr-auto-merge.yml`](../../.github/workflows/pr-auto-merge.yml) + [`auto-promote-to-prod.yml`](../../.github/workflows/auto-promote-to-prod.yml). PR open → CI passes → auto-merge → auto-promote → deploy fires, with no operator action required. `/ship` is now an **optional manual fallback** for direct-push or hot-fix paths that bypass the PR flow. The hygiene checks above still apply when it's invoked.

---

## What NOT to Do

| ❌ Don't | ✅ Do Instead |
|----------|--------------|
| Create `claude/feature-xyz` branches | Commit directly to `feature/latest2` |
| Push to `main` or `develop` | Push only to `feature/latest2` |
| Run `ssh vps ...` to deploy | Let the operator run `/ship` |
| Merge `develop` into your branch | Merge `origin/feature/latest2` to stay current |
| Leave work on an unpushed local branch | Always `git push origin feature/latest2` |

---

## Deployment Architecture Reference

For full details on the CI/CD pipeline, branching strategy, and infrastructure:

- [Architecture Overview](architecture_overview.md)
- [CI/CD Pipeline](ci_cd_pipeline.md)
- [Secrets and Environments](secrets_and_environments.md)

The canonical deployment contract is defined in `AGENTS.md` at the repo root.

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

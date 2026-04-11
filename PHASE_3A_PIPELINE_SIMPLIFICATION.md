# Phase 3A: Pipeline Simplification — From Three Workflows to One

**Status:** Not started. Begin only after Phase 2 (local dev) is verified working.
**Where this lives:** Save to `docs/pipeline/PHASE_3A_PIPELINE_SIMPLIFICATION.md` in the repo.

---

## What this does

Collapses the current three-workflow deploy pipeline into a simpler two-step flow:

**Current pipeline (three workflows):**
```
feature/latest2 → push to develop → deploy-staging.yml fires → staging VPS updates
                                   → promote-develop-to-main.yml → fast-forward main
                                                                  → deploy-prod.yml fires → production VPS updates
```

**Target pipeline (one workflow):**
```
feature/latest2 → PR into develop → CI runs, code lands on develop (nothing deploys)
                                   → fast-forward main → deploy.yml fires → VPS updates
```

One running environment. One deploy workflow. `develop` is for integration and review only. `main` is the deploy trigger.

---

## Why

- You can't afford two running environments (inference budget, Telegram bot conflict, VP worker queue contention).
- Staging was running with neutered secrets and wasn't exercising real code paths — it wasn't catching real bugs.
- You now have local dev (`localhost:3000`) for testing before you ship. That replaces what staging was supposed to do.
- Simpler pipeline = fewer things to break = less fear of deploying.

---

## What changes

### Deleted
- `.github/workflows/deploy-staging.yml` — no longer needed
- `.github/workflows/promote-develop-to-main.yml` — its job collapses into a plain `git push`
- `/opt/universal-agent-staging` on the VPS — archived, not deleted
- Staging systemd units (`universal-agent-staging-gateway`, etc.) — stopped and disabled

### Created
- `.github/workflows/deploy.yml` — single deploy workflow, triggers on push to `main` + `workflow_dispatch`

### Modified
- Slash commands: `/stagecommit`, `/promotecommit`, `/productioncommit` all get replaced by `/ship`, `/checkpoint`, `/rollback`

### Untouched
- `.github/workflows/nightly-doc-drift-audit.yml`
- `.github/workflows/openclaw-release-sync.yml`
- All scripts under `scripts/` that the deploy workflow calls
- All GitHub Actions secrets
- The VPS itself (except archiving the staging directory and disabling staging units)

---

## The new mental model

### Branches
- **`feature/latest2`** — where you work. All changes happen here.
- **`develop`** — integration branch. PRs from `feature/latest2` land here. CI runs. Nothing deploys.
- **`main`** — deploy branch. When `main` moves, the deploy workflow fires and the VPS gets new code.

### The four operations

**`/ship`** — the 95% case. Commit on `feature/latest2`, open PR to `develop`, auto-merge, fast-forward `main` to `develop`, deploy fires, VPS updates. One command, end-to-end.

**`/checkpoint`** — standalone fast-forward of `main` to `develop` without making a new commit. For when you want to deploy what's already on `develop`.

**`/rollback`** — reset `main` to its previous commit and force-push. The deploy fires with the old code. The VPS rolls back. `develop` is untouched — your work-in-progress is preserved.

**`/status`** — read-only. Shows current branch, `develop` SHA, `main` SHA, whether they match, latest deploy run status.

### Rollback safety
- `main` only moves forward (fast-forward) during normal operations
- Rolling back is an explicit `git push --force-with-lease` on `main` to a previous SHA
- The VPS at `/opt/universal_agent` is itself a git clone of `main` — you can SSH in and `git log` to see the deploy history
- In an emergency, you can roll back directly on the VPS: `cd /opt/universal_agent && git reset --hard HEAD~1 && sudo systemctl restart universal-agent-*`

### Database and state
- Stateful data (SQLite files, etc.) is NOT touched by code deploys
- Rolling back code does NOT affect the database
- If you roll back across a breaking schema change, old code may error on the new DB shape — this is a diagnostic signal ("the problem is the schema"), not a disaster. Fix forward.

---

## The new deploy workflow: `deploy.yml`

This is ~90% copy-paste from the existing `deploy-prod.yml`, with these changes:

| Setting | Old (`deploy-prod.yml`) | New (`deploy.yml`) |
|---|---|---|
| Filename | `deploy-prod.yml` | `deploy.yml` |
| Trigger | `push: branches: [main]` + `workflow_dispatch` | Same (no change) |
| Target directory | `/opt/universal_agent` | Same |
| Branch checkout | `git fetch origin main && git reset --hard origin/main` | Same |
| Infisical environment | `production` | Same |
| Services restarted | gateway, api, webui, telegram, VP workers | Same |

The workflow is essentially identical to `deploy-prod.yml`. The main work is deleting the other two workflows and rewriting the slash commands.

---

## The new slash commands

### `/ship`

```
// turbo-all
# Ship
Commit, push, deploy. End-to-end from feature/latest2 to live VPS.

## Pre-flight
1. Ensure on feature/latest2 with dirty working tree.

## Phase 1: Commit & Push
2. git add . && git diff --staged --stat
3. Generate conventional commit message
4. git commit --no-verify -m "message"
5. Verify: git log --oneline -1
6. git push origin feature/latest2

## Phase 2: Land on develop
7. Open or update PR: gh pr create --base develop --head feature/latest2 --fill
8. Enable auto-merge: gh pr merge --auto --squash
9. Wait for merge

## Phase 3: Deploy
10. Fast-forward main: git push origin origin/develop:main
11. Wait for deploy: gh run list --workflow=deploy.yml --limit 1
12. gh run watch <RUN_ID> --exit-status

## Report
13. "✅ Deployed (run #NNN). Open app.clearspringcg.com to verify."

## Rules
- ❌ Never claim deployed until CI shows success
- ❌ Never commit directly to develop or main
- ✅ Always end on feature/latest2
- ✅ Always use --no-verify on commits
```

### `/checkpoint`

```
// turbo-all
# Checkpoint
Deploy what's already on develop without making a new commit.

## Steps
1. git fetch origin develop main
2. Compare SHAs — if identical, "nothing to deploy" and STOP
3. git push origin origin/develop:main
4. Wait for deploy workflow
5. Report result

## Rules
- ❌ Do not make any commits
- ✅ Always verify develop != main before pushing
```

### `/rollback`

```
// turbo-all
# Rollback
Reset main to its previous commit. VPS gets the old code.

## Steps
1. git fetch origin main
2. Find previous SHA: git log --oneline origin/main -5 (show to user, confirm which)
3. git push --force-with-lease origin <SHA>:main
4. Wait for deploy workflow
5. Report result

## Rules
- ❌ Never touch develop — work-in-progress is preserved
- ✅ Always show the user the SHA list and confirm before force-pushing
- ✅ If deploy fails, offer to re-run: gh workflow run deploy.yml
```

### `/status`

```
// turbo-all
# Status
Read-only pipeline status check.

## Steps
1. git fetch origin develop main
2. Report: develop SHA, main SHA, whether they match
3. gh run list --workflow=deploy.yml --limit 3
4. Report latest deploy status

## Rules
- ❌ Read-only. Do not modify anything.
```

---

## VPS cutover script: `scripts/cutover_to_single_env.sh`

A one-shot script run manually on the VPS to consolidate from two environments to one. Must be idempotent.

1. Stop and disable staging systemd units
2. Move `/opt/universal-agent-staging` to `/opt/universal-agent-staging.archived.YYYYMMDD`
3. Verify `/opt/universal_agent` is healthy
4. Print summary

**Does NOT touch `/opt/universal_agent`, does NOT restart production services, does NOT modify Infisical.**

---

## Rollout sequence

1. Create branch `feature/pipeline-rebuild` off `feature/latest2`
2. Write `deploy.yml` (copy from `deploy-prod.yml`, rename)
3. Delete `deploy-staging.yml` and `promote-develop-to-main.yml`
4. Write the new slash commands
5. Write the cutover script
6. Show all diffs to the owner, get approval
7. Commit and push `feature/pipeline-rebuild`
8. Owner runs cutover script on VPS manually
9. Merge `feature/pipeline-rebuild` → `feature/latest2` → PR to `develop` → merge → fast-forward `main`
10. Watch the first deploy via the new `deploy.yml`
11. If anything fails: SSH to VPS, `git reset --hard HEAD~1`, restart services

---

## Acceptance criteria

- [ ] `.github/workflows/deploy.yml` exists and triggers on push to `main`
- [ ] `.github/workflows/deploy-staging.yml` does not exist
- [ ] `.github/workflows/deploy-prod.yml` does not exist (merged into `deploy.yml`)
- [ ] `.github/workflows/promote-develop-to-main.yml` does not exist
- [ ] `scripts/cutover_to_single_env.sh` exists and has been run on the VPS
- [ ] `/opt/universal-agent-staging` archived on VPS
- [ ] Staging systemd units stopped and disabled
- [ ] New slash commands (`/ship`, `/checkpoint`, `/rollback`, `/status`) documented
- [ ] A test `/ship` has run end-to-end and VPS came back healthy
- [ ] Old slash commands (`/stagecommit`, `/promotecommit`, `/productioncommit`) documented as deprecated

---

## End of Phase 3A

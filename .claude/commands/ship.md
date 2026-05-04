---
description: Run the commit changes through to production and deploy CI/CD
---

# Ship Workflow

This workflow automates the canonical deployment path for the Universal Agent repository. It commits all pending changes on your current branch, merges them into `develop`, fast-forwards `main` with `develop`, and pushes to GitHub to trigger the automated CI/CD deployment workflow.

## Notes for the Ship Operator (Pre-push Hygiene)

If you are the agent running `/ship` (you may be reading this in a different session than the AI Coder who produced the commits), apply the same pre-push hygiene before promoting:

- Never assume the local working copy is current. Always re-fetch and rebase first. This eliminates the failure mode where the operator's stale checkout collides with a remote commit a coder just pushed.
- If `/ship` fails on push with a non-fast-forward / 403 rejection, the recovery is the same: `git pull --rebase origin <branch>` and re-run the failed step. **Never `git push --force`.**

// turbo-all

## Execute Full Deployment Pipeline

This script stages your work, updates `develop`, fast-forwards `main`, pushes to trigger CI/CD, and returns you to your previous branch.

```bash
set -e

# 1. Save state and validate branch
CURRENT_BRANCH=$(git branch --show-current)
echo "🚀 Shipping from branch: $CURRENT_BRANCH"

# Guard: never ship directly from main or develop
if [ "$CURRENT_BRANCH" = "main" ] || [ "$CURRENT_BRANCH" = "develop" ]; then
    echo "❌ ERROR: /ship must be run from a feature branch (e.g., feature/latest2), not '$CURRENT_BRANCH'."
    echo "   Switch to your feature branch first: git checkout feature/latest2"
    exit 1
fi

# 2. Commit and sync current branch
git add .
git commit -m "chore: deployment auto-commit via /ship" || echo "No pending changes to commit."

echo "🔄 Fetching remote state to check for parallel activity..."
git fetch origin $CURRENT_BRANCH develop main

LOCAL_SHA=$(git rev-parse @)
REMOTE_SHA=$(git rev-parse @{u} 2>/dev/null || echo "no_remote")

if [ "$LOCAL_SHA" != "$REMOTE_SHA" ] && [ "$REMOTE_SHA" != "no_remote" ]; then
    echo "⚠️ Remote changes detected (potential Claude Code activity). Performing pre-flight rebase..."
    git pull --rebase origin $CURRENT_BRANCH
else
    echo "✅ Local branch is up to date. Proceeding with standard push..."
fi

git push origin $CURRENT_BRANCH

# 3. Merge into develop
echo "🔄 Updating integration branch (develop)..."
git fetch origin
git checkout develop
git pull origin develop
git merge $CURRENT_BRANCH -m "Merge branch '$CURRENT_BRANCH' into develop for deployment"
git push origin develop

# 4. Fast-forward main to deploy
echo "🚢 Fast-forwarding production branch (main)..."
git checkout main
git pull origin main
git merge develop --ff-only
git push origin main

# 5. Return to feature branch
echo "🧹 Returning to feature branch ($CURRENT_BRANCH)..."
git checkout $CURRENT_BRANCH

# 6. Fast-forward feature branch to stay in sync with main
echo "🔄 Syncing $CURRENT_BRANCH with main..."
git merge main --ff-only || echo "⚠️ Feature branch has diverged from main — manual merge may be needed."

# 7. Verify deployment completion
TARGET_SHA=$(git rev-parse main)
echo "👀 Waiting for GitHub Actions to register the deployment for commit $TARGET_SHA..."

# Prevent interactive prompts or colors from mangling output inside the script
export NO_COLOR=1
export GH_NO_UPDATE_NOTIFIER=1

LATEST_RUN=""
for i in {1..15}; do
    LATEST_RUN=$(gh run list --branch main --commit "$TARGET_SHA" --limit 1 --json databaseId -q ".[0].databaseId" | tr -dc '0-9')
    if [ -n "$LATEST_RUN" ]; then
        break
    fi
    sleep 4
done

if [ -n "$LATEST_RUN" ]; then
    echo "Watching pipeline run: $LATEST_RUN"
    if gh run watch "$LATEST_RUN" --exit-status; then
        echo "🎯 Deployment successfully completed and verified!"
    else
        echo "❌ Deployment FAILED. Please check GitHub Actions logs."
        exit 1
    fi
else
    echo "⚠️ Could not find a running GHA deployment for commit $TARGET_SHA after waiting. Please manually verify."
fi
```

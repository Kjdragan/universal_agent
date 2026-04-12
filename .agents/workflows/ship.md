---
description: Run the commit changes through to production and deploy CI CD
---

# Ship Workflow

This workflow automates the canonical deployment path for the Universal Agent repository. It commits all pending changes on your current branch, merges them into `develop`, fast-forwards `main` with `develop`, and pushes to GitHub to trigger the automated CI/CD deployment workflow.

// turbo-all

## Execute Full Deployment Pipeline
This script stages your work, updates `develop`, fast-forwards `main`, pushes to trigger CI/CD, and returns you to your previous branch.

```bash
set -e

# 1. Save state and commit
CURRENT_BRANCH=$(git branch --show-current)
echo "🚀 Shipping from branch: $CURRENT_BRANCH"

git add .
git commit -m "chore: deployment auto-commit via /ship" || echo "No pending changes to commit."
git push origin $CURRENT_BRANCH

# 2. Merge into develop
echo "🔄 Updating integration branch (develop)..."
git fetch origin
if [ "$CURRENT_BRANCH" != "develop" ]; then
    git checkout develop
    git pull origin develop
    git merge $CURRENT_BRANCH -m "Merge branch '$CURRENT_BRANCH' into develop for deployment"
    git push origin develop
fi

# 3. Fast-forward main to deploy
echo "🚢 Fast-forwarding production branch (main)..."
git checkout main
git pull origin main
git merge develop --ff-only
git push origin main

# 4. Cleanup
echo "🧹 Returning to original branch ($CURRENT_BRANCH)..."
git checkout $CURRENT_BRANCH

# 5. Verify deployment completion
echo "👀 Waiting for GitHub Actions deployment to finish..."
sleep 5 # Give GHA a moment to start the workflow

# Prevent interactive prompts or colors from mangling output inside the script
export NO_COLOR=1
export GH_NO_UPDATE_NOTIFIER=1

LATEST_RUN=$(gh run list --branch main --limit 1 --json databaseId -q ".[0].databaseId" | tr -dc '0-9')
if [ -n "$LATEST_RUN" ]; then
    echo "Watching pipeline run: $LATEST_RUN"
    gh run watch $LATEST_RUN --exit-status
    echo "🎯 Deployment successfully completed and verified!"
else
    echo "⚠️ Could not find a running GHA deployment. Please manually verify."
fi
```

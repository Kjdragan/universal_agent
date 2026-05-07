---
description: Run the commit changes through to production and deploy CI/CD
---

# Ship Workflow

This workflow automates the canonical deployment path for the Universal Agent repository. It commits all pending changes on your current branch, merges them into `develop`, fast-forwards `main` with `develop`, and pushes to GitHub to trigger the automated CI/CD deployment workflow.

> **Where can `/ship` run?** Anywhere with the right git remote and a working `gh` CLI session: your desktop, a VPS dev-tree at `/home/ua/dev/universal_agent` (provisioned per Phase D), Antigravity Remote-SSH'd into the VPS, or a sandbox-Claude with the repo cloned. The workflow is **checkout-agnostic** — it operates on `origin` only, so any clone tracking the same GitHub remote can promote feature/latest2 to production. See [`docs/WORKFLOW.md`](../../docs/WORKFLOW.md) for daily-flow context.

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

# 2. Pre-commit safety: refuse to ship a corrupt working tree.
#
# These checks are the lesson from the 2026-05-07 import storm: an
# autonomous patcher mangled durable/state.py (docstring inside a
# parameter list = SyntaxError), no syntax check ran, the broken file
# sat on the working tree, and every cron that imports the durable
# package failed for 8+ hours. /ship is the last gate before that kind
# of corruption goes to production. We keep it cheap (~2s on a normal
# diff) and bail loudly if anything looks wrong.

# 2a. Block .py.bak / .swp / .orig artifacts. These are the fingerprint
#     of a half-finished autonomous patch run.
STRAY=$(find . -path ./.git -prune -o -path ./.venv -prune -o -path ./node_modules -prune \
        -o -type f \( -name '*.py.bak' -o -name '*.swp' -o -name '*.py.orig' \) -print 2>/dev/null \
        | head -10)
if [ -n "$STRAY" ]; then
    echo "❌ ERROR: refusing to /ship — patcher artifacts present in the working tree:"
    echo "$STRAY" | sed 's/^/   /'
    echo "   Remove these (they're left over from an autonomous tool that didn't clean up)"
    echo "   then re-run /ship."
    exit 1
fi

# 2b. Syntax-check every modified or untracked .py file. compile() raises
#     SyntaxError on the exact (file, line) for invalid Python and exits
#     non-zero. Catches today's class of bug in seconds.
echo "🔬 Pre-commit syntax check on changed .py files..."
PY_FILES=$(git status --porcelain | awk '/^.[MAU?].*\.py$/ {print $NF}; /^[MAU?].*\.py$/ {print $NF}' | sort -u)
if [ -n "$PY_FILES" ]; then
    SYNTAX_FAILS=0
    for f in $PY_FILES; do
        if [ -f "$f" ]; then
            python3 -c "compile(open('$f').read(), '$f', 'exec')" 2>&1 \
                || { echo "   ❌ syntax error in $f"; SYNTAX_FAILS=$((SYNTAX_FAILS+1)); }
        fi
    done
    if [ "$SYNTAX_FAILS" -gt 0 ]; then
        echo "❌ ERROR: $SYNTAX_FAILS file(s) have Python syntax errors. /ship aborted."
        echo "   Fix the syntax errors above, then re-run /ship."
        exit 1
    fi
    echo "✅ All $(echo "$PY_FILES" | wc -l) changed Python file(s) compile cleanly."
else
    echo "   (no .py changes to check)"
fi

# 2c. Show the operator what is about to be shipped (visibility, not gating).
echo "📋 Pending changes about to ship:"
git status --porcelain | head -25 || true
TOTAL_CHANGES=$(git status --porcelain | wc -l)
if [ "$TOTAL_CHANGES" -gt 25 ]; then
    echo "   ... ($((TOTAL_CHANGES - 25)) more — git status for full list)"
fi

# 3. Commit and sync current branch
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

# 4. Merge into develop
echo "🔄 Updating integration branch (develop)..."
git fetch origin
git checkout develop
git pull origin develop
git merge $CURRENT_BRANCH -m "Merge branch '$CURRENT_BRANCH' into develop for deployment"
git push origin develop

# 5. Fast-forward main to deploy
echo "🚢 Fast-forwarding production branch (main)..."
git checkout main
git pull origin main
git merge develop --ff-only
git push origin main

# 6. Return to feature branch
echo "🧹 Returning to feature branch ($CURRENT_BRANCH)..."
git checkout $CURRENT_BRANCH

# 7. Fast-forward feature branch to stay in sync with main
echo "🔄 Syncing $CURRENT_BRANCH with main..."
git merge main --ff-only || echo "⚠️ Feature branch has diverged from main — manual merge may be needed."

# 8. Verify deployment completion
TARGET_SHA=$(git rev-parse main)
echo "👀 Deploy triggered. main = $TARGET_SHA"
echo "   GitHub Actions URL: https://github.com/Kjdragan/universal_agent/actions"

# Prevent interactive prompts or colors from mangling output inside the script
export NO_COLOR=1
export GH_NO_UPDATE_NOTIFIER=1

# In-script deploy watching is OPT-IN — only runs if `gh` is installed AND
# authenticated. /ship MUST work without `gh` because:
#   - /ship can run from any clone with the right git remote (per workflow header)
#   - Sandboxed Claude Code sessions on the web don't ship with gh installed
#   - The deploy itself doesn't depend on the operator watching it
# The git ops above are what actually deploy. Watching is just feedback.
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    echo "🛰️  gh CLI authenticated — watching deploy in-script..."
    LATEST_RUN=""
    for i in {1..15}; do
        LATEST_RUN=$(gh run list --branch main --commit "$TARGET_SHA" --limit 1 --json databaseId -q ".[0].databaseId" 2>/dev/null | tr -dc '0-9')
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
else
    echo "ℹ️  gh CLI not installed or not authenticated in this environment."
    echo "   Skipping in-script deploy watch — push to main has triggered GitHub Actions."
    echo "   Verify deploy at: https://github.com/Kjdragan/universal_agent/actions"
    echo "   To enable in-script monitoring on this machine: install gh and run 'gh auth login'."
fi
```

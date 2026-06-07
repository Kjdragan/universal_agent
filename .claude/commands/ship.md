---
description: Commit + push current branch, open PR to main, enable auto-merge — walk away
---

# Ship Workflow

Run this from a feature branch. It commits any pending changes, opens a PR to `main`, and enables auto-merge. Auto-merge waits for `pr-validate.yml` CI to pass and then merges automatically. The merge to `main` triggers `.github/workflows/deploy.yml`. You don't need to babysit it.

Full model: [`docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md`](../../docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md)

// turbo-all

```bash
set -e

CURRENT_BRANCH=$(git branch --show-current)
echo "🚀 Shipping from branch: $CURRENT_BRANCH"

# Guard: never ship from main, and refuse the legacy 'develop' branch
# (retired 2026-05-10).
if [ "$CURRENT_BRANCH" = "main" ]; then
    echo "❌ ERROR: /ship must be run from a feature branch, not 'main'."
    exit 1
fi
if [ "$CURRENT_BRANCH" = "develop" ]; then
    echo "❌ ERROR: 'develop' was retired 2026-05-10. PR directly to main."
    exit 1
fi

# Pre-flight: refuse a working tree with patcher artifacts
# (.py.bak / .swp / .py.orig — fingerprint of a half-finished autonomous patch run).
STRAY=$(find . -path ./.git -prune -o -path ./.venv -prune -o -path ./node_modules -prune \
        -o -type f \( -name '*.py.bak' -o -name '*.swp' -o -name '*.py.orig' \) -print 2>/dev/null \
        | head -10)
if [ -n "$STRAY" ]; then
    echo "❌ Patcher artifacts in working tree — refusing to ship:"
    echo "$STRAY" | sed 's/^/   /'
    exit 1
fi

# Pre-flight: python compile() check on changed .py files
echo "🔬 Pre-flight syntax check..."
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
        echo "❌ $SYNTAX_FAILS file(s) have syntax errors. /ship aborted."
        exit 1
    fi
    echo "✅ $(echo "$PY_FILES" | wc -l) file(s) compile cleanly."
fi

# Stage + commit pending changes (if any)
if [ -n "$(git status --porcelain)" ]; then
    git add .
    git commit -m "chore: /ship auto-commit"
else
    echo "✅ No pending changes to commit."
fi

# Push + verify origin actually advanced (silent-no-op-push guard)
git push -u origin "$CURRENT_BRANCH"
git fetch origin "$CURRENT_BRANCH" --quiet
POST_SHA=$(git rev-parse "origin/$CURRENT_BRANCH")
if [ "$POST_SHA" != "$(git rev-parse HEAD)" ]; then
    echo "❌ FATAL: push reported success but origin/$CURRENT_BRANCH is not at HEAD."
    echo "   Local HEAD: $(git rev-parse HEAD | cut -c1-8)"
    echo "   origin SHA: ${POST_SHA:0:8}"
    exit 1
fi

# Best-effort deslop pass on the diff (NON-blocking — never delays shipping).
# Before opening the PR, take a quick behavior-preserving cleanup pass over the
# change set: invoke the `technical-deslop` skill (or, for just a quick report,
# `python scripts/deslop_advisory.py --diff <a precomputed diff>`), apply ONLY
# behavior-preserving removals, and re-run the relevant tests. This is purely
# best-effort: if it errors, finds nothing, or there's no time, proceed to the
# PR anyway. It must NEVER block, fail, or delay the ship.
#
# Note: this is a manual cue for the shipping agent, not an automated gate —
# the `( ... ) || true` below guarantees nothing here can abort /ship.
( echo "🧹 (optional) Consider a quick deslop pass on the diff before the PR — best-effort, skip if pressed." ) || true

# gh CLI fallback — print the PR-create URL and exit
export NO_COLOR=1
export GH_NO_UPDATE_NOTIFIER=1
if ! command -v gh >/dev/null 2>&1 || ! gh auth status >/dev/null 2>&1; then
    echo "ℹ️ gh CLI not authenticated. Open the PR manually:"
    echo "   https://github.com/Kjdragan/universal_agent/compare/main...$CURRENT_BRANCH?expand=1"
    exit 0
fi

# Find or create the PR to main
PR_URL=$(gh pr list --head "$CURRENT_BRANCH" --base main --json url -q '.[0].url' 2>/dev/null || echo "")
if [ -z "$PR_URL" ]; then
    PR_TITLE=$(git log -1 --pretty=%s)
    PR_BODY_TMP=$(mktemp)
    {
      echo "## Summary"
      echo ""
      echo "Commits in this PR (vs origin/main):"
      echo ""
      git log --pretty="- %h %s" "origin/main..HEAD"
      echo ""
      echo "Opened by /ship from \`$CURRENT_BRANCH\`."
    } > "$PR_BODY_TMP"
    PR_URL=$(gh pr create --base main --head "$CURRENT_BRANCH" \
        --title "$PR_TITLE" --body-file "$PR_BODY_TMP" 2>&1 | tail -1)
    rm -f "$PR_BODY_TMP"
    if ! echo "$PR_URL" | grep -q "^https://"; then
        echo "❌ gh pr create failed: $PR_URL"
        exit 1
    fi
    echo "✅ PR opened: $PR_URL"
else
    echo "ℹ️ PR already exists: $PR_URL (new commits pushed; CI re-runs)"
fi

# Enable auto-merge — GitHub waits for CI then merges automatically
gh pr merge "$PR_URL" --auto --merge 2>&1 | tail -3
echo "✅ Auto-merge enabled. Walk away — PR will merge when CI is green."
echo "   $PR_URL"
```

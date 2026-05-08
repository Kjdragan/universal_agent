set -e
git checkout feature/latest2

# 1. Save state and validate branch
CURRENT_BRANCH=$(git branch --show-current)
echo "🚀 Shipping from branch: $CURRENT_BRANCH"

if [ "$CURRENT_BRANCH" = "main" ] || [ "$CURRENT_BRANCH" = "develop" ]; then
    echo "❌ ERROR: /ship must be run from a feature branch, not '$CURRENT_BRANCH'."
    exit 1
fi

# 2. Pre-commit safety
STRAY=$(find . -path ./.git -prune -o -path ./.venv -prune -o -path ./node_modules -prune \
        -o -type f \( -name '*.py.bak' -o -name '*.swp' -o -name '*.py.orig' \) -print 2>/dev/null \
        | head -10)
if [ -n "$STRAY" ]; then
    echo "❌ ERROR: refusing to /ship — patcher artifacts present:"
    echo "$STRAY" | sed 's/^/   /'
    exit 1
fi

echo "�� Pre-commit syntax check on changed .py files..."
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
        exit 1
    fi
    echo "✅ All $(echo "$PY_FILES" | wc -l) changed Python file(s) compile cleanly."
else
    echo "   (no .py changes to check)"
fi

echo "📋 Pending changes about to ship:"
git status --porcelain | head -25 || true

git add .
git commit -m "chore: deployment auto-commit via /ship" || echo "No pending changes to commit."

echo "🔄 Fetching remote state..."
git fetch origin $CURRENT_BRANCH develop main

LOCAL_SHA=$(git rev-parse @)
REMOTE_SHA=$(git rev-parse @{u} 2>/dev/null || echo "no_remote")

if [ "$LOCAL_SHA" != "$REMOTE_SHA" ] && [ "$REMOTE_SHA" != "no_remote" ]; then
    echo "⚠️ Remote changes detected. Performing pre-flight rebase..."
    git pull --rebase origin $CURRENT_BRANCH
else
    echo "✅ Local branch is up to date."
fi

git push origin $CURRENT_BRANCH

echo "🔄 Updating integration branch (develop)..."
git fetch origin
git checkout develop
git pull origin develop
git merge $CURRENT_BRANCH -m "Merge branch '$CURRENT_BRANCH' into develop for deployment"
git push origin develop

echo "🚢 Fast-forwarding production branch (main)..."
git checkout main
git pull origin main
git merge develop --ff-only
git push origin main

echo "🧹 Returning to $CURRENT_BRANCH..."
git checkout $CURRENT_BRANCH

echo "🔄 Syncing $CURRENT_BRANCH with main..."
git merge main --ff-only || echo "⚠️ Feature branch has diverged from main — manual merge may be needed."

TARGET_SHA=$(git rev-parse main)
echo "👀 Deploy triggered. main = $TARGET_SHA"
echo "   GitHub Actions URL: https://github.com/Kjdragan/universal_agent/actions"

export NO_COLOR=1
export GH_NO_UPDATE_NOTIFIER=1

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
            echo "❌ Deployment FAILED. Check GitHub Actions logs."
            exit 1
        fi
    else
        echo "⚠️ Could not find a running GHA deployment for $TARGET_SHA. Verify manually."
    fi
else
    echo "ℹ️  gh CLI not installed or not authenticated in this environment."
end

---
description: Open a pull request to main with the current branch's changes; CI gates the merge that triggers production deploy
---

# Ship Workflow

This workflow opens a pull request from your current branch into `main`. PR-Validate CI runs; once green, the operator clicks Merge in GitHub and the merge to `main` triggers `.github/workflows/deploy.yml`, which deploys to the production VPS.

> **Where can `/ship` run?** Anywhere with the right git remote and (optionally) a working `gh` CLI session: your desktop, a VPS dev-tree at `/home/ua/dev/universal_agent`, Antigravity Remote-SSH'd into the VPS, or a sandbox-Claude with the repo cloned. The workflow operates on `origin` only, so any clone tracking the same GitHub remote can ship. `gh` is **optional** — if installed and authenticated, `/ship` opens the PR for you and reports CI status; if not, it prints the PR-create URL and you click through.

> **History (2026-05-10):** This workflow used to do a fast-forward chain `feature/latest2 → develop → main` and trigger the deploy directly. The `develop` branch was retired after sustained complaints that the chain added failure modes (stale-branch divergence, silent no-op pushes, mid-chain `git fetch` flakiness) without delivering integration value. Everything now goes through one PR to `main` with PR-Validate CI as the pre-merge gate. See `docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md` for the full new model.

## Notes for the Ship Operator (Pre-push Hygiene)

If you are the agent running `/ship` (you may be reading this in a different session than the AI Coder who produced the commits), apply the same pre-push hygiene before opening the PR:

- Never assume the local working copy is current. Always re-fetch first. This eliminates the failure mode where the operator's stale checkout collides with a remote commit a coder just pushed.
- If `git push` fails with a non-fast-forward / 403 rejection, the recovery is `git pull --rebase origin <current_branch>` and re-run the failed step. **Never `git push --force`** unless explicitly authorized by Kevin.
- The PR Validate workflow (`.github/workflows/pr-validate.yml`) runs on every PR to `main`: `py_compile` on every changed `.py`, `ruff check`, and `pytest tests/unit -x -q`. **All three must pass before the merge button works.** That's the new pre-deploy gate.

// turbo-all

## Execute Pull Request Open

This script stages your work, runs pre-flight checks, pushes the branch, opens a PR to `main`, and (if `gh` is available) watches CI and reports status.

```bash
set -e

# 1. Save state and validate branch
CURRENT_BRANCH=$(git branch --show-current)
echo "🚀 Shipping from branch: $CURRENT_BRANCH"

# Guard: never ship from main itself (the PR head must be a feature branch)
if [ "$CURRENT_BRANCH" = "main" ]; then
    echo "❌ ERROR: /ship must be run from a feature branch, not 'main'."
    echo "   Switch to (or create) a feature branch first:"
    echo "      git checkout -b kevin/<task>   (or feature/<task>, claude/<task>)"
    exit 1
fi

# 1a. Refuse to ship the legacy 'develop' branch — it was retired 2026-05-10.
if [ "$CURRENT_BRANCH" = "develop" ]; then
    echo "❌ ERROR: 'develop' was retired 2026-05-10. Open your work from a feature"
    echo "   branch and PR directly to main."
    exit 1
fi

# 1b. SHA-delta preview — show operator what's about to ship before any pushes.
echo "🔭 Pre-flight ref state (origin):"
git fetch origin "$CURRENT_BRANCH" main --quiet 2>/dev/null || true
for ref in "$CURRENT_BRANCH" main; do
    sha=$(git rev-parse "origin/$ref" 2>/dev/null || echo "<none>")
    echo "   origin/$ref = ${sha:0:8}"
done

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

# 3. Commit and sync current branch — only if there ARE pending changes.
if [ -n "$(git status --porcelain)" ]; then
    git add .
    git commit -m "chore: deployment auto-commit via /ship"
else
    echo "✅ No pending changes to commit."
fi

echo "🔄 Fetching remote state to check for parallel activity..."
git fetch origin "$CURRENT_BRANCH" main

LOCAL_SHA=$(git rev-parse @)
REMOTE_SHA=$(git rev-parse @{u} 2>/dev/null || echo "no_remote")

if [ "$LOCAL_SHA" != "$REMOTE_SHA" ] && [ "$REMOTE_SHA" != "no_remote" ]; then
    echo "⚠️ Remote changes detected (potential Claude Code activity). Performing pre-flight rebase..."
    git pull --rebase origin "$CURRENT_BRANCH"
else
    echo "✅ Local branch is up to date. Proceeding with push..."
fi

git push -u origin "$CURRENT_BRANCH"

# 4. Verify origin/<branch> actually advanced (catches silent no-op pushes).
git fetch origin "$CURRENT_BRANCH" --quiet
POST_SHA=$(git rev-parse "origin/$CURRENT_BRANCH")
if [ "$POST_SHA" != "$(git rev-parse HEAD)" ]; then
    echo "❌ FATAL: push to $CURRENT_BRANCH reported success but origin/$CURRENT_BRANCH"
    echo "   is at ${POST_SHA:0:8}, expected $(git rev-parse HEAD | cut -c1-8)."
    echo "   The PR will not contain your latest commits."
    exit 1
fi

# 5. Open or update the PR to main.
echo "📝 Opening pull request to main..."

# Prevent interactive prompts or colors from mangling output inside the script
export NO_COLOR=1
export GH_NO_UPDATE_NOTIFIER=1

PR_URL=""

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    # Check if a PR already exists for this branch.
    EXISTING_PR=$(gh pr list --head "$CURRENT_BRANCH" --base main --json url -q '.[0].url' 2>/dev/null || echo "")

    if [ -n "$EXISTING_PR" ]; then
        echo "ℹ️  PR already exists for $CURRENT_BRANCH → main: $EXISTING_PR"
        echo "   New commits have been pushed; CI will re-run."
        PR_URL="$EXISTING_PR"
    else
        # Auto-generate title from the latest commit subject. Body lists
        # all commits between origin/main and HEAD.
        PR_TITLE=$(git log -1 --pretty=%s)
        PR_BODY_TMP=$(mktemp)
        {
          echo "## Summary"
          echo ""
          echo "Commits in this PR (vs origin/main):"
          echo ""
          git log --pretty="- %h %s" "origin/main..HEAD"
          echo ""
          echo "## Test plan"
          echo ""
          echo "- [x] Pre-flight syntax/lint checks (run by /ship)"
          echo "- [ ] PR-Validate CI passes (py_compile + ruff + pytest tests/unit)"
          echo "- [ ] GitGuardian secret scan passes"
          echo "- [ ] Operator review (or self-merge for trusted automation branches)"
          echo ""
          echo "Opened by /ship from \`$CURRENT_BRANCH\`."
        } > "$PR_BODY_TMP"

        PR_URL=$(gh pr create --base main --head "$CURRENT_BRANCH" \
            --title "$PR_TITLE" \
            --body-file "$PR_BODY_TMP" 2>&1 | tail -1)

        rm -f "$PR_BODY_TMP"

        if [ -z "$PR_URL" ] || ! echo "$PR_URL" | grep -q "^https://"; then
            echo "❌ gh pr create did not return a URL. Check 'gh auth status' and try again."
            echo "   Fallback: open the PR manually at:"
            echo "   https://github.com/Kjdragan/universal_agent/compare/main...$CURRENT_BRANCH?expand=1"
            exit 1
        fi
        echo "✅ PR created: $PR_URL"
    fi

    # 6. Watch CI status (best-effort — PR is open regardless of watch outcome).
    echo "🛰️  Watching PR CI..."
    if gh pr checks "$PR_URL" --watch --interval 10 --fail-fast >/dev/null 2>&1; then
        echo "🎯 CI green — click Merge in GitHub to deploy:"
        echo "   $PR_URL"
        echo ""
        echo "   On merge, .github/workflows/deploy.yml fires for the new main HEAD."
    else
        echo "⚠️  CI red or in-progress. Inspect on GitHub:"
        echo "   $PR_URL"
        echo "   Run 'gh pr checks $PR_URL' for the status table."
        echo "   /ship completed its job (PR is open); CI failure is yours to fix."
        exit 1
    fi
else
    # gh CLI not available — print the PR-create URL and exit cleanly.
    echo "ℹ️  gh CLI not installed or not authenticated in this environment."
    echo ""
    echo "📝 Open the PR manually at:"
    echo "   https://github.com/Kjdragan/universal_agent/compare/main...$CURRENT_BRANCH?expand=1"
    echo ""
    echo "   Once the PR is open, PR-Validate CI runs automatically."
    echo "   When CI is green, click Merge — that fires the production deploy."
    echo ""
    echo "   To enable in-script PR creation + CI watch on this machine:"
    echo "   install gh and run 'gh auth login'."
fi

echo ""
echo "✅ /ship complete. Branch is pushed; PR will land in main when CI passes and merge is clicked."
```

## Anti-patterns (DO NOT do these)

- **Direct push to `main`.** The new flow is PR-only. If branch protection is configured (recommended), the push will be rejected; if not, it bypasses CI and risks shipping a SyntaxError to production.
- **Using `develop`.** That branch was retired 2026-05-10. If you see references to it in older docs, those docs need updating — this workflow's `--base` is always `main`.
- **`git push --force` to `feature/latest2` or `main`.** Permanent destructive operation. Don't.
- **Skipping CI by self-merging without checks.** PR-Validate is the only pre-merge gate now that `develop` is gone. Don't merge red.

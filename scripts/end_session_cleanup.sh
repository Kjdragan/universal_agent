#!/usr/bin/env bash
# Session-end cleanup for Claude Code agent sessions.
#
# Runs from the project root via a `Stop` hook in .claude/settings.json,
# or manually via `just cleanup`.
#
# What it does:
#   1. Detect stale worktrees under `.claude/worktrees/` whose branch is
#      squash-merged into origin/main, and remove them. The squashed
#      content lives on main; leaving the worktree behind strands the
#      IDE on a dead branch.
#   2. If the parent checkout is on a branch other than main AND that
#      branch is squash-merged into origin/main, switch the parent to
#      main and fast-forward pull.
#
# Safety rails:
#   - NEVER removes the worktree the script is currently executing IN
#     (would yank the cwd out from under itself mid-run; learned this
#     the hard way in PR #321's first dry-run).
#   - NEVER touches a branch with unmerged commits.
#   - NEVER force-pushes, never deletes remote refs, never discards
#     uncommitted files.
#   - NEVER removes a worktree with zero commits beyond main — that's
#     a brand-new worktree with work still in flight.
#
# Designed to be idempotent and safe to invoke any number of times.
set -euo pipefail

log() { echo "CLEANUP: $*"; }

# Resolve the parent checkout. `git worktree list --porcelain` lists the
# main worktree first.
START_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
if ! git -C "$START_DIR" rev-parse --git-dir >/dev/null 2>&1; then
  log "not in a git repo (start_dir=$START_DIR) — nothing to do"
  exit 0
fi
REPO_ROOT="$(git -C "$START_DIR" worktree list --porcelain | awk '/^worktree / {print $2; exit}')"
if [ -z "$REPO_ROOT" ] || [ ! -d "$REPO_ROOT" ]; then
  log "could not resolve main worktree from $START_DIR — bailing"
  exit 0
fi

# Identify the worktree the script itself is running in, so we can skip it.
SELF_WORKTREE="$(git -C "$START_DIR" rev-parse --show-toplevel 2>/dev/null || echo "")"

cd "$REPO_ROOT"

# Refresh origin/main BEFORE deciding what's merged.
git fetch origin main --quiet 2>/dev/null || log "git fetch failed (continuing offline)"

# ---------------------------------------------------------------------------
# Step 1 — prune worktrees whose branches are squash-merged into origin/main
# ---------------------------------------------------------------------------
prune_worktree() {
  local worktree_path="$1"
  local branch
  branch=$(git -C "$worktree_path" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
  if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
    log "worktree $worktree_path on detached HEAD — skipping"
    return
  fi

  # Refuse to touch the worktree the script itself is executing in.
  if [ "$worktree_path" = "$SELF_WORKTREE" ]; then
    log "worktree $worktree_path is the active session — skipping"
    return
  fi

  # Don't remove a worktree that has uncommitted work — operator might
  # be in the middle of something the agent doesn't know about.
  if [ -n "$(git -C "$worktree_path" status --porcelain 2>/dev/null)" ]; then
    log "worktree $worktree_path has uncommitted changes — keeping"
    return
  fi

  # How many commits does this branch have that aren't on origin/main?
  # `git rev-list --count` covers both fresh branches (0 commits) and
  # branches with unsquashed commits. For squash-merged branches, the
  # commit count will be > 0 even after merge, BUT `git cherry`'s
  # patch-id matching reports 0 "+" lines because the squashed
  # equivalent IS on main. So we use BOTH: count for "is it new" and
  # cherry for "is it merged".
  local commit_count
  commit_count=$(git -C "$worktree_path" rev-list --count HEAD ^origin/main 2>/dev/null || echo 0)
  if [ "$commit_count" -eq 0 ]; then
    # Fresh worktree, no commits yet. Keep — operator may be about to
    # start work here.
    log "worktree $worktree_path branch=$branch has no commits beyond main — keeping (fresh)"
    return
  fi

  local unmerged
  unmerged=$(git -C "$worktree_path" cherry origin/main HEAD 2>/dev/null | grep -c '^+' || true)
  if [ "$unmerged" -gt 0 ]; then
    log "worktree $worktree_path branch=$branch has $unmerged unmerged commits — keeping"
    return
  fi

  log "worktree $worktree_path branch=$branch fully merged ($commit_count commits squashed) — removing"
  git worktree remove --force "$worktree_path" 2>&1 | sed 's/^/  /' || true
}

if [ -d "$REPO_ROOT/.claude/worktrees" ]; then
  for wt in "$REPO_ROOT"/.claude/worktrees/*/; do
    [ -d "$wt" ] || continue
    prune_worktree "${wt%/}"
  done
fi
git worktree prune

# ---------------------------------------------------------------------------
# Step 2 — get the parent checkout back on main if its branch is merged
# ---------------------------------------------------------------------------
current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")

if [ "$current_branch" = "main" ]; then
  log "parent checkout already on main — pulling"
  git pull --ff-only origin main 2>&1 | sed 's/^/  /' || true
elif [ -z "$current_branch" ] || [ "$current_branch" = "HEAD" ]; then
  log "parent checkout on detached HEAD — leaving alone"
else
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    log "parent checkout has uncommitted changes — staying on $current_branch"
  else
    unmerged=$(git cherry origin/main HEAD 2>/dev/null | grep -c '^+' || true)
    if [ "$unmerged" -gt 0 ]; then
      log "branch=$current_branch has $unmerged unmerged commits — keeping IDE checkout here"
    else
      log "branch=$current_branch fully merged into origin/main — switching IDE checkout to main"
      git checkout main 2>&1 | sed 's/^/  /'
      git pull --ff-only origin main 2>&1 | sed 's/^/  /'
    fi
  fi
fi

log "done"

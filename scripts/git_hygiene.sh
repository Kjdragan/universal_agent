#!/usr/bin/env bash
# Keep this desktop checkout's git state CURRENT, automatically.
#
# WHY THIS EXISTS
#   Every other piece of tooling here treated "local main drifts behind origin"
#   as a permanent fact of life and defended against it:
#     - CLAUDE.md told agents to always branch from origin/main instead,
#     - .claude/hooks/guard-fresh-branch.sh DENIES branch creation off a stale base.
#   Nothing ever removed the drift. So the checkout accumulated 339 commits of
#   lag, 35 worktrees, and 413 local branches — and agents kept reading stale
#   files and reporting stale conclusions from them. (On 2026-08-02 a session
#   grepped a 339-commit-old copy of scripts/install_vps_swap.sh, concluded a
#   long-fixed bug was still live, and started building a duplicate fix.)
#   Guarding against a dirty workspace is not the same as cleaning it.
#
#   This script is the missing half: it makes the checkout current instead of
#   making agents route around it.
#
# SAFETY CONTRACT (this runs unattended at session start — it must never
# destroy work):
#   - NEVER switches branches, and never touches the working tree.
#   - NEVER fast-forwards `main` while main is the checked-out branch and dirty.
#   - NEVER deletes a branch that has ANY commit not already in origin/main
#     (patch-id comparison via `git cherry`, so squash-merges are recognised).
#   - NEVER deletes a branch checked out in a worktree, or the current branch.
#   - NEVER removes a worktree that has uncommitted changes.
#   - Soft-fails everywhere: a hygiene failure must not block a session.
#
# Run manually any time:  bash scripts/git_hygiene.sh
# Report only, no writes: bash scripts/git_hygiene.sh --dry-run
set -uo pipefail

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

run() { if [ "$DRY_RUN" -eq 1 ]; then echo "    [dry-run] $*"; else "$@" >/dev/null 2>&1; fi; }

DEFAULT_BRANCH="${UA_DEFAULT_BRANCH:-main}"
CUR="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)"

# 1. Refresh remote refs and drop remote-tracking refs for deleted branches.
git fetch --prune --quiet origin 2>/dev/null || { echo "[git-hygiene] fetch failed; skipping (offline?)"; exit 0; }

git show-ref --verify --quiet "refs/remotes/origin/$DEFAULT_BRANCH" || exit 0
ORIGIN_SHA="$(git rev-parse "refs/remotes/origin/$DEFAULT_BRANCH")"

# 2. Fast-forward the local default branch.
#    When it is NOT checked out we can update the ref directly — this is the
#    common case here, since work happens on task branches.
before_sha="$(git rev-parse --verify --quiet "refs/heads/$DEFAULT_BRANCH" || echo none)"
if [ "$before_sha" != "$ORIGIN_SHA" ]; then
  if [ "$CUR" != "$DEFAULT_BRANCH" ]; then
    # Refuse if the branch is checked out in ANOTHER worktree.
    if git worktree list --porcelain | grep -qx "branch refs/heads/$DEFAULT_BRANCH"; then
      echo "[git-hygiene] $DEFAULT_BRANCH is checked out in another worktree; leaving it alone"
    else
      run git fetch --quiet origin "$DEFAULT_BRANCH:$DEFAULT_BRANCH"
    fi
  elif [ -z "$(git status --porcelain 2>/dev/null)" ]; then
    run git merge --ff-only --quiet "origin/$DEFAULT_BRANCH"
  else
    echo "[git-hygiene] on $DEFAULT_BRANCH with local changes; not fast-forwarding"
  fi
fi

# 3. Drop worktree admin entries whose directories are gone.
run git worktree prune

# 4. Delete local branches that contribute nothing beyond origin/<default>.
#    `git cherry` compares by PATCH ID, so a squash-merged branch is correctly
#    recognised as landed even though its commit SHAs never appear upstream.
#    A branch with even one unique commit is left completely alone.
git worktree list --porcelain 2>/dev/null \
  | awk '/^branch /{sub("refs/heads/","",$2); print $2}' | sort -u > /tmp/.ua_inuse.$$ || : > /tmp/.ua_inuse.$$

deleted=0
while read -r br; do
  [ -z "$br" ] && continue
  [ "$br" = "$DEFAULT_BRANCH" ] && continue
  [ "$br" = "$CUR" ] && continue
  grep -qxF "$br" /tmp/.ua_inuse.$$ && continue
  # Any '+' line is a commit whose patch is NOT upstream => real unmerged work.
  if [ "$(git cherry "origin/$DEFAULT_BRANCH" "$br" 2>/dev/null | grep -c '^+')" -eq 0 ] 2>/dev/null; then
    run git branch -D "$br"
    deleted=$((deleted + 1))
  fi
done < <(git for-each-ref --format='%(refname:short)' refs/heads/ 2>/dev/null)
rm -f /tmp/.ua_inuse.$$

after_sha="$(git rev-parse --verify --quiet "refs/heads/$DEFAULT_BRANCH" || echo none)"
synced="behind"; [ "$after_sha" = "$ORIGIN_SHA" ] && synced="in sync"
echo "[git-hygiene] $DEFAULT_BRANCH $synced with origin | branches: $(git for-each-ref refs/heads/ | wc -l) | worktrees: $(git worktree list | wc -l) | pruned $deleted merged branch(es)"
exit 0

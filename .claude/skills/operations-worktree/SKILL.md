---
name: operations-worktree
description: "Create and manage git worktrees safely by branching off a freshly-fetched origin/main instead of a stale local main. Use whenever the user wants to create a worktree, add a worktree, start a task branch, spin up a new branch for parallel work, or work on several issues at once — even if they don't mention worktrees by name. Use when the user says \"new branch\", \"new worktree\", \"start a task branch\", \"branch off origin/main\", \"avoid a stale base\", \"parallel issue fixing\", or asks how to keep main intact while working. Also use to clean up worktrees (remove/prune) or when a branch creation got denied for a stale base."
user-invocable: true
risk: safe
source: "Adapted from vincentkoc/dotskills (MIT) — operations-worktree"
---

# Operations Worktree

Create worktrees and task branches that are anchored to a **freshly-fetched `origin/main`**, never a stale local `main`. This is the single rule that prevents a merged PR from silently reverting commits that landed on origin while your branch was alive.

## The canonical command

Always fetch first, then base the new worktree explicitly on `origin/main`:

```bash
git fetch origin && git worktree add -b claude/<task> /home/kjdragan/lrepos/universal_agent-wt-<task> origin/main
```

- `git fetch origin` updates the local `origin/main` ref so the base is current.
- `git worktree add -b <branch> <path> origin/main` creates the branch off that fresh ref and checks it out in a separate directory, leaving your main checkout untouched.
- Path convention: `/home/kjdragan/lrepos/universal_agent-wt-<task>` (sibling of the main checkout, suffixed with the task slug).

To check out an **existing** remote branch (e.g. a PR under review) rather than create a new one, fetch first, then add the worktree on the fetched ref — do not create a new branch off a moving base:

```bash
git fetch origin && git worktree add /tmp/pr-<n>-review origin/<pr-branch>
```

## UA branch naming and auto-merge

The branch prefix decides whether the PR auto-merges. `pr-auto-merge.yml` auto-enables auto-merge for all non-draft PRs **EXCEPT** `codie/*`, `kevin/*`, and `feature/*` (those need manual review).

- Claude Code work → `claude/<task>` (auto-merges)
- Codie work → `codie/<task>` (manual review)
- Operator work → `kevin/<task>` or `feature/<task>` (manual review)

The `EnterWorktree` helper names branches `worktree-claude+<task>` rather than `claude/<task>`; that is functionally equivalent under the allowlist (it doesn't match the three manual-review globs, so it auto-merges). `EnterWorktree`'s default `fresh` base is `origin/main` — but still `git fetch` first so that ref is current.

## Cleanup

When the task is done and the branch is merged:

```bash
git worktree remove /home/kjdragan/lrepos/universal_agent-wt-<task>
git worktree prune   # drop stale administrative entries for deleted directories
```

Use `git worktree list` to see active worktrees before removing.

## Why this rule exists

This desktop checkout's local `main` drifts behind origin as PRs merge. Branching with a bare base off local `HEAD` therefore bases new work on a **stale** main — which, when the PR is built and squash-merged, can revert everything that landed on `origin/main` in the meantime. This is not hypothetical: a near-miss almost reverted **96 commits**. The durable invariant is: fetch, then base new branches on `origin/main`.

A local PreToolUse hook, `.claude/hooks/guard-fresh-branch.sh`, enforces this mechanically on this desktop: it denies a `git checkout -b` / `git switch -c` / `git worktree add` whose base is behind `origin/main` and prints the corrected command. The hook is a backstop, not a substitute — write the correct command yourself.

## When to use

- Creating any new task branch or worktree for Claude Code work.
- Fixing multiple issues in parallel, each in its own worktree.
- Reviewing a PR without polluting the main checkout (check out the existing PR branch in a throwaway worktree).
- A branch-creation command was denied with a "stale base" message.

## When NOT to use

- You are committing/pushing on a branch that already exists and is correctly based — no new worktree is needed.
- You explicitly need a different start point (a tag, a specific commit, another branch). Pass that ref explicitly; the freshness rule only governs the *default* of `main`.
- VPS/fleet deployments — the guard hook and this convention are desktop-only (wired from the gitignored `.claude/settings.local.json`).

## NEVER

- NEVER `git worktree add -b <branch> <path> main` or any bare `git checkout -b <name>` off local `main`. Local `main` is stale; this is the exact footgun that nearly reverted 96 commits.
- NEVER skip `git fetch` before creating a worktree off `origin/main`. Without the fetch, `origin/main` is itself a stale local mirror and the "fresh" base is a lie.
- NEVER push directly to `main`. Branch, push the branch, open a PR.
- NEVER review or build a PR inside the main checkout directory — use a separate worktree so a failed run can't corrupt your working tree.

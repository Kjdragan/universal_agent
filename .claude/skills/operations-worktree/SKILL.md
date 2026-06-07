---
name: operations-worktree
description: "Create and manage git worktrees safely by branching off a freshly-fetched origin/main instead of a stale local main. Use whenever the user wants to create a worktree, add a worktree, start a task branch, spin up a new branch for parallel work, or work on several issues at once — even if they don't mention worktrees by name. Use when the user says \"new branch\", \"new worktree\", \"start a task branch\", \"branch off origin/main\", \"avoid a stale base\", \"parallel issue fixing\", or asks how to keep main intact while working. Also use to clean up worktrees (remove/prune) or when a branch creation got denied for a stale base. Use to recover from worktree-session lifecycle errors: when EnterWorktree fails with \"Already in a worktree session\", and you must decide whether to ExitWorktree or pass a path to switch into another worktree (a nested worktree). Use when warned that work \"has not isolated changes\" / \"changes are not isolated\" — i.e. editing the deployed tree, working on main or the deployed checkout — and you need to move/carry your changes into a worktree or isolate my work. Use to recover a deleted or stale worktree: a pasted \"'<path>' is not a working tree\" error, \"git worktree remove failed\", a stale worktree entry to prune, or \"worktree already exists\". Use for parallel fix dispatch / fan out fixes across worktrees: implementing a fix in a worktree, one branch one PR per worktree, where each lane owns disjoint files. Use the WT= variable pattern — assign the worktree path to a shell variable so the long path isn't retyped and reference the worktree path via $WT — when setting the worktree path to a variable and running edits there."
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

## Error recovery

### Already in a worktree

If `EnterWorktree` fails with:

```
Already in a worktree session. Pass path to switch into another existing worktree, or use ExitWorktree to leave this one before creating a new worktree.
```

— that error means you **are already inside an isolated worktree**. Do **not** nest a worktree inside a worktree. Choose one:

- Proceed in place — you are already isolated; just keep working here.
- `EnterWorktree` with `path` set to an existing sibling worktree to switch into it.
- `ExitWorktree` to leave this one first, then create the new worktree.

### Changes are not isolated

When you are warned that your work is on the deployed / main tree — the recurring rule is "patches must run inside an isolated worktree, never on the deployed tree" — stop editing the live checkout immediately. Carry the in-progress diff into a fresh worktree, then resume there:

```bash
git fetch origin && git worktree add -b claude/<task> /home/kjdragan/lrepos/universal_agent-wt-<task> origin/main
```

Move your changes in (e.g. `git stash` then `git stash pop` in the new worktree, or re-apply the diff) and continue there. Never keep editing the deployed tree once warned.

### Recover a deleted worktree directory

If the worktree directory was removed out from under git, `git worktree remove <path>` fails with:

```
'<path>' is not a working tree
```

(remove failed). Fix it by pruning the stale administrative entry, then re-adding only if you still need the worktree:

```bash
git worktree prune
git fetch origin && git worktree add -b claude/<task> /home/kjdragan/lrepos/universal_agent-wt-<task> origin/main
```

If the re-add reports the branch or path `already exists`, prune again, or remove the stale branch first (`git branch -D claude/<task>`), then re-add.

## Parallel fix dispatch

### The WT= variable pattern

When a single lane does many `cd`/edit/commit steps in one worktree, assign its path to a shell variable **once**, then reference `$WT` everywhere so the long path is never retyped and lanes can't cross-contaminate:

```bash
WT=/home/kjdragan/lrepos/universal_agent-wt-<task>
# or, relative to the main checkout:
MAIN=/home/kjdragan/lrepos/universal_agent
WT=$MAIN/.claude/worktrees/<slug>

cd "$WT" && git status
$EDITOR "$WT/path/to/file"
git -C "$WT" add -A && git -C "$WT" commit -m "fix: ..."
```

### HARD-RULES for parallel fix dispatch

When fanning out fixes across several worktrees concurrently:

- One branch and one PR per worktree.
- Each lane owns a **disjoint** set of files.
- Never run two lanes that edit the same file concurrently — that is a merge / disjoint-history hazard.
- Fan out only over independent fixes.
- Each lane still uses the canonical fetch-then-`origin/main` creation command — no shortcuts.

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

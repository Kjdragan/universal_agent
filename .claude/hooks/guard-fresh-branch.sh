#!/usr/bin/env bash
# Universal Agent — "fresh branch base" guard (LOCAL / desktop-only).
#
# Why: this desktop checkout's local `main` drifts behind origin as PRs merge.
# Branching with a bare `git checkout -b <name>` off local HEAD therefore bases
# new work on a STALE main — which, when the PR is built and merged, can revert
# everything that landed on origin/main in the meantime (a near-miss that almost
# reverted 96 commits). The durable invariant is: fetch, then base new branches on
# origin/main. This hook enforces it mechanically.
#
# Behavior: a PreToolUse hook on Bash. When it sees a branch-creating git command
# (`git checkout -b` / `git switch -c` / `git worktree add`) whose start-point is
# NOT an origin ref, it fetches origin and checks whether the base is behind
# origin/main. If behind, it DENIES the command and prints the correct one.
# Branching off a current base, or explicitly off origin/main, passes silently.
#
# For `git worktree add`, the base is the trailing <commit-ish> argument when one
# is given; when omitted, git would branch off the current HEAD, which the same
# behind-origin/main check covers.
#
# Scope: wired only from the gitignored .claude/settings.local.json — desktop-only,
# never deployed to the VPS fleet.

set -uo pipefail

allow() { exit 0; }  # emit nothing + exit 0 => tool call proceeds normally
deny()  { printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$1"; exit 0; }

PAYLOAD="$(cat)"
CMD="$(printf '%s' "$PAYLOAD" | python3 -c 'import sys,json;print((json.load(sys.stdin).get("tool_input") or {}).get("command",""))' 2>/dev/null)"
[ -z "$CMD" ] && allow

# Only act on branch-creating commands.
echo "$CMD" | grep -Eq 'git[[:space:]]+(checkout[[:space:]]+-b|switch[[:space:]]+-c|worktree[[:space:]]+add)\b' || allow

# Decide if the command already bases off an origin/* ref AS THE START-POINT — the
# only safe-by-construction case. Two traps the naive `origin/ appears anywhere`
# check fell into, both real false-negatives:
#   1. Chained commands (&&, ||, ;, |, newline) can park an `origin/` token in an
#      UNRELATED segment, e.g. `... add -b fix /p main && echo origin/main`. So we
#      isolate ONLY the segment that carries the branch-creating verb and judge that.
#   2. An `origin/` substring can hide in the `-b <branch>` name or inside a path
#      (`-b origin/foo`, `/tmp/origin/x`). So within that segment we drop the
#      branch-name operand, then require `origin/` as its own whitespace-delimited
#      start-point token — not a substring of a path.
# Split on the shell separators, keep the segment with the creation verb, and test
# that segment. If it has a real origin/ start-point, allow; otherwise fall through
# to the live behind-origin/main check, which evaluates the actual base.
SEG="$(printf '%s' "$CMD" | sed -E 's/\|\|/\n/g; s/&&/\n/g; s/[;|]/\n/g' \
        | grep -E 'git[[:space:]]+(checkout[[:space:]]+-b|switch[[:space:]]+-c|worktree[[:space:]]+add)\b' \
        | head -1)"
STRIPPED="$(printf '%s' "$SEG" | sed -E 's/(-b|-c)[[:space:]]+[^[:space:]]+//g')"
printf '%s' "$STRIPPED" | grep -Eq '(^|[[:space:]])origin/[A-Za-z0-9._/-]+([[:space:]]|$)' && allow

# Must be inside a git work tree with an origin remote.
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || allow
git remote get-url origin >/dev/null 2>&1 || allow

# Determine the default branch (origin/HEAD -> origin/main fallback).
DEF="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null)"
[ -z "$DEF" ] && DEF="origin/main"

# Refresh the ref so "behind" reflects reality, not a stale local mirror.
timeout 15 git fetch origin --quiet >/dev/null 2>&1 || true
git rev-parse --verify --quiet "$DEF" >/dev/null 2>&1 || allow

BEHIND="$(git rev-list --count "HEAD..$DEF" 2>/dev/null || echo 0)"
[ "${BEHIND:-0}" -eq 0 ] 2>/dev/null && allow

# Stale base — block with the corrected command. Extraction differs by command
# shape: `git worktree add [-b <branch>] <path> [<commit-ish>]` puts the branch
# name and path in different positions than `checkout -b`/`switch -c`.
if echo "$CMD" | grep -Eq 'git[[:space:]]+worktree[[:space:]]+add\b'; then
  # Branch name follows -b when present; else git derives it from the path.
  NAME="$(echo "$CMD" | sed -E 's/.*worktree[[:space:]]+add[[:space:]]+.*-b[[:space:]]+([^[:space:]]+).*/\1/;t;s/.*//')"
  # Path = first non-flag argument after `add` that isn't the -b value.
  PTH="$(echo "$CMD" | sed -E 's/.*worktree[[:space:]]+add[[:space:]]+//; s/-b[[:space:]]+[^[:space:]]+[[:space:]]+//; s/^(-[^[:space:]]+[[:space:]]+)*//; s/[[:space:]].*//')"
  REASON="$(printf 'Stale base: current HEAD is %s commit(s) behind %s. Create the worktree from fresh origin instead:\n  git fetch origin && git worktree add -b %s %s %s' "$BEHIND" "$DEF" "${NAME:-<branch>}" "${PTH:-<path>}" "$DEF")"
else
  NAME="$(echo "$CMD" | sed -E 's/.*(checkout[[:space:]]+-b|switch[[:space:]]+-c)[[:space:]]+([^[:space:]]+).*/\2/')"
  REASON="$(printf 'Stale base: current HEAD is %s commit(s) behind %s. Branch from fresh origin instead:\n  git fetch origin && git checkout -b %s %s' "$BEHIND" "$DEF" "${NAME:-<name>}" "$DEF")"
fi
deny "$(printf '%s' "$REASON" | python3 -c 'import sys,json;print(json.dumps(sys.stdin.read()))')"

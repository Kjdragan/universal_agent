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
# (`git checkout -b` / `git switch -c`) whose start-point is NOT an origin ref, it
# fetches origin and checks whether the base (current HEAD) is behind origin/main.
# If behind, it DENIES the command and prints the correct one. Branching off a
# current base, or explicitly off origin/main, passes silently.
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
echo "$CMD" | grep -Eq 'git[[:space:]]+(checkout[[:space:]]+-b|switch[[:space:]]+-c)\b' || allow

# If the command already bases off an origin/* ref, it's correct by construction.
echo "$CMD" | grep -Eq '\borigin/[A-Za-z0-9._/-]+' && allow

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

# Stale base — block with the corrected command.
NAME="$(echo "$CMD" | sed -E 's/.*(checkout[[:space:]]+-b|switch[[:space:]]+-c)[[:space:]]+([^[:space:]]+).*/\2/')"
REASON="$(printf 'Stale base: current HEAD is %s commit(s) behind %s. Branch from fresh origin instead:\n  git fetch origin && git checkout -b %s %s' "$BEHIND" "$DEF" "${NAME:-<name>}" "$DEF")"
deny "$(printf '%s' "$REASON" | python3 -c 'import sys,json;print(json.dumps(sys.stdin.read()))')"

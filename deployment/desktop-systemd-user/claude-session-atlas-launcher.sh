#!/bin/bash
# claude-session-atlas launcher — installed at ~/.local/bin/claude-session-atlas.
# Always runs the latest MERGED (origin/main) version of the script via a
# `git show` snapshot, so the timer, the refresh endpoint, and interactive
# --find are immune to whatever branch the dev checkout happens to be parked
# on (git show reads the ref directly; no working tree is touched).
set -e
repo=/home/kjdragan/lrepos/universal_agent
snap="$HOME/.cache/claude-session-atlas/atlas.py"
mkdir -p "$(dirname "$snap")"
git -C "$repo" fetch -q origin main 2>/dev/null || true  # offline → last snapshot
if git -C "$repo" show origin/main:scripts/claude-session-atlas >"$snap.tmp" 2>/dev/null; then
    mv "$snap.tmp" "$snap"
fi
[ -s "$snap" ] || exec python3 "$repo/scripts/claude-session-atlas" "$@"
exec python3 "$snap" "$@"

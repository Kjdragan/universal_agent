#!/usr/bin/env bash
# Universal Agent — self-improvement Stop hook (LOCAL / desktop-only).
#
# Why this exists:
#   When a work session winds down, the conversation context is the freshest
#   it will ever be: the gotchas hit, conventions clarified, paths that were
#   wrong, and tool quirks discovered are all still in the transcript. This
#   hook captures that while it's fresh and proposes durable CLAUDE.md
#   improvements for human review — turning each session into a chance to
#   make the operating manual a little better.
#
# Scope & safety:
#   - Wired ONLY from .claude/settings.local.json (gitignored, this machine,
#     this project). It is intentionally NOT in the checked-in
#     .claude/settings.json, because that deploys to the VPS where the
#     autonomous fleet (Simone/Cody/Atlas) and Cody CLI subprocesses run —
#     a reflection that spawns `claude -p` fleet-wide would burn quota and
#     risk recursion.
#   - Fire-and-forget: we detach the reflector and return in milliseconds so
#     the user is never blocked. All slow work (a cheap model call) runs in
#     the background and writes proposals to disk.
#   - The reflector NEVER edits CLAUDE.md. It only drafts proposals under
#     .claude/self-improvement/proposals/ for you to accept or discard.
#
# Recursion guard:
#   The reflector calls `claude -p`, which is itself a Claude Code session and
#   would re-fire this very hook. We export UA_SELFIMPROVE_REFLECT=1 onto the
#   reflector (inherited by its `claude -p` child); the first check below makes
#   any such nested invocation exit immediately.

set -uo pipefail

# Recursion guard: bail instantly inside any reflector-spawned `claude -p`.
[ -n "${UA_SELFIMPROVE_REFLECT:-}" ] && exit 0

# Read the Stop-hook payload (JSON on stdin) without blocking.
PAYLOAD="$(cat)"

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REFLECTOR="$HOOK_DIR/self_improve_reflect.py"

# Nothing to do if the reflector or the CLI is missing.
command -v claude >/dev/null 2>&1 || exit 0
[ -f "$REFLECTOR" ] || exit 0

# Stash the payload where the detached reflector can read it (stdin won't
# survive detachment).
TMP="$(mktemp "${TMPDIR:-/tmp}/ua-selfimprove-XXXXXX.json")" || exit 0
printf '%s' "$PAYLOAD" > "$TMP"

# Fully detach: setsid + background + redirected std streams so the work
# survives this hook process exiting and never holds the session open.
setsid env UA_SELFIMPROVE_REFLECT=1 python3 "$REFLECTOR" "$TMP" \
  >/dev/null 2>&1 < /dev/null &

exit 0

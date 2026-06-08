#!/usr/bin/env bash
# Universal Agent — "no operational unit on the desktop" guard (LOCAL / desktop-only).
#
# Why: universal_agent RUNS on the VPS; the desktop (mint-desktop) is Kevin's
# interactive dev cockpit ONLY. Nothing operational ever runs on the desktop — no
# `systemctl --user` UA timers/services, no per-user unit installers. A prior agent
# session installed a desktop skill-gap-finder timer (and discord/tutorial/youtube
# units had drifted onto the desktop the same way), double-running work the VPS
# already owns. This hook makes that mistake mechanically impossible to repeat.
# See CLAUDE.md "Runtime vs Development Environment Contract".
#
# Behavior: a PreToolUse hook on Bash. It DENIES, with a pointer to the VPS path:
#   (A) `systemctl --user enable|start` of a ua-* / universal-agent-* .service/.timer
#   (B) invoking a desktop per-user unit installer (install_*timer*.sh /
#       install_*_user_service.sh) from the checkout.
# It deliberately does NOT touch `disable`/`stop`/`status`/`list`/`rm`/`cat`, and it
# ALLOWS the canonical VPS root installers (install_vps_*), which are the CORRECT
# place these units belong.
#
# Scope: wired only from the gitignored .claude/settings.local.json — desktop-only,
# never deployed to the VPS fleet (where installing these units is exactly right).

set -uo pipefail

allow() { exit 0; }  # emit nothing + exit 0 => tool call proceeds normally
deny()  { printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$1"; exit 0; }

PAYLOAD="$(cat)"
CMD="$(printf '%s' "$PAYLOAD" | python3 -c 'import sys,json;print((json.load(sys.stdin).get("tool_input") or {}).get("command",""))' 2>/dev/null)"
[ -z "$CMD" ] && allow

REASON='Desktop runtime guard: universal_agent RUNS on the VPS; the desktop is dev-only. Do NOT install or enable UA systemd units here. Build it as a deployment/systemd/ unit and wire it into the deploy (scripts/install_vps_*.sh -> scripts/deploy/remote_deploy.sh); merging to main deploys it to the VPS. See CLAUDE.md "Runtime vs Development Environment Contract".'
REASON_JSON="$(printf '%s' "$REASON" | python3 -c 'import sys,json;print(json.dumps(sys.stdin.read()))')"

# (A) systemctl --user enable/start of a UA unit (service OR timer). Substring
# match is intentional (catches a `sudo`/`env ...` prefix).
if printf '%s' "$CMD" | grep -Eq 'systemctl[[:space:]]+--user[[:space:]]+(enable|start)([[:space:]]+--now)?[[:space:]]+"?(ua-|universal-agent-)[A-Za-z0-9._-]*\.(service|timer)'; then
  deny "$REASON_JSON"
fi

# (B) Invoking a desktop per-user unit installer AT COMMAND POSITION (start of line
# or after a shell separator, optionally via sudo/bash/sh or a ./ path). Anchoring
# to command position is what lets `cat`/`less`/`git log <installer>` through.
# The canonical VPS installers (install_vps_*) are explicitly exempted.
if printf '%s' "$CMD" | grep -Eq '(^|&&|\|\||;|\|)[[:space:]]*((sudo|bash|sh)[[:space:]]+)*(\.?/)?[^[:space:]]*install_[A-Za-z0-9._/-]*(timer[A-Za-z0-9._-]*|_user_service)\.sh' \
   && ! printf '%s' "$CMD" | grep -Eq 'install_vps_'; then
  deny "$REASON_JSON"
fi

allow

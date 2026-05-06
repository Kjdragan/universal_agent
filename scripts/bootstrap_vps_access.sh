#!/usr/bin/env bash
# bootstrap_vps_access.sh — Phase E of the Interactive Coding Environment plan.
#
# DEFERRED-EXECUTION script. Provisions an ephemeral sandbox-Claude session with
# everything needed to drive the UA VPS as if locally attached:
#
#   1. SSH client (apt)
#   2. Tailscale in userspace mode (so the sandbox joins the tailnet without
#      kernel modules)
#   3. Infisical CLI authenticated via service-token env
#   4. Verified SSH connectivity to ua@uaonvps via `tailscale ssh`
#
# Read the canonical doc first:
#   docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md
#
# REQUIRED ENV (paste into sandbox prompt at session start; NEVER commit):
#   TS_AUTHKEY            Tailscale ephemeral auth key (one-time, rotates)
#   INFISICAL_TOKEN       Infisical service token, prod-read scope
#   INFISICAL_PROJECT_ID  9970e5b7-d48a-4ed8-a8af-43e923e67572
#
# OPTIONAL ENV:
#   VPS_HOST              Default: ua@uaonvps
#   SANDBOX_HOSTNAME      Default: sandbox-claude-<epoch>
#
# This script is intentionally idempotent — running it twice in the same
# sandbox session is safe.

set -euo pipefail

log()  { printf '\033[36m[bootstrap]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[fatal]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Sanity check: refuse to run on what looks like a real workstation.
# ---------------------------------------------------------------------------
sandbox_ok() {
  # Heuristics: hostname looks like a sandbox VM, $HOME is /root, and there
  # are no persistent user dotfiles (no .bash_history with substantive
  # content). Override with FORCE_BOOTSTRAP=1 if you really know better.
  [[ "${FORCE_BOOTSTRAP:-0}" == "1" ]] && return 0
  local h="${HOSTNAME:-$(hostname)}"
  if [[ "$HOME" != "/root" || ! "$h" =~ ^(vm|sandbox|claude-) ]]; then
    return 1
  fi
  return 0
}

if ! sandbox_ok; then
  die "This script refuses to run outside a sandbox. Set FORCE_BOOTSTRAP=1 to override."
fi

# ---------------------------------------------------------------------------
# 2. Required env validation.
# ---------------------------------------------------------------------------
: "${TS_AUTHKEY:?TS_AUTHKEY env var required (Tailscale ephemeral auth key)}"
: "${INFISICAL_TOKEN:?INFISICAL_TOKEN env var required (service token, prod-read)}"
: "${INFISICAL_PROJECT_ID:?INFISICAL_PROJECT_ID env var required}"

VPS_HOST="${VPS_HOST:-ua@uaonvps}"
SANDBOX_HOSTNAME="${SANDBOX_HOSTNAME:-sandbox-claude-$(date +%s)}"

log "VPS_HOST=$VPS_HOST"
log "SANDBOX_HOSTNAME=$SANDBOX_HOSTNAME"

# ---------------------------------------------------------------------------
# 3. Install prerequisites.
# ---------------------------------------------------------------------------
log "installing apt prerequisites"
apt-get update -qq
apt-get install -y -qq openssh-client curl jq ca-certificates >/dev/null

# ---------------------------------------------------------------------------
# 4. Install + start Tailscale in userspace mode.
# ---------------------------------------------------------------------------
if ! command -v tailscale >/dev/null 2>&1; then
  log "installing Tailscale"
  curl -fsSL https://tailscale.com/install.sh | sh
fi

if ! pgrep -x tailscaled >/dev/null 2>&1; then
  log "starting tailscaled (userspace networking, in-memory state)"
  tailscaled \
    --tun=userspace-networking \
    --socks5-server=localhost:1055 \
    --state=mem: \
    >/tmp/tailscaled.log 2>&1 &
  # Give it a moment to come up.
  for _ in 1 2 3 4 5; do
    if tailscale status --json >/dev/null 2>&1 || [ -S /var/run/tailscale/tailscaled.sock ]; then
      break
    fi
    sleep 1
  done
fi

log "tailscale up (ephemeral, with SSH)"
tailscale up \
  --authkey="$TS_AUTHKEY" \
  --hostname="$SANDBOX_HOSTNAME" \
  --ephemeral \
  --ssh \
  --accept-routes=false \
  --accept-dns=false

log "tailscale status:"
tailscale status | head -10 >&2 || true

# ---------------------------------------------------------------------------
# 5. Install Infisical CLI.
# ---------------------------------------------------------------------------
if ! command -v infisical >/dev/null 2>&1; then
  log "installing Infisical CLI"
  curl -1sLf 'https://artifacts-cli.infisical.com/setup.deb.sh' | bash
  apt-get install -y -qq infisical >/dev/null
fi

# Service-token auth uses the env var; no `infisical login` required.
export INFISICAL_TOKEN
log "verifying Infisical reachability (read-only)"
infisical secrets --env=production --projectId="$INFISICAL_PROJECT_ID" --plain --silent \
  | head -1 >/dev/null \
  || die "Infisical reachability check failed — bad token or project id?"

# ---------------------------------------------------------------------------
# 6. Verify SSH to VPS via Tailscale SSH (preferred, no key needed).
# ---------------------------------------------------------------------------
log "testing connectivity: tailscale ssh $VPS_HOST 'whoami'"
if tailscale ssh -o StrictHostKeyChecking=accept-new "$VPS_HOST" 'whoami' 2>/dev/null; then
  log "SSH ok via tailscale ssh"
else
  warn "tailscale ssh failed — likely Tailscale ACL needs adjustment for this sandbox node."
  warn "See docs/03_Operations/87_Tailscale_Architecture_And_Operations_Source_Of_Truth_2026-03-06.md"
  warn "ACL/SSH troubleshooting runbook for diagnosis steps."
  exit 2
fi

# ---------------------------------------------------------------------------
# 7. Print success banner with VPS service summary.
# ---------------------------------------------------------------------------
log "fetching VPS service summary"
tailscale ssh "$VPS_HOST" '
  echo "--- VPS heartbeat ---"
  echo "host: $(hostname)"
  echo "uptime: $(uptime)"
  echo "--- UA services ---"
  systemctl list-units --type=service --state=running 2>/dev/null \
    | grep -E "universal-agent-|csi-|ua-" || echo "(none active)"
' || warn "service summary fetch failed (non-fatal)"

log "bootstrap complete."
log "  - Tailscale: up as $SANDBOX_HOSTNAME"
log "  - Infisical: production env reachable"
log "  - VPS SSH:   tailscale ssh $VPS_HOST"
log ""
log "Suggested next commands:"
log "  tailscale ssh $VPS_HOST 'cd /opt/universal_agent && git status'"
log "  tailscale ssh $VPS_HOST 'sudo systemctl status universal-agent-gateway --no-pager'"
log "  tailscale ssh $VPS_HOST 'cat /home/ua/.claude/settings.json'"

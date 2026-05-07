#!/usr/bin/env bash
# provision_vps_dev_tree.sh — Phase D.1 of the Interactive Coding Environment plan.
#
# Provisions /home/ua/dev/universal_agent as Kevin's interactive dev-tree on the
# VPS (separate from /opt/universal_agent which is the prod checkout clobbered
# on every CI/CD deploy). Idempotent: safe to re-run.
#
# Usage (run as root via SSH from desktop):
#   ssh root@uaonvps 'bash -s' < scripts/provision_vps_dev_tree.sh
#
# Or once the script is deployed in /opt/universal_agent/scripts/:
#   ssh root@uaonvps 'bash /opt/universal_agent/scripts/provision_vps_dev_tree.sh'
#
# What it does:
#   1. Pre-flight checks (running on VPS, ua user exists, dependencies present).
#   2. Creates /home/ua/dev/ if missing (ua-owned, mode 755).
#   3. If /home/ua/dev/universal_agent doesn't exist:
#        - Clones https://github.com/Kjdragan/universal_agent.git
#        - Checks out feature/latest2
#      Else: git fetch origin (no destructive ops on existing tree).
#   4. Copies INFISICAL_CLIENT_ID / SECRET / PROJECT_ID from /opt/universal_agent/.env
#      to /home/ua/dev/universal_agent/.env, plus dev-appropriate identity:
#        - UA_DEPLOYMENT_PROFILE=local_workstation
#        - UA_MACHINE_SLUG=vps-dev
#        - FACTORY_ROLE=NONE  (dev-tree never acts as HQ or runs workers)
#        - INFISICAL_ENVIRONMENT=development
#        - UA_RUNTIME_STAGE=development
#   5. Runs `uv sync` in the dev-tree.
#   6. Verifies and reports.
#
# This dev-tree is *not* picked up by any systemd unit — UA's running services
# remain rooted in /opt/universal_agent. The dev-tree is purely Kevin's editing
# surface for Antigravity Remote-SSH workflows. /ship from this dev-tree pushes
# to origin and CI/CD deploys /opt/universal_agent as normal.

set -Eeuo pipefail

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------
DEV_PARENT=/home/ua/dev
DEV_REPO=$DEV_PARENT/universal_agent
PROD_REPO=/opt/universal_agent
PROD_ENV=$PROD_REPO/.env
DEV_ENV=$DEV_REPO/.env
GIT_URL=https://github.com/Kjdragan/universal_agent.git
GIT_BRANCH=feature/latest2

log()  { printf '\033[36m[D.1]\033[0m %s\n' "$*" >&2; }
ok()   { printf '\033[32m[D.1-OK]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[33m[D.1-WARN]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[D.1-FAIL]\033[0m %s\n' "$*" >&2; exit 1; }
as_ua(){ sudo -u ua -H "$@"; }

# ------------------------------------------------------------------
# Pre-flight
# ------------------------------------------------------------------
[[ $EUID -eq 0 ]] || die "Run as root (got EUID=$EUID)."
id -u ua >/dev/null 2>&1 || die "User 'ua' not found — wrong host?"
[[ -f "$PROD_ENV" ]] || die "$PROD_ENV not found. Phase A bootstrap missing?"

for tool in git python3 curl; do
  command -v "$tool" >/dev/null || die "$tool not on PATH"
done

# uv lives in ua's home in this project's pattern; check both locations.
UV_BIN=""
for c in /home/ua/.local/bin/uv /usr/local/bin/uv /usr/bin/uv; do
  [[ -x "$c" ]] && { UV_BIN="$c"; break; }
done
[[ -n "$UV_BIN" ]] || die "uv binary not found (looked in /home/ua/.local/bin, /usr/local/bin, /usr/bin)"
log "uv: $UV_BIN"

# ------------------------------------------------------------------
# Step 1 — parent dir
# ------------------------------------------------------------------
log "Ensuring $DEV_PARENT exists (ua-owned)"
if [[ ! -d "$DEV_PARENT" ]]; then
  install -d -m 755 -o ua -g ua "$DEV_PARENT"
  ok "Created $DEV_PARENT"
else
  ok "$DEV_PARENT already exists"
fi

# ------------------------------------------------------------------
# Step 2 — clone or fetch
# ------------------------------------------------------------------
if [[ ! -d "$DEV_REPO/.git" ]]; then
  log "Cloning $GIT_URL into $DEV_REPO"
  as_ua git clone "$GIT_URL" "$DEV_REPO"
  as_ua git -C "$DEV_REPO" checkout "$GIT_BRANCH"
  ok "Clone + checkout $GIT_BRANCH complete"
else
  log "$DEV_REPO already a git repo — fetching origin (no destructive ops)"
  as_ua git -C "$DEV_REPO" fetch origin --prune
  current_branch=$(as_ua git -C "$DEV_REPO" branch --show-current)
  ok "On branch '$current_branch' at $(as_ua git -C "$DEV_REPO" rev-parse --short HEAD)"
fi

# ------------------------------------------------------------------
# Step 3 — bootstrap .env (Infisical creds + dev identity)
# ------------------------------------------------------------------
log "Bootstrapping $DEV_ENV from $PROD_ENV (creds) + dev identity overrides"

extract() {
  awk -F= -v k="$1" '$1==k { sub(/^[^=]*=/, ""); gsub(/^["'"'"']|["'"'"']$/, ""); print; exit }' "$PROD_ENV"
}

CID=$(extract INFISICAL_CLIENT_ID)
CSEC=$(extract INFISICAL_CLIENT_SECRET)
PID=$(extract INFISICAL_PROJECT_ID)
[[ -n "$CID" && -n "$CSEC" && -n "$PID" ]] || die "Missing Infisical creds in $PROD_ENV"

# Write the dev .env. Permissions match prod (600, ua:ua).
TMP=$(mktemp)
cat > "$TMP" <<DEVENV
# /home/ua/dev/universal_agent/.env — Phase D.1 dev-tree bootstrap
# DO NOT COMMIT. Auto-generated by scripts/provision_vps_dev_tree.sh.
# This is the dev-tree analog of /opt/universal_agent/.env. Identity is
# scoped to a non-HQ workstation profile so this checkout never tries
# to act as the production HEADQUARTERS or run worker services.

INFISICAL_CLIENT_ID="$CID"
INFISICAL_CLIENT_SECRET="$CSEC"
INFISICAL_PROJECT_ID="$PID"
INFISICAL_ENVIRONMENT="development"

UA_RUNTIME_STAGE="development"
UA_INFISICAL_ENABLED="1"
UA_INFISICAL_STRICT="0"

FACTORY_ROLE="NONE"
UA_DEPLOYMENT_PROFILE="local_workstation"
UA_MACHINE_SLUG="vps-dev"

UA_GATEWAY_PORT="18002"
UA_API_PORT="18001"
UA_GATEWAY_URL="http://127.0.0.1:18002"
DEVENV

install -m 600 -o ua -g ua "$TMP" "$DEV_ENV"
rm -f "$TMP"
ok "Wrote $DEV_ENV (mode 600, owner ua:ua)"

# ------------------------------------------------------------------
# Step 4 — uv sync
# ------------------------------------------------------------------
log "Running 'uv sync' in $DEV_REPO (this can take ~30s on first run)"
if as_ua bash -c "cd '$DEV_REPO' && '$UV_BIN' sync"; then
  ok "uv sync complete"
else
  warn "uv sync had non-zero exit; inspect manually if surprising"
fi

# ------------------------------------------------------------------
# Step 5 — verification report
# ------------------------------------------------------------------
echo
log "=== Verification ==="
log "  path     : $DEV_REPO"
log "  branch   : $(as_ua git -C "$DEV_REPO" branch --show-current)"
log "  HEAD     : $(as_ua git -C "$DEV_REPO" rev-parse --short HEAD)"
log "  remote   : $(as_ua git -C "$DEV_REPO" remote get-url origin)"
log "  .env     : $(stat -c '%U:%G mode=%a size=%s' "$DEV_ENV")"
log "  venv     : $([[ -d "$DEV_REPO/.venv" ]] && echo present || echo missing)"

# Quick smoke: read INFISICAL_PROJECT_ID via the dev-tree's loader pattern.
log "  loader smoke (Infisical reachability):"
if as_ua bash -c "cd '$DEV_REPO' && '$DEV_REPO/.venv/bin/python' -c '
import os, sys
sys.path.insert(0, \"$DEV_REPO/src\")
from universal_agent.infisical_loader import initialize_runtime_secrets
try:
    initialize_runtime_secrets()
    url = os.environ.get(\"ANTHROPIC_BASE_URL\", \"<unset>\")
    print(f\"    ANTHROPIC_BASE_URL={url}\")
except Exception as e:
    print(f\"    smoke FAILED: {e}\")
    sys.exit(1)
'"; then
  ok "Infisical loader smoke passed"
else
  warn "Infisical loader smoke failed — investigate before using dev-tree"
fi

echo
ok "Phase D.1 complete."
echo "   Next steps for Kevin:"
echo "   1) Antigravity → Remote-SSH → connect to ua@uaonvps"
echo "   2) Open workspace: $DEV_REPO"
echo "   3) Install Claude Code extension on the REMOTE host (not locally)"
echo "   4) Open integrated terminal — verify whoami=ua, pwd=$DEV_REPO"
echo "   5) Run Phase D.3 acid tests (see docs/06_Deployment_And_Environments/11_Daily_Dev_Workflow.md)"

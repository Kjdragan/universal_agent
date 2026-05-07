#!/usr/bin/env bash
# apply_phase_b_inversion.sh — Phase B of the Interactive Coding Environment plan.
#
# Inverts /home/ua/.claude/settings.json and /home/ua/.bashrc on the VPS so
# that interactive `claude` invocations hit Anthropic Max while UA services
# continue to use ZAI/GLM via Infisical injection.
#
# Run on the VPS as ROOT. The script does its own user-context switching
# via sudo -u ua for /home/ua/ file edits.
#
# Usage:
#   bash apply_phase_b_inversion.sh           # dry-run; reports what it WOULD do
#   bash apply_phase_b_inversion.sh --apply   # actually mutate
#
# Safe to re-run. Idempotent.
#
# Pre-conditions:
#   - Phase A complete (5 ANTHROPIC_* keys staged in Infisical production env)
#   - Anthropic Max OAuth session present at /home/ua/.claude/.credentials.json
#     (or whatever the equivalent file is). Run `claude /login` from
#     /opt/ua_demos/_smoke first if missing.
#
# What it does (in order, fail-closed):
#   B.1  Verify OAuth credential file exists for ua user.
#   B.2  Restart UA services. Verify ANTHROPIC_BASE_URL=z.ai is in their env.
#   B.3  Backup + surgical-strip /home/ua/.claude/settings.json.
#   B.4  Append zai() function to /home/ua/.bashrc.
#
# Each step blocks the next on success.

set -euo pipefail

APPLY_MODE="dry-run"
if [[ "${1:-}" == "--apply" ]]; then APPLY_MODE="apply"; fi

UA_HOME=/home/ua
SETTINGS=$UA_HOME/.claude/settings.json
BASHRC=$UA_HOME/.bashrc
TS=$(date +%Y%m%d-%H%M%S)
BACKUP=$UA_HOME/.claude/settings.json.preinversion.$TS.bak
ZAI_MARKER='# --- ZAI explicit-opt-in wrapper (apply_phase_b_inversion.sh) ---'

log()  { printf '\033[36m[B]\033[0m %s\n' "$*" >&2; }
ok()   { printf '\033[32m[B-OK]\033[0m %s\n' "$*" >&2; }
warn() { printf '\033[33m[B-WARN]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[B-FAIL]\033[0m %s\n' "$*" >&2; exit 1; }
maybe(){ if [[ "$APPLY_MODE" == "apply" ]]; then "$@"; else log "DRY-RUN would: $*"; fi; }

[[ $EUID -eq 0 ]] || die "Run as root. Got EUID=$EUID."
[[ -d $UA_HOME ]] || die "$UA_HOME not found — wrong host?"
id -u ua >/dev/null 2>&1 || die "user 'ua' not found — wrong host?"
command -v jq >/dev/null || die "jq required (apt-get install -y jq)"
command -v systemctl >/dev/null || die "systemctl required"

log "Mode: $APPLY_MODE"
log "Host: $(hostname)"
log "UA home: $UA_HOME"

# ------------------------------------------------------------------
# B.1 — OAuth credential check
# ------------------------------------------------------------------
log "B.1: checking Anthropic Max OAuth session for ua user"
oauth_files=()
for f in "$UA_HOME/.claude/.credentials.json" \
         "$UA_HOME/.claude/credentials.json" \
         "$UA_HOME/.claude/auth.json"; do
  [[ -f $f ]] && oauth_files+=("$f")
done
if [[ ${#oauth_files[@]} -eq 0 ]]; then
  warn "No OAuth credentials file found in $UA_HOME/.claude/"
  warn "Listing the directory for diagnosis:"
  sudo -u ua ls -la $UA_HOME/.claude/ 2>&1 | head -20 >&2
  die "Run 'cd /opt/ua_demos/_smoke && claude /login' as ua user first, then re-run this script."
fi
ok "B.1 OAuth credentials present: ${oauth_files[*]}"

# ------------------------------------------------------------------
# B.2 — Restart UA services and verify Infisical-injected ZAI env
# ------------------------------------------------------------------
log "B.2: identifying UA services"
mapfile -t units < <(systemctl list-units --type=service --state=running --no-legend --plain 2>/dev/null \
                      | awk '{print $1}' \
                      | grep -E '^(universal-agent-|ua-)' || true)
if [[ ${#units[@]} -eq 0 ]]; then
  warn "No universal-agent-* / ua-* services currently running. Listing all:"
  systemctl list-units --type=service --no-legend --plain 2>/dev/null | grep -E '(universal-agent|ua-)' >&2 || true
  die "Cannot verify env injection without a running UA service. Investigate."
fi
log "Running UA services found: ${units[*]}"

# Pre-compute whether settings.json has any of the 5 keys to strip.
# This determines whether the env-injection verify is fail-closed.
[[ -f $SETTINGS ]] || die "$SETTINGS not found"
present=$(jq -r '
  (.env // {}) as $e |
  ["ANTHROPIC_BASE_URL","ANTHROPIC_AUTH_TOKEN","ANTHROPIC_DEFAULT_HAIKU_MODEL","ANTHROPIC_DEFAULT_SONNET_MODEL","ANTHROPIC_DEFAULT_OPUS_MODEL"]
  | map(select($e[.] != null)) | join(",")
' "$SETTINGS")
if [[ -z "$present" ]]; then
  log "settings.json has no ANTHROPIC_* keys — strip is a no-op; verify is informational only"
else
  log "settings.json carries keys to strip: $present — verify will fail-close"
fi

log "B.2: restarting UA services to ingest Infisical-injected ZAI vars"
maybe systemctl restart "${units[@]}"

if [[ "$APPLY_MODE" == "apply" ]]; then
  sleep 4  # give Python services time to call initialize_runtime_secrets
fi

log "B.2: contract test — invoke venv Python and verify Infisical injects ANTHROPIC_BASE_URL into os.environ"
# This is the *actual* mechanism by which UA services receive ZAI vars.
# /proc/<pid>/environ shows the EXEC-time env, which never carries these
# (systemd doesn't set them; Python injects via initialize_runtime_secrets()
# at runtime). The contract test below mirrors what real services do.
VENV_PY=/opt/universal_agent/.venv/bin/python
[[ -x $VENV_PY ]] || die "venv python not found at $VENV_PY"

contract_output=$(cd /opt/universal_agent && sudo -u ua env -i \
  HOME=/home/ua PATH=/usr/local/bin:/usr/bin:/bin \
  $VENV_PY -c '
import os, sys
sys.path.insert(0, "/opt/universal_agent/src")
from universal_agent.infisical_loader import initialize_runtime_secrets
try:
    initialize_runtime_secrets()
except Exception as e:
    print(f"FAIL initialize_runtime_secrets: {e}")
    sys.exit(2)
url = os.environ.get("ANTHROPIC_BASE_URL", "<unset>")
auth = "set" if os.environ.get("ANTHROPIC_AUTH_TOKEN") else "unset"
opus = os.environ.get("ANTHROPIC_DEFAULT_OPUS_MODEL", "<unset>")
print(f"ANTHROPIC_BASE_URL={url}")
print(f"ANTHROPIC_AUTH_TOKEN={auth}")
print(f"ANTHROPIC_DEFAULT_OPUS_MODEL={opus}")
sys.exit(0 if "z.ai" in url else 1)
' 2>&1) && contract_rc=$? || contract_rc=$?

echo "$contract_output" | sed 's/^/    /' >&2
verified=0
[[ "$contract_rc" == "0" ]] && verified=1

if [[ "$verified" -eq 1 ]]; then
  ok "B.2 contract test PASSED — initialize_runtime_secrets() injects z.ai into os.environ"
elif [[ "$APPLY_MODE" == "apply" && -n "$present" ]]; then
  die "B.2 contract test FAILED and settings.json has keys to strip. Refusing to proceed — would route services to Anthropic. Investigate Infisical: are the 5 ANTHROPIC_* keys staged in production env?"
else
  warn "B.2 contract test did not return z.ai. settings.json strip is a no-op so we proceed, but Phase G acid test must verify routing manually."
fi

# ------------------------------------------------------------------
# B.3 — Backup + surgical-strip settings.json
# ------------------------------------------------------------------
log "B.3: backup + surgical strip $SETTINGS"

if [[ -z "$present" ]]; then
  ok "B.3 settings.json env block already has no ANTHROPIC_* keys — no strip needed"
else
  log "B.3 keys to remove: $present"
  maybe sudo -u ua cp "$SETTINGS" "$BACKUP"
  if [[ "$APPLY_MODE" == "apply" ]]; then
    log "B.3 wrote backup: $BACKUP"
    sudo -u ua jq '
      if .env then
        .env |= (del(.ANTHROPIC_BASE_URL, .ANTHROPIC_AUTH_TOKEN, .ANTHROPIC_DEFAULT_HAIKU_MODEL, .ANTHROPIC_DEFAULT_SONNET_MODEL, .ANTHROPIC_DEFAULT_OPUS_MODEL))
      else . end
    ' "$SETTINGS" > "$SETTINGS.tmp" && sudo -u ua mv "$SETTINGS.tmp" "$SETTINGS"
    chown ua:ua "$SETTINGS"
    ok "B.3 stripped 5 ANTHROPIC_* keys from $SETTINGS"
  else
    log "DRY-RUN would: jq-strip 5 ANTHROPIC_* keys from $SETTINGS"
  fi
fi

# ------------------------------------------------------------------
# B.3.5 — Ensure Infisical CLI installed (zai() shells out to it)
# ------------------------------------------------------------------
if command -v infisical >/dev/null 2>&1; then
  ok "B.3.5 Infisical CLI present: $(command -v infisical)"
else
  log "B.3.5 Infisical CLI not installed — required by zai() function"
  if [[ "$APPLY_MODE" == "apply" ]]; then
    curl -1sLf "https://artifacts-cli.infisical.com/setup.deb.sh" | bash >/dev/null 2>&1
    apt-get install -y -qq infisical >/dev/null 2>&1
    command -v infisical >/dev/null 2>&1 || die "Infisical CLI install failed"
    ok "B.3.5 Infisical CLI installed: $(infisical --version 2>&1 | head -1)"
  else
    log "DRY-RUN would: install Infisical CLI from official deb repo"
  fi
fi

# ------------------------------------------------------------------
# B.4 — Append/replace zai() function in .bashrc
# ------------------------------------------------------------------
log "B.4: ensure zai() function in $BASHRC"
[[ -f $BASHRC ]] || maybe sudo -u ua touch "$BASHRC"

# The block we want present (canonical version).
ZAI_BLOCK_END='# --- end ZAI wrapper ---'
read -r -d '' ZAI_BLOCK <<'ZAI_EOF' || true

# --- ZAI explicit-opt-in wrapper (apply_phase_b_inversion.sh) ---
# Default `claude` hits Anthropic Max via OAuth.
# Use `zai` for cheap GLM inference in this terminal.
# See docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md
export INFISICAL_PROJECT_ID="${INFISICAL_PROJECT_ID:-9970e5b7-d48a-4ed8-a8af-43e923e67572}"
zai() {
  ( local creds=/opt/universal_agent/.env
    [[ -r "$creds" ]] || { echo "zai: cannot read $creds" >&2; exit 1; }
    set -a; source "$creds" >/dev/null 2>&1; set +a
    [[ -n "$INFISICAL_CLIENT_ID" && -n "$INFISICAL_CLIENT_SECRET" ]] \
      || { echo "zai: missing INFISICAL_CLIENT_ID/SECRET in $creds" >&2; exit 1; }
    local tok
    tok=$(infisical login --method=universal-auth \
            --client-id="$INFISICAL_CLIENT_ID" \
            --client-secret="$INFISICAL_CLIENT_SECRET" \
            --plain --silent 2>/dev/null) \
      || { echo "zai: infisical universal-auth failed" >&2; exit 1; }
    INFISICAL_TOKEN="$tok" infisical run \
      --env=production --projectId="$INFISICAL_PROJECT_ID" --silent -- \
      claude "$@"
  )
}
# --- end ZAI wrapper ---
ZAI_EOF

# Idempotent edit: detect any prior block (matched by the start marker),
# remove it, then append the canonical block. This way an out-of-date
# block from a prior run gets replaced cleanly.
if grep -qF "$ZAI_MARKER" "$BASHRC" 2>/dev/null; then
  log "B.4 detected existing zai block — will replace with canonical version"
  if [[ "$APPLY_MODE" == "apply" ]]; then
    sudo -u ua python3 - "$BASHRC" "$ZAI_MARKER" "$ZAI_BLOCK_END" <<'PY'
import sys, re
path, start_marker, end_marker = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path) as f: c = f.read()
# Remove any prior block: from start_marker through either end_marker or
# end of zai function (closing brace at column 0). Tolerates older versions
# that didn't have the explicit end marker.
pattern = re.compile(
    re.escape(start_marker) + r".*?(?:" + re.escape(end_marker) + r"|^\}\s*\n)",
    re.DOTALL | re.MULTILINE,
)
new = pattern.sub("", c).rstrip() + "\n"
with open(path, "w") as f: f.write(new)
PY
    sudo -u ua tee -a "$BASHRC" >/dev/null <<<"$ZAI_BLOCK"
    ok "B.4 replaced zai block in $BASHRC"
  else
    log "DRY-RUN would: replace existing zai block in $BASHRC with canonical version"
  fi
else
  if [[ "$APPLY_MODE" == "apply" ]]; then
    sudo -u ua tee -a "$BASHRC" >/dev/null <<<"$ZAI_BLOCK"
    ok "B.4 appended zai block to $BASHRC"
  else
    log "DRY-RUN would: append zai block to $BASHRC"
  fi
fi

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
echo
ok "Phase B complete. Mode: $APPLY_MODE"
if [[ "$APPLY_MODE" == "dry-run" ]]; then
  echo "   Re-run with --apply to actually mutate."
else
  echo "   Backup: $BACKUP"
  echo "   Next: open a NEW shell (so .bashrc loads zai), test:"
  echo "     ssh ua@uaonvps 'claude -p \"OK\"'   # should hit api.anthropic.com"
  echo "     ssh ua@uaonvps 'zai -p \"OK\"'      # should hit api.z.ai"
  echo "   Acid test (two terminals):"
  echo "     T1: ssh ua@uaonvps 'claude -p \"say hi\"'"
  echo "     T2: ssh ua@uaonvps 'ss -t state established | grep -E \"anthropic|z\\\\.ai\"'"
fi

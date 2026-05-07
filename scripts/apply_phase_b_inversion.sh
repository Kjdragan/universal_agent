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

log "B.2: restarting UA services to ingest Infisical-injected ZAI vars"
maybe systemctl restart "${units[@]}"

if [[ "$APPLY_MODE" == "apply" ]]; then
  sleep 4  # give Python services time to call initialize_runtime_secrets
fi

log "B.2: verifying ANTHROPIC_BASE_URL is injected into running UA processes"
verified=0
for unit in "${units[@]}"; do
  pid=$(systemctl show -p MainPID --value "$unit" 2>/dev/null || echo 0)
  [[ "$pid" == "0" || -z "$pid" ]] && continue
  if [[ ! -r /proc/$pid/environ ]]; then continue; fi
  base=$(tr '\0' '\n' < /proc/$pid/environ 2>/dev/null | awk -F= '/^ANTHROPIC_BASE_URL=/{print $2; exit}')
  if [[ -n "$base" ]]; then
    if [[ "$base" == *z.ai* ]]; then
      ok "  $unit (pid=$pid): ANTHROPIC_BASE_URL=$base ✓"
      verified=$((verified+1))
    else
      warn "  $unit (pid=$pid): ANTHROPIC_BASE_URL=$base (NOT z.ai!)"
    fi
  else
    warn "  $unit (pid=$pid): no ANTHROPIC_BASE_URL in env"
  fi
done

if [[ "$APPLY_MODE" == "apply" && $verified -eq 0 ]]; then
  die "No running UA service has ANTHROPIC_BASE_URL injected. Refusing to strip settings.json — would route services to Anthropic. Investigate Infisical secret loading."
fi
[[ "$APPLY_MODE" == "apply" ]] && ok "B.2 verified ($verified service(s) confirmed receiving z.ai from Infisical)"

# ------------------------------------------------------------------
# B.3 — Backup + surgical-strip settings.json
# ------------------------------------------------------------------
log "B.3: backup + surgical strip $SETTINGS"
[[ -f $SETTINGS ]] || die "$SETTINGS not found"

# Detect whether ANY of the 5 keys are present in env block
present=$(jq -r '
  (.env // {}) as $e |
  ["ANTHROPIC_BASE_URL","ANTHROPIC_AUTH_TOKEN","ANTHROPIC_DEFAULT_HAIKU_MODEL","ANTHROPIC_DEFAULT_SONNET_MODEL","ANTHROPIC_DEFAULT_OPUS_MODEL"]
  | map(select($e[.] != null)) | join(",")
' "$SETTINGS")

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
# B.4 — Append zai() function to .bashrc
# ------------------------------------------------------------------
log "B.4: ensure zai() function in $BASHRC"
[[ -f $BASHRC ]] || maybe sudo -u ua touch "$BASHRC"

if grep -qF "$ZAI_MARKER" "$BASHRC" 2>/dev/null; then
  ok "B.4 zai() block already present — no change"
else
  if [[ "$APPLY_MODE" == "apply" ]]; then
    sudo -u ua tee -a "$BASHRC" >/dev/null <<EOF

$ZAI_MARKER
# Default \`claude\` now hits Anthropic Max (after Phase B inversion).
# Use \`zai\` when you want cheap GLM inference in this terminal.
# See docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md
export INFISICAL_PROJECT_ID="\${INFISICAL_PROJECT_ID:-9970e5b7-d48a-4ed8-a8af-43e923e67572}"
zai() {
  infisical run --env=production --projectId="\$INFISICAL_PROJECT_ID" --silent -- \\
    claude "\$@"
}
EOF
    ok "B.4 appended zai() block to $BASHRC"
  else
    log "DRY-RUN would: append zai() block to $BASHRC"
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

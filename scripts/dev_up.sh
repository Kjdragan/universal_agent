#!/usr/bin/env bash
# ==============================================================================
# scripts/dev_up.sh
# ------------------------------------------------------------------------------
# One-command local development launcher for Universal Agent (HQ mode).
#
# What it does (in order):
#   1. Verifies Infisical bootstrap credentials are in the shell environment
#      (they must live in ~/.bashrc, never in files on disk).
#   2. Verifies required binaries (infisical, uv, node, npm, ssh).
#   3. Initializes fnm (if installed) so `node` and `npm` are on PATH.
#   4. Refuses to start if local services are already running (PID file).
#   5. SSHes to the VPS and pauses the services that would otherwise conflict
#      with a locally running HQ stack (Telegram long-poll, Discord bot, etc.).
#      Writes a pause-timestamp file on the VPS so the reconciler knows when it
#      is safe to auto-release the pause.
#   6. Exports local identity, ports, DB paths, and artifact dir into the
#      environment.
#   7. Launches api, gateway, and web-ui wrapped in `infisical run --env=local`
#      so secrets are injected into the child processes' memory only — never
#      written to disk.
#   8. Writes PIDs to /tmp/ua-local-dev.pids and per-service logs to
#      /tmp/ua-local-logs/. Prints a loud banner with the next commands to run
#      and the critical "DO NOT PUSH" rule.
#
# This script does NOT:
#   - Write any plaintext secret to disk.
#   - Modify the VPS repository or deployment.
#   - Run VP workers or the local factory by default.
#
# Companions:
#   scripts/dev_down.sh     Stop local stack and unpause VPS services.
#   scripts/dev_reset.sh    Wipe the local data dir (destructive, gated).
#   scripts/dev_status.sh   Read-only health check.
#
# Canonical docs: docs/development/LOCAL_DEV.md
# ==============================================================================
set -Eeuo pipefail

# ------------------------------------------------------------------------------
# EDIT-ME: VPS service units that conflict with a locally running HQ stack.
# These are paused on the VPS during local dev and resumed by dev_down.sh.
# If your VPS unit names drift from these, edit this block — nothing else.
# ------------------------------------------------------------------------------
VPS_CONFLICT_SERVICES=(
  "universal-agent-api.service"
  "universal-agent-gateway.service"
  "universal-agent-webui.service"
  "universal-agent-telegram.service"
  "ua-discord-cc-bot.service"
  "universal-agent-service-watchdog.service"
)

VPS_CONFLICT_TIMERS=(
  "universal-agent-service-watchdog.timer"
  "universal-agent-youtube-playlist-poller.timer"
)

# ------------------------------------------------------------------------------
# Paths and defaults. Override any of these via environment if needed.
# ------------------------------------------------------------------------------
APP_ROOT="${APP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOCAL_DATA_DIR="${UA_LOCAL_DATA_DIR:-$HOME/lrepos/universal_agent_local_data}"
LOG_DIR="${UA_LOCAL_LOG_DIR:-/tmp/ua-local-logs}"
PID_FILE="${UA_LOCAL_PID_FILE:-/tmp/ua-local-dev.pids}"

VPS_SSH_HOST="${UA_VPS_SSH_HOST:-root@uaonvps}"
VPS_SSH_KEY="${UA_VPS_SSH_KEY:-$HOME/.ssh/id_ed25519}"
VPS_PAUSE_STAMP_PATH="${UA_VPS_PAUSE_STAMP_PATH:-/etc/universal-agent/dev_pause.stamp}"
VPS_PAUSE_HOURS="${UA_VPS_PAUSE_HOURS:-8}"
VPS_SKIP_PAUSE="${UA_VPS_SKIP_PAUSE:-0}"

VPS_BANNER_STATE="unknown"

INFISICAL_ENV_SLUG="${UA_INFISICAL_ENV:-local}"
UA_API_PORT="${UA_API_PORT:-8001}"
UA_GATEWAY_PORT="${UA_GATEWAY_PORT:-8002}"
UA_WEBUI_PORT="${UA_WEBUI_PORT:-3000}"
UA_GATEWAY_URL="${UA_GATEWAY_URL:-http://127.0.0.1:${UA_GATEWAY_PORT}}"
UA_MACHINE_SLUG="${UA_MACHINE_SLUG:-kevins-desktop-dev}"
FACTORY_ROLE="${FACTORY_ROLE:-HEADQUARTERS}"
UA_DEPLOYMENT_PROFILE="${UA_DEPLOYMENT_PROFILE:-local_workstation}"
UA_RUNTIME_STAGE="${UA_RUNTIME_STAGE:-development}"

# ------------------------------------------------------------------------------
# Coloured output helpers.
# ------------------------------------------------------------------------------
if [[ -t 1 ]]; then
  C_RED=$'\033[31m'; C_YEL=$'\033[33m'; C_GRN=$'\033[32m'
  C_CYA=$'\033[36m'; C_BLD=$'\033[1m'; C_RST=$'\033[0m'
else
  C_RED=""; C_YEL=""; C_GRN=""; C_CYA=""; C_BLD=""; C_RST=""
fi
say()   { printf '%s[dev_up]%s %s\n' "$C_CYA" "$C_RST" "$*"; }
warn()  { printf '%s[dev_up]%s %s\n' "$C_YEL" "$C_RST" "$*" >&2; }
die()   { printf '%s[dev_up] ERROR:%s %s\n' "$C_RED" "$C_RST" "$*" >&2; exit 1; }

# ------------------------------------------------------------------------------
# Preflight: verify bootstrap creds and binaries.
# ------------------------------------------------------------------------------
preflight() {
  local missing=()
  [[ -n "${INFISICAL_CLIENT_ID:-}"     ]] || missing+=("INFISICAL_CLIENT_ID")
  [[ -n "${INFISICAL_CLIENT_SECRET:-}" ]] || missing+=("INFISICAL_CLIENT_SECRET")
  [[ -n "${INFISICAL_PROJECT_ID:-}"    ]] || missing+=("INFISICAL_PROJECT_ID")
  if (( ${#missing[@]} > 0 )); then
    die "Missing bootstrap env vars: ${missing[*]}
These must live in your shell profile (~/.bashrc). See docs/development/LOCAL_DEV.md."
  fi

  # Initialize fnm so node is on PATH in this non-interactive subshell.
  if command -v fnm >/dev/null 2>&1; then
    eval "$(fnm env --shell bash)"
    if [[ -s "$HOME/.fnm/aliases/default" || -s "${FNM_DIR:-$HOME/.local/share/fnm}/aliases/default" ]]; then
      fnm use default >/dev/null 2>&1 || true
    fi
  fi

  local need=(infisical uv node npm curl)
  # ssh is only required when we actually SSH to the VPS. If the user is
  # running with UA_VPS_SKIP_PAUSE=1 (e.g. VPS is unreachable on purpose),
  # don't demand ssh as a preflight precondition.
  if [[ "$VPS_SKIP_PAUSE" != "1" ]]; then
    need+=(ssh)
  fi
  local bad=()
  for bin in "${need[@]}"; do
    command -v "$bin" >/dev/null 2>&1 || bad+=("$bin")
  done
  if (( ${#bad[@]} > 0 )); then
    die "Missing binaries on PATH: ${bad[*]}
If node/npm are missing, install fnm and a Node LTS, or source your shell rc file."
  fi

  if [[ ! -d "$APP_ROOT/.venv" ]]; then
    die "Python venv not found at $APP_ROOT/.venv
Run: cd $APP_ROOT && uv sync"
  fi

  if [[ ! -d "$APP_ROOT/web-ui/node_modules" ]]; then
    warn "web-ui/node_modules is missing. Running 'npm install' now..."
    (cd "$APP_ROOT/web-ui" && npm install)
  fi

  if [[ -f "$PID_FILE" ]]; then
    local any_alive=0
    while IFS= read -r line; do
      local pid="${line##*:}"
      if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
        any_alive=1
      fi
    done < "$PID_FILE"
    if (( any_alive )); then
      die "Local dev services are already running. Run scripts/dev_down.sh first, or delete $PID_FILE if stale."
    fi
    rm -f "$PID_FILE"
  fi
}

# ------------------------------------------------------------------------------
# Pause conflicting VPS services via SSH. Idempotent: `systemctl stop` on an
# already-stopped unit is a no-op. Writes a timestamp file the VPS reconciler
# reads to decide whether to auto-release.
# ------------------------------------------------------------------------------
vps_pause() {
  if [[ "$VPS_SKIP_PAUSE" == "1" ]]; then
    warn "UA_VPS_SKIP_PAUSE=1 — skipping VPS hot-swap. You are responsible for any conflicts."
    VPS_BANNER_STATE="NOT touched (UA_VPS_SKIP_PAUSE=1) — you are responsible for any VPS/local conflicts"
    return 0
  fi

  say "Pausing VPS services on $VPS_SSH_HOST (pause window: ${VPS_PAUSE_HOURS}h)"

  local expire_ts
  expire_ts=$(( $(date +%s) + VPS_PAUSE_HOURS * 3600 ))
  local machine
  machine="$(hostname -s 2>/dev/null || echo unknown)"

  local stop_cmds=""
  for unit in "${VPS_CONFLICT_SERVICES[@]}" "${VPS_CONFLICT_TIMERS[@]}"; do
    stop_cmds+="systemctl stop '$unit' || true; "
  done

  local pause_dir
  pause_dir="$(dirname "$VPS_PAUSE_STAMP_PATH")"

  # Build one remote payload.
  local remote_cmd
  remote_cmd=$(cat <<REMOTE
set -e
mkdir -p '$pause_dir'
cat > '$VPS_PAUSE_STAMP_PATH' <<STAMP
# Universal Agent dev-mode pause stamp — do not edit by hand.
paused_by_host=$machine
paused_at_epoch=$(date +%s)
paused_at_iso=$(date -u +%Y-%m-%dT%H:%M:%SZ)
expires_at_epoch=$expire_ts
expires_at_iso=$(date -u -d "@$expire_ts" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)
reason=local_dev_hot_swap
STAMP
chmod 0644 '$VPS_PAUSE_STAMP_PATH'
$stop_cmds
echo "pause stamp installed at $VPS_PAUSE_STAMP_PATH"
REMOTE
)

  if ! ssh -i "$VPS_SSH_KEY" -o BatchMode=yes -o ConnectTimeout=10 \
        "$VPS_SSH_HOST" "bash -s" <<<"$remote_cmd"; then
    die "Failed to pause VPS services over SSH. Check: ssh -i $VPS_SSH_KEY $VPS_SSH_HOST 'echo ok'
If you genuinely want to run locally without touching the VPS (e.g. VPS is already down), re-run with UA_VPS_SKIP_PAUSE=1."
  fi

  say "VPS services paused. Reconciler will auto-release after $(date -d "@$expire_ts" '+%Y-%m-%d %H:%M %Z')."
  VPS_BANNER_STATE="paused (${VPS_PAUSE_HOURS}h window) via $VPS_SSH_HOST"
}

# ------------------------------------------------------------------------------
# Prepare local data/log dirs and export env vars the services will consume.
# These are set in the shell env BEFORE `infisical run`, so (because the
# Python loader uses overwrite=False) they take precedence over any values
# pulled from Infisical for keys we explicitly override here.
# ------------------------------------------------------------------------------
setup_local_env() {
  mkdir -p "$LOCAL_DATA_DIR" "$LOG_DIR"
  mkdir -p "$LOCAL_DATA_DIR/artifacts"

  # Runtime identity
  export FACTORY_ROLE
  export UA_RUNTIME_STAGE
  export UA_DEPLOYMENT_PROFILE
  export UA_MACHINE_SLUG
  export INFISICAL_ENVIRONMENT="$INFISICAL_ENV_SLUG"

  # Ports / URL (must match web-ui/next.config.js rewrites: 8001=api, 8002=gateway)
  export UA_API_PORT
  export UA_GATEWAY_PORT
  export UA_GATEWAY_URL

  # Isolated local DBs and artifact dir
  export UA_RUNTIME_DB_PATH="$LOCAL_DATA_DIR/runtime.db"
  export UA_CODER_VP_DB_PATH="$LOCAL_DATA_DIR/coder_vp.db"
  export UA_VP_DB_PATH="$LOCAL_DATA_DIR/vp.db"
  export UA_ACTIVITY_DB_PATH="$LOCAL_DATA_DIR/activity.db"
  export UA_LOSSLESS_DB_PATH="$LOCAL_DATA_DIR/lossless.db"
  export UA_ARTIFACTS_DIR="$LOCAL_DATA_DIR/artifacts"

  # Python path for services launched via `python -m universal_agent.*`
  export PYTHONPATH="$APP_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
  export PYTHONUNBUFFERED=1
  export PYDANTIC_DISABLE_PLUGINS="logfire-plugin"
}

# ------------------------------------------------------------------------------
# Render web-ui/.env.local directly via the Python renderer. We bypass
# scripts/install_local_webui_env.sh because that wrapper requires $APP_ROOT/.env
# to exist — a contract from the deprecated bootstrap_local_hq_dev.sh flow. In
# the new flow we deliberately do NOT keep a .env on disk, so we call the
# renderer directly. The renderer pulls secrets from Infisical into process
# memory, writes only non-secret runtime config (identity, ports, tokens) to
# the file, and the file itself is .gitignore'd.
# ------------------------------------------------------------------------------
render_webui_env() {
  local webui_env="$APP_ROOT/web-ui/.env.local"
  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/ua-local-webui-env.XXXXXX")"
  say "Rendering $webui_env from Infisical-backed runtime env"

  # Run the renderer under infisical so secrets are injected into its memory
  # only. The renderer calls initialize_runtime_secrets(force_reload=True),
  # which also pulls via SDK, but wrapping in `infisical run` guarantees the
  # child has everything it needs regardless of which path the loader takes.
  if ! env PYTHONPATH="$APP_ROOT/src" \
       infisical run \
         --env="$INFISICAL_ENV_SLUG" \
         --projectId="$INFISICAL_PROJECT_ID" \
         -- "$APP_ROOT/.venv/bin/python" \
              "$APP_ROOT/scripts/render_service_env_from_infisical.py" \
              --profile "$UA_DEPLOYMENT_PROFILE" \
              --include-runtime-identity \
              --output "$tmp" \
              --entry "UA_DASHBOARD_OPS_TOKEN=UA_DASHBOARD_OPS_TOKEN,UA_OPS_TOKEN" \
       >/dev/null; then
    rm -f "$tmp"
    die "Failed to render $webui_env. Check that the 'local' Infisical env exists and your bootstrap creds are valid."
  fi

  mkdir -p "$(dirname "$webui_env")"
  install -m 600 "$tmp" "$webui_env"
  rm -f "$tmp"
}

# ------------------------------------------------------------------------------
# Launch one service wrapped in `infisical run --env=local`. Secrets are
# injected into the child process's env only; nothing touches disk.
# Records PID into $PID_FILE as "<name>:<pid>".
# ------------------------------------------------------------------------------
launch_service() {
  local name="$1"; shift
  local logfile="$LOG_DIR/${name}.log"

  say "Starting $name → $logfile"

  # nohup so the child survives the parent shell exit.
  nohup infisical run \
    --env="$INFISICAL_ENV_SLUG" \
    --projectId="$INFISICAL_PROJECT_ID" \
    -- "$@" \
    >>"$logfile" 2>&1 &

  local pid=$!
  printf '%s:%s\n' "$name" "$pid" >> "$PID_FILE"
  disown "$pid" 2>/dev/null || true
}

start_local_stack() {
  : > "$PID_FILE"

  local py="$APP_ROOT/.venv/bin/python"

  launch_service "gateway" \
    "$py" -m universal_agent.gateway_server

  launch_service "api" \
    "$py" -m universal_agent.api.server

  # Web UI runs `next dev` out of web-ui/. The renderer wrote .env.local.
  launch_service "webui" \
    bash -lc "cd '$APP_ROOT/web-ui' && exec npm run dev -- --port $UA_WEBUI_PORT"
}

# ------------------------------------------------------------------------------
# Best-effort health check on the main Web UI port. Does not fail the script.
# ------------------------------------------------------------------------------
brief_health_check() {
  say "Waiting briefly for web-ui to come up on :$UA_WEBUI_PORT ..."
  local i=0
  while (( i < 30 )); do
    if curl -fsS "http://127.0.0.1:${UA_WEBUI_PORT}/" >/dev/null 2>&1; then
      say "${C_GRN}web-ui is responding${C_RST}"
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  warn "web-ui has not responded after 30s — check $LOG_DIR/webui.log"
}

# ------------------------------------------------------------------------------
# Loud final banner. State-coordination rule is repeated here per §13 / the
# state-machine discussion: do not push to develop or main while dev mode is on.
# ------------------------------------------------------------------------------
print_banner() {
  cat <<EOF

${C_GRN}${C_BLD}========================================================================
 Universal Agent — LOCAL DEV MODE (State B)
========================================================================${C_RST}
 Web UI:    ${C_CYA}http://localhost:${UA_WEBUI_PORT}${C_RST}
 API:       http://127.0.0.1:${UA_API_PORT}
 Gateway:   ${UA_GATEWAY_URL}
 Data dir:  $LOCAL_DATA_DIR
 Logs dir:  $LOG_DIR
 PID file:  $PID_FILE
 Infisical: env=${INFISICAL_ENV_SLUG} project=${INFISICAL_PROJECT_ID}
 VPS:       ${VPS_BANNER_STATE}

 Stop:      ${C_CYA}scripts/dev_down.sh${C_RST}
 Status:    ${C_CYA}scripts/dev_status.sh${C_RST}
 Reset DB:  scripts/dev_reset.sh

${C_RED}${C_BLD} DO NOT PUSH TO develop OR main WHILE LOCAL DEV IS RUNNING.${C_RST}
${C_YEL} A deploy will restart the paused services on the VPS and collide
 with this local stack (Telegram long-poll, Discord bot, queue workers).
 Always: scripts/dev_down.sh  →  commit  →  push.${C_RST}

 Reminder: the 'local' Infisical env is a copy of production. Anything
 you do here that writes to shared infra (Redis, Postgres, Slack, Discord,
 Telegram, AgentMail) IS HITTING THE REAL WORLD. See LOCAL_DEV.md.
${C_GRN}${C_BLD}========================================================================${C_RST}

EOF
}

main() {
  say "APP_ROOT=$APP_ROOT"
  preflight
  vps_pause
  setup_local_env
  render_webui_env
  start_local_stack
  brief_health_check
  print_banner
}

main "$@"

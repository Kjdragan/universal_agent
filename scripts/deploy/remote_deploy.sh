#!/usr/bin/env bash
# Production VPS deploy — runs ON the VPS, fed over SSH stdin by
# .github/workflows/deploy.yml:  cat scripts/deploy/remote_deploy.sh | ssh <vps> bash -s
#
# Extracted 2026-05-30 from the 420-line inline heredoc that used to live in
# deploy.yml (the "Phase 1" decomposition). Keeping the logic in a committed,
# lintable script (analyzable by ShellCheck) instead of bash-in-YAML-in-a-heredoc
# removes the GitHub-Actions-parser fragility (the 2026-05-27 quirk) and makes
# the deploy logic testable/diffable locally. The workflow now stays ~60 lines.
# NOTE: do not start a comment line with "# shellcheck" unless it's a real
# ShellCheck directive — ShellCheck parses "# shellcheck ..." as a directive.
#
# The three INFISICAL_* bootstrap secrets are NOT baked into this file (no
# ${{ secrets }} placeholders). deploy.yml prepends them as `export` lines on
# stdin (via stdin, not argv, so they never appear in the VPS process table)
# right before this script's body. The guards below fail fast if they're missing.

: "${INFISICAL_CLIENT_ID:?INFISICAL_CLIENT_ID must be exported by deploy.yml}"
: "${INFISICAL_CLIENT_SECRET:?INFISICAL_CLIENT_SECRET must be exported by deploy.yml}"
: "${INFISICAL_PROJECT_ID:?INFISICAL_PROJECT_ID must be exported by deploy.yml}"

# Exit immediately on error
set -euo pipefail
PROD_DIR="/opt/universal_agent"
REPO_URL="https://github.com/Kjdragan/universal_agent.git"

if [ ! -d "$PROD_DIR/.git" ]; then
  echo "--> Production repository missing at $PROD_DIR; bootstrapping clone..."
  if [ -e "$PROD_DIR" ] && [ ! -d "$PROD_DIR" ]; then
    echo "Error: $PROD_DIR exists but is not a directory."
    exit 1
  fi
  if [ -d "$PROD_DIR" ] && [ -n "$(ls -A "$PROD_DIR" 2>/dev/null)" ]; then
    echo "--> WARNING: $PROD_DIR is non-empty and not a git checkout; using /opt/universal_agent_repo instead."
    PROD_DIR="/opt/universal_agent_repo"
    if [ -e "$PROD_DIR" ] && [ ! -d "$PROD_DIR" ]; then
      echo "Error: $PROD_DIR exists but is not a directory."
      exit 1
    fi
  fi
  if [ ! -d "$PROD_DIR/.git" ]; then
    mkdir -p "$(dirname "$PROD_DIR")"
    if [ -d "$PROD_DIR" ] && [ -n "$(ls -A "$PROD_DIR" 2>/dev/null)" ] && [ ! -d "$PROD_DIR/.git" ]; then
      echo "Error: $PROD_DIR exists and is non-empty but is not a git checkout."
      exit 1
    fi
    git clone "$REPO_URL" "$PROD_DIR"
  fi
fi

echo "Deploying Production to $PROD_DIR..."

cd "$PROD_DIR"

# 1. Pull latest code
echo "--> Checking out branch main in $PROD_DIR"
cd "$PROD_DIR"
git config --global --add safe.directory "$PROD_DIR"

# Guard: remove stale git lock if no git process is running
# (prevents race when two deploys run in quick succession)
if [ -f "$PROD_DIR/.git/index.lock" ]; then
  if ! pgrep -f "git.*$PROD_DIR" > /dev/null 2>&1; then
    echo "---> Removing stale git index.lock (no active git process found)"
    rm -f "$PROD_DIR/.git/index.lock"
  else
    echo "---> WARNING: git lock exists AND a git process is running — waiting 10s..."
    sleep 10
    if [ -f "$PROD_DIR/.git/index.lock" ]; then
      echo "---> Lock still present after wait; removing it to proceed."
      rm -f "$PROD_DIR/.git/index.lock"
    fi
  fi
fi
git fetch origin main
git reset --hard origin/main
# Sync the local 'main' branch pointer to origin/main too. Without
# this, repeated deploys advance HEAD and the working tree but
# leave the local main branch ref stuck at its old SHA. Anyone
# later running 'git checkout main' on the production tree (e.g.
# operator during incident recovery) lands on stale code.
# See Followup #2 in docs/operations/2026-05-07_open_followups.md.
git update-ref refs/heads/main "$(git rev-parse origin/main)"

echo "--> Transferring ownership to service user before dependency sync..."
# || true: tolerate ENOENT from transient SQLite WAL/SHM files
# that may vanish mid-scan while the running service writes to DB.
sudo chown -R ua:ua "$PROD_DIR" || true

echo "--> Preparing production Infisical bootstrap..."
export PATH="$HOME/.local/bin:/home/ua/.local/bin:/usr/local/bin:$PATH"

run_as_ua() {
  ua_home=$(getent passwd ua | cut -d: -f6)
  if [ -z "$ua_home" ]; then
    ua_home='/home/ua'
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo -H -u ua env HOME="$ua_home" /bin/bash -lc "$1"
  elif command -v runuser >/dev/null 2>&1; then
    runuser -u ua -- env HOME="$ua_home" /bin/bash -lc "$1"
  else
    HOME="$ua_home" /bin/bash -lc "$1"
  fi
}

echo "--> Writing clean production bootstrap .env..."
export PROD_DIR
# UA_NOTEBOOKLM_PROFILE='default': the paper-to-podcast pipeline's NotebookLM
# auth. notebooklm_runtime.notebooklm_profile() defaults to 'vps' when unset,
# but the 'vps' profile is fed from the (stale) NOTEBOOKLM_AUTH_COOKIE_HEADER
# seed secret and was expired, while the VPS's 'default' profile holds a live
# kevinjdragan@gmail.com session. Pinning 'default' here points the pipeline at
# the valid session (no browser re-auth) and disables the seed path
# (notebooklm_auth_seed_enabled forces False for any non-'vps' profile), so the
# good cookies are never clobbered. Re-auth, when eventually needed, is a plain
# `nlm login` writing ~/.nlm/cookies.txt. See memory: notebooklm_profile_mismatch.
#
# UA_HACKERNEWS_SNAPSHOT_ENABLED='0': operator-disabled (2026-06-04). The
# hackernews_snapshot cron's _hydrate_stories ThreadPoolExecutor + _run_cli
# subprocess panels intermittently hit "can't start new thread" under gateway
# thread-pressure. _proactive_cron_enabled defaults this flag to '1', and
# gateway_server._register_system_cron_job re-applies the env-derived enabled
# state on every startup (so a dashboard toggle is re-enabled on the next
# restart/deploy). Pinning '0' here is the durable off-switch; the VPS .env is
# rewritten from this dict on every deploy. Re-enable by flipping to '1' (or
# remove the key — the code default is enabled) and address the thread-spawn
# resilience first. See project_docs/03_agents/03_heartbeat_service.md.
python3 -c "from pathlib import Path; import json, os; env_path = Path(os.environ['PROD_DIR']) / '.env'; bootstrap = {'INFISICAL_CLIENT_ID': os.environ['INFISICAL_CLIENT_ID'], 'INFISICAL_CLIENT_SECRET': os.environ['INFISICAL_CLIENT_SECRET'], 'INFISICAL_PROJECT_ID': os.environ['INFISICAL_PROJECT_ID'], 'INFISICAL_ENVIRONMENT': 'production', 'UA_RUNTIME_STAGE': 'production', 'FACTORY_ROLE': 'HEADQUARTERS', 'UA_DEPLOYMENT_PROFILE': 'vps', 'UA_NOTEBOOKLM_PROFILE': 'default', 'UA_HACKERNEWS_SNAPSHOT_ENABLED': '0', 'UA_MACHINE_SLUG': 'vps-hq-production', 'UA_INFISICAL_ENABLED': '1', 'UA_GATEWAY_PORT': '8002', 'UA_API_PORT': '8001', 'UA_GATEWAY_URL': 'http://127.0.0.1:8002'}; env_path.write_text(''.join(f'{key}={json.dumps(str(value))}\n' for key, value in bootstrap.items()), encoding='utf-8')"
sudo chown ua:ua "$PROD_DIR/.env"
sudo chmod 600 "$PROD_DIR/.env"

echo "--> Running centralized production runtime preflight..."
bash "$PROD_DIR/scripts/deploy_validate_runtime.sh" \
  --app-root "$PROD_DIR" \
  --profile vps \
  --expect-environment production \
  --expect-runtime-stage production \
  --expect-factory-role HEADQUARTERS \
  --expect-deployment-profile vps \
  --expect-machine-slug vps-hq-production \
  --require UA_OPS_TOKEN

echo "--> Rendering webui env from Infisical..."
run_as_ua "export PATH=\"$PATH\"; cd $PROD_DIR && PYTHONPATH=src ./.venv/bin/python scripts/render_service_env_from_infisical.py --profile vps --output web-ui/.env.local --entry UA_DASHBOARD_OPS_TOKEN=UA_DASHBOARD_OPS_TOKEN,UA_OPS_TOKEN"

echo "--> Installing NotebookLM CLI/MCP tool for ua..."
run_as_ua "export PATH=\"$PATH\"; uv tool install --force notebooklm-mcp-cli"
run_as_ua "export PATH=\"$PATH\"; command -v nlm && command -v notebooklm-mcp"

# Prune uv caches every deploy — bounds the disk-usage climb fixed 2026-06-04
# (four unpruned uv caches reached ~65G and pushed disk 70%->78% over ~2 weeks;
# `uv sync` + the floating `uv tool install --force notebooklm-mcp-cli` above
# repopulate them every deploy). Logic lives in scripts/prune_uv_caches.sh —
# the SINGLE SOURCE OF TRUTH shared with the universal-agent-uv-cache-prune
# timer (installed below) so the two callers can never drift. Non-fatal: a
# prune hiccup must never fail a deploy. Full rationale + the why behind
# `--ci --force`: project_docs/06_platform/04_deployment_and_cicd.md.
bash "$PROD_DIR/scripts/prune_uv_caches.sh" \
  || echo "WARN: prune_uv_caches.sh failed (non-fatal)"

echo "--> Installing goplaces CLI..."
run_as_ua "export PATH=\"$PATH\"; if [ ! -x \"$HOME/.local/bin/goplaces\" ]; then echo 'Downloading goplaces v0.3.0...'; mkdir -p /tmp/goplaces-dl && cd /tmp/goplaces-dl && curl -sSLO https://github.com/steipete/goplaces/releases/download/v0.3.0/goplaces_0.3.0_linux_amd64.tar.gz && tar -xzf goplaces_0.3.0_linux_amd64.tar.gz && mkdir -p ~/.local/bin && mv goplaces ~/.local/bin/ && chmod +x ~/.local/bin/goplaces && cd ~/ && rm -rf /tmp/goplaces-dl; else echo 'goplaces is already installed.'; fi"

echo "--> Installing hackernews-pp-cli (Phase 1 HN dashboard tab)..."
# Idempotent — only downloads if missing. Targets ~/.local/bin/ (not
# /opt/universal_agent/bin/) so it survives `git clean` / repo
# resets — see issue #179. The install script does its own SHA-pin
# verification + smoke test (`doctor`) before reporting success.
run_as_ua "export PATH=\"\$PATH\"; if [ ! -x \"\$HOME/.local/bin/hackernews-pp-cli\" ]; then bash $PROD_DIR/scripts/install_hackernews_cli.sh; else echo 'hackernews-pp-cli is already installed.'; fi"

echo "--> Building web-ui Next.js application..."
run_as_ua "export PATH=\"$PATH\"; export NODE_OPTIONS='--max-old-space-size=1536'; if [ -d \"$PROD_DIR/web-ui\" ] && command -v npm >/dev/null 2>&1; then cd \"$PROD_DIR/web-ui\" && [ -f next.config.js ] || true; if [ package.json -nt node_modules/.package-json-mtime ] 2>/dev/null || [ ! -d node_modules ]; then echo '--> package.json changed or node_modules missing — running npm install...'; npm install && touch node_modules/.package-json-mtime; else echo '--> node_modules up to date, skipping npm install'; fi && echo '--> Removing stale Next.js build artifacts...' && rm -rf .next && npm run build; else echo 'WARN: web-ui or npm missing, skipping web build'; fi"

echo "--> Building MkDocs documentation site..."
run_as_ua "export PATH=\"$PATH\"; cd $PROD_DIR && .venv/bin/mkdocs build -f mkdocs.yml --quiet" || echo 'WARN: MkDocs build failed; docs site may be stale'

# 3. Restart Systemd Services
# Set deployment-window flag so CSI canary suppresses SLO alerts during restart
echo "--> Setting deployment-window flag..."
touch /tmp/ua-deployment-window
cleanup_deployment_window() {
  rm -f /tmp/ua-deployment-window
}
trap cleanup_deployment_window EXIT
# Schedule automatic cleanup of the flag after 25 minutes (covers worst-case startup)
(sleep 1500 && rm -f /tmp/ua-deployment-window) </dev/null >/dev/null 2>&1 &

echo "--> Installing canonical production systemd units from repo templates..."
sudo bash "$PROD_DIR/scripts/install_vps_systemd_units.sh" --lane production --app-root "$PROD_DIR"
echo "--> Installing canonical VP worker unit template from repo..."
sudo bash "$PROD_DIR/scripts/install_vp_worker_services.sh" vp.coder.primary vp.general.primary
echo "--> Installing uv-cache-prune timer (daily deploy-independent disk backstop)..."
# Idempotent; ensures the daily prune timer survives a VPS rebuild and stays in
# sync with the repo. Non-fatal — a timer-install hiccup must not fail a deploy
# (the deploy already pruned inline above). See prune_uv_caches.sh.
sudo bash "$PROD_DIR/scripts/install_uv_cache_prune_timer.sh" \
  || echo "WARN: install_uv_cache_prune_timer.sh failed (non-fatal)"
# Sync the CSI lane's systemd units (timers + services). Without
# this, edits to CSI_Ingester/development/deployment/systemd/*.{service,timer}
# land in the repo but never reach /etc/systemd/system/, so the
# timer keeps running the old unit file (e.g. the 2026-05-16
# YouTube-transcript outage: rss-semantic-enrich timer was
# disabled and the env-file fix never took effect). The install
# script is idempotent.
if [ -x "$PROD_DIR/CSI_Ingester/development/scripts/csi_install_systemd_extras.sh" ]; then
  echo "--> Installing canonical CSI systemd units..."
  sudo bash "$PROD_DIR/CSI_Ingester/development/scripts/csi_install_systemd_extras.sh" || echo "WARN: csi_install_systemd_extras.sh exited non-zero; investigate post-deploy"
else
  echo "WARN: csi_install_systemd_extras.sh missing or not executable; CSI timers may drift"
fi

# Capture discord-services state BEFORE the restart so the
# post-restart health gate can distinguish "this deploy broke
# discord" (regression — fail) from "discord was already
# crashing before this deploy" (pre-existing — warn only).
# See PR #259 deploy-fail (2026-05-12): all HTTP services
# healthy on the new SHA, but ua-discord-intelligence was in
# an existing crash loop and the old hard gate flagged the
# whole deploy as failed.
discord_cc_pre="unknown"
discord_intel_pre="unknown"
if command -v systemctl >/dev/null 2>&1; then
  discord_cc_pre="$(systemctl is-active ua-discord-cc-bot 2>/dev/null || echo unknown)"
  discord_intel_pre="$(systemctl is-active ua-discord-intelligence 2>/dev/null || echo unknown)"
  echo "--> Discord baseline (pre-deploy): cc-bot=$discord_cc_pre intelligence=$discord_intel_pre"
fi

# Sync project skills to the ua user's ~/.claude/skills/ so they're
# discoverable from any CWD — including VP worker subprocess
# workspaces that run from /opt/universal_agent/AGENT_RUN_WORKSPACES/
# (where the project-relative .claude/skills/ isn't visible).
# Without this, skills like self-brief-and-attest deployed in the
# repo wouldn't be reachable by Cody's CLI subprocess. Discovery
# is hard-tested by the smoke-test on 2026-05-26 — Cody never
# invoked self-brief-and-attest because it wasn't in its session's
# skills[] list.
#
# Idempotent: rsync mirrors the project skills dir into the user
# location; --delete keeps the target in sync if a skill is
# removed upstream.
echo "--> Syncing project skills to ua user-level for VP worker discovery..."
sudo -u ua mkdir -p /home/ua/.claude/skills
if [ -d "$PROD_DIR/.claude/skills" ]; then
  sudo rsync -a --delete "$PROD_DIR/.claude/skills/" /home/ua/.claude/skills/
  sudo chown -R ua:ua /home/ua/.claude/skills
  ls -1 /home/ua/.claude/skills 2>&1 | sed 's/^/  /' | head -20 || true
else
  echo "  (no .claude/skills/ dir in repo — skipping skill sync)"
fi

echo "--> Restarting production services..."
if command -v systemctl >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1; then
    sudo systemctl restart universal-agent-gateway universal-agent-api universal-agent-webui universal-agent-telegram ua-discord-cc-bot ua-discord-intelligence
    # Restart VP workers so they pick up new code (graceful: systemd sends SIGTERM)
    for vp_svc in universal-agent-vp-worker@vp.coder.primary universal-agent-vp-worker@vp.general.primary; do
      if systemctl is-enabled "$vp_svc" >/dev/null 2>&1; then
        echo "--> Restarting VP worker: $vp_svc"
        sudo systemctl restart "$vp_svc"
      fi
    done
  else
    systemctl restart universal-agent-gateway universal-agent-api universal-agent-webui universal-agent-telegram ua-discord-cc-bot ua-discord-intelligence
    for vp_svc in universal-agent-vp-worker@vp.coder.primary universal-agent-vp-worker@vp.general.primary; do
      if systemctl is-enabled "$vp_svc" >/dev/null 2>&1; then
        echo "--> Restarting VP worker: $vp_svc"
        systemctl restart "$vp_svc"
      fi
    done
  fi
elif command -v service >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1; then
    sudo service universal-agent-gateway restart
    sudo service universal-agent-api restart
    sudo service universal-agent-webui restart
    sudo service universal-agent-telegram restart
  else
    service universal-agent-gateway restart
    service universal-agent-api restart
    service universal-agent-webui restart
    service universal-agent-telegram restart
  fi
else
  echo "WARNING: No supported service manager found (systemctl/service); skipping restart."
fi

echo "--> Verifying Python services use current venv interpreter..."
ensure_current_venv_interpreter() {
  service_name="$1"
  expected_python="$(readlink -f "$PROD_DIR/.venv/bin/python")"
  pid="$(systemctl show -p ExecMainPID --value "$service_name" 2>/dev/null || true)"
  if [ -z "$pid" ] || [ "$pid" = "0" ]; then
    echo "    $service_name has no active main PID yet; skipping interpreter comparison."
    return 0
  fi
  actual_python="$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)"
  echo "    $service_name pid=$pid actual=$actual_python expected=$expected_python"
  if [ -n "$actual_python" ] && [ "$actual_python" != "$expected_python" ]; then
    echo "    Restarting $service_name because it is running an old interpreter."
    sudo systemctl restart "$service_name"
  fi
}
if command -v systemctl >/dev/null 2>&1; then
  ensure_current_venv_interpreter universal-agent-gateway
  ensure_current_venv_interpreter universal-agent-api
  ensure_current_venv_interpreter ua-discord-intelligence
fi

echo "--> Verifying production service health..."
check_local_health() {
  name="$1"
  url="$2"
  max_attempts="${3:-36}"
  sleep_seconds="${4:-5}"
  service_unit="${5:-}"
  attempt=1
  while [ "$attempt" -le "$max_attempts" ]; do
    if curl -fsS -m 4 "$url" >/tmp/ua-health-${name}.json 2>/tmp/ua-health-${name}.err; then
      echo "    [OK] $name health responded at $url"
      cat /tmp/ua-health-${name}.json | head -c 500 || true
      echo ""
      rm -f /tmp/ua-health-${name}.json /tmp/ua-health-${name}.err
      return 0
    fi
    bash "$PROD_DIR/scripts/check_crashloop.sh" "$name" "$service_unit" "$attempt" 5 || return 1
    if [ "$attempt" -eq 1 ] || [ $((attempt % 6)) -eq 0 ]; then
      echo "    Waiting for $name health at $url (attempt $attempt/$max_attempts)..."
      cat /tmp/ua-health-${name}.err || true
    fi
    attempt=$((attempt + 1))
    sleep "$sleep_seconds"
  done
  echo "::error::$name did not become healthy at $url"
  cat /tmp/ua-health-${name}.err || true
  return 1
}

health_status_dir="$(mktemp -d)"
run_health_check() {
  name="$1"
  url="$2"
  max_attempts="$3"
  sleep_seconds="$4"
  service_unit="${5:-}"
  (
    if check_local_health "$name" "$url" "$max_attempts" "$sleep_seconds" "$service_unit"; then
      echo 0 >"$health_status_dir/$name.status"
    else
      echo 1 >"$health_status_dir/$name.status"
    fi
  ) &
  health_pids="$health_pids $!"
}
health_ok=true
health_pids=""
# Gateway: 96 × 5s = 8 minutes. The lifespan startup at
# gateway_server.py:13980-14714 runs ~734 lines of synchronous
# subsystem init (factory registry, runtime DB schema migration,
# heartbeat session seed, daemon session seed, task lifecycle
# reconcile, email mapping reconcile, task recovery sweep,
# autonomous cron registration, session reaper, workspace
# archiver, _reconcile_stale_vp_missions_on_startup) before
# FastAPI begins serving — and accumulated production state
# has pushed total cold-start past the previous 4-minute
# window (Deploy #436 + #437 both timed out at 4:00 even
# though the gateway came up healthy seconds later). The
# right architectural fix is to slim the pre-yield work
# (tracked separately); 8 minutes is the operational backstop.
run_health_check gateway "http://127.0.0.1:8002/api/v1/health" 96 5 universal-agent-gateway.service
run_health_check api "http://127.0.0.1:8001/api/health" 24 5
run_health_check webui "http://127.0.0.1:3000/dashboard" 24 5
for health_pid in $health_pids; do
  wait "$health_pid" || true
done
for health_name in gateway api webui; do
  if [ "$(cat "$health_status_dir/$health_name.status" 2>/dev/null || echo 1)" != "0" ]; then
    health_ok=false
  fi
done
rm -rf "$health_status_dir"
# Discord services: baseline-aware health check. Only fail
# the deploy if the service was healthy BEFORE the deploy
# and is unhealthy AFTER (true regression). Pre-existing
# crash loops surface as a warning so chronic discord
# flakiness doesn't mask real PR-caused failures elsewhere.
check_discord_regression() {
  svc="$1"
  pre_state="$2"
  if systemctl is-active --quiet "$svc"; then
    if [ "$pre_state" != "active" ] && [ "$pre_state" != "unknown" ]; then
      echo "    [RECOVERED] $svc transitioned $pre_state -> active across this deploy."
    fi
    return 0
  fi
  now_state="$(systemctl is-active "$svc" 2>/dev/null || echo unknown)"
  if [ "$pre_state" = "active" ]; then
    echo "::error::$svc was active pre-deploy but is now $now_state — likely regression caused by this deploy."
    return 1
  fi
  echo "::warning::$svc is $now_state (pre-deploy was $pre_state). Pre-existing failure, not blocking this deploy."
  return 0
}
if command -v systemctl >/dev/null 2>&1; then
  check_discord_regression ua-discord-cc-bot "$discord_cc_pre" || health_ok=false
  check_discord_regression ua-discord-intelligence "$discord_intel_pre" || health_ok=false
fi

if [ "$health_ok" != "true" ]; then
  echo "--> Production service health check failed; collecting diagnostics..."
  if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl status universal-agent-gateway universal-agent-api universal-agent-webui --no-pager || true
    sudo journalctl -u universal-agent-gateway -n 120 --no-pager || true
    sudo journalctl -u universal-agent-api -n 120 --no-pager || true
    sudo journalctl -u universal-agent-webui -n 80 --no-pager || true
  fi
  exit 1
fi

# Clear the deployment-window flag once services have restarted
echo "--> Clearing deployment-window flag..."
cleanup_deployment_window
trap - EXIT

echo "Deployment to Production complete!"

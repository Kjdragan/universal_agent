#!/usr/bin/env bash
set -euo pipefail

# Install a user-level systemd timer that runs the deslop auto-remediation
# dispatcher (Theme 2 v1a):
#   universal_agent.scripts.deslop_remediation_dispatch --mode observe
#
# Mirrors install_skill_gap_finder_timer.sh / install_backlog_triage_timer.sh:
# writes a .service + .timer into ~/.config/systemd/user/, daemon-reload,
# enable --now, with an --uninstall path. Secrets are injected via `infisical
# run` (no plaintext on disk); the service runs the repo .venv python with
# PYTHONPATH set to the repo src.
#
# IMPORTANT — runs in PROD (the VPS), not the desktop. The dispatcher queues a
# Cody mission into activity_state.db, which the gateway's idle-dispatch worker
# consumes. Install this where that worker runs, or dispatched missions orphan.
#
# Mode defaults to `observe` (always draft PR + email Kevin — never auto-merge).
# Only flip to `auto` (safe class auto-merges) AFTER watching real observe-mode
# runs produce good fixes. The HARD never-auto list is enforced in either mode.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SERVICE_NAME="ua-deslop-remediation"
MODE="${UA_DESLOP_AUTOREMEDIATE_MODE:-observe}"
# Every 6 hours at :15 (idempotent — claimed issues are skipped on re-runs).
ONCALENDAR="${UA_DESLOP_ONCALENDAR:-*-*-* 0/6:15:00}"
TIMEZONE="${UA_DESLOP_TIMEZONE:-America/Chicago}"
RANDOMIZED_DELAY_SEC="${UA_DESLOP_RANDOMIZED_DELAY_SEC:-300}"

UNINSTALL="false"

print_usage() {
  cat <<'USAGE'
Usage:
  scripts/install_deslop_remediation_timer.sh [options]

Purpose:
  Install a user-level systemd timer that runs:
  python -m universal_agent.scripts.deslop_remediation_dispatch --mode observe
  (inside `infisical run --env=local` so creds are injected).

Options:
  --service-name <name>   systemd unit base name (default: ua-deslop-remediation)
  --mode <observe|auto>   observe (default): always draft + email. auto: safe
                          class auto-merges (never-auto list still draft-only).
  --oncalendar <expr>     systemd OnCalendar (default: "*-*-* 0/6:15:00")
  --timezone <tz>         IANA timezone for OnCalendar (default: America/Chicago)
  --randomized-delay <s>  RandomizedDelaySec (default: 300)
  --uninstall             Remove timer/service and exit.
  --help                  Print help.

Requires:
  - INFISICAL_PROJECT_ID env (the Infisical project to pull creds from).
  - infisical CLI on PATH, and a repo .venv at ${REPO_ROOT}/.venv.
USAGE
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

assert_nonnegative_integer() {
  local value="$1"
  local label="$2"
  if [[ ! "${value}" =~ ^[0-9]+$ ]]; then
    echo "${label} must be a non-negative integer. Got: ${value}" >&2
    exit 1
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --service-name)
      SERVICE_NAME="${2:-}"
      shift 2
      ;;
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --oncalendar)
      ONCALENDAR="${2:-}"
      shift 2
      ;;
    --timezone)
      TIMEZONE="${2:-}"
      shift 2
      ;;
    --randomized-delay)
      RANDOMIZED_DELAY_SEC="${2:-}"
      shift 2
      ;;
    --uninstall)
      UNINSTALL="true"
      shift
      ;;
    --help|-h)
      print_usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      print_usage >&2
      exit 1
      ;;
  esac
done

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not found. Install manually with cron or run the module in a loop." >&2
  exit 1
fi

SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
SERVICE_FILE="${SYSTEMD_USER_DIR}/${SERVICE_NAME}.service"
TIMER_FILE="${SYSTEMD_USER_DIR}/${SERVICE_NAME}.timer"

mkdir -p "${SYSTEMD_USER_DIR}"

if [[ "${UNINSTALL}" == "true" ]]; then
  systemctl --user disable --now "${SERVICE_NAME}.timer" >/dev/null 2>&1 || true
  rm -f "${SERVICE_FILE}" "${TIMER_FILE}"
  systemctl --user daemon-reload
  log "Removed ${SERVICE_NAME}.service and ${SERVICE_NAME}.timer"
  exit 0
fi

if [[ "${MODE}" != "observe" && "${MODE}" != "auto" ]]; then
  echo "--mode must be 'observe' or 'auto'. Got: ${MODE}" >&2
  exit 1
fi
assert_nonnegative_integer "${RANDOMIZED_DELAY_SEC}" "--randomized-delay"

if [[ -z "${INFISICAL_PROJECT_ID:-}" ]]; then
  echo "INFISICAL_PROJECT_ID env is required (the Infisical project for creds)." >&2
  echo "Export it before running, e.g.: INFISICAL_PROJECT_ID=<id> scripts/install_deslop_remediation_timer.sh" >&2
  exit 1
fi

VENV_PYTHON="${REPO_ROOT}/.venv/bin/python"
if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "::warning:: repo venv python not found at ${VENV_PYTHON} — the timer will fail until 'uv sync' creates it." >&2
fi

# ExecStart runs the dispatcher directly. It calls initialize_runtime_secrets()
# itself, so it resolves Infisical creds from the EnvironmentFile (.env) the VPS
# way — NO `infisical run` wrapper. (The wrapper was copied from the desktop
# installers; on the VPS `infisical run` has no login session and tries an
# interactive login, which fails. Verified: the wrapper-form unit exits 1 at
# auth; this direct form resolves creds via initialize_runtime_secrets().)
exec_start=(
  env "PYTHONPATH=${REPO_ROOT}/src" "${VENV_PYTHON}"
  -m universal_agent.scripts.deslop_remediation_dispatch
  --mode "${MODE}"
)

exec_start_line="$(printf '%q ' "${exec_start[@]}")"

cat > "${SERVICE_FILE}" <<SERVICE
[Unit]
Description=Universal Agent deslop auto-remediation dispatcher (triage deslop-findings -> Cody draft fix)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
EnvironmentFile=-${REPO_ROOT}/.env
Environment=UA_DEPLOYMENT_PROFILE=vps
Environment=UA_INFISICAL_ENABLED=1
Environment=UA_DESLOP_AUTOREMEDIATE_MODE=${MODE}
WorkingDirectory=${REPO_ROOT}
ExecStart=${exec_start_line}
SERVICE

cat > "${TIMER_FILE}" <<TIMER
[Unit]
Description=Periodic trigger for the Universal Agent deslop auto-remediation dispatcher (${SERVICE_NAME})

[Timer]
OnCalendar=${ONCALENDAR} ${TIMEZONE}
Persistent=true
RandomizedDelaySec=${RANDOMIZED_DELAY_SEC}
Unit=${SERVICE_NAME}.service

[Install]
WantedBy=timers.target
TIMER

systemctl --user daemon-reload
systemctl --user enable --now "${SERVICE_NAME}.timer"

log "Installed and started ${SERVICE_NAME}.timer"
log "Schedule: OnCalendar=${ONCALENDAR} ${TIMEZONE}, Persistent=true, RandomizedDelaySec=${RANDOMIZED_DELAY_SEC}"
log "Mode: ${MODE} (observe = always draft PR + email Kevin; auto = safe class auto-merges)"
log ""
log "HINT: user timers only fire while you're logged in unless lingering is enabled."
log "  Enable persistence across logout with: loginctl enable-linger \"\$(whoami)\""
log "Inspect schedule with: systemctl --user list-timers ${SERVICE_NAME}.timer"
log "Inspect recent runs with: journalctl --user -u ${SERVICE_NAME}.service -n 80 --no-pager"
log "Dry-run once now (no side effects), using the same env the service uses:"
log "  systemd-run --user --pipe --quiet -p EnvironmentFile=-${REPO_ROOT}/.env -E UA_DEPLOYMENT_PROFILE=vps -E PYTHONPATH=${REPO_ROOT}/src ${VENV_PYTHON} -m universal_agent.scripts.deslop_remediation_dispatch --dry-run"

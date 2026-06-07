#!/usr/bin/env bash
set -euo pipefail

# Install a WEEKLY user-level systemd timer that runs the skill-gap finder:
#   universal_agent.skill_gap_finder --window-days 7 --open-issue
#
# Mirrors install_remote_workspace_sync_timer.sh: writes a .service + .timer
# into ~/.config/systemd/user/, daemon-reload, enable --now, --uninstall path.
#
# Secrets (ZAI/Anthropic via Infisical) are injected via `infisical run`, the
# same form dev_up.sh uses — no plaintext on disk. The service runs the repo
# .venv python with PYTHONPATH set to the repo src.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SERVICE_NAME="ua-skill-gap-finder"
WINDOW_DAYS="${UA_SKILL_GAP_WINDOW_DAYS:-7}"
# Weekly: Monday 09:00 America/Chicago.
ONCALENDAR="${UA_SKILL_GAP_ONCALENDAR:-Mon *-*-* 09:00:00}"
TIMEZONE="${UA_SKILL_GAP_TIMEZONE:-America/Chicago}"
RANDOMIZED_DELAY_SEC="${UA_SKILL_GAP_RANDOMIZED_DELAY_SEC:-300}"

UNINSTALL="false"

print_usage() {
  cat <<'USAGE'
Usage:
  scripts/install_skill_gap_finder_timer.sh [options]

Purpose:
  Install a WEEKLY user-level systemd timer that runs:
  python -m universal_agent.skill_gap_finder --window-days 7 --open-issue
  (inside `infisical run --env=local` so ZAI/Anthropic creds are injected).

Options:
  --service-name <name>   systemd unit base name (default: ua-skill-gap-finder)
  --window-days <N>       transcript window to mine (default: 7)
  --oncalendar <expr>     systemd OnCalendar (default: "Mon *-*-* 09:00:00")
  --timezone <tz>         IANA timezone for OnCalendar (default: America/Chicago)
  --randomized-delay <s>  RandomizedDelaySec (default: 300)
  --uninstall             Remove timer/service and exit.
  --help                  Print help.

Requires:
  - INFISICAL_PROJECT_ID env (the Infisical project to pull ZAI creds from).
  - infisical CLI on PATH, and a repo .venv at ${REPO_ROOT}/.venv.
USAGE
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

assert_positive_integer() {
  local value="$1"
  local label="$2"
  if [[ ! "${value}" =~ ^[0-9]+$ ]] || [[ "${value}" == "0" ]]; then
    echo "${label} must be a positive integer. Got: ${value}" >&2
    exit 1
  fi
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
    --window-days)
      WINDOW_DAYS="${2:-}"
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

assert_positive_integer "${WINDOW_DAYS}" "--window-days"
assert_nonnegative_integer "${RANDOMIZED_DELAY_SEC}" "--randomized-delay"

if [[ -z "${INFISICAL_PROJECT_ID:-}" ]]; then
  echo "INFISICAL_PROJECT_ID env is required (the Infisical project for ZAI creds)." >&2
  echo "Export it before running, e.g.: INFISICAL_PROJECT_ID=<id> scripts/install_skill_gap_finder_timer.sh" >&2
  exit 1
fi

VENV_PYTHON="${REPO_ROOT}/.venv/bin/python"
if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "::warning:: repo venv python not found at ${VENV_PYTHON} — the timer will fail until 'uv sync' creates it." >&2
fi

# ExecStart: infisical run --env=local form, exactly like dev_up.sh, then
# env PYTHONPATH=<src> <venv python> -m universal_agent.skill_gap_finder ...
exec_start=(
  infisical run --env=local --projectId "${INFISICAL_PROJECT_ID}" --
  env "PYTHONPATH=${REPO_ROOT}/src" "${VENV_PYTHON}"
  -m universal_agent.skill_gap_finder
  --window-days "${WINDOW_DAYS}"
  --open-issue
)

exec_start_line="$(printf '%q ' "${exec_start[@]}")"

cat > "${SERVICE_FILE}" <<SERVICE
[Unit]
Description=Weekly Universal Agent skill-gap finder (mine transcripts -> ranked skill candidates)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=INFISICAL_PROJECT_ID=${INFISICAL_PROJECT_ID}
WorkingDirectory=${REPO_ROOT}
ExecStart=${exec_start_line}
SERVICE

cat > "${TIMER_FILE}" <<TIMER
[Unit]
Description=Weekly trigger for the Universal Agent skill-gap finder (${SERVICE_NAME})

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
log "Schedule: OnCalendar=${ONCALENDAR} ${TIMEZONE} (weekly), Persistent=true, RandomizedDelaySec=${RANDOMIZED_DELAY_SEC}"
log "Window: --window-days ${WINDOW_DAYS}; opens one deduped GH issue labeled skill-gap"
log ""
log "HINT: user timers only fire while you're logged in unless lingering is enabled."
log "  Enable persistence across logout with: loginctl enable-linger \"\$(whoami)\""
log "Inspect schedule with: systemctl --user list-timers ${SERVICE_NAME}.timer"
log "Inspect recent runs with: journalctl --user -u ${SERVICE_NAME}.service -n 80 --no-pager"

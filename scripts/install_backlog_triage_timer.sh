#!/usr/bin/env bash
set -euo pipefail

# Install a DAILY user-level systemd timer that runs the backlog triage:
#   universal_agent.backlog_triage --send --seed-reminders --to <recipient>
#
# The daily re-send is the backbone ("noisy until you close the issue"); the
# --seed-reminders flag seeds ONE reminder row so the companion
# cron_artifact_reminders sweep fires the intra-day +4h/+72h nudges (hybrid).
# Mirrors install_skill_gap_finder_timer.sh. Secrets via `infisical run`.
#
# Pair with: scripts/install_artifact_reminders_sweep_timer.sh (the +4h/+72h sweep).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SERVICE_NAME="ua-backlog-triage"
RECIPIENT="${UA_TRIAGE_RECIPIENT:-kevinjdragan@gmail.com}"
ONCALENDAR="${UA_TRIAGE_ONCALENDAR:-*-*-* 08:30:00}"
TIMEZONE="${UA_TRIAGE_TIMEZONE:-America/Chicago}"
RANDOMIZED_DELAY_SEC="${UA_TRIAGE_RANDOMIZED_DELAY_SEC:-300}"
UNINSTALL="false"

print_usage() {
  cat <<'USAGE'
Usage: scripts/install_backlog_triage_timer.sh [options]
  Install a DAILY user systemd timer running:
    python -m universal_agent.backlog_triage --send --seed-reminders --to <recipient>
Options:
  --recipient <email>     digest recipient (default: kevinjdragan@gmail.com)
  --oncalendar <expr>     systemd OnCalendar (default: "*-*-* 08:30:00" = daily 8:30am)
  --timezone <tz>         IANA tz (default: America/Chicago)
  --randomized-delay <s>  RandomizedDelaySec (default: 300)
  --uninstall             Remove timer/service and exit.
  --help
Requires: INFISICAL_PROJECT_ID env; infisical CLI; repo .venv at ${REPO_ROOT}/.venv.
USAGE
}

log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --recipient) RECIPIENT="${2:-}"; shift 2 ;;
    --oncalendar) ONCALENDAR="${2:-}"; shift 2 ;;
    --timezone) TIMEZONE="${2:-}"; shift 2 ;;
    --randomized-delay) RANDOMIZED_DELAY_SEC="${2:-}"; shift 2 ;;
    --service-name) SERVICE_NAME="${2:-}"; shift 2 ;;
    --uninstall) UNINSTALL="true"; shift ;;
    --help|-h) print_usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; print_usage >&2; exit 1 ;;
  esac
done

command -v systemctl >/dev/null 2>&1 || { echo "systemctl not found." >&2; exit 1; }

SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
SERVICE_FILE="${SYSTEMD_USER_DIR}/${SERVICE_NAME}.service"
TIMER_FILE="${SYSTEMD_USER_DIR}/${SERVICE_NAME}.timer"
mkdir -p "${SYSTEMD_USER_DIR}"

if [[ "${UNINSTALL}" == "true" ]]; then
  systemctl --user disable --now "${SERVICE_NAME}.timer" >/dev/null 2>&1 || true
  rm -f "${SERVICE_FILE}" "${TIMER_FILE}"
  systemctl --user daemon-reload
  log "Removed ${SERVICE_NAME}.{service,timer}"
  exit 0
fi

if [[ ! "${RANDOMIZED_DELAY_SEC}" =~ ^[0-9]+$ ]]; then
  echo "--randomized-delay must be a non-negative integer" >&2; exit 1
fi
if [[ -z "${INFISICAL_PROJECT_ID:-}" ]]; then
  echo "INFISICAL_PROJECT_ID env is required (the Infisical project for ZAI/AgentMail creds)." >&2
  echo "e.g.: INFISICAL_PROJECT_ID=<id> scripts/install_backlog_triage_timer.sh" >&2
  exit 1
fi

VENV_PYTHON="${REPO_ROOT}/.venv/bin/python"
[[ -x "${VENV_PYTHON}" ]] || echo "::warning:: repo venv python not found at ${VENV_PYTHON} (run 'uv sync')." >&2

exec_start=(
  infisical run --env=local --projectId "${INFISICAL_PROJECT_ID}" --
  env "PYTHONPATH=${REPO_ROOT}/src" "${VENV_PYTHON}"
  -m universal_agent.backlog_triage
  --send --seed-reminders --to "${RECIPIENT}"
)
exec_start_line="$(printf '%q ' "${exec_start[@]}")"

cat > "${SERVICE_FILE}" <<SERVICE
[Unit]
Description=Daily Universal Agent backlog triage (assess open backlog -> Simone email + reminder seed)
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
Description=Daily trigger for the Universal Agent backlog triage (${SERVICE_NAME})

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
log "Installed ${SERVICE_NAME}.timer — daily OnCalendar=${ONCALENDAR} ${TIMEZONE}, recipient=${RECIPIENT}"
log "Pair with the +4h/+72h sweep: scripts/install_artifact_reminders_sweep_timer.sh"
log "HINT: user timers fire only while logged in unless: loginctl enable-linger \"\$(whoami)\""
log "Inspect: journalctl --user -u ${SERVICE_NAME}.service -n 80 --no-pager"

#!/usr/bin/env bash
set -euo pipefail

# Install a DESKTOP user-level systemd timer that runs the proactive-artifact
# reminder sweep every 30 min, so the rows the backlog triage seeds get their
# intra-day +4h / +72h nudges (the "hybrid" reminder layer) on this machine.
#
#   universal_agent.scripts.cron_artifact_reminders_sweep
#
# On the gateway this sweep runs as an in-process cron against the gateway DB;
# on the desktop it must run as a user timer against the desktop activity DB.
# We override the recipient to the gmail (the sweep's default UA_*_EMAIL chain
# resolves to a bounced outlook address); the sweep does NOT call
# initialize_runtime_secrets, so the `env UA_PROACTIVE_REVIEW_EMAIL=...` after
# `infisical run` wins.
#
# Pair with: scripts/install_backlog_triage_timer.sh (seeds the reminder rows).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

SERVICE_NAME="ua-artifact-reminders-sweep"
RECIPIENT="${UA_TRIAGE_RECIPIENT:-kevinjdragan@gmail.com}"
ONCALENDAR="${UA_SWEEP_ONCALENDAR:-*-*-* *:00/30:00}"
TIMEZONE="${UA_SWEEP_TIMEZONE:-America/Chicago}"
UNINSTALL="false"

print_usage() {
  cat <<'USAGE'
Usage: scripts/install_artifact_reminders_sweep_timer.sh [options]
  Install a 30-min user systemd timer running the proactive-artifact reminder
  sweep (fires +4h/+72h nudges for rows seeded by the backlog triage).
Options:
  --recipient <email>   reminder recipient (default: kevinjdragan@gmail.com)
  --oncalendar <expr>   systemd OnCalendar (default: "*-*-* *:00/30:00" = every 30m)
  --timezone <tz>       IANA tz (default: America/Chicago)
  --uninstall           Remove timer/service and exit.
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

if [[ -z "${INFISICAL_PROJECT_ID:-}" ]]; then
  echo "INFISICAL_PROJECT_ID env is required." >&2
  echo "e.g.: INFISICAL_PROJECT_ID=<id> scripts/install_artifact_reminders_sweep_timer.sh" >&2
  exit 1
fi

VENV_PYTHON="${REPO_ROOT}/.venv/bin/python"
[[ -x "${VENV_PYTHON}" ]] || echo "::warning:: repo venv python not found at ${VENV_PYTHON} (run 'uv sync')." >&2

# `env UA_PROACTIVE_REVIEW_EMAIL=...` AFTER `infisical run` overrides the vault's
# (bounced) recipient — the sweep reads os.getenv and never re-fetches secrets.
exec_start=(
  infisical run --env=local --projectId "${INFISICAL_PROJECT_ID}" --
  env "PYTHONPATH=${REPO_ROOT}/src" "UA_PROACTIVE_REVIEW_EMAIL=${RECIPIENT}" "${VENV_PYTHON}"
  -m universal_agent.scripts.cron_artifact_reminders_sweep
)
exec_start_line="$(printf '%q ' "${exec_start[@]}")"

cat > "${SERVICE_FILE}" <<SERVICE
[Unit]
Description=Universal Agent proactive-artifact reminder sweep (desktop; +4h/+72h nudges)
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
Description=30-min trigger for the desktop reminder sweep (${SERVICE_NAME})

[Timer]
OnCalendar=${ONCALENDAR} ${TIMEZONE}
Persistent=true
RandomizedDelaySec=60
Unit=${SERVICE_NAME}.service

[Install]
WantedBy=timers.target
TIMER

systemctl --user daemon-reload
systemctl --user enable --now "${SERVICE_NAME}.timer"
log "Installed ${SERVICE_NAME}.timer — every 30 min (${TIMEZONE}), recipient=${RECIPIENT}"
log "The sweep self-gates reminders to 6am-10pm and stops each row after the Day-3 nudge."
log "HINT: enable-linger so it runs while logged out: loginctl enable-linger \"\$(whoami)\""
log "Inspect: journalctl --user -u ${SERVICE_NAME}.service -n 80 --no-pager"

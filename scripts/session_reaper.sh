#!/usr/bin/env bash
set -Eeuo pipefail

# ---------------------------------------------------------------------------
# session_reaper.sh
#
# Scan AGENT_RUN_WORKSPACES/ for stale cron_* and session_* directories.
#   - Idle > threshold with active claude processes  => kill processes
#   - Idle > 24 h with no active processes           => archive directory
#   - Otherwise                                       => skip
#
# Usage: session_reaper.sh [IDLE_THRESHOLD_HOURS]
# ---------------------------------------------------------------------------

IDLE_THRESHOLD_HOURS="${1:-4}"
ARCHIVE_IDLE_HOURS=24

REPO_ROOT="/home/kjdragan/lrepos/universal_agent"
WORKSPACES="${REPO_ROOT}/AGENT_RUN_WORKSPACES"
ARCHIVE_DIR="${REPO_ROOT}/AGENT_RUN_WORKSPACES_ARCHIVE"
LOG_FILE="${REPO_ROOT}/logs/session_reaper.log"

# Counters
KILLED=0
ARCHIVED=0
SKIPPED=0
ERRORS=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() {
  local ts
  ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  local msg="[$ts] $*"
  echo "$msg" | tee -a "$LOG_FILE"
}

validate_uint() {
  [[ "$1" =~ ^[0-9]+$ ]] && [[ "$1" -gt 0 ]]
}

# Return the epoch timestamp of the most recently modified file found
# recursively up to maxdepth 3 inside the given directory.
last_activity_epoch() {
  local dir="$1"
  # find returns nothing for empty dirs; default to the directory mtime.
  local latest
  latest="$(find "$dir" -maxdepth 3 -type f -printf '%T@\n' 2>/dev/null \
              | sort -rn \
              | head -1)" || true
  if [[ -z "$latest" ]]; then
    latest="$(stat -c '%Y' "$dir")"
  fi
  # Truncate to integer seconds
  printf '%.0f' "$latest"
}

# Return the number of running processes whose cmdline contains the session
# directory name (a reasonable proxy for "claude processes tied to this
# session").
active_process_count() {
  local session_name="$1"
  local count
  count="$(pgrep -fc "$session_name" 2>/dev/null)" || true
  if [[ -z "$count" || "$count" -eq 0 ]]; then
    # Broader check: any claude / node / python process whose cwd is inside
    # the session directory.
    count="$(pgrep -afclaude 2>/dev/null \
              | grep -c "$session_name" || true)" || true
  fi
  printf '%s' "$count"
}

kill_session_processes() {
  local session_name="$1"
  local pids
  pids="$(pgrep -f "$session_name" 2>/dev/null)" || true
  if [[ -n "$pids" ]]; then
    echo "$pids" | xargs kill -TERM 2>/dev/null || true
    # Give a brief grace period, then force-kill survivors.
    sleep 2
    local survivors
    survivors="$(pgrep -f "$session_name" 2>/dev/null)" || true
    if [[ -n "$survivors" ]]; then
      echo "$survivors" | xargs kill -KILL 2>/dev/null || true
    fi
  fi
}

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

if ! validate_uint "$IDLE_THRESHOLD_HOURS"; then
  echo "ERROR: IDLE_THRESHOLD_HOURS must be a positive integer, got '${IDLE_THRESHOLD_HOURS}'" >&2
  exit 2
fi

mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$ARCHIVE_DIR"

NOW_EPOCH="$(date +%s)"
IDLE_THRESHOLD_SECS=$((IDLE_THRESHOLD_HOURS * 3600))
ARCHIVE_THRESHOLD_SECS=$((ARCHIVE_IDLE_HOURS * 3600))

log "=== session_reaper started (idle_threshold=${IDLE_THRESHOLD_HOURS}h, archive_threshold=${ARCHIVE_IDLE_HOURS}h) ==="

# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

for session_dir in "${WORKSPACES}"/cron_* "${WORKSPACES}"/session_*; do
  # Glob may yield a literal string when nothing matches.
  [[ -d "$session_dir" ]] || continue

  session_name="$(basename "$session_dir")"

  # Determine idle time.
  last_activity="$(last_activity_epoch "$session_dir")"
  idle_secs=$((NOW_EPOCH - last_activity))
  idle_hours=$((idle_secs / 3600))

  if [[ "$idle_secs" -lt "$IDLE_THRESHOLD_SECS" ]]; then
    log "SKIP  ${session_name}  (idle ${idle_hours}h < ${IDLE_THRESHOLD_HOURS}h threshold)"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  # Session is idle beyond threshold -- check for active processes.
  proc_count="$(active_process_count "$session_name")"

  if [[ "$proc_count" -gt 0 ]]; then
    log "KILL  ${session_name}  (idle ${idle_hours}h, ${proc_count} active process(es))"
    if kill_session_processes "$session_name"; then
      KILLED=$((KILLED + 1))
    else
      log "ERROR failed to kill processes for ${session_name}"
      ERRORS=$((ERRORS + 1))
    fi
    continue
  fi

  # No active processes -- check if old enough to archive.
  if [[ "$idle_secs" -ge "$ARCHIVE_THRESHOLD_SECS" ]]; then
    log "ARCHIVE  ${session_name}  (idle ${idle_hours}h >= ${ARCHIVE_IDLE_HOURS}h, no active processes)"
    if mv "$session_dir" "${ARCHIVE_DIR}/"; then
      ARCHIVED=$((ARCHIVED + 1))
    else
      log "ERROR failed to archive ${session_name}"
      ERRORS=$((ERRORS + 1))
    fi
  else
    log "SKIP  ${session_name}  (idle ${idle_hours}h, no processes but < ${ARCHIVE_IDLE_HOURS}h archive threshold)"
    SKIPPED=$((SKIPPED + 1))
  fi
done

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

log "=== session_reaper finished ==="

# Print summary JSON to stdout (not duplicated into log file).
cat <<EOF
{"killed": ${KILLED}, "archived": ${ARCHIVED}, "skipped": ${SKIPPED}, "errors": ${ERRORS}, "max_idle_hours": ${IDLE_THRESHOLD_HOURS}}
EOF

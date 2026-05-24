#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./csi_dev_env.sh
source "$SCRIPT_DIR/csi_dev_env.sh" >/dev/null

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 <command> [args...]"
  exit 2
fi

# On the production VPS this script's parent systemd units load bootstrap
# Infisical creds from /opt/universal_agent/.env. When those creds are
# present, log in and run the wrapped command through `infisical run` so
# secrets like UA_YOUTUBE_INGEST_TOKEN are injected as env vars into the
# child process — never landing in the wrapper's own env or on disk.
#
# When the creds are absent (local dev, CI), pass through unchanged so
# scripts can resolve secrets from local .env files or their own
# fallback paths.
if [ -n "${INFISICAL_CLIENT_ID:-}" ] \
   && [ -n "${INFISICAL_CLIENT_SECRET:-}" ] \
   && [ -n "${INFISICAL_PROJECT_ID:-}" ]; then
  INFISICAL_ENV="${INFISICAL_ENVIRONMENT:-production}"
  # `--plain --silent` gives just the token on stdout; the login itself never
  # prints the value of the bootstrap secret.
  if ! TOK=$(infisical login --method=universal-auth \
              --client-id="$INFISICAL_CLIENT_ID" \
              --client-secret="$INFISICAL_CLIENT_SECRET" \
              --plain --silent 2>/dev/null); then
    echo "csi_run.sh: infisical login failed; falling back to pass-through" >&2
    exec "$@"
  fi
  export INFISICAL_TOKEN="$TOK"
  exec infisical run \
       --projectId="$INFISICAL_PROJECT_ID" \
       --env="$INFISICAL_ENV" \
       --silent \
       -- "$@"
fi

exec "$@"

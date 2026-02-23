#!/usr/bin/env bash
set -euo pipefail

CSI_ENV="${CSI_ENV:-/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env}"
ROOT_ENV="${ROOT_ENV:-/opt/universal_agent/.env}"
LOCAL_FALLBACK_DEFAULT="http://127.0.0.1:8002/api/v1/youtube/ingest"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <primary_endpoint_url> [secondary_endpoint_url]"
  echo "Example: $0 http://100.95.187.38:8002/api/v1/youtube/ingest"
  exit 2
fi

PRIMARY="$1"
SECONDARY="${2:-$LOCAL_FALLBACK_DEFAULT}"

if [[ "$PRIMARY" != http://* && "$PRIMARY" != https://* ]]; then
  echo "ERROR: primary endpoint must start with http:// or https://"
  exit 2
fi
if [[ "$SECONDARY" != http://* && "$SECONDARY" != https://* ]]; then
  echo "ERROR: secondary endpoint must start with http:// or https://"
  exit 2
fi

mkdir -p "$(dirname "$CSI_ENV")"
if [[ ! -f "$CSI_ENV" ]]; then
  touch "$CSI_ENV"
fi

upsert_env() {
  local file="$1" key="$2" val="$3"
  if grep -q "^${key}=" "$file"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$file"
  else
    echo "${key}=${val}" >>"$file"
  fi
}

ENDPOINTS="${PRIMARY},${SECONDARY}"
upsert_env "$CSI_ENV" "CSI_RSS_ANALYSIS_TRANSCRIPT_ENDPOINTS" "$ENDPOINTS"
upsert_env "$CSI_ENV" "CSI_RSS_ANALYSIS_TRANSCRIPT_ENDPOINT" "$PRIMARY"
upsert_env "$ROOT_ENV" "UA_HOOKS_YOUTUBE_INGEST_URLS" "$ENDPOINTS"
upsert_env "$ROOT_ENV" "UA_HOOKS_YOUTUBE_INGEST_URL" "$PRIMARY"

chmod 600 "$CSI_ENV"
# Keep root env readable by the `ua` service account used by gateway/workers.
if id -u ua >/dev/null 2>&1; then
  chown root:ua "$ROOT_ENV"
  chmod 640 "$ROOT_ENV"
else
  chmod 600 "$ROOT_ENV"
fi
systemctl restart csi-ingester
systemctl restart universal-agent-gateway
echo "CSI_TRANSCRIPT_ENDPOINTS_SET=${ENDPOINTS}"
echo "CSI_SERVICE_STATUS=$(systemctl is-active csi-ingester)"
echo "GATEWAY_SERVICE_STATUS=$(systemctl is-active universal-agent-gateway)"

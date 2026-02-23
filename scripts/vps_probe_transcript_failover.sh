#!/usr/bin/env bash
set -euo pipefail

# Runs on VPS via: scripts/vpsctl.sh run-file scripts/vps_probe_transcript_failover.sh

cd /opt/universal_agent

CSI_ENV="CSI_Ingester/development/deployment/systemd/csi-ingester.env"

pick_video_id() {
  python3 - <<'PY'
import sqlite3
conn=sqlite3.connect('/opt/universal_agent/CSI_Ingester/development/var/csi.db')
row=conn.execute(
    "select video_id from rss_event_analysis where transcript_status='failed' and video_id<>'' order by id desc limit 1"
).fetchone()
print((row[0] if row else 'dQw4w9WgXcQ').strip())
conn.close()
PY
}

VIDEO_ID="${1:-$(pick_video_id)}"
TOKEN="$(awk -F= '/^UA_YOUTUBE_INGEST_TOKEN=/{print substr($0,index($0,$2)); exit}' .env || true)"
if [ -z "$TOKEN" ]; then
  TOKEN="$(awk -F= '/^UA_INTERNAL_API_TOKEN=/{print substr($0,index($0,$2)); exit}' .env || true)"
fi
if [ -z "$TOKEN" ]; then
  echo "ERROR: missing UA_YOUTUBE_INGEST_TOKEN / UA_INTERNAL_API_TOKEN in /opt/universal_agent/.env"
  exit 1
fi

ENDPOINTS="$(awk -F= '/^CSI_RSS_ANALYSIS_TRANSCRIPT_ENDPOINTS=/{print substr($0,index($0,$2)); exit}' "$CSI_ENV" || true)"
if [ -z "$ENDPOINTS" ]; then
  SINGLE="$(awk -F= '/^CSI_RSS_ANALYSIS_TRANSCRIPT_ENDPOINT=/{print substr($0,index($0,$2)); exit}' "$CSI_ENV" || true)"
  ENDPOINTS="${SINGLE}"
fi

echo "VIDEO_ID=${VIDEO_ID}"
echo "ENDPOINTS=${ENDPOINTS}"
echo

IFS=',' read -r -a EP_ARR <<< "$ENDPOINTS"
for ep in "${EP_ARR[@]}"; do
  ep="$(echo "$ep" | xargs)"
  [ -n "$ep" ] || continue
  echo "=== endpoint: $ep ==="
  curl -sS -m 15 -o /tmp/transcript_probe.json -w "HTTP=%{http_code}\n" \
    -X POST "$ep" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    --data-binary "{\"video_id\":\"${VIDEO_ID}\",\"timeout_seconds\":25,\"max_chars\":12000,\"min_chars\":20}" || true
  if command -v jq >/dev/null 2>&1; then
    jq '{ok,status,error,failure_class,source,transcript_chars,detail}' /tmp/transcript_probe.json 2>/dev/null || head -c 300 /tmp/transcript_probe.json; echo
  else
    head -c 300 /tmp/transcript_probe.json; echo
  fi
  echo
done

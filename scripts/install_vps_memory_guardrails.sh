#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

GATEWAY_SERVICE="${UA_GATEWAY_SERVICE_NAME:-universal-agent-gateway.service}"
CSI_SERVICE="${UA_CSI_SERVICE_NAME:-csi-ingester.service}"

GATEWAY_MEMORY_HIGH="${UA_GATEWAY_MEMORY_HIGH:-85%}"
GATEWAY_MEMORY_MAX="${UA_GATEWAY_MEMORY_MAX:-95%}"
GATEWAY_MEMORY_SWAP_MAX="${UA_GATEWAY_MEMORY_SWAP_MAX:-80%}"
GATEWAY_TASKS_MAX="${UA_GATEWAY_TASKS_MAX:-4096}"

CSI_MEMORY_HIGH="${UA_CSI_MEMORY_HIGH:-900M}"
CSI_MEMORY_MAX="${UA_CSI_MEMORY_MAX:-1400M}"
CSI_MEMORY_SWAP_MAX="${UA_CSI_MEMORY_SWAP_MAX:-1024M}"
CSI_TASKS_MAX="${UA_CSI_TASKS_MAX:-2048}"

write_dropin() {
  local service="$1"
  local memory_high="$2"
  local memory_max="$3"
  local memory_swap_max="$4"
  local tasks_max="$5"
  local dir="/etc/systemd/system/${service}.d"

  mkdir -p "$dir"
  cat >"${dir}/override.conf" <<EOF
[Service]
MemoryHigh=$memory_high
MemoryMax=$memory_max
MemorySwapMax=$memory_swap_max
TasksMax=$tasks_max
OOMPolicy=continue
EOF
}

write_dropin "$GATEWAY_SERVICE" "$GATEWAY_MEMORY_HIGH" "$GATEWAY_MEMORY_MAX" "$GATEWAY_MEMORY_SWAP_MAX" "$GATEWAY_TASKS_MAX"
write_dropin "$CSI_SERVICE" "$CSI_MEMORY_HIGH" "$CSI_MEMORY_MAX" "$CSI_MEMORY_SWAP_MAX" "$CSI_TASKS_MAX"

systemctl daemon-reload
systemctl restart "$GATEWAY_SERVICE" "$CSI_SERVICE"

echo "== Memory guardrails =="
systemctl show "$GATEWAY_SERVICE" -p MemoryHigh -p MemoryMax -p MemorySwapMax -p TasksMax -p OOMPolicy
systemctl show "$CSI_SERVICE" -p MemoryHigh -p MemoryMax -p MemorySwapMax -p TasksMax -p OOMPolicy

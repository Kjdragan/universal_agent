#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VPS_HOST="${UA_VPS_HOST:-root@srv1360701.taildcc090.ts.net}"
SSH_KEY="${UA_VPS_SSH_KEY:-$HOME/.ssh/id_ed25519}"
SSH_AUTH_MODE="${UA_SSH_AUTH_MODE:-keys}"
REMOTE_DIR="${UA_VPS_APP_DIR:-/opt/universal_agent}"
REMOTE_REDIS_DIR="${UA_REDIS_COMPOSE_DIR:-$REMOTE_DIR/corporation/infrastructure/redis}"
REDIS_PORT="${UA_REDIS_PORT:-6379}"
REDIS_ALLOWED_CIDRS="${UA_REDIS_ALLOWED_CIDRS:-}"

usage() {
  cat <<'EOF'
Install/refresh Redis delegation bus on VPS using the tracked compose assets.

Environment controls:
  UA_VPS_HOST                SSH target (default: root@srv1360701.taildcc090.ts.net)
  UA_SSH_AUTH_MODE           keys | tailscale_ssh (default: keys)
  UA_VPS_SSH_KEY             SSH private key path when using keys auth
  UA_VPS_APP_DIR             Remote app root (default: /opt/universal_agent)
  UA_REDIS_COMPOSE_DIR       Remote redis asset dir (default: $UA_VPS_APP_DIR/corporation/infrastructure/redis)
  UA_REDIS_PORT              Redis TCP port for UFW guidance (default: 6379)
  UA_REDIS_ALLOWED_CIDRS     Optional comma-separated CIDRs for ufw allow rules

Examples:
  scripts/install_vps_redis_bus.sh
  UA_REDIS_ALLOWED_CIDRS="100.64.0.0/10,198.51.100.40/32" scripts/install_vps_redis_bus.sh
EOF
}

ssh_base=(ssh -o StrictHostKeyChecking=no)
scp_base=(scp -o StrictHostKeyChecking=no)

case "$(printf '%s' "${SSH_AUTH_MODE}" | tr '[:upper:]' '[:lower:]')" in
  keys)
    if [[ -n "${SSH_KEY}" && ! -f "${SSH_KEY}" ]]; then
      echo "ERROR: SSH key does not exist: ${SSH_KEY}" >&2
      exit 1
    fi
    if [[ -n "${SSH_KEY}" ]]; then
      ssh_base+=(-i "${SSH_KEY}")
      scp_base+=(-i "${SSH_KEY}")
    fi
    ;;
  tailscale_ssh)
    ;;
  -h|--help|help)
    usage
    exit 0
    ;;
  *)
    echo "ERROR: UA_SSH_AUTH_MODE must be keys or tailscale_ssh. Got: ${SSH_AUTH_MODE}" >&2
    exit 1
    ;;
esac

if ! command -v ssh >/dev/null 2>&1; then
  echo "ERROR: ssh is required." >&2
  exit 1
fi
if ! command -v scp >/dev/null 2>&1; then
  echo "ERROR: scp is required." >&2
  exit 1
fi

LOCAL_REDIS_DIR="${REPO_ROOT}/corporation/infrastructure/redis"
LOCAL_COMPOSE="${LOCAL_REDIS_DIR}/docker-compose.yml"
LOCAL_CONF="${LOCAL_REDIS_DIR}/redis.conf"
if [[ ! -f "${LOCAL_COMPOSE}" || ! -f "${LOCAL_CONF}" ]]; then
  echo "ERROR: Missing local Redis assets under ${LOCAL_REDIS_DIR}" >&2
  exit 1
fi

echo "== Redis VPS install =="
echo "Host: ${VPS_HOST}"
echo "Remote app dir: ${REMOTE_DIR}"
echo "Remote redis dir: ${REMOTE_REDIS_DIR}"

echo "Checking SSH connectivity..."
if ! "${ssh_base[@]}" -q -o BatchMode=yes -o ConnectTimeout=10 "${VPS_HOST}" "echo ok" >/dev/null 2>&1; then
  echo "ERROR: Unable to connect to ${VPS_HOST}" >&2
  exit 1
fi

echo "Syncing Redis compose assets..."
"${ssh_base[@]}" "${VPS_HOST}" "mkdir -p '${REMOTE_REDIS_DIR}'"
"${scp_base[@]}" "${LOCAL_COMPOSE}" "${VPS_HOST}:${REMOTE_REDIS_DIR}/docker-compose.yml"
"${scp_base[@]}" "${LOCAL_CONF}" "${VPS_HOST}:${REMOTE_REDIS_DIR}/redis.conf"

remote_script=$(cat <<'REMOTE'
set -euo pipefail

REMOTE_DIR="${REMOTE_DIR:?missing REMOTE_DIR}"
REMOTE_REDIS_DIR="${REMOTE_REDIS_DIR:?missing REMOTE_REDIS_DIR}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_ALLOWED_CIDRS="${REDIS_ALLOWED_CIDRS:-}"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is required on the VPS." >&2
  exit 11
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: docker compose plugin is required on the VPS." >&2
  exit 12
fi

if [ ! -f "${REMOTE_DIR}/.env" ]; then
  echo "ERROR: missing ${REMOTE_DIR}/.env (needed for REDIS_PASSWORD injection)." >&2
  exit 13
fi

REDIS_PASSWORD="$(grep -E '^REDIS_PASSWORD=' "${REMOTE_DIR}/.env" | tail -n1 | cut -d= -f2- || true)"
if [ -z "${REDIS_PASSWORD}" ]; then
  echo "ERROR: REDIS_PASSWORD is missing in ${REMOTE_DIR}/.env." >&2
  exit 14
fi

cd "${REMOTE_REDIS_DIR}"
export REDIS_PASSWORD

echo "Bringing up Redis compose stack..."
docker compose up -d
docker compose ps

echo "Running Redis auth health check..."
docker compose exec -T redis redis-cli -a "${REDIS_PASSWORD}" ping >/tmp/ua_redis_ping.txt
if ! grep -q "PONG" /tmp/ua_redis_ping.txt; then
  echo "ERROR: Redis ping did not return PONG." >&2
  cat /tmp/ua_redis_ping.txt >&2 || true
  exit 15
fi
rm -f /tmp/ua_redis_ping.txt

if [ -n "${REDIS_ALLOWED_CIDRS}" ] && command -v ufw >/dev/null 2>&1; then
  IFS=',' read -r -a cidr_arr <<< "${REDIS_ALLOWED_CIDRS}"
  for cidr in "${cidr_arr[@]}"; do
    cidr_trim="$(echo "${cidr}" | xargs)"
    [ -n "${cidr_trim}" ] || continue
    ufw allow proto tcp from "${cidr_trim}" to any port "${REDIS_PORT}" >/dev/null || true
  done
fi

echo
echo "Redis deployment complete."
echo "Redis endpoint: $(hostname -I | awk '{print $1}'):${REDIS_PORT}"
echo "Use UFW to restrict access to trusted factory CIDRs only."
REMOTE
)

"${ssh_base[@]}" "${VPS_HOST}" \
  "REMOTE_DIR='${REMOTE_DIR}' REMOTE_REDIS_DIR='${REMOTE_REDIS_DIR}' REDIS_PORT='${REDIS_PORT}' REDIS_ALLOWED_CIDRS='${REDIS_ALLOWED_CIDRS}' bash -s" \
  <<<"${remote_script}"

echo
echo "Validation checklist:"
echo "1. On VPS: docker compose -f ${REMOTE_REDIS_DIR}/docker-compose.yml ps"
echo "2. On VPS: docker compose -f ${REMOTE_REDIS_DIR}/docker-compose.yml logs --tail=80 redis"
echo "3. Verify HQ .env has REDIS_PASSWORD and UA_DELEGATION_REDIS_ENABLED=1"
echo "4. Verify worker .env has matching REDIS_PASSWORD and UA_TUTORIAL_BOOTSTRAP_TRANSPORT=redis (or auto)"
echo "5. From a worker node, run one tutorial bootstrap and confirm queue -> running -> completed."

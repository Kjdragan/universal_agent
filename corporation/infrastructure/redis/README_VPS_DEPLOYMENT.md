# Redis Bus Deployment (VPS HQ)

This guide deploys the Redis Streams bus used for delegation missions:

- Mission stream: `ua:missions:delegation`
- Consumer group: `ua_workers`
- Dead-letter stream: `ua:missions:dlq`

## 1) Prerequisites

- VPS has Docker + `docker compose`.
- HQ runtime `.env` exists at `/opt/universal_agent/.env`.
- `REDIS_PASSWORD` is injected into that `.env` (from Infisical machine identity sync).
- Firewall policy is ready to restrict TCP `6379` to trusted factory CIDRs.

## 2) Deploy

From repo root:

```bash
bash scripts/install_vps_redis_bus.sh
```

Optional CIDR allowlist setup via UFW in the same run:

```bash
UA_REDIS_ALLOWED_CIDRS="100.64.0.0/10,198.51.100.40/32" \
bash scripts/install_vps_redis_bus.sh
```

## 3) Runtime env contract

Set on HQ + workers (via Infisical/secure env injection):

```env
REDIS_PASSWORD=...
UA_DELEGATION_REDIS_ENABLED=1
UA_REDIS_HOST=<hq-public-or-tailnet-host>
UA_REDIS_PORT=6379
UA_REDIS_DB=0
UA_DELEGATION_STREAM_NAME=ua:missions:delegation
UA_DELEGATION_CONSUMER_GROUP=ua_workers
UA_DELEGATION_DLQ_STREAM=ua:missions:dlq
```

Worker transport:

```env
UA_TUTORIAL_BOOTSTRAP_TRANSPORT=redis
```

(`auto` is also supported and picks Redis when `UA_DELEGATION_REDIS_ENABLED=1`.)

## 4) Validation checklist

1. Container health:
```bash
docker compose -f /opt/universal_agent/corporation/infrastructure/redis/docker-compose.yml ps
```
2. Redis auth check:
```bash
cd /opt/universal_agent/corporation/infrastructure/redis
REDIS_PASSWORD="$(grep -E '^REDIS_PASSWORD=' /opt/universal_agent/.env | tail -n1 | cut -d= -f2-)"
docker compose exec -T redis redis-cli -a "$REDIS_PASSWORD" ping
```
3. Gateway startup log includes Redis bus connected message.
4. Worker startup log includes Redis consumer connection + `worker_{FACTORY_ID}` name.
5. Tutorial bootstrap end-to-end:
   - dashboard enqueue
   - worker consumes from Redis
   - gateway job transitions `queued -> running -> completed`
   - completed job has `repo_dir` and open metadata.

## 5) Security notes

- Never hardcode `REDIS_PASSWORD` in compose files, scripts, or git-tracked docs.
- Keep Redis exposed only to trusted factory networks (Tailscale/private CIDR + UFW rules).
- Rotate `REDIS_PASSWORD` in Infisical and restart Redis + gateway/worker processes after rotation.

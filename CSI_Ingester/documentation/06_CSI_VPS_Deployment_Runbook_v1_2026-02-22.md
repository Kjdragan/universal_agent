# Document 06. CSI v1 VPS Deployment Runbook (2026-02-22)

## 1. Objective

Deploy CSI Ingester v1 to VPS with:

1. signed CSI -> UA ingest enabled,
2. parallel-run validation against legacy playlist timer,
3. explicit cutover and rollback steps.

## 2. Preconditions

Complete these locally first.

1. CSI + UA regression tests pass:

```bash
PYTHONPATH=src:CSI_Ingester/development uv run pytest \
  CSI_Ingester/development/tests/unit \
  CSI_Ingester/development/tests/integration/test_csi_to_ua_local_smoke.py \
  tests/unit/test_signals_ingest.py \
  tests/gateway/test_signals_ingest_endpoint.py \
  tests/contract/test_csi_ua_contract.py \
  tests/test_hooks_service.py -q
```

2. Local smoke passes:

```bash
uv run python CSI_Ingester/development/scripts/csi_local_e2e_smoke.py
```

## 3. Files To Push

Push CSI and UA changes to VPS:

```bash
scripts/vpsctl.sh push \
  CSI_Ingester/development/csi_ingester \
  CSI_Ingester/development/config/config.yaml \
  CSI_Ingester/development/deployment/systemd/csi-ingester.service \
  CSI_Ingester/development/deployment/systemd/csi-ingester.env.example \
  CSI_Ingester/development/scripts/csi_preflight.sh \
  CSI_Ingester/development/scripts/csi_dev_env.sh \
  CSI_Ingester/development/scripts/csi_run.sh \
  CSI_Ingester/development/scripts/csi_parallel_validate.py \
  CSI_Ingester/development/scripts/csi_replay_dlq.py \
  CSI_Ingester/development/scripts/csi_emit_smoke_event.py \
  CSI_Ingester/development/scripts/csi_migrate_from_legacy.sh \
  src/universal_agent/signals_ingest.py \
  src/universal_agent/gateway_server.py \
  src/universal_agent/hooks_service.py
```

## 4. VPS Environment Configuration

## 4.1 Gateway `.env` (UA side)

Edit `/opt/universal_agent/.env` and set:

1. `UA_SIGNALS_INGEST_ENABLED=1`
2. `UA_SIGNALS_INGEST_SHARED_SECRET=<strong-random-secret>`
3. `UA_SIGNALS_INGEST_ALLOWED_INSTANCES=csi-vps-01`
4. Optional: `UA_SIGNALS_INGEST_TIMESTAMP_TOLERANCE_SECONDS=300`

Then restart gateway:

```bash
scripts/vpsctl.sh restart gateway
```

## 4.2 CSI systemd env file

Create `/opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.env` from example and set:

1. `CSI_CONFIG_PATH=/opt/universal_agent/CSI_Ingester/development/config/config.yaml`
2. `CSI_DB_PATH=/opt/universal_agent/CSI_Ingester/development/var/csi.db`
3. `CSI_INSTANCE_ID=csi-vps-01`
4. `CSI_UA_ENDPOINT=http://127.0.0.1:8002/api/v1/signals/ingest`
5. `CSI_UA_SHARED_SECRET=<same secret as UA_SIGNALS_INGEST_SHARED_SECRET>`
6. `YOUTUBE_API_KEY=<youtube-data-api-key>`

## 5. Install CSI systemd Unit

On VPS:

```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 \
  "cp /opt/universal_agent/CSI_Ingester/development/deployment/systemd/csi-ingester.service /etc/systemd/system/csi-ingester.service && \
   systemctl daemon-reload && \
   systemctl enable csi-ingester"
```

## 6. Preflight + Smoke On VPS

Run strict preflight:

```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 \
  "cd /opt/universal_agent && \
   set -a && source CSI_Ingester/development/deployment/systemd/csi-ingester.env && set +a && \
   CSI_Ingester/development/scripts/csi_run.sh CSI_Ingester/development/scripts/csi_preflight.sh --strict"
```

Start CSI service:

```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 "systemctl restart csi-ingester && systemctl is-active csi-ingester"
```

Run CSI->UA signed smoke:

```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 \
  "cd /opt/universal_agent && \
   set -a && source CSI_Ingester/development/deployment/systemd/csi-ingester.env && source .env && set +a && \
   CSI_Ingester/development/scripts/csi_run.sh uv run python CSI_Ingester/development/scripts/csi_emit_smoke_event.py --require-internal-dispatch"
```

Expected: `SMOKE_OK`.

## 7. Parallel-Run Window (24h)

Keep legacy timer enabled while CSI runs. Monitor:

```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 \
  "cd /opt/universal_agent && \
   CSI_Ingester/development/scripts/csi_run.sh python CSI_Ingester/development/scripts/csi_parallel_validate.py --db-path /opt/universal_agent/CSI_Ingester/development/var/csi.db --since-minutes 1440"
```

Gateway logs:

```bash
scripts/vpsctl.sh logs gateway
```

CSI service logs:

```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 "journalctl -u csi-ingester -n 220 --no-pager"
```

## 8. Cutover

After successful 24h parallel run:

```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 \
  "systemctl stop universal-agent-youtube-playlist-poller.timer && \
   systemctl disable universal-agent-youtube-playlist-poller.timer && \
   systemctl stop universal-agent-youtube-playlist-poller.service"
```

Confirm:

```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 \
  "systemctl is-active csi-ingester; systemctl is-enabled universal-agent-youtube-playlist-poller.timer || true"
```

## 9. Rollback

If CSI fails:

```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 \
  "systemctl stop csi-ingester && \
   systemctl enable --now universal-agent-youtube-playlist-poller.timer"
```

Then investigate:

1. `journalctl -u csi-ingester -n 220 --no-pager`
2. `scripts/vpsctl.sh logs gateway`
3. `CSI_Ingester/development/scripts/csi_replay_dlq.py --dry-run`


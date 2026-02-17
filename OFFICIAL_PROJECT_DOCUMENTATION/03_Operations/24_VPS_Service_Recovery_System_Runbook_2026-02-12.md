# 24. VPS Service Recovery System Runbook (2026-02-12)

## Why this exists
This document defines the official recovery model for core Universal Agent services on the VPS.

Goal:
- Keep critical services recoverable without manual intervention.
- Detect unhealthy/offline states quickly.
- Provide a standard install, verify, and test flow for operators.

---

## Environment
- VPS host: `root@100.106.113.93` (Tailscale)
- App root: `/opt/universal_agent`
- Core services covered by recovery:
  - `universal-agent-gateway`
  - `universal-agent-api`
  - `universal-agent-webui`
  - `universal-agent-telegram` (process-state only by default)

---

## Recovery architecture
Recovery is implemented as a `systemd` timer + oneshot service + Bash watchdog script:

1. Timer:
   - Unit: `universal-agent-service-watchdog.timer`
   - Schedule:
     - `OnBootSec=30s`
     - `OnUnitActiveSec=30s`
     - `AccuracySec=5s`

2. Watchdog service:
   - Unit: `universal-agent-service-watchdog.service`
   - Type: `oneshot`
   - Executes: `/opt/universal_agent/scripts/vps_service_watchdog.sh`

3. Watchdog script behavior:
   - Checks `systemctl is-active` for each configured service.
   - Restarts inactive services immediately.
   - Optionally runs HTTP health checks.
   - Tracks consecutive health failures in state files under:
     - `/var/lib/universal-agent/watchdog`
   - Restarts on threshold breach (default: 3 consecutive failures).

Code and unit files:
- `scripts/vps_service_watchdog.sh`
- `scripts/install_vps_service_watchdog.sh`
- `deployment/systemd/universal-agent-service-watchdog.service`
- `deployment/systemd/universal-agent-service-watchdog.timer`

---

## Default health policy
Default service specifications in `vps_service_watchdog.sh`:

- `universal-agent-gateway|http://127.0.0.1:8002/api/v1/health`
- `universal-agent-api|http://127.0.0.1:8001/api/health`
- `universal-agent-webui|http://127.0.0.1:3000/`
- `universal-agent-telegram|` (no HTTP probe; active-state only)

Health handling:
- HTTP status in `100..499` is treated as healthy by default.
- HTTP probe timeout default: `3s`.
- Consecutive unhealthy probes before restart: `3`.

---

## Runtime configuration (environment variables)
Supported knobs (optional):

- `UA_WATCHDOG_SYSTEMCTL_BIN` (default: `systemctl`)
- `UA_WATCHDOG_CURL_BIN` (default: `curl`)
- `UA_WATCHDOG_STATE_DIR` (default: `/var/lib/universal-agent/watchdog`)
- `UA_WATCHDOG_HEALTH_FAIL_THRESHOLD` (default: `3`)
- `UA_WATCHDOG_HTTP_TIMEOUT_SECONDS` (default: `3`)
- `UA_WATCHDOG_HTTP_OK_MAX_STATUS` (default: `499`)
- `UA_WATCHDOG_POST_RESTART_SETTLE_SECONDS` (default: `2`)
- `UA_WATCHDOG_SERVICE_SPECS` (newline-delimited `service|health_url`)

The service unit loads `/opt/universal_agent/.env`, so these may be set there.

---

## Install or update on VPS
Run on VPS as root:

```bash
cd /opt/universal_agent
bash scripts/install_vps_service_watchdog.sh
```

What this installer does:
- Verifies required files exist in app root.
- Installs unit files to `/etc/systemd/system`.
- Runs `systemctl daemon-reload`.
- Enables and starts the timer.
- Starts one immediate watchdog cycle.

---

## Verify current status
Run from local machine:

```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 '
  systemctl is-enabled universal-agent-service-watchdog.timer
  systemctl is-active universal-agent-service-watchdog.timer
  systemctl status universal-agent-service-watchdog.timer --no-pager -n 20
  systemctl status universal-agent-service-watchdog.service --no-pager -n 20
'
```

Tail recent watchdog logs:

```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 \
  "journalctl -u universal-agent-service-watchdog --since '20 minutes ago' --no-pager"
```

Expected healthy cycle log examples:
- `service=... health=ok status_code=200`
- `service=universal-agent-telegram state=active health=not_configured`
- `watchdog cycle complete`

---

## Recovery test procedure (safe operational test)
Use one core service (gateway shown here):

1. Stop service intentionally:
```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 'systemctl stop universal-agent-gateway'
```

2. Wait up to one watchdog interval (`~30s`) plus settle time.

3. Verify restart happened:
```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 '
  systemctl is-active universal-agent-gateway
  journalctl -u universal-agent-service-watchdog --since "10 minutes ago" --no-pager | \
    grep -E "service=universal-agent-gateway action=restart|restart_result"
'
```

4. Verify gateway health:
```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 \
  "curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8002/api/v1/health"
```

---

## Verified evidence (2026-02-12)
Observed on VPS at `2026-02-12T15:12:28Z`:
- `universal-agent-service-watchdog.timer` was `enabled` and `active`.
- Watchdog cycle was running every ~30 seconds.
- Recent logs showed healthy checks for gateway/API/WebUI (`status_code=200`).

Observed restart evidence from watchdog logs on `2026-02-12`:
- `service=universal-agent-gateway action=restart reason=inactive:inactive`
- `service=universal-agent-gateway restart_result=ok post_state=active`
- `service=universal-agent-gateway action=restart reason=inactive:deactivating`
- `service=universal-agent-gateway restart_result=ok post_state=active`

---

## Known limits
- This watchdog is service/process and endpoint-health recovery, not full business-level SLA validation.
- If a service appears `active` but is logically stuck while still returning "healthy", watchdog will not restart it.
- `universal-agent-telegram` has no default HTTP health URL, so only active-state recovery is applied unless configured.

---

## Disable or rollback
To disable automatic recovery:

```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 '
  systemctl disable --now universal-agent-service-watchdog.timer
  systemctl stop universal-agent-service-watchdog.service
'
```

To re-enable:

```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 '
  systemctl daemon-reload
  systemctl enable --now universal-agent-service-watchdog.timer
'
```

---

## Operator checklist
1. Keep watchdog units and script in sync with repo changes.
2. After deploy, run status + log verification commands.
3. Run controlled recovery test after major service lifecycle changes.
4. If false positives occur, tune threshold/timeouts in `.env`.
5. Record incident timestamps and corresponding watchdog log lines for auditability.

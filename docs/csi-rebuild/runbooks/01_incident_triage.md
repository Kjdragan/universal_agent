# CSI Incident Triage Runbook

## Purpose
Guide an on-call operator through triaging a CSI pipeline incident from alert to resolution or escalation.

## Prerequisites
- Access to CSI dashboard (`/dashboard/csi`)
- Access to sessions dashboard (`/dashboard/sessions`)
- SSH access to VPS host
- Familiarity with `journalctl`, `systemctl`, and SQLite

## Step 1: Identify the Alert Source

| Alert Kind | Severity | First Action |
|---|---|---|
| `csi_delivery_health_regression` | warning/error | Check delivery health panel |
| `csi_reliability_slo_breach` | warning | Check SLO panel |
| `csi_delivery_health_auto_remediation_failed` | error | Check auto-remediation logs |
| Specialist loop stall | info | Check specialist loops panel |

## Step 2: Check Dashboard Health

```bash
# Quick health check via API
curl -s http://localhost:18789/api/v1/dashboard/csi/delivery-health | python3 -m json.tool

# Check SLO state
curl -s http://localhost:18789/api/v1/dashboard/csi/reliability-slo | python3 -m json.tool
```

**What good looks like:**
- Delivery health: all sources `status: healthy`, no `failing_sources`
- SLO: `breach_active: false`, `compliance_pct >= 99.0`

## Step 3: Check Adapter Health

```bash
# Check CSI ingester service
systemctl --user status csi-ingester.service

# Check recent logs for errors
journalctl --user -u csi-ingester.service --since "1 hour ago" --no-pager | tail -50

# Check SQLite DB size and recent events
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "SELECT event_type, COUNT(*) FROM events WHERE created_at > datetime('now', '-1 hour') GROUP BY event_type;"
```

## Step 4: Classify Severity

| Condition | Severity | Response Time |
|---|---|---|
| All sources failing, no new events in 1h | P1 | Immediate |
| Single source regression, others healthy | P2 | Within 1h |
| SLO breach but data flowing | P2 | Within 1h |
| Low-signal streak on one topic | P3 | Next business day |
| Quality score degradation | P3 | Next business day |

## Step 5: Escalation Path

1. **P3**: Document in incident log, monitor for 24h
2. **P2**: Run auto-remediation, check results within 1h
3. **P1**: Manual intervention required, see rollback runbook

## Step 6: Resolution Verification

After applying fix:
```bash
# Verify delivery health recovered
curl -s http://localhost:18789/api/v1/dashboard/csi/delivery-health | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('OK' if not d.get('failing_sources') else 'STILL FAILING')"

# Verify new events flowing
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "SELECT COUNT(*) FROM events WHERE created_at > datetime('now', '-10 minutes');"
```

Record resolution in `docs/csi-rebuild/05_incident_log.md`.

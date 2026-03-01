# CSI Remediation Escalation Runbook

## Purpose
Guide an operator through remediation steps when auto-remediation fails or manual intervention is needed.

## Auto-Remediation Check

```bash
# Check if auto-remediation ran recently
journalctl --user -u csi-delivery-health-auto-remediate.service --since "2 hours ago" --no-pager | tail -30

# Check auto-remediation timer
systemctl --user status csi-delivery-health-auto-remediate.timer
```

## Manual Remediation Steps

### Source-Specific Recovery

#### YouTube RSS Source
```bash
# Check RSS adapter health
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "SELECT source, status, last_success_at, consecutive_failures FROM source_state WHERE source LIKE '%youtube%';"

# Reset source state if stuck
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "UPDATE source_state SET consecutive_failures=0, status='healthy' WHERE source LIKE '%youtube%' AND status='failing';"
```

#### Reddit Source
```bash
# Check Reddit adapter health
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "SELECT source, status, last_success_at, consecutive_failures FROM source_state WHERE source LIKE '%reddit%';"

# Reset source state if stuck
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "UPDATE source_state SET consecutive_failures=0, status='healthy' WHERE source LIKE '%reddit%' AND status='failing';"
```

### Pipeline-Wide Recovery

```bash
# Restart CSI ingester
systemctl --user restart csi-ingester.service

# Verify it's running
systemctl --user status csi-ingester.service

# Force a canary check
python3 /opt/universal_agent/scripts/csi_delivery_health_canary.py
```

### Specialist Loop Recovery

```bash
# List stuck loops
curl -s http://localhost:18789/api/v1/dashboard/csi/specialist-loops | \
  python3 -c "import sys,json; [print(f'{l[\"topic_key\"]}: {l[\"status\"]} budget={l[\"follow_up_budget_remaining\"]}') for l in json.load(sys.stdin).get('loops',[])]"

# Reset budget on a stuck loop
curl -X POST http://localhost:18789/api/v1/dashboard/csi/specialist-loops/<TOPIC_KEY>/action \
  -H 'Content-Type: application/json' \
  -d '{"action": "reset_budget", "follow_up_budget": 3}'
```

## Escalation Decision Matrix

| Condition | Action |
|---|---|
| Auto-remediation succeeded | Close incident |
| Auto-remediation failed, source reset fixes it | Monitor 1h, close if stable |
| Service restart fixes it | Monitor 1h, investigate root cause |
| Multiple sources failing simultaneously | Escalate to P1, check network/VPS health |
| Recurring failure (3+ incidents in 24h) | File bug, investigate adapter code |

## Post-Remediation Verification

```bash
# Wait 10 minutes, then verify
sleep 600

# Check delivery health
curl -s http://localhost:18789/api/v1/dashboard/csi/delivery-health | \
  python3 -c "import sys,json; d=json.load(sys.stdin); print('HEALTHY' if d.get('status')=='healthy' else f'ISSUE: {d.get(\"failing_sources\",[])}') "

# Verify events flowing
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "SELECT COUNT(*) as recent_events FROM events WHERE created_at > datetime('now', '-10 minutes');"
```

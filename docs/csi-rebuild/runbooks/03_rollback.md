# CSI Rollback Runbook

## Purpose
Roll back CSI components to a known-good state when a deployment or configuration change causes regressions.

## Pre-Rollback Assessment

```bash
# Identify current deployed commit
cd /opt/universal_agent && git log --oneline -5

# Check if recent deployment caused the issue
git log --oneline --since="2 hours ago"

# Snapshot current state before rollback
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "SELECT event_type, COUNT(*) as cnt, MAX(created_at) as latest FROM events GROUP BY event_type ORDER BY latest DESC LIMIT 10;"
```

## Rollback Steps

### Step 1: Stop Services

```bash
# Stop CSI-related services
systemctl --user stop csi-ingester.service
systemctl --user stop csi-delivery-health-canary.timer
systemctl --user stop csi-delivery-health-auto-remediate.timer
systemctl --user stop csi-delivery-slo-gatekeeper.timer
```

### Step 2: Revert Code

```bash
cd /opt/universal_agent

# Option A: Revert to specific known-good commit
git checkout <KNOWN_GOOD_COMMIT>

# Option B: Revert last N commits
git revert --no-commit HEAD~N..HEAD
git commit -m "ops(csi): rollback last N commits due to regression"
```

### Step 3: Restart Services

```bash
# Restart gateway
pkill -9 -f openclaw-gateway || true
nohup openclaw gateway run --bind loopback --port 18789 --force > /tmp/openclaw-gateway.log 2>&1 &

# Restart CSI services
systemctl --user start csi-ingester.service
systemctl --user start csi-delivery-health-canary.timer
systemctl --user start csi-delivery-health-auto-remediate.timer
systemctl --user start csi-delivery-slo-gatekeeper.timer
```

### Step 4: Verify Recovery

```bash
# Wait for services to stabilize
sleep 30

# Check service status
systemctl --user status csi-ingester.service
ss -ltnp | grep 18789

# Verify delivery health
curl -s http://localhost:18789/api/v1/dashboard/csi/delivery-health | python3 -m json.tool

# Verify events flowing
sleep 300
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "SELECT COUNT(*) FROM events WHERE created_at > datetime('now', '-5 minutes');"
```

## Database Rollback (If Needed)

Only if schema changes caused corruption:

```bash
# Backup current DB
cp /opt/universal_agent/CSI_Ingester/development/var/csi.db \
   /opt/universal_agent/CSI_Ingester/development/var/csi.db.bak.$(date +%s)

# Check for recent schema changes
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db ".schema" | head -50

# If DB is corrupted, restore from backup
ls -la /opt/universal_agent/CSI_Ingester/development/var/csi.db.bak.*
cp /opt/universal_agent/CSI_Ingester/development/var/csi.db.bak.<TIMESTAMP> \
   /opt/universal_agent/CSI_Ingester/development/var/csi.db
```

## Post-Rollback Checklist

- [ ] All CSI services running
- [ ] Gateway responding on port 18789
- [ ] Delivery health API returns healthy
- [ ] New events appearing in database
- [ ] CSI dashboard loading without errors
- [ ] Incident logged in `docs/csi-rebuild/05_incident_log.md`
- [ ] Root cause identified and documented

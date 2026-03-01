# CSI Data Repair Runbook

## Purpose
Guide an operator through repairing corrupted, missing, or inconsistent data in the CSI pipeline.

## Common Data Issues

| Issue | Symptom | Repair Method |
|---|---|---|
| Duplicate events | Inflated counts, repeated notifications | Deduplication query |
| Missing events | Gap in timeline, starvation alerts | Re-ingest from source |
| Stale source state | Source stuck in "failing" | Reset source state |
| Orphaned specialist loops | Loops with no recent events | Close or reset loops |
| DLQ buildup | High DLQ count in delivery health | Replay or purge DLQ |

## Deduplication Repair

```bash
# Check for duplicates
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "SELECT dedupe_key, COUNT(*) as cnt FROM events GROUP BY dedupe_key HAVING cnt > 1 LIMIT 20;"

# Remove duplicates (keep earliest)
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "DELETE FROM events WHERE rowid NOT IN (SELECT MIN(rowid) FROM events GROUP BY dedupe_key);"
```

## Source State Reset

```bash
# View current source states
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "SELECT source, status, consecutive_failures, last_success_at FROM source_state ORDER BY source;"

# Reset a specific failing source
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "UPDATE source_state SET status='healthy', consecutive_failures=0 WHERE source='<SOURCE_NAME>';"

# Reset all failing sources
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "UPDATE source_state SET status='healthy', consecutive_failures=0 WHERE status='failing';"
```

## Specialist Loop Cleanup

```bash
# List all loops with status
curl -s http://localhost:18789/api/v1/dashboard/csi/specialist-loops | \
  python3 -c "
import sys, json
loops = json.load(sys.stdin).get('loops', [])
for l in loops:
    print(f'{l[\"topic_key\"]:40s} status={l[\"status\"]:20s} budget={l[\"follow_up_budget_remaining\"]}/{l[\"follow_up_budget_total\"]} events={l[\"events_count\"]}')
"

# Close stale loops (no events in 7 days)
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "UPDATE csi_specialist_loops SET status='closed', closed_at=datetime('now') WHERE status='open' AND last_event_at < datetime('now', '-7 days');" 2>/dev/null || \
sqlite3 /opt/universal_agent/gateway_activity.db \
  "UPDATE csi_specialist_loops SET status='closed', closed_at=datetime('now') WHERE status='open' AND last_event_at < datetime('now', '-7 days');"
```

## DLQ Management

```bash
# Check DLQ depth
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "SELECT COUNT(*) as dlq_depth FROM delivery_attempts WHERE status='failed';"

# View recent DLQ entries
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "SELECT event_id, target, error_message, created_at FROM delivery_attempts WHERE status='failed' ORDER BY created_at DESC LIMIT 10;"

# Retry failed deliveries (mark as pending for re-attempt)
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "UPDATE delivery_attempts SET status='pending', retry_count=retry_count+1 WHERE status='failed' AND retry_count < 3;"

# Purge old DLQ entries (older than 7 days)
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "DELETE FROM delivery_attempts WHERE status='failed' AND created_at < datetime('now', '-7 days');"
```

## Report Data Repair

```bash
# Check for reports with missing artifacts
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "SELECT report_key, created_at FROM reports WHERE artifact_path_markdown IS NULL AND created_at > datetime('now', '-24 hours');"

# Check artifact files exist on disk
find /opt/universal_agent/CSI_Ingester/development/var/reports/ -name "*.md" -mtime -1 -ls
```

## Database Integrity Check

```bash
# Run integrity check
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db "PRAGMA integrity_check;"

# Check for WAL issues
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db "PRAGMA journal_mode;"
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db "PRAGMA wal_checkpoint(TRUNCATE);"

# Vacuum if DB is bloated
ls -lh /opt/universal_agent/CSI_Ingester/development/var/csi.db
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db "VACUUM;"
```

## Post-Repair Verification

```bash
# Verify event flow resumed
sleep 300
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "SELECT event_type, COUNT(*) FROM events WHERE created_at > datetime('now', '-5 minutes') GROUP BY event_type;"

# Verify delivery health
curl -s http://localhost:18789/api/v1/dashboard/csi/delivery-health | python3 -m json.tool

# Verify no new DLQ entries
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "SELECT COUNT(*) FROM delivery_attempts WHERE status='failed' AND created_at > datetime('now', '-5 minutes');"
```

Record all data repairs in `docs/csi-rebuild/05_incident_log.md` with before/after counts.

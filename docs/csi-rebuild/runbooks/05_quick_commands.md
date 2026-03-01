# CSI On-Call Quick Commands

## Health Checks (Copy-Paste Ready)

### Pipeline Status
```bash
# All-in-one health check
echo "=== Delivery Health ===" && \
curl -s http://localhost:18789/api/v1/dashboard/csi/delivery-health | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Status: {d.get(\"status\",\"unknown\")}  Failing: {d.get(\"failing_sources\",[])}  Degraded: {d.get(\"degraded_sources\",[])}')" && \
echo "=== SLO ===" && \
curl -s http://localhost:18789/api/v1/dashboard/csi/reliability-slo | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'Breach: {d.get(\"breach_active\",\"?\")}  Compliance: {d.get(\"compliance_pct\",\"?\")}%')" && \
echo "=== Recent Events ===" && \
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db "SELECT event_type, COUNT(*) FROM events WHERE created_at > datetime('now', '-1 hour') GROUP BY event_type;"
```

### Service Status
```bash
systemctl --user status csi-ingester.service csi-delivery-health-canary.timer csi-delivery-health-auto-remediate.timer csi-delivery-slo-gatekeeper.timer 2>/dev/null | grep -E '(Active|Loaded|●)'
```

### Gateway Status
```bash
ss -ltnp | grep 18789 && echo "Gateway: UP" || echo "Gateway: DOWN"
curl -s http://localhost:18789/api/v1/health | python3 -m json.tool 2>/dev/null || echo "Gateway not responding"
```

### Recent Notifications
```bash
curl -s http://localhost:18789/api/v1/dashboard/activity/notifications?limit=5 | \
  python3 -c "import sys,json; [print(f'{n[\"severity\"]:8s} {n[\"kind\"]:50s} {n[\"created_at\"]}') for n in json.load(sys.stdin).get('notifications',[])]"
```

### Specialist Loops
```bash
curl -s http://localhost:18789/api/v1/dashboard/csi/specialist-loops | \
  python3 -c "import sys,json; [print(f'{l[\"topic_key\"]:40s} {l[\"status\"]:15s} conf={l[\"confidence_score\"]:.2f}/{l[\"confidence_target\"]:.2f} budget={l[\"follow_up_budget_remaining\"]}') for l in json.load(sys.stdin).get('loops',[])]"
```

## Common Fixes

### Restart Gateway
```bash
pkill -9 -f openclaw-gateway || true
nohup openclaw gateway run --bind loopback --port 18789 --force > /tmp/openclaw-gateway.log 2>&1 &
sleep 5 && ss -ltnp | grep 18789
```

### Restart CSI Ingester
```bash
systemctl --user restart csi-ingester.service
sleep 5 && systemctl --user status csi-ingester.service | head -5
```

### Force Canary Check
```bash
python3 /opt/universal_agent/scripts/csi_delivery_health_canary.py
```

### Reset Failing Source
```bash
# Replace <SOURCE> with actual source name
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "UPDATE source_state SET status='healthy', consecutive_failures=0 WHERE source='<SOURCE>';"
```

### Reset Specialist Loop Budget
```bash
# Replace <TOPIC_KEY> with actual topic key
curl -X POST http://localhost:18789/api/v1/dashboard/csi/specialist-loops/<TOPIC_KEY>/action \
  -H 'Content-Type: application/json' -d '{"action":"reset_budget","follow_up_budget":3}'
```

### Purge Old Notifications
```bash
curl -X POST http://localhost:18789/api/v1/dashboard/csi/purge-sessions \
  -H 'Content-Type: application/json' -d '{"older_than_hours": 168}'
```

## Expected Outputs ("What Good Looks Like")

### Healthy Delivery Health
```json
{
  "status": "healthy",
  "failing_sources": [],
  "degraded_sources": [],
  "sources": {
    "youtube_rss": {"status": "healthy", "events_last_hour": 5},
    "reddit": {"status": "healthy", "events_last_hour": 3}
  }
}
```

### Healthy SLO
```json
{
  "breach_active": false,
  "compliance_pct": 99.5,
  "window_hours": 24
}
```

### Active Specialist Loop
```
topic_key: rss_trend:ai                    status: open    conf=0.65/0.72 budget=2
```

### Quality Grade Distribution (Healthy)
- Grade A: 20-40% of reports
- Grade B: 30-40% of reports
- Grade C: 15-25% of reports
- Grade D: < 10% of reports

# CSI v2 Release Candidate Soak + GA Gate (Packet 22)

## Purpose
Certify the CSI rebuild for stable operations through a structured soak period and formal GA sign-off.

## 72-Hour Soak Validation Report Template

### Soak Window
- **Start:** `YYYY-MM-DDTHH:MM:SSZ`
- **End:** `YYYY-MM-DDTHH:MM:SSZ`
- **Duration:** 72 hours
- **Environment:** VPS production (host `100.106.113.93`)

### Data Plane Health

| Metric | Target | Observed | Pass? |
|---|---|---|---|
| Event ingestion uptime | >= 99% | | |
| Source failure rate | < 5% per source | | |
| DLQ depth (end of soak) | < 20 | | |
| Delivery success rate | >= 95% | | |
| Canary check pass rate | >= 95% | | |

### SLO Compliance Summary

| SLO | Target | Observed | Pass? |
|---|---|---|---|
| Daily compliance % | >= 99.0% | | |
| Breach count (72h window) | <= 2 | | |
| Breach duration (total) | < 2 hours | | |
| Recovery time (p95) | < 30 minutes | | |

### Quality Metrics

| Metric | Target | Observed | Pass? |
|---|---|---|---|
| Reports generated (72h) | >= 10 | | |
| Quality grade A+B rate | >= 50% | | |
| Quality grade D rate | < 15% | | |
| Source diversity (avg sources/report) | >= 1.5 | | |

### Specialist Loop Health

| Metric | Target | Observed | Pass? |
|---|---|---|---|
| Active loops | >= 1 | | |
| Budget-exhausted loops (no resolution) | < 3 | | |
| Follow-up correlation coverage | 100% | | |
| Escalation rate | < 20% | | |

### Notification Pipeline

| Metric | Target | Observed | Pass? |
|---|---|---|---|
| Critical alerts (false positive rate) | < 10% | | |
| Suppressed low-value rate | >= 30% | | |
| Executive digest generated | Daily | | |

### Regression Check

| Check | Result |
|---|---|
| All unit tests pass | |
| All gateway integration tests pass | |
| Web UI builds without errors | |
| No P1 regressions in soak window | |
| No P2 regressions unresolved | |

---

## GA Sign-Off Criteria

All of the following must be true for GA sign-off:

### Hard Gates (Must Pass)
- [ ] 72h soak completed without P1 regression
- [ ] SLO compliance >= 99.0% over soak window
- [ ] All unit tests pass (`uv run python -m pytest tests/unit/test_csi_*.py`)
- [ ] All gateway tests pass (`uv run python -m pytest tests/gateway/test_signals_ingest_endpoint.py`)
- [ ] Web UI builds (`npm --prefix web-ui run build`)
- [ ] Runbooks reviewed and validated (packet 21)
- [ ] Open-risk list reviewed with owners assigned

### Soft Gates (Should Pass)
- [ ] Quality grade A+B rate >= 50%
- [ ] Source diversity score >= 1.5 avg
- [ ] Executive digest producing actionable output
- [ ] No new feature-flag sources enabled without adapter tests
- [ ] Dashboard loads in < 3 seconds

### Sign-Off Record

| Role | Name | Date | Approval |
|---|---|---|---|
| Operator | | | |
| Developer | | | |

---

## Open Risk Registry

| ID | Risk | Severity | Owner | Due Date | Status | Mitigation |
|---|---|---|---|---|---|---|
| R-001 | Runtime-generated artifacts may reintroduce panel noise | Medium | Operator | Soak+7d | Open | Monitor notification volume during soak; tune suppression thresholds |
| R-002 | Deploy/runtime state divergence after mainline consolidation | Medium | Developer | Pre-GA | Open | Run full test suite post-merge; verify gateway restart |
| R-003 | Source coverage limited to RSS+Reddit; expansion untested in production | Low | Developer | Post-GA | Accepted | Feature flags scaffold ready (packet 20); enable incrementally |
| R-004 | Specialist loop budget exhaustion may leave topics unresolved | Low | Operator | Soak+7d | Open | Monitor via quick commands; reset budgets if needed |
| R-005 | Quality scoring calibration based on heuristics, not ML | Low | Developer | Post-GA | Accepted | Packet 16 scoring is v1; plan v2 with feedback loop |
| R-006 | Follow-up contract timeout (1h default) may be too short for complex analyses | Low | Developer | Post-GA | Accepted | Configurable via contract builder; tune based on soak data |

---

## Soak Validation Commands

Run these at 24h, 48h, and 72h marks during soak:

```bash
# Full health check
echo "=== $(date -u) ===" && \
echo "--- Delivery Health ---" && \
curl -s http://localhost:18789/api/v1/dashboard/csi/delivery-health | python3 -c "
import sys,json; d=json.load(sys.stdin)
print(f'  Status: {d.get(\"status\",\"unknown\")}')
print(f'  Failing: {d.get(\"failing_sources\",[])}')
print(f'  Degraded: {d.get(\"degraded_sources\",[])}')
" && \
echo "--- SLO ---" && \
curl -s http://localhost:18789/api/v1/dashboard/csi/reliability-slo | python3 -c "
import sys,json; d=json.load(sys.stdin)
print(f'  Breach active: {d.get(\"breach_active\",\"?\")}')
print(f'  Compliance: {d.get(\"compliance_pct\",\"?\")}%')
" && \
echo "--- Event Volume (last 24h) ---" && \
sqlite3 /opt/universal_agent/CSI_Ingester/development/var/csi.db \
  "SELECT event_type, COUNT(*) FROM events WHERE created_at > datetime('now', '-24 hours') GROUP BY event_type;" && \
echo "--- Specialist Loops ---" && \
curl -s http://localhost:18789/api/v1/dashboard/csi/specialist-loops | python3 -c "
import sys,json
loops = json.load(sys.stdin).get('loops', [])
for l in loops:
    print(f'  {l[\"topic_key\"]:40s} {l[\"status\"]:15s} conf={l[\"confidence_score\"]:.2f}/{l[\"confidence_target\"]:.2f}')
" && \
echo "--- Notification Tiers (last 24h) ---" && \
curl -s http://localhost:18789/api/v1/dashboard/activity/notifications?limit=100 | python3 -c "
import sys,json
notifs = json.load(sys.stdin).get('notifications', [])
tiers = {}
for n in notifs:
    q = (n.get('metadata') or {}).get('quality') or {}
    grade = q.get('quality_grade', 'N/A')
    tiers[grade] = tiers.get(grade, 0) + 1
print(f'  Quality distribution: {dict(sorted(tiers.items()))}')
print(f'  Total: {len(notifs)}')
"
```

---

## Post-GA Roadmap (Out of Scope)

These items are deferred to post-GA iterations:
- Quality scoring v2 with ML-based calibration
- X/Twitter and Threads source adapters (feature flags ready)
- Notification routing to Telegram with tier-based filtering
- Multi-tenant source isolation
- Historical trend analysis across soak windows

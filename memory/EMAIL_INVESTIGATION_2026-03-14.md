# Email Investigation: Infra Config Drift Alert

**Date:** 2026-03-14
**Thread ID:** fb28e7f8-47e4-4b67-b136-f0918bf2e20e
**Message ID:** <CAEi7pTmLHmNaAteK3Rdc09t0CXTn=zi95QmE6hnGNAT-bnuHZQ@mail.gmail.com>
**From:** Kevin Dragan <kevinjdragan@gmail.com>
**Classification:** Investigation Request
**Status:** Complete

---

## Kevin's Request

> "please investigate what is going on and explain it to me in simple terms and any recommmended solutions"

Context: Kevin received a heartbeat alert classified as "infra_config_drift" with recommendation to set `UA_HOOKS_AGENT_DISPATCH_CONCURRENCY=20`.

---

## Investigation Findings

### Root Cause
The alert was a **false positive from a past incident** (March 12). The heartbeat monitoring system has a design gap where it only emits findings when actionable work exists, but the observer expects fresh findings on every cycle.

### Original Incident (March 12)
- 168 agent processes spawned consuming 13GB RAM
- System activated swap (4GB), causing performance degradation
- Root cause: No concurrency limit on agent dispatch

### Current State (Healthy)
- `UA_HOOKS_AGENT_DISPATCH_CONCURRENCY=2` (already set)
- RAM: 23GB / 31GB (74%)
- Swap: 1.9GB / 14GB (14%)
- CPU Load: 3.84 / 8 cores (48%)
- Active agents: ~22 processes

### Why the Alert Triggered
During a no-op heartbeat cycle (no actionable work), the findings file wasn't updated, causing a "stale artifact" warning that was misclassified as "infra_config_drift".

---

## Recommended Solutions

### Immediate: No Action Required
System is operating normally. Concurrency cap is working as intended.

### Low Priority: Heartbeat Design Fix
Update heartbeat logic to always emit findings file, even when empty:
```python
findings = {
    "version": 1,
    "overall_status": "ok",
    "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    "source": "vps_system_health_check",
    "summary": "Heartbeat completed. No actionable items.",
    "findings": []
}
write_json("work_products/heartbeat_findings_latest.json", findings)
```

### Optional: Concurrency Tuning
Current setting of `2` is conservative. Consider increasing to `5` if webhook processing feels slow.

---

## Action Taken

1. Created full analysis: `/home/kjdragan/lrepos/universal_agent/artifacts/email-investigations/infra_config_drift_analysis_2026-03-14.md`

2. Drafted reply to Kevin via AgentMail (draft ID: `<0100019cefb156bd-89a2f1dd-c2a9-4abc-9e56-efd39a41b174-000000@email.amazonses.com>`)

3. Reply includes:
   - Simple explanation of what happened
   - Current system state confirmation
   - Why the alert triggered
   - Recommended actions (immediate, low-priority, optional)
   - Link to full analysis document

---

## Files Referenced

- Heartbeat instructions: `/home/kjdragan/lrepos/universal_agent/memory/HEARTBEAT.md`
- Latest findings: `/home/kjdragan/lrepos/universal_agent/work_products/heartbeat_findings_latest.json`
- System health: `/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/cron_6eb03023c0/work_products/system_health_latest.md`
- Hooks service: `/home/kjdragan/lrepos/universal_agent/src/universal_agent/hooks_service.py`
- Full analysis: `/home/kjdragan/lrepos/universal_agent/artifacts/email-investigations/infra_config_drift_analysis_2026-03-14.md`

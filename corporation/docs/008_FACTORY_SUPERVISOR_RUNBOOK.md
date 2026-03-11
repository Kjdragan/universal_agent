# Factory Supervisor Runbook

## Purpose
`factory-supervisor` provides a plain-language HQ snapshot of fleet posture, queue pressure, and delegation flow.

## Operating Boundary
- Advisory-only by default.
- No runtime mutation without explicit confirmation.

## Inputs
- Factory posture/capabilities.
- Factory registrations and freshness state.
- Delegation history.
- Task Hub overview and queue state.
- System timers.

## Output
- Severity (`info|warning|critical`)
- KPI cards (dispatch eligible, backlog, open CSI incidents, stale/offline factories)
- Diagnostics JSON
- Recommendation list with confirmation flags
- Persisted artifact pair (`.md`, `.json`)

## HQ + Worker Flow
1. HQ receives telemetry and registrations.
2. Workers publish heartbeat/mission status.
3. Task Hub tracks agent-eligible queue.
4. Factory Supervisor summarizes fleet health and pressure.

## Frequency Notes
- Worker heartbeat send interval: env-driven, default 60s.
- HQ self-heartbeat loop: 60s.
- Registration stale/offline thresholds: 5m/15m.
- Supervisor tab refresh: 15s polling.
- Run-now snapshot: on demand.

## Common Diagnoses
- High `dispatch_eligible`: task pressure or routing overproduction.
- High `open_csi_incidents`: CSI signal volume exceeding triage bandwidth.
- Stale/offline workers: mission flow risk.

## Typical Tuning Levers
- Adjust CSI-to-task routing strictness.
- Rebuild or triage dispatch queue.
- Review heartbeat cadence and worker connectivity.

## Escalation Ladder
1. Info: observe and monitor.
2. Warning: inspect diagnostics and tune policies.
3. Critical: immediate operator review; apply guarded control actions with confirmation.

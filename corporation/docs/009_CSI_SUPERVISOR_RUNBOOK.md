# CSI Supervisor Runbook

## Purpose
`csi-supervisor` turns CSI telemetry into a concise operational status: source health, delivery/SLO posture, loop pressure, and Task Hub impact.

## Operating Boundary
- Advisory-only by default.
- No destructive or state-mutating CSI actions without explicit confirmation.

## Inputs
- CSI health and delivery-health APIs.
- CSI reliability SLO API.
- Specialist loop inventory.
- Latest opportunity bundle summary.
- Task Hub queue overlays for CSI load.
- CSI-filtered events.

## Output
- Severity (`info|warning|critical`)
- KPI cards (degraded sources, DLQ, loops open, CSI task footprint)
- Diagnostics JSON
- Recommendation list with confirmation flags
- Persisted artifact pair (`.md`, `.json`)

## Flow Interpretation
1. CSI produces signals and specialist loop state.
2. Gateway ingests CSI activity.
3. Routing policy decides which CSI signals become Task Hub items.
4. CSI Supervisor explains what is auto-resolved vs what is queued for human/agent review.

## Frequency Notes
- CSI event streams update continuously; many artifacts roll up hourly/daily.
- Supervisor tab refresh: 15s polling.
- Run-now snapshot: on demand.

## Common Diagnoses
- `delivery` degraded + nonzero DLQ: transport/retry path issue.
- `slo` breached: sustained reliability drift.
- high loop-open + high CSI task count: too much conversion pressure.

## Typical Tuning Levers
- Tighten CSI-to-task routing policy.
- Increase suppression for low-signal loop classes.
- Investigate adapter failures for degraded sources.

## Escalation Ladder
1. Info: monitor normal variance.
2. Warning: perform targeted tuning and follow-up checks.
3. Critical: initiate operator intervention and guarded remediations.

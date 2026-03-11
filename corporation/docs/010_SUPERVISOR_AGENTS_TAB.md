# Supervisor Agents Tab Architecture

## Goal
Provide a single HQ-only dashboard surface for supervisor snapshots across major system lanes.

## Route
- UI route: `/dashboard/supervisors`
- Navigation label: `Supervisor Agents`
- Visibility: headquarters only (`requiresHeadquarters: true`)

## Current Supervisors (v1)
1. `factory-supervisor`
2. `csi-supervisor`

## API Contract
- `GET /api/v1/dashboard/supervisors/registry`
- `GET /api/v1/dashboard/supervisors/{supervisor_id}/snapshot`
- `POST /api/v1/dashboard/supervisors/{supervisor_id}/run`
- `GET /api/v1/dashboard/supervisors/{supervisor_id}/runs?limit=N`

## Snapshot Envelope
Each supervisor returns:
- `status`
- `supervisor_id`
- `generated_at`
- `summary`
- `severity`
- `kpis`
- `diagnostics`
- `recommendations`
- `artifacts`

## Report Persistence
`run` writes:
- `UA_ARTIFACTS_DIR/supervisor-briefs/<supervisor_id>/<YYYY-MM-DD>/<timestamp>.md`
- matching `.json`

## Event Emission
Each run emits a dashboard system event with:
- `event_type`: `supervisor_brief_ready`
- payload includes `supervisor_id`, `severity`, `summary`, and artifact paths.

## UI Behavior
- Selector tabs: Factory / CSI.
- Controls: `Run now`, `Refresh`.
- Auto-refresh polling: 15s.
- Sections: status summary, KPI cards, flow diagnostics, recommendations, latest brief links.

## Extensibility
Add new supervisor views by:
1. Extending registry in `src/universal_agent/supervisors/registry.py`.
2. Adding a snapshot builder in `src/universal_agent/supervisors/builders.py`.
3. Wiring selection logic in gateway snapshot resolver.

Candidate next supervisors:
- `threads-supervisor`
- `delivery-supervisor`
- `notebooklm-supervisor`

# ADR-001: Control Plane and Deployment Topology

- **Date:** 2026-02-15
- **Status:** Accepted (for v1 baseline)
- **Owners:** DraganCorp architecture track

## Context

We need a sustainable path to support:

1. pragmatic coding automation inside the existing UA runtime,
2. future autonomous long-running missions,
3. strict simplicity and maintainability constraints,
4. API concurrency-aware scaling.

A key clarification: Simone is the core orchestrator and should not be treated as a factory worker runtime.

## Decision

Adopt a **hybrid phased topology**:

1. **v1:** Simone-only user interface + persistent CODER VP session delegation.
2. **v1.5:** shared-VPS multi-runtime VP lanes (separate runtime process/service for CODER VP).
3. **v2:** clone-ready baseline package (shared UA skeleton + mission overlays).
4. **v2+:** externalized cloned UA factories for missions that justify autonomy/isolation/concurrency scaling.

## Rationale

- Preserves current production behavior while introducing mission governance.
- Delivers value quickly without premature distributed-system complexity.
- Supports future autonomous workflows with clear isolation boundaries.
- Minimizes technical debt by keeping a shared base and avoiding divergent forks.

## Alternatives considered

### A) In-core only (no factories)

- Pros: simplest operations, fastest initial delivery.
- Cons: weak fit for multi-day autonomous missions and high parallel load.

### B) Shared-VPS multi-runtime VP lanes (no clones yet)

- Pros: strong isolation from core while retaining operational simplicity of one host.
- Cons: still shares host-level resource constraints and needs lane governance.

### C) Factory-first (clone-heavy from day one)

- Pros: strong isolation and parallelism.
- Cons: high early complexity, higher ops burden, slower time-to-value.

### D) Hybrid phased (chosen)

- Balances speed, safety, and scalability.
- Defers complexity until load/mission evidence justifies it.

## Consequences

### Positive

- Clear governance model (Simone as COO/control plane).
- Controlled migration path.
- Better long-term extensibility for autonomous mission lanes.

### Negative / tradeoffs

- Requires disciplined phase boundaries and explicit graduation criteria.
- Adds control-plane design work (mission contracts, quotas, callbacks).

## Guardrails

- Keep Simone as default entrypoint for all user intents.
- Anchor VP persistence on gateway-managed session lifecycle + explicit VP session registry.
- Use idempotency keys for cross-instance actions.
- Enforce mission budgets (time/calls/cost).
- Require heartbeat/report cadence for autonomous missions.

## Rollback triggers

Rollback any factory rollout to Simone-only + in-core execution if one or more occur:

1. Factory mission success rate drops below agreed threshold for sustained window.
2. Core UX latency degrades beyond budget due to control-plane overhead.
3. Incident rate (duplicate actions, hung missions, or missed callbacks) exceeds threshold.
4. Maintenance burden grows beyond acceptable ops envelope.

## Follow-up actions

1. Define mission envelope spec in `DraganCorp/specs/`.
2. Define VP session registry contract and ownership model.
3. Define inter-runtime callback/auth contract.
4. Add shared-VPS VP runtime runbook + clone deployment runbook in `DraganCorp/docs/operations/`.
5. Build pilot scorecards for v1.5 VP runtime and v2+ factory graduation decisions.

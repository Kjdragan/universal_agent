# DraganCorp

DraganCorp is a parallel architecture and implementation track for scaling Universal Agent (UA) into a multi-instance operating model where Simone remains the core control plane and mission-specific factory clones execute long-running autonomous work.

## Why this exists

This track is intentionally separate from current production UA docs/code paths so we can design and validate a multi-primary/multi-instance model without destabilizing the core system.

## Core stance

1. **Simone is not a factory.** Simone is the core orchestrator/COO and default user-facing interface.
2. **Factories are separate cloned UA deployments.** They run mission-specific workloads and report outcomes back to Simone.
3. **Build in phases.** Start with pragmatic in-core CODER capabilities, then add clone-ready packaging and communication contracts, then externalized autonomous factories.
4. **Optimize for low maintenance.** Reuse one shared UA skeleton with configurable overlays (agents, skills, mission directives) rather than maintaining divergent forks.

## Directory map

- `docs/architecture/` — target operating model, control/data plane, governance, migration
- `docs/decisions/` — ADRs for key architecture choices and rollback criteria
- `docs/operations/` — deployment and runbook guidance for clone/factory lifecycle
- `specs/` — protocol and contract specs (mission envelope, events, safety rails)
- `prototypes/` — implementation experiments and proof-of-concept outputs

## Initial artifacts

- `docs/architecture/01_Multi_Primary_Agent_Governance_And_Factory_Architecture_2026-02-15.md`
- `docs/decisions/ADR-001-control-plane-and-deployment-topology.md`

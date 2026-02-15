# 01. Factory Clone Deployment and Supervision Runbook

This runbook defines a safe, phased process for launching and supervising mission-specific UA factory clones under Simone control.

## 1) Scope

Use this runbook when spinning up a separate UA instance for long-running autonomous missions (research, freelance workstreams, multi-day coding backlogs).

Do not use this runbook for normal in-core Simone/CODER execution.

## 2) Principles

1. Keep one shared UA base skeleton.
2. Specialize via overlays (agent set, skills, mission directives), not forks.
3. Keep Simone as control-plane authority and default human interface.
4. Require mission contracts, heartbeats, and kill switches before autonomous execution.

## 3) Clone preparation checklist

1. Create clone instance identifier (`factory_<mission>_<date>`).
2. Apply baseline config profile from core UA.
3. Apply mission overlay package:
   - enabled agents
   - enabled skills
   - prompt policy overlay
   - safety/approval rules
4. Verify secrets and tokens for the clone environment.
5. Confirm callback path to Simone control plane is reachable.
6. Run health checks before mission dispatch.

## 4) Mission dispatch checklist

1. Generate `mission_id` and dispatch payload (`specs/mission-envelope-v1.md`).
2. Set autonomy window and budgets.
3. Register kill switch for mission.
4. Start mission and await `mission.accepted` event.
5. Monitor heartbeat and progress cadence.

## 5) Supervision policy

1. Missing heartbeat > threshold -> mark degraded and trigger recovery workflow.
2. Budget threshold crossed -> pause and request Simone decision.
3. Repeated blocked state -> escalate to Simone with blocker summary.
4. Terminal state required for mission closure.

## 6) Incident response

1. Duplicate action detected -> apply idempotency replay guard, freeze mission lane.
2. Callback failures -> switch to buffered retry queue and alert Simone.
3. Runaway behavior -> invoke kill switch, persist trace/artifacts, postmortem required.

## 7) Rollback

Rollback to Simone-only + in-core execution when factory reliability, latency, or ops overhead exceeds guardrail thresholds documented in ADR-001.

## 8) Post-mission closure

1. Validate final artifacts.
2. Record outcome scorecard (quality, latency, incidents, maintainability impact).
3. Decide: retire clone, keep warm standby, or convert to reusable mission template.

# Autonomous Eligibility Matrix (Phase 0 Spec)

Last updated: 2026-02-24

## Decision Inputs

1. Task labels/flags (`blocked`, `manual_gate`, `agent-ready`).
2. Schedule state (due/overdue/no-date, repeat windows).
3. Runtime capacity (active runs, queue depth, safety limits).
4. Capability prerequisites (required tools/secrets available).
5. Mission fit (heartbeat objectives + identity/soul guidance).

## Eligibility Table

1. Eligible:
- `agent-ready` present
- not `blocked`
- not `manual_gate`
- capability prerequisites satisfied
- no conflicting high-priority foreground run

2. Not eligible:
- blocked/missing dependency
- explicit manual approval required
- destructive/high-risk operation without approval
- missing credentials or unsafe runtime state

## Selection Policy

1. Sort by:
- urgency (overdue/due)
- priority
- mission fit
- confidence

2. Throughput guard:
- Start with max `1` proactive task per heartbeat cycle.

3. Visibility:
- Every autonomous completion/failure emits dashboard notification.
- Daily 7:00 AM briefing summarizes autonomous actions.


# 25. System Configuration Agent Architecture and Implementation Plan (2026-02-12)

## Why this exists
We need natural-language control over system/runtime behavior (for example, Chron/Cron schedule changes) without overloading the primary assistant context and behavior.

Goal:
1. Keep primary assistant focused on user-task execution.
2. Route platform-operations intent to a dedicated specialist.
3. Support intelligent, schema-backed change execution instead of regex-only mapping.
4. Keep subagent delegation hidden in normal user-facing responses.

---

## Design decision
Adopt a dedicated sub-agent:
- Name: `system-configuration-agent`
- Role: interpret and execute system parameter/runtime operations
- Visibility: hidden by default in user-facing narration

Primary assistant behavior:
1. Detect operations intent (settings/runtime/scheduling changes).
2. Delegate to `system-configuration-agent`.
3. Return final result as a unified Simon response.

---

## Scope model
In scope:
1. Chron/Cron schedule changes (one-shot, repeating, pause/resume, enable/disable, run-now).
2. Heartbeat operational settings.
3. Ops config updates requested by user.
4. Non-destructive diagnostics + verification of applied changes.

Out of scope (without explicit approval):
1. Destructive bulk operations.
2. Arbitrary filesystem/service mutation not tied to requested system behavior.
3. Secret management beyond approved env/config procedures.

---

## Architecture
### 1) Intent routing layer (primary assistant)
Responsibilities:
1. Route system-parameter requests to `system-configuration-agent`.
2. Preserve unified user-facing response style.

Current step completed:
1. Added primary prompt guidance to mandate delegation for system/runtime requests.

### 2) Interpretation layer (system-configuration-agent)
Responsibilities:
1. Parse natural-language request into structured operation payload.
2. Include confidence, assumptions, and warnings.

Target output contract:
```json
{
  "status": "proposal|applied|blocked|failed",
  "operations": [
    {
      "type": "cron_set_schedule|cron_set_enabled|heartbeat_set_interval|...",
      "target": {"source": "cron", "job_id": "abc123"},
      "params": {"run_at": "...", "every": "...", "cron_expr": "...", "timezone": "UTC"}
    }
  ],
  "verification": [],
  "notes": []
}
```

### 3) Policy and schema layer
Responsibilities:
1. Validate operation schema.
2. Enforce guardrails and allowlist by operation type.
3. Reject ambiguous/unsafe requests with precise feedback.

### 4) Execution adapter layer
Responsibilities:
1. Apply operations through existing first-class endpoints/services.
2. Maintain idempotence and state consistency.
3. Produce before/after snapshots and verification checks.

---

## Rollout plan
### Phase 0 (completed)
1. Define and add `system-configuration-agent` sub-agent spec.
2. Add primary prompt routing policy to delegate system operations.
3. Document architecture and phased plan (this document).

### Phase 1 (next)
1. Add internal operation schema models for system-configuration actions.
2. Add interpreter entrypoint that calls `system-configuration-agent` for structured proposals.
3. Add strict parser/validator for returned operation payloads.
4. Integrate with existing Chron update path (`schedule_time`) as primary interpretation path.
5. Keep deterministic fallback as safety net during rollout.

### Phase 2
1. Route calendar change-request proposals through system-ops interpreter.
2. Add hidden delegation UX behavior by default across chat + dashboard paths.
3. Add approvals for high-risk operation classes.

### Phase 3
1. Add operation audit log endpoint (who, what, before/after, verification).
2. Add replay-safe idempotence keys for repeated requests.
3. Add regression test suite for operations-intent parsing and execution.

---

## Testing strategy
### Unit tests
1. Operation schema validation.
2. Interpreter response parsing and rejection paths.
3. Schedule translation correctness (one-shot vs recurring vs daily time).

### Integration tests
1. NL request -> proposal -> apply -> verify cycle.
2. Ambiguous request -> blocked/proposal with clarifications.
3. Risky operations -> approval required behavior.

### Safety tests
1. Unknown operation type rejected.
2. Missing required fields rejected.
3. Conflicting schedule fields normalized safely.

---

## Hidden delegation behavior
Default behavior:
1. User says: "Simon, move this cron job to next Tuesday noon."
2. Primary assistant delegates internally to `system-configuration-agent`.
3. User receives a normal Simon response with outcome and verification, without explicit subagent narration.

Exception:
1. If user asks for internal execution details, reveal delegation path transparently.

---

## Files introduced/updated in this phase
1. `.claude/agents/system-configuration-agent.md` (new)
2. `src/universal_agent/main.py` (routing guidance + skill-awareness mapping)
3. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/25_System_Ops_Subagent_Architecture_And_Implementation_Plan_2026-02-12.md` (new)

# 001 URW Evidence + Adapter Guide

Purpose: Educational reference explaining **how evidence is captured and verified** in URW and **how the adapter extracts artifacts/receipts** from the Universal Agent runtime.

## 1) Evidence Capture Flow (High Level)
1. **URW orchestrator** executes a single task (phase) using a fresh agent instance.
2. The **adapter** returns a structured `AgentExecutionResult`:
   - `artifacts_produced` (files)
   - `side_effects` (receipts like Gmail message IDs)
   - `learnings`, `failed_approaches`, `tools_invoked`
3. The **state manager** stores:
   - Artifacts in `.urw/artifacts/`
   - Side effects in the DB (idempotency keys)
4. The **evaluator** checks task completion using evidence types.
5. A **verification findings artifact** is written for audit.

## 2) Evidence Types (Standard)
- **Receipt**: provider ID or success receipt (e.g., Gmail message ID)
- **Artifact**: file output (PDF, JSON, report)
- **Hybrid**: receipt + artifact
- **Programmatic**: deterministic checks (tests/lint)

## 3) Where Evidence Lives
**Files:**
- `.urw/artifacts/` — file outputs
- `.urw/verification/verify_<task>_<iter>.json` — verification findings

**Database (SQLite):**
- `artifacts` table
- `side_effects` table (idempotency + receipts)
- `verification_findings` table

Reference: `src/universal_agent/urw/state.py`

## 4) Verification Findings Artifact
Each iteration writes a JSON record like:
```json
{
  "verification_id": "verify_task_001_1",
  "task_id": "task_001",
  "iteration": 1,
  "status": "pass",
  "evidence_type": "hybrid",
  "evidence_refs": ["report.md", "gmail:123"],
  "summary": {
    "evaluation": {"overall_score": 1.0, "is_complete": true},
    "outcome": "success",
    "execution_time_seconds": 12.3
  },
  "timestamp": "2026-01-16T16:44:00Z"
}
```

## 5) Adapter Extraction (UniversalAgentAdapter)
The adapter runs **one URW phase** using `UniversalAgent.run_query()` and collects:

### 5.1 Artifacts
- **Work products** emitted via `EventType.WORK_PRODUCT` are treated as artifacts.
- **File writes** are detected from tool calls (e.g., Write/append tools).

### 5.2 Receipts (Side Effects)
- Tool results are scanned for delivery actions.
- Example: Gmail send tool produces a message ID → stored as a receipt:
  - `side_effects`: `{ type: "email_sent", key: "gmail:123", details: { message_id: "123" } }`

### 5.3 Idempotency
- Each receipt uses a stable `key` to prevent duplicate sends on retries.

Reference: `src/universal_agent/urw/integration.py`

## 6) Why This Matters
- **Verification** becomes predictable and auditable.
- **Long runs** can resume without repeating emails or actions.
- The system can enforce completion promises using **real evidence**, not just text output.

## 7) Quick Trace
- Adapter → `AgentExecutionResult`
- Orchestrator → registers artifacts + side effects
- Evaluator → validates completion
- State manager → writes verification findings

## 8) Pointers
- Orchestrator: `src/universal_agent/urw/orchestrator.py`
- Adapter: `src/universal_agent/urw/integration.py`
- State manager: `src/universal_agent/urw/state.py`

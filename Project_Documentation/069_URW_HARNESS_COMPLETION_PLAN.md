# 069: URW Harness Completion Plan (Context Handoff + Phase Continuation)

**Date:** 2026-01-16  
**Status:** Active Plan  
**Owner:** URW Harness Team

---

## 1) Purpose
Establish a single, current source of truth for finishing the URW harness with reliable **phase handoff**, **context ingestion**, **context injection**, and **durable progress tracking** across restarts and re-plans.

---

## 2) Where We Stand (Current State)
**Working components**
- URW Orchestrator + State Manager (SQLite) persists tasks, artifacts, side effects, verification findings.
- Harness verification pipeline (mission.json) can mark tasks for retry and restart.
- Durable runtime DB + tool ledger provides idempotency and replay safety.
- Decomposer templates are in place and already use explicit artifacts + binary checks.

**Confirmed gaps**
- No explicit, prompt-ready **handoff artifact** for phase continuation or mid-phase resume.
- No enforced verification of handoff/checkpoint presence.
- Context injection does not explicitly ingest phase checkpoint artifacts.

---

## 3) Design Decisions (Locked In)
1) **Hybrid storage model**
   - **SQLite = canonical execution state** (tasks, side effects, receipts, iterations).
   - **JSON = prompt-ready handoff** (phase checkpoints for agent context).

2) **Handoff is mandatory**
   - Every phase must emit a `handoff.json` (or `phase_checkpoint.json`).
   - Verification should fail if handoff is missing when a phase is marked complete.

3) **Phase list is canonical in SQLite**
   - Task plan persists in URW DB and is mirrored to JSON (`task_plan.json`, `mission.json`).

---

## 4) Required Capabilities to Reach Completion
### 4.1 Handoff Artifact (Phase Checkpoint)
**Goal:** Ensure resumable context when an agent restarts mid-phase or between phases.

**Minimum schema**
```json
{
  "phase_id": "phase_3_synthesize",
  "status": "completed",
  "inputs": ["refined_corpus.md"],
  "outputs": ["analysis.md"],
  "checks": {
    "file_exists:analysis.md": true,
    "min_word_count:analysis.md:600": true
  },
  "notes": "Key findings summarized; report drafting can begin.",
  "next_phase": "phase_4_execute"
}
```

### 4.2 Context Ingestion + Injection
**Goal:** New agents always resume with the right context.

**Context sources to inject (priority order)**
1) `handoff.json` (most recent phase checkpoint)
2) Artifacts from dependencies (URW state)
3) Failed approaches + learnings (URW state)
4) Actions already taken (side effects)

---

## 5) Implementation Plan (Step-by-Step)
### Step 1 — Add handoff writer (URW State Manager)
- Write `handoff.json` (or `phase_checkpoint.json`) to `.urw/verification/` or `.urw/artifacts/`.
- Include phase inputs/outputs, checks, and next phase.

### Step 2 — Require handoff in verification
- If phase is marked complete without `handoff.json`, verification fails.
- This forces resumable context to exist before completion is accepted.

### Step 3 — Inject handoff into new agent context
- Update `generate_agent_context` to include the latest handoff artifact.
- Prioritize handoff content above all other context.

### Step 4 — Ensure phase list is mirrored to JSON
- Keep `task_plan.json` and `mission.json` updated after every task update.
- SQLite remains canonical; JSON is for human + harness consumption.

---

## 6) Verification Checklist (Phase Completion)
- [ ] All required artifacts exist.
- [ ] Binary checks pass.
- [ ] Side effect receipts recorded (if applicable).
- [ ] `handoff.json` written and valid.

---

## 7) Milestones
1) **Phase Handoff Writer** implemented and tested.
2) **Verification Enforced** (handoff required).
3) **Context Injection** updated to ingest handoff.
4) **Template Coverage** updated to include handoff outputs.

---

## 8) Dependencies & Notes
- Durable tool ledger already handles idempotency; do not duplicate this logic.
- Handoff artifacts should be used only for context and not replace SQLite task state.

---

## 9) Open Questions
- Should handoff schema live as a separate versioned spec doc?
- Should we include a compact, summarized “last N steps” for partial-progress resumes?

---

## 10) References
- `URW/README.md`
- `Project_Documentation/031_LONG_RUNNING_HARNESS_ARCHITECTURE.md`
- `Project_Documentation/062_CONTEXT_STORAGE_DURABILITY_SUMMARY.md`
- `Project_Documentation/067_DESIGN_ISSUES_FOR_HARNESS.md`

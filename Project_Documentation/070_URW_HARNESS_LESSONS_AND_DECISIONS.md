# 070: URW Harness Lessons Learned + Decisions (2026-01-16)

**Date:** 2026-01-16  
**Status:** Active  
**Purpose:** Capture lessons learned, decisions made, and forward-looking guidance to keep harness work aligned.

---

## 1) Lessons Learned (So Far)
1. **Tool router ≠ decomposer**
   - Composio search tools are best at *tool discovery*, not full phase planning.
   - Phase structure must come from URW decomposition.

2. **Phase success must be evidence-driven**
   - Artifacts + receipts are required to prevent “partial completion” deadlocks.

3. **Context window limits are normal**
   - Phases must be sized to complete within one iteration or cleanly resume.

4. **Restarting without checkpoint loses progress**
   - Partial phase progress needs a handoff artifact to make resumes deterministic.

5. **Idempotency must live below the agent**
   - The durable tool ledger (SQLite) is the correct place to prevent duplicate side effects.

---

## 2) Key Decisions
1. **Hybrid persistence model**
   - **SQLite = canonical execution state**
   - **JSON = prompt-ready handoff**

2. **Handoff artifacts are mandatory**
   - Verification must fail if `handoff.json` is missing for completed phases.

3. **Phase list stored in URW DB**
   - JSON is a mirror for humans + harness.

---

## 3) Known Gaps
- Handoff writer not yet enforced.
- Context injection does not yet load handoff artifacts.
- Verification does not yet require phase checkpoint outputs.

---

## 4) Immediate Next Actions
1. Implement `handoff.json` writer in URW state manager.
2. Add verification rule requiring handoff artifact for completed phases.
3. Inject handoff content into new agent context.
4. Update decomposition templates to include handoff output requirements.

---

## 5) Evaluation Criteria
- No partial phases marked complete without a checkpoint.
- Restarted agent can resume a mid-phase task without duplication.
- Side-effects remain deduped by ledger.

---

## 6) References
- `Project_Documentation/069_URW_HARNESS_COMPLETION_PLAN.md`
- `Project_Documentation/031_LONG_RUNNING_HARNESS_ARCHITECTURE.md`
- `Project_Documentation/062_CONTEXT_STORAGE_DURABILITY_SUMMARY.md`
- `Project_Documentation/067_DESIGN_ISSUES_FOR_HARNESS.md`

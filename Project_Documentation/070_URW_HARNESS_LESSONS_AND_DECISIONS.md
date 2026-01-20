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

## 6) Robustness: Idempotency & Directory Mismatches (2026-01-19)

### The Issue: Idempotency Blocking Retries
**Observation:**
The agent failed to execute `finalize_research` correctly (due to directory issues) but was blocked from retrying the tool call.
- The system correctly flagged the second attempt as **Idempotent** (duplicate call) because `finalize_research` is classified as a Side-Effect tool (`local`) with `REPLAY_EXACT` policy.
- This prevented the agent from correcting its mistake (e.g., pointing to the right directory) if it used the same arguments, or simply re-running it after fixing the environment.

**Solution: Add `retry_id` Argument**
- **Action:** Modify `finalize_research` (and underlying `refine_corpus_programmatic`) to accept an optional `retry_id: str` argument.
- **Why:** This allows the agent to intentionally bypass the `REPLAY_EXACT` lock by providing a unique value (e.g., current timestamp or "retry_1"), making the tool call distinct in the ledger.
- **Status:** Recommended for next sprint.

### The Issue: Search Tool Directory Mismatches
**Observation:**
The agent improvised (hallucinated) the `refined_corpus.md` content because it couldn't find the raw search results.
- `COMPOSIO_SEARCH` (or equivalent) saved files to a generic `Main Session` directory.
- `finalize_research` expected files in a task-specific directory (likely `Phase 1`).
- The agent lacked the usage of plumbing to move files or direct the search tool to the correct output path.

**Solution: Explicit Output Paths & Robust Discovery**
- **Action 1:** Update `search_web` / `composio` tool wrappers to accept an explicit `output_dir` or `workspace_path` argument to force saving in the correct context.
- **Action 2:** Improve `finalize_research` to accept a flexible input path or perform a recursive search for compatible files if the default path is empty.
- **Status:** Recommended for next sprint.

---

## 7) References
- `Project_Documentation/069_URW_HARNESS_COMPLETION_PLAN.md`
- `Project_Documentation/031_LONG_RUNNING_HARNESS_ARCHITECTURE.md`
- `Project_Documentation/062_CONTEXT_STORAGE_DURABILITY_SUMMARY.md`
- `Project_Documentation/067_DESIGN_ISSUES_FOR_HARNESS.md`

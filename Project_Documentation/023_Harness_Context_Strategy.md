# Proposal: Harness Context Continuity Strategy (v2)

**Current Status**: Verified.
The current harness implements a "Hard Reset" strategy ("Window Compaction").
- **What is preserved**: File system state (artifacts, logs).
- **What is injected**: A list of *paths* to prior session directories.
- **What is lost**: All semantic context, decision history, and "train of thought" from previous phases.

**Risk**: The agent in Phase N is "blind" to the *content* of Phase N-1 unless it explicitly reads the files. It lacks the "big picture" of *why* certain decisions were made, potentially leading to disjointed or contradictory outputs across phases.

---

## Proposed Solution: Semantic Handoffs

We should implement a **"Summarize & Inject"** pattern. This gives the agent the best of both worlds: a fresh, efficient context window, but with the "institutional memory" of the project preserved.

### 1. New Artifact: `phase_handoff.md`
At the end of each phase (in `harness_orchestrator.py`), we explicitly task the agent (or a separate helper call) to generate a standardized handoff document.

**Structure of `phase_handoff.md`**:
```markdown
# Phase 1 Handoff: AI Developments 2025
## Status
Completed Successfully.

## Key Outcomes
- Identified top 3 trends: Agentic AI, Gigawatt Datacenters, Regulatory alignments.
- Created report: `AI_Developments_2025.html`

## Critical Decisions
- Decided to exclude "consumer robotics" due to lack of credible sources.
- Focused heavily on "open source" per user preference found in search results.

## Notes for Next Phase
- Phase 2 (2026 Trends) should pick up on the "Regulatory alignment" thread.
- WARNING: Do not duplicate the "Agentic AI" section; focus on *evolution* not definition.
```

### 2. Updated Injection Logic
Modify `harness_helpers.py:build_harness_context_injection` to dynamicallly read these files.

**Current Prompt Injection**:
> "Prior work: Sessions at paths: /.../session_phase_1"

**New Prompt Injection**:
> "## Project History (Context Summary)
> ### Phase 1: AI Developments 2025
> - **Outcomes**: Identified Agentic AI & Gigawatt centers as key. Created `AI_Developments_2025.html`.
> - **Hand-off Note**: Phase 2 should focus on the *evolution* of Agentic AI, not re-defining it.
>
> *(Full files available at /.../session_phase_1)*"

### 3. Implementation Plan
1.  **Add `summarize_phase` step** to `HarnessOrchestrator._execute_phase`.
    - Run a quick LLM call after verification passes.
    - Save result to `.../session_phase_X/phase_handoff.md`.
2.  **Update `harness_helpers.py`**.
    - Function `get_prior_context_summaries(prior_paths)` references these files.
    - Inject text into the system prompt.

## Recommendation
This approach ("Semantic Handoffs") maintains the stability benefits of Window Compaction (no infinite token growth) while solving the "Blind Agent" problem. I recommend implementing this immediately for the next run.

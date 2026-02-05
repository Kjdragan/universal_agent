# Experimental Feature: Semantic Handoff (Phase Summaries)

> [!IMPORTANT]
> **Status**: Experimental (Added 2026-01-28)
> **Goal**: Improve multi-phase agent continuity without context window bloat.

## Problem
In the harness architecture, each phase starts with a "fresh" agent (context compacted). While this prevents hallucination and token bloat, it causes "Context Blindness"—Phase 2 agent knows *where* Phase 1 files are, but not *what* happened, leading to disconnected workflows.

## Solution: Semantic Handoffs
We implemented a mechanism where the agent "talks to its future self".

### 1. Generation (`harness_helpers.generate_phase_summary`)
At the successful conclusion of a phase (after verification passes), the orchestrator triggers a special prompt:
> "Summarize what was accomplished. List key decisions. Mention important artifacts."

The agent generates a markdown summary which is saved to:
`.../harness_ID/session_phase_X/phase_handoff.md`

### 2. Injection (`harness_helpers.build_harness_context_injection`)
When starting the *next* phase, the system scans all prior session directories for `phase_handoff.md` files.
These are appended to the System Prompt under a new section:
`## 📜 Prior Phase Summaries`

## Hypothesis
- **Pros**: The agent will have "episodic memory" of the project's evolution.
- **Cons**: Bad summaries might distract the agent or bias it towards previous decisions that should be revisited.

## Verification
Monitor execution logs for:
1. `✅ Generated Phase Handoff: ...` (End of phase)
2. `## 📜 Prior Phase Summaries` appearing in the prompt (Start of next phase)

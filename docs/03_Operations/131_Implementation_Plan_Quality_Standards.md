# 131 — Implementation Plan Quality Standards

**Last updated:** 2026-05-13 (extracted from CLAUDE.md trim)

Implementation plans are decision documents — they must make complex system flows understandable at a glance. Text-only explanations are insufficient for this codebase's multi-agent architecture. A wrong mental model leads to flawed design decisions; visual artifacts catch the misunderstandings that paragraphs hide.

## Every implementation plan MUST include

1. **Mermaid sequence diagrams** for any multi-component interaction (email flows, task dispatch chains, agent delegation). Show the actual participants, message payloads, and decision points.
2. **Mermaid flowcharts** for routing/branching logic (e.g., "which inbox → which agent → which action").
3. **Code-verified citations** with `file:///path#Lnnn` links to the actual source lines that support each claim. Do not describe system behavior without pointing to the code that implements it.
4. **Summary tables** for change impact ("What Changes vs. What Stays"), communication patterns, or comparison of alternatives.
5. **Concrete code snippets** for every proposed modification — show the actual function signatures, new helper functions, and prompt text changes. Pseudocode is not sufficient.
6. **Phase-by-phase breakdown** with clear boundaries between config-only changes, code changes, and prompt changes. Each phase should be independently verifiable.

## Why this matters

This system has complex multi-agent pipelines where the gap between "how it looks in prose" and "how it actually flows in production" is wide. Past plans that lacked diagrams shipped on wrong mental models — see the v2 ClaudeDevs intel postmortem in [`130_Production_Verification_Rules.md`](130_Production_Verification_Rules.md) for the failure mode.

A plan without a sequence diagram for a multi-component flow is not a plan; it's a wish list.

## Companion standards

- `CLAUDE.md` § "Documentation Maintenance — MANDATORY" — when and how to update canonical docs in the same PR as the code change.
- `CLAUDE.md` § "Code-Verified Answers" — the citation discipline that plan claims must satisfy.
- `docs/03_Operations/130_Production_Verification_Rules.md` — the verification cadence that every implementation plan must terminate in.

# Memory System Status and Letta Path

This document summarizes where we are with the in-house Memory System PRD,
why we pivoted to Letta for now, and the small compatibility shim we added to
make Letta work reliably in our runtime. This is intended as a current-state
summary so we can revisit our own implementation when the time is right.

## 1. In-house Memory System (PRD) Status

We designed and partially implemented a first-party memory system:
- Core Memory (SQLite) and Archival Memory (ChromaDB) exist in `Memory_System/`.
- PRDs and implementation plans are documented in:
  - `ACTIVE_MEMORY_MANAGEMENT_PRD.md`
  - `ACTIVE_MEMORY_IMPLEMENTATION_PRD_V2.md`
  - `002_ROADMAP_PRD.md`
- We have working local APIs and tests, but we did not finish full integration
  into the primary agent runtime and sub-agent lifecycle.

Summary: the system is “very close” but not fully production-wired.

## 2. Why We Pivoted to Letta (for Now)

We decided to evaluate Letta because it provides:
- Fast time-to-value for memory capture and injection.
- Managed persistence and retrieval with minimal changes to the agent loop.
- Sub-agent memory separation (when names are valid and blocks are available).

This gives us a usable memory layer today while we keep the PRD path warm.

## 3. Letta Compatibility Shim (“Monkey Patch”)

We added a small compatibility shim to ensure Letta works cleanly:
- **Sub-agent name normalization** to avoid invalid characters (e.g., `:`),
  which were rejected by the Letta SDK/API. Names are sanitized so Letta
  will create and index those agents consistently.
- **Explicit block setup** for `recent_queries` and `recent_reports` so the
  Letta agent has stable block names for our workflows.
- **CLI visibility** for sub-agent Letta injection (log line on inject).

This is intentionally minimal and reversible, but it stabilizes Letta in the
current runtime.

## 4. Current Operating Mode

- Letta is the active memory system for now.
- The in-house Memory System is kept in the repo and documented, but is not
  fully enabled in the main runtime.
- We will continue to validate Letta behavior in durability and agent tests.

## 5. Criteria for Returning to the In-house Plan

We will revisit the PRD implementation when:
- We need tighter data locality or deterministic retrieval guarantees.
- We want full control over memory retention, pruning, and storage costs.
- Letta behavior or limits become a blocker.

Until then, Letta remains the pragmatic choice, with our PRD as the fallback.

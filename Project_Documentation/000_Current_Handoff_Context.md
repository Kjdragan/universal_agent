# Handoff Context: Universal Agent Architecture Refactor

**Date:** 2026-01-23
**Status:** Architecture Analysis Phase

## 📍 Where We Are
We have successfully stabilized the **Universal Agent** execution pipeline across the Terminal (CLI) and Web UI.
-   **Fixed:** Web UI agent now correctly delegates research tasks (synced system prompt from CLI).
-   **Fixed:** Internal MCP logs are now visible in the Web UI.
-   **Enhanced:** Web UI "Internal Logs" panel now has a verbosity toggle (DEBUG/INFO).

## 🚧 The Next Challenge: Unification & Multi-Channel
The user correctly identified that maintaining two entry points (`main.py` vs `server.py`) with duplicated prompt logic is fragile. They also referenced **ClawdBOT** as a target architecture style to emulate.

**Goal:** Refactor `universal_agent` towards a truly "universal" architecture that relies on a single core logic engine serving multiple interfaces (Terminal, Web, Slack, Telegram).

## 📂 Relevant Context
1.  **Reference Reference:** `lrepos/clawdbot` (User wants us to analyze this repo).
2.  **Strategy Doc:** `Project_Documentation/010_clawdbot_integration_phasing.md` (Already outlines Phase 2: "Plumbing & Event Bus").
3.  **Core Issue:** Currently `main.py` (CLI) and `server.py` (Web) are loosely coupled forks. We need to unify them into a streamlined service architecture.

## 📝 Immediate Next Tasks
1.  **Analyze Clawdbot**: Perform a deep dive into `lrepos/clawdbot` to understand how it handles multi-channel support and event dispatching.
2.  **Gap Analysis**: Compare `universal_agent` vs `clawdbot` architectures. Look for:
    -   Event Bus implementation.
    -   Plugin/Skill loading patterns.
    -   Separation of "Core Agent" from "Interface".
3.  **Proposal**: Create a plan to refactor `universal_agent` to use a "Unified Config Factory" and disjoint "Interface Adapters" (CLI Adapter, Web Adapter, Bot Adapter).
    -   *Constraint:* Minimize disruption to the currently working Web UI.

## 🔑 Key Files
-   `src/universal_agent/main.py`: Current CLI entry point (contains the "Golden Path" logic).
-   `src/universal_agent/api/server.py`: Current Web entry point.
-   `src/universal_agent/agent_setup.py`: The newly patched shared config (good start, but needs to go further).
-   `Project_Documentation/010_clawdbot_integration_phasing.md`: The roadmap.

# 013: Handoff Briefing for Codex 5.2 - Architecture Refactor

**To:** Codex 5.2
**From:** Antigravity (Previous Agent)
**Date:** 2026-01-23
**Subject:** Mission Briefing: Unifying Universal Agent via the Gateway Pattern

---

## 🚀 The Mission

We are preparing to refactor **Universal Agent** (UA). Currently, UA has a **world-class execution engine** (Claude SDK + Durability + URW Harness) but suffers from **"Bifurcated Plumbing"**—the Terminal (`main.py`) and Web UI (`server.py`) are separate, loosely coupled applications.

**Your Goal:** Assess how to refactor UA into a **Unified, Interface-Agnostic Gateway Architecture** using **Clawdbot** as a reference model, *without* compromising our powerful execution capabilities.

---

## 🧐 The Landscape

### 1. The Asset: Universal Agent (UA)
*   **Strengths:**
    *   **URW Harness (`src/universal_agent/urw/`)**: A "Supervisor" system that interviews users, creates multi-phase plans, and creates durability checkpoints for long-running tasks.
    *   **Execution Durability (`src/universal_agent/durable/`)**: SQLite-backed state that tracks every tool call and token usage, allowing crash recovery at the atomic step level.
    *   **Logfire Integration**: Deep observability into agent thoughts.
*   **Weakness:**
    *   **The "Split Brain"**: The logic for running an agent is duplicated between the CLI loop and the WebSocket server. Adding a feature to one doesn't give it to the other.

### 2. The Reference: Clawdbot
*   **Strengths:**
    *   **The Gateway (`src/gateway/`)**: A central traffic cop that receives normalized messages from *any* channel (Telegram, Slack, Web).
    *   **The Event Bus (`src/gateway/server-node-events.ts`)**: The core logic *emits* events (Thoughts, Text) rather than printing to stdout.
    *   **Channels (`src/channels/`)**: Dumb adapters that translate external APIs into internal Gateway events.

---

## 🧩 Key Architectural Concepts

As you investigate, verify and refine these architectural concepts we have identified:

### A. The Gateway Pattern
Move `UniversalAgent` logic behind a unified API.
*   **Current:** `main.py` -> `UniversalAgent` (Direct Call)
*   **Target:** `main.py` -> `Gateway Client` -> `Gateway` -> `UniversalAgent`
*   **Benefit:** The "Agent" doesn't care if the user is typing in a terminal or clicking buttons in a browser.

### B. The "Supervisor" Pattern (URW)
**Crucial Context:** Do not try to merge URW into the Agent.
*   **Observation:** The URW Harness is effectively a **"Meta-Client"**. It automates the user's role.
*   **Interaction:** URW should send high-level commands to the Gateway ("Execute Phase 1") and subscribe to the Event Bus to monitor progress, just like a human user would.

### C. State Separation
*   **Project State (`urw/state.db`)**: "Did we finish the 'Financial Analysis' phase?" (Belongs to URW).
*   **Execution State (`durable/runs.db`)**: "Did the last tool call crash?" (Belongs to the Gateway/Worker).

---

## 📂 Key Intelligence Files

Use these files to get up to speed quickly:

### 1. Analysis So Far
*   `Clawdbot_analysis/011_clawdbot_architecture_comparison.md`: (My comparison of the two systems).
*   `Clawdbot_analysis/012_urw_durability_integration.md`: (My deep dive into how URW fits).

### 2. Universal Agent (The Core)
*   `src/universal_agent/main.py`: The CLI entry point (note the `process_turn` logic).
*   `src/universal_agent/api/server.py`: The Web entry point (note the duplicated bridge logic).
*   `src/universal_agent/urw/orchestrator.py`: The Harness logic (how it drives the agent).
*   `src/universal_agent/durable/state.py`: The low-level crash recovery metrics.

### 3. Clawdbot (The Reference)
*   `clawdbot/src/entry.ts`: How it bootstraps.
*   `clawdbot/src/gateway/`: The event routing logic we want to emulate.
*   `clawdbot/src/channels/telegram/`: Example of a clean channel adapter.

---

## 🚦 Your Orders

1.  **Validate:** Read the files above. Do you agree with my assessment? Did I miss any critical dependencies in `agent_setup.py`?
2.  **The "Bag of Tricks":** Look for other Clawdbot features we might want (e.g., "Lanes" for concurrency control, "Sandboxing").
3.  **Draft the Plan:** The User wants a refactoring plan. You need to tell us *how* to move from `A` to `B` incrementally, ensuring we never break the existing Terminal workflow that the team relies on.

**Good luck, Codex 5.2. The architecture is in your hands.**

# The Soul Architecture

**Date:** 2026-01-28
**System:** Universal Agent
**Component:** Identity & Prompt Injection

## Overview

The "Soul" is the definitive source of truth for the Universal Agent's identity, personality, and core operational directives. It separates *who the agent is* from *how the agent runs*.

Prior to this architecture, system prompts were hardcoded in `main.py` or `agent_setup.py`, mixing plumbing constraints (e.g., "Use Composio tools") with behavioral traits (e.g., "Be helpful"). The Soul Architecture decouples these.

## The Persona: "Simon"

The default persona for the Universal Agent is **Simon**.

*   **Role:** Senior Staff Engineer.
*   **Trait:** Opinionated, Autonomous, High-Velocity.
*   **Anti-Pattern:** "Helpful Assistant" (Passive, ask-for-permission style).
*   **Motto:** "Solve the problem, don't just answer the question."

## Architecture

### 1. Storage (`prompt_assets/`)

The core assets live in:
`src/universal_agent/prompt_assets/`

*   **`SOUL.md`**: The primary markdown definition of the persona.
*   **`README.md`**: Documentation for the directory.

### 2. Injection Hierarchy

The agent loads the Soul at runtime using a 3-tier checks system. The first file found wins.

1.  **Session Override**: `<Active Workspace>/SOUL.md`
    *   *Use Case*: You want the agent to roleplay as a "Creative Writer" or "Legal Analyst" for a specific job without changing the codebase.
2.  **Global Standard**: `src/universal_agent/prompt_assets/SOUL.md`
    *   *Use Case*: The default "Simon" engineer persona.
3.  **Legacy Fallback**: `RepoRoot/SOUL.md`
    *   *Use Case*: Outdated deployments.

### 3. Implementation Details

*   **`AgentSetup`**: The library class `AgentSetup._build_system_prompt` checks the hierarchy and injects the content at the *very top* of the system prompt.
*   **`main.py` (CLI)**: The CLI entry point also performs this check (patched in `load_soul_context`) to ensure that even "Fast Path" (Simple) queries without full agent initialization still embody the persona.

## Usage

To change the agent's behavior for a specific task:
1.  Create a `SOUL.md` file in the task's workspace directory.
2.  Define the new persona (e.g., "You are a QA Tester...").
3.  Run the agent. It will auto-detect the override and log: `👻 Loaded Soul override from workspace`.

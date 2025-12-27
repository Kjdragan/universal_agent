# AgentCollege Architecture

**Goal**: Enable `universal_agent` to autonomously improve its capabilities, learn new skills, and refine its own behavior.

## 1. Core Principles (Adapted from DeepFetch)

1.  **Skill-as-a-Resource**: A "Skill" is a self-contained unit (Code + Documentation + Prompt).
2.  **Experience Loop**: The agent's history (Traces/Logs) is data for self-improvement.
3.  **Decentralized-ish**: Sub-agents (Swarm) handle specialized "Learning" tasks without blocking the main "Doing" agent.

## 2. Key Modules

### A. The Registrar (Professor Agent)
*   **Concept**: Uses the `.claude/skills/` SDK standard via the **Skill Creator** logic.
*   **Role**: The "Gatekeeper" of the system.
*   **Input**: Reads `[AGENT_COLLEGE_NOTES]` (The variable Scratchpad).
*   **Mechanism**:
    1.  Reviews "Suggestions" from the scratchpad.
    2.  **HITL Gateway**: Surfaces a "Graduation Proposal" to the User (e.g., "I suggest creating a specific skill for PDF handling").
    3.  **Action**: *Only upon approval*, usage `init_skill.py` to create the skill.

### B. The Critic (Self-Correction Loop)
*   **Mechanism**: **Push-based** via Logfire Webhooks.
*   **Action**: `LogfireFetch` receives `POST /webhook/alert`.
*   **Output**: Writes a "Correction Hypothesis" to **[AGENT_COLLEGE_NOTES]** (e.g. "Suggestion: Avoid tool X for PDF").
    *   *Safe*: Does NOT touch production `[SYSTEM_RULES]`.

### C. The Scribe (Auto-Memory)
*   **Trigger**: End of Session.
*   **Action**: Scans Logfire traces.
*   **Output**: writes "Fact Candidates" to **[AGENT_COLLEGE_NOTES]** for review.


## 3. Implementation Roadmap

1.  **Skill Architecture**:
    *   Standardize the `mcp_server.py` tool registration to dynamically load modules from a `Skills/` folder.
    
2.  **Use .claude/skills/**:
    *   Leverage the existing `discover_skills()` in `main.py`.
    *   Create `Skills/` integration test that writes a dummy `SKILL.md` and verifies the agent sees it.

3.  **Logfire Service Integration**:
    *   Connect the "Critic" loop to the existing `main.py` hooks (`UserPromptSubmit` / `PreToolUse`).
    *   If Logfire detects repeated failures, the Critic can update a `SKILL.md` to improve instructions.

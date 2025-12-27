# AgentCollege Architecture

**Goal**: Enable `universal_agent` to autonomously improve its capabilities, learn new skills, and refine its own behavior.

## 1. Core Principles (Adapted from DeepFetch)

1.  **Skill-as-a-Resource**: A "Skill" is a self-contained unit (Code + Documentation + Prompt).
2.  **Experience Loop**: The agent's history (Traces/Logs) is data for self-improvement.
3.  **Decentralized-ish**: Sub-agents (Swarm) handle specialized "Learning" tasks without blocking the main "Doing" agent.

## 2. Key Modules

### A. The Registrar (Skill Manager)
*   **Concept**: A local registry of available capabilities.
*   **Implementation**: A `Skills/` directory where each sub-folder is a skill.
*   **Discovery**: On startup, `main.py` scans `Skills/` and registers tools dynamically (similar to `Memory_System` integration).
*   **Auto-Generation**: An agent can write a new script to `Skills/new_skill.py`, valid it, and it becomes available next run.

### B. The Critic (Self-Correction Loop)
*   **Mechanism**: **Push-based** via Logfire Webhooks.
*   **Trigger**: Logfire Alert (SQL: `SELECT * FROM records WHERE exception IS NOT NULL`) -> Sends Webhook.
*   **Action**: `LogfireFetch` receives `POST /webhook/alert`, extracts trace ID, and wakes the Critic Agent.
*   **Output**: Prompt Patch or Rule Injection.
### C. The Scribe (Auto-Memory)
*   **Mechanism**: Queries **LogfireFetch Service** (`GET /traces/recent`).
*   **Trigger**: End of Session (or periodic).
*   **Action**: Scans the session transcript via the API.
*   **Logic**: "Did the user state a fact? Did I learn a new CLI flag?"
*   **Output**: Calls `archival_memory_insert()` autonomously.

## 3. Implementation Roadmap

1.  **Skill Architecture**:
    *   Standardize the `mcp_server.py` tool registration to dynamically load modules from a `Skills/` folder.
    
2.  **DeepFetch-style "Skill" Structure**:
    ```
    Skills/
      ├── video_processing/
      │   ├── tool.py (The @mcp.tool code)
      │   ├── instructions.md (How to use it)
      │   └── requirements.txt
    ```

3.  **The "Professor" Agent**:
    *   A specialized System Prompt for an agent whose *only* job is to read docs and write `Skills/*` code.

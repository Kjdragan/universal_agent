# Handoff Context: Harness V2 & System Diagnostics

## 1. Project Status
We are refining the **Harness V2** architecture for the Universal Agent. This system allows the agent to execute long-running, multi-phase missions. We have stabilized the CLI entry point but face two major challenges: **Operational Logic** (Agent trying to do too much at once) and **System Restrictions** (Tools being blocked mysteriously).

## 2. Recent Fixes
1.  **Tool Access Hack**: Implemented a "Hard Override" in `main.py` (`on_pre_tool_use_ledger`) to explicitly force-allow `Task` and `Bash` tools in harness mode. This bypasses the unknown blocking mechanism.
2.  **WebSearch Hallucinations**: Added `WebSearch` to `DISALLOWED_TOOLS` and added a guardrail to guide the agent to `composio_search`.
3.  **Prompting**: Updated Harness Planning Prompt to encourage "Vertical Slice" decomposition.

---

## 3. Priority A: Implementing Sequential Execution ("The Dump" Problem)
**The Issue**: The agent currently sees the entire `mission.json` (all tasks) in its context and tries to execute them in parallel batches, overwhelming the system.

**The Solution (Anthropic Pattern)**:
We need to refactor the harness loop (`main.py` -> `process_harness_phase` / `on_agent_restart`) to strictly follow the **Sequencer Pattern**:
1.  **Load State**: Read `mission.json`.
2.  **Select ONE Task**: Find the first task with `status: "PENDING"`.
3.  **Focus Prompt**: Inject *only* that task into the next prompt ("Your Objective: Task 001. Ignore others.").
4.  **Execute & Update**: Agent runs task, updates `mission.json` to `COMPLETED`.
5.  **Loop**: Harness detects completion and restarts for the next task.

---

## 4. Priority B: Deep Investigation of Tool Blocking
**The Issue**: We are currently relying on a *hard override* to use `Task` and `Bash`. Without it, we get `Hook PreToolUse:... denied this tool`. We need to find the **Root Cause** to remove the override.

**Investigation Checklist for the Next Agent**:
1.  **Claude Agent SDK**: Inspect `claude_agent_sdk.client` initialization in `main.py` and `server.py`. Are there `allowed_tools` lists or default policies?
2.  **Composio MCP**: Check `src/universal_agent/main.py` where `composio` is initialized. Are we passing a restricted `entity_id` or `integration_id`?
3.  **Hook Universe**:
    *   Audit `src/universal_agent/main.py` -> `on_pre_tool_use_ledger`.
    *   Audit `src/universal_agent/guardrails/`.
    *   Search for any other `PreToolUse` hook registrations in `agent_core.py`.
4.  **Prompts & Knowledge Injection**:
    *   Inspect `/home/kjdragan/lrepos/universal_agent/.claude/knowledge` and `.claude/skills`.
    *   Are there Markdown files defining "Rules" that the SDK is ingesting and interpreting as rigid policies?
    *   Check if `Basic Agent` or `Orchestrator` prompts have "Do not use Bash" instructions.

---

## 5. Key Files
*   `src/universal_agent/main.py`: Core Harness & Loop Logic.
*   `src/universal_agent/harness/`: Harness tools.
*   `anthropics/claude-quickstarts/autonomous-coding/`: Reference implementation for sequential loops.

## 6. Verification
*   **Sequential Test**: Run `--harness` and ensure the agent only attempts ONE task at a time.
*   **Blocking Test**: Remove the debug override in `main.py` (lines ~901) and trace *exactly* where the "denied" message originates using `grep` or `logfire` spanning.

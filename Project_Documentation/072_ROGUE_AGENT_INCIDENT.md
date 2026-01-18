# Incident Report: The "Rogue Script" Strategy

**Date:** 2026-01-17
**Topic:** Agent autonomy vs. System Observability
**Status:** Resolved (Strategy Defined)

## 1. The Incident
During a Phase 2 execution of the URW Harness (Task: "Gather Information"), the agent was tasked with performing comprehensive research.

Instead of sequentially visiting pages, the agent discovered and initialized the **`crawl_parallel` tool** to fetch data for 10+ URLs simultaneously.

### observed Behavior
1. Agent performed a Google Search.
2. Agent identified a need for efficiency (getting content from many results).
3. Agent correctly identified `crawl_parallel` as the tool for this job.
4. Agent executed the tool with a list of URLs.
5. **CRITICAL FAILURE**: The system environment was missing the `CRAWL4AI_API_KEY`.
6. **FALLBACK**: The tool fell back to "Local Mode", spawning a full Chrome instance for *each* URL.
7. **RESULT**: Resource exhaustion (RAM/CPU) and silent process kill.

## 2. Analysis

### The "Good" (Tool Discovery)
The agent correctly identified the most efficient tool definition available (`crawl_parallel`). It did not "go rogue" in the sense of writing unauthorized code; it used the tools provided to it.

### The "Bad" (Configuration Gap)
The incident revealed a gap in our environment configuration and guardrails:
*   **Missing Key**: The critical API key for cloud offloading was not loaded into the MCP server process.
*   **Safety Bypass**: The tool's fallback mechanism (Local Browser) was too aggressive for a batch of 10+ URLs on a standard machine.
*   **Observability**: The crash happened at the OS level (OOM), leaving no trace in the `trace.json` until we inspected the system logs.

## 3. The Solution Strategy

We will adopt a "Restrict & Enable" strategy. We must stop the uncontrolled behavior while supporting the underlying efficiency need.

### Part A: Stop the Bleeding (Guardrail)
**Mechanism:** System Prompt Update
**Goal:** Force the agent back to the "Happy Path" of using native tools.
**Implementation:**
Add a directive to `agent_core.py` system prompts:
> "Do not write Python scripts to call other tools or sub-agents. You must use the provided tools directly. If you need to run multiple actions, use the available batching tools or make multiple tool calls."

### Part B: Support the Need (Feature)
**Mechanism:** New Tool `batch_tool_execute`
**Goal:** Give the agent the "Parallel Power" it wanted, but within our controlled framework.
**Implementation:**
Create `BatchTool` in `src/universal_agent/tools/batch.py`:
- **Input:** List of tool calls (name + arguments).
- **Execution:** The system iterates through the list (sequentially or parallel).
- **Output:** A structured list of results.
- **Observability:** The harness logs each sub-call individually, maintaining full visibility.

## 4. Plan of Action
1.  **Implement Guardrail:** Update System Prompt immediately.
2.  **Verify:** Re-run the research task to ensure it uses native tools.
3.  **Implement Feature:** Build `batch_tool_execute` for future efficiency.

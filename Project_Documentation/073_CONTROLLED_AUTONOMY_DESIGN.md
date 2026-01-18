# Design: Controlled Autonomy & The Happy Path
**User Goal:** Harness the creative/efficiency efficiency of advanced agent behaviors (like parallel crawling) while maintaining system control, observability, and deterministic outcomes.

## Core Tension
- **Autonomy:** Agents seek optimal paths (e.g., fetching 20 URLs at once).
- **Control:** The Harness needs durability, resume capability, and observability (trace logs).
- **Conflict:** Highly efficient tools (like `crawl_parallel`) can consume massive resources if not properly configured (e.g., missing API keys causing local fallout), leading to silent crashes.

## Strategy: "Pave the Desire Paths"
Instead of blocking efficient behaviors, we should **sanction and wrap** them ensuring the *environment* supports them.

### Core Principle: The Justification Threshold
Autonomy is not a blank check. The Agent should only deviate from the standard path (Native Tools) if:
1.  **Significant Advantage:** The deviation provides >2x speedup or solves a blocker.
2.  **No Native Alternative:** Existing tools cannot reasonably perform the task.
3.  **Rationalized:** The agent must explicitly state *why* it is deviating in its "Thought" trace.
*If these criteria are not met, the Critic should reject the deviation.*

### Tier 1: Sanctioned Native Tools (The "Fast Lane")
The most direct solution is to provide native tools that match the agent's desired patterns, but with strict configuration checks.
*   **Pattern:** "I want to crawl 20 URLs."
*   **Old Risk:** Agent launches 20 concurrent local browsers -> OOM Crash.
*   **New Design:** `crawl_parallel` with **Backend Awareness**.
    *   **Check:** Does `CRAWL4AI_API_KEY` exist?
    *   **Yes:** Allow 20 concurrent (Cloud Mode).
    *   **No:** Force throttle to 3 concurrent (Local Mode).
    *   **Result:** Agent gets maximum safe efficiency without "thinking" about hardware limits.

### Tier 2: Path Integrity (Reintegration)
The user priority is **Outcome Validity**, not **Execution Safety** (Sandboxing).
If an agent "goes rogue" (e.g., writes a bash script to generate 50 files), we assume the execution is fine (or if it crashes, it crashes).
The critical piece is **Getting Back on Track**.

#### The "Reintegration" Pattern
Instead of wrapping the *code* (SafeScript), we wrap the *checkpoint*.
1.  **Phase-Based Gates**: Each Harness Phase (e.g., Gather) must have a `finalize` or `sync` tool.
2.  **State Reconciliation**: This tool scans the workspace for "Dark Matter" (files created outside the harness's knowledge).
3.  **Adoption**: It registers these artifacts into the `state.db` and Artifact Manifest, making them available for downstream tasks.

*Example:* `finalize_research` currently scans `search_results/` and indexes *all* markdown files, whether they came from `crawl_parallel`, a manual `curl`, or a rogue script. This is the model to replicate.

### Tier 3: Just-in-Time Promotion
If an agent *tries* to go rogue (e.g., writes a raw script), the System Prompt or a "Critic" should check:
*   "Will this script produce standard artifacts (files in known dirs)?" -> **Allow**.
*   "Will this script modify hidden state or external systems?" -> **Block**.

If an agent *tries* to go rogue (e.g., writes a raw script), the System Prompt or a "Critic" intercepts:
*   *Interceptor:* "I see you are writing a script to crawl 50 urls. Please use `batch_tool_execute` instead for better performance and safety."
*   This turns "Encouragement" into "Education".

## Proposed Architecture for "Tamed Efficiency"

1.  **The "Meta-Tool" Interface**:
    *   Expose a `propose_optimization` tool.
    *   Agent: "I want to run a complex data extraction that requires looping 100 files. Doing this serially via `read_file` will be too slow."
    *   Harness (Auto-Response): "Approved. Use `run_safe_batch_script` with the following template..."

2.  **Safety Guardrails for Autonomy**:
    *   **Timeouts:** Strict limits on custom script execution time.
    *   **Resource Limits:** RAM/CPU caps on the sandbox.
    *   **FS Access:** Sandbox restricted to `workspace_artifacts/`.

## Why `BatchTool` is the Right First Step
The `BatchTool` implemented in Track 4.2 essentially acts as a primitive Tier 2 solution. It allows "script-like" flows (loops) without actual code generation.
**Next Evolution:** Allow the `BatchTool` to accept *logic* (e.g., "if X then Y") or simply move to a safe Code Interpreter model.

## Recommendation for Next Iteration
1.  **Keep the Guardrails:** Keep the "No Raw Scripts" rule for now.
2.  **Monitor Deviations:** If the Agent struggles or complains about slowness, that is a signal to build a new specific tool (e.g., `ParallelAnalyze`).
3.  **Implement `SafeScript` (Future):** Eventually, replace the absolute ban with a `SafeScript` tool that runs python code in a containerized environment where we *can* guarantee observability.

## Durability & Recovery
- **Sanctioned Scripts:** If we wrap the script in a tool, the *Harness* handles the retry logic.
- **Crash Recovery:** If the script crashes the interpreter, the Harness (running in a separate process/thread) catches the exception and marks the tool call as failed, allowing the Agent to try a different approach (e.g., "Script failed, I will fall back to serial reading"). This preserves the "Deterministic Outcome" even if the efficient method fails.

## Nuanced Presentation & Encouragement Strategy

To move from "Blocking" to "Guiding", we need to change how options are presented to the agent.

### 1. The "Fast Lane" Prompting (Encouragement)
Instead of "Prohibited", we frame it as "Efficiency Tiers":
> "To complete tasks, choose the most efficient method:
> *   **Tier A (Best for <5 items):** Direct tool calls (e.g., `read_file`).
> *   **Tier B (Best for >5 items):** Use `batch_tool_execute` for parallel processing.
> *   **Tier C (Complex Logic):** Use `submit_safe_script` to run custom Python logic in a sandbox."

### 2. Just-In-Time Guidance (Taming)
Implement a `PreToolUse` hook in the Harness to intercept raw script attempts (e.g., `Bash` calling `python myscript.py`).
*   **Trigger:** Detection of potentially unobservable `python` execution.
*   **Response (Not Error, but Guidance):** "It looks like you are trying to run a custom script. For this to be safe and observable, please wrap your code in the `submit_safe_script` tool so we can capture logs and resume if it fails."

### 3. Discouragement of Fragile Patterns
We discourage *unmanaged* complexity, not complexity itself.
*   **Discourage:** Writing files to disk just to execute them immediately. (Fragile, leaves artifacts).
*   **Encourage:** Passing code directly to the execution tool (Ephemeral, cleaner).

## Implementation Roadmap

1.  **Phase 1 (Done):** `BatchTool` for simple parallel actions. (Paved the most common path).
2.  **Phase 2 (Immediate):** Update System Prompt to "Sell" the `BatchTool` as the preferred efficiency method, rather than just warning against scripts.
3.  **Phase 3 (Medium Term):** Formalize "State Reconciliation".
    *   Ensure every phase has a `finalize_X` tool that scans for "rogue" artifacts.
    *   This ensures that even if an agent uses a hidden script, the *outputs* are captured and the workflow remains deterministic.
4.  **Phase 4 (Advanced):** "Autonomy Budget".
    *   Allow the agent to "spend" iterations on high-risk, high-reward scripts, but if they fail, force a fallback to the "Happy Path" (standard tools).

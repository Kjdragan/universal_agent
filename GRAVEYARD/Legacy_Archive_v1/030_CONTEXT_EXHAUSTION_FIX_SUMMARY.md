# Context Exhaustion Fix: The "Context Refresh" Strategy

**Date:** January 13, 2026
**Related Trace ID:** `019bb968b905e152f250e188bc6e525a`
**Status:** ✅ Fixed & Verified

## 1. The Problem: "Context Exhaustion" (Zero-Byte Write)

We encountered a critical failure mode in long-running research tasks. The Agent would successfully research and read vast amounts of data (200k+ tokens), but then fail to generate the final report file.

### Symptoms
*   **Zero-Byte Writes:** The `Write` tool would be called with empty content or correct parameters but result in an empty file.
*   **Model Degradation:** The model would attempt to call `Write`, but the output would be truncated or malformed due to the massive context load.
*   **"InputValidationError"**: The Claude SDK/API would consistently reject the final tool call.

### Root Cause
**Context Saturation.** The monolithic `report-creation-expert` sub-agent was doing too much:
1.  Search (Queries + Results)
2.  Crawl (Multiple pages of raw HTML)
3.  Reading (Processing 10+ massive MD files)
4.  **Generation** (Holding all the above in memory while trying to output a 10k char report)

By the time it reached step 4, the context window was either full or so noisy that the model couldn't effectively attend to the instructions for the `Write` tool.

## 2. The Solution: "Context Refresh" Strategy

We implemented a **Two-Phase Delegation Strategy** to enforce a hard context reset between "Gathering" and "Synthesis".

### Phase 1: The Gatherer (`research-specialist`)
*   **Role:** Search, Crawl, Filter.
*   **Action:** It runs searches and crawls URLs. Crucially, it **DOES NOT** attempt to write the final report.
*   **Handoff:** It calls `finalize_research`, which processes the raw JSON/HTML into clean Markdown files in `tasks/<id>/filtered_corpus/`.
*   **Exit:** It returns a summary pointing to `research_overview.md`.

### Phase 2: The Author (`report-writer`)
*   **Role:** Synthesis & Writing.
*   **Context State:** **FRESH**. It starts with a near-empty context window.
*   **Action:**
    1.  Reads `research_overview.md`.
    2.  Reads specific files from the processed corpus on-demand (RAG-style access).
    3.  Writes the report.
*   **Result:** Because it isn't burdened by the raw crawl data history, it has ample "cognitive room" to generate high-quality, long-form content.

## 3. Verification & Logfire Confirmation

We verified this fix in Session `20260113_161008`.

### Logfire Trace Analysis (`019bb968b905e152f250e188bc6e525a`)
The trace confirms the clean separation of concerns:

1.  **Main Agent** delegates to `research-specialist`.
2.  **Specialist** runs `COMPOSIO_SEARCH_NEWS` (x6) and `finalize_research`.
3.  **Specialist** returns (Context dumped).
4.  **Main Agent** delegates to `report-writer`.
5.  **Writer** starts (New sub-agent span).
6.  **Writer** calls `Write` (Payload: ~7KB html) -> **SUCCESS**.
7.  **Writer** calls `append_to_file` (x4) to add huge sections (Payloads: ~6-10KB each).

**Result:** A complete 30KB+ HTML report was generated without a single zero-byte error or truncation.

### The "Double Session" Bug
During verification, we also identified and fixed a bug where `main.py` was creating two session directories (`session_...08` and `session_...09`).
*   **Cause:** `main.py` created a directory, but then instantiated `UniversalAgent()` without arguments, causing the class to create a *second* default directory.
*   **Fix:** Updated `main.py` to pass the `workspace_dir` to the constructor: `UniversalAgent(workspace_dir=workspace_dir)`.

## 4. Key Learnings

1.  **Vertical Slicing is Mandatory for Long Tasks:** You cannot expect an LLM to hold "Research" and "Writing" in the same context window for complex tasks.
2.  **Sub-Agents as Context Boundaries:** Use sub-agents not just for specialization, but as a mechanism to **dump context**.
3.  **Hooks are Vital:** Our `PostToolUse` hooks (which detect zero-byte writes) were critical in identifying the problem, even though the solution required an architectural change.

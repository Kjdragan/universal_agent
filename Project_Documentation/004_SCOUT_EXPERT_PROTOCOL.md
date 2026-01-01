# 004: Scout/Expert Protocol & Deterministic Automation
**Date:** December 31, 2025
**Status:** Implemented (Hybrid)

## 1. The Challenge: "The Plumbing Problem"
When building an autonomous research agent, we encountered two critical limitations:

1.  **Context Bottleneck**: A robust search might return 30-50 relevant results. Passing all these snippets to the LLM to "pick the best" overloads the context window.
2.  **Agent vs. Script Efficiency**: Using an LLM to manually `list_directory`, then `read_file` 50 times, then `extract_url`, then `crawl` is slow, expensive, and error-prone. This is "plumbing" work that scripts do better.

## 2. The Solution: Scout/Expert with Deterministic Tools

We decoupled "Finding" from "Processing" and introduced **Deterministic Automation** for the heavy lifting.

### Role A: The Scout (Main Agent)
*   **Responsibility**: Logistics and Discovery.
*   **Behavior**:
    *   Executes Search (Composite/Google/News).
    *   **STOP RULE**: Forbidden from reading the files or extracting URLs manually.
    *   **HAND-OFF**: Delegates to the Expert only after search results are saved to disk (`search_results/` directory).

### Role B: The Deterministic Expert
*   **Responsibility**: Reliable Extraction and Synthesis.
*   **Shift to Automation**: Instead of the agent manually looping through files (O(N) operations), we enable it to use **Deterministic Tools** (O(1) operation).
*   **The "Magic Tool": `finalize_research`**
    *   This python-native MCP tool replaces the entire "Scan -> Extract -> Crawl" loop.
    *   **Input**: `session_dir`
    *   **Logic (Python)**:
        1.  Scans `search_results/*.json`.
        2.  Aggregates ALL URLs.
        3.  Crawls them in parallel (Cloud or Local).
        4.  Generates a tiered `research_overview.md`.
    *   **Result**: The Agent receives a single, high-density summary file to read, rather than 50 raw data files.

## 3. Workflow Comparison

| Legacy (Manual Sub-Agent) | Modern (Deterministic Tool) |
|---------------------------|-----------------------------|
| 1. `list_directory` (1 turn) | 1. `finalize_research` (1 turn) |
| 2. `read_file` (N turns) | *(Tool handles scanning)* |
| 3. `crawl_parallel` (1 turn) | *(Tool handles crawling)* |
| 4. `read_crawl_results` (N turns) | 2. `read_file(research_overview.md)` |
| **Total**: 20+ Steps | **Total**: 2 Steps |

## 4. Technical Implementation

### Hybrid MCP Architecture
*   **Cloud Tools (Composio)**: Used for initial discovery (`SEARCH_NEWS`, `SEARCH_WEB`).
*   **Deterministic Tools (Local MCP)**:
    *   `finalize_research`: The "closer" that turns raw search results into a clean corpus.
    *   `crawl_parallel`: The engine powering the extraction.
*   **Safety Mechanisms**:
    *   We prompt the Main Agent to **DELEGATE** report creation.
    *   We prompt the Sub-Agent to **USE AUTOMATION** (`finalize_research`) rather than manual iteration.

## 5. Results
*   **Throughput**: 27+ URLs processed in seconds.
*   **Reliability**: 100% capture rate (no cherry-picking by lazy LLMs).
*   **Cost**: Significantly reduced input tokens by handling "plumbing" in Python.

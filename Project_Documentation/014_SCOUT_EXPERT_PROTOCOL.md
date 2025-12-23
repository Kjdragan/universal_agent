# 014: High-Volume Research Architecture (Scout/Expert Protocol)
**Date:** 2025-12-23
**Status:** Implemented

## 1. The Challenge: "The Context Bottleneck"
When building an autonomous research agent, we encountered a critical limitation in the standard "One Agent Does It All" approach:

1.  **Search Volume**: A robust search might return 30-50 relevant results across multiple JSON files.
2.  **Context Window**: Passing 50 URLs (and their metadata) to the LLM to "pick the best ones" fills the context window and confuses the model.
3.  **Cherry-Picking**: The LLM, trying to be efficient, would arbitrarily pick ~4 URLs to read, ignoring the rest.
4.  **Premature Optimization**: The Main Agent would often try to "just answer the question" based on search snippets, resulting in a hallucinated or shallow report, completely bypassing the deep-scraping tool.

## 2. The Solution: Scout/Expert Protocol
We decoupled the "Finding" from the "Processing" using a strict delegation protocol.

### Role A: The Scout (Main Agent)
*   **Responsibility**: Logistics and Discovery.
*   **Behavior**:
    *   Executes Search (Composite/Google/News).
    *   **STOP RULE**: Forbidden from reading the files or extracting URLs.
    *   **HAND-OFF**: Passes the *Directory Path* (Location), not the Data (Content).
*   **Key Prompt Instruction**:
    > "HAND-OFF PROMPT: 'I have located search data in the `search_results/` directory. Please scan these files using list_directory, scrape ALL URLs, and generate the report.'"

### Role B: The Expert (Sub-Agent)
*   **Responsibility**: Infinite Extraction and Synthesis.
*   **Behavior**:
    1.  **Discovery**: Calls `list_directory("search_results/")` to find ALL JSONs.
    2.  **Aggregation**: reads every JSON file and compiles a master list of URLs (e.g., 27 URLs).
    3.  **Bulk Action**: Calls `crawl_parallel(urls=[...])` with the full list.
    4.  **Synthesis**: Reads all 27 scraped Markdowns and writes the final report.

## 3. Technical Implementation

### Hybrid MCP Architecture
*   **Cloud Tools (Composio)**: Used for `SEARCH_NEWS`, `SEARCH_WEB`, `GMAIL_SEND_EMAIL`.
*   **Local Tools (Stdio Server)**: Used for `list_directory`, `read_local_file`, `crawl_parallel`.
*   **Integration**: Both servers are mounted in `main.py`, giving the agent a unified toolset.

### Safety Mechanisms (Prompt Engineering)
To force this behavior, we had to continuously fight the model's desire to "be helpful immediately".

1.  **The Auto-Save Exception**:
    *   *Problem*: A global rule "Mandatory Auto-Save" forced the Main Agent to write a file immediately after searching.
    *   *Fix*: Added explicit exception: **"Do NOT use this for 'Reports'. Delegate Reports to the 'Report Creation Expert'."**

2.  **The Strict Handoff**:
    *   *Problem*: Main Agent would try to "help" by pasting URLs into the prompt, verifying only a subset.
    *   *Fix*: Enforced a specific "Magic String" prompt that contains NO URLs, forcing the Sub-Agent to look at the filesystem.

## 4. Results
*   **Throughput**: Increased from ~4 URLs/run to **27+ URLs/run** (verified in testing).
*   **Quality**: Reports are now sourced from full article content, not snippets.
*   **Reliability**: Elimination of "Main Agent Hallucinations" where it wrote reports without reading sources.

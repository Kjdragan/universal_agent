# Research Pipeline Evaluation: Split-Agent Delegation

**Date:** January 14, 2026
**Run ID:** session_20260114_113502
**Topic:** Russia-Ukraine War (Week of Jan 7-14, 2026)

## 1. Executive Summary
This evaluation documents the successful execution of the new **Split-Agent Research Pipeline**, where the monolithic research agent was decomposed into two specialized sub-agents:
1.  **Research Specialist**: Dedicated to gathering, filtering, and organizing data.
2.  **Report Writer**: Dedicated to synthesizing the corpus into a report.

The run demonstrated that this architecture significantly improves report quality and reliability compared to previous single-agent iterations.

## 2. Configuration & Workflow
- **Architecture**: 2-Step Delegation.
- **Writing Method**: **Iterative Append** (Report Writer generated sections one by one and used `append_to_file`).
- **Tools Used**:
    - `mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL` (Search)
    - `mcp__local_toolkit__finalize_research` (Crawl & Filter)
    - `read_research_files` (Batch Verification)
    - `Write` + `append_to_file` (Report Generation)

## 3. Run Results
### Research Phase (`research-specialist`)
- **Search**: Executed 20 parallel searches.
- **Corpus**: Processed 24 URLs.
- **Filtering**: Produced a highly curated "Filtered Corpus" of 4 high-quality sources (after deduplication and noise removal).
    - *Note:* Issues with Al Jazeera content filtering were identified and fixed post-run.

### Writing Phase (`report-writer`)
- **Strategy**: The agent successfully employed an **iterative writing strategy**, breaking the report into 10 distinct sections.
- **Context Management**: By splitting the agents, the Writer started with a fresh context window, allowing it to ingest the full research corpus without truncation.
- **Output**:
    - **Format**: HTML Report with embedded CSS.
    - **Length**: Comprehensive (approx 12KB text).
    - **PDF**: Successfully converted and emailed via `GMAIL_SEND_EMAIL`.

## 4. Quality Assessment
The resulting report was evaluated as **high quality**. The iterative structure allowed for deep coverage of specific sub-topics (e.g., "Energy Infrastructure", "Humanitarian Impact") without the model losing focus or hitting output token limits mid-generation.

> **User Feedback:** "It seems to be generating a very good quality reports this way... This is a very intensive research run... So this is a good output. It works fine too."

## 5. Identified Improvements
During this run, two key areas for optimization were identified and subsequently patched:
1.  **Deduplication**: The system was deduping URLs but not content. A content hash fix has been applied to `finalize_research`.
2.  **Aggressive Filtering**: The crawler was stripping bullet-point articles (e.g., Al Jazeera timelines). A fix for `_remove_navigation_lines` has been applied.

## 6. Next Steps: One-Shot Experiment
While the iterative "append" approach worked, we are now testing a **One-Shot Generation** strategy to see if the fresh context allows the agent to write the entire report in a single coherent pass, potentially improving narrative flow and reducing tool call overhead.

**Status:** The `report-writer` instructions have been updated to enforce One-Shot writing for the next run.

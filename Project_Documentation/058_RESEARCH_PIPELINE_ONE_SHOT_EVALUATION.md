# Research Pipeline Evaluation: One-Shot Strategy

**Date:** January 14, 2026
**Run ID:** session_20260114_120517
**Topic:** Russia-Ukraine War (Week of Jan 7-14, 2026)

## 1. Executive Summary
Following the successful test of the Split-Agent architecture with an *Iterative Append* strategy, we tested a **One-Shot Generation** strategy where the `report-writer` sub-agent ingests the entire corpus and writes the full report in a single tool call.

**Verdict:** The One-Shot strategy is superior for standard comprehensive reports, offering a **4x speed improvement** with negligible loss in detail and improved narrative cohesion.

## 2. Methodology Comparison
| Metric | Iterative Run (Session 113502) | One-Shot Run (Session 120517) | Improvement |
|--------|--------------------------------|-------------------------------|-------------|
| **Execution Time** | ~21 minutes (1296s) | ~5 minutes (303s) | **76% Faster** |
| **Tool Calls** | 41 | 15 | **63% Fewer** |
| **Report Size** | 39KB (HTML) | 33KB (HTML) | Slightly more concise |
| **Quality** | High (Segmented) | High (Cohesive) | Improved Flow |
| **Errors** | None | None | - |

## 3. Detailed Observations

### Efficiency
The One-Shot approach eliminated the overhead of:
- Repeatedly calling `read_research_files` (though it did batch read, it didn't need to re-read for context).
- Repeatedly calling `append_to_file` (10 separate calls in the iterative run vs 1 `Write` call).
- Intermediate "Task" logic steps where the model plans the next section.

### Context Utilization
The Split-Agent architecture was critical here. Because the `research-specialist` handled the noise, the `report-writer` started with a fresh context window. This allowed it to ingest ~150k characters of raw research data and synthesize it all at once without hitting context limits.

### Output Quality
- **Completeness:** The report covered all requested sections (Executive Summary, Timeline, Military, Civilian, International, Outlook).
- **Integrity:** The generated HTML file was complete with no truncation (`</html>` tag present).
- **Styling:** The single-pass generation resulted in a more consistent CSS and layout structure compared to the appended sections.

## 4. Risks & Mitigations
- **Context Limit Risk:** While successful here, a truly massive corpus (>200k chars) might still overwhelm a single prompt or output limit.
- **Mitigation:** The `report-writer` instructions retain a fallback: *If the single Write call fails due to limits, split into chunks.* This hybrid approach offers the best of both worlds.

## 5. Conclusion
**One-Shot Generation** should be the default mode for the `report-writer` sub-agent. The "Iterative Append" strategy remains a valuable fallback for "Deep Research" tasks where the output report alone is expected to exceed 8-10k tokens (approx 20+ pages of text).

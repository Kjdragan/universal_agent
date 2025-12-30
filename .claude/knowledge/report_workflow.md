# Report Creation Workflow

## Critical: Web Search Results Require Delegation

When you receive web search results from COMPOSIO_SEARCH_WEB, COMPOSIO_SEARCH_NEWS, or similar search tools:

⚠️ **DO NOT** summarize the search snippets and write a report yourself.
⚠️ **DO NOT** begin generating report content based on search snippets alone.

These search results contain **incomplete snippets**, not full article content.

### Required Workflow

1. **Search Phase**: Use COMPOSIO_MULTI_EXECUTE_TOOL to search for relevant sources
2. **Delegation Phase**: Call the `Task` tool with `subagent_type='report-creation-expert'`
3. The sub-agent will:
   - Read the saved search result files from `search_results/` directory
   - Use `mcp__local_toolkit__crawl_parallel` to get **FULL article content** from URLs
   - Generate a comprehensive HTML report with proper citations
   - Convert to PDF using `google-chrome --headless` (NOT reportlab)

### Why This Matters

- Search snippets are 1-2 sentences per result - insufficient for quality reports
- Full crawl extracts complete article text with context
- Sub-agent has specialized report-creation skills and PDF knowledge
- This workflow produces dramatically higher quality outputs

### Correct Pattern

```
1. COMPOSIO_MULTI_EXECUTE_TOOL → search results saved to search_results/
2. Task(subagent_type="report-creation-expert", description="...", background="Search results saved in search_results/ directory. Read them, crawl URLs for full content, create HTML report, convert to PDF.")
```

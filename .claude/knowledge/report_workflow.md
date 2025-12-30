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

---

## Critical: Batch File Reading

After `crawl_parallel` creates multiple `crawl_*.md` files:

⚠️ **DO NOT** call `read_local_file` individually for each crawled file.
✅ **DO** use `mcp__local_toolkit__read_research_files` to read all files at once.

### Required Pattern for Reading Crawled Content

```python
# WRONG (slow, 30+ tool calls):
read_local_file(path="search_results/crawl_abc123.md")
read_local_file(path="search_results/crawl_def456.md")
# ... 30 more calls

# CORRECT (fast, 1 tool call):
read_research_files(file_paths=[
    "search_results/crawl_abc123.md",
    "search_results/crawl_def456.md",
    # ... all files
])
```

### How to Get File List

1. After `crawl_parallel` completes, call `list_directory(path="search_results/")`
2. Collect all `crawl_*.md` file paths
3. Pass entire list to `read_research_files(file_paths=[...])`

### Context Overflow Protection

`read_research_files` has built-in protection:
- Stops at 25,000 words to prevent context overflow
- Returns list of skipped files that exceed limit
- For skipped files only, use `read_local_file` individually

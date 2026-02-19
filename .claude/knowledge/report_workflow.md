# Report Creation Workflow

## Critical: Immediate Delegation for Explicit Research Reports

When the user explicitly requests a research report or written research deliverable:

⚠️ **DO NOT** perform web searches yourself as the primary agent.
⚠️ **DO NOT** summarize search snippets and write a report yourself.

### Required Workflow (2 Delegations for report tasks)

1. **Research Phase**: Delegate to `research-specialist` IMMEDIATELY
   - Call `Task` tool with `subagent_type='research-specialist'`
   - Prompt: "Research [topic]: execute searches, finalize corpus."
   - The specialist handles: COMPOSIO search → crawl → filter → **refine**
   - Output: `tasks/{topic}/refined_corpus.md` (token-efficient extraction)

2. **Writing Phase**: After research completes, delegate to `report-writer`
   - Call `Task` tool with `subagent_type='report-writer'`
   - Prompt: "Write the full HTML report using refined_corpus.md"
   - The writer reads the refined corpus and generates the report

### Why This Matters

- **Clean Primary Context**: You don't accumulate search results.
- **Token Efficient**: Refined corpus is ~10K tokens vs ~50K raw.
- **Quality Preserved**: Extraction keeps quotes, stats, citations.

### Correct Pattern

```
1. Task(subagent_type="research-specialist", description="Research [topic]")
2. Task(subagent_type="report-writer", description="Write HTML report from refined_corpus.md")
```

---

## Report Writer Input: refined_corpus.md

The report writer reads ONE file: `refined_corpus.md`

This file contains:
- Key facts and statistics (extracted from each source)
- Direct quotes with speaker attribution
- Source metadata (title, date, URL)
- Thematic groupings

**No need to read individual crawl files or research_overview.md.**

The refined corpus is pre-compressed by the research pipeline using LLM extraction,
delivering the "essence" of 30+ articles in a single, citation-rich document.

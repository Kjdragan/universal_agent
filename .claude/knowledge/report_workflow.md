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

2. **Writing Phase**: After research completes, generate the report using the **`modular-research-report-expert` skill** (preferred) or `report-writer` sub-agent (fallback).
   - **Preferred**: Invoke the `modular-research-report-expert` skill via `/modular-research-report-expert` or the Skill tool. This uses the Agent Teams pipeline with 6 specialized teammates (Narrative Architect, Deep Reader, Storyteller, Visual Director, Diagram Craftsman, Editorial Judge), draft-critique-revise loops, and integrated visual design. Requires `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` (set via Infisical or env).
   - **Fallback**: Call `Task` tool with `subagent_type='report-writer'` — uses the legacy single-pass `run_report_generation` MCP tool. Use only if Agent Teams is unavailable or the skill fails.

### Why This Matters

- **Clean Primary Context**: You don't accumulate search results.
- **Token Efficient**: Refined corpus is ~10K tokens vs ~50K raw.
- **Quality Preserved**: Extraction keeps quotes, stats, citations.

### Correct Pattern

```
1. Task(subagent_type="research-specialist", description="Research [topic]")
2. Skill(skill="modular-research-report-expert", args="[task_name or corpus path]")
   — OR (fallback) —
   Task(subagent_type="report-writer", description="Write HTML report from refined_corpus.md")
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

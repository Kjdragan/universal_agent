---
name: massive-report-writing
description: Multi-stage report writing for large research corpora using map-reduce batching, evidence ledgering, and chunked section writes. Use when reports must synthesize many sources (e.g., 20+ files, >150k chars, long research_overview) or when single-pass writing risks context overload or malformed tool calls.
---

# Massive Report Writing

## Overview
Use a map-reduce workflow to turn large research corpora into a coherent report without context collapse. Keep intermediate artifacts small and structured so the final write step relies on distilled evidence, not raw corpus text.

## Trigger Heuristics
- Corpus >= 20 files, or research_overview > ~30k chars.
- Batch reads return >60k chars or frequent truncation warnings.
- Prior runs show Write tool input missing/empty or malformed tool calls.
- Report scope is "comprehensive", "deep dive", or >30-day horizon.

## Workflow (Map -> Reduce -> Write)

### 1) Normalize scope + budget
- Confirm report format (HTML or Markdown), required sections, and output path.
- Set batch size: 5-10 files per batch, target 60-75k chars per batch.
- Decide a fixed section outline early (even provisional).

### 2) Map: batch read + ledger extraction
- Read research in batches using `read_research_files` only. Avoid single-file reads.
- For each batch, extract evidence into a ledger and produce a short batch summary.
- Keep each batch summary under ~500-800 words and include evidence IDs.
- Treat batch summaries as navigation only, not source material for final writing.
- If the ledger grows too large, compress older entries into a "prior evidence summary" that preserves quotes, numbers, and URLs.

### 3) Reduce: thematic consolidation
- Group evidence into 4-8 themes.
- Build a section outline and assign evidence IDs to each section.
- Note contradictions or uncertainty in a dedicated "gaps and conflicts" list.
 - Use the ledger as the only source of truth. Do not write from summaries of summaries.

### 4) Write in sections (chunked)
- Draft one section at a time from the ledger + outline only.
- If context pressure is high, write section-by-section to disk (append) instead of a single giant write.
- Verify each Write call includes `file_path` and `content` and uses plain JSON (no XML markup in tool names).

### 5) Final pass
- Add executive summary, table of contents, references, and visuals (if required).
- Validate citations and dates against the ledger.

## Guardrails for Tool Calls
- Keep tool inputs minimal and well-formed; avoid inline XML/HTML in tool calls.
- When Write fails due to missing inputs, immediately re-issue with a minimal JSON payload.
- If repeated failures occur, request a compaction or re-run with only the ledger + outline in context.

## Anti-Summary-of-Summary Rules
- Every section draft must cite ledger items directly (facts, quotes, numbers, dates).
- Batch summaries never replace ledger evidence; they only point back to it.
- If a point cannot be traced to a ledger item, drop it or re-read the source batch.

## Reference Templates
Use the templates in `references/massive_report_templates.md` for:
- Evidence ledger entries
- Batch summaries
- Section outline
- Chunked write sequence

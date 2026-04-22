---
name: paper-to-podcast-tf
description: >
  Turn academic research into digestible multi-format content (podcast, quiz, flashcards).
  Given a topic string, searches ArXiv for the most relevant recent papers, extracts key findings,
  creates a NotebookLM notebook with all papers as sources, and generates audio overview podcast,
  quiz, and flashcard artifacts. USE when the user says "paper to podcast", "research podcast",
  "turn papers into", "digest academic", "make a podcast from research", "audio overview from papers",
  "flashcards from papers", "quiz from research", or any request to transform academic papers into
  learnable content formats. Also triggers on "study arXiv papers", "review literature as podcast",
  or "convert papers to audio".
---

# Paper to Podcast

Transform academic research papers into a multi-format learning package: audio podcast, quiz, and flashcards.

## Goal

Given a research topic, produce a complete learning package sourced from the top recent ArXiv papers:
- Audio overview podcast (~15 min deep dive)
- Multiple-choice quiz
- Study flashcards
- All artifacts downloaded and saved to work_products/

## Success Criteria

- At least 4 relevant ArXiv papers found and ingested (target 5, minimum 4)
- NotebookLM notebook created with all paper sources
- Audio overview podcast generated and downloaded
- Quiz generated and downloaded
- Flashcard set generated and downloaded
- All files saved to CURRENT_RUN_WORKSPACE/work_products/paper_to_podcast/
- A manifest.json written listing all outputs with paths and metadata

## Constraints

- Papers must be from ArXiv (no generic web sources)
- Default to papers from the last 12 months unless user specifies a date range
- NotebookLM notebook title should include the topic string
- All artifacts must be downloaded (not just URLs)
- Do NOT generate artifacts using generic LLM when NotebookLM is available
- If a paper download fails, skip it and continue with remaining papers
- Download artifacts sequentially (one at a time) — never in parallel

## Context

### Required Capabilities

ArXiv tools (call directly via MCP):
- mcp__arxiv-mcp-server__search_papers — search by topic, category, date range
- mcp__arxiv-mcp-server__download_paper — download paper by arXiv ID
- mcp__arxiv-mcp-server__read_paper — read full text of downloaded paper

NotebookLM tools (call directly via MCP):
- mcp__notebooklm-mcp__refresh_auth — authenticate before operations
- mcp__notebooklm-mcp__notebook_create — create a new notebook
- mcp__notebooklm-mcp__source_add — add text content as a source
- mcp__notebooklm-mcp__studio_create — generate audio/quiz/flashcards
- mcp__notebooklm-mcp__studio_status — check generation status
- mcp__notebooklm-mcp__download_artifact — download generated artifact

### CRITICAL: Audio Download Fallback

The MCP download_artifact tool frequently fails for audio (CDN auth scoping issue).
When audio download fails via MCP, ALWAYS fall back to CLI:

    /home/ua/.local/bin/nlm download audio <notebook_id> -o <output_path> --no-progress

The nlm CLI authenticates differently and reliably downloads audio.

### Pipeline Phases

Phase A — Paper Discovery (direct MCP tools):
1. Call mcp__arxiv-mcp-server__search_papers with the user's topic, max_results=5, sort_by=relevance, date_from 12 months ago, and relevant categories (cs.AI, cs.CL, cs.LG, cs.MA for AI/ML topics)
2. For each paper, call download_paper then read_paper to get full text
3. Extract: title, authors, key findings, methodology, contributions
4. Save paper metadata to work_products/paper_to_podcast/papers_metadata.json

Phase B — NotebookLM Content Generation (direct MCP tools):
1. Call refresh_auth to verify authentication
2. Call notebook_create with title "Paper to Podcast: {topic}"
3. For each paper, call source_add with source_type="text" using curated content: title, authors, abstract, key findings (3-5 bullets), methodology highlights, results/conclusions
4. Generate 3 studio artifacts sequentially:
   a. Audio overview: studio_create with artifact_type="audio", audio_format="deep_dive"
   b. Quiz: studio_create with artifact_type="quiz", question_count=5, difficulty="medium"
   c. Flashcards: studio_create with artifact_type="flashcards", difficulty="medium"
5. Poll studio_status every 30s until all 3 complete (audio takes 5-10 min)

Phase C — Download and Package (sequential, one at a time):
1. Download quiz via download_artifact with artifact_type="quiz", output_format="json"
2. Download flashcards via download_artifact with artifact_type="flashcards", output_format="json"
3. Download audio via download_artifact with artifact_type="audio"
   - If audio MCP download fails, fall back to CLI: /home/ua/.local/bin/nlm download audio <notebook_id> -o <path> --no-progress
4. Write manifest.json with: topic, papers, artifact paths, notebook_id

## Anti-Patterns

- Do NOT delegate to sub-agents (notebooklm-operator, arxiv-specialist). Call MCP tools directly. Sub-agent delegation hits a "nested Claude Code" guard and wastes tokens.
- Do NOT download artifacts in parallel. Sequential only — parallel downloads cause cascading cancellation.
- Do NOT generate audio/quiz/flashcards with generic LLM tools when NotebookLM is available.
- Do NOT skip the manifest.json — it proves the pipeline completed.
- Do NOT try to pass entire raw paper HTML as sources. Curate to abstract + key findings + methodology.
- Do NOT give up on audio download without trying the CLI fallback.

## Output Structure

    work_products/paper_to_podcast/
    ├── manifest.json
    ├── papers_metadata.json
    ├── podcast_audio.m4a
    ├── quiz.json
    └── flashcards.json

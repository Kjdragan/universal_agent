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
- NotebookLM notebook created (via the `nlm` CLI) with all paper sources
- Audio overview podcast generated and downloaded as a real `.m4a` — REQUIRED (the headline deliverable)
- Quiz generated and downloaded — REQUIRED
- Flashcard set generated and downloaded — best-effort (skip cleanly if NotebookLM cannot produce it)
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

ArXiv tools (call directly via MCP — these are the ONLY supported way to reach arXiv):
- mcp__arxiv-mcp-server__search_papers — search by topic, category, date range
- mcp__arxiv-mcp-server__download_paper — download paper by arXiv ID
- mcp__arxiv-mcp-server__read_paper — read full text of downloaded paper
- mcp__arxiv-mcp-server__list_papers — list papers already downloaded locally

The arxiv-mcp-server **enforces arXiv's 3-second rate limit automatically** and
backs off on HTTP 429. You do NOT need to manage timing yourself. If a call ever
returns a rate-limit error, wait ~60 seconds and retry the SAME MCP call once.
Do NOT fall back to the raw `arxiv` Python library, `curl`/`wget` against
export.arxiv.org, or any hand-rolled HTTP — those bypass the rate limiter and
cause the 429 storms this skill exists to avoid.

NotebookLM — use the `nlm` CLI for ALL NotebookLM operations (NOT the `mcp__notebooklm-mcp__*` tools).

**CRITICAL:** The long-lived NotebookLM MCP server's `refresh_auth` intermittently reports
"Authentication expired" even when the credentials are perfectly valid — it fails a live homepage
probe that gets transiently redirected (e.g. under IP throttling) and never recovers, which makes
the agent abandon NotebookLM. The `nlm` CLI authenticates fresh from the on-disk profile on every
invocation, self-refreshes the CSRF token over HTTP, and is reliable. Verified 2026-06-04 end-to-end
(create → source → audio → download produced a real `.m4a`) against `notebooklm-mcp-cli` v0.7.0.
So drive NotebookLM with the CLI commands below, and do NOT call `mcp__notebooklm-mcp__refresh_auth`,
`notebook_create`, `source_add`, `studio_create`, or `download_artifact`.

CLI binary: `/home/ua/.local/bin/nlm` (referred to as `nlm` below).

Pin the profile ONCE before any NotebookLM step — some subcommands (e.g. `download`) do not accept
`-p`, so set the env var instead of passing a per-command flag:

    export NLM_PROFILE=default

- `nlm login --check` → verify auth (rc=0 = valid). If rc!=0 the cookies are genuinely expired:
  STOP, do not fabricate anything (see Anti-Patterns), and report that a desktop `nlm login` re-auth
  is needed.
- `nlm notebook create "<title>" --json` → create the notebook; parse `notebook_id` from the JSON.
- `nlm source add <nb> --file <pdf> --wait` → add a PDF source (one call per paper).
- `nlm audio create <nb> --format deep_dive --confirm` → generate the audio overview (headline deliverable).
- `nlm quiz create <nb> --count 10 --difficulty 3 --confirm` → generate the quiz.
- `nlm studio status <nb> --json` → poll generation status.
- `nlm download audio <nb> -o <path> --no-progress` → download the `.m4a` (NOTE: no `-p` flag here).
- `nlm download quiz <nb> -o <path>` → download the quiz JSON.
- `nlm download flashcards <nb> -o <path>` → download flashcards (only if generated; see Phase B).

### Deploy-restart resume (checkpoint + adopt)

NotebookLM audio generates on Google's servers and can take 5-15 minutes. If a
deploy restart kills this run mid-generation, the audio still finishes on
Google's side — but the `notebook_id` needed to fetch it lives only in this
run's memory unless we write it down. So this skill keeps a tiny **resume
checkpoint** and checks for one before creating anything:

- The checkpoint is `.nlm_resume.json` in the run workspace root (the directory
  that contains `work_products/` — your current working directory). It is a
  dotfile on purpose: the workspace organizer and the artifact notifier both
  skip dotfiles, so it never leaks into deliverables.
- It holds `{"notebook_id", "topic", "run_started_at" (epoch seconds), "status"}`,
  where `status` moves `creating` → `polling` → `done`.
- Write it the INSTANT the notebook exists (Phase B.2), update it after audio is
  requested (Phase B.4), and delete it once everything is downloaded (Phase C.5).

A run that finds a fresh checkpoint ADOPTS the existing notebook instead of
building a new one — that is the recovery path after a deploy-restart.

### Pipeline Phases

Phase A — Paper Discovery (direct MCP tools):
1. Call mcp__arxiv-mcp-server__search_papers with the user's topic, max_results=5, sort_by=relevance, date_from 12 months ago, and relevant categories (cs.AI, cs.CL, cs.LG, cs.MA for AI/ML topics). Make ONE search call; the server already paces requests. If it returns a rate-limit/429 error, wait ~60s and retry the same call ONCE — never switch to the raw `arxiv` library or curl.
2. For each paper, call download_paper then read_paper to get full text (one paper at a time — the server paces these for you)
3. Extract: title, authors, key findings, methodology, contributions
4. Save paper metadata to work_products/paper_to_podcast/papers_metadata.json

Phase B — NotebookLM Content Generation (via the `nlm` CLI — see Required Capabilities):
0. RESUME CHECK (deploy-restart recovery — do this BEFORE creating anything).
   Look for `.nlm_resume.json` in the workspace root. If it exists, parse it, and
   if its `status` is not `"done"` AND `run_started_at` is within the last 24
   hours:
   a. `export NLM_PROFILE=default`, then `nlm login --check` (if it fails, STOP
      per Anti-Patterns — never fabricate).
   b. Run `nlm studio status <notebook_id> --json` for the checkpoint's
      `notebook_id`.
      - If the notebook is valid and its audio is `completed` or still
        generating, ADOPT it: SKIP notebook/source/audio creation (steps 1-4),
        keep using the checkpoint's `topic` for ALL titles/subjects (do NOT pick
        a new topic), and continue at step 5 (poll) then Phase C (download +
        package + email). This recovers a run a deploy restart interrupted.
      - If the notebook is missing/errored/expired, delete `.nlm_resume.json` and
        fall through to step 1 (fresh run).
   If there is no checkpoint, or it is stale (>24h) or already `"done"`, proceed
   to step 1.
1. `export NLM_PROFILE=default`, then `nlm login --check`. If it fails, STOP per Anti-Patterns
   (report that desktop re-auth is needed — never fabricate audio/quiz/flashcards).
2. `nlm notebook create "Paper to Podcast: {topic}" --json` → capture `notebook_id` from the JSON.
   IMMEDIATELY write the resume checkpoint `.nlm_resume.json` at the workspace
   root: `{"notebook_id": "<id>", "topic": "<topic>", "run_started_at": <epoch
   seconds now>, "status": "creating"}`. Until the manifest is written at the very
   end this is the ONLY durable record of the notebook handle, so writing it now
   is what lets a deploy-restart adopt this notebook instead of building a new one.
3. For each downloaded paper PDF (from Phase A), `nlm source add <notebook_id> --file <path-to-paper.pdf> --wait`
   (one call per paper, sequential).
4. Generate artifacts — the audio overview is the headline deliverable, so kick it off FIRST and never skip it:
   a. Audio: `nlm audio create <notebook_id> --format deep_dive --confirm`
      — then update `.nlm_resume.json` `status` to `"polling"`.
   b. Quiz:  `nlm quiz create <notebook_id> --count 10 --difficulty 3 --confirm`
   c. Flashcards (best-effort ONLY): there is no `nlm` CLI command to *create* flashcards. You MAY
      attempt `mcp__notebooklm-mcp__studio_create` with `artifact_type="flashcards"` AFTER audio + quiz
      are created — but treat flashcards as optional. If it errors, skip it; never let a flashcards
      failure block or undo the audio.
5. Poll `nlm studio status <notebook_id> --json` every ~45s until the audio artifact shows
   `"status":"completed"` (audio takes ~5-10 min; quiz completes faster).

Phase C — Download and Package (sequential, one at a time, via `nlm`):
1. Audio: `nlm download audio <notebook_id> -o work_products/paper_to_podcast/podcast_audio.m4a --no-progress`
   Verify the file exists and is > 100 KB (a real `.m4a`). This is the primary success signal.
2. Quiz: `nlm download quiz <notebook_id> -o work_products/paper_to_podcast/quiz.json`
3. Flashcards (only if generated in B4c): `nlm download flashcards <notebook_id> -o work_products/paper_to_podcast/flashcards.json`
4. Write manifest.json with: topic, papers, notebook_id, and each artifact's path + size. Note any
   skipped flashcards as a gap — that is acceptable; a missing audio podcast is NOT.
5. Once manifest.json is written and the audio `.m4a` is verified present, DELETE
   `.nlm_resume.json` (the run is complete; the next day's run must not adopt this
   notebook). If deletion fails for any reason, set its `status` to `"done"`.

## Anti-Patterns

- Do NOT use the `mcp__notebooklm-mcp__*` tools for the notebook / source / audio / quiz chain. The long-lived MCP server's auth is unreliable (intermittent false "Authentication expired"). Use the `nlm` CLI, which authenticates reliably per-invocation.
- NEVER fabricate the audio podcast with a generic LLM, and NEVER write a text "podcast transcript" as a substitute. The audio overview MUST come from NotebookLM via `nlm audio create`. If the CLI audio step genuinely fails, report the gap honestly in the manifest and email — do not paper over it with LLM-written text.
- Do NOT delegate to sub-agents (notebooklm-operator, arxiv-specialist). Call tools directly. Sub-agent delegation hits a "nested Claude Code" guard and wastes tokens.
- Do NOT download artifacts in parallel. Sequential only — parallel downloads cause cascading cancellation.
- Do NOT skip the manifest.json — it proves the pipeline completed.
- Do NOT pass entire raw papers as text sources — use `nlm source add <nb> --file <pdf>` to upload the PDF directly.

## Output Structure

    work_products/paper_to_podcast/
    ├── manifest.json
    ├── papers_metadata.json
    ├── podcast_audio.m4a
    ├── quiz.json
    └── flashcards.json

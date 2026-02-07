# Clawdbot Voyage Memory Architecture Assessment (2026-02-07)

## Scope

This report analyzes how Voyage AI is used in the latest Clawdbot fork at:

- `/home/kjdragan/lrepos/clawdbot`

It also cross-checks upstream OpenClaw release and PR data to identify what changed recently, and whether Voyage replaces or augments existing memory systems.

## Executive Summary

Voyage integration in current Clawbot is a provider-level upgrade to the existing builtin memory indexer, not a replacement of the memory architecture.

What changed recently:

- Native Voyage embeddings support was added and merged upstream on **2026-02-06 UTC** via PR `#7078` (merge commit `6965a2cc9`).
- Voyage `input_type` handling (`query` vs `document`) was added on **2026-02-07 UTC** via PR `#10818` (merge commit `e78ae48e6`) to improve retrieval quality.

What did not change:

- The core builtin memory flow still uses SQLite + chunking + vector search + optional BM25 hybrid merging.
- No Voyage reranker endpoint is called anywhere in the builtin memory pipeline.
- QMD remains the only memory backend path that explicitly documents reranking behavior.

Bottom line:

- Voyage is currently an embedding provider option inside the existing builtin memory system.
- It does not replace memory tools, index shape, chunking model, or retrieval fusion logic.
- It does not implement Voyage reranking in the builtin path.

## Recent Upstream Change Timeline (Verified)

1. **PR #7078** (`feat(memory): native Voyage AI support`) merged **2026-02-06T21:09:33Z**
- Adds Voyage provider wiring across config/schema/types.
- Adds `src/memory/embeddings-voyage.ts`.
- Adds `src/memory/batch-voyage.ts` and manager integration.
- Enables Voyage in provider selection and fallback.

2. **PR #10818** (`fix(memory): add input_type to Voyage AI embeddings`) merged **2026-02-07T03:55:09Z**
- `embedQuery` now sends `input_type: "query"`.
- `embedBatch` and Voyage batch request params now send `input_type: "document"`.

3. Release packaging evidence in local tags:
- `v2026.2.6` commit date: **2026-02-06 17:56:22 -0800**
- `v2026.2.6-1` commit date: **2026-02-06 22:48:19 -0800**
- `v2026.2.6-2` commit date: **2026-02-07 00:30:43 -0800**
- `v2026.2.6-3` commit date: **2026-02-07 00:44:32 -0800**

`#10818` is included in tags from `v2026.2.6-1` onward in this fork history.

## Current Memory Architecture in Clawbot

### Backend selection

Memory backend is selected globally:

- Default: `memory.backend = "builtin"`
- Optional alternate backend: `memory.backend = "qmd"`

Code path:

- `src/memory/backend-config.ts`
- `src/memory/search-manager.ts`

If QMD is configured and available, QMD is primary with fallback to builtin manager. If QMD is unavailable or fails at runtime, builtin manager is used.

### Builtin backend (where Voyage is used)

Primary manager:

- `src/memory/manager.ts` (`MemoryIndexManager`)

Pipeline (unchanged structurally by Voyage):

1. Discover memory files + optional session transcript sources.
2. Chunk markdown into overlapping chunks.
3. Generate embeddings via selected provider.
4. Store chunks in SQLite (and optional sqlite-vec virtual table).
5. Query with vector similarity plus optional BM25 hybrid merge.

### Memory tools surface

Agent tools still remain:

- `memory_search`
- `memory_get`

Wrapper:

- `src/agents/tools/memory-tool.ts`

Voyage does not change tool contracts; it changes how embeddings are produced under the hood when provider resolves to `voyage`.

## Exactly How Voyage Is Utilized

### 1) Provider and auth resolution

Voyage is now a first-class provider in memory search config and runtime types:

- `src/agents/memory-search.ts`
- `src/config/types.tools.ts`
- `src/config/zod-schema.agent-runtime.ts`

Auth source resolution includes `VOYAGE_API_KEY` and provider config entries:

- `src/agents/model-auth.ts`

### 2) Embedding client behavior

Voyage embedding client:

- `src/memory/embeddings-voyage.ts`

Current behavior:

- Default model: `voyage-4-large`
- Default base URL: `https://api.voyageai.com/v1`
- Query embedding sends `input_type: "query"`
- Indexing batch embedding sends `input_type: "document"`

This `input_type` split was added by `#10818` and is specifically intended to improve retrieval quality.

### 3) Batch indexing implementation

Voyage batch path:

- `src/memory/batch-voyage.ts`
- Integrated in `src/memory/manager.ts`

Flow:

1. Build JSONL requests (`custom_id`, input text).
2. Upload file to Voyage Files API.
3. Create batch job (`/batches`) targeting embeddings endpoint.
4. Poll until completion (or fail/timeout).
5. Stream output NDJSON and map embeddings by `custom_id`.
6. Cache embeddings and continue indexing.

Batch config knobs are shared with remote provider batch settings (`enabled`, `wait`, `concurrency`, polling interval, timeout).

### 4) Query/retrieval path with Voyage

At search time:

1. `embedQueryWithTimeout()` calls provider query embedding.
2. Vector search runs in sqlite-vec when available, otherwise JS cosine fallback.
3. Optional BM25 search runs (FTS5) when enabled/available.
4. Vector + BM25 are merged by weighted score.

Code paths:

- `src/memory/manager.ts`
- `src/memory/manager-search.ts`
- `src/memory/hybrid.ts`

Important: this is still hybrid retrieval + weighted merge; there is no secondary Voyage rerank call.

## Reranking / "LOM re-ranking" Assessment

### What is present

- QMD backend docs explicitly describe BM25 + vectors + reranking in the QMD sidecar path (`docs/concepts/memory.md`).

### What is not present in builtin Voyage path

- No code references to Voyage rerank endpoints.
- No `rerank`, `voyage-rerank`, or similar calls in `src/memory` builtin retrieval flow.
- Builtin scoring remains vector similarity and optional BM25 weighted merge.

Interpretation:

- If your expectation is Voyage-powered reranking in builtin memory, that is not currently implemented.
- The reranking concept in current docs is attached to QMD backend behavior, not Voyage provider behavior in builtin mode.

## Replace vs Augment Decision

### Does Voyage replace prior memory systems?

No.

It augments the builtin memory system by adding a new embeddings provider and batch route. Existing architecture (SQLite index, chunking, memory tools, hybrid merge, QMD option) remains intact.

### What would replace builtin memory?

Only switching backend to QMD (`memory.backend = "qmd"`) replaces the retrieval engine, and even then it has a guarded fallback back to builtin manager.

## Practical Integration Guidance for Universal Agent

1. If you want Clawbot parity first, integrate Voyage as an embedding provider option in your existing memory pipeline before any reranking work.
2. Keep backend abstraction boundaries explicit (provider vs backend) so a provider change does not imply retrieval strategy change.
3. If you need reranking quality gains, evaluate separately:
- QMD-style backend swap, or
- explicit rerank stage after hybrid retrieval.
4. Preserve provider-specific query/document embedding modes (`input_type`) in your own integration to avoid quality regression.

## Risks and Notes

- Provider auto-selection order still tries local/OpenAI/Gemini before Voyage unless configured explicitly.
- Batch mode can auto-disable after repeated failures and fallback to non-batch embedding calls.
- Header override order in Voyage client allows custom header overrides; ensure this is acceptable in your security model.

## Verification Performed

- Full source trace of memory manager, provider wiring, tool wrappers, and backend selector in local fork.
- Commit and tag timeline checks in local git history.
- Upstream PR metadata verification for `#7078` and `#10818`.
- Local tests executed:
  - `src/memory/embeddings-voyage.test.ts`
  - `src/memory/batch-voyage.test.ts`

Result: all Voyage-specific tests passed.

## Key Source Links

- Upstream PR #7078: https://github.com/openclaw/openclaw/pull/7078
- Upstream PR #10818: https://github.com/openclaw/openclaw/pull/10818
- Upstream releases page: https://github.com/moltbot/moltbot/releases
- Memory concepts doc (QMD + backend behavior): https://docs.openclaw.ai/concepts/memory


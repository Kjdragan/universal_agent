---
title: "UA Memory Feasibility + Implementation Plan (Clawdbot-style)"
status: draft
last_updated: 2026-01-29
---

# 12. UA Memory Feasibility + Implementation Plan (Clawdbot-style)

## 1. Executive summary
It is **feasible** to implement a Clawdbot‑style, file‑based memory system in Universal Agent. The key differences are:
- **Language + runtime**: UA is Python; Clawdbot is TypeScript/Node. We should re‑implement the design patterns rather than copy code.
- **Workspace model**: UA already has per‑session workspaces (`AGENT_RUN_WORKSPACES/session_*`). This maps naturally to Clawdbot’s workspace‑centric memory model.
- **Existing memory system**: UA currently has Letta‑based memory (disabled). A file‑based system can exist **alongside** Letta or replace it.

## 2. What to borrow from Clawdbot
### 2.1 File‑based memory as source of truth
- Add `MEMORY.md` and `memory/YYYY-MM-DD.md` to UA workspaces.
- Keep memory write explicit and auditable.

Reference model: @/home/kjdragan/lrepos/clawdbot/docs/concepts/memory.md#15-35

### 2.2 Retrieval with safe tools
- `ua_memory_search`: semantic/hybrid search returning snippets + line ranges.
- `ua_memory_get`: read limited lines from memory files only.

Reference model: @/home/kjdragan/lrepos/clawdbot/src/agents/tools/memory-tool.ts#22-112

### 2.3 Indexing + embeddings
- Store index in SQLite (per user or per agent) with tables for files/chunks/embedding cache.
- Use hybrid (vector + BM25) scoring when FTS is available.

Reference model:
- Index manager: @/home/kjdragan/lrepos/clawdbot/src/memory/manager.ts#118-540
- Schema: @/home/kjdragan/lrepos/clawdbot/src/memory/memory-schema.ts#3-82

### 2.4 Optional session transcript indexing
- Index transcripts only if enabled (expensive, privacy‑sensitive).

Reference model: @/home/kjdragan/lrepos/clawdbot/docs/concepts/memory.md#286-325

## 3. Feasibility assessment for UA
### 3.1 Fits UA architecture
- UA already tracks **workspace per session**, making memory file placement straightforward.
- UA already produces **durable artifacts** (`transcript.md`, `trace.json`) that can optionally feed memory search.

### 3.2 Risks / considerations
- **Embedding cost**: vector indexing can be expensive. Use caching + batching.
- **Storage**: per‑user memory index grows; enforce size caps or retention.
- **Runtime complexity**: indexing watchers + background sync adds moving parts.
- **Concurrency**: need to avoid blocking active runs on memory indexing.

## 4. Proposed architecture for UA
### 4.1 Memory files (workspace)
- `MEMORY.md`
- `memory/YYYY-MM-DD.md`

### 4.2 Index storage
- SQLite in a stable location (e.g., `~/.universal_agent/memory/<user>.sqlite`)
- Tables: `files`, `chunks`, `embedding_cache`, optional `fts` table

### 4.3 Embedding providers
- Support at least one remote provider (OpenAI or Gemini)
- Optional local embeddings (future)

### 4.4 Retrieval tools
Expose tools to the agent:
- `ua_memory_search(query, max_results, min_score)`
- `ua_memory_get(path, from, lines)`

## 5. Implementation plan (phased, safe)
### Phase 1 — File‑based memory without embeddings
- Add memory file scaffolding to UA workspaces.
- Add `ua_memory_get` tool (read‑only, safe path restrictions).
- Manual memory writing (user or agent).

**Gate:** no regressions in existing CLI + gateway runs.

### Phase 2 — Embedding + vector index MVP
- Implement SQLite index schema.
- Add embedding provider (remote only at first).
- Add `ua_memory_search` with vector similarity.

**Gate:** correct retrieval + index builds without blocking runs.

### Phase 3 — Hybrid search + caching
- Add FTS5 table if available; merge vector + BM25.
- Add embedding cache to avoid re‑embedding unchanged text.

**Gate:** performance benchmarks on sample memory corpus.

### Phase 4 — Automatic sync + watchers
- Debounced file watchers on memory files.
- Background sync and interval‑based reindex.

**Gate:** no crashes; indexing stays in background.

### Phase 5 — Optional session transcript indexing
- Add opt‑in “session memory” indexing for transcripts.
- Use delta thresholds to avoid indexing on every write.

**Gate:** verify privacy and data volume controls.

### Phase 6 — Compaction‑aware memory flush (optional)
- If UA introduces compaction or token‑budget management, add a **silent memory flush** step similar to Clawdbot.

**Gate:** flush does not emit user‑visible messages unless configured.

## 6. Key decisions to make early
- **Storage location** for memory index (per user vs per agent).
- **Embedding provider** and cost model (OpenAI vs Gemini).
- **Whether to enable session transcript indexing** at all.
- **How memory tooling interacts with Letta** (replace, coexist, or later integrate).

## 7. Recommendation
Start with **Phase 1 + 2** only (file‑based + vector search). This delivers core capability with minimal risk. Add hybrid + session memory only after you confirm performance and stability in real usage.

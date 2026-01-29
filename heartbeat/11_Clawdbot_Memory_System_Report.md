---
title: "Clawdbot Memory System Report"
status: draft
last_updated: 2026-01-29
---

# 11. Clawdbot Memory System Report

## 1. Summary (one page)
Clawdbot/Moltbot memory is **file‑based Markdown in the agent workspace**, backed by a **SQLite‑based vector + FTS index** for semantic retrieval. The system is designed to be **auditable and user‑controlled**: the model only “remembers” what is written to disk. Retrieval is handled through two tools:
- `memory_search`: semantic + hybrid retrieval over memory files (and optionally session transcripts).
- `memory_get`: safe, line‑bounded file reads.

Primary sources:
- Memory docs: @/home/kjdragan/lrepos/clawdbot/docs/concepts/memory.md#1-389
- Memory index manager: @/home/kjdragan/lrepos/clawdbot/src/memory/manager.ts#1-2178
- Memory tools: @/home/kjdragan/lrepos/clawdbot/src/agents/tools/memory-tool.ts#1-112
- Memory schema: @/home/kjdragan/lrepos/clawdbot/src/memory/memory-schema.ts#1-95

## 2. File‑based memory layout
Clawdbot keeps memory in Markdown files under the agent workspace:
- `MEMORY.md` (curated, long‑term memory)
- `memory/YYYY-MM-DD.md` (daily append‑only log)

See: @/home/kjdragan/lrepos/clawdbot/docs/concepts/memory.md#15-35

This file‑based approach provides:
- **Auditability** (memory is explicit and human‑editable)
- **Portability** (the workspace is the source of truth)
- **Safety** (no opaque hidden state)

## 3. Automatic memory flush before compaction
Clawdbot runs a **silent, agentic memory flush** when sessions near compaction. It prompts the agent to store durable notes to memory files, and typically returns `NO_REPLY` so users do not see the flush.

This is controlled by `agents.defaults.compaction.memoryFlush` in config, and is tracked per compaction cycle. Workspace write access is required.

See: @/home/kjdragan/lrepos/clawdbot/docs/concepts/memory.md#37-75

## 4. Retrieval system (vector + hybrid search)
Clawdbot builds a small index over memory files (and optionally session transcripts). Retrieval supports:
- **Vector similarity** (semantic recall)
- **FTS/BM25 keyword matches** (exact token recall)
- **Hybrid scoring** (weighted combination)

Details:
- Index manager: @/home/kjdragan/lrepos/clawdbot/src/memory/manager.ts#118-540
- Hybrid search logic: @/home/kjdragan/lrepos/clawdbot/src/memory/manager-search.ts#21-182

### 4.1 Sources indexed
- `MEMORY.md` and `memory/**/*.md` are always eligible. @/home/kjdragan/lrepos/clawdbot/src/memory/internal.ts#33-85
- Session transcripts can be indexed when enabled (experimental). @/home/kjdragan/lrepos/clawdbot/docs/concepts/memory.md#286-325

### 4.2 Index storage
Clawdbot stores the index in SQLite:
- `files` table (file metadata + hash)
- `chunks` table (chunk text + embedding)
- `embedding_cache` table
- Optional FTS5 table for keyword search

See: @/home/kjdragan/lrepos/clawdbot/src/memory/memory-schema.ts#3-82

### 4.3 Embedding providers
Embedding backends support:
- **OpenAI**
- **Gemini**
- **Local (node‑llama‑cpp)**

Auto‑selection and fallback are supported.

See: @/home/kjdragan/lrepos/clawdbot/src/memory/embeddings.ts#13-188

## 5. Sync and indexing behavior
The memory index is kept fresh via:
- **File watchers** on `MEMORY.md` and `memory/` (debounced) @/home/kjdragan/lrepos/clawdbot/src/memory/manager.ts#770-959
- **Session transcript delta tracking** (if enabled) @/home/kjdragan/lrepos/clawdbot/src/memory/manager.ts#792-900
- **Periodic sync** (interval timer) @/home/kjdragan/lrepos/clawdbot/src/memory/manager.ts#940-948

Index updates are async and non‑blocking; `memory_search` never blocks on indexing completion.

## 6. Memory tools exposed to the agent
Clawdbot exposes two tools:
- `memory_search`: semantic retrieval + metadata and line ranges
- `memory_get`: safe, line‑bounded file reads

See: @/home/kjdragan/lrepos/clawdbot/src/agents/tools/memory-tool.ts#22-112

## 7. Key design properties
- **Human‑auditable**: memory is plain Markdown.
- **Safe retrieval**: tools only read from allowed memory paths.
- **Performance‑aware**: embedding cache + hybrid search + batch embeddings.
- **Opt‑in session indexing**: avoids indexing the full session history by default.


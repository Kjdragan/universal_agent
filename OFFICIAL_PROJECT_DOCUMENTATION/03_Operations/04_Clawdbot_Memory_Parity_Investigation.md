# 04 Clawdbot Memory Parity Investigation

## Executive Summary

This report analyzes two advanced memory features in **Clawdbot** to evaluate their parity with the current **Universal Agent** (UA) implementation. The investigation focused on "Memory Flush" and "Session Memory Search" functionality.

While UA has a technically superior underlying storage architecture (ChromaDB + SQLite FTS5 hybrid search), Clawdbot currently leads in agent-driven context distillation during the compaction cycle.

---

## 1. Feature: Memory Flush before Compaction

In Clawdbot, this is controlled by `compaction.memoryFlush.enabled`.

### Clawdbot Implementation

- **Mechanism**: When reaching a token soft-threshold, Clawdbot triggers a "silent" agent turn.
- **Logic**: The agent is given a specialized system prompt to "capture durable memories" that might be lost during the upcoming compaction. It summarizes key facts, decisions, and outcomes into `memory/YYYY-MM-DD.md`.
- **Strength**: High fidelity. The AI decides what is worth keeping, rather than just taking a raw transcript tail.

### Universal Agent Status: **Basic Parity (Logic-based)**

- UA already has a `pre_compact_context_capture_hook` in `src/universal_agent/agent_core.py`.
- It performs a deterministic "Memory Flush" via `flush_pre_compact_memory`, which extracts the last ~4000 characters of the transcript and appends them to archival memory.
- **Gap**: UA lacks the AI-driven "summarization turn" that distills the memories before storage.

---

## 2. Feature: Session Memory Search

In Clawdbot, this is `memorySearch.experimental.sessionMemory`.

### Clawdbot Implementation

- **Mechanism**: Indexes past session transcripts into a local SQLite-based vector store.
- **Strength**: Allows the agent to "remember" cross-session context that is no longer in the active context window.

### Universal Agent Status: **Infrastructure Parity (Ahead in Capability)**

- UA's `MemoryManager` (in `Memory_System/`) utilizes **ChromaDB** for vector storage and **SQLite FTS5** for keyword search. This hybrid approach is significantly more robust than Clawdbot's current experimental implementation.
- **Gap**: Although the `transcript_index` function exists in `manager.py`, it is currently only invoked in **unit tests**. The live execution loop does not yet automatically index session transcripts for future search.

---

## 3. Feasibility & Integration Recommendations

### Recommended Actions for UA

1. **AI-Driven Flush**: Upgrade the `pre_compact_context_capture_hook` to perform an AI-driven summarization turn (similar to Clawdbot) instead of a raw text-tail extraction. This ensures high-entropy memories are preserved.
2. **Enable Live Indexing**: Integrate `MemoryManager.transcript_index()` into the `agent_core` execution loop. This would immediately enable "Session Memory Search" parity by populating the existing ChromaDB collection.
3. **Proactive Memory Tooling**: Ensure the `archival_memory_search` tool is highly ranked in the agent's internal tool-selection prompts to encourage cross-session recall.

### Conclusion

Integrating these features is **highly feasible** as 80% of the required infrastructure (Hooks and Hybrid Storage) is already present in the codebase. Activating these latent features would significantly improve the agent's long-term autonomy and context awareness.

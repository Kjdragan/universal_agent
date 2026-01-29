---
title: "UA Memory Migration Checklist"
status: draft
last_updated: 2026-01-29
---

# 13. UA Memory Migration Checklist (Letta → File-Based)

## 1. Goals
- Provide a **safe, staged path** to introduce Clawdbot‑style memory.
- Allow **coexistence** with Letta or a **clean cutover**.
- Maintain **auditability** and **minimal runtime risk**.

## 2. Prerequisites (must decide)
1. **Memory scope**: per‑user vs per‑agent vs per‑workspace index.
2. **Embedding provider**: OpenAI vs Gemini vs local (cost + infra).
3. **Session transcript indexing**: enabled/disabled + retention limits.
4. **Letta coexistence**: parallel retrieval vs replacement.

## 3. Workspace scaffolding
1. Create per‑run memory files in workspace:
   - `MEMORY.md`
   - `memory/YYYY-MM-DD.md`
2. Add guardrails to prevent non‑memory file reads by memory tools.

## 4. Tooling migration
1. Implement tools:
   - `ua_memory_get` (read‑only, line‑bounded)
   - `ua_memory_search` (vector or hybrid)
2. Add tool visibility only to agents that should use memory.

## 5. Indexing migration (low‑risk)
1. Add SQLite schema: files/chunks/embedding_cache (+ FTS).
2. Build index **async** to avoid blocking runs.
3. Cache embeddings by content hash.

## 6. Letta coexistence strategy
**Option A — Coexistence (recommended first):**
- Keep Letta disabled by default.
- Introduce file‑based memory in parallel.
- Allow manual toggles via config to compare retrieval quality.

**Option B — Controlled replacement:**
- Disable Letta.
- Enable file‑based memory for all runs.

**Option C — Hybrid retrieval:**
- Query both Letta + file memory; merge results.
- Requires consistent scoring + deduplication.

## 7. Rollout steps
1. Phase 1 (File‑only): memory files + `ua_memory_get` only.
2. Phase 2 (Vector search MVP): embedding + search.
3. Phase 3 (Hybrid + cache): BM25 + cache + perf tuning.
4. Phase 4 (Watcher + background sync): debounce + interval sync.
5. Phase 5 (Optional session memory): transcript indexing.

## 8. Validation gates
- **Correctness:** `ua_memory_get` only reads memory paths.
- **Performance:** search returns in <200ms on medium corpus.
- **Cost:** embedding batch size + caching verified.
- **Stability:** no impact on active run latency.

## 9. Observability + rollback
1. Add metrics: index time, embedding time, cache hit rate.
2. Add toggles to disable memory entirely at runtime.
3. Provide one‑click rollback to “memory off” mode.

## 10. References
- Clawdbot memory report: @/home/kjdragan/lrepos/universal_agent/heartbeat/11_Clawdbot_Memory_System_Report.md#1-87
- UA feasibility plan: @/home/kjdragan/lrepos/universal_agent/heartbeat/12_UA_Memory_Feasibility_and_Implementation.md#1-72

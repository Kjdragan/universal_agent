# 06 Detailed Memory Pipeline Recommendations (2026-02-07)

## Executive Summary

This document expands the previous parity assessment into an implementation decision guide.

Core recommendation:

1. Keep existing UA memory capabilities (including dormant ones like Letta integration hooks).
2. Introduce one **active canonical memory pipeline** for runtime behavior.
3. Keep alternative implementations in **shadow/off** mode behind explicit config.
4. Treat memory as two explicit classes: **session memory** and **long-term memory**.
5. Use a **hybrid flush model** (deterministic capture + agentic distillation) and incremental session indexing.

This gives stable behavior now, avoids deleting future options, and supports controlled evolution later.

---

## 1. Goals and Non-Goals

### Goals

1. Deliver a reliable, understandable memory pipeline with predictable runtime behavior.
2. Preserve optional systems for future use (Letta, alternate backends, old memory paths).
3. Reach practical parity with Clawdbot where it is useful, without forcing architecture copy/paste.
4. Improve traceability: know what was captured, indexed, searchable, and why.
5. Keep memory useful during real work while avoiding memory pollution during repetitive development tests.

### Non-Goals

1. Remove existing memory codepaths immediately.
2. Force all environments to enable session memory by default.
3. Tie the architecture to a single backend forever.

## 1.1 Memory Types We Must Handle

This architecture explicitly manages at least two different memory forms:

1. **Session Memory**:
   - Transcript-derived memory from conversations and turns.
   - Main purpose: recover context from prior runs/sessions.
   - Best storage/indexing pattern: append-only logs + incremental semantic indexing.
2. **Long-Term Memory**:
   - Durable facts, user preferences, project decisions, stable context.
   - Main purpose: persistent recall independent of a specific session timeline.
   - Best storage/indexing pattern: curated markdown + durable index/vector store.

Design requirement: session memory and long-term memory share retrieval interfaces, but have separate ingestion and retention policies.

---

## 2. Current State (Condensed)

UA currently has strong primitives, but multiple partially overlapping paths:

1. `universal_agent.memory` path:
   - Deterministic pre-compact snapshot capture.
   - Daily markdown + index.json + optional vector indexing.
2. `Memory_System` path:
   - Core blocks in SQLite.
   - Chroma-backed archival/hybrid search.
   - `transcript_index` exists but is not wired into active runtime path.
3. Tooling surfaces:
   - Some memory tools are functional.
   - `archival_memory_search` MCP path currently has a stub return path.

Result: capability exists, but active path selection and integration boundaries are not explicit enough.

---

## 3. Decision Framework

Use these criteria for each choice:

1. **Reliability**: low risk of memory loss.
2. **Determinism**: predictable behavior across runs.
3. **Recall Quality**: ability to retrieve useful past context.
4. **Operational Control**: flags, thresholds, observability.
5. **Migration Safety**: minimal breakage while converging.
6. **Future Flexibility**: can switch adapters/backends without rewrite.

---

## 4. Option Set and Recommendations

## 4.1 Canonical Pipeline Ownership

### Option A: Keep multiple active pipelines

- Pros:
  - No refactor now.
- Cons:
  - Ambiguous behavior.
  - Conflicting writes and search surfaces.
  - Hard to debug and tune.

### Option B: Pick one existing subsystem and remove others

- Pros:
  - Simple conceptual model.
- Cons:
  - Prematurely discards useful work.
  - Blocks future experimentation.

### Option C (Recommended): Orchestrator + adapter model

- Pros:
  - One active runtime path.
  - Existing systems retained as adapters (`active|shadow|off`).
  - Supports future Letta activation without redesign.
- Cons:
  - Requires initial orchestration layer.

**Recommendation**: Option C.

---

## 4.2 Pre-Compaction Memory Flush Strategy

### Option A: Deterministic only (tail snapshot)

- Pros:
  - Highly reliable.
  - Easy to test.
- Cons:
  - Lower semantic quality.
  - Can store noise.

### Option B: Agentic only (silent memory turn)

- Pros:
  - Higher signal-to-noise.
  - Better distilled memory.
- Cons:
  - More variable.
  - Failure modes if model/tools unavailable.

### Option C (Recommended): Hybrid

- Step 1: deterministic capture (guarantee no loss).
- Step 2: optional agentic distillation into durable facts/decisions.

- Pros:
  - Best reliability + quality mix.
  - Graceful degradation if agentic pass fails.
- Cons:
  - Slightly more implementation complexity.

**Recommendation**: Option C.

---

## 4.3 Session Memory Search Strategy

### Option A: No session transcript indexing

- Pros:
  - Low complexity.
- Cons:
  - Misses major value from cross-session memory.

### Option B: Full transcript reindex on every turn

- Pros:
  - Simple logic.
- Cons:
  - High overhead.
  - Latency/cost risk.

### Option C (Recommended): Incremental delta indexing

- Trigger indexing when transcript growth crosses configured thresholds.
- Debounce updates.
- Add forced index on session end to cover short one-off runs.
- Enable `sessionMemory` by default for normal development and operator usage.

- Pros:
  - Good scalability and freshness.
  - Operationally controllable.
  - Works for both long sessions and short single-run workflows.
- Cons:
  - Requires delta bookkeeping.

**Recommendation**: Option C.

---

## 4.4 Retrieval Model

### Option A: Keep separate tools and unmanaged source selection

- Pros:
  - No major refactor.
- Cons:
  - Agent may miss relevant source.
  - Inconsistent ranking across backends.

### Option B (Recommended): Unified retrieval broker

- One memory query interface.
- Query configured sources with weights.
- Return merged/ranked results with source metadata.

- Pros:
  - Stable behavior.
  - Better debuggability and tuning.
- Cons:
  - Requires a central ranking/merge layer.

**Recommendation**: Option B.

---

## 4.5 Dormant Feature Management

### Option A: Delete unused integrations

- Pros:
  - Smaller codebase.
- Cons:
  - Loses future optionality.
  - Creates rework later.

### Option B (Recommended): Lifecycle states

Use explicit state per adapter/backend:

1. `active`: receives reads/writes in production path.
2. `shadow`: receives mirrored writes or validation reads only.
3. `off`: loaded but not executed.
4. `deprecated`: planned removal date.

**Recommendation**: Option B.

---

## 4.5.1 Provider vs Backend Separation (Voyage-Informed)

Treat these as separate decisions:

1. **Embedding provider**:
   - openai, gemini, local, voyage, etc.
   - Affects how vectors are generated.
2. **Retrieval backend**:
   - builtin indexer, QMD, future alternatives.
   - Affects retrieval engine and ranking strategy.

Design rule:

1. Switching embedding provider must not implicitly change backend behavior.
2. Switching backend must not require provider contract changes.

Practical implication for UA:

1. Keep provider selection/config in one layer (embedding adapter).
2. Keep retrieval strategy (semantic/hybrid/rerank) in backend/broker layer.

**Recommendation**: Preserve this boundary as a hard architectural constraint.

---

## 4.6 Development Anti-Pollution Strategy (Practical)

Problem:

During development, repeated runs of similar tests can pollute memory with low-value duplicates.

### Option A: Disable memory entirely in development

- Pros:
  - Zero pollution.
- Cons:
  - Cannot validate memory behavior while building memory features.

### Option B: Keep memory on and clean up manually later

- Pros:
  - Simple to start.
- Cons:
  - Cleanup burden grows quickly.
  - Low confidence in retrieval quality during active dev.

### Option C (Recommended): Memory Profiles + Scoped Writes + Fast Prune

Use environment/profile modes:

1. `prod`:
   - session memory on,
   - long-term memory on,
   - normal retention.
2. `dev_standard`:
   - session memory on,
   - long-term writes restricted to high-confidence categories,
   - stronger dedupe.
3. `dev_memory_test`:
   - session memory on,
   - long-term memory on (for validating memory pipeline),
   - writes tagged `env:dev`, `task:test`.
4. `dev_no_persist`:
   - in-session behavior allowed,
   - no durable writes to long-term store.

Implementation controls:

1. Add `memory.write_policy.min_importance` threshold for long-term inserts.
2. Add duplicate suppression by normalized hash + similarity threshold.
3. Tag all dev-mode writes and provide one-click prune by tag/time window.
4. Add optional dashboard toggles for:
   - session memory on/off,
   - long-term writes on/off,
   - semantic indexing on/off,
   - prune dev-tagged memory.

**Recommendation**: Option C.

---

## 4.7 Semantic Search and Storage Model (Direct Answer)

We should explicitly keep both:

1. **File-based memory artifacts**:
   - `MEMORY.md`, `memory/YYYY-MM-DD.md`, session logs.
   - Human-auditable source of truth and easy inspection/export.
2. **Database/index layer**:
   - vector index (ChromaDB/LanceDB/SQLite as configured),
   - lightweight lexical index where useful.
   - Provides semantic retrieval performance.

This is the recommended 80/20 model:

1. Files for transparency and portability.
2. Index/database for retrieval quality and speed.

Semantic search is mandatory in the target design. It should remain first-class and not be treated as optional "nice to have."

---

## 4.8 Voyage-Informed Enhancements (No Architecture Pivot Required)

Voyage-like integration informs implementation details, not a full architecture change.

### 4.8.1 Query vs Document Embedding Modes

Provider contract should support different embedding intent:

1. `embed_query()` for retrieval queries.
2. `embed_document_batch()` for indexed content.

Why:

1. Retrieval quality can improve when provider receives explicit query/document intent.
2. Avoids silent quality regression when moving providers.

### 4.8.2 Batch Embedding Policy

Batch indexing should be optional and policy-gated:

1. Enable where stable and cost-effective.
2. Auto-fallback to non-batch on repeated failures.
3. Keep indexing continuity as higher priority than batch throughput.

Why:

1. Better resilience during provider/API instability.
2. Keeps memory pipeline reliable during development and production.

### 4.8.3 Reranking Scope (80/20)

Do not make reranking a day-1 requirement.

1. Day-1: semantic-first retrieval + lexical fallback.
2. Phase-2+: add rerank stage only if metrics show meaningful recall/precision gain.

Why:

1. Reranking adds latency and complexity.
2. Most value is usually captured by good embeddings + sensible retrieval fusion.

**Recommendation**:

1. Add provider intent modes now (`query` vs `document` embedding semantics).
2. Keep reranking behind a separate feature gate and data-driven adoption criteria.

---

## 5. Recommended Target Architecture

Introduce a **Memory Orchestrator** as control plane, leaving storage/search engines behind adapters.

### 5.1 Logical Components

1. `MemoryOrchestrator`
   - Single entrypoint for memory writes, flushes, indexing, and retrieval.
2. `MemoryPolicyEngine`
   - Applies config and routing rules (which source is active/shadow/off).
3. `MemoryIngestionPipeline`
   - Handles deterministic capture, agentic distillation, and classification.
4. `MemoryIndexCoordinator`
   - Manages session/memory file indexing cadence and thresholds.
5. `MemoryRetrievalBroker`
   - Queries multiple sources and merges ranked results.
6. `MemoryTelemetry`
   - Emits metrics/events for flush/index/search quality and freshness.
7. `EmbeddingProviderAdapter`
   - Encapsulates provider selection, auth, and query/document embedding intent handling.

80/20 simplification rule for architecture:

1. Start with one active retrieval strategy: semantic + basic lexical fallback.
2. Defer advanced weighting/reranking until quality data says it is needed.
3. Prefer fewer knobs with clear defaults over broad tuning surfaces.

### 5.2 Adapter Boundary

Initial adapters:

1. `UAFileMemoryAdapter` (current `universal_agent.memory` path).
2. `MemorySystemAdapter` (current `Memory_System` path).
3. `LettaAdapter` (dormant/off initially).
4. Optional future adapters (remote vector stores, managed memory APIs).

### 5.3 Data Flow (Recommended Happy Path)

1. Turn runs.
2. Pre-compact threshold/event triggers hybrid flush:
   - deterministic snapshot written immediately.
   - agentic distillation run (best-effort).
3. Session delta tracker marks transcript growth.
4. Background index worker updates configured session source(s), with forced final index on session end.
5. Retrieval requests call broker:
   - query active sources.
   - merge/rank results.
   - include source + confidence metadata.

---

## 6. Proposed Config Contract (Draft)

```yaml
memory:
  orchestrator:
    enabled: true
    mode: unified              # unified | legacy

  adapters:
    ua_file_memory:
      state: active            # active | shadow | off | deprecated
    memory_system:
      state: shadow
    letta:
      state: off

  flush:
    enabled: true
    mode: hybrid               # deterministic | agentic | hybrid
    deterministic:
      on_pre_compact: true
      on_exit: true
      max_chars: 4000
    agentic:
      enabled: true
      timeout_ms: 12000
      prompt_profile: default
      fallback_on_error: true

  session_memory:
    enabled: true
    sources: ["memory", "sessions"]
    experimental: true
    index_on_session_end: true
    delta:
      bytes: 100000
      messages: 50
    debounce_ms: 1500
    background_sync: true

  retrieval:
    enabled: true
    broker: unified
    strategy: semantic_first       # semantic_first | lexical_only | hybrid
    rerank:
      enabled: false
      provider: none               # none | voyage | qmd | other
      top_k: 20
    max_results: 8

  embeddings:
    provider: local                # local | openai | gemini | voyage
    mode:
      query_intent: true           # provider gets explicit query embedding intent when supported
      document_intent: true        # provider gets explicit document embedding intent when supported
    batch:
      enabled: true
      auto_disable_on_failures: true
      failure_threshold: 3
      fallback_to_non_batch: true

  profile:
    mode: dev_standard             # prod | dev_standard | dev_memory_test | dev_no_persist
    tag_dev_writes: true
    prune:
      enabled: true
      max_dev_age_days: 7

  observability:
    emit_metrics: true
    emit_runlog_events: true
    debug_decisions: false
```

### Why this config shape

1. Makes active path explicit.
2. Separates policy concerns (flush/index/retrieval).
3. Supports dormant systems without code deletion.
4. Supports staged rollout by flipping states/modes.
5. Keeps complexity bounded while preserving semantic retrieval.
6. Makes provider upgrades (e.g., Voyage) additive rather than architectural rewrites.

---

## 7. Implementation Choices (Recommended Defaults)

1. Default runtime mode: `orchestrator.mode = unified`.
2. Default active adapter: `ua_file_memory`.
3. Keep `memory_system` in `shadow` initially for validation and migration confidence.
4. Keep `letta` in `off` initially.
5. Default flush mode: `hybrid`.
6. Default session memory: `enabled` (incremental + session-end indexing).
7. Default retrieval: unified broker using `semantic_first`.
8. Default profile for day-to-day local dev: `dev_standard`.
9. Default rerank: `off` (enable only with demonstrated gains).
10. Provider contract: query/document embedding intent required where supported.

---

## 8. Migration Plan

## Phase 0: Preparation

1. Add orchestrator interfaces and adapter contracts.
2. Add lifecycle state config (`active|shadow|off|deprecated`).
3. Add no-op Letta adapter to preserve future pathway.

Exit criteria:

1. No runtime behavior change yet.
2. Existing tests still pass.

## Phase 1: Canonical Write Path

1. Route all pre-compact and manual memory writes through orchestrator.
2. Keep current write target behavior via `ua_file_memory` active adapter.
3. Mirror selected writes to `memory_system` shadow adapter.

Exit criteria:

1. Memory writes observable from one control path.
2. No loss vs current deterministic flush behavior.

## Phase 2: Hybrid Flush

1. Keep deterministic capture as hard guarantee.
2. Add agentic distillation step with strict timeout and fallback.
3. Track flush outcome metrics (`deterministic_ok`, `agentic_ok`, `agentic_timeout`).

Exit criteria:

1. Deterministic capture success rate near 100%.
2. Agentic failures do not block execution.

## Phase 3: Session Indexing (Incremental)

1. Add transcript delta watcher.
2. Enable background indexing pipeline behind config.
3. Add forced final index on session end.
4. Keep fast disable switch for emergency rollback.

Exit criteria:

1. Indexing overhead bounded.
2. Retrieval freshness lag within agreed SLO.
3. Short single-run sessions become searchable after completion.

## Phase 4: Unified Retrieval Broker

1. Route memory query tools through broker.
2. Merge/rank across active sources.
3. Return source attribution and confidence.

Exit criteria:

1. Improved recall on memory retrieval scenarios.
2. Broker logs explain why results were returned.

## Phase 4.5: Provider Intent Hardening

1. Add `embed_query` vs `embed_document_batch` interfaces to provider adapters.
2. Ensure configured providers respect intent semantics when supported.
3. Validate retrieval quality against baseline using fixed test set.

Exit criteria:

1. No regression in retrieval quality after provider switch.
2. Provider-specific behavior is explicit and test-covered.

## Phase 5: Optional Backend Activation

1. Evaluate Letta or other adapters in `shadow` first.
2. Compare recall quality, latency, and operational complexity.
3. Promote only if materially better.

Exit criteria:

1. Data-driven decision with benchmark evidence.

---

## 9. Observability and Quality Gates

Track these minimum metrics:

1. Flush:
   - pre-compact triggers,
   - deterministic success rate,
   - agentic success/timeout/error rates.
2. Indexing:
   - indexed files/sessions count,
   - queue depth,
   - freshness lag.
3. Retrieval:
   - query count,
   - hit rate by source,
   - average top score,
   - no-result rate.
   - duplicate-rate in top-K for repetitive dev runs.
   - provider-switch quality deltas (same query set across providers).
4. Safety:
   - memory write failures,
   - malformed entries,
   - oversized payload rejections.

Quality gates before broad enablement:

1. No memory-loss regressions in compaction scenarios.
2. Session indexing does not materially degrade turn latency.
3. Unified retrieval improves recall on representative tasks.
4. Dev profile pruning keeps low-value duplicate memory under control.

---

## 10. Risks and Mitigations

1. Risk: Duplicate/conflicting memories from dual writes.
   - Mitigation: idempotency keys + dedupe hash + source tagging.
2. Risk: Agentic distillation writes low-quality memory.
   - Mitigation: strict prompt policy, max write quotas, confidence tagging.
3. Risk: Session indexing cost growth.
   - Mitigation: delta thresholds, debounce, cap windows, prune policies.
4. Risk: Tooling confusion during migration.
   - Mitigation: broker-first routing and clear deprecation notices.
5. Risk: Dev/test noise pollutes long-term memory quality.
   - Mitigation: profiles, write thresholds, dedupe, tag-based prune.

---

## 11. Explicit Choices to Review Together

These are the concrete choices needing your approval before implementation:

1. Canonical architecture: orchestrator + adapters (yes/no).
2. Initial active adapter: `ua_file_memory` (or alternative).
3. Hybrid flush default (or deterministic-only for first release).
4. Session memory default: `on` with incremental + session-end indexing.
5. Shadow strategy:
   - keep `memory_system` in shadow for N weeks.
   - keep `letta` off but registered.
6. Dev profile defaults and dashboard toggles (which are day-1 vs phase-2).
7. Provider strategy:
   - which provider is default day-1 (`local` vs `openai/gemini/voyage`).
   - whether provider-intent mode is mandatory.
8. Rerank adoption gate:
   - metrics threshold required before enabling.
9. Unified retrieval broker adoption order (early or after indexing phase).

---

## 12. Final Recommendation

Adopt the orchestrator + adapter model with hybrid flush and incremental session indexing, while preserving non-active systems as dormant via explicit lifecycle states. Treat session memory and long-term memory as separate first-class concerns. Keep semantic retrieval first-class, enforce provider/backend separation, add query/document embedding intent support, and keep reranking gated behind evidence. Use dev profiles + pruning to prevent test pollution. This yields the best tradeoff across effectiveness, latency, and complexity.

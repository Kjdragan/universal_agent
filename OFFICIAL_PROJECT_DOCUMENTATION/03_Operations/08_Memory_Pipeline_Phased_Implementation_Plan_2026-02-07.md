# 08 Memory Pipeline Phased Implementation Plan (2026-02-07)

## Purpose

This is the execution plan for implementing the revised memory architecture defined in:

1. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/06_Detailed_Memory_Pipeline_Recommendations_2026-02-07.md`
2. `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/07_Clawdbot_Voyage_Memory_Architecture_Assessment_2026-02-07.md`

This document is intended to be the living source plan and should be updated as implementation progresses.

---

## Scope

This plan covers:

1. Architecture convergence to one active memory control path.
2. Explicit handling of two memory classes:
   - session memory,
   - long-term memory.
3. Semantic retrieval as first-class functionality.
4. Development anti-pollution controls.
5. Provider/backend separation and provider-intent embedding semantics.

This plan does not include immediate broad production rollout; rollout happens after phase gates are met.

---

## Working Rules

1. No phase advances without passing all required phase tests and gate criteria.
2. Keep dormant integrations (for example, Letta) available behind explicit off/shadow states.
3. Prefer 80/20 implementation choices in early phases; defer high-complexity refinements until metrics justify them.
4. Keep rollback path available at each phase.

---

## Baseline Decisions (Locked for Implementation Start)

1. Canonical runtime control path: orchestrator-based unified mode.
2. Two memory classes are first-class: session and long-term.
3. Session memory default target: on, incremental indexing, with session-end final indexing.
4. Retrieval default target: semantic-first with lexical fallback.
5. Reranking: off by default; only enabled with data-backed gate.
6. Provider/backend boundary is mandatory.

---

## Phase Map

1. Phase 0: Baseline, contracts, and safety rails.
2. Phase 1: Orchestrator scaffold + adapter lifecycle states.
3. Phase 2: Canonical write path (deterministic path first).
4. Phase 3: Hybrid flush (add agentic distillation with fallback).
5. Phase 4: Session memory indexing (incremental + session-end index).
6. Phase 5: Unified retrieval broker (semantic-first).
7. Phase 6: Provider-intent hardening (`query` vs `document` embedding intent).
8. Phase 7: Dev anti-pollution profile controls + dashboard toggle surface.
9. Phase 8: Shadow validation, stabilization, and rollout readiness.

---

## Phase 0: Baseline, Contracts, Safety Rails

### Objectives

1. Freeze baseline behavior and metrics.
2. Define implementation interfaces and config contract skeleton.
3. Ensure safe fallback to current legacy path.

### Implementation Tasks

1. Add architecture notes + module stubs:
   - `src/universal_agent/memory/orchestrator.py` (or equivalent package path).
   - `src/universal_agent/memory/adapters/` contract stubs.
2. Add initial config parsing keys (no behavior switch yet):
   - orchestrator mode,
   - adapter states,
   - session memory controls,
   - retrieval strategy,
   - embedding provider intent flags,
   - profile mode.
3. Add explicit `legacy` mode fallback that preserves current behavior.
4. Add structured run-log tags for memory events (off by default if needed).

### Required Tests

1. Unit:
   - config parsing for new keys with defaults.
   - `legacy` mode remains default/compatible until activated.
2. Existing regressions to run:
   - `tests/memory/test_file_memory_store.py`
   - `tests/memory/test_memory_tool.py`
   - `tests/memory/test_memory_indexing.py`
   - `tests/integration/test_memory_integration.py`

### Gate (Go/No-Go)

1. New config keys parse without breaking current runs.
2. No behavior change when unified mode is disabled.
3. Baseline metrics captured and stored for comparison.

### Rollback

1. Set `orchestrator.mode=legacy` and bypass new control path.

---

## Phase 1: Orchestrator Scaffold + Adapter Lifecycle States

### Objectives

1. Introduce orchestrator as the single control entrypoint.
2. Register adapters with explicit lifecycle states:
   - `active`, `shadow`, `off`, `deprecated`.

### Implementation Tasks

1. Implement orchestrator core operations:
   - `write_memory_event(...)`,
   - `search_memory(...)`,
   - `sync_session_memory(...)`,
   - `flush_memory(...)`.
2. Implement adapter registry + routing policy.
3. Integrate initial adapters:
   - UA file memory adapter (active),
   - Memory_System adapter (shadow),
   - Letta adapter (off placeholder).
4. Add adapter decision logging (which adapters ran and why).

### Required Tests

1. Unit:
   - adapter routing by state.
   - shadow-write mirror behavior.
   - off/deprecated adapter behavior.
2. Integration:
   - orchestrator in unified mode routes to active adapter.
   - shadow adapter does not affect active return values.
3. Existing regressions:
   - `tests/memory/test_memory_hybrid.py`
   - `tests/reproduction/test_session_persistence.py`

### Gate (Go/No-Go)

1. Orchestrator routing is deterministic and test-covered.
2. Shadow path cannot break active path.
3. Letta remains safely off unless explicitly enabled.

### Rollback

1. Flip back to `orchestrator.mode=legacy`.

---

## Phase 2: Canonical Write Path (Deterministic First)

### Objectives

1. Route memory writes through orchestrator.
2. Preserve deterministic reliability before adding agentic complexity.

### Implementation Tasks

1. Route deterministic pre-compact snapshots through orchestrator write API.
2. Route explicit memory tool writes through orchestrator.
3. Ensure write tags include:
   - memory class (`session` or `long_term`),
   - source,
   - environment/profile tags.
4. Add write dedupe keys (hash + source + time bucket).

### Required Tests

1. Unit:
   - deterministic write always succeeds (when storage available).
   - dedupe prevents repeated duplicate inserts under test loops.
2. Integration:
   - pre-compact hook writes through orchestrator.
   - tool writes still visible to current memory retrieval.
3. Existing regressions:
   - `tests/memory/test_vector_memory_integration.py`
   - `tests/memory/test_vector_index.py`

### Gate (Go/No-Go)

1. No memory-loss regression on deterministic path.
2. Duplicate write rate is reduced under repeated test workloads.

### Rollback

1. Route write path back to current direct memory functions.

---

## Phase 3: Hybrid Flush (Agentic Distillation Added)

### Objectives

1. Keep deterministic write as hard guarantee.
2. Add optional agentic distillation as quality enhancer.

### Implementation Tasks

1. Implement flush mode policy:
   - `deterministic`,
   - `agentic`,
   - `hybrid`.
2. In `hybrid` mode:
   - run deterministic capture first,
   - run agentic distillation with timeout and fallback.
3. Add safeguards:
   - maximum distillation runtime,
   - write budget limits,
   - non-blocking failure behavior.

### Required Tests

1. Unit:
   - mode routing correctness.
   - timeout fallback behavior.
   - failure isolation (agentic failure cannot cancel deterministic capture).
2. Integration:
   - pre-compact event produces deterministic record plus optional distilled entries.
3. Existing regressions:
   - `tests/unit/test_crash_hooks.py`
   - `tests/test_hooks_workspace_guard.py`

### Gate (Go/No-Go)

1. Deterministic flush success remains near baseline 100%.
2. Agentic failures do not block turn completion.
3. Flush telemetry emitted for both stages.

### Rollback

1. Set `flush.mode=deterministic`.

---

## Phase 4: Session Memory Indexing (Incremental + Session-End)

### Objectives

1. Make session memory first-class and practical for short and long runs.
2. Avoid heavy per-turn full reindexing.

### Implementation Tasks

1. Implement transcript delta tracker:
   - bytes threshold,
   - message threshold,
   - debounce window.
2. Implement background incremental indexing worker.
3. Add session-end forced index pass.
4. Add fast disable switch for session indexing.
5. Ensure session memory tagged as separate class from long-term memory.

### Required Tests

1. Unit:
   - delta threshold logic.
   - debounce scheduling.
   - session-end index trigger.
2. Integration:
   - short run becomes searchable post-session.
   - long run updates index incrementally.
3. Performance:
   - compare turn latency with indexing on/off under representative load.
4. Existing regressions:
   - `tests/reproduction/test_session_persistence.py`
   - `tests/gateway/test_execution_engine_logging.py`

### Gate (Go/No-Go)

1. Session memory freshness SLO met.
2. Latency overhead within agreed budget.
3. Searchability confirmed for completed single-run sessions.

### Rollback

1. Set `session_memory.enabled=false`.

---

## Phase 5: Unified Retrieval Broker (Semantic-First)

### Objectives

1. Query session and long-term memory via one broker.
2. Keep semantic retrieval first-class with lexical fallback.

### Implementation Tasks

1. Implement broker query flow:
   - source fan-out to active adapters,
   - merge and rank,
   - source attribution in output.
2. Implement retrieval strategy:
   - `semantic_first` default.
3. Add duplicate collapse in top-K results.
4. Route existing memory search tool surfaces through broker.

### Required Tests

1. Unit:
   - source merge/rank.
   - duplicate collapse.
   - no-result behavior.
2. Integration:
   - existing memory tools return broker-backed results.
3. Relevance validation:
   - fixed query set measured against baseline (hit rate, top score, useful@k).
4. Existing regressions:
   - `tests/memory/test_memory_tool.py`
   - `tests/integration/test_memory_integration.py`

### Gate (Go/No-Go)

1. Retrieval quality >= baseline on agreed dataset.
2. Broker logs clearly explain result provenance.

### Rollback

1. Route search tool surfaces back to legacy search path.

---

## Phase 6: Provider-Intent Hardening (Voyage-Informed)

### Objectives

1. Enforce provider/backend separation.
2. Support query/document embedding intent semantics across providers.

### Implementation Tasks

1. Define provider adapter contract:
   - `embed_query(text)`,
   - `embed_document_batch(texts)`.
2. Ensure provider configuration remains independent from backend selection.
3. Add fallback strategy for providers lacking explicit intent support.
4. Keep batch embedding policy optional with auto-disable on failures.

### Required Tests

1. Unit:
   - provider contract compliance.
   - fallback behavior when intent mode unavailable.
2. Integration:
   - provider switch does not alter backend behavior.
3. Quality tests:
   - provider-switch regression set for retrieval quality.
4. Existing regressions:
   - `tests/memory/test_chromadb_backend.py`
   - `tests/memory/test_lancedb_backend.py`

### Gate (Go/No-Go)

1. No regression on retrieval quality after provider switch.
2. Provider intent behavior is explicit and test-covered.

### Rollback

1. Force provider to previous stable default.
2. Disable batch mode and use non-batch embeddings.

---

## Phase 7: Dev Anti-Pollution Controls + Dashboard Toggles

### Objectives

1. Prevent memory pollution during repetitive development testing.
2. Provide operationally practical toggles.

### Implementation Tasks

1. Implement profile modes:
   - `prod`,
   - `dev_standard`,
   - `dev_memory_test`,
   - `dev_no_persist`.
2. Implement write policy controls:
   - minimum importance threshold,
   - duplicate suppression thresholds,
   - environment/task tagging.
3. Implement prune controls:
   - prune by tag/date/profile.
4. Add dashboard toggles (initial minimal set):
   - session memory on/off,
   - long-term writes on/off,
   - semantic indexing on/off,
   - prune dev-tagged memory.

### Required Tests

1. Unit:
   - profile policy routing.
   - prune selection logic.
2. Integration:
   - `dev_no_persist` prevents long-term writes.
   - `dev_memory_test` keeps writes but tags them for cleanup.
3. Manual UI verification:
   - toggle actions apply to runtime config safely.

### Gate (Go/No-Go)

1. Repetitive dev runs do not substantially degrade retrieval quality.
2. Cleanup operation is fast and predictable.
3. Toggle changes are observable and reversible.

### Rollback

1. Disable toggles and keep CLI/env-only controls.

---

## Phase 8: Shadow Validation, Stabilization, Rollout Readiness

### Objectives

1. Validate unified pipeline against shadow paths and baseline behavior.
2. Produce release readiness report.

### Implementation Tasks

1. Compare active vs shadow retrieval outcomes on benchmark queries.
2. Validate long-term memory integrity after extended test cycles.
3. Confirm no critical regressions in gateway and session behavior.
4. Produce final readiness assessment and promotion recommendation.

### Required Tests

1. Full targeted memory suite.
2. Critical gateway and session regression suite:
   - `tests/gateway/test_gateway.py`
   - `tests/gateway/test_gateway_integration.py`
   - `tests/gateway/test_ops_api.py`
3. End-to-end smoke:
   - `tests/stabilization/test_smoke_gateway.py`

### Gate (Go/No-Go)

1. Memory SLOs met (quality, latency, freshness, reliability).
2. Rollback plan validated.
3. Open risks accepted or resolved.

---

## Test Matrix by Phase

| Phase | Unit | Integration | Performance | Manual/UI | Required for Advance |
| --- | --- | --- | --- | --- | --- |
| 0 | Yes | Yes | No | No | Yes |
| 1 | Yes | Yes | No | No | Yes |
| 2 | Yes | Yes | No | No | Yes |
| 3 | Yes | Yes | Optional | No | Yes |
| 4 | Yes | Yes | Yes | Optional | Yes |
| 5 | Yes | Yes | Optional | No | Yes |
| 6 | Yes | Yes | Optional | No | Yes |
| 7 | Yes | Yes | Optional | Yes | Yes |
| 8 | Yes | Yes | Yes | Yes | Yes |

---

## Minimum Metrics to Track Throughout

1. Write reliability:
   - deterministic flush success,
   - agentic flush success/timeout/error,
   - write failure rate.
2. Indexing:
   - indexing queue depth,
   - freshness lag,
   - session-end indexing completion rate.
3. Retrieval quality:
   - hit rate,
   - useful@k,
   - no-result rate,
   - duplicate top-k rate.
4. Latency:
   - p50/p95 retrieval latency,
   - p50/p95 turn latency impact when memory features enabled.
5. Dev pollution:
   - growth of low-value duplicate entries over repeated dev runs.

---

## Change-Control Process for This Plan

When modifying this plan:

1. Update phase status table below.
2. Add dated rationale in decision log.
3. Record test evidence links (run logs, reports, screenshots).

### Phase Status Tracker

| Phase | Status | Owner | Start Date | End Date | Gate Result | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | Planned | TBD |  |  |  |  |
| 1 | Planned | TBD |  |  |  |  |
| 2 | Planned | TBD |  |  |  |  |
| 3 | Planned | TBD |  |  |  |  |
| 4 | Planned | TBD |  |  |  |  |
| 5 | Planned | TBD |  |  |  |  |
| 6 | Planned | TBD |  |  |  |  |
| 7 | Planned | TBD |  |  |  |  |
| 8 | Planned | TBD |  |  |  |  |

### Decision Log

| Date | Decision | Rationale | Impacted Phases | Approved By |
| --- | --- | --- | --- | --- |
| 2026-02-07 | Initial phased plan created from Doc 6/7 | Establish source-of-truth implementation plan | 0-8 | Pending |

---

## Immediate Next Step

Start Phase 0 implementation with strict no-behavior-change guardrails, and collect baseline metrics before enabling unified mode.


# OpenClaw Functionality Parity Evaluation and UA Recommendations

Date: 2026-02-20  
Status: Analysis complete, implementation pending approval  
Scope: OpenClaw re-evaluation and UA parity recommendations (documentation only)

## Executive Summary

UA has made substantial progress on memory hard-cut direction, but parity with current OpenClaw behavior is still incomplete in key runtime paths.

What is working:

1. Canonical memory tools exist in UA (`memory_search`, `memory_get`).
2. Canonical session memory policy shape exists (`enabled`, `sessionMemory`, `sources`, `scope`).
3. Shared persistent memory artifacts are present and populated under `Memory_System/ua_shared_workspace`.
4. Memory-focused test suite is green in current environment (`32 passed, 1 skipped`).

What is broken or misaligned:

1. Prompt/runtime memory guidance still references legacy internal tool names in several paths.
2. UA still contains legacy global memory transplant behavior (`_inject_global_memory`, `_persist_global_memory`) that conflicts with canonical shared-memory model.
3. UA session creation does not provide deterministic OpenClaw-style key-file bootstrap lifecycle.
4. UA lacks OpenClaw-equivalent session rollover memory capture behavior (`/new` session-memory hook pattern).
5. Heartbeat behavior in UA can suppress proactive reliability due to idle-unregister and visibility gating choices.
6. UA does not yet have explicit OpenClaw-style heartbeat empty-content short-circuit.

What to fix first:

1. P0: tool contract guidance mismatch.
2. P0: remove legacy global memory transplant path.
3. P0: deterministic key-file bootstrap parity in session creation.

## Baseline and Evidence Used

## OpenClaw baseline commit

1. Repository: `/home/kjdragan/lrepos/clawdbot`
2. Commit: `7ce357ff8`
3. Commit date: `2026-02-19`

## OpenClaw sources inspected

1. `src/agents/workspace.ts`
2. `src/hooks/bundled/session-memory/handler.ts`
3. `src/hooks/bundled/session-memory/HOOK.md`
4. `src/agents/tools/memory-tool.ts`
5. `src/agents/memory-search.ts`
6. `src/auto-reply/heartbeat.ts`
7. `src/infra/heartbeat-runner.ts`
8. `src/infra/heartbeat-wake.ts`
9. `src/auto-reply/reply/agent-runner-memory.ts`
10. Supporting docs under `docs/*` for memory/heartbeat/bootstrap patterns.

## UA sources inspected

1. `src/universal_agent/session_policy.py`
2. `src/universal_agent/memory/orchestrator.py`
3. `src/universal_agent/tools/memory.py`
4. `src/universal_agent/prompt_builder.py`
5. `src/universal_agent/agent_setup.py`
6. `src/universal_agent/main.py`
7. `src/universal_agent/gateway.py`
8. `src/universal_agent/gateway_server.py`
9. `src/universal_agent/heartbeat_service.py`
10. `src/universal_agent/feature_flags.py`
11. Existing parity doc: `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/31_UA_Memory_Hard_Cut_OpenClaw_Parity_2026-02-20.md`
12. Runtime memory artifacts under `Memory_System/ua_shared_workspace/*`.

## Validation evidence

1. Command: `./.venv/bin/pytest -q tests/memory tests/integration/test_memory_integration.py`
2. Result: `32 passed, 1 skipped`

## OpenClaw Functional Baseline (Current)

## Memory lifecycle and contract

1. OpenClaw uses workspace-first durable memory files (`MEMORY.md` and `memory/*.md`) as canonical long-term memory substrate.
2. Runtime memory tool contract is strict and first-class:
   - `memory_search`
   - `memory_get`
3. Memory retrieval includes snippet-centric results with path/line metadata and optional citation decoration behavior.
4. Search manager supports backend abstraction, sync-on-start/search/watch, and session memory pathways.

## Session-memory capture behavior

1. OpenClaw ships bundled `session-memory` hook for `command:new`.
2. On `/new`, it resolves prior session transcript context, extracts recent messages, generates a descriptive slug, and writes a dated memory file.
3. This creates automatic continuity from normal conversational use without requiring manual memory operations.

## Workspace bootstrap and key files

1. OpenClaw workspace lifecycle seeds and governs key files:
   - `AGENTS.md`
   - `SOUL.md`
   - `TOOLS.md`
   - `IDENTITY.md`
   - `USER.md`
   - `HEARTBEAT.md`
   - `BOOTSTRAP.md`
2. This provides deterministic persona, identity, safety, and operational behavior from session start.

## Heartbeat behavior and proactive runtime

1. OpenClaw heartbeat stack includes:
   - wake/retry/coalescing behavior
   - explicit heartbeat prompt/token handling
   - output stripping and heartbeat acknowledgment handling
2. Heartbeat utility includes behavior to avoid unnecessary low-value runs, including logic around effectively empty heartbeat instructions.
3. Proactive workflow is reinforced by cron-vs-heartbeat design separation in docs and runtime patterns.

## Pre-compaction memory flush

1. OpenClaw includes memory flush logic tied to context pressure and compaction cycle progression.
2. Flush behavior records metadata and avoids repeated uncontrolled flushes during same cycle.

## UA Current-State Assessment

## What UA already matches

1. Canonical memory tools are present in `src/universal_agent/tools/memory.py`.
2. Session memory policy schema is aligned at high level in `src/universal_agent/session_policy.py`.
3. Canonical orchestration path exists in `src/universal_agent/memory/orchestrator.py`.
4. Shared memory root now stores durable memory artifacts outside transient session workspace.
5. Memory module tests currently pass.

## What UA only partially matches

1. Heartbeat system is present and configurable, but operational behavior diverges in important lifecycle details.
2. Session transcript indexing exists, but does not yet match OpenClaw’s `/new`-triggered session memory capture semantics.
3. Key files exist in repo assets, but are not deterministically bootstrapped per session workspace lifecycle in parity form.

## What UA currently diverges on

1. Prompt and runtime guidance still references legacy internal memory tool naming:
   - `mcp__internal__memory_search`
   - `mcp__internal__memory_get`
2. `main.py` still has legacy global memory copy-in/copy-back path:
   - `_inject_global_memory`
   - `_persist_global_memory`
3. Session creation path does not seed OpenClaw-equivalent key-file set with lifecycle semantics.
4. Heartbeat visibility and idle-unregister behavior can suppress proactive continuity for inactive but still relevant sessions.

## Parity Matrix

| Capability | OpenClaw Behavior | UA Behavior | Status | User Impact | Severity |
|---|---|---|---|---|---|
| Canonical memory tool names | `memory_search`, `memory_get` only | Tools exist, but guidance still references legacy internal names in prompt/runtime text | partial | Tooling confusion and inconsistent agent behavior | P0 |
| Persistent memory root | Workspace-level durable memory files in canonical workspace | Shared persistent root exists (`Memory_System/ua_shared_workspace`) | parity | Positive continuity baseline | P1 |
| Legacy memory path removal | Single canonical path | Legacy transplant functions still active in `main.py` | divergent | Reintroduces drift and duplication risk | P0 |
| Session rollover memory capture | Bundled `/new` hook writes dated session memory note | No direct equivalent workflow | missing | Lost continuity from normal session resets | P1 |
| Key-file bootstrap lifecycle | Deterministic seeded key files and onboarding flow | No deterministic parity bootstrap during session creation | missing | Persona/proactive inconsistency across sessions | P0 |
| Heartbeat coalescing/wake robustness | Explicit wake state machine and retries | Heartbeat exists; behavior differs and can be suppressed by session idle policies | partial | Proactive reliability degradation | P1 |
| Heartbeat empty-content skip | Explicit handling for effectively empty heartbeat content | No equivalent explicit short-circuit parity behavior identified | partial | Token and cycle waste risk | P1 |
| Pre-compaction memory flush | Integrated with compaction lifecycle metadata | Flush exists in UA, but lifecycle parity details differ | partial | Lower quality continuity near compaction boundaries | P1 |
| Memory source/scope controls | Configurable search behavior and source selection | UA has `sources` and `scope` policy fields | parity | Correct policy foundation | P2 |
| Legacy config surface cleanup | Canonicalized behavior with clear config intent | UA feature flags still expose legacy/compat surfaces | partial | Operator confusion, drift | P2 |

## Prioritized Recommendations

## P0-1: Fix memory tool guidance mismatch in prompt and runtime wiring

Problem:

1. UA guidance paths still mention internal legacy names despite canonical tool contract.

Why it matters:

1. Weakens parity and can misguide model behavior away from canonical memory tools.

Touchpoints:

1. `src/universal_agent/prompt_builder.py`
2. `src/universal_agent/agent_setup.py`
3. `src/universal_agent/main.py`

Expected outcome:

1. All runtime/prompt guidance consistently directs usage of `memory_search` and `memory_get`.

Risk/dependencies:

1. Low risk.
2. Requires consistency audit across prompt and policy text emitters.

## P0-2: Remove legacy global memory transplant path from runtime

Problem:

1. `_inject_global_memory` and `_persist_global_memory` keep copy-based legacy behavior alive.

Why it matters:

1. Conflicts with hard-cut canonical memory model and creates state divergence risk.

Touchpoints:

1. `src/universal_agent/main.py`

Expected outcome:

1. Single canonical memory root behavior, no copy-in/copy-back stage.

Risk/dependencies:

1. Medium risk if any remaining path assumes local copies.
2. Must confirm no hidden callers depend on these side effects.

## P0-3: Add deterministic key-file bootstrap parity on session creation

Problem:

1. UA lacks deterministic OpenClaw-style key-file seeding lifecycle for new session workspaces.

Why it matters:

1. Prevents stable identity/proactive behavior foundation across sessions.

Touchpoints:

1. `src/universal_agent/gateway.py`
2. New helper module under `src/universal_agent/workspace/` (recommended)

Expected outcome:

1. Session workspace starts with required key files and controlled initialization behavior.

Risk/dependencies:

1. Medium risk.
2. Requires agreement on template precedence and overwrite policy.

## P1-1: Implement session-turn memory capture equivalent to OpenClaw `/new` hook

Problem:

1. UA does not automatically convert session rollover into durable memory note artifacts.

Why it matters:

1. Manual memory writing burden remains too high; continuity quality degrades over time.

Touchpoints:

1. `src/universal_agent/gateway_server.py`
2. `src/universal_agent/memory/orchestrator.py`
3. Related session lifecycle/hook integration paths

Expected outcome:

1. Session rollover/finalization writes deduped, timestamped memory slices with provenance.

Risk/dependencies:

1. Medium risk.
2. Requires clear lifecycle trigger points for chat/webhook/telegram/session resets.

## P1-2: Heartbeat operational parity hardening

Problem:

1. Idle unregister and visibility logic can suppress intended proactive processing.

Why it matters:

1. Core proactive function becomes unreliable in real production cadence.

Touchpoints:

1. `src/universal_agent/heartbeat_service.py`
2. `src/universal_agent/gateway_server.py`

Expected outcome:

1. Reliable proactive heartbeat execution while still suppressing noisy calendar clutter.

Risk/dependencies:

1. Medium risk.
2. Must preserve anti-noise UX while improving run reliability.

## P1-3: Add HEARTBEAT empty-content short-circuit parity

Problem:

1. UA does not explicitly mirror OpenClaw behavior for skipping no-op heartbeat calls when file content has no actionable items.

Why it matters:

1. Unnecessary cycle/token use and reduced signal quality.

Touchpoints:

1. `src/universal_agent/heartbeat_service.py`

Expected outcome:

1. No-op heartbeat runs are proactively skipped when instruction surface is effectively empty.

Risk/dependencies:

1. Low risk.
2. Requires robust “effectively empty” detection semantics.

## P1-4: Complete key-file lifecycle integration with memory/proactive loop

Problem:

1. Key files exist but are not yet fully integrated into memory refresh and proactive runtime loop in parity form.

Why it matters:

1. Prevents compounding improvement in identity and proactive behavior over time.

Touchpoints:

1. `src/universal_agent/prompt_builder.py`
2. `src/universal_agent/heartbeat_service.py`
3. Memory write/flush pipeline modules

Expected outcome:

1. Key files and durable memory reinforce each other and improve behavior continuity.

Risk/dependencies:

1. Medium risk.
2. Needs strict guardrails on what can be autonomously updated.

## P2-1: Clean remaining legacy/compat memory flags and docs drift

Problem:

1. Legacy compatibility switches remain in feature flag surface and documentation.

Why it matters:

1. Confuses operators and weakens hard-cut guarantees.

Touchpoints:

1. `src/universal_agent/feature_flags.py`
2. `.env.sample`
3. Operations docs

Expected outcome:

1. Single, clear, canonical memory operating model.

Risk/dependencies:

1. Low risk.
2. Requires synchronized code and documentation updates.

## Suggested Implementation Sequence (for next approved phase)

1. Phase A (P0):
   - Normalize memory tool guidance names.
   - Remove legacy global memory transplant path.
   - Add deterministic key-file bootstrap.
2. Phase B (P1):
   - Add session rollover memory capture pipeline.
   - Harden heartbeat reliability and empty-content skip behavior.
3. Phase C (P1/P2):
   - Integrate key-file lifecycle with proactive memory loop.
   - Remove remaining legacy/compat flags and docs drift.
4. Phase D (Validation):
   - End-to-end parity tests and ops runbook updates.

## Acceptance Criteria for Future Implementation

1. Tool contract:
   - All prompt/runtime guidance references `memory_search` and `memory_get` only.
2. Runtime memory path:
   - No runtime copy-in/copy-back of memory between global and session workspace.
3. Session bootstrap:
   - New sessions consistently seed required key files with deterministic policy.
4. Session memory capture:
   - Session reset/rollover emits searchable durable memory notes with provenance.
5. Heartbeat reliability:
   - Heartbeats remain proactive without calendar spam.
   - No-op heartbeat content is skipped.
6. Regression:
   - Non-memory gateway/session execution remains stable.

## Assumptions and Defaults

1. Parity target is functional parity with OpenClaw behavior, not source-level porting.
2. UA architectural differences remain valid; implementation should emulate behavior appropriately.
3. This document is analysis/recommendation only and does not perform implementation.
4. Severity model:
   - P0: correctness/parity blockers
   - P1: proactive reliability and continuity quality
   - P2: cleanup and operational simplification

## Final Recommendation

Proceed with a focused P0 implementation sprint first.  
Without P0 fixes, UA will continue to appear memory-enabled while still behaving inconsistently with OpenClaw’s intended continuity and proactive design.

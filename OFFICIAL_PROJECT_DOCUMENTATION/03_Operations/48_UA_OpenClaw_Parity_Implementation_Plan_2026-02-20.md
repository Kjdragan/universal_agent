# UA OpenClaw-Parity Implementation Plan (Phased P0→P1→P2)

Date: 2026-02-20  
Status: Active implementation blueprint  
Parent analysis: `47_OpenClaw_Functionality_Parity_Evaluation_And_UA_Recommendations_2026-02-20.md`

## Objective

Implement OpenClaw-aligned functionality parity in UA using a low-risk phased rollout:

1. Phase A: P0 blockers
2. Phase B: P1 proactive/continuity parity
3. Phase C: P2 legacy cleanup

## Rollout Contract

1. One implementation branch per phase.
2. One VPS deployment per phase.
3. One numbered verification note per phase with test evidence and observed behavior.

## Phase A (P0) Scope

1. Memory tool guidance normalization:
   - Replace legacy internal tool naming in guidance with canonical behavior guidance.
   - Touchpoints:
     - `src/universal_agent/prompt_builder.py`
     - `src/universal_agent/agent_setup.py`
     - `src/universal_agent/main.py`

2. Remove legacy global memory transplant runtime flow:
   - Eliminate runtime use of `_inject_global_memory` and `_persist_global_memory`.
   - Keep canonical shared-memory-only behavior.
   - Touchpoint:
     - `src/universal_agent/main.py`

3. Deterministic key-file bootstrap parity:
   - Add seed-if-missing bootstrap helper.
   - Wire into session creation.
   - Required artifacts:
     - `AGENTS.md`
     - `SOUL.md`
     - `TOOLS.md`
     - `IDENTITY.md`
     - `USER.md`
     - `HEARTBEAT.md`
     - `BOOTSTRAP.md`
     - `MEMORY.md`
     - `memory/`
   - Touchpoints:
     - `src/universal_agent/gateway.py`
     - `src/universal_agent/workspace/bootstrap.py` (new)
     - Supporting prompt/template assets as needed.

## Phase B (P1) Scope

1. Session-turn memory capture parity:
   - Add lifecycle capture on reset/archive/delete style boundaries.
   - Write durable session memory slices with dedupe/provenance.
   - Touchpoints:
     - `src/universal_agent/gateway_server.py`
     - `src/universal_agent/memory/orchestrator.py`
     - Relevant lifecycle helpers/services

2. Heartbeat operational hardening:
   - Reduce proactive suppression from idle-unregister behavior.
   - Preserve anti-noise UI behavior.
   - Touchpoints:
     - `src/universal_agent/heartbeat_service.py`
     - `src/universal_agent/gateway_server.py`

3. HEARTBEAT empty-content short-circuit:
   - Ensure no-op heartbeat content avoids unnecessary LLM calls.
   - Touchpoint:
     - `src/universal_agent/heartbeat_service.py`

4. Key-file lifecycle integration:
   - Ensure key files and memory/proactive loops reinforce continuity over time.
   - Touchpoints:
     - `src/universal_agent/prompt_builder.py`
     - `src/universal_agent/heartbeat_service.py`
     - Memory write/flush pathways

## Phase C (P2) Scope

1. Remove remaining legacy compatibility memory surfaces.
2. Align env/docs to canonical-only memory operation.
3. Touchpoints:
   - `src/universal_agent/feature_flags.py`
   - `.env.sample`
   - `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/*` relevant docs

## Testing Gates

## Core gates per phase

1. Memory contract and cross-session persistence.
2. Session bootstrap correctness.
3. Session lifecycle memory capture behavior.
4. Heartbeat reliability and no-op suppression.
5. Gateway/webhook/telegram regression stability.

## Required commands

1. `./.venv/bin/pytest -q tests/memory tests/integration/test_memory_integration.py`
2. Targeted gateway and heartbeat suites relevant to changed modules.
3. Web UI checks only if touched:
   - `npm --prefix web-ui run lint`
   - `npm --prefix web-ui run build`

## Verification Artifacts (Post-Deploy)

Each phase ships with a verification note containing:

1. Commit SHA and deployment timestamp.
2. Test command output summary.
3. Runtime checks:
   - memory recall quality
   - heartbeat cadence
   - calendar visibility correctness
   - session reset/close capture behavior
4. Follow-up actions or defects.

## Assumptions

1. Functional parity target is OpenClaw behavior, not line-for-line code parity.
2. Seed-if-missing is non-destructive by default.
3. Canonical shared memory is source of truth.
4. No compatibility aliasing is reintroduced unless explicitly approved.

# DraganCorp Phase A Handoff Context

**Date:** 2026-02-15
**Status:** In Progress (WS-A/WS-B foundation implemented)
**Branch:** `dev-telegram`

## 1) What was completed

### 1.1 Documentation and governance scaffold (committed)

- DraganCorp architecture/governance baseline and Phase A plan were created and committed.
- Program control center is active and now tracks checklist progress and implementation decisions.

### 1.2 Source-control hygiene fix (committed)

- Local runtime DB artifacts were untracked from git and kept ignored.
- Prompt scratch directory was added to ignore rules.

### 1.3 Web UI product work (committed)

- Dashboard/chat flow now supports starting a fresh session and focusing the chat input.

### 1.4 Phase A implementation kickoff (committed)

- Added VP session registry specification.
- Updated mission envelope contract with VP linkage fields (`vp_id`, `vp_session_id`, `vp_runtime_id`).
- Added durable schema for:
  - `vp_sessions`
  - `vp_missions`
  - `vp_events`
- Added durable state APIs for VP session/mission/event lifecycle operations.
- Added/extended durable tests for VP session registry operations.
- Ran test suite:
  - `uv run python -m pytest tests/durable/test_durable_state.py -q` -> **4 passed**.

## 2) Relevant commits (latest first)

1. `affec5e` — `feat(phase-a): add VP registry spec and durable state APIs`
2. `8eb3c5c` — `feat(web-ui): add new session and focus input`
3. `1593196` — `chore: ignore and untrack local state dbs`
4. `9d95388` — `docs: add DraganCorp Phase A scaffold`

## 3) Key files touched in this workstream

### DraganCorp docs/specs

- `DraganCorp/docs/operations/00_DraganCorp_Program_Control_Center.md`
- `DraganCorp/docs/operations/02_Phase_A_Persistent_CODER_VP_Implementation_Plan.md`
- `DraganCorp/specs/mission-envelope-v1.md`
- `DraganCorp/specs/vp-session-registry-v1.md`

### Runtime implementation

- `src/universal_agent/durable/migrations.py`
- `src/universal_agent/durable/state.py`
- `tests/durable/test_durable_state.py`

### Supporting source-control cleanup and UI

- `.gitignore`
- `web-ui/app/dashboard/chat/page.tsx`
- `web-ui/app/dashboard/layout.tsx`
- `web-ui/app/dashboard/page.tsx`
- `web-ui/app/page.tsx`
- `web-ui/lib/chatWindow.ts`
- `web-ui/lib/websocket.ts`

## 4) Current position vs Phase A plan

From the active Phase A checklist perspective:

- **A0 complete**: acceptance contract formalized in specs/docs.
- **A1 complete**: VP registry schema + durable data APIs + tests.
- **A2 next**: implement CODER VP mission dispatch/runtime adapter wiring.
- **A3+ pending**: Simone route integration flag, observability, fallback hardening, broader rollout stages.

## 5) Open risks and caveats

1. VP registry data model exists, but runtime adapter wiring is not yet completed for end-to-end dispatch.
2. Lease/heartbeat primitives are implemented at data-layer level; operational recovery workers/flows still need integration.
3. Need guarded rollout path so Simone can route to CODER VP while preserving `code-writer` fallback.

## 6) Recommended next steps for the next chat

1. Implement **WS-C runtime adapter**:
   - Wire mission dispatch to `vp_sessions` / `vp_missions` lifecycle calls.
   - Persist mission events via `append_vp_event(...)`.
2. Implement **WS-D Simone routing integration** behind feature flag.
3. Add **WS-E observability fields** to logs/metrics around VP lease state, mission status transitions, and fallback usage.
4. Add **WS-F integration tests** for:
   - VP dispatch happy path
   - lease loss/recovery path
   - fallback to `code-writer`

## 7) Fast resume checklist (for next agent/session)

1. Read:
   - `DraganCorp/docs/operations/00_DraganCorp_Program_Control_Center.md`
   - `DraganCorp/docs/operations/02_Phase_A_Persistent_CODER_VP_Implementation_Plan.md`
   - `DraganCorp/specs/vp-session-registry-v1.md`
2. Inspect recent commits:
   - `git log --oneline -n 8`
3. Re-run durable tests:
   - `uv run python -m pytest tests/durable/test_durable_state.py -q`
4. Continue with WS-C implementation in gateway/runtime orchestration paths.

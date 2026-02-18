# 44. Telegram Functionality Implementation Plan (2026-02-18)

## 1. Purpose

Continue Telegram implementation with a current-state-accurate plan that can be executed immediately.

This plan supersedes stale assumptions in older Telegram notes and aligns work to the current gateway/runtime architecture.

## 2. Current State (Verified in Code)

1. Telegram runtime exists and is active at `src/universal_agent/bot/main.py`.
2. Telegram execution is already gateway-based through `AgentAdapter` (`InProcessGateway` or `ExternalGateway`), not direct legacy `process_turn`.
3. Telegram service currently runs polling mode in `src/universal_agent/bot/main.py` (no webhook handling path in this module).
4. Session handling in `src/universal_agent/bot/agent_adapter.py` is intentionally fresh-session-per-request plus checkpoint reinjection.
5. `/continue` and `/new` commands in `src/universal_agent/bot/plugins/commands.py` now map to explicit behavior:
   - `/continue` prefers `resume_session("tg_<user_id>")` in adapter flow.
   - `/new` keeps fresh-session behavior.
6. Telegram gateway tests are now aligned and integrated into the default pytest suite:
   - `uv run pytest -q tests/bot/test_telegram_gateway.py -q` passes without environment gating.
7. Documentation drift on launch command was corrected:
   - `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/Running_The_Agent.md` now references `uv run python -m universal_agent.bot.main`.

## 3. Problem Statement

Telegram is functional, but semantics and validation are inconsistent:

1. User-facing continuation controls imply persistent session reuse, while runtime uses checkpointed fresh sessions.
2. Telegram-specific tests previously had stale expectations and were skipped by default; this is now corrected in suite-integrated coverage.
3. Operator docs are partially stale, which increases deployment and debugging friction.
4. Reliability controls (per-user task gating, message send retry policy, explicit timeout/status telemetry) are not yet hardened for small multi-user production usage.

## 4. Scope and Non-Goals

### In Scope

1. Align Telegram session semantics and command behavior.
2. Restore Telegram test validity and CI coverage for core flows.
3. Correct Telegram run/deploy docs.
4. Harden bot runtime behavior for low-volume multi-user operation.

### Out of Scope (This Phase)

1. Full role/tenant model for Telegram beyond allowlist.
2. Rich Telegram file upload/download UX redesign.
3. Major gateway protocol changes.

## 5. Workstreams

### TG-A: Session Semantics Alignment (P0) — Implemented

Goal: Make `/continue` and `/new` behavior truthful and deterministic.

Implementation target:

1. Decide and codify one model:
   - Model A: keep fresh+checkpoint always, remove `/continue` semantic.
   - Model B (recommended): default fresh+checkpoint, but `/continue` opts into pinned session reuse via `resume_session`.
2. Update `src/universal_agent/bot/agent_adapter.py` and `src/universal_agent/bot/task_manager.py` to match selected model.
3. Update command text in `src/universal_agent/bot/plugins/commands.py` to match actual behavior.

Acceptance:

1. Manual runs prove `/new` starts isolated state and `/continue` follows declared behavior.
2. No mismatch between runtime behavior and command copy.

### TG-B: Test Rehabilitation (P0) — Implemented

Goal: Telegram tests validate actual behavior and run in a controlled lane.

Implementation target:

1. Rewrite `tests/bot/test_telegram_gateway.py` for current adapter contract.
2. Keep Telegram gateway tests runnable in the default suite (no env gate). Done.
3. Add one behavior test for continuation mode semantics (selected in TG-A).
4. Keep `tests/unit/test_telegram_formatter.py` green.

Acceptance:

1. `uv run pytest -q tests/bot/test_telegram_gateway.py -q` passes.
2. `uv run pytest -q tests/unit/test_telegram_formatter.py -q` passes.

### TG-C: Operational Documentation Corrections (P0) — Implemented

Goal: eliminate launch/runbook drift.

Implementation target:

1. Fix Telegram launch command in `OFFICIAL_PROJECT_DOCUMENTATION/03_Operations/Running_The_Agent.md`.
2. Add explicit Telegram runtime mode note (polling currently).
3. Add troubleshooting snippet for timeout and allowed-user configuration:
   - `UA_TELEGRAM_TASK_TIMEOUT_SECONDS`
   - `TELEGRAM_ALLOWED_USER_IDS`
   - `UA_GATEWAY_URL` vs in-process mode.

Acceptance:

1. A new operator can launch Telegram service without code spelunking.

### TG-D: Reliability Hardening (P1) — Implemented (Phase 1)

Goal: improve production stability without architecture churn.

Implementation target:

1. Added per-user active-task guard in `src/universal_agent/bot/task_manager.py` (prevents user flooding their own queue).
2. Added bounded send retry for Telegram outbound messages in `src/universal_agent/bot/main.py` callbacks.
3. Improved timeout surface text in `src/universal_agent/bot/agent_adapter.py` with task/workspace hints.
4. Normalized task status/completion callback sends through safe plain-text delivery path.

Acceptance:

1. Duplicate `/agent` spam from one user is controlled.
2. Transient Telegram send failures do not immediately drop final status delivery.

## 6. Execution Order

1. TG-A session semantics.
2. TG-B tests.
3. TG-C docs correction.
4. TG-D reliability hardening.

## 7. Verification Matrix

### Unit/Behavior

1. `uv run pytest -q tests/unit/test_telegram_formatter.py -q`
2. `uv run pytest -q tests/bot/test_telegram_gateway.py -q`

### Integration Smoke

1. Start bot:
   - `uv run python -m universal_agent.bot.main`
2. Exercise commands in Telegram:
   - `/status`
   - `/agent <short prompt>`
   - `/continue`
   - `/agent <follow-up>`
   - `/new`
   - `/agent <fresh prompt>`
3. Confirm:
   - responses delivered
   - command semantics match docs
   - no unhandled exceptions in bot logs.

## 8. Risks and Mitigations

1. Risk: semantic changes break existing user habits.
   - Mitigation: ship clear `/status` mode indicator and explicit mode-change confirmations.
2. Risk: test updates lag behind semantics.
   - Mitigation: land TG-A and TG-B in one packet.
3. Risk: markdown formatting failures cause dropped messages.
   - Mitigation: route all generated response text through formatter-safe path.

## 9. Definition of Done

1. Telegram session semantics are explicit and implemented as documented.
2. Telegram gateway tests pass in the default test suite.
3. Telegram run docs match actual runtime entrypoint and behavior.
4. Reliability guardrails are in place for low-volume multi-user operation.

## 10. Immediate Next Step

Completed hardening packet:

1. Added cancellation command flow (`/cancel <task_id>` with latest-active fallback).
2. Added telemetry logs for active-task rejections and send-retry exhaustions.
3. Addressed checkpoint timestamp deprecation (`datetime.utcnow()` -> timezone-aware UTC).

Current verification status:

1. `./run_verification.sh` passes with Telegram coverage included.

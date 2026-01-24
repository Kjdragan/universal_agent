# Universal Agent Gateway Refactor - Execution Plan

**Owner:** Cascade
**Created:** 2026-01-24
**Status:** Active

## Purpose
Deliver a gateway-first architecture that unifies CLI/Web/UI entrypoints while preserving current CLI behavior and all Claude Agent SDK guardrails.

## Guiding Principles
- Preserve CLI behavior at every stage (default paths remain in-process).
- No semantic regressions in guardrails/hooks/injections.
- Prefer additive changes behind flags until validated.
- Document each milestone and decision in `ua_gateway_refactor_progress.md`.

## Stage 1 — Dependency Hardening (Completed)
Goal: isolate CLI I/O, workspace binding, trace persistence; prepare for gateway without routing changes.

### Completed
- Extracted CLI I/O helpers (run.log, prompt input, job completion summary) into `cli_io.py`.
- Centralized workspace binding in `execution_context` (env + observer).
- Added `trace_utils.write_trace` for trace persistence.
- Added `ExecutionSession` and plumbed into CLI path.
- Wired bot adapter to pass `ExecutionSession` into `process_turn`.
- Added Gateway contract + in-process facade (AgentBridge-backed).

### Remaining (Stage 1)
- None. Stage 1 exit criteria satisfied via Stage 2 parity checks.

### Exit Criteria
- CLI behavior unchanged in default path.
- Guardrails checklist unchanged or explicitly revalidated.
- Progress log updated with concrete call site validation.

### Gateway Preview Flags (CLI)
- `--use-gateway` / `UA_USE_GATEWAY=1`: route interactive CLI turns through `InProcessGateway`.
- `--gateway-use-cli-workspace` / `UA_GATEWAY_USE_CLI_WORKSPACE=1`: reuse CLI workspace for gateway sessions.
- Auto-disabled for resume/fork/harness/URW modes to avoid behavior regressions.

## Stage 2 — Event Stream Normalization
Goal: normalize agent output to structured events consumed by CLI/Web without behavior changes.

Tasks
- Define a CLI event renderer that mirrors current output formatting.
- Build adapter to map `AgentEvent` → CLI output with no semantic differences.
- Add feature flag to route CLI rendering from event stream (off by default).
 - Expand parity coverage for tool-heavy flows (search, Write/Read, Bash).

Exit Criteria
- CLI output diff matches current behavior for representative runs.
- Web/API event streams remain unchanged.

## Stage 3 — Gateway API (In-Process)
Goal: make CLI/Web call the Gateway interface in-process.

Tasks
- Add CLI option/flag to use in-process Gateway by default in dev mode.
- Ensure session lifecycle + workspace handling match current CLI logic.
- Maintain `process_turn` for URW harness during transition.

Exit Criteria
- CLI can run end-to-end via Gateway with no regressions.
- URW harness continues to operate via `process_turn`.

## Stage 4 — Gateway Externalization
Goal: optional HTTP/WebSocket gateway that mirrors in-process behavior.

Tasks
- Expose Gateway via HTTP/WebSocket endpoints.
- Preserve in-process path as CLI default.
- Add compatibility layer for Web UI session management.

Exit Criteria
- External gateway path matches in-process behavior.
- CLI default remains in-process.

## Stage 5 — URW as Meta-Client
Goal: URW calls Gateway API instead of `process_turn` directly.

Tasks
- Update URW adapter to call Gateway, subscribe to events.
- Preserve per-phase workspace binding semantics.
- Decide whether URW needs dedicated phase events.

Exit Criteria
- URW workflows continue uninterrupted through Gateway.

## Stage 6 — Worker Pool + Lease Durability
Goal: distribute runs across workers using durable leases.

Tasks
- Introduce lease acquisition/heartbeat in Gateway workers.
- Add worker pool config and scheduling.

Exit Criteria
- Durable runs can be resumed across workers.

## Validation & Tests
- CLI smoke test for interactive, job, resume, and harness paths.
- Verify trace.json and run.log behavior unchanged.
- Verify guardrails checklist in `ua_gateway_guardrails_checklist.md`.

## Documentation Cadence
- Update `ua_gateway_refactor_progress.md` after each milestone.
- Add decisions to Decisions Log with date + rationale.

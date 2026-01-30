# CLI-Centric Execution Engine Refactor — Living Docs Index

**Scope:** Make `src/universal_agent/main.py:process_turn()` the canonical execution engine and route all clients through the gateway.

**Update rule:** These docs are *living*. When we change direction, add/modify files, or learn something from testing, update the relevant pages here.

## Document Map

| # | Document | Purpose |
|---|----------|---------|
| 00 | [00_INDEX.md](00_INDEX.md) | This index |
| 01 | [01_PROGRESS.md](01_PROGRESS.md) | Chronological progress log + current status |
| 02 | [02_DECISIONS.md](02_DECISIONS.md) | ADR-style decisions and rationale |
| 03 | [03_TEST_PLAYBOOK.md](03_TEST_PLAYBOOK.md) | Repeatable test matrix + commands (uv) |
| 04 | [04_OPERATIONS.md](04_OPERATIONS.md) | How to operate/run/debug each mode |
| 05 | [05_LESSONS_LEARNED.md](05_LESSONS_LEARNED.md) | Gotchas, sharp edges, future cleanup |

## Current State (as of 2026-01-26)

- **Unified in-process gateway** uses `ProcessTurnAdapter` (not legacy `AgentBridge`) by default.
- **Event callback path** added to `process_turn` to support event streaming.
- **Workspace guard** helper exists (not yet wired as a global tool hook).
- **Repeatable tests** exist under `scripts/test_gateway_refactor.py`.

## Key Code Touchpoints

- `src/universal_agent/main.py`
- `src/universal_agent/gateway.py`
- `src/universal_agent/execution_engine.py`
- `src/universal_agent/guardrails/workspace_guard.py`
- `scripts/test_gateway_refactor.py`

## Related Project Docs

- `Project_Documentation/016_Execution_Engine_Gateway_Model.md`
- `Project_Documentation/017_Execution_Engine_Gateway_Development_Plan.md`
- `Project_Documentation/018_Execution_Engine_Gateway_Implementation_Plan.md`

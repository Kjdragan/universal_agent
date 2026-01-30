# Test Playbook (Repeatable)

## Principles

- Always run tests via `uv run`.
- Store results as artifacts (JSON/logs) so we can diff between refactor iterations.

## Quick Commands

- **Imports only:**
  - `uv run python scripts/test_gateway_refactor.py --test imports`

- **Unit tests (guardrails, gateway wiring):**
  - `uv run python scripts/test_gateway_refactor.py --test unit`

- **Gateway integration (non-live):**
  - `uv run python scripts/test_gateway_refactor.py --test gateway`

- **All non-live tests:**
  - `uv run python scripts/test_gateway_refactor.py --test all`

## Live Tests (requires API keys)

- **Direct CLI execution:**
  - `uv run python scripts/test_gateway_refactor.py --test live-cli`

- **In-process gateway execution:**
  - `uv run python scripts/test_gateway_refactor.py --test live-gateway`

## Where results go

- `scripts/test_results.json`

## Test Matrix

| Mode | Entry point | Expected engine | Expected events |
|------|-------------|-----------------|----------------|
| CLI direct | `process_turn()` | `process_turn` | Complex/tool loop should emit events; SIMPLE fast path may not |
| Gateway in-process | `InProcessGateway.execute()` | `process_turn` | `SESSION_INFO`, `STATUS`, `TEXT`, `ITERATION_END` at minimum |
| Web UI API | `universal_agent.api.server` | TBD (should also use gateway unified path) | WebSocket stream parity |

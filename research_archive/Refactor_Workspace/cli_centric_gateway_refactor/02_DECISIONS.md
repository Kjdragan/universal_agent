# Decisions (ADR-style)

## ADR-001: Canonical execution engine is `process_turn`

- **Decision:** The CLI path in `src/universal_agent/main.py:process_turn()` is the single source of truth for agent execution.
- **Why:** The Web UI path (`UniversalAgent.run_query`) diverged (timeouts/output location mismatch). CLI path is stable and already used by the harness.
- **Status:** Implemented (gateway now routes to `process_turn`).

## ADR-002: Gateway defaults to unified engine but supports legacy fallback

- **Decision:** `InProcessGateway` uses `ProcessTurnAdapter` by default and supports `use_legacy_bridge=True`.
- **Why:** Allows incremental rollout and easy fallback while we stabilize event parity and path enforcement.
- **Status:** Implemented.

## ADR-003: Use `uv` for dependency management and execution

- **Decision:** All docs and scripts use `uv` commands.
- **How:**
  - Add deps: `uv add <package>`
  - Run: `uv run <command>`
- **Status:** Implemented in test playbook + scripts.

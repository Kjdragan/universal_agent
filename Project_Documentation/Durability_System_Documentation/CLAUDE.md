# CLAUDE.md - Durability System Documentation

This directory contains **code-verified** documentation for the Universal Agent's durability system. All documentation here is derived from actual code paths in `src/universal_agent/` and `src/universal_agent/durable/`.

## What the Durability System Does

The durability system provides **crash recovery and idempotency** for long-running agent workflows:

- **Prevents duplicate side effects** (email, upload, file write) across crashes
- **Replays in-flight tool calls** deterministically on resume
- **Persists run state** in a local runtime database
- **Produces evidence summaries** strictly from ledger receipts (not model text)

## Core Invariants

1. **No duplicate side effects** once a receipt exists (idempotency via ledger)
2. **In-flight tool calls are replayed** before new work continues
3. **Task relaunch never uses TaskOutput/TaskResult** from the SDK
4. **Resume uses phase checkpoints** as anchors for recovery

## Key Components

### Runtime Database
- **Location**: `AGENT_RUN_WORKSPACES/runtime_state.db`
- **Tables**: `runs`, `run_steps`, `tool_calls`, `checkpoints`
- **Code**: [state.py](../../src/universal_agent/durable/state.py)

### Tool Ledger + Receipts
- **Ledger implementation**: [ledger.py](../../src/universal_agent/durable/ledger.py)
- **Idempotency keys**: Generated from tool parameters to prevent duplicate side effects
- **Receipt creation**: `on_pre_tool_use_ledger`, `on_post_tool_use_ledger` in [main.py](../../src/universal_agent/main.py)

### Checkpointing
- **Checkpoint creation**: [checkpointing.py](../../src/universal_agent/durable/checkpointing.py)
- **Phase anchors**: `pre_read_only`, `pre_side_effect`, `post_replay`

### Replay Orchestration
- **Replay queue**: `reconcile_inflight_tools` in [main.py](../../src/universal_agent/main.py)
- **Replay policies**: [classification.py](../../src/universal_agent/durable/classification.py)
  - `REPLAY_EXACT`: Re-run with same input
  - `RELAUNCH`: Re-create Task call or reuse output if artifacts exist

### Guardrails
- **Tool-name sanitization**: [tool_gateway.py](../../src/universal_agent/durable/tool_gateway.py)
- **Tool schema validation**: [tool_schema.py](../../src/universal_agent/guardrails/tool_schema.py)
- **Durable job mode**: Blocks file edit tools and COMPOSIO_REMOTE_WORKBENCH during replay

## Documentation Files

| File | Purpose |
|------|---------|
| [000_DURABILITY_SYSTEM_OVERVIEW.md](000_DURABILITY_SYSTEM_OVERVIEW.md) | High-level overview and invariants |
| [001_DURABILITY_ARCHITECTURE.md](001_DURABILITY_ARCHITECTURE.md) | Data model, execution flow, replay flow |
| [002_DURABILITY_RUNTIME_AND_REPLAY.md](002_DURABILITY_RUNTIME_AND_REPLAY.md) | Crash hooks, replay policies, checkpoints |
| [003_DURABILITY_TESTING_RUNBOOK.md](003_DURABILITY_TESTING_RUNBOOK.md) | Test matrix and commands for durability testing |
| [004_DURABILITY_TROUBLESHOOTING.md](004_DURABILITY_TROUBLESHOOTING.md) | Common issues and fixes |
| [005_IDENTITY_AND_EMAIL_GUARDRAILS.md](005_IDENTITY_AND_EMAIL_GUARDRAILS.md) | Email alias resolution |
| [006_DURABILITY_TEST_MASTER.md](006_DURABILITY_TEST_MASTER.md) | Comprehensive testing guide |
| [007_DURABILITY_TESTING_RUNBOOK_COMMANDS.md](007_DURABILITY_TESTING_RUNBOOK_COMMANDS.md) | Full command reference |
| [008_SYSTEM_COMPONENT_MAP_DURABILITY.md](008_SYSTEM_COMPONENT_MAP_DURABILITY.md) | Component map with code locations |
| [009_SYSTEM_CALL_GRAPH_DURABILITY.md](009_SYSTEM_CALL_GRAPH_DURABILITY.md) | Call graph visualization |

## Crash Testing Environment Variables

For synthetic crash testing:

| Variable | Purpose |
|----------|---------|
| `UA_TEST_CRASH_AFTER_TOOL` | Crash after specified tool executes |
| `UA_TEST_CRASH_AFTER_TOOL_CALL_ID` | Crash after specific tool call ID |
| `UA_TEST_CRASH_STAGE` | Crash at specific stage (e.g., `after_tool_success_before_ledger_commit`) |
| `UA_TEST_CRASH_MATCH` | Match mode: `raw`, `slug`, or `any` |

## Resume Commands

```bash
# Resume a crashed run
PYTHONPATH=src uv run python -m universal_agent.main --resume --run-id <RUN_ID>

# Resume with timeout (recommended for tests with Task + PDF + upload)
timeout 400s uv run python -m universal_agent.main --resume --run-id <RUN_ID>
```

## Evidence Sources

When investigating durability issues:

- **Session artifacts**: `AGENT_RUN_WORKSPACES/session_<ts>/run.log`, `trace.json`, `transcript.md`
- **Runtime DB**: `AGENT_RUN_WORKSPACES/runtime_state.db`

## Important Notes

- All documentation in this directory is **code-verified** - it reflects actual code behavior
- When modifying durability code, update these docs to keep them in sync
- Evidence summaries are derived **only from receipts**, never from model text
- Deterministic Task relaunch uses artifact existence, not model judgment

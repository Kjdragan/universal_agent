# Durability System Overview

This directory contains the current durability system documentation based on the
**current code paths** in `src/universal_agent/` and `src/universal_agent/durable/`.

## What the Durability System Does
- Prevents duplicate side effects (email, upload, file write) across crashes.
- Replays in-flight tool calls deterministically on resume.
- Persists run state in a local runtime DB.
- Produces evidence summaries strictly from ledger receipts.

## Core Invariants
- No duplicate side effects once a receipt exists (idempotency via ledger).
- In-flight tool calls are replayed before new work continues.
- Task relaunch never uses TaskOutput/TaskResult.
- Resume uses phase checkpoints as anchors.

## Where This Lives (Code)
- Runtime DB schema + helpers: `src/universal_agent/durable/state.py`
- Tool ledger + receipts: `src/universal_agent/durable/ledger.py`
- Checkpoints + replay: `src/universal_agent/durable/checkpointing.py`
- Replay orchestration: `src/universal_agent/main.py` (`reconcile_inflight_tools`)
- Tool validation guardrails: `src/universal_agent/guardrails/tool_schema.py`
- Tool name sanitation: `src/universal_agent/durable/tool_gateway.py`

## Evidence Sources
- Session artifacts: `AGENT_RUN_WORKSPACES/session_<ts>/run.log`, `trace.json`, `transcript.md`
- Runtime DB: `AGENT_RUN_WORKSPACES/runtime_state.db`

## Read Next
- `001_DURABILITY_ARCHITECTURE.md`
- `002_DURABILITY_RUNTIME_AND_REPLAY.md`
- `003_DURABILITY_TESTING_RUNBOOK.md`
- `004_DURABILITY_TROUBLESHOOTING.md`

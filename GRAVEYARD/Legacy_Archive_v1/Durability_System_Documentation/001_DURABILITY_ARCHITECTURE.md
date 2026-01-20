# Durability Architecture (Code-Verified)

This architecture summary is derived from the current code paths:
- `src/universal_agent/main.py`
- `src/universal_agent/durable/`
- `src/universal_agent/guardrails/tool_schema.py`

## Data Model (Runtime DB)
DB file: `AGENT_RUN_WORKSPACES/runtime_state.db`

Tables used by durability:
- `runs`: run status, current step, resume metadata.
- `run_steps`: step index + phase.
- `tool_calls`: tool ledger and receipts.
- `checkpoints`: phase checkpoints.

Code:
- `src/universal_agent/durable/state.py`
- `src/universal_agent/durable/checkpointing.py`
- `src/universal_agent/durable/ledger.py`

## Execution Flow
1) **Prepare tool call** (ledger entry + idempotency key).
2) **Mark running**.
3) **Store receipt** on success.
4) **Update phase checkpoints** before side effects.

Code:
- `src/universal_agent/main.py` → `on_pre_tool_use_ledger`, `on_post_tool_use_ledger`

## Replay Flow (Resume)
- Resume constructs a replay queue of in-flight tools.
- Replay policy is determined per tool call.
- Replay runs before normal continuation.

Code:
- `src/universal_agent/main.py` → `reconcile_inflight_tools`, `_build_forced_tool_prompt`
- `src/universal_agent/durable/classification.py` → replay policy classification

## Task Relaunch Rules
Task replay uses deterministic paths:
- If sub-agent output exists, reuse it.
- If output files referenced in the Task prompt exist, mark Task succeeded without re-run.

Code:
- `src/universal_agent/main.py` → `_subagent_output_available`, `_extract_task_output_paths`

## Guardrails
- **Tool-name sanitization**: deny malformed tool names.
- **Schema guardrails**: validate tool input (skipped in forced replay).
- **Durable job mode**: blocks file edit tools and COMPOSIO_REMOTE_WORKBENCH.

Code:
- `src/universal_agent/durable/tool_gateway.py` (`is_malformed_tool_name`, `is_invalid_tool_name`)
- `src/universal_agent/guardrails/tool_schema.py`
- `src/universal_agent/main.py` (`on_pre_tool_use_ledger`)

## Evidence Summary
Evidence is derived only from receipts in the ledger, never from model text.

Code:
- `src/universal_agent/main.py` → job completion summary and evidence blocks


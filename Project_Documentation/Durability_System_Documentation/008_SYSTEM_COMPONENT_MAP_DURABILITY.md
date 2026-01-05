# Durability System Component Map (Code-Verified)

This map focuses only on components directly involved in durability.

## Runtime State
- DB: `AGENT_RUN_WORKSPACES/runtime_state.db`
- Tables: `runs`, `run_steps`, `tool_calls`, `checkpoints`
- Code: `src/universal_agent/durable/state.py`

## Tool Ledger + Receipts
- Ledger implementation: `src/universal_agent/durable/ledger.py`
- Receipt creation and idempotency: `on_pre_tool_use_ledger`, `on_post_tool_use_ledger`
  in `src/universal_agent/main.py`

## Checkpointing
- Checkpoint creation: `src/universal_agent/durable/checkpointing.py`
- Phase anchors: `pre_read_only`, `pre_side_effect`, `post_replay`

## Replay Orchestration
- Replay queue + forced replay: `reconcile_inflight_tools` in `src/universal_agent/main.py`
- Replay policies: `src/universal_agent/durable/classification.py`

## Guardrails
- Tool-name sanitization: `src/universal_agent/durable/tool_gateway.py`
- Tool schema validation: `src/universal_agent/guardrails/tool_schema.py`
- Durable job mode blocking: `src/universal_agent/main.py` pre-tool hook

## Session Artifacts
- Run logs and transcripts:
  - `AGENT_RUN_WORKSPACES/session_<ts>/run.log`
  - `AGENT_RUN_WORKSPACES/session_<ts>/transcript.md`
  - `AGENT_RUN_WORKSPACES/session_<ts>/trace.json`


# 013: Phase 4 Ticket 4 — Side-Effect Receipt Summary

Date: 2026-01-02  
Scope: Run receipt summary view + export (md/json)

## Summary
Added an operator CLI subcommand to export side-effect receipts for a run. The command aggregates non-read-only tool calls from the runtime DB and surfaces external identifiers from stored tool responses when available.

## What Changed
### Operator CLI
- New subcommand: `runs receipts`
- Formats: Markdown table or JSON

### DB access helpers
- `src/universal_agent/operator/operator_db.py`
  - `list_receipt_tool_calls(run_id)` returns non-read-only tool calls with response refs.

### External ID extraction (best-effort)
- `src/universal_agent/operator/operator_cli.py`
  - Parses `response_ref` JSON and extracts common identifiers:
    - `id`, `message_id`, `threadId`, `thread_id`, `s3key`, `request_id`, `log_id`
  - Includes `external_correlation_id` if present.

## Usage
```
PYTHONPATH=src uv run python -m universal_agent.operator runs receipts --run-id <RUN_ID> --format md
PYTHONPATH=src uv run python -m universal_agent.operator runs receipts --run-id <RUN_ID> --format json
```

## Example (Markdown)
```
# Run receipts (<RUN_ID>)

| created_at | tool | status | replay_policy | idempotency_key | external_ids |
|---|---|---|---|---|---|
| 2026-01-02T20:49:02.561Z | mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL | succeeded | REPLAY_EXACT | ... | id=..., threadId=... |
```

## Notes
- Receipts are derived from `response_ref` stored in the ledger, not a dedicated summary field.
- Older runs may include tools misclassified before policy updates (e.g., `COMPOSIO_SEARCH_TOOLS`).

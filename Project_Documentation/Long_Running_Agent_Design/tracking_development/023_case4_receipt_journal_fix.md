# Case 4 Fix — Receipt Journal + Provider Idempotency

Date: 2026-01-02

## Summary
Added a pending-receipt journal so tool successes are durably recorded before ledger commit, and promoted on resume to avoid duplicate side effects after post-success crashes. Added best-effort provider idempotency injection for Composio tools.

## Why
Case 4 exposed a crash window after a tool succeeded but before the ledger was marked succeeded, which could cause duplicate external actions on resume. This fix closes that window by persisting a receipt immediately and using it to short-circuit replay.

## Changes
- Added `tool_receipts` table to persist pending receipts.
- Added ledger APIs to record, promote, and clear pending receipts.
- On resume, pending receipts are promoted to `succeeded` and replay is skipped.
- Added best-effort `client_request_id` injection for Composio tools.
- Normalized tool input ignores injected idempotency fields to keep replay matching stable.

## Files
- `src/universal_agent/durable/migrations.py`
- `src/universal_agent/durable/ledger.py`
- `src/universal_agent/main.py`
- `tests/test_durable_ledger.py`
- `tests/test_forced_tool_matches.py`

## Tests Run
```
UV_CACHE_DIR=/home/kjdragan/lrepos/universal_agent/.uv_cache uv run pytest tests/test_durable_ledger.py tests/test_forced_tool_matches.py
```

## Notes
- Provider idempotency is best-effort (adds `client_request_id` where possible).
- For strict proof, re-run Case 4 with crash hook after deploying this change.

# 014: Phase 4 Ticket 3 — Tool Policy Audit + Unknown Tool Detection

Date: 2026-01-02  
Scope: Detect unknown tools, persist policy match metadata, and provide audit output.

## Summary
Implemented policy auditing across tool calls by recording policy matches in the ledger, emitting warnings for unknown Composio tools, and adding a CLI audit view that summarizes coverage and input variance.

## What Changed
### Runtime DB (tool_calls)
- Added columns:
  - `policy_matched` (INTEGER)
  - `policy_rule_id` (TEXT)

### Ledger
- `policy_matched` and `policy_rule_id` are set per tool call.
- Unknown tool detection for Composio:
  - Logs warning with code `UA_POLICY_UNKNOWN_TOOL`
  - Writes to `AGENT_RUN_WORKSPACES/policy_audit/unknown_tools.jsonl` (best effort).

### Operator CLI
- New command: `policy audit`
  - Counts by `side_effect_class`
  - Tools without explicit policy matches
  - Tools with input variance (distinct normalized input hashes > 1)

## Usage
```
export UV_CACHE_DIR=/home/kjdragan/lrepos/universal_agent/.uv-cache
PYTHONPATH=src uv run python -m universal_agent.operator policy audit --format md --limit 50
PYTHONPATH=src uv run python -m universal_agent.operator policy audit --format json --limit 50
```

## Example Output (Markdown)
```
# Tool policy audit

## Counts by side_effect_class
| side_effect_class | count |
|---|---|
| external | 5 |
| local | 3 |

## Tools without explicit policy matches
| tool_namespace | tool_name | raw_tool_name | count |
|---|---|---|---|
| claude_code | bash | Bash | 3 |
| mcp | COMPOSIO_MULTI_EXECUTE_TOOL | mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL | 1 |

## Tools with input variance (distinct normalized inputs > 1)
| tool_namespace | tool_name | distinct_inputs | distinct_runs |
|---|---|---|---|
| claude_code | bash | 3 | 1 |
```

## Notes
- Unknown-tool warnings are limited to Composio tools; other namespaces surface in audit as “no explicit policy match” until policies are added.
- This audit reflects current runtime DB state; older runs may reflect earlier policy mappings.

# Ticket 4 — Tool Policy Hardening + Explain Mode (Next Steps)

Date: 2026-01-02

## Summary
Hardened tool policy loading with schema validation and overlay support, added an explain CLI mode for resolved policy details, and expanded classification tests for regex policies and invalid YAML.

## Why
Tool policy classification must be maintainable and safe. Invalid policy files should fail fast, overrides should be explicit, and maintainers need a quick way to inspect resolved policy behavior.

## Changes
- Validated tool policy schema at load time; invalid YAML or invalid fields raise clear errors.
- Added support for `tool_name_regex` and `namespace` aliases in policy entries.
- Added optional overlay policy paths (`UA_TOOL_POLICIES_OVERLAY_PATH` / `UA_TOOL_POLICIES_OVERLAY_PATHS`) with overlay precedence.
- Added CLI flag `--explain-tool-policy` to print the resolved policy for a raw tool name.
- Added test coverage for regex policies, overlay precedence, and invalid schema fail-fast behavior.

## Files
- `src/universal_agent/durable/classification.py`
- `src/universal_agent/main.py`
- `tests/test_durable_classification.py`

## Repro Command
```
PYTHONPATH=src uv run python -m universal_agent.main --explain-tool-policy GMAIL_SEND_EMAIL
```

## Pass/Fail Signal
- **Pass**: Explain mode prints resolved namespace, matched policy (if any), side_effect_class, and replay_policy. Invalid YAML fails fast with a clear error.
- **Fail**: Invalid policy YAML is silently ignored or explain mode yields incorrect classification.

## Regression Check
```
PYTHONPATH=src uv run python -m universal_agent.main --job tmp/quick_resume_job.json
```
Kill during sleep, then resume; expect clean completion.

## Tests Run
```
UV_CACHE_DIR=/home/kjdragan/lrepos/universal_agent/.uv_cache uv run pytest tests/test_durable_classification.py
```

## Notes
- Overlay policies take precedence over base policies by loading first.
- Schema validation occurs at CLI startup via `validate_tool_policies()`.

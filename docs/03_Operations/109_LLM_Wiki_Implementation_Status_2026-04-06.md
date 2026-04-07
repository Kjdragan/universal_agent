# LLM Wiki Implementation Status (2026-04-06)

## Current Phase

Phase 1: core scaffolding, runtime surfaces, initial projection/query/lint plumbing, and targeted verification complete.

## Completed Surfaces

- Created the shared wiki engine under `src/universal_agent/wiki/`
- Added internal wiki MCP tools:
  - `wiki_init_vault`
  - `wiki_ingest_external_source`
  - `wiki_sync_internal_memory`
  - `wiki_query`
  - `wiki_lint`
- Registered the tools in the internal MCP registry
- Added `.claude` runtime assets:
  - `llm-wiki-orchestration`
  - `wiki-maintainer`
- Wired prompt-trigger and capability-discovery surfaces for the wiki workflow
- Added documentation drift mappings for the wiki subsystem
- Added initial subsystem documentation
- Added targeted unit tests for assets, registry wiring, engine behavior, and docs indexing
- Verified wiki-specific tests and local compile checks

## Pending Surfaces

- broader runtime validation of external ingest and internal sync behavior
- integration of wiki query results into higher-level consumers beyond the dedicated wiki surface

## Last Verified Tests

- `uv run pytest -q tests/unit/test_llm_wiki_assets.py tests/unit/test_internal_registry_wiki_tools.py tests/unit/test_llm_wiki_engine.py tests/unit/test_llm_wiki_docs.py tests/unit/test_prompt_assets_capabilities.py`
- `uv run python -m compileall src/universal_agent/wiki src/universal_agent/tools/wiki_bridge.py src/universal_agent/memory/memory_store.py src/universal_agent/memory/orchestrator.py src/universal_agent/session_checkpoint.py`

## Current Blockers

- Need broader runtime validation of auto-sync behavior against real session/gateway execution
- Need to decide when or whether the dedicated wiki query surface should partially feed higher-level recall paths
- A wider nearby regression pass surfaced unrelated existing failures in:
  - `tests/unit/test_feature_flags_defaults.py`
  - `tests/unit/test_notebooklm_assets.py`

## Next Milestone

Complete the next integration pass in:

1. wiki engine behavior
2. internal tool registration
3. runtime auto-sync behavior under real execution paths
4. broader regression coverage around adjacent subsystems

## Files Added Or Changed So Far

### New

- `src/universal_agent/wiki/__init__.py`
- `src/universal_agent/wiki/core.py`
- `src/universal_agent/wiki/projection.py`
- `src/universal_agent/tools/wiki_bridge.py`
- `.claude/skills/llm-wiki-orchestration/SKILL.md`
- `.claude/agents/wiki-maintainer.md`
- `.claude/knowledge/llm_wiki_runtime.md`
- `docs/02_Subsystems/LLM_Wiki_System.md`

### Updated

- `src/universal_agent/tools/internal_registry.py`
- `src/universal_agent/durable/classification.py`
- `src/universal_agent/durable/tool_policies.yaml`
- `src/universal_agent/feature_flags.py`
- `src/universal_agent/memory/memory_store.py`
- `src/universal_agent/memory/orchestrator.py`
- `src/universal_agent/session_checkpoint.py`
- `src/universal_agent/main.py`
- `src/universal_agent/hooks.py`
- `src/universal_agent/prompt_assets.py`
- `src/universal_agent/agent_setup.py`
- `src/universal_agent/scripts/doc_drift_auditor.py`

## Notes

- The internal memory vault remains a derived projection. Canonical runtime state, memory indexes, and checkpoint files are intentionally unchanged in role.
- The external vault remains separate from official project documentation.

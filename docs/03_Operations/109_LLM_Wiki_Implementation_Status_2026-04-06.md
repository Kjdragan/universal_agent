# LLM Wiki Implementation Status (2026-04-06)

**Last updated:** 2026-04-07

## Current Phase

Phase 1 complete: core scaffolding, runtime surfaces, initial projection/query/lint plumbing, and targeted verification are in place.

Phase 2 pending: runtime smoke validation and hardening based on real usage.

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
- Ran the first manual smoke-test pass and recorded results in `110_LLM_Wiki_Smoke_Test_2026-04-07.md`

## Pending Surfaces

- broader runtime validation of external ingest and internal sync behavior
- integration of wiki query results into higher-level consumers beyond the dedicated wiki surface
- hardening of internal sync performance and completion behavior
- improvement of entity/concept extraction and wiki self-linking quality

## Last Verified Tests

- `uv run pytest -q tests/unit/test_llm_wiki_assets.py tests/unit/test_internal_registry_wiki_tools.py tests/unit/test_llm_wiki_engine.py tests/unit/test_llm_wiki_docs.py tests/unit/test_prompt_assets_capabilities.py`
- `uv run python -m compileall src/universal_agent/wiki src/universal_agent/tools/wiki_bridge.py src/universal_agent/memory/memory_store.py src/universal_agent/memory/orchestrator.py src/universal_agent/session_checkpoint.py`

## Current Blockers

- Internal sync against real project data timed out in the first smoke test and did not reliably materialize compiled ledger pages
- External auto-generated entity/concept pages are too noisy and produce self-inflicted lint failures
- Need to decide when or whether the dedicated wiki query surface should partially feed higher-level recall paths
- A wider nearby regression pass surfaced unrelated existing failures in:
  - `tests/unit/test_feature_flags_defaults.py`
  - `tests/unit/test_notebooklm_assets.py`

## Next Milestone

Complete the next integration pass in:

1. wiki engine behavior
2. internal tool registration
3. runtime auto-sync behavior under real execution paths
4. entity/concept generation quality and reciprocal linking
5. broader regression coverage around adjacent subsystems

## Recommended Next Action

Use the feature first in a controlled smoke-test pass before extending the build further.

Recommended order:

1. Initialize an external vault and ingest one small local source
2. Query that vault and confirm the answer cites the created pages
3. Run wiki lint and confirm index/log/overview behavior
4. Trigger or manually run internal memory sync and inspect `memory/wiki/`
5. Only after that, continue the build-out to the next hardening phase

Reason:

- the remaining risk is not missing core scaffolding
- the remaining risk is real runtime behavior under actual usage
- a smoke test will tell us where the next implementation work should focus

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
- `docs/03_Operations/110_LLM_Wiki_Smoke_Test_2026-04-07.md`

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
- The best immediate path is to exercise the feature now, then harden based on what breaks or feels weak in actual use.
- That smoke-test pass is now complete, and the next build step should focus on internal sync performance/completion and external page-generation quality.

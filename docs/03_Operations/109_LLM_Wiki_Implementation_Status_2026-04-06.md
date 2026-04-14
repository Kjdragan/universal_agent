# LLM Wiki Implementation Status (2026-04-06)

**Last updated:** 2026-04-14

## Current Phase

Phase 1 complete: core scaffolding, runtime surfaces, initial projection/query/lint plumbing, and targeted verification are in place.

Phase 2 in progress: runtime smoke validation has been completed, and hardening is underway based on real usage.

Phase 3 in progress: nightly proactive integration is live — the wiki system is now wired into the autonomous pipeline via `nightly_wiki_agent.py` (cron at 03:15 CST), which selects proactive signal cards, dispatches VP missions to build NLM-backed knowledge bases, and surfaces results in the morning briefing.

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
- Hardened internal sync to complete in bounded time against real project data
- Reduced external lint noise by fixing reciprocal path-link handling and singularization issues
- Added backlinks for saved analyses and source-page candidate filtering hardening
- Added timing telemetry to internal sync results
- Added incremental sync metadata with copied/skipped evidence tracking
- Added human-readable `sync_progress.md` output for internal sync
- Reduced internal sync overhead by excluding `evidence/` pages from managed-page scans

## Pending Surfaces

- broader runtime validation of external ingest and internal sync behavior
- integration of wiki query results into higher-level consumers beyond the dedicated wiki surface
- improvement of entity/concept extraction quality
- dedicated integration coverage for the real shared-memory path

## Last Verified Tests

- `uv run pytest -q tests/unit/test_llm_wiki_assets.py tests/unit/test_internal_registry_wiki_tools.py tests/unit/test_llm_wiki_engine.py tests/unit/test_llm_wiki_docs.py tests/unit/test_prompt_assets_capabilities.py`
- `uv run python -m compileall src/universal_agent/wiki src/universal_agent/tools/wiki_bridge.py src/universal_agent/memory/memory_store.py src/universal_agent/memory/orchestrator.py src/universal_agent/session_checkpoint.py`
- `uv run pytest -q tests/unit/test_llm_wiki_engine.py tests/unit/test_llm_wiki_assets.py tests/unit/test_internal_registry_wiki_tools.py tests/unit/test_llm_wiki_docs.py`
- smoke reruns:
  - external vault lint findings reduced from `9` -> `1` -> `0`
  - internal sync completed inside the 20s timeout window with telemetry
  - warm internal sync rerun skipped all previously copied evidence
  - later warm internal sync rerun dropped to `2398ms` total with `sync_progress.md` present

## Current Blockers

- Internal sync now has timing telemetry, incremental metadata, and readable progress output
- External auto-generated concept pages are still semantically weak
- Need to decide when or whether the dedicated wiki query surface should partially feed higher-level recall paths
- A wider nearby regression pass surfaced unrelated existing failures in:
  - `tests/unit/test_feature_flags_defaults.py`
  - `tests/unit/test_notebooklm_assets.py`

## Next Milestone

Complete the next integration pass in:

1. wiki engine behavior
2. internal tool registration
3. runtime auto-sync behavior under real execution paths
4. entity/concept generation quality and real shared-memory integration coverage
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
- Smoke validation is now materially better: external flow is smoke-clean and internal sync completes quickly in bounded warm runs with telemetry, copy-skipping, and readable progress output.
- The next build step should focus on concept/entity quality and real shared-memory integration coverage.

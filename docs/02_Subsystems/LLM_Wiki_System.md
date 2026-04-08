# LLM Wiki System

**Canonical subsystem reference** for the shared LLM wiki engine that supports:

- external knowledge vaults
- the derived internal memory vault

## Overview

The LLM Wiki System adds a persistent, markdown-native knowledge layer on top of the existing Universal Agent runtime.

It operates in two modes:

1. **External Knowledge Vault**
   A canonical markdown wiki for outside sources. Raw sources are immutable. The wiki is the maintained synthesis layer.
2. **Internal Memory Vault**
   A derived markdown wiki built from existing canonical memory, session, checkpoint, and run evidence. It supplements recall and continuity without replacing runtime state.

## Canonical Boundaries

The LLM Wiki System does **not** replace:

- run/attempt lineage
- checkpoint files or checkpoint DB records
- existing `MEMORY.md` / `memory/*.md` / session memory indexes
- vector memory backends

The internal vault is rebuildable from those sources.

## Vault Locations

### External

- `UA_LLM_WIKI_ROOT/<vault_slug>/` if set
- otherwise `UA_ARTIFACTS_DIR/knowledge-vaults/<vault_slug>/`

### Internal

- `resolve_shared_memory_workspace()/memory/wiki/`

### Staging

- `CURRENT_RUN_WORKSPACE/tasks/<wiki_slug>/...`

## Shared Files

Each vault contains:

- `vault_manifest.json`
- `AGENTS.md`
- `index.md`
- `log.md`
- `overview.md`

## External Vault Structure

- `raw/`
- `sources/`
- `entities/`
- `concepts/`
- `analyses/`
- `assets/`
- `lint/`

## Internal Vault Structure

- `evidence/memory/`
- `evidence/sessions/`
- `evidence/checkpoints/`
- `decisions/`
- `preferences/`
- `incidents/`
- `projects/`
- `threads/`
- `analyses/`
- `lint/`

## Operations

### Ingest

External ingest:

- stage source in the run workspace
- preserve immutable raw source
- create/update source page
- update entity/concept pages
- update index/log/overview

Internal sync:

- read canonical memory/session/checkpoint evidence
- refresh evidence copies
- rebuild compiled pages
- update index/log/overview

### Query

- index-first retrieval
- page-level evidence selection
- cited answers
- optional filing into `analyses/`

### Lint

Deterministic checks include:

- broken wikilinks
- orphan pages
- missing index entries
- malformed frontmatter
- stale provenance refs
- missing source ids
- missing concept/entity candidates

## Runtime Surfaces

### Skill

- `.claude/skills/llm-wiki-orchestration/SKILL.md`

### Agent

- `.claude/agents/wiki-maintainer.md`

### Internal Tools

- `wiki_init_vault`
- `wiki_ingest_external_source`
- `wiki_sync_internal_memory`
- `wiki_query`
- `wiki_lint`
- `wiki_health` — observability: vault integrity, page counts, LLM availability
- `wiki_rebuild_page` — targeted re-ingestion and semantic enrichment of a source page

## NotebookLM Role

NotebookLM is optional and derivative:

- useful for external-source acceleration and derivative artifact generation
- not canonical for either vault
- outputs imported from NotebookLM must preserve derivative provenance

## Obsidian Compatibility

The system is designed to be Obsidian-friendly:

- markdown pages
- wikilinks
- YAML frontmatter
- local assets
- graph-friendly page structure
- Dataview-compatible metadata

## Testing Expectations

The subsystem requires:

- asset discovery tests
- tool registry tests
- vault scaffold tests
- ingest/query/lint tests
- wiki integrity tests
- documentation index/link tests

## LLM Integration Layer

Since April 2026, the wiki engine uses a semantic LLM layer (`wiki/llm.py`) for:

- **Entity extraction** — named entities worth creating wiki pages for
- **Concept extraction** — abstract concepts and techniques
- **Summary generation** — 2-3 sentence semantic summaries for index pages
- **Description generation** — entity/concept page content synthesis
- **Ledger synthesis** — structuring raw memory evidence into markdown

### Provider

The LLM layer uses the project's standard **Z.AI Anthropic emulation** (same pattern as `llm_classifier.py`). API key chain: `ANTHROPIC_API_KEY` → `ANTHROPIC_AUTH_TOKEN` → `ZAI_API_KEY`. Model resolved via `resolve_model('sonnet')` which currently maps to GLM-4.7.

### Graceful Degradation

All LLM-driven features fall back to existing heuristic methods if:

- No API key is available
- The LLM call fails (timeout, rate limit, etc.)

This keeps the wiki engine functional in CI, tests, and offline environments.

## TDD Test Suite

- `tests/unit/test_wiki_llm.py` — 18 unit tests (mocked LLM responses)
- `tests/unit/test_wiki_semantic_ingest.py` — 5 semantic ingest pipeline tests
- `tests/integration/test_wiki_integration_real_workspace.py` — 9 integration tests against real shared-memory workspace
- `tests/unit/test_llm_wiki_engine.py` — 3 existing engine tests

## Next Steps

1. **Monitor semantic quality** — use `wiki_health` to track vault integrity as usage scales
2. **Tune prompts** — adjust extraction prompts in `wiki/llm.py` if semantic quality needs refinement
3. **Expand recall integration** — connect wiki query into agent recall paths once quality is validated
4. **Keep internal sync observable** — preserve timing telemetry; if warm-run timing regresses, inspect phase timing output first

## How To Try It

These commands are intended for local operator smoke testing from the repo root.

### External Knowledge Vault

Create a temporary source file, initialize an external vault, ingest the source, query it, and lint it:

```bash
PYTHONPATH=src uv run python - <<'PY'
import json
import tempfile
from pathlib import Path
from universal_agent.wiki.core import ensure_vault, ingest_external_source, query_vault, lint_vault

work_dir = Path(tempfile.mkdtemp(prefix='llm_wiki_try_external_'))
external_root = work_dir / 'external_roots'
source_path = work_dir / 'sample_source.md'
source_path.write_text(
    "# Sample Source\n\n"
    "Universal Agent can maintain a persistent wiki with immutable raw sources, "
    "an index, and a log. Agents should query the wiki before raw sources.\n",
    encoding='utf-8',
)

ctx = ensure_vault('external', 'try-vault', root_override=str(external_root))
ingest = ingest_external_source(
    vault_slug='try-vault',
    source_path=str(source_path),
    title='Sample Source',
    root_override=str(external_root),
)
query = query_vault(
    vault_kind='external',
    vault_slug='try-vault',
    query='What is canonical and how should agents query it?',
    save_answer=True,
    answer_title='Try Query Result',
    root_override=str(external_root),
)
lint = lint_vault(
    vault_kind='external',
    vault_slug='try-vault',
    root_override=str(external_root),
)

print(json.dumps({
    'vault_path': str(ctx.path),
    'ingest': ingest,
    'query': query,
    'lint': lint,
}, indent=2))
PY
```

What to inspect after it runs:

- `sources/*.md`
- `entities/*.md`
- `analyses/*.md`
- `index.md`
- `log.md`
- `overview.md`
- `lint/*.md`

### Internal Memory Vault

Run a bounded internal sync and inspect the output plus sync telemetry:

```bash
PYTHONPATH=src uv run python - <<'PY'
import json
from universal_agent.wiki.core import sync_internal_memory_vault

result = sync_internal_memory_vault(trigger='manual_tryout')
print(json.dumps({
    'vault_path': result['vault_path'],
    'generated_pages': result['generated_pages'],
    'timings_ms': result.get('timings_ms', {}),
    'total_duration_ms': result.get('total_duration_ms'),
    'copied_counts': result.get('copied_counts', {}),
    'skipped_counts': result.get('skipped_counts', {}),
}, indent=2))
PY
```

Then inspect:

```bash
sed -n '1,200p' Memory_System/ua_shared_workspace/memory/wiki/sync_progress.md
find Memory_System/ua_shared_workspace/memory/wiki -maxdepth 2 -type f | sort
```

What to look for:

- `decisions/decision-ledger.md`
- `preferences/preferences-ledger.md`
- `incidents/incidents-ledger.md`
- `threads/recent-threads.md`
- `projects/project-memory.md`
- `sync_state.json`
- `sync_progress.json`
- `sync_progress.md`

## Related Files

- `src/universal_agent/wiki/`
- `src/universal_agent/tools/wiki_bridge.py`
- `src/universal_agent/tools/internal_registry.py`
- `src/universal_agent/scripts/doc_drift_auditor.py`
- `docs/03_Operations/109_LLM_Wiki_Implementation_Status_2026-04-06.md`

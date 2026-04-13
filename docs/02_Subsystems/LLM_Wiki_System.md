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

- `.claude/agents/notebooklm-operator.md` (for external KBs)
- Internal wiki is handled locally without a delegating subagent.

### Internal Tools

- `wiki_init_vault`
- `wiki_sync_internal_memory`
- `wiki_query`
- `wiki_lint`

### KB Registry Tools

Added in the NotebookLM migration (April 2026). These tools manage the local `kb_registry.json` mapping wiki vault slugs to NotebookLM notebook UUIDs.

- `kb_register` — register a new NotebookLM notebook as a knowledge base
- `kb_get` — retrieve a knowledge base entry by slug
- `kb_update` — update KB metadata (source count, last queried, tags)

### External Ingest (Code-Level)

`wiki_ingest_external_source` exists as a Python function in `wiki/core.py` for programmatic external vault ingestion with LLM semantic extraction. It is not currently registered as an MCP tool — external knowledge bases are primarily managed through NotebookLM via the `notebooklm-operator` agent.

## NotebookLM Role

NotebookLM is the CANONICAL engine for External Knowledge Bases (KBs).
- KBs are built and queried via the `notebooklm-operator` subagent using MCP tools.
- We maintain a local `kb_registry.json` mapping slugs to NotebookLM notebook UUIDs.
- Outputs imported from NotebookLM preserve derivative provenance and can be exported as Markdown, Audio, or PDFs.
- For internal memory syncs, the local Python tools are still used.

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

- **Entity extraction** — named entities (people, organizations, tools) extracted via LLM, with heuristic fallback
- **Concept extraction** — abstract concepts and themes extracted via LLM, with heuristic fallback
- **Summary generation** — 1-3 sentence semantic summaries via LLM, with first-sentence heuristic fallback

The LLM layer uses the project's standard **Z.AI Anthropic emulation** (same pattern as `llm_classifier.py`). API key chain: `ANTHROPIC_API_KEY` → `ANTHROPIC_AUTH_TOKEN` → `ZAI_API_KEY`. Model resolved via `resolve_sonnet()`.

### Graceful Degradation

All LLM-driven features fall back to existing heuristic methods if:

- No API key is available
- The LLM call fails (timeout, rate limit, etc.)

This keeps the wiki engine functional in CI, tests, and offline environments.

## TDD Test Suite

- `tests/unit/test_kb_registry.py` — KB registry unit tests
- `tests/unit/test_wiki_semantic_ingest.py` — semantic ingest pipeline tests
- `tests/unit/test_internal_registry_wiki_tools.py` — wiki tool registration tests

## Next Steps

1. **Monitor semantic quality** — use `wiki_lint` to track vault integrity as usage scales
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
from universal_agent.wiki.core import ensure_vault, wiki_ingest_external_source, query_vault, lint_vault

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
ingest = wiki_ingest_external_source(
    vault_slug='try-vault',
    source_title='Sample Source',
    source_content=source_path.read_text(encoding='utf-8'),
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
- `src/universal_agent/tools/kb_bridge.py`
- `src/universal_agent/tools/internal_registry.py`
- `src/universal_agent/scripts/doc_drift_auditor.py`
- `docs/03_Operations/109_LLM_Wiki_Implementation_Status_2026-04-06.md`

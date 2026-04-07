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

## Related Files

- `src/universal_agent/wiki/`
- `src/universal_agent/tools/wiki_bridge.py`
- `src/universal_agent/tools/internal_registry.py`
- `src/universal_agent/scripts/doc_drift_auditor.py`
- `docs/03_Operations/109_LLM_Wiki_Implementation_Status_2026-04-06.md`

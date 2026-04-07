---
name: llm-wiki-orchestration
description: >
  Operate LLM wiki vaults for external knowledge and internal memory projection.
  Use for knowledge vault creation, source ingest, wiki querying, wiki linting,
  internal memory wiki sync, Obsidian-friendly markdown knowledge bases, and
  long-lived knowledge or memory vault workflows.
---

# LLM Wiki Orchestration

Use this skill whenever the user wants a persistent markdown knowledge base or
memory vault rather than a one-shot retrieval answer.

## Routing Contract

1. Detect whether the request is for:
   - an **external knowledge vault**
   - an **internal memory vault**
2. Delegate to:
   - `Task(subagent_type='wiki-maintainer', ...)`
3. Keep the primary agent concise:
   - resolve intent
   - choose vault mode
   - delegate

## Vault Modes

### External Knowledge Vault

Use when the user wants to:
- ingest sources into a persistent wiki
- organize research or external documents
- ask questions against an accumulated knowledge base
- lint or maintain wiki integrity over time

### Internal Memory Vault

Use when the user wants to:
- supplement operational memory
- project durable memory/checkpoint/session knowledge into structured wiki pages
- query organized project memory rather than raw logs/snippets
- lint or refresh internal memory projections

## Required Operations

The delegated agent should support:

1. `wiki_init_vault`
2. `wiki_ingest_external_source`
3. `wiki_sync_internal_memory`
4. `wiki_query`
5. `wiki_lint`

## Behavioral Rules

- External vault raw sources are immutable.
- Internal vault is derived and rebuildable, not canonical runtime state.
- Query the wiki before raw evidence by default.
- Keep `index.md`, `log.md`, and `overview.md` current.
- Preserve provenance on every managed page.
- When asked to save a useful query result, file it into `analyses/`.

## Output Expectations

The delegated agent should return:

1. `status`
2. `vault_kind`
3. `vault_path`
4. `operation_summary`
5. `artifacts` or page paths touched
6. `warnings`
7. `next_step_if_blocked`

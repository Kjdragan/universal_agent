# UA Memory Hard-Cut (OpenClaw Parity)

Date: 2026-02-20  
Status: Implemented (hard break, no legacy runtime path)

## Summary

UA memory was converted from mixed legacy pathways to a single canonical memory pipeline modeled on OpenClaw behavior:

1. Canonical memory tools only: `memory_search`, `memory_get`.
2. Canonical memory policy schema: `enabled`, `sessionMemory`, `sources`, `scope`.
3. Canonical storage root for long-term/session memory index: `UA_SHARED_MEMORY_DIR` resolved root.
4. No legacy memory tool contracts in runtime.

## Canonical Behavior

## Source of truth

1. `MEMORY.md`
2. `memory/YYYY-MM-DD.md`
3. `memory/sessions/*.md` (session transcript-derived memory slices)

## Retrieval model

1. Snippet-first recall with path + line metadata.
2. Semantic-first retrieval with lexical fallback.
3. Session transcript indexing supported and forced on session close.
4. Scope gate supports `direct_only` or `all`.

## Flush model

1. Pre-compaction flush remains best-effort and writes durable memory entries.
2. Controlled by:
   - `UA_MEMORY_FLUSH_ENABLED`
   - `UA_MEMORY_FLUSH_SOFT_THRESHOLD_TOKENS`

## Public Contract Changes

## Removed memory tool names

1. `core_memory_replace`
2. `core_memory_append`
3. `archival_memory_insert`
4. `archival_memory_search`
5. `get_core_memory_blocks`
6. `ua_memory_search`
7. `ua_memory_get`

## Active memory tool names

1. `memory_search`
2. `memory_get`

## Session policy memory schema

```json
{
  "memory": {
    "enabled": true,
    "sessionMemory": true,
    "sources": ["memory", "sessions"],
    "scope": "direct_only"
  }
}
```

## Environment defaults

```bash
UA_MEMORY_ENABLED=1
UA_MEMORY_PROVIDER=auto
UA_MEMORY_SOURCES=memory,sessions
UA_MEMORY_SCOPE=direct_only
UA_MEMORY_SESSION_ENABLED=1
UA_MEMORY_FLUSH_ENABLED=1
UA_MEMORY_FLUSH_SOFT_THRESHOLD_TOKENS=4000
UA_SHARED_MEMORY_DIR=Memory_System/ua_shared_workspace
```

## Migration and Cleanup

Use the migration utility:

```bash
python scripts/memory_hard_cut_migrate.py --dry-run
python scripts/memory_hard_cut_migrate.py --delete-legacy
```

Behavior:

1. Scans session workspaces for legacy/session memory artifacts.
2. Backfills Markdown memory and transcript-derived session memory into canonical shared memory.
3. Produces JSON migration report.
4. Archives legacy paths to tarball.
5. Deletes legacy paths when `--delete-legacy` is provided.

## Verification Runbook

1. Confirm canonical tools only:
   - `mcp__internal__memory_search`
   - `mcp__internal__memory_get`
2. Start session A, create memory, end session.
3. Delete session A workspace.
4. Start session B, query prior memory with `memory_search`.
5. Confirm result includes snippets and file line metadata.
6. Confirm transcript memory appears in `memory/sessions/*.md` under shared root.

## Rollback

Rollback policy is intentionally none for this cut.  
If issues occur, fix forward on canonical path.

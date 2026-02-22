# CSI Phase 1 Reliability Validation (2026-02-22)

## Scope Covered

1. Persistent adapter state in SQLite (`source_state` table).
2. One-time seeding markers that survive restarts.
3. Restart-safe seen ID caches for:
   1. YouTube playlist adapter
   2. YouTube channel RSS adapter
4. SQLite schema migration tracking (`schema_migrations` table).

## Key Files Updated

1. `CSI_Ingester/development/csi_ingester/store/sqlite.py`
2. `CSI_Ingester/development/csi_ingester/store/source_state.py`
3. `CSI_Ingester/development/csi_ingester/service.py`
4. `CSI_Ingester/development/csi_ingester/adapters/youtube_playlist.py`
5. `CSI_Ingester/development/csi_ingester/adapters/youtube_channel_rss.py`
6. `CSI_Ingester/development/tests/unit/test_source_state_store.py`
7. `CSI_Ingester/development/tests/unit/test_youtube_playlist_adapter.py`
8. `CSI_Ingester/development/tests/unit/test_youtube_rss_adapter.py`

## Verification Commands Executed

1. CSI unit suite:

```bash
PYTHONPATH=CSI_Ingester/development uv run pytest CSI_Ingester/development/tests/unit -q
```

Result: `13 passed`

2. CSI + UA regression subset:

```bash
PYTHONPATH=CSI_Ingester/development uv run pytest CSI_Ingester/development/tests/unit tests/unit/test_signals_ingest.py tests/gateway/test_signals_ingest_endpoint.py tests/contract/test_csi_ua_contract.py tests/test_hooks_service.py -q
```

Result: `50 passed`

3. Python compile check for modified CSI files:

```bash
python3 -m py_compile CSI_Ingester/development/csi_ingester/adapters/youtube_playlist.py CSI_Ingester/development/csi_ingester/adapters/youtube_channel_rss.py CSI_Ingester/development/csi_ingester/service.py CSI_Ingester/development/csi_ingester/store/sqlite.py CSI_Ingester/development/csi_ingester/store/source_state.py
```

Result: success

## Observed Outcome

1. Adapter restart behavior now preserves seeding/checkpoint semantics.
2. Schema upgrades can be applied idempotently through `ensure_schema`.
3. No regressions detected in CSI or UA contract/ingest paths.


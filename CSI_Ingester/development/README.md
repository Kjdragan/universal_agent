# CSI Ingester Development

Standalone implementation workspace for CSI Ingester v1.

## Quick start

```bash
cd CSI_Ingester/development
source scripts/csi_dev_env.sh
scripts/csi_run.sh uv run uvicorn csi_ingester.app:app --host 0.0.0.0 --port 8091
```

Run tests with the same wrapper:

```bash
scripts/csi_run.sh uv run --group dev pytest tests/unit/test_signature.py -q
```

Run preflight checks:

```bash
scripts/csi_run.sh scripts/csi_preflight.sh
scripts/csi_run.sh scripts/csi_preflight.sh --strict
```

Run parallel-run snapshot checks:

```bash
scripts/csi_run.sh python3 scripts/csi_parallel_validate.py --db-path /path/to/csi.db --since-minutes 60
```

Run signed CSI->UA smoke check:

```bash
uv run python scripts/csi_local_e2e_smoke.py
```

Run endpoint smoke against a live UA endpoint:

```bash
scripts/csi_run.sh uv run python scripts/csi_emit_smoke_event.py --require-internal-dispatch
```

## Structure

- `csi_ingester/`: runtime package
- `config/`: config examples
- `scripts/`: operational utilities
- `tests/`: unit, contract, integration tests

Key runtime notes:

- SQLite schema is migration-based (`schema_migrations` table).
- Adapter checkpoint/seed state is persisted in `source_state` for restart-safe behavior.

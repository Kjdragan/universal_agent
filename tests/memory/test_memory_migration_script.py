from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest


def _load_migration_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "migrate_session_memory_dbs.py"
    spec = importlib.util.spec_from_file_location("migrate_session_memory_dbs", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load migration script module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _seed_sqlite(path: Path, *, core_blocks: list[tuple], traces: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS core_blocks (
                label TEXT PRIMARY KEY,
                value TEXT,
                description TEXT,
                is_editable BOOLEAN,
                last_updated TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_traces (
                trace_id TEXT PRIMARY KEY,
                timestamp TEXT
            )
            """
        )
        for row in core_blocks:
            conn.execute(
                "INSERT OR REPLACE INTO core_blocks (label, value, description, is_editable, last_updated) VALUES (?, ?, ?, ?, ?)",
                row,
            )
        for row in traces:
            conn.execute(
                "INSERT OR REPLACE INTO processed_traces (trace_id, timestamp) VALUES (?, ?)",
                row,
            )
        conn.commit()
    finally:
        conn.close()


def test_migration_merges_sqlite_and_is_idempotent(tmp_path: Path):
    module = _load_migration_module()
    source_root = tmp_path / "AGENT_RUN_WORKSPACES"
    target_dir = tmp_path / "Memory_System" / "data"

    source_a = source_root / "session_a" / "Memory_System_Data" / "agent_core.db"
    source_b = source_root / "session_b" / "Memory_System_Data" / "agent_core.db"

    _seed_sqlite(
        source_a,
        core_blocks=[
            ("persona", "old persona", "desc", 1, "2026-01-01T00:00:00+00:00"),
        ],
        traces=[("trace_a", "2026-01-01T00:00:00+00:00")],
    )
    _seed_sqlite(
        source_b,
        core_blocks=[
            ("persona", "new persona", "desc", 1, "2026-02-01T00:00:00+00:00"),
        ],
        traces=[("trace_a", "2026-01-01T00:00:00+00:00"), ("trace_b", "2026-02-01T00:00:00+00:00")],
    )

    report_first = module.run_migration(
        source_root=source_root,
        target_persist_dir=target_dir,
        dry_run=False,
    )
    assert report_first["sources_scanned"] == 2
    assert report_first["sqlite_totals"]["core_blocks"]["inserted"] >= 1
    assert report_first["sqlite_totals"]["core_blocks"]["updated"] >= 1
    assert report_first["sqlite_totals"]["processed_traces"]["inserted"] == 2

    target_conn = sqlite3.connect(target_dir / "agent_core.db")
    try:
        row = target_conn.execute(
            "SELECT value FROM core_blocks WHERE label = 'persona'"
        ).fetchone()
        assert row is not None
        assert row[0] == "new persona"
    finally:
        target_conn.close()

    report_second = module.run_migration(
        source_root=source_root,
        target_persist_dir=target_dir,
        dry_run=False,
    )
    assert report_second["sqlite_totals"]["core_blocks"]["inserted"] == 0
    assert report_second["sqlite_totals"]["core_blocks"]["updated"] == 0
    assert report_second["sqlite_totals"]["processed_traces"]["inserted"] == 0


def test_migration_chroma_content_dedupe(tmp_path: Path):
    module = _load_migration_module()
    if module.chromadb is None:
        pytest.skip("chromadb not available")

    source_root = tmp_path / "AGENT_RUN_WORKSPACES"
    target_dir = tmp_path / "Memory_System" / "data"
    source_a = source_root / "session_a" / "Memory_System_Data" / "chroma_db"
    source_b = source_root / "session_b" / "Memory_System_Data" / "chroma_db"
    source_a.mkdir(parents=True, exist_ok=True)
    source_b.mkdir(parents=True, exist_ok=True)

    client_a = module.chromadb.PersistentClient(path=str(source_a))
    coll_a = client_a.get_or_create_collection(module.COLLECTION_NAME)
    coll_a.upsert(
        ids=["a1"],
        documents=["shared content one"],
        metadatas=[{"timestamp": "2026-01-01T00:00:00+00:00"}],
    )

    client_b = module.chromadb.PersistentClient(path=str(source_b))
    coll_b = client_b.get_or_create_collection(module.COLLECTION_NAME)
    coll_b.upsert(
        ids=["b1", "b2"],
        documents=["shared content one", "unique content two"],
        metadatas=[
            {"timestamp": "2026-01-02T00:00:00+00:00"},
            {"timestamp": "2026-01-03T00:00:00+00:00"},
        ],
    )

    report_first = module.run_migration(
        source_root=source_root,
        target_persist_dir=target_dir,
        dry_run=False,
    )
    assert report_first["chroma_totals"]["inserted"] == 2
    assert report_first["chroma_totals"]["skipped"] >= 1

    report_second = module.run_migration(
        source_root=source_root,
        target_persist_dir=target_dir,
        dry_run=False,
    )
    assert report_second["chroma_totals"]["inserted"] == 0

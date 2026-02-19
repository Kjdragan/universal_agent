#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from universal_agent.memory.paths import resolve_persist_directory

try:
    import chromadb
except Exception:  # pragma: no cover - dependency import guard for script usage
    chromadb = None  # type: ignore


COLLECTION_NAME = "archival_memory"


@dataclass
class TableStats:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _ensure_target_sqlite_schema(conn: sqlite3.Connection) -> None:
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
    conn.commit()


def _timestamp_key(raw: str | None) -> tuple[int, str]:
    value = (raw or "").strip()
    if not value:
        return (0, "")
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        return (1, parsed.isoformat())
    except ValueError:
        return (0, value)


def _merge_core_blocks(
    source_conn: sqlite3.Connection,
    target_conn: sqlite3.Connection,
    *,
    dry_run: bool,
) -> TableStats:
    stats = TableStats()
    if not _table_exists(source_conn, "core_blocks"):
        return stats

    rows = source_conn.execute(
        """
        SELECT label, value, description, is_editable, last_updated
        FROM core_blocks
        """
    ).fetchall()
    for row in rows:
        label, value, description, is_editable, last_updated = row
        existing = target_conn.execute(
            """
            SELECT value, description, is_editable, last_updated
            FROM core_blocks
            WHERE label = ?
            """,
            (label,),
        ).fetchone()
        if existing is None:
            if not dry_run:
                target_conn.execute(
                    """
                    INSERT INTO core_blocks (label, value, description, is_editable, last_updated)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (label, value, description, is_editable, last_updated),
                )
            stats.inserted += 1
            continue

        source_ts = _timestamp_key(last_updated)
        existing_ts = _timestamp_key(existing[3])
        if source_ts <= existing_ts:
            stats.skipped += 1
            continue

        if not dry_run:
            target_conn.execute(
                """
                UPDATE core_blocks
                SET value = ?, description = ?, is_editable = ?, last_updated = ?
                WHERE label = ?
                """,
                (value, description, is_editable, last_updated, label),
            )
        stats.updated += 1

    return stats


def _merge_processed_traces(
    source_conn: sqlite3.Connection,
    target_conn: sqlite3.Connection,
    *,
    dry_run: bool,
) -> TableStats:
    stats = TableStats()
    if not _table_exists(source_conn, "processed_traces"):
        return stats

    rows = source_conn.execute(
        "SELECT trace_id, timestamp FROM processed_traces"
    ).fetchall()
    for trace_id, timestamp in rows:
        existing = target_conn.execute(
            "SELECT 1 FROM processed_traces WHERE trace_id = ? LIMIT 1",
            (trace_id,),
        ).fetchone()
        if existing is not None:
            stats.skipped += 1
            continue
        if not dry_run:
            target_conn.execute(
                "INSERT INTO processed_traces (trace_id, timestamp) VALUES (?, ?)",
                (trace_id, timestamp),
            )
        stats.inserted += 1
    return stats


def _fingerprint_content(content: str) -> str:
    normalized = " ".join((content or "").split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _iter_collection_rows(collection: Any, batch_size: int = 500) -> list[tuple[str, str, dict[str, Any]]]:
    rows: list[tuple[str, str, dict[str, Any]]] = []
    try:
        total = int(collection.count())
    except Exception:
        total = 0

    if total <= 0:
        return rows

    supports_paging = True
    for offset in range(0, total, batch_size):
        try:
            if supports_paging:
                payload = collection.get(
                    include=["documents", "metadatas"],
                    limit=batch_size,
                    offset=offset,
                )
            else:
                payload = collection.get(include=["documents", "metadatas"])
        except TypeError:
            supports_paging = False
            payload = collection.get(include=["documents", "metadatas"])
        ids = payload.get("ids") or []
        documents = payload.get("documents") or []
        metadatas = payload.get("metadatas") or []
        for idx, item_id in enumerate(ids):
            doc = (documents[idx] if idx < len(documents) else "") or ""
            metadata = metadatas[idx] if idx < len(metadatas) and metadatas[idx] else {}
            rows.append((str(item_id), str(doc), dict(metadata)))
        if not supports_paging:
            break
    return rows


def _collection_exists(client: Any, name: str) -> bool:
    try:
        collections = client.list_collections()
    except Exception:
        return False
    for item in collections:
        if isinstance(item, str) and item == name:
            return True
        if getattr(item, "name", None) == name:
            return True
    return False


def _merge_chroma_store(
    source_dir: Path,
    target_dir: Path,
    *,
    dry_run: bool,
) -> TableStats:
    stats = TableStats()
    if chromadb is None:
        return stats
    if not source_dir.exists():
        return stats

    source_client = chromadb.PersistentClient(path=str(source_dir))
    if not _collection_exists(source_client, COLLECTION_NAME):
        return stats

    source_collection = source_client.get_collection(COLLECTION_NAME)
    target_client = chromadb.PersistentClient(path=str(target_dir))
    target_collection = target_client.get_or_create_collection(COLLECTION_NAME)

    target_fingerprints: set[str] = set()
    for _, content, _ in _iter_collection_rows(target_collection):
        target_fingerprints.add(_fingerprint_content(content))

    pending_ids: list[str] = []
    pending_docs: list[str] = []
    pending_meta: list[dict[str, Any]] = []

    for _, content, metadata in _iter_collection_rows(source_collection):
        fingerprint = _fingerprint_content(content)
        if fingerprint in target_fingerprints:
            stats.skipped += 1
            continue

        stats.inserted += 1
        target_fingerprints.add(fingerprint)
        if dry_run:
            continue

        pending_ids.append(f"migrated_{fingerprint}")
        pending_docs.append(content)
        pending_meta.append(metadata or {})
        if len(pending_ids) >= 250:
            target_collection.upsert(
                ids=pending_ids,
                documents=pending_docs,
                metadatas=pending_meta,
            )
            pending_ids.clear()
            pending_docs.clear()
            pending_meta.clear()

    if pending_ids and not dry_run:
        target_collection.upsert(
            ids=pending_ids,
            documents=pending_docs,
            metadatas=pending_meta,
        )
    return stats


def _discover_source_dirs(source_root: Path, target_persist_dir: Path) -> list[Path]:
    discovered: list[Path] = []
    target_resolved = target_persist_dir.resolve()
    for candidate in source_root.rglob("Memory_System_Data"):
        if not candidate.is_dir():
            continue
        resolved = candidate.resolve()
        if resolved == target_resolved:
            continue
        if (resolved / "agent_core.db").exists() or (resolved / "chroma_db").exists():
            discovered.append(resolved)
    return sorted(set(discovered))


def _default_report_path() -> Path:
    report_dir = REPO_ROOT / "AGENT_RUN_WORKSPACES"
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return report_dir / f"memory_migration_report_{stamp}.json"


def run_migration(
    *,
    source_root: Path,
    target_persist_dir: Path,
    dry_run: bool,
) -> dict[str, Any]:
    source_dirs = _discover_source_dirs(source_root, target_persist_dir)

    sqlite_totals = {
        "core_blocks": asdict(TableStats()),
        "processed_traces": asdict(TableStats()),
    }
    chroma_totals = asdict(TableStats())
    errors: list[str] = []
    per_source: list[dict[str, Any]] = []

    target_persist_dir.mkdir(parents=True, exist_ok=True)
    target_sqlite_path = target_persist_dir / "agent_core.db"
    target_conn = sqlite3.connect(target_sqlite_path)
    try:
        _ensure_target_sqlite_schema(target_conn)

        for source_dir in source_dirs:
            source_report: dict[str, Any] = {
                "source_dir": str(source_dir),
                "sqlite": {
                    "core_blocks": asdict(TableStats()),
                    "processed_traces": asdict(TableStats()),
                },
                "chroma": asdict(TableStats()),
                "errors": [],
            }
            try:
                source_sqlite = source_dir / "agent_core.db"
                if source_sqlite.exists():
                    source_conn = sqlite3.connect(source_sqlite)
                    try:
                        core_stats = _merge_core_blocks(source_conn, target_conn, dry_run=dry_run)
                        trace_stats = _merge_processed_traces(source_conn, target_conn, dry_run=dry_run)
                        source_report["sqlite"]["core_blocks"] = asdict(core_stats)
                        source_report["sqlite"]["processed_traces"] = asdict(trace_stats)
                    finally:
                        source_conn.close()

                chroma_stats = _merge_chroma_store(
                    source_dir / "chroma_db",
                    target_persist_dir / "chroma_db",
                    dry_run=dry_run,
                )
                source_report["chroma"] = asdict(chroma_stats)
            except Exception as exc:
                message = f"{source_dir}: {exc}"
                source_report["errors"].append(message)
                errors.append(message)
            per_source.append(source_report)

            for key in ("inserted", "updated", "skipped"):
                sqlite_totals["core_blocks"][key] += int(source_report["sqlite"]["core_blocks"][key])
                sqlite_totals["processed_traces"][key] += int(source_report["sqlite"]["processed_traces"][key])
                chroma_totals[key] += int(source_report["chroma"][key])

        if not dry_run:
            target_conn.commit()
    finally:
        target_conn.close()

    return {
        "generated_at": _now_utc(),
        "dry_run": dry_run,
        "source_root": str(source_root.resolve()),
        "target_persist_dir": str(target_persist_dir.resolve()),
        "sources_scanned": len(source_dirs),
        "sqlite_totals": sqlite_totals,
        "chroma_totals": chroma_totals,
        "sources": per_source,
        "errors": errors,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge session-local Memory_System_Data into persistent Memory_System/data.",
    )
    parser.add_argument(
        "--source-root",
        default=str((REPO_ROOT / "AGENT_RUN_WORKSPACES").resolve()),
        help="Root directory to scan for Memory_System_Data folders.",
    )
    parser.add_argument(
        "--target-persist-dir",
        default=resolve_persist_directory(None),
        help="Persistent target directory containing agent_core.db and chroma_db.",
    )
    parser.add_argument(
        "--report-path",
        default=str(_default_report_path()),
        help="Path to write JSON migration report.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze and report without writing changes.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    source_root = Path(args.source_root).resolve()
    target_persist_dir = Path(args.target_persist_dir).resolve()
    report_path = Path(args.report_path).resolve()

    report = run_migration(
        source_root=source_root,
        target_persist_dir=target_persist_dir,
        dry_run=bool(args.dry_run),
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=True), encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=True))
    print(f"\nReport written to: {report_path}")
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

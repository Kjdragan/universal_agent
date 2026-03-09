"""Persistent SQLite-backed factory registration store.

Replaces the in-memory ``_factory_registrations`` dict in gateway_server.py
so that registrations survive HQ gateway restarts.  Also provides stale/offline
detection enforcement.

Schema
------
::

    factory_registrations (
        factory_id          TEXT PRIMARY KEY,
        factory_role        TEXT NOT NULL,
        deployment_profile  TEXT NOT NULL DEFAULT 'local_workstation',
        source              TEXT NOT NULL DEFAULT 'unknown',
        registration_status TEXT NOT NULL DEFAULT 'online',
        heartbeat_latency_ms REAL,
        capabilities        TEXT DEFAULT '[]',   -- JSON array
        metadata            TEXT DEFAULT '{}',    -- JSON object
        first_seen_at       TEXT NOT NULL,
        last_seen_at        TEXT NOT NULL,
        updated_at          TEXT NOT NULL
    )
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS factory_registrations (
    factory_id          TEXT PRIMARY KEY,
    factory_role        TEXT NOT NULL,
    deployment_profile  TEXT NOT NULL DEFAULT 'local_workstation',
    source              TEXT NOT NULL DEFAULT 'unknown',
    registration_status TEXT NOT NULL DEFAULT 'online',
    heartbeat_latency_ms REAL,
    capabilities        TEXT DEFAULT '[]',
    metadata            TEXT DEFAULT '{}',
    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
"""

_UPSERT_SQL = """
INSERT INTO factory_registrations (
    factory_id, factory_role, deployment_profile, source,
    registration_status, heartbeat_latency_ms, capabilities, metadata,
    first_seen_at, last_seen_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(factory_id) DO UPDATE SET
    factory_role        = excluded.factory_role,
    deployment_profile  = excluded.deployment_profile,
    source              = excluded.source,
    registration_status = excluded.registration_status,
    heartbeat_latency_ms = excluded.heartbeat_latency_ms,
    capabilities        = excluded.capabilities,
    metadata            = excluded.metadata,
    last_seen_at        = excluded.last_seen_at,
    updated_at          = excluded.updated_at
"""

# Thresholds in seconds
STALE_THRESHOLD_SECONDS = 300    # 5 min → stale
OFFLINE_THRESHOLD_SECONDS = 900  # 15 min → offline


class FactoryRegistry:
    """Thread-safe, SQLite-backed factory registration store."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self._lock:
            self._conn.execute(_CREATE_TABLE_SQL)
            self._conn.commit()

    def upsert(self, payload: dict[str, Any], *, source: str) -> dict[str, Any]:
        """Insert or update a factory registration.  Returns the record dict."""
        now_iso = datetime.now(timezone.utc).isoformat()
        factory_id = str(payload.get("factory_id") or "").strip()
        if not factory_id:
            raise ValueError("factory_id is required")

        role = str(payload.get("factory_role") or "UNKNOWN").strip()
        profile = str(payload.get("deployment_profile") or "local_workstation").strip()
        status = str(payload.get("registration_status") or "online").strip() or "online"
        latency = payload.get("heartbeat_latency_ms")
        caps = payload.get("capabilities") if isinstance(payload.get("capabilities"), list) else []
        meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}

        caps_json = json.dumps(caps, sort_keys=True)
        meta_json = json.dumps(meta, sort_keys=True)

        with self._lock:
            # Preserve first_seen_at from existing row
            row = self._conn.execute(
                "SELECT first_seen_at FROM factory_registrations WHERE factory_id = ?",
                (factory_id,),
            ).fetchone()
            first_seen = row[0] if row else now_iso

            self._conn.execute(
                _UPSERT_SQL,
                (
                    factory_id, role, profile, source,
                    status, latency, caps_json, meta_json,
                    first_seen, now_iso, now_iso,
                ),
            )
            self._conn.commit()

        return {
            "factory_id": factory_id,
            "factory_role": role,
            "deployment_profile": profile,
            "source": source,
            "registration_status": status,
            "heartbeat_latency_ms": latency,
            "capabilities": caps,
            "metadata": meta,
            "first_seen_at": first_seen,
            "last_seen_at": now_iso,
            "updated_at": now_iso,
        }

    def get(self, factory_id: str) -> Optional[dict[str, Any]]:
        """Get a single factory registration by ID."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM factory_registrations WHERE factory_id = ?",
                (factory_id,),
            ).fetchone()
        return _row_to_dict(row) if row else None

    def list_all(
        self,
        *,
        limit: int = 200,
        status_filter: str = "",
    ) -> list[dict[str, Any]]:
        """List all registrations, optionally filtered by status."""
        with self._lock:
            if status_filter:
                rows = self._conn.execute(
                    "SELECT * FROM factory_registrations WHERE registration_status = ? "
                    "ORDER BY last_seen_at DESC LIMIT ?",
                    (status_filter, max(1, min(limit, 1000))),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM factory_registrations ORDER BY last_seen_at DESC LIMIT ?",
                    (max(1, min(limit, 1000)),),
                ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def count(self) -> int:
        """Total number of registered factories."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM factory_registrations").fetchone()
        return int(row[0]) if row else 0

    def delete_ids(self, factory_ids: list[str]) -> int:
        """Delete registry rows by factory_id. Returns deleted row count."""
        normalized = [str(fid).strip() for fid in factory_ids if str(fid).strip()]
        if not normalized:
            return 0
        placeholders = ",".join("?" for _ in normalized)
        with self._lock:
            cursor = self._conn.execute(
                f"DELETE FROM factory_registrations WHERE factory_id IN ({placeholders})",
                tuple(normalized),
            )
            self._conn.commit()
        return int(cursor.rowcount or 0)

    def enforce_staleness(self) -> dict[str, int]:
        """Mark stale/offline factories based on last_seen_at thresholds.

        Returns counts of factories transitioned to each status.
        """
        now = datetime.now(timezone.utc)
        stale_cutoff = _iso_minus_seconds(now, STALE_THRESHOLD_SECONDS)
        offline_cutoff = _iso_minus_seconds(now, OFFLINE_THRESHOLD_SECONDS)

        with self._lock:
            # Mark offline first (superset threshold)
            cursor_offline = self._conn.execute(
                "UPDATE factory_registrations "
                "SET registration_status = 'offline', updated_at = ? "
                "WHERE last_seen_at < ? AND registration_status != 'offline'",
                (now.isoformat(), offline_cutoff),
            )
            offline_count = cursor_offline.rowcount

            # Mark stale (between stale and offline thresholds)
            cursor_stale = self._conn.execute(
                "UPDATE factory_registrations "
                "SET registration_status = 'stale', updated_at = ? "
                "WHERE last_seen_at < ? AND last_seen_at >= ? "
                "AND registration_status = 'online'",
                (now.isoformat(), stale_cutoff, offline_cutoff),
            )
            stale_count = cursor_stale.rowcount

            self._conn.commit()

        if offline_count or stale_count:
            logger.info(
                "FactoryRegistry staleness enforced: %d→stale, %d→offline",
                stale_count, offline_count,
            )
        return {"stale": stale_count, "offline": offline_count}


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite3.Row to a plain dict with parsed JSON fields."""
    d = dict(row)
    for json_field in ("capabilities", "metadata"):
        raw = d.get(json_field)
        if isinstance(raw, str):
            try:
                d[json_field] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                d[json_field] = [] if json_field == "capabilities" else {}
    return d


def _iso_minus_seconds(dt: datetime, seconds: int) -> str:
    """Return ISO timestamp ``seconds`` before ``dt``."""
    from datetime import timedelta
    return (dt - timedelta(seconds=seconds)).isoformat()


def connect_registry_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Connect to (or create) the factory registry database."""
    import os
    if not db_path:
        db_path = os.getenv("UA_FACTORY_REGISTRY_DB_PATH", "")
        if not db_path:
            repo_root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            )
            runtime_dir = os.path.join(repo_root, "AGENT_RUN_WORKSPACES")
            os.makedirs(runtime_dir, exist_ok=True)
            db_path = os.path.join(runtime_dir, "factory_registry.db")

    conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn

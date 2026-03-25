from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from universal_agent.durable.db import connect_runtime_db, get_runtime_db_path
from universal_agent.durable.migrations import ensure_schema

logger = logging.getLogger(__name__)


class RunCatalogService:
    """Read-only durable run catalog backed by the runtime database."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or get_runtime_db_path()

    def _connect(self) -> sqlite3.Connection:
        conn = connect_runtime_db(self.db_path)
        ensure_schema(conn)
        return conn

    @staticmethod
    def _normalize_workspace_dir(value: Optional[str]) -> Optional[str]:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return str(Path(raw).resolve())
        except Exception:
            return raw

    @classmethod
    def _extract_workspace_dir(cls, row: sqlite3.Row) -> Optional[str]:
        workspace_dir = cls._normalize_workspace_dir(row["workspace_dir"])
        if workspace_dir:
            return workspace_dir
        run_spec_raw = row["run_spec_json"]
        if not run_spec_raw:
            return None
        try:
            payload = json.loads(run_spec_raw)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        return cls._normalize_workspace_dir(payload.get("workspace_dir"))

    @classmethod
    def _row_to_summary(cls, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "workspace_dir": cls._extract_workspace_dir(row),
            "status": row["status"],
            "entrypoint": row["entrypoint"],
            "run_kind": row["run_kind"],
            "trigger_source": row["trigger_source"],
            "dedup_key": row["dedup_key"],
            "run_policy": row["run_policy"],
            "interrupt_policy": row["interrupt_policy"],
            "terminal_reason": row["terminal_reason"],
            "attempt_count": int(row["attempt_count"] or 0),
            "latest_attempt_id": row["latest_attempt_id"],
            "last_success_attempt_id": row["last_success_attempt_id"],
            "canonical_attempt_id": row["canonical_attempt_id"],
            "provider_session_id": row["provider_session_id"],
            "external_origin": row["external_origin"],
            "external_origin_id": row["external_origin_id"],
            "external_correlation_id": row["external_correlation_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_runs(self, limit: int = 500) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM runs
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            return [self._row_to_summary(row) for row in rows]
        finally:
            conn.close()

    def list_runs_for_workspace_prefix(
        self,
        workspace_prefix: Path | str,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        prefix = self._normalize_workspace_dir(str(workspace_prefix or ""))
        if not prefix:
            return []
        like_prefix = f"{prefix.rstrip('/')}/%"

        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM runs
                WHERE workspace_dir = ?
                   OR workspace_dir LIKE ?
                   OR run_spec_json LIKE ?
                ORDER BY updated_at DESC, created_at DESC
                LIMIT ?
                """,
                (
                    prefix,
                    like_prefix,
                    f'%"workspace_dir": "{prefix}%',
                    max(1, int(limit)),
                ),
            ).fetchall()
            summaries: list[dict[str, Any]] = []
            seen: set[str] = set()
            for row in rows:
                summary = self._row_to_summary(row)
                run_id = str(summary.get("run_id") or "")
                if not run_id or run_id in seen:
                    continue
                seen.add(run_id)
                summaries.append(summary)
            return summaries
        finally:
            conn.close()

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?",
                (str(run_id or "").strip(),),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_summary(row)
        finally:
            conn.close()

    def find_run_for_workspace(self, workspace_dir: Path | str) -> Optional[dict[str, Any]]:
        workspace_key = self._normalize_workspace_dir(str(workspace_dir or ""))
        if not workspace_key:
            return None

        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT *
                FROM runs
                WHERE workspace_dir = ?
                ORDER BY updated_at DESC, created_at DESC
                """,
                (workspace_key,),
            ).fetchall()
            if rows:
                return self._row_to_summary(rows[0])

            rows = conn.execute(
                """
                SELECT *
                FROM runs
                WHERE run_spec_json LIKE ?
                ORDER BY updated_at DESC, created_at DESC
                """,
                (f'%"workspace_dir": "{workspace_key}"%',),
            ).fetchall()
            for row in rows:
                summary = self._row_to_summary(row)
                if summary.get("workspace_dir") == workspace_key:
                    return summary
            return None
        finally:
            conn.close()

    def find_latest_run_for_provider_session(self, provider_session_id: str) -> Optional[dict[str, Any]]:
        session_key = str(provider_session_id or "").strip()
        if not session_key:
            return None

        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT *
                FROM runs
                WHERE provider_session_id = ?
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (session_key,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_summary(row)
        finally:
            conn.close()

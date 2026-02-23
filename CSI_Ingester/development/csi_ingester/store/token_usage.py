"""Token usage persistence helpers."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _coerce_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return 0


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT NOT NULL,
            process_name TEXT NOT NULL,
            model_name TEXT,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_occurred_at ON token_usage(occurred_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_process_name ON token_usage(process_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_model_name ON token_usage(model_name)")
    conn.commit()


def insert_usage(
    conn: sqlite3.Connection,
    *,
    process_name: str,
    model_name: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    occurred_at: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    row_occurred_at = occurred_at.strip() if occurred_at else _utc_now_text()
    prompt = _coerce_int(prompt_tokens)
    completion = _coerce_int(completion_tokens)
    total = _coerce_int(total_tokens)
    if total <= 0:
        total = prompt + completion
    payload = metadata if isinstance(metadata, dict) else {}

    try:
        conn.execute(
            """
            INSERT INTO token_usage (
                occurred_at, process_name, model_name, prompt_tokens,
                completion_tokens, total_tokens, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_occurred_at,
                process_name.strip() or "unknown",
                (model_name or "").strip() or None,
                prompt,
                completion,
                total,
                json.dumps(payload, separators=(",", ":"), sort_keys=True),
            ),
        )
        conn.commit()
    except sqlite3.OperationalError:
        _ensure_table(conn)
        conn.execute(
            """
            INSERT INTO token_usage (
                occurred_at, process_name, model_name, prompt_tokens,
                completion_tokens, total_tokens, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row_occurred_at,
                process_name.strip() or "unknown",
                (model_name or "").strip() or None,
                prompt,
                completion,
                total,
                json.dumps(payload, separators=(",", ":"), sort_keys=True),
            ),
        )
        conn.commit()


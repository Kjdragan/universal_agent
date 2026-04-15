"""Preference scoring for proactive artifact surfacing.

Phase 1 keeps this deliberately small and SQLite-backed. The model learns from
explicit feedback and is used to rank surfacing candidates, not to suppress
future artifact generation.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS proactive_preference_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            artifact_id TEXT NOT NULL,
            signal_key TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            weight REAL NOT NULL,
            score INTEGER,
            text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_proactive_preference_signals_key
            ON proactive_preference_signals(signal_key, created_at DESC)
        """
    )
    conn.commit()


def signal_weight_for_score(score: int | None) -> float:
    if score in {1, 5}:
        return 0.9
    if score in {3, 4}:
        return -0.8
    if score == 2:
        return 0.2
    return 0.0


def record_artifact_feedback_signal(
    conn: sqlite3.Connection,
    *,
    artifact: dict[str, Any],
    score: int | None,
    text: str = "",
) -> None:
    ensure_schema(conn)
    base_weight = signal_weight_for_score(score)
    if base_weight == 0.0 and not str(text or "").strip():
        return

    artifact_id = str(artifact.get("artifact_id") or "").strip()
    keys = _artifact_signal_keys(artifact)
    now = _now_iso()
    for key in keys:
        conn.execute(
            """
            INSERT INTO proactive_preference_signals (
                artifact_id, signal_key, signal_type, weight, score, text,
                created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                key,
                "explicit_feedback",
                base_weight,
                score,
                str(text or "").strip(),
                now,
                _json_dumps({"artifact_type": artifact.get("artifact_type"), "source_kind": artifact.get("source_kind")}),
            ),
        )
    conn.commit()


def score_artifact_for_review(conn: sqlite3.Connection, artifact: dict[str, Any]) -> float:
    ensure_schema(conn)
    keys = _artifact_signal_keys(artifact)
    if not keys:
        return float(artifact.get("priority") or 0)
    placeholders = ",".join("?" for _ in keys)
    rows = conn.execute(
        f"""
        SELECT signal_key, AVG(weight) AS avg_weight, COUNT(*) AS signal_count
        FROM proactive_preference_signals
        WHERE signal_key IN ({placeholders})
        GROUP BY signal_key
        """,
        tuple(keys),
    ).fetchall()
    preference_bonus = 0.0
    for row in rows:
        signal_count = max(1, int(row["signal_count"] or 0))
        preference_bonus += float(row["avg_weight"] or 0.0) * min(2.0, 0.5 + signal_count / 4)
    return float(artifact.get("priority") or 0) + preference_bonus


def _artifact_signal_keys(artifact: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    artifact_type = str(artifact.get("artifact_type") or "").strip().lower()
    source_kind = str(artifact.get("source_kind") or "").strip().lower()
    if artifact_type:
        keys.append(f"type:{artifact_type}")
    if source_kind:
        keys.append(f"source:{source_kind}")
    for tag in artifact.get("topic_tags") or []:
        clean = str(tag or "").strip().lower()
        if clean:
            keys.append(f"topic:{clean}")
    return sorted(set(keys))

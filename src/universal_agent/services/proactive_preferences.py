"""Preference scoring for proactive artifact surfacing.

Phase 1 keeps this deliberately small and SQLite-backed. The model learns from
explicit feedback and is used to rank surfacing candidates, not to suppress
future artifact generation.
"""

from __future__ import annotations

import json
import sqlite3
import math
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS proactive_preference_model (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            model_json TEXT NOT NULL,
            last_updated TEXT NOT NULL
        )
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
    rebuild_preference_snapshot(conn)


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


def rebuild_preference_snapshot(conn: sqlite3.Connection, *, half_life_days: float = 14.0) -> dict[str, Any]:
    ensure_schema(conn)
    rows = conn.execute(
        """
        SELECT signal_key, signal_type, weight, score, created_at
        FROM proactive_preference_signals
        ORDER BY created_at DESC
        """
    ).fetchall()
    now = datetime.now(timezone.utc)
    topic_preferences: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row["signal_key"] or "").strip()
        if not key:
            continue
        age_days = _age_days(str(row["created_at"] or ""), now=now)
        decayed_weight = float(row["weight"] or 0.0) * _decay_multiplier(age_days, half_life_days)
        record = topic_preferences.setdefault(
            key,
            {
                "weight_sum": 0.0,
                "signal_count": 0,
                "positive_count": 0,
                "negative_count": 0,
                "last_signal_at": str(row["created_at"] or ""),
            },
        )
        record["weight_sum"] += decayed_weight
        record["signal_count"] += 1
        if decayed_weight > 0:
            record["positive_count"] += 1
        elif decayed_weight < 0:
            record["negative_count"] += 1
    normalized = {
        key: {
            "weight": max(-1.0, min(1.0, round(value["weight_sum"], 4))),
            "signal_count": value["signal_count"],
            "positive_count": value["positive_count"],
            "negative_count": value["negative_count"],
            "last_signal_at": value["last_signal_at"],
        }
        for key, value in topic_preferences.items()
    }
    model = {
        "topic_preferences": normalized,
        "meta": {
            "last_updated": _now_iso(),
            "half_life_days": half_life_days,
            "total_signals_processed": len(rows),
            "model_version": 1,
        },
    }
    conn.execute(
        """
        INSERT INTO proactive_preference_model (id, model_json, last_updated)
        VALUES (1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            model_json=excluded.model_json,
            last_updated=excluded.last_updated
        """,
        (_json_dumps(model), model["meta"]["last_updated"]),
    )
    conn.commit()
    return model


def get_preference_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_schema(conn)
    row = conn.execute("SELECT model_json FROM proactive_preference_model WHERE id = 1").fetchone()
    if row is None:
        return rebuild_preference_snapshot(conn)
    try:
        parsed = json.loads(str(row["model_json"] or "{}"))
    except Exception:
        return rebuild_preference_snapshot(conn)
    return parsed if isinstance(parsed, dict) else rebuild_preference_snapshot(conn)


def get_delegation_context(
    conn: sqlite3.Connection,
    *,
    task_type: str = "",
    topic_tags: list[str] | None = None,
) -> str:
    model = get_preference_snapshot(conn)
    preferences = model.get("topic_preferences") if isinstance(model.get("topic_preferences"), dict) else {}
    keys = _context_keys(task_type=task_type, topic_tags=topic_tags or [])
    matches = [(key, preferences[key]) for key in keys if key in preferences]
    if not matches:
        ranked = sorted(
            preferences.items(),
            key=lambda item: abs(float((item[1] or {}).get("weight") or 0.0)),
            reverse=True,
        )[:3]
        matches = ranked
    if not matches:
        return ""
    lines = ["Kevin's preference context for this proactive work:"]
    for key, value in matches[:5]:
        weight = float((value or {}).get("weight") or 0.0)
        if abs(weight) < 0.05:
            continue
        direction = "positive" if weight > 0 else "negative"
        lines.append(f"- {key}: {direction} signal weight {weight:.2f} from {value.get('signal_count', 0)} explicit signal(s).")
    return "\n".join(lines) if len(lines) > 1 else ""


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


def _context_keys(*, task_type: str, topic_tags: list[str]) -> list[str]:
    keys = []
    clean_task_type = str(task_type or "").strip().lower()
    if clean_task_type:
        keys.append(f"type:{clean_task_type}")
    for tag in topic_tags:
        clean = str(tag or "").strip().lower()
        if clean:
            keys.append(f"topic:{clean}")
    return sorted(set(keys))


def _age_days(raw: str, *, now: datetime) -> float:
    try:
        parsed = datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
    except Exception:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds() / 86400)


def _decay_multiplier(age_days: float, half_life_days: float) -> float:
    half_life = max(1.0, float(half_life_days or 14.0))
    return math.pow(0.5, max(0.0, age_days) / half_life)

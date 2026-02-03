from __future__ import annotations

import asyncio
import json
import math
import os
import sqlite3
from dataclasses import dataclass
from typing import Iterable


VECTOR_DIM = 128


@dataclass
class VectorEntry:
    entry_id: str
    content_hash: str
    timestamp: str
    summary: str
    preview: str
    vector: list[float]


def _ensure_db(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS embeddings (
                entry_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                timestamp TEXT,
                summary TEXT,
                preview TEXT,
                vector_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_embeddings_hash ON embeddings(content_hash)"
        )


def _tokenize(text: str) -> list[str]:
    return [t for t in text.lower().split() if t]


def _hash_embed(tokens: Iterable[str], dim: int = VECTOR_DIM) -> list[float]:
    # Simple deterministic hashing-based embedding (MVP placeholder).
    vec = [0.0] * dim
    for token in tokens:
        h = hash(token)
        idx = h % dim
        vec[idx] += 1.0
    # L2 normalize
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def upsert_vector(
    db_path: str,
    entry_id: str,
    content_hash: str,
    timestamp: str,
    summary: str,
    preview: str,
    content: str,
) -> None:
    _ensure_db(db_path)
    vector = _hash_embed(_tokenize(content))
    payload = json.dumps(vector)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO embeddings(entry_id, content_hash, timestamp, summary, preview, vector_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
                content_hash=excluded.content_hash,
                timestamp=excluded.timestamp,
                summary=excluded.summary,
                preview=excluded.preview,
                vector_json=excluded.vector_json
            """,
            (entry_id, content_hash, timestamp, summary, preview, payload),
        )


def search_vectors(db_path: str, query: str, limit: int = 5) -> list[dict]:
    if not query or not os.path.exists(db_path):
        return []
    _ensure_db(db_path)
    query_vec = _hash_embed(_tokenize(query))
    results: list[tuple[float, dict]] = []
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            "SELECT entry_id, timestamp, summary, preview, vector_json FROM embeddings"
        )
        for row in cursor.fetchall():
            entry_id, timestamp, summary, preview, vector_json = row
            try:
                vec = json.loads(vector_json)
            except Exception:
                continue
            score = _cosine_similarity(query_vec, vec)
            results.append(
                (
                    score,
                    {
                        "entry_id": entry_id,
                        "timestamp": timestamp,
                        "summary": summary,
                        "preview": preview,
                        "score": score,
                    },
                )
            )
    results.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in results[:limit]]


def schedule_vector_upsert(
    db_path: str,
    entry_id: str,
    content_hash: str,
    timestamp: str,
    summary: str,
    preview: str,
    content: str,
) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        upsert_vector(db_path, entry_id, content_hash, timestamp, summary, preview, content)
        return

    loop.create_task(
        asyncio.to_thread(
            upsert_vector,
            db_path,
            entry_id,
            content_hash,
            timestamp,
            summary,
            preview,
            content,
        )
    )

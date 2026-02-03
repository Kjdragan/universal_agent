from __future__ import annotations

import json
import os
import hashlib
from typing import List, Dict

from .memory_models import MemoryEntry


def _safe_load_json(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _safe_write_json(path: str, payload: list[dict]) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=True)
    os.replace(tmp_path, path)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def load_index(index_path: str) -> list[dict]:
    return _safe_load_json(index_path)


def append_index_entry(
    index_path: str,
    entry: MemoryEntry,
    file_path: str,
    preview: str,
) -> dict:
    os.makedirs(os.path.dirname(index_path), exist_ok=True)
    data = _safe_load_json(index_path)
    content_hash = _content_hash(entry.content)

    for existing in data:
        if existing.get("content_hash") == content_hash and existing.get("file_path") == file_path:
            return existing

    record = {
        "entry_id": entry.entry_id,
        "timestamp": entry.timestamp,
        "source": entry.source,
        "session_id": entry.session_id,
        "tags": list(entry.tags),
        "summary": entry.summary,
        "preview": preview,
        "file_path": file_path,
        "content_hash": content_hash,
    }
    data.append(record)
    _safe_write_json(index_path, data)
    return record


def recent_entries(index_path: str, limit: int = 10) -> list[dict]:
    data = _safe_load_json(index_path)
    data.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    return data[:limit]


def search_entries(index_path: str, query: str, limit: int = 5) -> list[dict]:
    data = _safe_load_json(index_path)
    if not query:
        return []
    terms = [t for t in query.lower().split() if t]
    if not terms:
        return []

    def score(item: dict) -> int:
        haystack = " ".join(
            [
                str(item.get("summary") or ""),
                str(item.get("preview") or ""),
                " ".join(item.get("tags") or []),
            ]
        ).lower()
        return sum(haystack.count(term) for term in terms)

    scored = [(score(item), item) for item in data]
    scored = [pair for pair in scored if pair[0] > 0]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored[:limit]]

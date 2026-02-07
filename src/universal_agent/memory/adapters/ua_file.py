from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from universal_agent.feature_flags import (
    memory_backend,
    memory_index_mode,
    memory_session_delta_bytes,
    memory_session_delta_messages,
)
from universal_agent.memory.adapters.base import MemoryAdapter
from universal_agent.memory.memory_index import (
    _content_hash,
    append_index_entry,
    load_index,
    search_entries,
)
from universal_agent.memory.memory_models import MemoryEntry
from universal_agent.memory.memory_store import append_memory_entry, ensure_memory_scaffold
from universal_agent.memory.memory_vector_index import schedule_vector_upsert, search_vectors


class UAFileMemoryAdapter(MemoryAdapter):
    """File + index adapter backed by workspace memory artifacts."""

    @property
    def name(self) -> str:
        return "ua_file_memory"

    def __init__(self, workspace_dir: str, state: str = "active") -> None:
        super().__init__(workspace_dir=workspace_dir, state=state)
        self.paths = ensure_memory_scaffold(workspace_dir)
        self.session_index_path = os.path.join(self.paths.memory_dir, "session_index.json")
        self.session_vector_db = os.path.join(self.paths.memory_dir, "session_vector_index.sqlite")
        self.session_state_path = os.path.join(self.paths.memory_dir, ".session_sync_state.json")

    def write_entry(
        self,
        entry: MemoryEntry,
        *,
        memory_class: str,
        importance: float = 0.7,
    ) -> bool:
        if memory_class == "session":
            return self._write_session_entry(entry)
        return self._write_long_term_entry(entry)

    def search(
        self,
        query: str,
        *,
        memory_class: str,
        limit: int,
        strategy: str,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        if memory_class == "session":
            return self._search_session(query=query, limit=limit, strategy=strategy)
        return self._search_long_term(query=query, limit=limit, strategy=strategy)

    def sync_session(
        self,
        *,
        session_id: str | None,
        transcript_path: str,
        force: bool = False,
    ) -> dict[str, Any]:
        transcript = Path(transcript_path)
        if not transcript.exists() or not transcript.is_file():
            return {"indexed": False, "reason": "transcript_missing"}

        content = transcript.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        stat = transcript.stat()
        current_size = int(stat.st_size)
        current_lines = len(lines)
        state = self._load_session_sync_state()
        key = str(transcript.resolve())
        previous = state.get(key, {"size": 0, "lines": 0})
        prev_size = int(previous.get("size") or 0)
        prev_lines = int(previous.get("lines") or 0)

        delta_bytes = max(0, current_size - prev_size)
        delta_messages = max(0, current_lines - prev_lines)

        bytes_threshold = memory_session_delta_bytes(default=100_000)
        messages_threshold = memory_session_delta_messages(default=50)

        should_index = force
        if not should_index:
            bytes_hit = bytes_threshold <= 0 and delta_bytes > 0 or delta_bytes >= bytes_threshold
            messages_hit = (
                messages_threshold <= 0 and delta_messages > 0 or delta_messages >= messages_threshold
            )
            should_index = bytes_hit or messages_hit

        if not should_index:
            state[key] = {"size": current_size, "lines": current_lines}
            self._save_session_sync_state(state)
            return {
                "indexed": False,
                "reason": "threshold_not_reached",
                "delta_bytes": delta_bytes,
                "delta_messages": delta_messages,
            }

        if force:
            selected = "\n".join(lines[-300:])
        else:
            selected = "\n".join(lines[prev_lines:])
            if len(selected) > 20_000:
                selected = selected[-20_000:]

        selected = selected.strip()
        if not selected:
            state[key] = {"size": current_size, "lines": current_lines}
            self._save_session_sync_state(state)
            return {"indexed": False, "reason": "empty_delta"}

        session_key = session_id or transcript.stem
        entry = MemoryEntry(
            content=selected,
            source="session_index",
            session_id=session_key,
            tags=["memory_class:session", "session_index", f"session:{session_key}"],
        )
        written = self._write_session_entry(entry)

        state[key] = {"size": current_size, "lines": current_lines}
        self._save_session_sync_state(state)
        return {
            "indexed": written,
            "reason": "indexed" if written else "duplicate",
            "delta_bytes": delta_bytes,
            "delta_messages": delta_messages,
        }

    def _write_long_term_entry(self, entry: MemoryEntry) -> bool:
        content_hash = _content_hash(entry.content)
        records = load_index(self.paths.index_path)
        if any(record.get("content_hash") == content_hash for record in records):
            return False
        append_memory_entry(self.workspace_dir, entry)
        return True

    def _write_session_entry(self, entry: MemoryEntry) -> bool:
        os.makedirs(os.path.join(self.paths.memory_dir, "sessions"), exist_ok=True)
        content = entry.content.strip()
        if not content:
            return False

        date_stamp = entry.timestamp[:10]
        session_label = (entry.session_id or "unknown").replace("/", "_")
        session_path = os.path.join(self.paths.memory_dir, "sessions", f"{session_label}_{date_stamp}.md")
        rel_path = os.path.relpath(session_path, self.workspace_dir)
        content_hash = _content_hash(content)

        existing = load_index(self.session_index_path)
        if any(
            record.get("content_hash") == content_hash and record.get("file_path") == rel_path
            for record in existing
        ):
            return False

        with open(session_path, "a", encoding="utf-8") as handle:
            handle.write(f"## {entry.timestamp} — session\n")
            handle.write(f"- session: {entry.session_id or 'unknown'}\n")
            handle.write(f"- tags: {', '.join(entry.tags) if entry.tags else '(none)'}\n\n")
            handle.write(content)
            handle.write("\n\n")

        preview = (entry.summary or content)[:280]
        append_index_entry(self.session_index_path, entry, rel_path, preview)

        if memory_index_mode() == "vector":
            schedule_vector_upsert(
                self.session_vector_db,
                entry.entry_id,
                content_hash,
                entry.timestamp,
                entry.summary or "",
                preview,
                content,
            )
        return True

    def _search_long_term(self, *, query: str, limit: int, strategy: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        if strategy != "lexical_only":
            results = self._semantic_search_long_term(query=query, limit=limit)
        if strategy == "semantic_first" and results:
            return results[:limit]

        lexical = search_entries(self.paths.index_path, query, limit=limit)
        lexical_results = [self._entry_to_hit(item, source="memory") for item in lexical]
        if strategy == "lexical_only":
            return lexical_results[:limit]
        return self._merge_hits(results, lexical_results, limit=limit)

    def _semantic_search_long_term(self, *, query: str, limit: int) -> list[dict[str, Any]]:
        mode = memory_index_mode()
        if mode != "vector":
            return []

        backend = memory_backend()
        if backend == "sqlite":
            vector_db = os.path.join(self.paths.memory_dir, "vector_index.sqlite")
            rows = search_vectors(vector_db, query, limit=limit)
            return [self._entry_to_hit(item, source="memory") for item in rows]

        try:
            if backend == "lancedb":
                from universal_agent.memory.lancedb_backend import LanceDBMemory

                db = LanceDBMemory(os.path.join(self.paths.memory_dir, "lancedb"))
            else:
                from universal_agent.memory.chromadb_backend import ChromaDBMemory

                db = ChromaDBMemory(os.path.join(self.paths.memory_dir, "chromadb"))
            rows = db.search(query, limit=limit, min_score=0.0)
            return [
                {
                    "source": "memory",
                    "memory_class": "long_term",
                    "timestamp": row.timestamp,
                    "summary": row.text[:280],
                    "preview": row.text[:280],
                    "score": row.score,
                    "path": "",
                    "session_id": row.session_id,
                }
                for row in rows
            ]
        except Exception:
            return []

    def _search_session(self, *, query: str, limit: int, strategy: str) -> list[dict[str, Any]]:
        semantic: list[dict[str, Any]] = []
        if strategy != "lexical_only" and memory_index_mode() == "vector":
            semantic_rows = search_vectors(self.session_vector_db, query, limit=limit)
            semantic = [self._entry_to_hit(item, source="sessions") for item in semantic_rows]

        if strategy == "semantic_first" and semantic:
            return semantic[:limit]

        lexical_rows = search_entries(self.session_index_path, query, limit=limit)
        lexical = [self._entry_to_hit(item, source="sessions") for item in lexical_rows]
        if strategy == "lexical_only":
            return lexical[:limit]
        return self._merge_hits(semantic, lexical, limit=limit)

    @staticmethod
    def _entry_to_hit(item: dict[str, Any], *, source: str) -> dict[str, Any]:
        return {
            "source": source,
            "memory_class": "session" if source == "sessions" else "long_term",
            "timestamp": item.get("timestamp", ""),
            "summary": item.get("summary") or item.get("preview") or "",
            "preview": item.get("preview") or item.get("summary") or "",
            "score": float(item.get("score", 0.0)),
            "path": item.get("file_path", ""),
            "session_id": item.get("session_id"),
            "tags": item.get("tags") or [],
        }

    @staticmethod
    def _merge_hits(
        semantic: list[dict[str, Any]],
        lexical: list[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for row in semantic + lexical:
            key = (row.get("source", ""), row.get("timestamp", ""), row.get("summary", ""))
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
        merged.sort(key=lambda row: float(row.get("score", 0.0)), reverse=True)
        return merged[:limit]

    def _load_session_sync_state(self) -> dict[str, dict[str, int]]:
        if not os.path.exists(self.session_state_path):
            return {}
        try:
            with open(self.session_state_path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, dict):
                return raw
        except Exception:
            pass
        return {}

    def _save_session_sync_state(self, payload: dict[str, dict[str, int]]) -> None:
        tmp_path = f"{self.session_state_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
        os.replace(tmp_path, self.session_state_path)


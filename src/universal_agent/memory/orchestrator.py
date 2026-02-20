from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from universal_agent.feature_flags import (
    memory_backend,
    memory_enabled,
    memory_index_mode,
    memory_retrieval_strategy,
    memory_scope,
    memory_session_delta_bytes,
    memory_session_delta_messages,
    memory_session_enabled,
    memory_session_sources,
)
from universal_agent.memory.memory_index import (
    _content_hash,
    append_index_entry,
    load_index,
    search_entries,
)
from universal_agent.memory.memory_models import MemoryEntry
from universal_agent.memory.memory_store import append_memory_entry, ensure_memory_scaffold
from universal_agent.memory.memory_vector_index import schedule_vector_upsert, search_vectors

_BROKERS: dict[str, "MemoryOrchestrator"] = {}
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _resolve_workspace_dir(workspace_dir: str | None) -> str:
    if workspace_dir:
        return str(Path(workspace_dir).resolve())
    candidate = (
        os.getenv("AGENT_WORKSPACE_DIR")
        or os.getenv("CURRENT_SESSION_WORKSPACE")
        or os.getenv("UA_WORKSPACE_DIR")
        or os.getcwd()
    )
    return str(Path(candidate).resolve())


def _extract_transcript_tail(
    transcript_path: str,
    *,
    max_chars: int = 4000,
    max_lines: int = 120,
) -> str:
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
        tail_lines = lines[-max_lines:] if max_lines > 0 else lines
        content = "".join(tail_lines).strip()
        if max_chars > 0 and len(content) > max_chars:
            content = content[-max_chars:]
        return content.strip()
    except Exception:
        return ""


def _safe_read_json(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _safe_write_json(path: str, payload: dict[str, Any]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)
    os.replace(tmp, path)


class MemoryOrchestrator:
    """Canonical memory service (single pipeline, no adapter multiplex)."""

    def __init__(self, workspace_dir: str):
        self.workspace_dir = _resolve_workspace_dir(workspace_dir)
        self.paths = ensure_memory_scaffold(self.workspace_dir)
        self.session_index_path = os.path.join(self.paths.memory_dir, "session_index.json")
        self.session_vector_db = os.path.join(self.paths.memory_dir, "session_vector_index.sqlite")
        self.session_state_path = os.path.join(self.paths.memory_dir, ".session_sync_state.json")

    def write(
        self,
        *,
        content: str,
        source: str,
        session_id: str | None,
        tags: list[str] | None = None,
        summary: str | None = None,
        memory_class: str = "long_term",
        importance: float = 0.7,
    ) -> MemoryEntry | None:
        if not memory_enabled(default=True):
            return None
        body = (content or "").strip()
        if not body:
            return None

        entry = MemoryEntry(
            content=body,
            source=source,
            session_id=session_id,
            tags=list(tags or []),
            summary=summary,
        )

        if memory_class == "session":
            wrote = self._write_session_entry(entry)
        else:
            wrote = self._write_long_term_entry(entry)
        return entry if wrote else None

    def flush_pre_compact(
        self,
        *,
        session_id: str | None,
        transcript_path: str | None,
        trigger: str,
        max_chars: int = 4000,
    ) -> MemoryEntry | None:
        content = _extract_transcript_tail(transcript_path or "", max_chars=max_chars)
        if not content:
            return None
        return self.write(
            content=content,
            source="pre_compact",
            session_id=session_id,
            tags=["pre_compact", f"trigger:{trigger}"],
            memory_class="long_term",
            importance=0.75,
        )

    def read_file(self, *, rel_path: str, from_line: int = 1, lines: int = 120) -> dict[str, Any]:
        path_value = (rel_path or "").strip()
        if not path_value:
            return {"path": "", "text": "", "error": "path is required"}

        root = Path(self.workspace_dir).resolve()
        target = (root / path_value).resolve()
        if not str(target).startswith(str(root)):
            return {"path": path_value, "text": "", "error": "path escapes memory root"}

        rel = str(target.relative_to(root))
        if rel != "MEMORY.md" and not rel.startswith("memory" + os.sep):
            return {"path": path_value, "text": "", "error": "path must be MEMORY.md or memory/*"}
        if not target.exists() or not target.is_file():
            return {"path": path_value, "text": "", "error": "file not found"}

        start = max(1, int(from_line or 1))
        count = max(1, int(lines or 120))
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as handle:
                all_lines = handle.readlines()
            chunk = "".join(all_lines[start - 1 : start - 1 + count])
        except Exception as exc:
            return {"path": rel, "text": "", "error": str(exc)}
        return {"path": rel, "text": chunk, "from": start, "lines": count}

    def search(
        self,
        *,
        query: str,
        limit: int = 5,
        sources: list[str] | None = None,
        direct_context: bool = True,
    ) -> list[dict[str, Any]]:
        if not memory_enabled(default=True):
            return []
        text = (query or "").strip()
        if not text:
            return []
        if memory_scope(default="direct_only") == "direct_only" and not direct_context:
            return []

        max_results = max(1, int(limit))
        strategy = memory_retrieval_strategy(default="semantic_first")
        source_list = sources or memory_session_sources(default=("memory", "sessions"))
        source_list = [item for item in source_list if item in {"memory", "sessions"}]
        if not memory_session_enabled(default=True):
            source_list = [item for item in source_list if item != "sessions"]

        hits: list[dict[str, Any]] = []
        if "memory" in source_list:
            hits.extend(self._search_long_term(query=text, limit=max_results, strategy=strategy))
        if "sessions" in source_list:
            hits.extend(self._search_session(query=text, limit=max_results, strategy=strategy))
        return self._merge_hits(hits, limit=max_results)

    def sync_session(
        self,
        *,
        session_id: str | None,
        transcript_path: str,
        force: bool = False,
    ) -> dict[str, Any]:
        if not memory_enabled(default=True):
            return {"indexed": False, "reason": "memory_disabled"}
        if not memory_session_enabled(default=True):
            return {"indexed": False, "reason": "session_memory_disabled"}

        transcript = Path(transcript_path)
        if not transcript.exists() or not transcript.is_file():
            return {"indexed": False, "reason": "transcript_missing"}

        content = transcript.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        stat = transcript.stat()
        current_size = int(stat.st_size)
        current_lines = len(lines)

        state = _safe_read_json(self.session_state_path)
        key = str(transcript.resolve())
        previous = state.get(key, {"size": 0, "lines": 0})
        prev_size = int(previous.get("size") or 0)
        prev_lines = int(previous.get("lines") or 0)
        delta_bytes = max(0, current_size - prev_size)
        delta_messages = max(0, current_lines - prev_lines)

        bytes_threshold = memory_session_delta_bytes(default=100_000)
        msg_threshold = memory_session_delta_messages(default=50)
        should_index = bool(force)
        if not should_index:
            bytes_hit = (bytes_threshold <= 0 and delta_bytes > 0) or delta_bytes >= bytes_threshold
            msg_hit = (msg_threshold <= 0 and delta_messages > 0) or delta_messages >= msg_threshold
            should_index = bytes_hit or msg_hit
        if not should_index:
            state[key] = {"size": current_size, "lines": current_lines}
            _safe_write_json(self.session_state_path, state)
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
            _safe_write_json(self.session_state_path, state)
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
        _safe_write_json(self.session_state_path, state)
        return {
            "indexed": written,
            "reason": "indexed" if written else "duplicate",
            "delta_bytes": delta_bytes,
            "delta_messages": delta_messages,
            "force": force,
        }

    def capture_session_rollover(
        self,
        *,
        session_id: str,
        trigger: str,
        transcript_path: str | None = None,
        run_log_path: str | None = None,
        summary: str | None = None,
        max_lines: int = 200,
        max_chars: int = 12_000,
    ) -> dict[str, Any]:
        """
        Capture a durable memory slice at session transition boundaries.

        This emulates OpenClaw's session-memory continuity behavior by creating
        a session-specific markdown slice in `memory/sessions/` and indexing it.
        """
        sid = (session_id or "").strip()
        if not sid:
            return {"captured": False, "reason": "missing_session_id"}

        transcript_tail = _extract_transcript_tail(
            transcript_path or "",
            max_chars=max_chars,
            max_lines=max_lines,
        )
        if transcript_tail:
            source = "transcript"
            excerpt = transcript_tail
        else:
            source = "run_log"
            excerpt = _extract_transcript_tail(
                run_log_path or "",
                max_chars=max_chars,
                max_lines=max_lines,
            )
        if not excerpt:
            return {"captured": False, "reason": "no_content"}

        note_summary = (summary or "").strip() or f"Session rollover capture ({trigger})"
        slug_source = summary or (excerpt.splitlines()[0] if excerpt.splitlines() else sid)
        slug = self._slugify(slug_source)
        date_part = (self._now_date() or "unknown-date")
        session_key = sid.replace("/", "_")
        sessions_dir = os.path.join(self.paths.memory_dir, "sessions")
        os.makedirs(sessions_dir, exist_ok=True)
        base_path = Path(sessions_dir) / f"{session_key}_{date_part}_{slug}.md"
        session_path = str(base_path)
        if base_path.exists():
            suffix = 2
            while True:
                candidate = base_path.with_name(f"{base_path.stem}_{suffix}.md")
                if not candidate.exists():
                    session_path = str(candidate)
                    break
                suffix += 1
        rel_path = os.path.relpath(session_path, self.workspace_dir)

        content = (
            f"# Session Capture\n\n"
            f"- Session: `{sid}`\n"
            f"- Trigger: `{trigger}`\n"
            f"- Source: `{source}`\n\n"
            f"## Summary\n\n"
            f"{note_summary}\n\n"
            f"## Recent Context\n\n"
            f"{excerpt.strip()}\n"
        ).strip()
        content_hash = _content_hash(content)
        existing = load_index(self.session_index_path)
        if any(record.get("content_hash") == content_hash for record in existing):
            return {"captured": False, "reason": "duplicate", "path": rel_path}

        with open(session_path, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.write("\n")

        entry = MemoryEntry(
            content=content,
            source=f"session_transition:{trigger}",
            session_id=sid,
            tags=[
                "memory_class:session",
                "session_transition",
                f"trigger:{trigger}",
                f"session:{sid}",
            ],
            summary=note_summary,
        )
        preview = note_summary[:280] if note_summary else content[:280]
        append_index_entry(self.session_index_path, entry, rel_path, preview)
        if memory_index_mode(default="vector") == "vector":
            schedule_vector_upsert(
                self.session_vector_db,
                entry.entry_id,
                content_hash,
                entry.timestamp,
                entry.summary or "",
                preview,
                content,
            )
        return {
            "captured": True,
            "path": rel_path,
            "source": source,
            "trigger": trigger,
        }

    def _write_long_term_entry(self, entry: MemoryEntry) -> bool:
        content_hash = _content_hash(entry.content)
        records = load_index(self.paths.index_path)
        if any(record.get("content_hash") == content_hash for record in records):
            return False
        append_memory_entry(self.workspace_dir, entry)
        return True

    @staticmethod
    def _slugify(value: str, fallback: str = "session-capture") -> str:
        text = (value or "").strip().lower()
        if not text:
            return fallback
        slug = _NON_ALNUM_RE.sub("-", text).strip("-")
        if not slug:
            return fallback
        return slug[:64]

    @staticmethod
    def _now_date() -> str:
        try:
            from datetime import datetime, timezone

            return datetime.now(timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            return "unknown-date"

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
        if memory_index_mode(default="vector") == "vector":
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
            results = self._semantic_search(
                query=query,
                limit=limit,
                vector_db=os.path.join(self.paths.memory_dir, "vector_index.sqlite"),
                source="memory",
                memory_class="long_term",
            )
        if strategy == "semantic_first" and results:
            return results[:limit]
        lexical = search_entries(self.paths.index_path, query, limit=limit)
        lexical_rows = [self._entry_to_hit(item, source="memory", memory_class="long_term", query=query) for item in lexical]
        if strategy == "lexical_only":
            return lexical_rows[:limit]
        return self._merge_hits(results + lexical_rows, limit=limit)

    def _search_session(self, *, query: str, limit: int, strategy: str) -> list[dict[str, Any]]:
        semantic: list[dict[str, Any]] = []
        if strategy != "lexical_only":
            semantic = self._semantic_search(
                query=query,
                limit=limit,
                vector_db=self.session_vector_db,
                source="sessions",
                memory_class="session",
            )
        if strategy == "semantic_first" and semantic:
            return semantic[:limit]
        lexical = search_entries(self.session_index_path, query, limit=limit)
        lexical_rows = [self._entry_to_hit(item, source="sessions", memory_class="session", query=query) for item in lexical]
        if strategy == "lexical_only":
            return lexical_rows[:limit]
        return self._merge_hits(semantic + lexical_rows, limit=limit)

    def _semantic_search(
        self,
        *,
        query: str,
        limit: int,
        vector_db: str,
        source: str,
        memory_class: str,
    ) -> list[dict[str, Any]]:
        if memory_index_mode(default="vector") != "vector":
            return []
        rows = search_vectors(vector_db, query, limit=limit)
        hits: list[dict[str, Any]] = []
        for row in rows:
            hits.append(
                self._entry_to_hit(row, source=source, memory_class=memory_class, query=query, semantic=True)
            )
        if hits:
            return hits

        # Optional higher-fidelity semantic store path.
        backend = memory_backend(default="chromadb")
        try:
            if backend == "lancedb":
                from universal_agent.memory.lancedb_backend import LanceDBMemory

                db = LanceDBMemory(os.path.join(self.paths.memory_dir, "lancedb"))
            else:
                from universal_agent.memory.chromadb_backend import ChromaDBMemory

                db = ChromaDBMemory(os.path.join(self.paths.memory_dir, "chromadb"))
            rows = db.search(query, limit=limit, min_score=0.0)
        except Exception:
            return []

        for row in rows:
            hits.append(
                {
                    "source": source,
                    "memory_class": memory_class,
                    "timestamp": row.timestamp,
                    "summary": row.text[:280],
                    "preview": row.text[:280],
                    "snippet": row.text[:700],
                    "score": row.score,
                    "path": "",
                    "start_line": 1,
                    "end_line": 1,
                    "provider": backend,
                    "model": "",
                    "fallback": False,
                    "session_id": row.session_id,
                }
            )
        return hits

    def _entry_to_hit(
        self,
        item: dict[str, Any],
        *,
        source: str,
        memory_class: str,
        query: str,
        semantic: bool = False,
    ) -> dict[str, Any]:
        rel_path = str(item.get("file_path", "") or "")
        start_line, end_line, snippet = self._resolve_snippet(rel_path=rel_path, query=query)
        fallback = not semantic
        provider = "lexical"
        model = "fts"
        if semantic:
            provider = memory_backend(default="chromadb")
            model = "vector"
        return {
            "source": source,
            "memory_class": memory_class,
            "timestamp": item.get("timestamp", ""),
            "summary": item.get("summary") or item.get("preview") or "",
            "preview": item.get("preview") or item.get("summary") or "",
            "snippet": snippet or (item.get("preview") or item.get("summary") or ""),
            "score": float(item.get("score", 0.0)),
            "path": rel_path,
            "start_line": start_line,
            "end_line": end_line,
            "provider": provider,
            "model": model,
            "fallback": fallback,
            "session_id": item.get("session_id"),
            "tags": item.get("tags") or [],
        }

    def _resolve_snippet(self, *, rel_path: str, query: str) -> tuple[int, int, str]:
        if not rel_path:
            return 1, 1, ""
        file_path = os.path.join(self.workspace_dir, rel_path)
        if not os.path.exists(file_path):
            return 1, 1, ""
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()
        except Exception:
            return 1, 1, ""

        terms = [term for term in (query or "").lower().split() if term]
        best_idx = 0
        best_score = -1
        for idx, line in enumerate(lines):
            score = 0
            lower = line.lower()
            for term in terms:
                if term in lower:
                    score += 1
            if score > best_score:
                best_score = score
                best_idx = idx

        start = max(0, best_idx - 2)
        end = min(len(lines), best_idx + 3)
        snippet = "".join(lines[start:end]).strip()
        if len(snippet) > 700:
            snippet = snippet[:700]
        return start + 1, max(start + 1, end), snippet

    @staticmethod
    def format_search_results(hits: list[dict[str, Any]]) -> str:
        if not hits:
            return "No memory matches found."
        lines = ["# Memory Search Results", ""]
        for item in hits:
            source = item.get("source") or "memory"
            score = item.get("score")
            path = item.get("path") or "(index)"
            start_line = int(item.get("start_line") or 1)
            end_line = int(item.get("end_line") or start_line)
            snippet = (item.get("snippet") or item.get("summary") or "").strip()
            if len(snippet) > 280:
                snippet = snippet[:280] + "..."
            score_text = ""
            if isinstance(score, (float, int)):
                score_text = f" score={float(score):.3f}"
            lines.append(
                f"- [{source}] {path}#L{start_line}-L{end_line}{score_text}"
                f" provider={item.get('provider','')} model={item.get('model','')} fallback={bool(item.get('fallback', False))}"
            )
            if snippet:
                lines.append(f"  {snippet}")
        return "\n".join(lines)

    @staticmethod
    def _merge_hits(hits: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str, int, int]] = set()
        for row in hits:
            key = (
                str(row.get("source", "")),
                str(row.get("path", "")),
                str(row.get("timestamp", "")),
                str(row.get("snippet") or row.get("summary") or ""),
                int(row.get("start_line") or 1),
                int(row.get("end_line") or 1),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
        merged.sort(
            key=lambda row: (
                float(row.get("score", 0.0)),
                str(row.get("timestamp", "")),
            ),
            reverse=True,
        )
        return merged[:limit]


def get_memory_orchestrator(workspace_dir: str | None = None) -> MemoryOrchestrator:
    resolved = _resolve_workspace_dir(workspace_dir)
    broker = _BROKERS.get(resolved)
    if broker is None:
        broker = MemoryOrchestrator(workspace_dir=resolved)
        _BROKERS[resolved] = broker
    return broker

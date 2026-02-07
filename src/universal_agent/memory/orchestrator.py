from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from universal_agent.feature_flags import (
    memory_adapter_state,
    memory_profile_mode,
    memory_retrieval_strategy,
    memory_session_enabled,
    memory_session_sources,
    memory_tag_dev_writes,
    memory_write_policy_min_importance,
)
from universal_agent.memory.adapters.letta import LettaAdapter
from universal_agent.memory.adapters.memory_system import MemorySystemAdapter
from universal_agent.memory.adapters.ua_file import UAFileMemoryAdapter
from universal_agent.memory.memory_models import MemoryEntry

_BROKERS: dict[str, "MemoryOrchestrator"] = {}


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


class MemoryOrchestrator:
    """Unified broker for memory writes/search/sync across adapters."""

    def __init__(self, workspace_dir: str):
        self.workspace_dir = _resolve_workspace_dir(workspace_dir)
        self._adapters: dict[str, Any] = {}
        self._register_adapters()

    def _register_adapters(self) -> None:
        ua_state = memory_adapter_state("ua_file_memory", default="active")
        self._adapters["ua_file_memory"] = UAFileMemoryAdapter(self.workspace_dir, state=ua_state)

        memory_system_state = memory_adapter_state("memory_system", default="shadow")
        if memory_system_state != "off":
            try:
                self._adapters["memory_system"] = MemorySystemAdapter(
                    self.workspace_dir,
                    state=memory_system_state,
                )
            except Exception:
                pass

        letta_state = memory_adapter_state("letta", default="off")
        if letta_state != "off":
            try:
                self._adapters["letta"] = LettaAdapter(self.workspace_dir, state=letta_state)
            except Exception:
                pass

    def _iter_adapters(self, *, include_shadow: bool) -> list[Any]:
        allowed = {"active"}
        if include_shadow:
            allowed.add("shadow")
        return [adapter for adapter in self._adapters.values() if getattr(adapter, "state", "off") in allowed]

    def _decorate_tags(self, tags: list[str]) -> list[str]:
        profile = memory_profile_mode(default="dev_standard")
        output = list(tags)
        if memory_tag_dev_writes(default=True) and profile != "prod":
            output.append(f"profile:{profile}")
        return output

    def _allow_write(self, *, memory_class: str, importance: float) -> bool:
        profile = memory_profile_mode(default="dev_standard")
        if profile == "dev_no_persist":
            return False
        if memory_class == "long_term" and profile in {"dev_standard", "dev_memory_test"}:
            threshold = memory_write_policy_min_importance(default=0.6)
            return importance >= threshold
        return True

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
        body = (content or "").strip()
        if not body:
            return None
        if not self._allow_write(memory_class=memory_class, importance=importance):
            return None

        entry = MemoryEntry(
            content=body,
            source=source,
            session_id=session_id,
            tags=self._decorate_tags(tags or []),
            summary=summary,
        )

        active_success = False
        for adapter in self._iter_adapters(include_shadow=True):
            try:
                wrote = adapter.write_entry(
                    entry,
                    memory_class=memory_class,
                    importance=importance,
                )
                if wrote and getattr(adapter, "state", "off") == "active":
                    active_success = True
            except Exception:
                continue
        return entry if active_success else None

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

    def search(
        self,
        *,
        query: str,
        limit: int = 5,
        sources: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        text = (query or "").strip()
        if not text:
            return []
        max_results = max(1, int(limit))
        strategy = memory_retrieval_strategy(default="semantic_first")
        source_list = sources or memory_session_sources(default=("memory", "sessions"))
        if not memory_session_enabled(default=True):
            source_list = [item for item in source_list if item != "sessions"]

        adapters = self._iter_adapters(include_shadow=False)
        if not adapters:
            adapters = self._iter_adapters(include_shadow=True)

        hits: list[dict[str, Any]] = []
        for adapter in adapters:
            if "memory" in source_list:
                try:
                    hits.extend(
                        adapter.search(
                            text,
                            memory_class="long_term",
                            limit=max_results,
                            strategy=strategy,
                        )
                    )
                except Exception:
                    pass
            if "sessions" in source_list and memory_session_enabled(default=True):
                try:
                    hits.extend(
                        adapter.search(
                            text,
                            memory_class="session",
                            limit=max_results,
                            strategy=strategy,
                        )
                    )
                except Exception:
                    pass
        return self._merge_hits(hits, limit=max_results)

    def sync_session(
        self,
        *,
        session_id: str | None,
        transcript_path: str,
        force: bool = False,
    ) -> dict[str, Any]:
        if not memory_session_enabled(default=True):
            return {"indexed": False, "reason": "session_memory_disabled", "details": []}

        details: list[dict[str, Any]] = []
        active_indexed = False
        for adapter in self._iter_adapters(include_shadow=True):
            try:
                result = adapter.sync_session(
                    session_id=session_id,
                    transcript_path=transcript_path,
                    force=force,
                )
            except Exception:
                result = {"indexed": False, "reason": "sync_failed"}
            state = getattr(adapter, "state", "off")
            details.append({"adapter": adapter.name, "state": state, **result})
            if state == "active" and bool(result.get("indexed")):
                active_indexed = True

        return {
            "indexed": active_indexed,
            "reason": "indexed" if active_indexed else "no_active_write",
            "details": details,
            "force": force,
        }

    @staticmethod
    def format_search_results(hits: list[dict[str, Any]]) -> str:
        if not hits:
            return "No memory matches found."
        lines = ["# Memory Search Results", ""]
        for item in hits:
            source = item.get("source") or "memory"
            ts = item.get("timestamp", "")
            summary = item.get("summary") or item.get("preview") or ""
            score = item.get("score")
            label = f"[{source}] "
            if isinstance(score, (float, int)):
                lines.append(f"- {label}{ts}: {summary} (score: {float(score):.3f})")
            else:
                lines.append(f"- {label}{ts}: {summary}")
        return "\n".join(lines)

    @staticmethod
    def _merge_hits(hits: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for row in hits:
            key = (
                str(row.get("source", "")),
                str(row.get("timestamp", "")),
                str(row.get("summary") or row.get("preview") or ""),
                str(row.get("path", "")),
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


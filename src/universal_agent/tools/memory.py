"""Canonical memory tools (hard-cut contract)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from universal_agent.feature_flags import memory_session_sources
from universal_agent.memory.orchestrator import get_memory_orchestrator


def _workspace_root() -> str:
    workspace_dir = os.environ.get("AGENT_WORKSPACE_DIR", os.getcwd())
    return str(Path(workspace_dir).resolve())


def memory_get(path: str, from_line: int = 1, lines: int = 120) -> str:
    broker = get_memory_orchestrator(workspace_dir=_workspace_root())
    result = broker.read_file(rel_path=path, from_line=from_line, lines=lines)
    error = result.get("error")
    if error:
        return f"Memory read error: {error}"
    return str(result.get("text") or "")


@tool(
    name="memory_get",
    description=(
        "Safe snippet read from MEMORY.md or memory/* with optional from/lines. "
        "Use after memory_search to pull only the lines needed."
    ),
    input_schema={"path": str, "from": int, "lines": int},
)
async def memory_get_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    content = memory_get(
        path=args.get("path", "MEMORY.md"),
        from_line=args.get("from", 1),
        lines=args.get("lines", 120),
    )
    return {"content": [{"type": "text", "text": content}]}


def memory_search(query: str, limit: int = 5) -> str:
    broker = get_memory_orchestrator(workspace_dir=_workspace_root())
    hits = broker.search(
        query=query,
        limit=limit,
        sources=memory_session_sources(default=("memory", "sessions")),
    )
    return broker.format_search_results(hits)


@tool(
    name="memory_search",
    description=(
        "Semantic-first memory retrieval over MEMORY.md, memory/*.md, and indexed session transcript memory. "
        "Returns snippets with path, line ranges, score, provider/model, and fallback marker."
    ),
    input_schema={"query": str, "limit": int},
)
async def memory_search_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    query = args.get("query", "")
    limit = args.get("limit", 5)
    content = memory_search(query=query, limit=limit)
    return {"content": [{"type": "text", "text": content}]}

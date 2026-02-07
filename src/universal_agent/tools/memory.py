"""
Memory tool implementation for safe file retrieval.
"""

import os
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from universal_agent.feature_flags import (
    memory_index_mode,
    memory_orchestrator_enabled,
    memory_session_sources,
)
from universal_agent.memory.memory_index import search_entries
from universal_agent.memory.memory_vector_index import search_vectors

def ua_memory_get(path: str, line_start: int = 1, num_lines: int = 100) -> str:
    """
    Read content from the agent's memory files.
    
    This tool allows safe access to 'MEMORY.md' and files within the 'memory/'
    subdirectory of the current workspace. It prevents access to any other
    files on the system.
    
    Args:
        path: Relative path to the file (e.g., 'MEMORY.md', 'memory/project_notes.md').
        line_start: Line number to start reading from (1-based index).
        num_lines: Number of lines to read.
        
    Returns:
        The content of the file or an error message.
    """
    # 1. Resolve workspace root
    # In the tool execution context (unlike the agent setup), we might not have 'self.workspace_dir'
    # readily available if this is a standalone function. However, the agent execution environment
    # typically sets current working directory to the workspace or provides it via env.
    # We will assume CWD is the workspace root or relies on an env var 'AGENT_WORKSPACE_DIR'.
    # Fallback to CWD if env is not set.
    
    workspace_dir = os.environ.get("AGENT_WORKSPACE_DIR", os.getcwd())
    root = Path(workspace_dir).resolve()
    
    # 2. Resolve target path
    # Prevent absolute paths that escape root immediately if possible, 
    # but .resolve() handles '..' sanitization best.
    try:
        target_path = (root / path).resolve()
    except Exception as e:
         return f"Error resolving path: {e}"

    # 3. Security Check: Must be within root and follow rules
    # Rule 1: Must be inside the workspace root
    if not str(target_path).startswith(str(root)):
        return f"Access Denied: Path '{path}' is outside the active workspace."
    
    # Rule 2: Allowed paths are ONLY 'MEMORY.md' (in valid root) OR inside 'memory/' dir
    rel_path = target_path.relative_to(root)
    is_memory_md = str(rel_path) == "MEMORY.md"
    is_in_memory_dir = str(rel_path).startswith("memory" + os.sep)
    
    if not (is_memory_md or is_in_memory_dir):
        return f"Access Denied: You may only read 'MEMORY.md' or files in the 'memory/' directory. Requested: {path}"
        
    # 4. Read File
    if not target_path.exists():
         return f"File not found: {path}"
         
    if not target_path.is_file():
         return f"Not a file: {path}"

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        total_lines = len(lines)
        if line_start < 1:
            line_start = 1
            
        start_idx = line_start - 1
        end_idx = start_idx + num_lines
        
        selected_lines = lines[start_idx:end_idx]
        content = "".join(selected_lines)
        
        return content
    except Exception as e:
        return f"Error reading file: {e}"


@tool(
    name="ua_memory_get",
    description=(
        "Read content from the agent's memory files. Only allows 'MEMORY.md' "
        "or files under the 'memory/' directory in the current workspace."
    ),
    input_schema={
        "path": str,
        "line_start": int,
        "num_lines": int,
    },
)
async def ua_memory_get_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    """Claude SDK tool wrapper for ua_memory_get."""
    content = ua_memory_get(
        path=args.get("path", "MEMORY.md"),
        line_start=args.get("line_start", 1),
        num_lines=args.get("num_lines", 100),
    )
    return {"content": [{"type": "text", "text": content}]}


def ua_memory_search(query: str, limit: int = 5) -> str:
    """Search memory index (vector or json) for matching entries."""
    workspace_dir = os.environ.get("AGENT_WORKSPACE_DIR", os.getcwd())
    root = Path(workspace_dir).resolve()
    memory_dir = root / "memory"
    max_results = max(1, int(limit))

    if memory_orchestrator_enabled(default=False):
        try:
            from universal_agent.memory.orchestrator import get_memory_orchestrator

            broker = get_memory_orchestrator(workspace_dir=str(root))
            results = broker.search(
                query=query,
                limit=max_results,
                sources=memory_session_sources(default=("memory", "sessions")),
            )
            return broker.format_search_results(results)
        except Exception:
            # Fall through to legacy path.
            pass

    index_mode = memory_index_mode()

    if index_mode == "vector":
        db_path = memory_dir / "vector_index.sqlite"
        results = search_vectors(str(db_path), query, limit=max_results)
    else:
        index_path = memory_dir / "index.json"
        results = search_entries(str(index_path), query, limit=max_results)

    if not results:
        return "No memory matches found."

    lines = ["# Memory Search Results", ""]
    for item in results:
        ts = item.get("timestamp", "")
        summary = item.get("summary") or item.get("preview") or ""
        score = item.get("score")
        if score is not None:
            lines.append(f"- {ts}: {summary} (score: {score:.3f})")
        else:
            lines.append(f"- {ts}: {summary}")
    return "\n".join(lines)


@tool(
    name="ua_memory_search",
    description="Search the memory index for relevant entries.",
    input_schema={"query": str, "limit": int},
)
async def ua_memory_search_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    query = args.get("query", "")
    limit = args.get("limit", 5)
    content = ua_memory_search(query=query, limit=limit)
    return {"content": [{"type": "text", "text": content}]}

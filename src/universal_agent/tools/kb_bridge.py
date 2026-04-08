"""
Bridge tools for the NotebookLM Knowledge Base registry.
"""
from typing import Any
import json
from claude_agent_sdk import tool
from universal_agent.wiki.kb_registry import (
    register_kb,
    get_kb,
    list_kbs,
    update_kb,
)

def _ok(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=False)}]}

def _err(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"error: {message}"}]}

@tool(
    name="kb_list",
    description="List all knowledge bases in the registry.",
    input_schema={},
)
async def kb_list_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    try:
        kbs = list_kbs()
        return _ok({"knowledge_bases": kbs})
    except Exception as exc:
        return _err(str(exc))

@tool(
    name="kb_get",
    description="Get details of a knowledge base by slug.",
    input_schema={"slug": str},
)
async def kb_get_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    try:
        slug = str(args.get("slug") or "").strip()
        if not slug:
            return _err("Missing slug")
        kb = get_kb(slug)
        if not kb:
            return _err(f"Knowledge base '{slug}' not found.")
        return _ok(kb)
    except Exception as exc:
        return _err(str(exc))

@tool(
    name="kb_register",
    description="Register a new NLM notebook as a knowledge base.",
    input_schema={
        "slug": str,
        "notebook_id": str,
        "title": str,
        "tags": list,
    },
)
async def kb_register_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    try:
        slug = str(args.get("slug") or "").strip()
        notebook_id = str(args.get("notebook_id") or "").strip()
        title = str(args.get("title") or "").strip()
        tags = args.get("tags") or []
        
        if not slug or not notebook_id or not title:
            return _err("Missing required fields: slug, notebook_id, title")
            
        kb = register_kb(slug=slug, notebook_id=notebook_id, title=title, tags=tags)
        return _ok({"status": "registered", "kb": kb})
    except Exception as exc:
        return _err(str(exc))

@tool(
    name="kb_update",
    description="Update KB metadata (e.g. source_count, last_queried, tags).",
    input_schema={
        "slug": str,
        "source_count": int,
        "last_queried": str,
        "tags": list,
    },
)
async def kb_update_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    try:
        slug = str(args.get("slug") or "").strip()
        if not slug:
            return _err("Missing slug")
            
        kwargs = {}
        if "source_count" in args:
            kwargs["source_count"] = int(args["source_count"])
        if "last_queried" in args:
            kwargs["last_queried"] = str(args["last_queried"])
        if "tags" in args:
            kwargs["tags"] = list(args["tags"])
            
        if not kwargs:
            return _err("No fields to update provided.")
            
        kb = update_kb(slug, **kwargs)
        return _ok({"status": "updated", "kb": kb})
    except Exception as exc:
        return _err(str(exc))

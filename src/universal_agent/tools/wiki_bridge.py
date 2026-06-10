from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from universal_agent.wiki.core import (
    ensure_vault,
    lint_vault,
    query_vault,
    sync_internal_memory_vault,
    wiki_ingest_external_source,
)


def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=True)}]}


def _err(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"error: {message}"}]}


@tool(
    name="wiki_init_vault",
    description="Initialize or validate an LLM wiki vault. Supports `external` and `internal` vault kinds.",
    input_schema={"vault_kind": str, "vault_slug": str, "title": str, "root_override": str},
)
async def wiki_init_vault_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    try:
        context = ensure_vault(
            args.get("vault_kind", "external"),
            args.get("vault_slug", "default"),
            title=args.get("title"),
            root_override=args.get("root_override"),
        )
    except Exception as exc:
        return _err(str(exc))
    return _ok(
        {
            "status": "success",
            "vault_kind": context.kind,
            "vault_slug": context.slug,
            "title": context.title,
            "vault_path": str(context.path),
        }
    )


@tool(
    name="wiki_sync_internal_memory",
    description="Refresh the derived internal memory vault from canonical memory, session, and checkpoint sources.",
    input_schema={"vault_slug": str, "trigger": str, "root_override": str},
)
async def wiki_sync_internal_memory_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    try:
        result = sync_internal_memory_vault(
            vault_slug=str(args.get("vault_slug") or "internal-memory"),
            trigger=str(args.get("trigger") or "manual"),
            root_override=str(args.get("root_override") or "").strip() or None,
        )
    except Exception as exc:
        return _err(str(exc))
    return _ok(result)


@tool(
    name="wiki_query",
    description="Query an external or internal wiki vault using index-first retrieval. Optionally persist the answer into analyses/.",
    input_schema={
        "vault_kind": str,
        "vault_slug": str,
        "query": str,
        "max_results": int,
        "save_answer": bool,
        "answer_title": str,
        "root_override": str,
    },
)
async def wiki_query_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    try:
        result = query_vault(
            vault_kind=str(args.get("vault_kind") or "external"),
            vault_slug=str(args.get("vault_slug") or "default"),
            query=str(args.get("query") or "").strip(),
            max_results=int(args.get("max_results") or 5),
            save_answer=bool(args.get("save_answer", False)),
            answer_title=str(args.get("answer_title") or "").strip() or None,
            root_override=str(args.get("root_override") or "").strip() or None,
        )
    except Exception as exc:
        return _err(str(exc))
    return _ok(result)


@tool(
    name="wiki_lint",
    description="Run wiki integrity checks against an external or internal vault and write a lint report under lint/.",
    input_schema={"vault_kind": str, "vault_slug": str, "root_override": str},
)
async def wiki_lint_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    try:
        result = lint_vault(
            vault_kind=str(args.get("vault_kind") or "external"),
            vault_slug=str(args.get("vault_slug") or "default"),
            root_override=str(args.get("root_override") or "").strip() or None,
        )
    except Exception as exc:
        return _err(str(exc))
    return _ok(result)


@tool(
    name="wiki_ingest_external_source",
    description=(
        "Ingest an external source document (e.g. a downloaded NotebookLM report) into an "
        "`external` wiki vault, enriching it with LLM-extracted entities, concepts, and a "
        "summary and writing it under sources/. This is 'step 6' of the Create-Wiki flow — "
        "the verb that persists the NLM report into the knowledge vault. Pass the report body "
        "as `source_content` and a human title as `source_title`."
    ),
    input_schema={
        "vault_slug": str,
        "source_title": str,
        "source_content": str,
        "source_id": str,
        "root_override": str,
    },
)
async def wiki_ingest_external_source_wrapper(args: dict[str, Any]) -> dict[str, Any]:
    vault_slug = str(args.get("vault_slug") or "").strip()
    source_title = str(args.get("source_title") or "").strip()
    source_content = str(args.get("source_content") or "")
    if not vault_slug:
        return _err("vault_slug is required")
    if not source_title:
        return _err("source_title is required")
    if not source_content.strip():
        return _err("source_content is required")
    try:
        result = wiki_ingest_external_source(
            vault_slug=vault_slug,
            source_title=source_title,
            source_content=source_content,
            source_id=str(args.get("source_id") or "").strip() or None,
            root_override=str(args.get("root_override") or "").strip() or None,
        )
    except Exception as exc:
        return _err(str(exc))
    return _ok(result)



from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from claude_agent_sdk import tool


_SKILL_FUNCS: Optional[Tuple[Callable[..., Dict[str, Any]], Callable[..., Dict[str, Any]], Callable[[Dict[str, Any]], Dict[str, Any]]]] = None


def _import_skill_lib() -> Tuple[
    Callable[..., Dict[str, Any]],
    Callable[..., Dict[str, Any]],
    Callable[[Dict[str, Any]], Dict[str, Any]],
]:
    """
    Import grok-x-trends skill library functions with a stable sys.path setup.

    This keeps the architecture clean:
    - Primary model (ZAI / Anthropic-emulated) reasons and calls this tool.
    - This tool fetches X evidence via xAI `x_search` (Grok).
    - Primary model uses returned evidence to infer themes / write narrative.
    """
    global _SKILL_FUNCS
    if _SKILL_FUNCS is not None:
        return _SKILL_FUNCS

    repo_root = Path(__file__).resolve().parents[3]
    skill_scripts = repo_root / ".claude" / "skills" / "grok-x-trends" / "scripts"
    sys.path.insert(0, str(skill_scripts))

    from lib.xai_x_search import global_trends, parse_trends_response, trends_for_query  # type: ignore

    _SKILL_FUNCS = (trends_for_query, global_trends, parse_trends_response)
    return _SKILL_FUNCS


def _env_api_key() -> Optional[str]:
    return os.environ.get("GROK_API_KEY") or os.environ.get("XAI_API_KEY")


def _compute_window(from_date: Optional[str], to_date: Optional[str], days: int) -> Tuple[str, str]:
    if from_date and to_date:
        return from_date, to_date
    today = dt.datetime.now(dt.timezone.utc).date()
    days = max(1, int(days))
    start = today - dt.timedelta(days=days)
    return start.isoformat(), today.isoformat()


@tool(
    name="x_trends_posts",
    description=(
        "Fetch evidence posts from X (Twitter) about a topic via xAI Grok `x_search` and return structured JSON. "
        "This tool returns posts only (themes=[]). Use the primary model to infer themes and write the narrative."
    ),
    input_schema={
        "query": str,  # required unless global_mode
        "global_mode": bool,
        "region": str,
        "from_date": str,
        "to_date": str,
        "days": int,
        "depth": str,  # quick/default/deep
        "allowed_x_handles": list,
        "excluded_x_handles": list,
        "enable_image_understanding": bool,
        "enable_video_understanding": bool,
        "model": str,
        "max_posts": int,  # optional clamp for output size
    },
)
async def x_trends_posts_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    return await _x_trends_posts_impl(args)


async def _x_trends_posts_impl(args: Dict[str, Any]) -> Dict[str, Any]:
    query = str(args.get("query", "") or "").strip()
    global_mode = bool(args.get("global_mode", False))
    region = str(args.get("region", "US") or "US").strip() or "US"

    from_date = (str(args.get("from_date") or "").strip() or None)
    to_date = (str(args.get("to_date") or "").strip() or None)
    days = int(args.get("days", 1) or 1)
    depth = str(args.get("depth", "default") or "default").strip()

    allowed = args.get("allowed_x_handles") or []
    excluded = args.get("excluded_x_handles") or []
    if not isinstance(allowed, list):
        allowed = []
    if not isinstance(excluded, list):
        excluded = []

    enable_img = bool(args.get("enable_image_understanding", False))
    enable_vid = bool(args.get("enable_video_understanding", False))
    model = str(args.get("model", "grok-4-1-fast") or "grok-4-1-fast").strip()
    max_posts = int(args.get("max_posts", 40) or 40)
    max_posts = max(1, min(max_posts, 120))

    if not global_mode and not query:
        return {"content": [{"type": "text", "text": "error: query is required unless global_mode=true"}]}

    api_key = _env_api_key()
    if not api_key:
        return {"content": [{"type": "text", "text": "error: missing GROK_API_KEY (or XAI_API_KEY)"}]}

    win_from, win_to = _compute_window(from_date, to_date, days)

    trends_for_query, global_trends, parse_trends_response = _import_skill_lib()

    if global_mode:
        raw = global_trends(
            api_key=api_key,
            model=model,
            region=region,
            from_date=win_from,
            to_date=win_to,
            depth=depth,
            max_themes=0,
            posts_only=True,
            allowed_x_handles=allowed or None,
            excluded_x_handles=excluded or None,
            enable_image_understanding=enable_img,
            enable_video_understanding=enable_vid,
        )
        query_label = f"GLOBAL ({region})"
    else:
        raw = trends_for_query(
            api_key=api_key,
            model=model,
            query=query,
            from_date=win_from,
            to_date=win_to,
            depth=depth,
            max_themes=0,
            posts_only=True,
            allowed_x_handles=allowed or None,
            excluded_x_handles=excluded or None,
            enable_image_understanding=enable_img,
            enable_video_understanding=enable_vid,
        )
        query_label = query

    parsed = parse_trends_response(raw)
    posts = parsed.get("posts", []) or []
    if isinstance(posts, list):
        posts = posts[:max_posts]
    else:
        posts = []

    out = {
        "query": query_label,
        "from_date": win_from,
        "to_date": win_to,
        "model": model,
        "depth": depth,
        "themes": [],  # explicit for downstream consumers
        "posts": posts,
        "raw_text": parsed.get("raw_text", "") if not posts else "",
    }

    return {"content": [{"type": "text", "text": json.dumps(out, indent=2, ensure_ascii=True)}]}

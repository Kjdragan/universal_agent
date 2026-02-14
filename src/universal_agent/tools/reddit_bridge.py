from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from claude_agent_sdk import tool

from pathlib import Path

from universal_agent.utils.session_workspace import (
    build_interim_work_product_paths,
    resolve_current_session_workspace,
    safe_slug,
    write_json,
)

def _as_dict(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    return obj


def _extract_listing(resp: Any) -> Tuple[Optional[dict], Optional[str]]:
    """
    Attempt to normalize Composio Reddit responses into a standard Reddit Listing:
    {"kind":"Listing","data":{"children":[...], "after": ...}}
    """
    resp = _as_dict(resp)
    if not isinstance(resp, dict):
        return None, "response_not_dict"

    # Common Composio wrapper: {"successful":true,"data":{...}}
    data = resp.get("data") if isinstance(resp.get("data"), dict) else None
    if data and (data.get("kind") == "Listing" or (isinstance(data.get("data"), dict) and "children" in data.get("data", {}))):
        return data, None

    # Some tools return the Listing at top-level.
    if resp.get("kind") == "Listing" and isinstance(resp.get("data"), dict):
        return resp, None

    # Multi-execute-style wrapper (rare for direct tool execution): {"success":true,"results":[{"response":{...}}]}
    results = resp.get("results")
    if isinstance(results, list) and results:
        first = results[0]
        if isinstance(first, dict) and isinstance(first.get("response"), dict):
            inner = first["response"]
            inner = _as_dict(inner)
            if isinstance(inner, dict):
                inner_data = inner.get("data")
                if isinstance(inner_data, dict):
                    if inner_data.get("kind") == "Listing" or (isinstance(inner_data.get("data"), dict) and "children" in inner_data.get("data", {})):
                        return inner_data, None

    return None, "listing_not_found"


def _normalize_time_window(t: str) -> str:
    t = (t or "").strip().lower()
    allowed = {"hour", "day", "week", "month", "year", "all"}
    return t if t in allowed else "week"


@tool(
    name="reddit_top_posts",
    description=(
        "Fetch top posts for a subreddit (time-filtered) and return a compact structured JSON object "
        "(rank/title/score/comments/author/permalink/url/created_utc). This avoids large raw Listing payloads."
    ),
    input_schema={
        "subreddit": str,  # required (without leading r/)
        "t": str,  # hour/day/week/month/year/all (default week)
        "limit": int,  # default 10, clamped
        "include_nsfw": bool,  # default false
        "max_posts": int,  # optional clamp
        "save_to_workspace": bool,  # default true; best-effort save under session work_products/
    },
)
async def reddit_top_posts_wrapper(args: Dict[str, Any]) -> Dict[str, Any]:
    subreddit = str(args.get("subreddit", "") or "").strip().lstrip("/").replace("r/", "")
    t = _normalize_time_window(str(args.get("t", "week") or "week"))
    limit = int(args.get("limit", 10) or 10)
    limit = max(1, min(limit, 50))
    max_posts = int(args.get("max_posts", limit) or limit)
    max_posts = max(1, min(max_posts, 50))
    include_nsfw = bool(args.get("include_nsfw", False))
    save_to_workspace = bool(args.get("save_to_workspace", True))

    if not subreddit:
        return {"content": [{"type": "text", "text": "error: subreddit is required (e.g. 'artificial')"}]}

    api_key = (os.environ.get("COMPOSIO_API_KEY") or "").strip()
    if not api_key:
        return {"content": [{"type": "text", "text": "error: missing COMPOSIO_API_KEY"}]}

    # Use the same identity resolution as the rest of the system.
    from composio import Composio  # local import to keep module import cheap
    from universal_agent.identity.resolver import resolve_user_id

    user_id = resolve_user_id()
    client = Composio(api_key=api_key)

    try:
        resp = client.tools.execute(
            slug="REDDIT_GET_R_TOP",
            arguments={"subreddit": subreddit, "t": t, "limit": limit},
            user_id=user_id,
            dangerously_skip_version_check=True,
        )
    except Exception as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"error: failed to execute REDDIT_GET_R_TOP via Composio ({type(e).__name__}): {e}",
                }
            ]
        }

    listing, err = _extract_listing(resp)
    if not listing:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"error: could not parse Reddit Listing ({err}). Raw keys={list(_as_dict(resp).keys()) if isinstance(_as_dict(resp), dict) else type(resp).__name__}",
                }
            ]
        }

    data = listing.get("data") if isinstance(listing.get("data"), dict) else {}
    children = data.get("children") if isinstance(data.get("children"), list) else []

    posts: List[dict] = []
    for child in children[:max_posts]:
        if not isinstance(child, dict):
            continue
        p = child.get("data")
        if not isinstance(p, dict):
            continue
        if (not include_nsfw) and bool(p.get("over_18", False)):
            continue
        permalink = str(p.get("permalink") or "")
        if permalink and not permalink.startswith("http"):
            permalink = "https://www.reddit.com" + permalink
        posts.append(
            {
                "rank": len(posts) + 1,
                "id": p.get("id"),
                "title": p.get("title"),
                "score": p.get("score"),
                "num_comments": p.get("num_comments"),
                "author": p.get("author"),
                "created_utc": p.get("created_utc"),
                "permalink": permalink,
                "url": p.get("url"),
                "is_self": p.get("is_self"),
                "domain": p.get("domain"),
            }
        )

        if len(posts) >= max_posts:
            break

    out = {
        "subreddit": subreddit,
        "t": t,
        "limit": limit,
        "after": data.get("after"),
        "posts": posts,
    }

    # Best-effort session capture for downstream agents.
    if save_to_workspace:
        ws = resolve_current_session_workspace(repo_root=str(Path(__file__).resolve().parents[3]))
        if ws:
            wp = build_interim_work_product_paths(
                workspace_dir=ws,
                domain="top_posts",
                source="reddit",
                run_slug=safe_slug(f"r_{subreddit}_{t}", fallback="reddit_top"),
            )
            try:
                write_json(
                    wp.request_path,
                    {
                        "tool": "reddit_top_posts",
                        "args": args,
                    },
                )
                write_json(wp.result_path, out)
                write_json(
                    wp.manifest_path,
                    {
                        "type": "interim_work_product",
                        "domain": "social_intel",
                        "source": "reddit",
                        "kind": "top_posts",
                        "paths": {
                            "request": str(wp.request_path.relative_to(ws)),
                            "result": str(wp.result_path.relative_to(ws)),
                        },
                        "retention": "session",
                    },
                )
            except Exception:
                pass

    return {"content": [{"type": "text", "text": json.dumps(out, indent=2, ensure_ascii=True)}]}

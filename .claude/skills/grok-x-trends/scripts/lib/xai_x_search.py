"""xAI Responses API wrapper for X Search (x_search tool)."""

from __future__ import annotations

import json
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from . import http

XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"

# (min_posts, max_posts)
DEPTH_CONFIG: Dict[str, Tuple[int, int]] = {
    "quick": (8, 12),
    "default": (20, 30),
    "deep": (40, 60),
}

# Hard cap on tool calls to reduce runaway spend. The x_search tool may fan out
# into multiple internal calls (e.g., keyword + semantic searches), so these are
# intentionally not too tight.
MAX_TOOL_CALLS_BY_DEPTH: Dict[str, int] = {
    "quick": 8,
    "default": 14,
    "deep": 24,
}

# Prefer native JSON output. This removes the need for regex extraction and
# makes downstream parsing deterministic.
RESPONSE_FORMAT_JSON_OBJECT: Dict[str, Any] = {"type": "json_object"}


def _build_x_search_tool(
    *,
    from_date: str,
    to_date: str,
    allowed_x_handles: Optional[List[str]] = None,
    excluded_x_handles: Optional[List[str]] = None,
    enable_image_understanding: bool = False,
    enable_video_understanding: bool = False,
) -> Dict[str, Any]:
    # Per docs: allowed and excluded are mutually exclusive; max 10 entries.
    allowed = [h.strip().lstrip("@") for h in (allowed_x_handles or []) if str(h).strip()]
    excluded = [h.strip().lstrip("@") for h in (excluded_x_handles or []) if str(h).strip()]
    if allowed and excluded:
        raise ValueError("allowed_x_handles and excluded_x_handles cannot be set together")
    if len(allowed) > 10:
        allowed = allowed[:10]
    if len(excluded) > 10:
        excluded = excluded[:10]

    tool: Dict[str, Any] = {"type": "x_search", "from_date": from_date, "to_date": to_date}
    if allowed:
        tool["allowed_x_handles"] = allowed
    if excluded:
        tool["excluded_x_handles"] = excluded
    if enable_image_understanding:
        tool["enable_image_understanding"] = True
    if enable_video_understanding:
        tool["enable_video_understanding"] = True
    return tool


def _log_error(msg: str) -> None:
    sys.stderr.write(f"[X ERROR] {msg}\n")
    sys.stderr.flush()


X_TRENDS_PROMPT = """You have access to real-time X (Twitter) data via the x_search tool.

Task: Find what is "trending" on X about: {query}

Time window: {from_date} to {to_date} (inclusive).

Instructions:
1) Use x_search to retrieve {min_items}-{max_items} high-quality, high-engagement posts about the query in the time window.
2) Infer up to {max_themes} distinct trending themes/angles from those posts.
3) Prefer substantive posts; avoid low-signal spam, pure giveaways, and link-only posts when possible.

Output JSON with keys:
- themes: list of objects with keys: label, keywords, why_trending, example_urls
- posts: list of objects with keys: text, url, author_handle, date, engagement

Rules:
- date must be YYYY-MM-DD format or null
- engagement can be null if unknown
- example_urls should reference URLs from returned posts when possible
"""

X_POSTS_ONLY_PROMPT = """You have access to real-time X (Twitter) data via the x_search tool.

Task: Retrieve high-quality, high-engagement X posts about: {query}

Time window: {from_date} to {to_date} (inclusive).

Instructions:
1) Use x_search to retrieve {min_items}-{max_items} high-quality, high-engagement posts about the query in the time window.
2) Prefer substantive posts; avoid low-signal spam, pure giveaways, and link-only posts when possible.
3) Do NOT infer themes in this mode. Set themes to an empty list: [].

Output JSON with keys:
- themes: []
- posts: list of objects with keys: text, url, author_handle, date, engagement

Rules:
- date must be YYYY-MM-DD format or null
- engagement can be null if unknown
"""

X_GLOBAL_TRENDS_PROMPT = """You have access to real-time X (Twitter) data via the x_search tool.

Task: Infer what is broadly trending on X for region: {region}

Time window: {from_date} to {to_date} (inclusive).

Instructions:
1) Use x_search multiple times with diverse broad queries (at least 5 searches), spanning:
   - breaking news / politics
   - tech / AI
   - sports
   - entertainment / pop culture
   - finance / markets
   - memes / general chatter
2) Across all searches, collect {min_items}-{max_items} total high-quality, high-engagement posts.
3) From those posts, infer up to {max_themes} distinct trending themes/hashtags/events.
4) Prefer posts with substantive content; avoid spam, giveaways, and link-only posts when possible.

Output JSON with keys:
- themes: list of objects with keys: label, keywords, why_trending, example_urls
- posts: list of objects with keys: text, url, author_handle, date, engagement

Rules:
- date must be YYYY-MM-DD format or null
- engagement can be null if unknown
- example_urls should reference URLs from returned posts when possible
"""

X_GLOBAL_POSTS_ONLY_PROMPT = """You have access to real-time X (Twitter) data via the x_search tool.

Task: Retrieve high-quality, high-engagement X posts representing what is broadly trending for region: {region}

Time window: {from_date} to {to_date} (inclusive).

Instructions:
1) Use x_search multiple times with diverse broad queries (at least 5 searches), spanning:
   - breaking news / politics
   - tech / AI
   - sports
   - entertainment / pop culture
   - finance / markets
   - memes / general chatter
2) Across all searches, collect {min_items}-{max_items} total high-quality, high-engagement posts.
3) Do NOT infer themes in this mode. Set themes to an empty list: [].

Output JSON with keys:
- themes: []
- posts: list of objects with keys: text, url, author_handle, date, engagement

Rules:
- date must be YYYY-MM-DD format or null
- engagement can be null if unknown
"""


def trends_for_query(
    *,
    api_key: str,
    model: str,
    query: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    max_themes: int = 8,
    posts_only: bool = False,
    allowed_x_handles: Optional[List[str]] = None,
    excluded_x_handles: Optional[List[str]] = None,
    enable_image_understanding: bool = False,
    enable_video_understanding: bool = False,
    timeout_s: int = 180,
    mock_response: Optional[Dict[str, Any]] = None,
    response_format: Optional[Dict[str, Any]] = RESPONSE_FORMAT_JSON_OBJECT,
) -> Dict[str, Any]:
    if mock_response is not None:
        return mock_response

    min_items, max_items = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    max_tool_calls = MAX_TOOL_CALLS_BY_DEPTH.get(depth, MAX_TOOL_CALLS_BY_DEPTH["default"])

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "tools": [
            _build_x_search_tool(
                from_date=from_date,
                to_date=to_date,
                allowed_x_handles=allowed_x_handles,
                excluded_x_handles=excluded_x_handles,
                enable_image_understanding=enable_image_understanding,
                enable_video_understanding=enable_video_understanding,
            )
        ],
        "response_format": response_format,
        "max_tool_calls": max_tool_calls,
        "input": [
            {
                "role": "user",
                "content": (X_POSTS_ONLY_PROMPT if posts_only else X_TRENDS_PROMPT).format(
                    query=query,
                    from_date=from_date,
                    to_date=to_date,
                    min_items=min_items,
                    max_items=max_items,
                    max_themes=max_themes,
                ),
            }
        ],
    }

    return http.post_json(XAI_RESPONSES_URL, payload, headers=headers, timeout_s=timeout_s)


def global_trends(
    *,
    api_key: str,
    model: str,
    region: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    max_themes: int = 10,
    posts_only: bool = False,
    allowed_x_handles: Optional[List[str]] = None,
    excluded_x_handles: Optional[List[str]] = None,
    enable_image_understanding: bool = False,
    enable_video_understanding: bool = False,
    timeout_s: int = 240,
    mock_response: Optional[Dict[str, Any]] = None,
    response_format: Optional[Dict[str, Any]] = RESPONSE_FORMAT_JSON_OBJECT,
) -> Dict[str, Any]:
    if mock_response is not None:
        return mock_response

    min_items, max_items = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    max_tool_calls = MAX_TOOL_CALLS_BY_DEPTH.get(depth, MAX_TOOL_CALLS_BY_DEPTH["default"])

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "tools": [
            _build_x_search_tool(
                from_date=from_date,
                to_date=to_date,
                allowed_x_handles=allowed_x_handles,
                excluded_x_handles=excluded_x_handles,
                enable_image_understanding=enable_image_understanding,
                enable_video_understanding=enable_video_understanding,
            )
        ],
        "response_format": response_format,
        "max_tool_calls": max_tool_calls,
        "input": [
            {
                "role": "user",
                "content": (X_GLOBAL_POSTS_ONLY_PROMPT if posts_only else X_GLOBAL_TRENDS_PROMPT).format(
                    region=region,
                    from_date=from_date,
                    to_date=to_date,
                    min_items=min_items,
                    max_items=max_items,
                    max_themes=max_themes,
                ),
            }
        ],
    }

    return http.post_json(XAI_RESPONSES_URL, payload, headers=headers, timeout_s=timeout_s)


def _extract_output_text(response: Dict[str, Any]) -> str:
    # Newer xAI Responses format
    output_text = ""
    output = response.get("output")
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        for item in output:
            if isinstance(item, dict):
                if item.get("type") == "message":
                    content = item.get("content", [])
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "output_text":
                            output_text = c.get("text", "")
                            break
                elif "text" in item:
                    output_text = str(item.get("text") or "")
            elif isinstance(item, str):
                output_text = item
            if output_text:
                break

    # Older chat-style format fallback
    if not output_text and "choices" in response:
        try:
            output_text = response["choices"][0]["message"].get("content", "")  # type: ignore[index]
        except Exception:
            pass

    return output_text or ""


def parse_trends_response(response: Dict[str, Any]) -> Dict[str, Any]:
    """Return dict with keys: themes(list), posts(list), raw_text(str)."""

    if response.get("error"):
        err = response["error"]
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        _log_error(f"xAI API error: {msg}")
        return {"themes": [], "posts": [], "raw_text": ""}

    text = _extract_output_text(response)
    if not text:
        return {"themes": [], "posts": [], "raw_text": ""}

    raw_text = text
    data: Dict[str, Any] | None = None

    # Preferred path: when using response_format=json_object, the entire output
    # should be a JSON object. This avoids regex extraction entirely.
    try:
        if text.lstrip().startswith("{"):
            data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    # Fallback: extract the first JSON object that contains a "posts" key.
    # This is only for backward compatibility if response_format isn't applied
    # or the model violated the contract.
    if data is None:
        json_match = re.search(r'\{[\s\S]*"posts"[\s\S]*\}', text)
        if not json_match:
            return {"themes": [], "posts": [], "raw_text": raw_text}
        try:
            data = json.loads(json_match.group(0))
        except json.JSONDecodeError:
            return {"themes": [], "posts": [], "raw_text": raw_text}
    if not isinstance(data, dict):
        return {"themes": [], "posts": [], "raw_text": raw_text}

    themes = data.get("themes") if isinstance(data.get("themes"), list) else []
    posts = data.get("posts") if isinstance(data.get("posts"), list) else []

    # Light cleanup.
    cleaned_posts: List[Dict[str, Any]] = []
    for p in posts:
        if not isinstance(p, dict):
            continue
        url = str(p.get("url") or "").strip()
        if not url:
            continue
        date = p.get("date")
        if date and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(date)):
            date = None
        cleaned_posts.append(
            {
                "text": str(p.get("text") or "").strip()[:600],
                "url": url,
                "author_handle": str(p.get("author_handle") or "").strip().lstrip("@"),
                "date": date,
                "engagement": p.get("engagement"),
            }
        )

    cleaned_themes: List[Dict[str, Any]] = []
    for t in themes:
        if not isinstance(t, dict):
            continue
        label = str(t.get("label") or "").strip()
        if not label:
            continue
        keywords = t.get("keywords")
        if not isinstance(keywords, list):
            keywords = []
        example_urls = t.get("example_urls")
        if not isinstance(example_urls, list):
            example_urls = []
        cleaned_themes.append(
            {
                "label": label[:80],
                "keywords": [str(k).strip()[:40] for k in keywords if str(k).strip()][:10],
                "why_trending": str(t.get("why_trending") or "").strip()[:280],
                "example_urls": [str(u).strip() for u in example_urls if str(u).strip()][:5],
            }
        )

    return {"themes": cleaned_themes, "posts": cleaned_posts, "raw_text": text}

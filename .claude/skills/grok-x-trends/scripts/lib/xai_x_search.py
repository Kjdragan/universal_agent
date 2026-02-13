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


def _log_error(msg: str) -> None:
    sys.stderr.write(f"[X ERROR] {msg}\n")
    sys.stderr.flush()


X_TRENDS_PROMPT = """You have access to real-time X (Twitter) data via the x_search tool.

Task: Find what is "trending" on X about: {query}

Time window: {from_date} to {to_date} (inclusive).

Instructions:
1) Use x_search to retrieve {min_items}-{max_items} high-quality, high-engagement posts about the query in the time window.
2) From those posts, infer up to {max_themes} distinct trending themes/angles (not just repeated phrasing).
3) Prefer posts with substantive content; avoid low-signal spam, pure giveaways, and link-only posts when possible.

Return ONLY valid JSON in this exact format (no markdown, no extra keys, no commentary):
{{
  "themes": [
    {{
      "label": "Short theme label",
      "keywords": ["keyword1", "keyword2"],
      "why_trending": "1-2 sentences explaining why this theme is showing up",
      "example_urls": ["https://x.com/user/status/...", "https://x.com/user/status/..."]
    }}
  ],
  "posts": [
    {{
      "text": "Post text (truncate if long)",
      "url": "https://x.com/user/status/...",
      "author_handle": "username",
      "date": "YYYY-MM-DD or null if unknown",
      "engagement": {{
        "likes": 100,
        "reposts": 25,
        "replies": 15,
        "quotes": 5
      }}
    }}
  ]
}}

Rules:
- date must be YYYY-MM-DD format or null
- engagement can be null if unknown
- example_urls must reference URLs from returned posts when possible
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

Return ONLY valid JSON in this exact format (no markdown, no extra keys, no commentary):
{
  "themes": [
    {
      "label": "Short theme label",
      "keywords": ["keyword1", "keyword2"],
      "why_trending": "1-2 sentences explaining why this theme is showing up",
      "example_urls": ["https://x.com/user/status/...", "https://x.com/user/status/..."]
    }
  ],
  "posts": [
    {
      "text": "Post text (truncate if long)",
      "url": "https://x.com/user/status/...",
      "author_handle": "username",
      "date": "YYYY-MM-DD or null if unknown",
      "engagement": {
        "likes": 100,
        "reposts": 25,
        "replies": 15,
        "quotes": 5
      }
    }
  ]
}

Rules:
- date must be YYYY-MM-DD format or null
- engagement can be null if unknown
- example_urls must reference URLs from returned posts when possible
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
    timeout_s: int = 180,
    mock_response: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if mock_response is not None:
        return mock_response

    min_items, max_items = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "tools": [{"type": "x_search"}],
        "input": [
            {
                "role": "user",
                "content": X_TRENDS_PROMPT.format(
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
    timeout_s: int = 240,
    mock_response: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if mock_response is not None:
        return mock_response

    min_items, max_items = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "tools": [{"type": "x_search"}],
        "input": [
            {
                "role": "user",
                "content": X_GLOBAL_TRENDS_PROMPT.format(
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

    # Extract JSON object.
    json_match = re.search(r"\{[\\s\\S]*\"posts\"[\\s\\S]*\}", text)
    if not json_match:
        return {"themes": [], "posts": [], "raw_text": text}

    try:
        data = json.loads(json_match.group(0))
    except json.JSONDecodeError:
        return {"themes": [], "posts": [], "raw_text": text}

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
        if date and not re.match(r"^\\d{4}-\\d{2}-\\d{2}$", str(date)):
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

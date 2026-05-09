"""Narrow client wrappers around the hackernews-pp-cli for Lane A briefing context.

Reuses the `_run_cli` subprocess helper from
`hackernews_snapshot_service` so we don't duplicate the env/timeout/JSON
plumbing. Each function targets one specific CLI subcommand and returns
a compact, caller-ready shape (or empty / None on any failure).

Three commands are wrapped:
  - `items thread <id>`  → `fetch_thread_comments` (top-level comments)
  - `items <id>`         → `fetch_item` (full item dict, includes `text`)
  - `search <topic> --tag comment --since 7d`
                        → `search_topic_mentions` (Algolia hits)

Failure-tolerant by design: any CLI failure (timeout, non-zero exit,
bad JSON, missing binary) returns the "empty" sentinel for that
function — callers must never see exceptions from this module under
normal operation.
"""
from __future__ import annotations

import logging
from typing import Any

from universal_agent.services.hackernews_snapshot_service import _run_cli

logger = logging.getLogger(__name__)

# Per-call subprocess timeout. Each individual CLI invocation gets this
# many seconds before being killed. Bounded so a single hung call cannot
# stall the whole briefing helper.
PER_CALL_TIMEOUT_S = 15


def fetch_thread_comments(item_id: int, limit: int = 10) -> list[dict[str, Any]]:
    """Return the top-level comments for an HN story in document order.

    Algolia's per-comment `points` field is unreliable (often 0 across
    the board), so we don't sort by points. Instead we take top-level
    children only (no nested replies) in document order — Algolia
    preserves Firebase's authoritative ranking at depth-1, which is the
    HN-canonical "top comments" view.
    """
    result = _run_cli(["items", "thread", str(item_id)], timeout=PER_CALL_TIMEOUT_S)
    if not isinstance(result, dict):
        return []
    children = result.get("children")
    if not isinstance(children, list):
        return []
    out: list[dict[str, Any]] = []
    for child in children:
        if not isinstance(child, dict):
            continue
        if child.get("type") != "comment":
            continue
        out.append(child)
        if len(out) >= limit:
            break
    return out


def fetch_item(item_id: int) -> dict[str, Any] | None:
    """Return the full item dict for a single HN id, or None on failure.

    The CLI wraps the response as `{meta, results}`; this helper unwraps
    `results` and returns it directly. Returns None if `results` is
    missing or not a dict.
    """
    result = _run_cli(["items", str(item_id)], timeout=PER_CALL_TIMEOUT_S)
    if not isinstance(result, dict):
        return None
    payload = result.get("results")
    if not isinstance(payload, dict):
        return None
    return payload


def search_topic_mentions(topic: str, limit: int = 5) -> list[dict[str, Any]]:
    """Algolia search for comments mentioning `topic` in the past 7 days.

    Uses default relevance sort (Algolia's combined recency + tf-idf
    ranker). Returns up to `limit` raw Algolia hit dicts so the caller
    can decide how to render them.
    """
    args = [
        "search", topic,
        "--tag", "comment",
        "--since", "7d",
        "--hits-per-page", str(int(limit)),
    ]
    result = _run_cli(args, timeout=PER_CALL_TIMEOUT_S)
    if not isinstance(result, dict):
        return []
    hits = result.get("hits")
    if not isinstance(hits, list):
        return []
    return [h for h in hits if isinstance(h, dict)]

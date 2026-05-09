"""Build the HN evidence block for the daily briefing (Lane A, hybrid γ″).

The helper does HN-internal content fetching via the `hackernews-pp-cli`
subprocess — comment threads, post bodies, and per-watchlist-topic
Algolia comment searches. These are deterministic, free, never
paywalled, and always reliable.

Article body fetching is NOT done here. Atlas selectively invokes the
ZAI-native `webReader` MCP during its briefing mission for items where
the comments suggest the article matters. That keeps article-fetch
judgment in the LLM where it belongs and avoids needing to install
defuddle or handle paywall edge cases helper-side.

Failure-tolerant: any single CLI call can fail without aborting the
block — that item just renders with whatever did succeed. If the
snapshot is missing or stale (>24h), the helper returns "" and the
briefing proceeds without HN context.

See docs/integrations/hackernews_phase2_plan.md § 1 for the full
design and decision log.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import logging
from typing import Any
from urllib.parse import urlparse

from universal_agent.services.hackernews_content_fetch import (
    fetch_item,
    fetch_thread_comments,
    search_topic_mentions,
)
from universal_agent.services.hackernews_snapshot_service import read_latest

logger = logging.getLogger(__name__)

# A snapshot older than this is treated as stale; the briefing proceeds
# without HN context rather than serve day-old "fresh" data.
MAX_AGE_HOURS = 24

# Candidate selection caps. See § 1.3a of the Phase 2 plan.
TOP_STORIES_PICKS = 5
CONTROVERSIAL_PICKS = 3
PER_TOPIC_PICKS = 2
MAX_CANDIDATES = 10

# Per-item comment cap. Algolia returns the whole thread tree; we only
# render the top-level comments (HN-canonical "top comments" view) and
# cap further to keep the briefing prompt bounded.
COMMENTS_PER_ITEM = 10

# Per-watchlist-topic Algolia mention cap. 5 × 6 topics = 30 hits max.
MENTIONS_PER_TOPIC = 5

# Parallel fan-out for fetcher calls. Bounded so a slow CLI tail-latency
# spike on one call doesn't stall all of them serially.
HYDRATE_PARALLELISM = 6


def build_briefing_block(watchlist: list[str], now: datetime | None = None) -> str:
    """Return a multi-section markdown block, or "" when no HN context is available.

    The block has three sections:
      1. Watchlist pulse (count + avg points per topic from the snapshot).
      2. Candidate items with hydrated HN-internal content (comments + post text).
      3. Algolia per-watchlist-topic mentions across all of HN this week.

    Atlas is instructed (via the briefing prompt) to read the comments
    first and selectively call `webReader` for items where the comments
    suggest the article matters.
    """
    snap = read_latest()
    if not _is_fresh(snap, now=now):
        return ""

    candidates = _select_candidates(snap, watchlist)
    if not candidates:
        return ""

    hydrated = _hydrate_candidates(candidates)
    topic_mentions = _gather_topic_mentions(watchlist)

    return _render(snap, hydrated, topic_mentions, watchlist)


# ─── snapshot freshness ────────────────────────────────────────────────


def _is_fresh(snap: Any, now: datetime | None = None) -> bool:
    if not isinstance(snap, dict):
        return False
    iso_ts = snap.get("meta", {}).get("generated_at", "")
    if not iso_ts:
        return False
    try:
        ts = datetime.fromisoformat(iso_ts)
    except (TypeError, ValueError):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age_h = ((now or datetime.now(timezone.utc)) - ts).total_seconds() / 3600
    return age_h <= MAX_AGE_HOURS


# ─── candidate selection (§ 1.3a) ──────────────────────────────────────


def _select_candidates(snap: dict[str, Any], watchlist: list[str]) -> list[dict[str, Any]]:
    """Returns up to MAX_CANDIDATES deduplicated candidates, prioritized by source list."""
    seen: set[Any] = set()
    out: list[dict[str, Any]] = []

    def push(items: list[Any]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            sid = item.get("id")
            if sid is None or sid in seen or len(out) >= MAX_CANDIDATES:
                continue
            seen.add(sid)
            out.append(item)

    push((snap.get("top_stories") or [])[:TOP_STORIES_PICKS])
    push((snap.get("controversial") or [])[:CONTROVERSIAL_PICKS])
    pulses = snap.get("pulses") or {}
    for topic in watchlist:
        topic_pulse = pulses.get(topic) or {}
        topic_top = topic_pulse.get("top_stories") or []
        push(topic_top[:PER_TOPIC_PICKS])

    return out


# ─── hydration ─────────────────────────────────────────────────────────


def _hydrate_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For each candidate, fetch comments + post text in parallel. Best-effort."""
    with ThreadPoolExecutor(max_workers=HYDRATE_PARALLELISM) as pool:
        return list(pool.map(_hydrate_one, candidates))


def _hydrate_one(candidate: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(candidate)
    sid = candidate.get("id")
    if sid is None:
        out["comments"] = []
        out["post_text"] = ""
        return out
    try:
        comments = fetch_thread_comments(int(sid), limit=COMMENTS_PER_ITEM)
    except Exception as exc:  # noqa: BLE001 — best-effort, never raise upward
        logger.warning("HN briefing: thread fetch for %s failed: %s", sid, exc)
        comments = []
    try:
        item = fetch_item(int(sid))
        post_text = (item or {}).get("text") or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("HN briefing: item fetch for %s failed: %s", sid, exc)
        post_text = ""
    out["comments"] = comments
    out["post_text"] = post_text
    return out


# ─── per-topic Algolia comment search ──────────────────────────────────


def _gather_topic_mentions(watchlist: list[str]) -> dict[str, list[dict[str, Any]]]:
    """For each watchlist topic, fetch top Algolia comment mentions in parallel."""
    out: dict[str, list[dict[str, Any]]] = {t: [] for t in watchlist}
    with ThreadPoolExecutor(max_workers=HYDRATE_PARALLELISM) as pool:
        results = list(pool.map(_safe_search_topic, watchlist))
    for topic, mentions in zip(watchlist, results, strict=True):
        out[topic] = mentions
    return out


def _safe_search_topic(topic: str) -> list[dict[str, Any]]:
    try:
        return search_topic_mentions(topic, limit=MENTIONS_PER_TOPIC)
    except Exception as exc:  # noqa: BLE001
        logger.warning("HN briefing: topic search '%s' failed: %s", topic, exc)
        return []


# ─── rendering ─────────────────────────────────────────────────────────


def _render(
    snap: dict[str, Any],
    candidates: list[dict[str, Any]],
    topic_mentions: dict[str, list[dict[str, Any]]],
    watchlist: list[str],
) -> str:
    """Render the three-section markdown block."""
    generated_at = snap.get("meta", {}).get("generated_at", "")
    parts: list[str] = [f"## Hacker News This Week (snapshot from {generated_at})"]
    parts.append("")
    parts.append(_render_watchlist_pulse(snap, watchlist))
    parts.append(_render_candidates(candidates))
    parts.append(_render_topic_mentions(topic_mentions, watchlist))

    errors = snap.get("meta", {}).get("errors") or []
    if errors:
        parts.append(f"_(panels with errors this run: {', '.join(errors)})_")

    return "\n\n".join(p for p in parts if p)


def _render_watchlist_pulse(snap: dict[str, Any], watchlist: list[str]) -> str:
    pulses = snap.get("pulses") or {}
    rows = []
    for topic in watchlist:
        p = pulses.get(topic) or {}
        count = int(p.get("count") or 0)
        avg = int(p.get("avg_points") or 0)
        rows.append(f"| {topic} | {count} | {avg} |")
    if not rows:
        return ""
    return "\n".join([
        "### 1. Watchlist pulse — 7-day mention volume",
        "",
        "| topic | mentions | avg points |",
        "|---|---:|---:|",
        *rows,
    ])


def _render_candidates(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return ""
    parts: list[str] = [
        "### 2. Front-page candidates with HN-internal content",
        "",
        "_For each item, read the comments first. **If the comments suggest the article body would clarify whether the substance matters, call `webReader` with the article URL** (the ZAI-native MCP) to fetch the article. Be selective — typically 2-5 of these warrant a fetch._",
        "",
    ]
    for i, item in enumerate(candidates, start=1):
        parts.append(_render_one_candidate(i, item))
    return "\n\n".join(parts)


def _render_one_candidate(rank: int, item: dict[str, Any]) -> str:
    title = item.get("title") or "(untitled)"
    sid = item.get("id")
    url = item.get("url") or ""
    score = int(item.get("score") or 0)
    descendants = int(item.get("descendants") or 0)
    by = item.get("by") or item.get("author") or ""
    host = ""
    if url:
        try:
            host = urlparse(url).hostname or ""
            host = host.removeprefix("www.")
        except Exception:  # noqa: BLE001
            host = ""

    hn_url = f"https://news.ycombinator.com/item?id={sid}" if sid is not None else ""
    lines: list[str] = [
        f"#### [{rank}] {title}",
        f"- {score} pts · {descendants} cmt · {host or 'news.ycombinator.com'} · by {by}",
        f"- Article: {url or '(self post)'}",
        f"- HN thread: {hn_url}",
    ]

    post_text = (item.get("post_text") or "").strip()
    if post_text:
        excerpt = post_text if len(post_text) <= 800 else (post_text[:800] + "…")
        lines.append("- **Post body:**")
        lines.append(f"  > {excerpt}")

    comments = item.get("comments") or []
    if comments:
        lines.append(f"- **Top comments** ({len(comments)} of {descendants}):")
        for c in comments:
            author = c.get("author") or "?"
            text = (c.get("text") or "").strip()
            if not text:
                continue
            excerpt = text if len(text) <= 600 else (text[:600] + "…")
            # Replace newlines so the markdown blockquote stays coherent.
            excerpt = excerpt.replace("\n", " ").replace("\r", " ")
            lines.append(f"  > **{author}**: {excerpt}")

    return "\n".join(lines)


def _render_topic_mentions(
    topic_mentions: dict[str, list[dict[str, Any]]],
    watchlist: list[str],
) -> str:
    """Render the Algolia per-topic comment search results."""
    sections = []
    for topic in watchlist:
        mentions = topic_mentions.get(topic) or []
        if not mentions:
            continue
        section = [f"#### {topic}"]
        for m in mentions:
            comment = (m.get("comment_text") or "").strip()
            story_title = m.get("story_title") or ""
            story_url = m.get("story_url") or ""
            author = m.get("author") or "?"
            created = m.get("created_at") or ""
            story_id = m.get("story_id")
            if not comment:
                continue
            excerpt = comment if len(comment) <= 500 else (comment[:500] + "…")
            excerpt = excerpt.replace("\n", " ").replace("\r", " ")
            story_link = (
                f"https://news.ycombinator.com/item?id={story_id}"
                if story_id is not None else ""
            )
            section.append(
                f"- **{author}** ({created}): {excerpt} "
                f"— in: {story_title or '(no title)'} ({story_url or story_link})"
            )
        if len(section) > 1:
            sections.append("\n".join(section))

    if not sections:
        return ""

    header = [
        "### 3. Watchlist mentions across ALL HN comments this week (Algolia, top 5 per topic, default relevance sort)",
        "",
    ]
    return "\n\n".join(header + sections)

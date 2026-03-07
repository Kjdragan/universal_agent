"""Trend scoring helpers for Threads adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any

from csi_ingester.adapters.threads_api import velocity_score

_STOPWORDS = {
    "about",
    "after",
    "again",
    "against",
    "between",
    "could",
    "first",
    "from",
    "have",
    "just",
    "more",
    "most",
    "other",
    "should",
    "their",
    "there",
    "these",
    "those",
    "today",
    "using",
    "where",
    "while",
    "with",
    "would",
    "threads",
}


@dataclass(slots=True)
class TrendWindow:
    minutes: int


DEFAULT_TREND_WINDOWS: tuple[TrendWindow, ...] = (
    TrendWindow(minutes=15),
    TrendWindow(minutes=60),
    TrendWindow(minutes=24 * 60),
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(raw: Any) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rank_terms(
    *,
    bucket: str,
    hits: list[dict[str, Any]],
    windows: list[int] | None = None,
    top_k: int = 8,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    if not hits:
        return []
    now_dt = now or _utc_now()
    window_minutes = windows or [window.minutes for window in DEFAULT_TREND_WINDOWS]
    query_by_term: dict[str, list[dict[str, Any]]] = {}
    for hit in hits:
        term = str(hit.get("query_term") or hit.get("trend_term") or "").strip().lower()
        if not term:
            continue
        query_by_term.setdefault(term, []).append(hit)

    snapshots: list[dict[str, Any]] = []
    for minutes in sorted({max(1, int(m)) for m in window_minutes}):
        # Align windows to deterministic UTC minute buckets so trend dedupe keys
        # remain stable across repeated polls inside the same window.
        now_minutes = int(now_dt.timestamp() // 60)
        bucket_start_minutes = (now_minutes // minutes) * minutes
        start = datetime.fromtimestamp(bucket_start_minutes * 60, tz=timezone.utc)
        end = start + timedelta(minutes=minutes)
        scored: list[tuple[str, float, int, int]] = []
        for term, term_hits in query_by_term.items():
            in_window = [
                item
                for item in term_hits
                if start <= (_parse_iso(item.get("timestamp")) or now_dt) < end
            ]
            if not in_window:
                continue
            score = sum(float(velocity_score(item, now=now_dt)) for item in in_window)
            unique_posts = len({str(item.get("media_id") or "") for item in in_window if str(item.get("media_id") or "").strip()})
            scored.append((term, round(score, 6), len(in_window), unique_posts))

        scored.sort(key=lambda row: (row[1], row[3], row[2], row[0]), reverse=True)
        for rank, row in enumerate(scored[: max(1, top_k)], start=1):
            term, score, hit_count, unique_posts = row
            snapshots.append(
                {
                    "trend_bucket": bucket,
                    "trend_term": term,
                    "window_minutes": int(minutes),
                    "window_start_utc": _iso(start),
                    "window_end_utc": _iso(end),
                    "velocity_score": float(score),
                    "hit_count": int(hit_count),
                    "unique_posts": int(unique_posts),
                    "rank": int(rank),
                    "generated_at": _iso(now_dt),
                }
            )
    return snapshots


def discover_terms_from_posts(posts: list[dict[str, Any]], *, limit: int = 20) -> list[str]:
    scored: dict[str, float] = {}
    for post in posts:
        text = str(post.get("text") or "").strip().lower()
        if not text:
            continue
        weight = max(0.01, float(velocity_score(post)))
        hashtags = re.findall(r"#([a-z0-9_]{3,40})", text)
        words = re.findall(r"\b[a-z][a-z0-9]{4,32}\b", text)
        for token in hashtags + words:
            cleaned = token.strip("_").lower()
            if len(cleaned) < 3 or cleaned in _STOPWORDS:
                continue
            scored[cleaned] = float(scored.get(cleaned) or 0.0) + weight
    ranked = sorted(scored.items(), key=lambda row: (row[1], row[0]), reverse=True)
    return [term for term, _ in ranked[: max(1, int(limit))]]

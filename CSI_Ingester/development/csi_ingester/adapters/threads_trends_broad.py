"""Threads broad-trends adapter."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from csi_ingester.adapters.base import RawEvent, SourceAdapter
from csi_ingester.adapters.threads_api import (
    ThreadsAPIClient,
    normalize_threads_item,
    stable_hash,
    velocity_score,
)
from csi_ingester.adapters.threads_trends import discover_terms_from_posts, rank_terms
from csi_ingester.contract import CreatorSignalEvent

logger = logging.getLogger(__name__)


class ThreadsBroadTrendsAdapter(SourceAdapter):
    """Broad crawl trend adapter with adaptive term expansion."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._load_state_fn = lambda source_key: None
        self._save_state_fn = lambda source_key, state: None
        self._state_key = "threads_trends_broad:state"
        self._max_seen_cache = max(300, int(config.get("max_seen_cache", 8000)))
        self._seen_ids: set[str] = set()
        self._adaptive_scores: dict[str, float] = {}

    def set_state_backend(self, load_state_fn, save_state_fn) -> None:
        self._load_state_fn = load_state_fn
        self._save_state_fn = save_state_fn

    def _hydrate_state(self) -> dict[str, Any]:
        raw = self._load_state_fn(self._state_key)
        if not isinstance(raw, dict):
            return {}
        seen = raw.get("seen_ids") if isinstance(raw.get("seen_ids"), list) else []
        cleaned = [str(item).strip() for item in seen if str(item).strip()]
        if cleaned:
            self._seen_ids = set(cleaned[: self._max_seen_cache])
        adaptive = raw.get("adaptive_scores") if isinstance(raw.get("adaptive_scores"), dict) else {}
        self._adaptive_scores = {
            str(term).strip().lower(): float(score)
            for term, score in adaptive.items()
            if str(term).strip() and isinstance(score, (int, float))
        }
        return raw

    def _persist_state(self, *, quota_state: dict[str, Any], last_poll_at: str) -> None:
        self._save_state_fn(
            self._state_key,
            {
                "seen_ids": sorted(self._seen_ids)[: self._max_seen_cache],
                "adaptive_scores": self._adaptive_scores,
                "quota_state": quota_state,
                "last_poll_at": last_poll_at,
            },
        )

    def _base_queries(self) -> list[str]:
        query_pool = self.config.get("query_pool") if isinstance(self.config.get("query_pool"), list) else []
        terms = [str(item or "").strip() for item in query_pool if str(item or "").strip()]
        if not terms:
            terms = ["ai", "news", "creator", "startup", "product", "economy"]
        return terms

    def _adaptive_queries(self) -> list[str]:
        max_terms = max(0, int(self.config.get("adaptive_max_terms", 12)))
        if max_terms <= 0:
            return []
        ranked = sorted(self._adaptive_scores.items(), key=lambda row: (row[1], row[0]), reverse=True)
        return [term for term, _ in ranked[:max_terms]]

    def _decay_adaptive_scores(self) -> None:
        decay = float(self.config.get("adaptive_decay", 0.9) or 0.9)
        min_score = float(self.config.get("adaptive_min_score", 0.025) or 0.025)
        next_scores: dict[str, float] = {}
        for term, score in self._adaptive_scores.items():
            updated = float(score) * decay
            if updated >= min_score:
                next_scores[term] = round(updated, 6)
        self._adaptive_scores = next_scores

    def _update_adaptive_scores(self, discovered_terms: list[str], posts: list[dict[str, Any]]) -> None:
        if not discovered_terms:
            return
        per_term_bonus = max(0.01, float(self.config.get("adaptive_term_bonus", 0.25) or 0.25))
        cap = max(1, int(self.config.get("adaptive_max_candidates", 50)))

        hit_by_term: dict[str, float] = {}
        for post in posts:
            text = str(post.get("text") or "").lower()
            if not text:
                continue
            score = max(0.01, float(velocity_score(post)))
            for term in discovered_terms:
                term_l = str(term).strip().lower()
                if term_l and term_l in text:
                    hit_by_term[term_l] = float(hit_by_term.get(term_l) or 0.0) + score

        for term in discovered_terms:
            term_l = str(term).strip().lower()
            if not term_l:
                continue
            bonus = per_term_bonus + float(hit_by_term.get(term_l) or 0.0)
            self._adaptive_scores[term_l] = round(float(self._adaptive_scores.get(term_l) or 0.0) + bonus, 6)

        ranked = sorted(self._adaptive_scores.items(), key=lambda row: (row[1], row[0]), reverse=True)
        self._adaptive_scores = {term: score for term, score in ranked[:cap]}

    async def fetch_events(self) -> list[RawEvent]:
        state = self._hydrate_state()
        quota_state = state.get("quota_state") if isinstance(state.get("quota_state"), dict) else {}
        client = ThreadsAPIClient.from_config(self.config, quota_state=quota_state)
        if not client.is_configured():
            logger.info("Threads broad trends adapter skipped: missing THREADS_USER_ID or THREADS_ACCESS_TOKEN")
            return []

        self._decay_adaptive_scores()
        base_queries = self._base_queries()
        adaptive_queries = self._adaptive_queries() if bool(self.config.get("adaptive_enabled", True)) else []
        all_queries: list[str] = []
        seen: set[str] = set()
        for query in base_queries + adaptive_queries:
            key = str(query).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            all_queries.append(str(query).strip())

        max_queries = max(1, int(self.config.get("max_queries_per_cycle", 20)))
        all_queries = all_queries[:max_queries]
        search_types = self.config.get("search_types") if isinstance(self.config.get("search_types"), list) else ["TOP", "RECENT"]
        normalized_types = [str(item or "").strip().upper() for item in search_types if str(item or "").strip()]
        if not normalized_types:
            normalized_types = ["TOP", "RECENT"]
        per_query_limit = max(1, min(int(self.config.get("per_query_limit", 20)), 50))
        media_types = self.config.get("media_types") if isinstance(self.config.get("media_types"), list) else []
        trend_windows = self.config.get("trend_windows_minutes") if isinstance(self.config.get("trend_windows_minutes"), list) else [15, 60, 1440]
        trend_top_k = max(1, min(int(self.config.get("trend_top_k", 10)), 20))

        events: list[RawEvent] = []
        post_hits: list[dict[str, Any]] = []
        for query in all_queries:
            for search_type in normalized_types:
                try:
                    rows = await client.keyword_search(
                        query=query,
                        search_type=search_type,
                        search_surface="KEYWORD",
                        media_types=media_types,
                        limit=per_query_limit,
                    )
                except Exception as exc:
                    logger.warning(
                        "Threads broad keyword search failed query=%s search_type=%s error=%s",
                        query,
                        search_type,
                        exc,
                    )
                    continue
                for row in rows:
                    normalized = normalize_threads_item(
                        row,
                        term=query,
                        trend_bucket="broad",
                        search_type=search_type,
                    )
                    media_id = str(normalized.get("media_id") or "").strip()
                    if not media_id:
                        continue
                    post_hits.append(normalized)
                    if media_id in self._seen_ids:
                        continue
                    events.append(
                        RawEvent(
                            source="threads_trends_broad",
                            event_type="threads_keyword_hit",
                            payload=normalized,
                            occurred_at=str(normalized.get("timestamp") or _iso_now()),
                        )
                    )
                    self._seen_ids.add(media_id)

        discovered_terms = discover_terms_from_posts(post_hits, limit=max(5, int(self.config.get("adaptive_discovery_limit", 25))))
        self._update_adaptive_scores(discovered_terms, post_hits)

        trend_rows = rank_terms(
            bucket="broad",
            hits=post_hits,
            windows=[int(item) for item in trend_windows if int(item) > 0],
            top_k=trend_top_k,
        )
        for row in trend_rows:
            row["discovered_terms"] = discovered_terms[:15]
            events.append(
                RawEvent(
                    source="threads_trends_broad",
                    event_type="threads_trend_snapshot",
                    payload=row,
                    occurred_at=str(row.get("generated_at") or _iso_now()),
                )
            )

        self._seen_ids = set(sorted(self._seen_ids)[: self._max_seen_cache])
        self._persist_state(quota_state=client.quota_state(), last_poll_at=_iso_now())
        return events

    def normalize(self, raw: RawEvent) -> CreatorSignalEvent:
        payload = raw.payload if isinstance(raw.payload, dict) else {}
        event_type = str(raw.event_type or "threads_keyword_hit")
        now_iso = _iso_now()

        if event_type == "threads_trend_snapshot":
            trend_term = str(payload.get("trend_term") or "").strip().lower()
            window_start = str(payload.get("window_start_utc") or "").strip()
            subject = {
                "platform": "threads",
                "trend_bucket": "broad",
                "trend_term": trend_term,
                "window_minutes": int(payload.get("window_minutes") or 0),
                "window_start_utc": window_start,
                "window_end_utc": str(payload.get("window_end_utc") or ""),
                "velocity_score": float(payload.get("velocity_score") or 0.0),
                "hit_count": int(payload.get("hit_count") or 0),
                "unique_posts": int(payload.get("unique_posts") or 0),
                "rank": int(payload.get("rank") or 0),
                "discovered_terms": payload.get("discovered_terms") if isinstance(payload.get("discovered_terms"), list) else [],
            }
            event = CreatorSignalEvent(
                event_id=f"threads:broad:trend:{trend_term}:{window_start}:{int(datetime.now(timezone.utc).timestamp())}",
                dedupe_key="",
                source="threads_trends_broad",
                event_type=event_type,
                occurred_at=str(raw.occurred_at or now_iso),
                received_at=now_iso,
                subject=subject,
                routing={"pipeline": "creator_watchlist_handler", "priority": "high", "tags": ["threads", "trends", "broad"]},
                metadata={"source_adapter": "threads_trends_broad_v1"},
            )
            event.dedupe_key = self.get_dedupe_key(event)
            return event

        media_id = str(payload.get("media_id") or "").strip()
        event = CreatorSignalEvent(
            event_id=(
                f"threads:broad:hit:{media_id}:{int(datetime.now(timezone.utc).timestamp())}"
                if media_id
                else f"threads:broad:hit:{stable_hash([str(payload), raw.occurred_at])}"
            ),
            dedupe_key="",
            source="threads_trends_broad",
            event_type="threads_keyword_hit",
            occurred_at=str(raw.occurred_at or now_iso),
            received_at=now_iso,
            subject={
                "platform": "threads",
                "media_id": media_id,
                "text": str(payload.get("text") or ""),
                "timestamp": str(payload.get("timestamp") or now_iso),
                "username": str(payload.get("username") or ""),
                "permalink": str(payload.get("permalink") or ""),
                "media_type": str(payload.get("media_type") or ""),
                "reply_count": int(payload.get("reply_count") or 0),
                "repost_count": int(payload.get("repost_count") or 0),
                "quote_count": int(payload.get("quote_count") or 0),
                "like_count": int(payload.get("like_count") or 0),
                "query_term": str(payload.get("query_term") or ""),
                "search_type": str(payload.get("search_type") or ""),
                "trend_bucket": "broad",
            },
            routing={"pipeline": "creator_watchlist_handler", "priority": "high", "tags": ["threads", "trends", "broad"]},
            metadata={"source_adapter": "threads_trends_broad_v1"},
        )
        event.dedupe_key = self.get_dedupe_key(event)
        return event

    def get_dedupe_key(self, event: CreatorSignalEvent) -> str:
        if str(event.event_type or "") == "threads_trend_snapshot":
            bucket = str(event.subject.get("trend_bucket") or "broad").strip().lower() or "broad"
            term = str(event.subject.get("trend_term") or "").strip().lower()
            window_start = str(event.subject.get("window_start_utc") or "").strip()
            return f"threads:trend:{bucket}:{term}:{window_start}"
        media_id = str(event.subject.get("media_id") or "").strip()
        if media_id:
            return f"threads:{media_id}"
        return f"threads:broad:{stable_hash([event.event_type, event.occurred_at, str(event.subject)])}"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

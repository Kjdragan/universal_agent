"""Threads seeded-trends adapter."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from csi_ingester.adapters.base import RawEvent, SourceAdapter
from csi_ingester.adapters.threads_api import ThreadsAPIClient, normalize_threads_item, stable_hash
from csi_ingester.adapters.threads_trends import rank_terms
from csi_ingester.contract import CreatorSignalEvent

logger = logging.getLogger(__name__)


class ThreadsSeededTrendsAdapter(SourceAdapter):
    """Discover seeded-domain trend signals via Threads keyword search."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._load_state_fn = lambda source_key: None
        self._save_state_fn = lambda source_key, state: None
        self._state_key = "threads_trends_seeded:state"
        self._max_seen_cache = max(300, int(config.get("max_seen_cache", 6000)))
        self._seen_ids: set[str] = set()

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
        return raw

    def _persist_state(self, *, quota_state: dict[str, Any], last_poll_at: str) -> None:
        self._save_state_fn(
            self._state_key,
            {
                "seen_ids": sorted(self._seen_ids)[: self._max_seen_cache],
                "quota_state": quota_state,
                "last_poll_at": last_poll_at,
            },
        )

    def _seed_terms(self) -> list[str]:
        terms: list[str] = []
        raw_packs = self.config.get("query_packs")
        if isinstance(raw_packs, list):
            for row in raw_packs:
                if not isinstance(row, dict):
                    continue
                values = row.get("terms") if isinstance(row.get("terms"), list) else []
                for value in values:
                    cleaned = str(value or "").strip()
                    if cleaned:
                        terms.append(cleaned)
        direct_terms = self.config.get("seed_terms") if isinstance(self.config.get("seed_terms"), list) else []
        for value in direct_terms:
            cleaned = str(value or "").strip()
            if cleaned:
                terms.append(cleaned)

        deduped: list[str] = []
        seen: set[str] = set()
        for term in terms:
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(term)
        return deduped

    async def fetch_events(self) -> list[RawEvent]:
        state = self._hydrate_state()
        quota_state = state.get("quota_state") if isinstance(state.get("quota_state"), dict) else {}
        client = ThreadsAPIClient.from_config(self.config, quota_state=quota_state)
        if not client.is_configured():
            logger.info("Threads seeded trends adapter skipped: missing THREADS_USER_ID or THREADS_ACCESS_TOKEN")
            return []

        terms = self._seed_terms()
        if not terms:
            logger.warning("Threads seeded trends adapter has no seed_terms/query_packs configured")
            self._persist_state(quota_state=client.quota_state(), last_poll_at=_iso_now())
            return []

        search_types = self.config.get("search_types") if isinstance(self.config.get("search_types"), list) else ["TOP", "RECENT"]
        normalized_types = [str(item or "").strip().upper() for item in search_types if str(item or "").strip()]
        if not normalized_types:
            normalized_types = ["TOP", "RECENT"]
        per_query_limit = max(1, min(int(self.config.get("per_query_limit", 20)), 50))
        media_types = self.config.get("media_types") if isinstance(self.config.get("media_types"), list) else []
        trend_windows = self.config.get("trend_windows_minutes") if isinstance(self.config.get("trend_windows_minutes"), list) else [15, 60, 1440]
        trend_top_k = max(1, min(int(self.config.get("trend_top_k", 8)), 20))

        post_hits: list[dict[str, Any]] = []
        events: list[RawEvent] = []
        for term in terms:
            for search_type in normalized_types:
                try:
                    rows = await client.keyword_search(
                        query=term,
                        search_type=search_type,
                        search_surface="KEYWORD",
                        media_types=media_types,
                        limit=per_query_limit,
                    )
                except Exception as exc:
                    logger.warning(
                        "Threads seeded keyword search failed term=%s search_type=%s error=%s",
                        term,
                        search_type,
                        exc,
                    )
                    continue

                for row in rows:
                    normalized = normalize_threads_item(
                        row,
                        term=term,
                        trend_bucket="seeded",
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
                            source="threads_trends_seeded",
                            event_type="threads_keyword_hit",
                            payload=normalized,
                            occurred_at=str(normalized.get("timestamp") or _iso_now()),
                        )
                    )
                    self._seen_ids.add(media_id)

        trend_rows = rank_terms(
            bucket="seeded",
            hits=post_hits,
            windows=[int(item) for item in trend_windows if int(item) > 0],
            top_k=trend_top_k,
        )
        for row in trend_rows:
            events.append(
                RawEvent(
                    source="threads_trends_seeded",
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
                "trend_bucket": "seeded",
                "trend_term": trend_term,
                "window_minutes": int(payload.get("window_minutes") or 0),
                "window_start_utc": window_start,
                "window_end_utc": str(payload.get("window_end_utc") or ""),
                "velocity_score": float(payload.get("velocity_score") or 0.0),
                "hit_count": int(payload.get("hit_count") or 0),
                "unique_posts": int(payload.get("unique_posts") or 0),
                "rank": int(payload.get("rank") or 0),
            }
            event = CreatorSignalEvent(
                event_id=(
                    f"threads:seeded:trend:{trend_term}:{window_start}:{int(datetime.now(timezone.utc).timestamp())}"
                ),
                dedupe_key="",
                source="threads_trends_seeded",
                event_type=event_type,
                occurred_at=str(raw.occurred_at or now_iso),
                received_at=now_iso,
                subject=subject,
                routing={"pipeline": "creator_watchlist_handler", "priority": "high", "tags": ["threads", "trends", "seeded"]},
                metadata={"source_adapter": "threads_trends_seeded_v1"},
            )
            event.dedupe_key = self.get_dedupe_key(event)
            return event

        media_id = str(payload.get("media_id") or "").strip()
        subject = {
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
            "trend_bucket": "seeded",
        }
        event = CreatorSignalEvent(
            event_id=(
                f"threads:seeded:hit:{media_id}:{int(datetime.now(timezone.utc).timestamp())}"
                if media_id
                else f"threads:seeded:hit:{stable_hash([str(payload), raw.occurred_at])}"
            ),
            dedupe_key="",
            source="threads_trends_seeded",
            event_type="threads_keyword_hit",
            occurred_at=str(raw.occurred_at or now_iso),
            received_at=now_iso,
            subject=subject,
            routing={"pipeline": "creator_watchlist_handler", "priority": "high", "tags": ["threads", "trends", "seeded"]},
            metadata={"source_adapter": "threads_trends_seeded_v1"},
        )
        event.dedupe_key = self.get_dedupe_key(event)
        return event

    def get_dedupe_key(self, event: CreatorSignalEvent) -> str:
        if str(event.event_type or "") == "threads_trend_snapshot":
            bucket = str(event.subject.get("trend_bucket") or "seeded").strip().lower() or "seeded"
            term = str(event.subject.get("trend_term") or "").strip().lower()
            window_start = str(event.subject.get("window_start_utc") or "").strip()
            return f"threads:trend:{bucket}:{term}:{window_start}"
        media_id = str(event.subject.get("media_id") or "").strip()
        if media_id:
            return f"threads:{media_id}"
        return f"threads:seeded:{stable_hash([event.event_type, event.occurred_at, str(event.subject)])}"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

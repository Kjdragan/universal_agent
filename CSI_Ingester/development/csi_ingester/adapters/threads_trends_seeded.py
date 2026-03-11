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
        self._term_limit_overrides: dict[str, dict[str, Any]] = {}

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
        raw_overrides = raw.get("term_limit_overrides")
        if isinstance(raw_overrides, dict):
            cleaned_overrides: dict[str, dict[str, Any]] = {}
            for term, payload in raw_overrides.items():
                term_key = str(term or "").strip().lower()
                if not term_key or not isinstance(payload, dict):
                    continue
                limit = int(payload.get("limit") or 0)
                if limit <= 0:
                    continue
                cleaned_overrides[term_key] = {
                    "limit": limit,
                    "updated_at": str(payload.get("updated_at") or ""),
                    "reduce_errors": int(payload.get("reduce_errors") or 0),
                }
            self._term_limit_overrides = cleaned_overrides
        return raw

    def _persist_state(self, *, quota_state: dict[str, Any], last_poll_at: str, last_cycle: dict[str, Any] | None = None) -> None:
        self._save_state_fn(
            self._state_key,
            {
                "seen_ids": sorted(self._seen_ids)[: self._max_seen_cache],
                "term_limit_overrides": self._term_limit_overrides,
                "quota_state": quota_state,
                "last_poll_at": last_poll_at,
                "last_cycle": last_cycle if isinstance(last_cycle, dict) else {},
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

    @staticmethod
    def _is_reduce_data_error(exc: Exception) -> bool:
        text = str(exc or "").strip().lower()
        if not text:
            return False
        return (
            "please reduce the amount of data" in text
            or '"code":1' in text
            or "code=1" in text
            or " code:1" in text
        )

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        text = str(exc or "").strip().lower()
        if not text:
            return False
        return (
            "rate limit" in text
            or '"code":4' in text
            or " code:4" in text
            or "error_subcode\":1349210" in text
        )

    @staticmethod
    def _is_timeout_error(exc: Exception) -> bool:
        text = str(exc or "").strip().lower()
        if not text:
            return False
        return "readtimeout" in text or "timeout" in text

    def _configured_limit_for_term(self, *, term: str, default_limit: int, min_limit: int) -> int:
        term_key = str(term or "").strip().lower()
        override = self._term_limit_overrides.get(term_key) if term_key else None
        if isinstance(override, dict):
            override_limit = max(min_limit, int(override.get("limit") or default_limit))
            return min(default_limit, override_limit)
        return int(default_limit)

    def _mark_term_reduce_error(self, *, term: str, attempted_limit: int, min_limit: int) -> None:
        term_key = str(term or "").strip().lower()
        if not term_key:
            return
        current = self._term_limit_overrides.get(term_key) if isinstance(self._term_limit_overrides.get(term_key), dict) else {}
        fallback = max(min_limit, min(int(attempted_limit), max(min_limit, int(attempted_limit) // 2)))
        reduce_errors = int(current.get("reduce_errors") or 0) + 1
        self._term_limit_overrides[term_key] = {
            "limit": int(fallback),
            "updated_at": _iso_now(),
            "reduce_errors": int(reduce_errors),
        }

    def _mark_term_success(self, *, term: str, success_limit: int, max_limit: int, recover_step: int) -> None:
        term_key = str(term or "").strip().lower()
        if not term_key:
            return
        existing = self._term_limit_overrides.get(term_key)
        if not isinstance(existing, dict):
            if int(success_limit) >= int(max_limit):
                return
            next_limit = min(int(max_limit), int(success_limit) + max(1, int(recover_step)))
            self._term_limit_overrides[term_key] = {
                "limit": int(next_limit),
                "updated_at": _iso_now(),
                "reduce_errors": 0,
            }
            return
        next_limit = min(int(max_limit), max(int(success_limit), int(success_limit) + max(1, int(recover_step))))
        if next_limit >= int(max_limit):
            self._term_limit_overrides.pop(term_key, None)
            return
        self._term_limit_overrides[term_key] = {
            "limit": int(next_limit),
            "updated_at": _iso_now(),
            "reduce_errors": int(existing.get("reduce_errors") or 0),
        }

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
            self._persist_state(
                quota_state=client.quota_state(),
                last_poll_at=_iso_now(),
                last_cycle={"reason": "no_seed_terms", "terms_count": 0},
            )
            return []

        search_types = self.config.get("search_types") if isinstance(self.config.get("search_types"), list) else ["TOP", "RECENT"]
        normalized_types = [str(item or "").strip().upper() for item in search_types if str(item or "").strip()]
        if not normalized_types:
            normalized_types = ["TOP", "RECENT"]
        per_query_limit = max(1, min(int(self.config.get("per_query_limit", 20)), 50))
        min_query_limit = max(1, min(int(self.config.get("min_query_limit", 3)), per_query_limit))
        reduce_retry_steps = max(0, int(self.config.get("reduce_retry_steps", 3)))
        limit_recovery_step = max(1, int(self.config.get("limit_recovery_step", 1)))
        media_types = self.config.get("media_types") if isinstance(self.config.get("media_types"), list) else []
        trend_windows = self.config.get("trend_windows_minutes") if isinstance(self.config.get("trend_windows_minutes"), list) else [15, 60, 1440]
        trend_top_k = max(1, min(int(self.config.get("trend_top_k", 8)), 20))

        post_hits: list[dict[str, Any]] = []
        events: list[RawEvent] = []
        query_attempts = 0
        query_success = 0
        query_degraded = 0
        reduce_data_errors = 0
        rate_limit_errors = 0
        rate_limited_cycle = False
        timeout_errors = 0
        max_timeout_errors = max(1, int(self.config.get("max_timeout_errors_per_cycle", 2)))
        timeout_aborted_cycle = False
        total_hits = 0
        new_media_hits = 0
        for term in terms:
            if rate_limited_cycle or timeout_aborted_cycle:
                break
            for search_type in normalized_types:
                if rate_limited_cycle or timeout_aborted_cycle:
                    break
                query_attempts += 1
                attempt_limit = self._configured_limit_for_term(
                    term=term,
                    default_limit=per_query_limit,
                    min_limit=min_query_limit,
                )
                degraded_this_query = False
                remaining_reduce_steps = int(reduce_retry_steps)
                try:
                    while True:
                        try:
                            rows = await client.keyword_search(
                                query=term,
                                search_type=search_type,
                                search_surface="KEYWORD",
                                media_types=media_types,
                                limit=attempt_limit,
                            )
                            break
                        except Exception as inner_exc:
                            if not self._is_reduce_data_error(inner_exc):
                                raise
                            reduce_data_errors += 1
                            if attempt_limit <= min_query_limit or remaining_reduce_steps <= 0:
                                raise
                            if degraded_this_query and remaining_reduce_steps <= 1:
                                raise
                            next_limit = max(min_query_limit, int(attempt_limit) // 2)
                            if next_limit >= attempt_limit:
                                raise
                            query_degraded += 1
                            degraded_this_query = True
                            remaining_reduce_steps -= 1
                            attempt_limit = next_limit
                except Exception as exc:
                    if self._is_reduce_data_error(exc):
                        self._mark_term_reduce_error(
                            term=term,
                            attempted_limit=attempt_limit,
                            min_limit=min_query_limit,
                        )
                    if self._is_rate_limit_error(exc):
                        rate_limit_errors += 1
                        rate_limited_cycle = True
                        logger.warning(
                            "Threads seeded keyword search rate-limited term=%s search_type=%s; halting cycle early",
                            term,
                            search_type,
                        )
                    if self._is_timeout_error(exc):
                        timeout_errors += 1
                        if timeout_errors >= max_timeout_errors:
                            timeout_aborted_cycle = True
                            logger.warning(
                                "Threads seeded keyword search timeout threshold reached (%s); halting cycle early",
                                timeout_errors,
                            )
                    logger.warning(
                        "Threads seeded keyword search failed term=%s search_type=%s error=%s",
                        term,
                        search_type,
                        exc,
                    )
                    continue
                query_success += 1
                self._mark_term_success(
                    term=term,
                    success_limit=attempt_limit,
                    max_limit=per_query_limit,
                    recover_step=limit_recovery_step,
                )

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
                    total_hits += 1
                    post_hits.append(normalized)
                    if media_id in self._seen_ids:
                        continue
                    new_media_hits += 1
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
        self._persist_state(
            quota_state=client.quota_state(),
            last_poll_at=_iso_now(),
            last_cycle={
                "terms_count": len(terms),
                "search_types": normalized_types,
                "query_attempts": int(query_attempts),
                "query_success": int(query_success),
                "query_degraded": int(query_degraded),
                "reduce_data_errors": int(reduce_data_errors),
                "rate_limit_errors": int(rate_limit_errors),
                "rate_limited_cycle": bool(rate_limited_cycle),
                "timeout_errors": int(timeout_errors),
                "timeout_aborted_cycle": bool(timeout_aborted_cycle),
                "total_hits": int(total_hits),
                "new_media_hits": int(new_media_hits),
                "events_emitted": int(len(events)),
                "trend_snapshots": int(len(trend_rows)),
            },
        )
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

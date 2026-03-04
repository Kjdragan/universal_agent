"""Threads owned-account adapter (phase 1 read path)."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from csi_ingester.adapters.base import RawEvent, SourceAdapter
from csi_ingester.adapters.threads_api import (
    ThreadsAPIClient,
    insights_to_metric_map,
    normalize_threads_item,
    stable_hash,
)
from csi_ingester.contract import CreatorSignalEvent

logger = logging.getLogger(__name__)


class ThreadsOwnedAdapter(SourceAdapter):
    """Ingest own-account posts, mentions, replies, and media insights."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._load_state_fn = lambda source_key: None
        self._save_state_fn = lambda source_key, state: None
        self._state_key = "threads_owned:state"
        self._max_seen_cache = max(200, int(config.get("max_seen_cache", 4000)))
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
        payload = {
            "seen_ids": sorted(self._seen_ids)[: self._max_seen_cache],
            "quota_state": quota_state,
            "last_poll_at": last_poll_at,
        }
        self._save_state_fn(self._state_key, payload)

    async def fetch_events(self) -> list[RawEvent]:
        state = self._hydrate_state()
        quota_state = state.get("quota_state") if isinstance(state.get("quota_state"), dict) else {}
        client = ThreadsAPIClient.from_config(self.config, quota_state=quota_state)
        if not client.is_configured():
            logger.info("Threads owned adapter skipped: missing THREADS_USER_ID or THREADS_ACCESS_TOKEN")
            return []

        limit = max(1, min(int(self.config.get("limit", 25)), 100))
        include_mentions = bool(self.config.get("include_mentions", True))
        include_replies = bool(self.config.get("include_replies", True))
        include_insights = bool(self.config.get("include_insights", True))
        max_replies_per_post = max(1, min(int(self.config.get("replies_limit", 15)), 100))

        events: list[RawEvent] = []
        try:
            own_posts = await client.get_user_threads(limit=limit)
        except Exception as exc:
            logger.warning("Threads owned get_user_threads failed error=%s", exc)
            own_posts = []

        for item in own_posts:
            normalized = normalize_threads_item(item)
            media_id = str(normalized.get("media_id") or "").strip()
            if not media_id or media_id in self._seen_ids:
                continue
            insights: dict[str, int] = {}
            if include_insights:
                try:
                    insights_payload = await client.get_media_insights(media_id)
                    insights = insights_to_metric_map(insights_payload)
                except Exception as exc:
                    logger.warning("Threads owned insights failed media_id=%s error=%s", media_id, exc)
            normalized["insights"] = insights
            events.append(
                RawEvent(
                    source="threads_owned",
                    event_type="threads_post_observed",
                    payload=normalized,
                    occurred_at=str(normalized.get("timestamp") or _iso_now()),
                )
            )
            self._seen_ids.add(media_id)

            if include_replies:
                try:
                    replies = await client.get_replies(media_id, limit=max_replies_per_post)
                except Exception as exc:
                    logger.warning("Threads owned replies fetch failed media_id=%s error=%s", media_id, exc)
                    replies = []
                for reply in replies:
                    reply_norm = normalize_threads_item(reply)
                    reply_id = str(reply_norm.get("media_id") or "").strip()
                    if not reply_id or reply_id in self._seen_ids:
                        continue
                    reply_norm["parent_media_id"] = media_id
                    events.append(
                        RawEvent(
                            source="threads_owned",
                            event_type="threads_reply_observed",
                            payload=reply_norm,
                            occurred_at=str(reply_norm.get("timestamp") or _iso_now()),
                        )
                    )
                    self._seen_ids.add(reply_id)

        if include_mentions:
            try:
                mentions = await client.get_mentions(limit=limit)
            except Exception as exc:
                logger.warning("Threads owned mentions fetch failed error=%s", exc)
                mentions = []
            for mention in mentions:
                normalized = normalize_threads_item(mention)
                media_id = str(normalized.get("media_id") or "").strip()
                if not media_id or media_id in self._seen_ids:
                    continue
                events.append(
                    RawEvent(
                        source="threads_owned",
                        event_type="threads_mention_observed",
                        payload=normalized,
                        occurred_at=str(normalized.get("timestamp") or _iso_now()),
                    )
                )
                self._seen_ids.add(media_id)

        self._seen_ids = set(sorted(self._seen_ids)[: self._max_seen_cache])
        self._persist_state(quota_state=client.quota_state(), last_poll_at=_iso_now())
        return events

    def normalize(self, raw: RawEvent) -> CreatorSignalEvent:
        now_iso = _iso_now()
        payload = raw.payload if isinstance(raw.payload, dict) else {}
        media_id = str(payload.get("media_id") or "").strip()

        event = CreatorSignalEvent(
            event_id=(
                f"threads:owned:{raw.event_type}:{media_id}:{int(datetime.now(timezone.utc).timestamp())}"
                if media_id
                else f"threads:owned:{raw.event_type}:{stable_hash([raw.event_type, raw.occurred_at, str(payload)])}"
            ),
            dedupe_key="",
            source="threads_owned",
            event_type=str(raw.event_type or "threads_post_observed"),
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
                "shortcode": str(payload.get("shortcode") or ""),
                "reply_count": int(payload.get("reply_count") or 0),
                "repost_count": int(payload.get("repost_count") or 0),
                "quote_count": int(payload.get("quote_count") or 0),
                "like_count": int(payload.get("like_count") or 0),
                "insights": payload.get("insights") if isinstance(payload.get("insights"), dict) else {},
                "parent_media_id": str(payload.get("parent_media_id") or ""),
            },
            routing={
                "pipeline": "creator_watchlist_handler",
                "priority": "standard",
                "tags": ["threads", "owned"],
            },
            metadata={"source_adapter": "threads_owned_v1"},
        )
        event.dedupe_key = self.get_dedupe_key(event)
        return event

    def get_dedupe_key(self, event: CreatorSignalEvent) -> str:
        media_id = str(event.subject.get("media_id") or "").strip()
        if media_id:
            return f"threads:{media_id}"
        return f"threads:owned:{stable_hash([event.event_type, event.occurred_at, str(event.subject)])}"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

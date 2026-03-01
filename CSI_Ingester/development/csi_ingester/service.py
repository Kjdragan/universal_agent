"""CSI ingestion service orchestration."""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

from csi_ingester.adapters.base import SourceAdapter
from csi_ingester.adapters.reddit_discovery import RedditDiscoveryAdapter
from csi_ingester.adapters.youtube_channel_rss import YouTubeChannelRSSAdapter
from csi_ingester.adapters.youtube_playlist import YouTubePlaylistAdapter
from csi_ingester.config import CSIConfig
from csi_ingester.emitter.ua_client import UAEmitter
from csi_ingester.metrics import MetricsRegistry
from csi_ingester.scheduler import PollingScheduler
from csi_ingester.store import dedupe as dedupe_store
from csi_ingester.store import delivery_attempts as delivery_attempt_store
from csi_ingester.store import dlq as dlq_store
from csi_ingester.store import events as event_store
from csi_ingester.store import source_state as source_state_store

logger = logging.getLogger(__name__)


class CSIService:
    def __init__(self, *, config: CSIConfig, conn: sqlite3.Connection, metrics: MetricsRegistry) -> None:
        self.config = config
        self.conn = conn
        self.metrics = metrics
        self.scheduler = PollingScheduler()
        self.adapters: dict[str, SourceAdapter] = {}
        self.emitter: UAEmitter | None = None

    async def start(self) -> None:
        self._build_adapters()
        self._build_emitter()
        for name, adapter in self.adapters.items():
            interval = self._poll_interval_for_adapter(name)
            self.scheduler.add_job(name, interval, lambda adapter=adapter, name=name: self._poll_adapter(name, adapter))
        logger.info("CSI service started adapters=%s", ",".join(sorted(self.adapters.keys())))

    async def stop(self) -> None:
        await self.scheduler.stop()

    def _build_adapters(self) -> None:
        sources = self.config.raw.get("sources") if isinstance(self.config.raw, dict) else {}
        if not isinstance(sources, dict):
            return
        yt_playlist_cfg = sources.get("youtube_playlist")
        if isinstance(yt_playlist_cfg, dict) and yt_playlist_cfg.get("enabled", False):
            self.adapters["youtube_playlist"] = YouTubePlaylistAdapter(yt_playlist_cfg)
        yt_rss_cfg = sources.get("youtube_channel_rss")
        if isinstance(yt_rss_cfg, dict) and yt_rss_cfg.get("enabled", False):
            self.adapters["youtube_channel_rss"] = YouTubeChannelRSSAdapter(yt_rss_cfg)
        reddit_cfg = sources.get("reddit_discovery")
        if isinstance(reddit_cfg, dict) and reddit_cfg.get("enabled", False):
            self.adapters["reddit_discovery"] = RedditDiscoveryAdapter(reddit_cfg)
        for adapter in self.adapters.values():
            if hasattr(adapter, "set_state_backend"):
                adapter.set_state_backend(
                    lambda source_key, conn=self.conn: source_state_store.get_state(conn, source_key),
                    lambda source_key, state, conn=self.conn: source_state_store.set_state(conn, source_key, state),
                )

    def _build_emitter(self) -> None:
        endpoint = self.config.ua_endpoint
        secret = self.config.ua_shared_secret
        if endpoint and secret:
            self.emitter = UAEmitter(endpoint=endpoint, shared_secret=secret, instance_id=self.config.instance_id)
        else:
            self.emitter = None

    def _poll_interval_for_adapter(self, name: str) -> float:
        sources = self.config.raw.get("sources") if isinstance(self.config.raw, dict) else {}
        if not isinstance(sources, dict):
            return 60.0
        cfg = sources.get(name)
        if not isinstance(cfg, dict):
            return 60.0
        return max(5.0, float(cfg.get("poll_interval_seconds", 60)))

    def _record_adapter_health(
        self,
        *,
        adapter_name: str,
        ok: bool,
        fetched: int = 0,
        stored: int = 0,
        deduped: int = 0,
        normalized_errors: int = 0,
        delivered: int = 0,
        dlq: int = 0,
        emit_disabled: int = 0,
        error: str = "",
        started_at: float,
        finished_at: float,
    ) -> None:
        key = f"adapter_health:{adapter_name}"
        now_iso = _iso_now()
        prev = source_state_store.get_state(self.conn, key) or {}
        if not isinstance(prev, dict):
            prev = {}
        consecutive_failures = int(prev.get("consecutive_failures") or 0)
        if ok:
            consecutive_failures = 0
        else:
            consecutive_failures += 1
        state = {
            "adapter": adapter_name,
            "ok": bool(ok),
            "last_poll_started_at": _iso_from_epoch(started_at),
            "last_poll_completed_at": _iso_from_epoch(finished_at),
            "last_poll_duration_ms": int(max(0.0, (finished_at - started_at) * 1000)),
            "last_success_at": now_iso if ok else str(prev.get("last_success_at") or ""),
            "last_error_at": now_iso if not ok else str(prev.get("last_error_at") or ""),
            "last_error": error[:600] if error else "",
            "consecutive_failures": consecutive_failures,
            "last_cycle": {
                "fetched": int(max(0, fetched)),
                "stored": int(max(0, stored)),
                "deduped": int(max(0, deduped)),
                "normalized_errors": int(max(0, normalized_errors)),
                "delivered": int(max(0, delivered)),
                "dlq": int(max(0, dlq)),
                "emit_disabled": int(max(0, emit_disabled)),
            },
            "totals": {
                "polls": int(prev.get("totals", {}).get("polls", 0)) + 1 if isinstance(prev.get("totals"), dict) else 1,
                "successes": int(prev.get("totals", {}).get("successes", 0)) + (1 if ok else 0)
                if isinstance(prev.get("totals"), dict)
                else (1 if ok else 0),
                "failures": int(prev.get("totals", {}).get("failures", 0)) + (0 if ok else 1)
                if isinstance(prev.get("totals"), dict)
                else (0 if ok else 1),
                "fetched": int(prev.get("totals", {}).get("fetched", 0)) + int(max(0, fetched))
                if isinstance(prev.get("totals"), dict)
                else int(max(0, fetched)),
                "stored": int(prev.get("totals", {}).get("stored", 0)) + int(max(0, stored))
                if isinstance(prev.get("totals"), dict)
                else int(max(0, stored)),
                "deduped": int(prev.get("totals", {}).get("deduped", 0)) + int(max(0, deduped))
                if isinstance(prev.get("totals"), dict)
                else int(max(0, deduped)),
                "normalized_errors": int(prev.get("totals", {}).get("normalized_errors", 0)) + int(max(0, normalized_errors))
                if isinstance(prev.get("totals"), dict)
                else int(max(0, normalized_errors)),
                "delivered": int(prev.get("totals", {}).get("delivered", 0)) + int(max(0, delivered))
                if isinstance(prev.get("totals"), dict)
                else int(max(0, delivered)),
                "dlq": int(prev.get("totals", {}).get("dlq", 0)) + int(max(0, dlq))
                if isinstance(prev.get("totals"), dict)
                else int(max(0, dlq)),
                "emit_disabled": int(prev.get("totals", {}).get("emit_disabled", 0)) + int(max(0, emit_disabled))
                if isinstance(prev.get("totals"), dict)
                else int(max(0, emit_disabled)),
            },
        }
        source_state_store.set_state(self.conn, key, state)

    async def _poll_adapter(self, adapter_name: str, adapter: SourceAdapter) -> None:
        self.metrics.inc("csi.poll.cycles")
        started_at = time.time()
        fetched_count = 0
        stored_count = 0
        deduped_count = 0
        normalized_error_count = 0
        delivered_count = 0
        dlq_count = 0
        emit_disabled_count = 0
        try:
            raw_events = await adapter.fetch_events()
        except Exception as exc:
            self.metrics.inc("csi.poll.errors")
            self._record_adapter_health(
                adapter_name=adapter_name,
                ok=False,
                error=f"fetch_events:{type(exc).__name__}:{exc}",
                started_at=started_at,
                finished_at=time.time(),
            )
            raise

        fetched_count = len(raw_events)
        self.metrics.inc("csi.events.fetched", fetched_count)
        for raw in raw_events:
            try:
                event = adapter.normalize(raw)
            except Exception as exc:
                normalized_error_count += 1
                self.metrics.inc("csi.events.normalize_errors")
                logger.warning(
                    "CSI normalize failed adapter=%s source=%s event_type=%s error=%s",
                    adapter_name,
                    raw.source,
                    raw.event_type,
                    exc,
                )
                continue
            if dedupe_store.has_key(self.conn, event.dedupe_key):
                self.metrics.inc("csi.events.deduped")
                deduped_count += 1
                continue
            dedupe_store.upsert_key(self.conn, event.dedupe_key, ttl_days=90)
            event_store.insert_event(self.conn, event)
            self.metrics.inc("csi.events.stored")
            stored_count += 1
            if self.emitter is None:
                emit_disabled_count += 1
                delivery_attempt_store.record_attempt(
                    self.conn,
                    event_id=event.event_id,
                    target="ua_signals_ingest",
                    delivered=False,
                    status_code=503,
                    payload={"error": "ua_delivery_not_configured"},
                )
                dlq_store.enqueue(
                    self.conn,
                    event_id=event.event_id,
                    event=event.model_dump(),
                    error_reason="ua_delivery_not_configured",
                    retry_count=3,
                )
                self.metrics.inc("csi.events.dlq")
                dlq_count += 1
                continue
            delivered, status_code, payload = await self.emitter.emit_with_retries([event])
            delivery_attempt_store.record_attempt(
                self.conn,
                event_id=event.event_id,
                target="ua_signals_ingest",
                delivered=bool(delivered),
                status_code=int(status_code or 0),
                payload=payload if isinstance(payload, dict) else {"payload": payload},
            )
            if delivered:
                event_store.mark_delivered(self.conn, event.event_id)
                self.metrics.inc("csi.events.delivered")
                delivered_count += 1
            else:
                dlq_store.enqueue(
                    self.conn,
                    event_id=event.event_id,
                    event=event.model_dump(),
                    error_reason=f"ua_status_{status_code}",
                    retry_count=3,
                )
                self.metrics.inc("csi.events.dlq")
                dlq_count += 1
                logger.warning(
                    "CSI emit failed adapter=%s event_id=%s status=%s payload=%s",
                    adapter_name,
                    event.event_id,
                    status_code,
                    payload,
                )
        self._record_adapter_health(
            adapter_name=adapter_name,
            ok=True,
            fetched=fetched_count,
            stored=stored_count,
            deduped=deduped_count,
            normalized_errors=normalized_error_count,
            delivered=delivered_count,
            dlq=dlq_count,
            emit_disabled=emit_disabled_count,
            started_at=started_at,
            finished_at=time.time(),
        )


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_from_epoch(epoch_seconds: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

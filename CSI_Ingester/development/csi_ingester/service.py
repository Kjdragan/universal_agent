"""CSI ingestion service orchestration."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from csi_ingester.adapters.base import SourceAdapter
from csi_ingester.adapters.youtube_channel_rss import YouTubeChannelRSSAdapter
from csi_ingester.adapters.youtube_playlist import YouTubePlaylistAdapter
from csi_ingester.config import CSIConfig
from csi_ingester.emitter.ua_client import UAEmitter
from csi_ingester.metrics import MetricsRegistry
from csi_ingester.scheduler import PollingScheduler
from csi_ingester.store import dedupe as dedupe_store
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

    async def _poll_adapter(self, adapter_name: str, adapter: SourceAdapter) -> None:
        self.metrics.inc("csi.poll.cycles")
        raw_events = await adapter.fetch_events()
        self.metrics.inc("csi.events.fetched", len(raw_events))
        for raw in raw_events:
            event = adapter.normalize(raw)
            if dedupe_store.has_key(self.conn, event.dedupe_key):
                self.metrics.inc("csi.events.deduped")
                continue
            dedupe_store.upsert_key(self.conn, event.dedupe_key, ttl_days=90)
            event_store.insert_event(self.conn, event)
            self.metrics.inc("csi.events.stored")
            if self.emitter is None:
                continue
            delivered, status_code, payload = await self.emitter.emit_with_retries([event])
            if delivered:
                event_store.mark_delivered(self.conn, event.event_id)
                self.metrics.inc("csi.events.delivered")
            else:
                dlq_store.enqueue(
                    self.conn,
                    event_id=event.event_id,
                    event=event.model_dump(),
                    error_reason=f"ua_status_{status_code}",
                    retry_count=3,
                )
                self.metrics.inc("csi.events.dlq")
                logger.warning(
                    "CSI emit failed adapter=%s event_id=%s status=%s payload=%s",
                    adapter_name,
                    event.event_id,
                    status_code,
                    payload,
                )

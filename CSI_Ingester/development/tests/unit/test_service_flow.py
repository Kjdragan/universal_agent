from __future__ import annotations

from pathlib import Path

from csi_ingester.adapters.base import RawEvent, SourceAdapter
from csi_ingester.config import CSIConfig
from csi_ingester.contract import CreatorSignalEvent
from csi_ingester.metrics import MetricsRegistry
from csi_ingester.service import CSIService
from csi_ingester.store.sqlite import connect, ensure_schema


class _FakeAdapter(SourceAdapter):
    def __init__(self):
        self._raw_events = [
            RawEvent(
                source="youtube_playlist",
                event_type="video_added_to_playlist",
                payload={"video_id": "abc", "playlist_id": "pl1"},
                occurred_at="2026-02-22T00:00:00Z",
            )
        ]

    async def fetch_events(self) -> list[RawEvent]:
        return list(self._raw_events)

    def normalize(self, raw: RawEvent) -> CreatorSignalEvent:
        return CreatorSignalEvent(
            event_id="evt-1",
            dedupe_key="youtube:video:abc:pl1",
            source="youtube_playlist",
            event_type="video_added_to_playlist",
            occurred_at=raw.occurred_at,
            received_at="2026-02-22T00:00:01Z",
            subject={
                "platform": "youtube",
                "video_id": "abc",
                "playlist_id": "pl1",
                "url": "https://youtube.com/watch?v=abc",
                "title": "t",
                "channel_id": "c",
                "published_at": "2026-02-22T00:00:00Z",
            },
            routing={"pipeline": "youtube_tutorial_explainer", "priority": "urgent"},
        )

    def get_dedupe_key(self, event: CreatorSignalEvent) -> str:
        return event.dedupe_key


class _EmitterOK:
    async def emit_with_retries(self, events, max_attempts=3):
        return True, 200, {"ok": True}


class _EmitterFail:
    async def emit_with_retries(self, events, max_attempts=3):
        return False, 503, {"ok": False}


class _AdapterFetchFail(SourceAdapter):
    async def fetch_events(self) -> list[RawEvent]:
        raise RuntimeError("fetch boom")

    def normalize(self, raw: RawEvent) -> CreatorSignalEvent:
        raise AssertionError("normalize should not be called")

    def get_dedupe_key(self, event: CreatorSignalEvent) -> str:
        return "unused"


def _service(tmp_path: Path) -> tuple[CSIService, object]:
    config = CSIConfig(
        raw={
            "csi": {"instance_id": "csi-test"},
            "storage": {"db_path": str(tmp_path / "csi.db")},
            "sources": {},
            "delivery": {},
        }
    )
    conn = connect(tmp_path / "csi.db")
    ensure_schema(conn)
    service = CSIService(config=config, conn=conn, metrics=MetricsRegistry())
    return service, conn


async def test_service_stores_and_dedupes(tmp_path):
    """_poll_adapter stores events locally and dedupes on second call.

    Events are NOT emitted per-poll any more; emission happens
    via the batch-brief scheduler.
    """
    service, conn = _service(tmp_path)
    adapter = _FakeAdapter()
    service.emitter = _EmitterOK()

    await service._poll_adapter("fake", adapter)
    row = conn.execute("SELECT delivered FROM events WHERE event_id = 'evt-1'").fetchone()
    assert row is not None
    # Event is stored but NOT delivered (delivered=0) — batch brief will mark it later
    assert int(row["delivered"]) == 0

    # Second poll: same event is deduped, no second insert
    await service._poll_adapter("fake", adapter)
    rows = conn.execute("SELECT COUNT(*) AS c FROM events WHERE event_id = 'evt-1'").fetchone()
    assert int(rows["c"]) == 1


async def test_service_stores_without_emitter(tmp_path):
    """When emitter is None, events are still stored normally.

    No DLQ is created — the batch brief will emit them later.
    """
    service, conn = _service(tmp_path)
    adapter = _FakeAdapter()
    service.emitter = None

    await service._poll_adapter("fake", adapter)
    row = conn.execute("SELECT COUNT(*) AS c FROM events WHERE event_id = 'evt-1'").fetchone()
    assert int(row["c"]) == 1
    # No dead-letter entries created
    dlq = conn.execute("SELECT COUNT(*) AS c FROM dead_letter").fetchone()
    assert int(dlq["c"]) == 0

    state = conn.execute(
        "SELECT state_json FROM source_state WHERE source_key = 'adapter_health:fake' LIMIT 1"
    ).fetchone()
    assert state is not None
    import json as _json

    parsed = _json.loads(str(state["state_json"]))
    assert bool(parsed.get("ok")) is True


async def test_service_records_adapter_health_on_fetch_failure(tmp_path):
    service, conn = _service(tmp_path)
    adapter = _AdapterFetchFail()
    service.emitter = _EmitterOK()

    import pytest

    with pytest.raises(RuntimeError):
        await service._poll_adapter("fetch_fail", adapter)
    state = conn.execute(
        "SELECT state_json FROM source_state WHERE source_key = 'adapter_health:fetch_fail' LIMIT 1"
    ).fetchone()
    assert state is not None
    import json as _json

    parsed = _json.loads(str(state["state_json"]))
    assert bool(parsed.get("ok")) is False
    assert int(parsed.get("consecutive_failures") or 0) >= 1
    assert "fetch_events:RuntimeError" in str(parsed.get("last_error") or "")


def test_service_builds_threads_adapters_when_enabled(tmp_path):
    config = CSIConfig(
        raw={
            "csi": {"instance_id": "csi-test"},
            "storage": {"db_path": str(tmp_path / "csi.db")},
            "sources": {
                "threads_owned": {"enabled": True},
                "threads_trends_seeded": {"enabled": True},
                "threads_trends_broad": {"enabled": True},
            },
            "delivery": {},
        }
    )
    conn = connect(tmp_path / "csi.db")
    ensure_schema(conn)
    service = CSIService(config=config, conn=conn, metrics=MetricsRegistry())
    service._build_adapters()
    assert "threads_owned" in service.adapters
    assert "threads_trends_seeded" in service.adapters
    assert "threads_trends_broad" in service.adapters

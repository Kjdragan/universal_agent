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


async def test_service_delivers_and_dedupes(tmp_path):
    service, conn = _service(tmp_path)
    adapter = _FakeAdapter()
    service.emitter = _EmitterOK()

    await service._poll_adapter("fake", adapter)
    delivered = conn.execute("SELECT delivered FROM events WHERE event_id = 'evt-1'").fetchone()
    assert delivered is not None
    assert int(delivered["delivered"]) == 1
    attempts = conn.execute(
        "SELECT delivered, status_code, target FROM delivery_attempts WHERE event_id = 'evt-1' ORDER BY id ASC"
    ).fetchall()
    assert len(attempts) == 1
    assert int(attempts[0]["delivered"]) == 1
    assert int(attempts[0]["status_code"]) == 200
    assert str(attempts[0]["target"]) == "ua_signals_ingest"

    await service._poll_adapter("fake", adapter)
    rows = conn.execute("SELECT COUNT(*) AS c FROM events WHERE event_id = 'evt-1'").fetchone()
    assert int(rows["c"]) == 1
    attempt_count = conn.execute("SELECT COUNT(*) AS c FROM delivery_attempts WHERE event_id = 'evt-1'").fetchone()
    # Dedupe prevents a second outbound emit attempt for the same event.
    assert int(attempt_count["c"]) == 1


async def test_service_moves_failed_emit_to_dlq(tmp_path):
    service, conn = _service(tmp_path)
    adapter = _FakeAdapter()
    service.emitter = _EmitterFail()

    await service._poll_adapter("fake", adapter)
    dlq = conn.execute("SELECT COUNT(*) AS c FROM dead_letter").fetchone()
    assert int(dlq["c"]) == 1
    attempts = conn.execute(
        "SELECT delivered, status_code, error_class FROM delivery_attempts WHERE event_id = 'evt-1' ORDER BY id ASC"
    ).fetchall()
    assert len(attempts) == 1
    assert int(attempts[0]["delivered"]) == 0
    assert int(attempts[0]["status_code"]) == 503
    assert str(attempts[0]["error_class"]) == "upstream_5xx"

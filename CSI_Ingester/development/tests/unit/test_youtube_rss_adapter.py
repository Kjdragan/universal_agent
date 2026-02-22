from __future__ import annotations

import json
from pathlib import Path

from csi_ingester.adapters.youtube_channel_rss import YouTubeChannelRSSAdapter


def _cfg() -> dict:
    return {
        "watchlist": [{"channel_id": "UC_TEST"}],
        "poll_interval_seconds": 300,
        "seed_on_first_run": True,
    }


async def test_rss_adapter_seeds_then_emits_new(monkeypatch):
    adapter = YouTubeChannelRSSAdapter(_cfg())
    responses = [
        [
            {
                "video_id": "v_old",
                "channel_id": "UC_TEST",
                "url": "https://youtube.com/watch?v=v_old",
                "title": "Old",
                "published_at": "2026-02-22T00:00:00Z",
                "occurred_at": "2026-02-22T00:00:00Z",
            }
        ],
        [
            {
                "video_id": "v_new",
                "channel_id": "UC_TEST",
                "url": "https://youtube.com/watch?v=v_new",
                "title": "New",
                "published_at": "2026-02-22T00:10:00Z",
                "occurred_at": "2026-02-22T00:10:00Z",
            },
            {
                "video_id": "v_old",
                "channel_id": "UC_TEST",
                "url": "https://youtube.com/watch?v=v_old",
                "title": "Old",
                "published_at": "2026-02-22T00:00:00Z",
                "occurred_at": "2026-02-22T00:00:00Z",
            },
        ],
    ]

    async def _fake_fetch(client, *, channel_id):
        return responses.pop(0)

    monkeypatch.setattr(adapter, "_fetch_channel_entries", _fake_fetch)

    first = await adapter.fetch_events()
    assert first == []
    second = await adapter.fetch_events()
    assert len(second) == 1
    event = adapter.normalize(second[0])
    assert event.subject["video_id"] == "v_new"
    assert event.dedupe_key == "youtube:video:v_new"


async def test_rss_adapter_persists_seed_state_across_restart(monkeypatch):
    state_store: dict[str, dict] = {}

    def _load_state(key: str):
        return state_store.get(key)

    def _save_state(key: str, state: dict):
        state_store[key] = state

    first_adapter = YouTubeChannelRSSAdapter(_cfg())
    first_adapter.set_state_backend(_load_state, _save_state)
    responses_first = [
        [
            {
                "video_id": "v_old",
                "channel_id": "UC_TEST",
                "url": "https://youtube.com/watch?v=v_old",
                "title": "Old",
                "published_at": "2026-02-22T00:00:00Z",
                "occurred_at": "2026-02-22T00:00:00Z",
            }
        ]
    ]

    async def _fake_fetch_first(client, *, channel_id):
        return responses_first.pop(0)

    monkeypatch.setattr(first_adapter, "_fetch_channel_entries", _fake_fetch_first)
    assert await first_adapter.fetch_events() == []
    assert state_store["youtube_channel_rss:UC_TEST"]["seeded"] is True

    second_adapter = YouTubeChannelRSSAdapter(_cfg())
    second_adapter.set_state_backend(_load_state, _save_state)
    responses_second = [
        [
            {
                "video_id": "v_new",
                "channel_id": "UC_TEST",
                "url": "https://youtube.com/watch?v=v_new",
                "title": "New",
                "published_at": "2026-02-22T00:10:00Z",
                "occurred_at": "2026-02-22T00:10:00Z",
            },
            {
                "video_id": "v_old",
                "channel_id": "UC_TEST",
                "url": "https://youtube.com/watch?v=v_old",
                "title": "Old",
                "published_at": "2026-02-22T00:00:00Z",
                "occurred_at": "2026-02-22T00:00:00Z",
            },
        ]
    ]

    async def _fake_fetch_second(client, *, channel_id):
        return responses_second.pop(0)

    monkeypatch.setattr(second_adapter, "_fetch_channel_entries", _fake_fetch_second)
    events = await second_adapter.fetch_events()
    assert len(events) == 1
    assert events[0].payload["video_id"] == "v_new"


def test_rss_adapter_loads_watchlist_from_json_file(tmp_path: Path):
    payload = {
        "channels": [
            {"channel_id": "UC_ONE"},
            {"channel_id": "UC_TWO"},
            {"channel_id": "UC_ONE"},
        ]
    }
    watchlist_file = tmp_path / "channels_watchlist.json"
    watchlist_file.write_text(json.dumps(payload), encoding="utf-8")
    adapter = YouTubeChannelRSSAdapter({"watchlist_file": str(watchlist_file), "watchlist": []})
    resolved = adapter._resolve_watchlist()
    assert [item["channel_id"] for item in resolved] == ["UC_ONE", "UC_TWO"]

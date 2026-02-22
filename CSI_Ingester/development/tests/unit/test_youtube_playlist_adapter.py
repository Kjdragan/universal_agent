from __future__ import annotations

from csi_ingester.adapters.youtube_playlist import YouTubePlaylistAdapter


def _cfg() -> dict:
    return {
        "api_key_env": "YOUTUBE_API_KEY",
        "playlists": [{"id": "PL_TEST"}],
        "poll_interval_seconds": 30,
        "adaptive_polling": {
            "enabled": True,
            "active_interval_seconds": 30,
            "idle_interval_seconds": 300,
            "activity_threshold_minutes": 15,
            "min_quota_buffer": 1000,
        },
        "quota_limit": 10000,
        "seed_on_first_run": True,
    }


async def test_playlist_adapter_no_api_key(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    adapter = YouTubePlaylistAdapter(_cfg())
    events = await adapter.fetch_events()
    assert events == []


async def test_playlist_adapter_seeds_then_emits_new(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "key")
    adapter = YouTubePlaylistAdapter(_cfg())
    responses = [
        [
            {
                "playlist_id": "PL_TEST",
                "video_id": "vid_old",
                "title": "Old",
                "channel_id": "UC1",
                "published_at": "2026-02-22T00:00:00Z",
                "occurred_at": "2026-02-22T00:00:00Z",
                "url": "https://youtube.com/watch?v=vid_old",
            }
        ],
        [
            {
                "playlist_id": "PL_TEST",
                "video_id": "vid_new",
                "title": "New",
                "channel_id": "UC1",
                "published_at": "2026-02-22T00:10:00Z",
                "occurred_at": "2026-02-22T00:10:00Z",
                "url": "https://youtube.com/watch?v=vid_new",
            },
            {
                "playlist_id": "PL_TEST",
                "video_id": "vid_old",
                "title": "Old",
                "channel_id": "UC1",
                "published_at": "2026-02-22T00:00:00Z",
                "occurred_at": "2026-02-22T00:00:00Z",
                "url": "https://youtube.com/watch?v=vid_old",
            },
        ],
    ]

    async def _fake_fetch(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(adapter, "_fetch_playlist_items", _fake_fetch)

    first = await adapter.fetch_events()
    assert first == []

    state = adapter._state_by_playlist.get("PL_TEST") or {}
    state["next_check_ts"] = 0.0
    adapter._state_by_playlist["PL_TEST"] = state

    second = await adapter.fetch_events()
    assert len(second) == 1
    assert second[0].payload["video_id"] == "vid_new"


async def test_playlist_adapter_persists_seed_state_across_restart(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "key")
    state_store: dict[str, dict] = {}

    def _load_state(key: str):
        return state_store.get(key)

    def _save_state(key: str, state: dict):
        state_store[key] = state

    first_adapter = YouTubePlaylistAdapter(_cfg())
    first_adapter.set_state_backend(_load_state, _save_state)

    responses_first = [
        [
            {
                "playlist_id": "PL_TEST",
                "video_id": "vid_old",
                "title": "Old",
                "channel_id": "UC1",
                "published_at": "2026-02-22T00:00:00Z",
                "occurred_at": "2026-02-22T00:00:00Z",
                "url": "https://youtube.com/watch?v=vid_old",
            }
        ]
    ]

    async def _fake_fetch_first(*args, **kwargs):
        return responses_first.pop(0)

    monkeypatch.setattr(first_adapter, "_fetch_playlist_items", _fake_fetch_first)
    assert await first_adapter.fetch_events() == []
    assert state_store["youtube_playlist:PL_TEST"]["seeded"] is True
    state_store["youtube_playlist:PL_TEST"]["poll_state"]["next_check_ts"] = 0.0

    second_adapter = YouTubePlaylistAdapter(_cfg())
    second_adapter.set_state_backend(_load_state, _save_state)
    responses_second = [
        [
            {
                "playlist_id": "PL_TEST",
                "video_id": "vid_new",
                "title": "New",
                "channel_id": "UC1",
                "published_at": "2026-02-22T00:10:00Z",
                "occurred_at": "2026-02-22T00:10:00Z",
                "url": "https://youtube.com/watch?v=vid_new",
            },
            {
                "playlist_id": "PL_TEST",
                "video_id": "vid_old",
                "title": "Old",
                "channel_id": "UC1",
                "published_at": "2026-02-22T00:00:00Z",
                "occurred_at": "2026-02-22T00:00:00Z",
                "url": "https://youtube.com/watch?v=vid_old",
            },
        ]
    ]

    async def _fake_fetch_second(*args, **kwargs):
        return responses_second.pop(0)

    monkeypatch.setattr(second_adapter, "_fetch_playlist_items", _fake_fetch_second)
    events = await second_adapter.fetch_events()
    assert len(events) == 1
    assert events[0].payload["video_id"] == "vid_new"

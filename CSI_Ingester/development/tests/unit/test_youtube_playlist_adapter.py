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


async def test_playlist_adapter_truncates_to_max_seen_cache(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "key")
    config = _cfg()
    # Adapter forces a MINIMUM of 50 for max_seen_cache
    config["max_seen_cache_per_playlist"] = 10  
    adapter = YouTubePlaylistAdapter(config)

    # API returns 55 items. 
    mock_items = []
    for i in range(55):
        mock_items.append({
            "playlist_id": "PL_TEST",
            "video_id": f"vid_{i}",
            "title": f"Video {i}",
            # Create a sequence of timestamps 1 minute apart
            "occurred_at": f"2026-02-22T00:{i:02d}:00Z" if i < 60 else f"2026-02-22T01:{i-60:02d}:00Z"
        })
    responses = [mock_items]

    async def _fake_fetch(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(adapter, "_fetch_playlist_items", _fake_fetch)
    events = await adapter.fetch_events()
    
    # max_seen_cache enforces a minimum of 50, so despite returning 55 items,
    # the adapter should ONLY emit and cache the newest 50
    assert len(events) == 0  # First run just seeds the cache
    
    # Check the seen cache size and contents
    seen = adapter._seen_by_playlist["PL_TEST"]
    assert len(seen) == 50
    
    # The newest videos are those with the highest index (i -> 54 is newest)
    assert "vid_54" in seen
    assert "vid_5" in seen
    # The oldest 5 should have been ignored entirely
    assert "vid_0" not in seen
    assert "vid_1" not in seen
    assert "vid_4" not in seen

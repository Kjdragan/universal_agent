from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from universal_agent.services.youtube_playlist_watcher import YouTubePlaylistWatcher, _state_path


@pytest.mark.asyncio
async def test_poll_now_clears_stale_last_error(monkeypatch, tmp_path):
    monkeypatch.setenv("YT_TUTORIALS_PLAYLIST_ID", "PLdemo")
    monkeypatch.setenv("YOUTUBE_API_KEY", "demo-key")
    monkeypatch.setenv("UA_OPS_DIR", str(tmp_path))

    watcher = YouTubePlaylistWatcher(
        dispatch_fn=AsyncMock(return_value=(True, "agent")),
    )
    watcher._last_error = "FileNotFoundError: stale"
    watcher._fetch_playlist_items = AsyncMock(return_value=[])

    result = await watcher.poll_now()

    assert result["ok"] is True
    assert watcher.status()["last_error"] == ""


@pytest.mark.asyncio
async def test_loop_seed_clears_stale_last_error(monkeypatch, tmp_path):
    monkeypatch.setenv("YT_TUTORIALS_PLAYLIST_ID", "PLdemo")
    monkeypatch.setenv("YOUTUBE_API_KEY", "demo-key")
    monkeypatch.setenv("UA_OPS_DIR", str(tmp_path))

    watcher = YouTubePlaylistWatcher(
        dispatch_fn=AsyncMock(return_value=(True, "agent")),
    )
    watcher._last_error = "youtube_api_quota_exceeded"
    watcher._fetch_playlist_items = AsyncMock(
        return_value=[
            {
                "video_id": "vid123",
                "url": "https://www.youtube.com/watch?v=vid123",
                "title": "Seeded via RSS",
                "channel_id": "chan123",
                "occurred_at": "2026-03-12T22:00:00Z",
                "playlist_id": "PLdemo",
            }
        ]
    )
    watcher._sleep_or_stop = AsyncMock(
        side_effect=lambda _seconds: watcher._stop_event.set() or None
    )

    await watcher._loop("PLdemo", "demo-key", set())

    status = watcher.status()
    assert status["last_poll_ok"] is True
    assert status["last_error"] == ""
    assert status["seen_count"] == 1


@pytest.mark.asyncio
async def test_fetch_playlist_items_falls_back_to_rss_when_api_quota_exceeded(monkeypatch, tmp_path):
    monkeypatch.setenv("YT_TUTORIALS_PLAYLIST_ID", "PLdemo")
    monkeypatch.setenv("YOUTUBE_API_KEY", "demo-key")
    monkeypatch.setenv("UA_OPS_DIR", str(tmp_path))

    watcher = YouTubePlaylistWatcher(dispatch_fn=AsyncMock(return_value=(True, "agent")))
    watcher._fetch_playlist_items_via_api = AsyncMock(return_value=None)
    watcher._fetch_playlist_items_via_rss = AsyncMock(
        return_value=[
            {
                "video_id": "vid123",
                "url": "https://www.youtube.com/watch?v=vid123",
                "title": "RSS Fallback",
                "channel_id": "chan123",
                "occurred_at": "2026-03-12T22:00:00Z",
                "playlist_id": "PLdemo",
            }
        ]
    )
    watcher._last_error = "youtube_api_quota_exceeded"

    items = await watcher._fetch_playlist_items("PLdemo", "demo-key")

    assert items and items[0]["video_id"] == "vid123"
    watcher._fetch_playlist_items_via_api.assert_awaited_once()
    watcher._fetch_playlist_items_via_rss.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_now_works_without_api_key_when_rss_is_available(monkeypatch, tmp_path):
    monkeypatch.setenv("YT_TUTORIALS_PLAYLIST_ID", "PLdemo")
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.setenv("UA_OPS_DIR", str(tmp_path))

    watcher = YouTubePlaylistWatcher(dispatch_fn=AsyncMock(return_value=(True, "agent")))
    watcher._fetch_playlist_items = AsyncMock(return_value=[])

    result = await watcher.poll_now()

    assert result["ok"] is True
    watcher._fetch_playlist_items.assert_awaited_once_with("PLdemo", "")


@pytest.mark.asyncio
async def test_poll_now_persists_seen_ids_before_dispatch_side_effects(monkeypatch, tmp_path):
    monkeypatch.setenv("YT_TUTORIALS_PLAYLIST_ID", "PLdemo")
    monkeypatch.setenv("YOUTUBE_API_KEY", "demo-key")
    monkeypatch.setenv("UA_OPS_DIR", str(tmp_path))

    watcher = YouTubePlaylistWatcher(dispatch_fn=AsyncMock(return_value=(True, "agent")))
    watcher._fetch_playlist_items = AsyncMock(
        return_value=[
            {
                "video_id": "vid123",
                "url": "https://www.youtube.com/watch?v=vid123",
                "title": "Crash During Dispatch",
                "channel_id": "chan123",
                "occurred_at": "2026-03-12T22:00:00Z",
                "playlist_id": "PLdemo",
            }
        ]
    )
    watcher._dispatch = AsyncMock(side_effect=RuntimeError("dispatch_crashed"))

    with pytest.raises(RuntimeError, match="dispatch_crashed"):
        await watcher.poll_now()

    saved = json.loads(_state_path().read_text(encoding="utf-8"))
    assert saved["seen_ids"] == ["vid123"]


@pytest.mark.asyncio
async def test_concurrent_poll_now_calls_dispatch_exactly_once(monkeypatch, tmp_path):
    """Regression test: two concurrent poll_now calls on the same new video must
    emit exactly one 'New Tutorial Video Detected' notification and dispatch exactly once.

    This simulates the race that previously caused duplicate Telegram messages when
    the background _loop and a manual poll_now overlapped on the same new video.
    """
    import asyncio

    monkeypatch.setenv("YT_TUTORIALS_PLAYLIST_ID", "PLdemo")
    monkeypatch.setenv("YOUTUBE_API_KEY", "demo-key")
    monkeypatch.setenv("UA_OPS_DIR", str(tmp_path))

    new_item = {
        "video_id": "raceVid1",
        "url": "https://www.youtube.com/watch?v=raceVid1",
        "title": "Race Condition Test Video",
        "channel_id": "chan1",
        "occurred_at": "2026-03-13T22:00:00Z",
        "playlist_id": "PLdemo",
    }

    notifications: list[dict] = []
    dispatch_mock = AsyncMock(return_value=(True, "agent"))

    watcher = YouTubePlaylistWatcher(
        dispatch_fn=dispatch_mock,
        notification_sink=notifications.append,
    )
    # Both concurrent calls see the same unseen video
    watcher._fetch_playlist_items = AsyncMock(return_value=[new_item])

    # Fire two poll_now calls concurrently — this is the race scenario
    results = await asyncio.gather(watcher.poll_now(), watcher.poll_now())

    # Exactly one should have dispatched, the other should have returned 0
    total_dispatched = sum(r["new_dispatched"] for r in results)
    assert total_dispatched == 1, f"Expected 1 dispatch, got {total_dispatched}: {results}"

    # dispatch_fn must be called exactly once
    assert dispatch_mock.await_count == 1

    # Exactly one 'New Tutorial Video Detected' notification
    detected_notifs = [n for n in notifications if n["kind"] == "youtube_playlist_new_video"]
    assert len(detected_notifs) == 1, (
        f"Expected 1 'New Tutorial Video Detected' notification, got {len(detected_notifs)}"
    )

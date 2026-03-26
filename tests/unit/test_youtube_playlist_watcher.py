from __future__ import annotations

import asyncio
import json
import sqlite3
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
async def test_poll_now_persists_seen_and_pending_when_dispatch_crashes(monkeypatch, tmp_path):
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

    result = await watcher.poll_now()

    assert result["ok"] is True
    state_file = _state_path()
    assert state_file.exists()
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert "vid123" in saved.get("seen_ids", [])
    pending = saved.get("pending_dispatch_items") or {}
    assert "vid123" in pending


@pytest.mark.asyncio
async def test_concurrent_poll_now_calls_dispatch_exactly_once(monkeypatch, tmp_path):
    """Regression test: two concurrent poll_now calls on the same new video must
    dispatch exactly once and emit only run-admission notifications.

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
    dispatch_mock = AsyncMock(
        return_value={
            "decision": "accepted",
            "reason": "dispatched",
            "run_id": "run_race_1",
            "attempt_id": "attempt_race_1",
            "attempt_number": 1,
            "workspace_dir": "/tmp/run_race_1",
        }
    )

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

    # Detection-only notifications are intentionally suppressed.
    detected_notifs = [n for n in notifications if n["kind"] == "youtube_playlist_new_video"]
    assert len(detected_notifs) == 0
    # Run-admission progress should still appear exactly once.
    progress_notifs = [n for n in notifications if n["kind"] == "youtube_tutorial_progress"]
    assert len(progress_notifs) == 1


@pytest.mark.asyncio
async def test_poll_now_emits_progress_notification_when_run_admission_is_returned(monkeypatch, tmp_path):
    monkeypatch.setenv("YT_TUTORIALS_PLAYLIST_ID", "PLdemo")
    monkeypatch.setenv("YOUTUBE_API_KEY", "demo-key")
    monkeypatch.setenv("UA_OPS_DIR", str(tmp_path))

    new_item = {
        "video_id": "runAware123",
        "url": "https://www.youtube.com/watch?v=runAware123",
        "title": "Run-aware dispatch video",
        "channel_id": "chan1",
        "occurred_at": "2026-03-25T15:00:00Z",
        "playlist_id": "PLdemo",
    }

    notifications: list[dict] = []
    dispatch_mock = AsyncMock(
        return_value={
            "decision": "accepted",
            "reason": "dispatched",
            "run_id": "run_youtube_123",
            "attempt_id": "attempt_youtube_1",
            "attempt_number": 1,
            "workspace_dir": "/tmp/run_session_hook_yt_demo",
        }
    )

    watcher = YouTubePlaylistWatcher(
        dispatch_fn=dispatch_mock,
        notification_sink=notifications.append,
    )
    watcher._fetch_playlist_items = AsyncMock(return_value=[new_item])

    result = await watcher.poll_now()

    assert result["ok"] is True
    progress = next((n for n in notifications if n.get("kind") == "youtube_tutorial_progress"), None)
    assert progress is not None
    assert "attempt 1 admitted for processing" in str(progress.get("message") or "").lower()
    assert (progress.get("metadata") or {}).get("run_id") == "run_youtube_123"
    assert (progress.get("metadata") or {}).get("attempt_id") == "attempt_youtube_1"


@pytest.mark.asyncio
async def test_dispatch_exception_emits_dispatch_failed_notification(monkeypatch, tmp_path):
    monkeypatch.setenv("YT_TUTORIALS_PLAYLIST_ID", "PLdemo")
    monkeypatch.setenv("YOUTUBE_API_KEY", "demo-key")
    monkeypatch.setenv("UA_OPS_DIR", str(tmp_path))

    notifications: list[dict] = []
    watcher = YouTubePlaylistWatcher(
        dispatch_fn=AsyncMock(side_effect=RuntimeError("hook dispatch crashed")),
        notification_sink=notifications.append,
    )

    await watcher._dispatch(
        {
            "video_id": "dispatchBoom123",
            "url": "https://www.youtube.com/watch?v=dispatchBoom123",
            "title": "Dispatch boom",
            "channel_id": "chan1",
            "playlist_id": "PLdemo",
        }
    )

    failed = next((n for n in notifications if n.get("kind") == "youtube_playlist_dispatch_failed"), None)
    assert failed is not None
    assert "hook dispatch crashed" in str(failed.get("message") or "")
    assert (failed.get("metadata") or {}).get("failure_class") == "dispatch_exception"


@pytest.mark.asyncio
async def test_poll_now_retries_same_video_after_rejected_dispatch(monkeypatch, tmp_path):
    monkeypatch.setenv("YT_TUTORIALS_PLAYLIST_ID", "PLdemo")
    monkeypatch.setenv("YOUTUBE_API_KEY", "demo-key")
    monkeypatch.setenv("UA_OPS_DIR", str(tmp_path))

    new_item = {
        "video_id": "retryVid123",
        "url": "https://www.youtube.com/watch?v=retryVid123",
        "title": "Retry after rejected dispatch",
        "channel_id": "chan1",
        "occurred_at": "2026-03-25T15:00:00Z",
        "playlist_id": "PLdemo",
    }

    dispatch_mock = AsyncMock(
        side_effect=[
            (False, "dispatch_rejected"),
            {
                "decision": "accepted",
                "reason": "dispatched",
                "run_id": "run_retry_123",
                "attempt_id": "attempt_retry_1",
                "attempt_number": 1,
                "workspace_dir": "/tmp/run_session_hook_yt_retry",
            },
        ]
    )

    watcher = YouTubePlaylistWatcher(dispatch_fn=dispatch_mock)
    watcher._fetch_playlist_items = AsyncMock(return_value=[new_item])

    first = await watcher.poll_now()
    second = await watcher.poll_now()

    assert first["new_dispatched"] == 0
    assert second["new_dispatched"] == 1
    saved = json.loads(_state_path().read_text(encoding="utf-8"))
    assert saved["seen_ids"] == ["retryVid123"]
    assert dispatch_mock.await_count == 2


@pytest.mark.asyncio
async def test_runtime_db_locked_dispatch_is_silent_and_left_retryable(monkeypatch, tmp_path):
    monkeypatch.setenv("YT_TUTORIALS_PLAYLIST_ID", "PLdemo")
    monkeypatch.setenv("YOUTUBE_API_KEY", "demo-key")
    monkeypatch.setenv("UA_OPS_DIR", str(tmp_path))

    notifications: list[dict] = []
    watcher = YouTubePlaylistWatcher(
        dispatch_fn=AsyncMock(
            return_value={
                "decision": "failed",
                "reason": "runtime_db_locked",
                "retryable": True,
            }
        ),
        notification_sink=notifications.append,
    )

    ok = await watcher._dispatch(
        {
            "video_id": "locked123",
            "url": "https://www.youtube.com/watch?v=locked123",
            "title": "Runtime DB lock test",
            "channel_id": "chan1",
            "playlist_id": "PLdemo",
        }
    )

    assert ok is False
    assert notifications == []


@pytest.mark.asyncio
async def test_runtime_db_lock_exception_is_silent_and_left_retryable(monkeypatch, tmp_path):
    monkeypatch.setenv("YT_TUTORIALS_PLAYLIST_ID", "PLdemo")
    monkeypatch.setenv("YOUTUBE_API_KEY", "demo-key")
    monkeypatch.setenv("UA_OPS_DIR", str(tmp_path))

    notifications: list[dict] = []
    watcher = YouTubePlaylistWatcher(
        dispatch_fn=AsyncMock(side_effect=sqlite3.OperationalError("database is locked")),
        notification_sink=notifications.append,
    )

    ok = await watcher._dispatch(
        {
            "video_id": "lockedExc123",
            "url": "https://www.youtube.com/watch?v=lockedExc123",
            "title": "Runtime DB lock exception test",
            "channel_id": "chan1",
            "playlist_id": "PLdemo",
        }
    )

    assert ok is False
    assert notifications == []
    assert "lockedExc123" in watcher._notified_delayed_videos


@pytest.mark.asyncio
async def test_poll_now_skips_videos_with_existing_processed_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv("YT_TUTORIALS_PLAYLIST_ID", "PLdemo")
    monkeypatch.setenv("YOUTUBE_API_KEY", "demo-key")
    monkeypatch.setenv("UA_OPS_DIR", str(tmp_path / "ops"))
    monkeypatch.setenv("UA_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    video_id = "doneVid123"
    run_dir = (
        tmp_path
        / "artifacts"
        / "youtube-tutorial-creation"
        / "2026-03-25"
        / "done-video"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "video_id": video_id,
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "README.md").write_text("# ready\n", encoding="utf-8")
    (run_dir / "CONCEPT.md").write_text("# concept\n", encoding="utf-8")

    notifications: list[dict] = []
    dispatch_mock = AsyncMock(return_value=(True, "agent"))
    watcher = YouTubePlaylistWatcher(
        dispatch_fn=dispatch_mock,
        notification_sink=notifications.append,
    )
    watcher._fetch_playlist_items = AsyncMock(
        return_value=[
            {
                "video_id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "title": "Already processed",
                "channel_id": "chan1",
                "occurred_at": "2026-03-25T15:00:00Z",
                "playlist_id": "PLdemo",
            }
        ]
    )

    result = await watcher.poll_now()

    assert result["new_dispatched"] == 0
    assert dispatch_mock.await_count == 0
    assert notifications == []
    saved = json.loads(_state_path().read_text(encoding="utf-8"))
    assert saved["seen_ids"] == [video_id]


@pytest.mark.asyncio
async def test_poll_now_seeds_on_startup_before_first_background_seed(monkeypatch, tmp_path):
    monkeypatch.setenv("YT_TUTORIALS_PLAYLIST_ID", "PLdemo")
    monkeypatch.setenv("YOUTUBE_API_KEY", "demo-key")
    monkeypatch.setenv("UA_OPS_DIR", str(tmp_path))

    video_id = "seedNow123"
    watcher = YouTubePlaylistWatcher(dispatch_fn=AsyncMock(return_value=(True, "agent")))
    watcher._fetch_playlist_items = AsyncMock(
        return_value=[
            {
                "video_id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "title": "Seed now video",
                "channel_id": "chan1",
                "occurred_at": "2026-03-25T15:00:00Z",
                "playlist_id": "PLdemo",
            }
        ]
    )
    watcher._task = asyncio.create_task(asyncio.sleep(10))
    try:
        result = await watcher.poll_now()
    finally:
        watcher._task.cancel()
        try:
            await watcher._task
        except asyncio.CancelledError:
            pass

    assert result["ok"] is True
    assert result["new_dispatched"] == 0
    assert result["seeded_existing"] == 1
    saved = json.loads(_state_path().read_text(encoding="utf-8"))
    assert saved["seen_ids"] == [video_id]

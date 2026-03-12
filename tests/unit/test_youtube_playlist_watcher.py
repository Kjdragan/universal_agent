from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from universal_agent.services.youtube_playlist_watcher import YouTubePlaylistWatcher


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

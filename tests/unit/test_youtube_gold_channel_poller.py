"""Unit tests for the gold-channel RSS poller.

We mock every external call (RSS HTTP, YouTube Data API, playlist add/list)
so these run offline. The goal is to lock in the dedup + weekday-routing +
duration-override + cap logic since that's where the value lives.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from universal_agent.services import youtube_gold_channel_poller as poller

# --- Test fixtures ---------------------------------------------------------


def _make_watchlist(tmp_path: Path, channels: list[dict]) -> Path:
    path = tmp_path / "channels_watchlist.json"
    path.write_text(json.dumps({"channels": channels}, indent=2), encoding="utf-8")
    return path


def _gold_channel(channel_id: str, name: str, **overrides) -> dict:
    base = {
        "channel_id": channel_id,
        "channel_name": name,
        "video_count": 5,
        "rss_feed_url": f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}",
        "tier": "gold",
        "manual_add_count_30d": 0,
        "sidecar_approval_count_30d": 0,
        "last_publication_seen_at": None,
        "last_promoted_to_gold_at": None,
        "duration_max_seconds_override": None,
    }
    base.update(overrides)
    return base


def _candidate(
    *, video_id: str, channel_id: str = "UC1", channel_name: str = "ChanA",
    published_at: datetime | None = None,
) -> poller.CandidateVideo:
    if published_at is None:
        published_at = datetime.now(timezone.utc) - timedelta(hours=2)
    return poller.CandidateVideo(
        video_id=video_id,
        title=f"Video {video_id}",
        channel_id=channel_id,
        channel_name=channel_name,
        published_at=published_at,
    )


# --- Tests -----------------------------------------------------------------


def test_resolve_duration_cap_uses_override_when_set():
    channel = _gold_channel("UC1", "ChanA", duration_max_seconds_override=86400)
    assert poller._resolve_duration_cap(channel) == 86400


def test_resolve_duration_cap_falls_back_to_global_default(monkeypatch):
    channel = _gold_channel("UC1", "ChanA")  # override=None
    monkeypatch.delenv("UA_YOUTUBE_GOLD_GLOBAL_DURATION_CAP", raising=False)
    assert poller._resolve_duration_cap(channel) == 5400


def test_resolve_duration_cap_env_var_overrides_default(monkeypatch):
    channel = _gold_channel("UC1", "ChanA")
    monkeypatch.setenv("UA_YOUTUBE_GOLD_GLOBAL_DURATION_CAP", "7200")
    assert poller._resolve_duration_cap(channel) == 7200


def test_resolve_target_playlist_reads_env(monkeypatch):
    monkeypatch.setenv("THURSDAY_YT_PLAYLIST", "PLthu123")
    assert poller._resolve_target_playlist("THURSDAY") == "PLthu123"


def test_resolve_target_playlist_missing_returns_none(monkeypatch):
    monkeypatch.delenv("FRIDAY_YT_PLAYLIST", raising=False)
    assert poller._resolve_target_playlist("FRIDAY") is None


def test_is_already_processed_returns_false_when_db_missing(tmp_path):
    assert poller._is_already_processed("abc123", processed_db_path=tmp_path / "nope.db") is False


def test_is_already_processed_finds_row(tmp_path):
    db = tmp_path / "state.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE processed_videos (video_id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO processed_videos VALUES ('abc123')")
    conn.commit()
    conn.close()
    assert poller._is_already_processed("abc123", processed_db_path=db) is True
    assert poller._is_already_processed("zzz999", processed_db_path=db) is False


def test_poll_no_gold_channels_is_noop(tmp_path):
    path = _make_watchlist(tmp_path, [
        {"channel_id": "UC1", "channel_name": "ChanA", "tier": "sidecar"},
        {"channel_id": "UC2", "channel_name": "ChanB", "tier": "blocked"},
    ])
    result = poller.poll_gold_channels(watchlist_path=path, dry_run=True)
    assert result.inspected_channels == 0
    assert result.added == 0
    assert result.candidates_discovered == 0


def test_poll_routes_by_published_weekday(tmp_path, monkeypatch):
    """A video published Wednesday lands in WEDNESDAY playlist, even if poller runs Thursday."""
    monkeypatch.setenv("WEDNESDAY_YT_PLAYLIST", "PLwed")
    monkeypatch.setenv("THURSDAY_YT_PLAYLIST", "PLthu")
    monkeypatch.setenv("UA_YOUTUBE_INGESTION_STATE_DB", str(tmp_path / "nope.db"))

    # Wednesday 10 AM Houston (CDT = UTC-5) = Wednesday 15:00 UTC
    wed_pub = datetime(2026, 5, 20, 15, 0, tzinfo=timezone.utc)
    poller_now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)  # Thursday 5:30 AM Houston

    path = _make_watchlist(tmp_path, [_gold_channel("UC1", "ChanA")])
    with patch.object(poller, "_fetch_rss_entries") as mock_rss, \
         patch.object(poller, "_fetch_duration_seconds", return_value=1200), \
         patch.object(poller, "get_playlist_items", return_value=[]), \
         patch.object(poller, "add_playlist_item") as mock_add:
        mock_rss.return_value = [_candidate(video_id="vidWED", published_at=wed_pub)]
        result = poller.poll_gold_channels(
            now=poller_now, watchlist_path=path, dry_run=False,
        )

    assert result.added == 1
    mock_add.assert_called_once_with("PLwed", "vidWED")


def test_poll_skips_video_already_in_playlist(tmp_path, monkeypatch):
    monkeypatch.setenv("WEDNESDAY_YT_PLAYLIST", "PLwed")
    monkeypatch.setenv("UA_YOUTUBE_INGESTION_STATE_DB", str(tmp_path / "nope.db"))
    wed_pub = datetime(2026, 5, 20, 15, 0, tzinfo=timezone.utc)
    poller_now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)

    path = _make_watchlist(tmp_path, [_gold_channel("UC1", "ChanA")])
    with patch.object(poller, "_fetch_rss_entries") as mock_rss, \
         patch.object(poller, "_fetch_duration_seconds", return_value=1200), \
         patch.object(poller, "get_playlist_items", return_value=[{"video_id": "vidWED"}]), \
         patch.object(poller, "add_playlist_item") as mock_add:
        mock_rss.return_value = [_candidate(video_id="vidWED", published_at=wed_pub)]
        result = poller.poll_gold_channels(
            now=poller_now, watchlist_path=path, dry_run=False,
        )

    assert result.added == 0
    assert result.skipped_already_in_playlist == 1
    mock_add.assert_not_called()


def test_poll_skips_video_already_processed(tmp_path, monkeypatch):
    monkeypatch.setenv("WEDNESDAY_YT_PLAYLIST", "PLwed")
    # Seed the processed_videos DB
    db = tmp_path / "state.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE processed_videos (video_id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO processed_videos VALUES ('vidWED')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("UA_YOUTUBE_INGESTION_STATE_DB", str(db))

    wed_pub = datetime(2026, 5, 20, 15, 0, tzinfo=timezone.utc)
    poller_now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)

    path = _make_watchlist(tmp_path, [_gold_channel("UC1", "ChanA")])
    with patch.object(poller, "_fetch_rss_entries") as mock_rss, \
         patch.object(poller, "_fetch_duration_seconds", return_value=1200), \
         patch.object(poller, "get_playlist_items", return_value=[]), \
         patch.object(poller, "add_playlist_item") as mock_add:
        mock_rss.return_value = [_candidate(video_id="vidWED", published_at=wed_pub)]
        result = poller.poll_gold_channels(
            now=poller_now, watchlist_path=path, dry_run=False,
        )

    assert result.added == 0
    assert result.skipped_already_processed == 1
    mock_add.assert_not_called()


def test_poll_respects_duration_cap(tmp_path, monkeypatch):
    """A 2-hour video on a default-cap channel is skipped; same video on a
    Lex-style override channel is accepted."""
    monkeypatch.setenv("WEDNESDAY_YT_PLAYLIST", "PLwed")
    monkeypatch.setenv("UA_YOUTUBE_INGESTION_STATE_DB", str(tmp_path / "nope.db"))
    wed_pub = datetime(2026, 5, 20, 15, 0, tzinfo=timezone.utc)
    poller_now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)
    two_hours = 7200  # exceeds default 5400s cap

    # Case 1: default cap → reject
    path1 = _make_watchlist(tmp_path, [_gold_channel("UC1", "ChanA")])
    with patch.object(poller, "_fetch_rss_entries") as mock_rss, \
         patch.object(poller, "_fetch_duration_seconds", return_value=two_hours), \
         patch.object(poller, "get_playlist_items", return_value=[]), \
         patch.object(poller, "add_playlist_item") as mock_add:
        mock_rss.return_value = [_candidate(video_id="vidLong", published_at=wed_pub)]
        result = poller.poll_gold_channels(
            now=poller_now, watchlist_path=path1, dry_run=False,
        )
    assert result.skipped_duration_cap == 1
    mock_add.assert_not_called()

    # Case 2: Lex-style 86400 override → accept
    path2 = _make_watchlist(
        tmp_path,
        [_gold_channel("UC1", "ChanA", duration_max_seconds_override=86400)],
    )
    with patch.object(poller, "_fetch_rss_entries") as mock_rss, \
         patch.object(poller, "_fetch_duration_seconds", return_value=two_hours), \
         patch.object(poller, "get_playlist_items", return_value=[]), \
         patch.object(poller, "add_playlist_item") as mock_add:
        mock_rss.return_value = [_candidate(video_id="vidLong", published_at=wed_pub)]
        result = poller.poll_gold_channels(
            now=poller_now, watchlist_path=path2, dry_run=False,
        )
    assert result.skipped_duration_cap == 0
    assert result.added == 1
    mock_add.assert_called_once_with("PLwed", "vidLong")


def test_poll_daily_cap_stops_after_n(tmp_path, monkeypatch):
    monkeypatch.setenv("WEDNESDAY_YT_PLAYLIST", "PLwed")
    monkeypatch.setenv("UA_YOUTUBE_INGESTION_STATE_DB", str(tmp_path / "nope.db"))
    wed_pub = datetime(2026, 5, 20, 15, 0, tzinfo=timezone.utc)
    poller_now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)

    # 5 candidate videos from one gold channel
    path = _make_watchlist(tmp_path, [_gold_channel("UC1", "ChanA")])
    with patch.object(poller, "_fetch_rss_entries") as mock_rss, \
         patch.object(poller, "_fetch_duration_seconds", return_value=1200), \
         patch.object(poller, "get_playlist_items", return_value=[]), \
         patch.object(poller, "add_playlist_item"):
        mock_rss.return_value = [
            _candidate(video_id=f"vid{i}", published_at=wed_pub + timedelta(minutes=i))
            for i in range(5)
        ]
        result = poller.poll_gold_channels(
            now=poller_now, watchlist_path=path, dry_run=False, daily_cap=3,
        )

    assert result.added == 3
    assert result.cap_reached is True


def test_poll_sorts_newest_first(tmp_path, monkeypatch):
    """When more candidates than cap, the newer ones win."""
    monkeypatch.setenv("WEDNESDAY_YT_PLAYLIST", "PLwed")
    monkeypatch.setenv("UA_YOUTUBE_INGESTION_STATE_DB", str(tmp_path / "nope.db"))
    wed_pub_base = datetime(2026, 5, 20, 15, 0, tzinfo=timezone.utc)
    poller_now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)

    path = _make_watchlist(tmp_path, [_gold_channel("UC1", "ChanA")])
    added: list[str] = []
    with patch.object(poller, "_fetch_rss_entries") as mock_rss, \
         patch.object(poller, "_fetch_duration_seconds", return_value=1200), \
         patch.object(poller, "get_playlist_items", return_value=[]), \
         patch.object(poller, "add_playlist_item", side_effect=lambda pid, vid: added.append(vid)):
        # Three videos: vid_old, vid_mid, vid_new (chronological)
        mock_rss.return_value = [
            _candidate(video_id="vid_old", published_at=wed_pub_base),
            _candidate(video_id="vid_mid", published_at=wed_pub_base + timedelta(hours=1)),
            _candidate(video_id="vid_new", published_at=wed_pub_base + timedelta(hours=2)),
        ]
        result = poller.poll_gold_channels(
            now=poller_now, watchlist_path=path, dry_run=False, daily_cap=2,
        )

    assert result.added == 2
    # Newest two should be selected
    assert "vid_new" in added
    assert "vid_mid" in added
    assert "vid_old" not in added


def test_poll_skips_outside_lookback_window(tmp_path, monkeypatch):
    monkeypatch.setenv("WEDNESDAY_YT_PLAYLIST", "PLwed")
    poller_now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)
    # Published 5 days ago — way outside default 30h lookback
    old_pub = poller_now - timedelta(days=5)

    path = _make_watchlist(tmp_path, [_gold_channel("UC1", "ChanA")])
    with patch.object(poller, "_fetch_rss_entries") as mock_rss, \
         patch.object(poller, "_fetch_duration_seconds", return_value=1200), \
         patch.object(poller, "get_playlist_items", return_value=[]), \
         patch.object(poller, "add_playlist_item") as mock_add:
        mock_rss.return_value = [_candidate(video_id="vid_old", published_at=old_pub)]
        result = poller.poll_gold_channels(
            now=poller_now, watchlist_path=path, dry_run=False,
        )

    assert result.candidates_discovered == 0
    assert result.added == 0
    mock_add.assert_not_called()


def test_poll_skips_when_target_playlist_env_missing(tmp_path, monkeypatch):
    # WEDNESDAY_YT_PLAYLIST intentionally not set
    monkeypatch.delenv("WEDNESDAY_YT_PLAYLIST", raising=False)
    monkeypatch.setenv("UA_YOUTUBE_INGESTION_STATE_DB", str(tmp_path / "nope.db"))
    wed_pub = datetime(2026, 5, 20, 15, 0, tzinfo=timezone.utc)
    poller_now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)

    path = _make_watchlist(tmp_path, [_gold_channel("UC1", "ChanA")])
    with patch.object(poller, "_fetch_rss_entries") as mock_rss, \
         patch.object(poller, "_fetch_duration_seconds", return_value=1200), \
         patch.object(poller, "get_playlist_items", return_value=[]), \
         patch.object(poller, "add_playlist_item") as mock_add:
        mock_rss.return_value = [_candidate(video_id="vidWED", published_at=wed_pub)]
        result = poller.poll_gold_channels(
            now=poller_now, watchlist_path=path, dry_run=False,
        )

    assert result.added == 0
    assert result.skipped_no_playlist_env == 1
    mock_add.assert_not_called()


def test_dry_run_does_not_call_add(tmp_path, monkeypatch):
    monkeypatch.setenv("WEDNESDAY_YT_PLAYLIST", "PLwed")
    monkeypatch.setenv("UA_YOUTUBE_INGESTION_STATE_DB", str(tmp_path / "nope.db"))
    wed_pub = datetime(2026, 5, 20, 15, 0, tzinfo=timezone.utc)
    poller_now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)

    path = _make_watchlist(tmp_path, [_gold_channel("UC1", "ChanA")])
    with patch.object(poller, "_fetch_rss_entries") as mock_rss, \
         patch.object(poller, "_fetch_duration_seconds", return_value=1200), \
         patch.object(poller, "get_playlist_items", return_value=[]), \
         patch.object(poller, "add_playlist_item") as mock_add:
        mock_rss.return_value = [_candidate(video_id="vidWED", published_at=wed_pub)]
        result = poller.poll_gold_channels(
            now=poller_now, watchlist_path=path, dry_run=True,
        )

    assert result.added == 1  # counted as added in dry-run for stats
    assert result.dry_run is True
    mock_add.assert_not_called()


def test_poll_idempotent_persists_last_seen_only_on_real_add(tmp_path, monkeypatch):
    monkeypatch.setenv("WEDNESDAY_YT_PLAYLIST", "PLwed")
    monkeypatch.setenv("UA_YOUTUBE_INGESTION_STATE_DB", str(tmp_path / "nope.db"))
    wed_pub = datetime(2026, 5, 20, 15, 0, tzinfo=timezone.utc)
    poller_now = datetime(2026, 5, 21, 10, 30, tzinfo=timezone.utc)

    path = _make_watchlist(tmp_path, [_gold_channel("UC1", "ChanA")])
    # Dry-run first — should NOT persist
    with patch.object(poller, "_fetch_rss_entries") as mock_rss, \
         patch.object(poller, "_fetch_duration_seconds", return_value=1200), \
         patch.object(poller, "get_playlist_items", return_value=[]), \
         patch.object(poller, "add_playlist_item"):
        mock_rss.return_value = [_candidate(video_id="vidWED", published_at=wed_pub)]
        poller.poll_gold_channels(
            now=poller_now, watchlist_path=path, dry_run=True,
        )
    before = json.loads(path.read_text())
    assert before["channels"][0]["last_publication_seen_at"] is None

    # Real run — should persist last_publication_seen_at
    with patch.object(poller, "_fetch_rss_entries") as mock_rss, \
         patch.object(poller, "_fetch_duration_seconds", return_value=1200), \
         patch.object(poller, "get_playlist_items", return_value=[]), \
         patch.object(poller, "add_playlist_item"):
        mock_rss.return_value = [_candidate(video_id="vidWED", published_at=wed_pub)]
        poller.poll_gold_channels(
            now=poller_now, watchlist_path=path, dry_run=False,
        )
    after = json.loads(path.read_text())
    assert after["channels"][0]["last_publication_seen_at"] == poller_now.isoformat()

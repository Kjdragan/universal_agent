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


async def test_rss_adapter_persists_last_fetch_epoch_across_restart(monkeypatch):
    """T13: `_last_fetch_epoch` must survive a process restart via the
    already-wired state backend, not just live in adapter memory. Before
    this fix, a fresh process always started from 0.0 — which, combined with
    `schedule_min_interval_seconds` gating on truthiness (`if ... and
    self._last_fetch_epoch:`), meant a tight restart loop re-triggered a
    full-watchlist sweep every single time (the 2026-08-02 incident)."""
    state_store: dict[str, dict] = {}

    def _load_state(key: str):
        return state_store.get(key)

    def _save_state(key: str, state: dict):
        state_store[key] = state

    cfg = _cfg()
    first_adapter = YouTubeChannelRSSAdapter(cfg)
    first_adapter.set_state_backend(_load_state, _save_state)
    assert first_adapter.last_fetch_epoch == 0.0

    async def _fake_fetch(client, *, channel_id):
        return []

    monkeypatch.setattr(first_adapter, "_fetch_channel_entries", _fake_fetch)
    await first_adapter.fetch_events()
    assert first_adapter.last_fetch_epoch > 0.0
    persisted_epoch = first_adapter.last_fetch_epoch
    assert state_store["youtube_channel_rss:__schedule__"]["last_fetch_epoch"] == persisted_epoch

    # Simulate a restart: brand-new adapter instance, same state backend.
    second_adapter = YouTubeChannelRSSAdapter(cfg)
    second_adapter.set_state_backend(_load_state, _save_state)
    assert second_adapter.last_fetch_epoch == 0.0  # not hydrated yet — lazy on first fetch_events()

    async def _fake_fetch_second(client, *, channel_id):
        return []

    monkeypatch.setattr(second_adapter, "_fetch_channel_entries", _fake_fetch_second)
    await second_adapter.fetch_events()
    # The restarted adapter picked up the persisted epoch as its baseline
    # rather than starting cold from 0.0 — its post-fetch epoch must be >=
    # the epoch the first instance persisted (monotonic, same clock).
    assert second_adapter.last_fetch_epoch >= persisted_epoch


async def test_rss_adapter_schedule_gate_min_interval_respects_persisted_epoch(monkeypatch):
    """Without persistence, a fresh adapter's `_last_fetch_epoch` is 0.0 —
    falsy — so `schedule_min_interval_seconds` gating (`if ... and
    self._last_fetch_epoch:`) is skipped entirely and a restart always
    triggers an immediate fetch. With persistence + hydration, a restart
    inside the min-interval window must skip the fetch, exactly like an
    un-restarted process would."""
    state_store: dict[str, dict] = {}

    def _load_state(key: str):
        return state_store.get(key)

    def _save_state(key: str, state: dict):
        state_store[key] = state

    cfg = {
        **_cfg(),
        "schedule_fetch_hours": list(range(24)),  # always "in schedule"
        "schedule_min_interval_seconds": 5400,
    }
    first_adapter = YouTubeChannelRSSAdapter(cfg)
    first_adapter.set_state_backend(_load_state, _save_state)
    fetch_calls = {"count": 0}

    async def _fake_fetch(client, *, channel_id):
        fetch_calls["count"] += 1
        return []

    monkeypatch.setattr(first_adapter, "_fetch_channel_entries", _fake_fetch)
    await first_adapter.fetch_events()
    assert fetch_calls["count"] == 1

    # Restart: a fresh instance immediately re-polls (simulating the
    # scheduler's first tick right after process start).
    second_adapter = YouTubeChannelRSSAdapter(cfg)
    second_adapter.set_state_backend(_load_state, _save_state)
    monkeypatch.setattr(second_adapter, "_fetch_channel_entries", _fake_fetch)
    events = await second_adapter.fetch_events()
    # Still well inside schedule_min_interval_seconds (5400s) of the first
    # fetch -> gated out, no new RSS request issued, no events.
    assert events == []
    assert fetch_calls["count"] == 1


async def test_rss_adapter_channel_failure_does_not_abort_other_channels(monkeypatch):
    adapter = YouTubeChannelRSSAdapter(
        {
            "watchlist": [{"channel_id": "UC_FAIL"}, {"channel_id": "UC_GOOD"}],
            "seed_on_first_run": False,
        }
    )

    async def _fake_fetch(client, *, channel_id):
        if channel_id == "UC_FAIL":
            raise RuntimeError("network boom")
        return [
            {
                "video_id": "v_good",
                "channel_id": "UC_GOOD",
                "url": "https://youtube.com/watch?v=v_good",
                "title": "Good",
                "published_at": "2026-02-22T00:20:00Z",
                "occurred_at": "2026-02-22T00:20:00Z",
            }
        ]

    monkeypatch.setattr(adapter, "_fetch_channel_entries", _fake_fetch)
    events = await adapter.fetch_events()
    assert len(events) == 1
    assert events[0].payload["video_id"] == "v_good"


def test_rss_adapter_loads_watchlist_from_json_file(tmp_path: Path):
    payload = {
        "channels": [
            {"channel_id": "UC_ONE", "channel_name": "Creator One"},
            {"channel_id": "UC_TWO"},
            {"channel_id": "UC_ONE"},
        ]
    }
    watchlist_file = tmp_path / "channels_watchlist.json"
    watchlist_file.write_text(json.dumps(payload), encoding="utf-8")
    adapter = YouTubeChannelRSSAdapter({"watchlist_file": str(watchlist_file), "watchlist": []})
    resolved = adapter._resolve_watchlist()
    assert [item["channel_id"] for item in resolved] == ["UC_ONE", "UC_TWO"]
    assert resolved[0]["channel_name"] == "Creator One"


def test_rss_adapter_uses_fallback_watchlist_when_configured_path_missing(tmp_path: Path):
    fallback_payload = {
        "channels": [
            {"channel_id": "UC_FALLBACK", "channel_name": "Fallback Creator"},
        ]
    }
    fallback_file = tmp_path / "channels_watchlist.json"
    fallback_file.write_text(json.dumps(fallback_payload), encoding="utf-8")
    missing_file = tmp_path / "missing_watchlist.json"

    adapter = YouTubeChannelRSSAdapter({"watchlist_file": str(missing_file), "watchlist": []})
    adapter._watchlist_fallback_file = fallback_file

    resolved = adapter._resolve_watchlist()
    assert [item["channel_id"] for item in resolved] == ["UC_FALLBACK"]
    assert resolved[0]["channel_name"] == "Fallback Creator"


def test_rss_adapter_warns_when_no_watchlist_channels(tmp_path: Path, caplog):
    missing_file = tmp_path / "missing_watchlist.json"
    missing_fallback = tmp_path / "missing_fallback.json"
    adapter = YouTubeChannelRSSAdapter({"watchlist_file": str(missing_file), "watchlist": []})
    adapter._watchlist_fallback_file = missing_fallback

    with caplog.at_level("WARNING"):
        resolved = adapter._resolve_watchlist()

    assert resolved == []
    assert "resolved to zero channels" in caplog.text


async def test_rss_adapter_extracts_media_metadata_and_filters_shorts():
    adapter = YouTubeChannelRSSAdapter({"watchlist": [{"channel_id": "UC_TEST"}], "seed_on_first_run": False})

    class _Response:
        status_code = 200
        headers = {}
        text = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"
              xmlns:yt="http://www.youtube.com/xml/schemas/2015"
              xmlns:media="http://search.yahoo.com/mrss/">
          <entry>
            <yt:videoId>good_video_1</yt:videoId>
            <yt:channelId>UC_TEST</yt:channelId>
            <title>Deep agent workflow</title>
            <link rel="alternate" href="https://www.youtube.com/watch?v=good_video_1" />
            <author><name>Creator One</name><uri>https://www.youtube.com/channel/UC_TEST</uri></author>
            <published>2026-04-13T00:00:00+00:00</published>
            <updated>2026-04-13T01:00:00+00:00</updated>
            <media:group>
              <media:title>Deep agent workflow</media:title>
              <media:thumbnail url="https://img.youtube.com/vi/good_video_1/hqdefault.jpg" />
              <media:description>Practical agent orchestration walkthrough.</media:description>
            </media:group>
          </entry>
          <entry>
            <yt:videoId>short_vid_1</yt:videoId>
            <title>Short</title>
            <link rel="alternate" href="https://www.youtube.com/shorts/short_vid_1" />
            <published>2026-04-13T00:05:00+00:00</published>
          </entry>
        </feed>
        """

    class _Client:
        async def get(self, *args, **kwargs):
            return _Response()

    entries = await adapter._fetch_channel_entries(_Client(), channel_id="UC_TEST")
    assert [entry["video_id"] for entry in entries] == ["good_video_1"]
    assert entries[0]["description"] == "Practical agent orchestration walkthrough."
    assert entries[0]["thumbnail_url"].endswith("/hqdefault.jpg")
    assert entries[0]["author_name"] == "Creator One"
    assert entries[0]["updated_at"] == "2026-04-13T01:00:00+00:00"

    event = adapter.normalize(type("Raw", (), {"payload": entries[0], "occurred_at": entries[0]["occurred_at"]})())
    assert event.subject["description"] == "Practical agent orchestration walkthrough."
    assert event.subject["thumbnail_url"].endswith("/hqdefault.jpg")

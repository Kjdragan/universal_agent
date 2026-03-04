from __future__ import annotations

import copy
from datetime import datetime, timezone

from csi_ingester.adapters.threads_trends_broad import ThreadsBroadTrendsAdapter
from csi_ingester.adapters.threads_trends_seeded import ThreadsSeededTrendsAdapter


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fake_post(post_id: str, *, text: str, ts: str | None = None) -> dict:
    return {
        "id": post_id,
        "text": text,
        "timestamp": ts or _iso_now(),
        "username": "demo",
        "permalink": f"https://threads.net/{post_id}",
        "media_type": "TEXT_POST",
        "reply_count": 3,
        "repost_count": 2,
        "quote_count": 1,
        "like_count": 20,
    }


class _FakeThreadsClient:
    def __init__(self, payloads_by_query: dict[str, list[dict]]):
        self.payloads_by_query = payloads_by_query

    def is_configured(self) -> bool:
        return True

    async def keyword_search(self, *, query: str, search_type: str, search_surface: str, media_types, limit: int):
        return [copy.deepcopy(item) for item in self.payloads_by_query.get(query, [])]

    def quota_state(self) -> dict:
        return {"hits": {}}


async def test_threads_seeded_adapter_emits_hits_and_trends(monkeypatch):
    payloads = {
        "ai": [
            _fake_post("t-1", text="AI systems are moving fast #ai"),
            _fake_post("t-2", text="AI agent trends #ai"),
        ]
    }

    adapter = ThreadsSeededTrendsAdapter(
        {
            "query_packs": [{"name": "pack", "terms": ["ai"]}],
            "search_types": ["TOP"],
            "trend_windows_minutes": [15],
            "trend_top_k": 4,
            "per_query_limit": 20,
        }
    )

    state: dict[str, dict] = {}
    adapter.set_state_backend(lambda key: state.get(key), lambda key, payload: state.__setitem__(key, payload))
    monkeypatch.setattr(
        "csi_ingester.adapters.threads_trends_seeded.ThreadsAPIClient.from_config",
        lambda config, quota_state=None: _FakeThreadsClient(payloads),
    )

    events = await adapter.fetch_events()
    event_types = [row.event_type for row in events]
    assert "threads_keyword_hit" in event_types
    assert "threads_trend_snapshot" in event_types

    trend_raw = next(row for row in events if row.event_type == "threads_trend_snapshot")
    trend_event = adapter.normalize(trend_raw)
    assert trend_event.subject["trend_bucket"] == "seeded"
    assert trend_event.dedupe_key.startswith("threads:trend:seeded:")

    # second poll should not emit duplicate post hits for the same media IDs
    events_second = await adapter.fetch_events()
    assert all(row.event_type == "threads_trend_snapshot" for row in events_second)


async def test_threads_broad_adapter_updates_adaptive_scores(monkeypatch):
    payloads = {
        "ai": [
            _fake_post("b-1", text="Breaking market signal for robotics #robotics"),
            _fake_post("b-2", text="Robotics and ai product launch"),
        ],
        "news": [
            _fake_post("b-3", text="Startup economy and robotics"),
        ],
    }

    adapter = ThreadsBroadTrendsAdapter(
        {
            "query_pool": ["ai", "news"],
            "search_types": ["TOP"],
            "trend_windows_minutes": [15],
            "trend_top_k": 5,
            "per_query_limit": 20,
            "adaptive_enabled": True,
            "adaptive_max_terms": 5,
            "max_queries_per_cycle": 5,
        }
    )

    state: dict[str, dict] = {}
    adapter.set_state_backend(lambda key: state.get(key), lambda key, payload: state.__setitem__(key, payload))
    monkeypatch.setattr(
        "csi_ingester.adapters.threads_trends_broad.ThreadsAPIClient.from_config",
        lambda config, quota_state=None: _FakeThreadsClient(payloads),
    )

    events = await adapter.fetch_events()
    assert any(row.event_type == "threads_keyword_hit" for row in events)
    assert any(row.event_type == "threads_trend_snapshot" for row in events)

    persisted = state.get("threads_trends_broad:state") or {}
    adaptive_scores = persisted.get("adaptive_scores") if isinstance(persisted.get("adaptive_scores"), dict) else {}
    assert "robotics" in adaptive_scores

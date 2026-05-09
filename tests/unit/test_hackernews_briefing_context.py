"""Unit tests for the Hacker News briefing-context orchestrator (Lane A, P2.A2).

The orchestrator picks ~10 candidate items from the snapshot, hydrates
each with HN-internal content (comments + post text via the fetcher),
runs per-topic Algolia comment search, and renders a markdown block
for the daily briefing prompt. It does NOT fetch article bodies —
Atlas does that selectively via webReader during its mission.

All fetcher calls are mocked. The snapshot is constructed in-test.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from universal_agent.services import hackernews_briefing_context as ctx

# ─── fixtures ──────────────────────────────────────────────────────────


def _make_story(story_id: int, *, title: str = "Story", url: str = "https://example.com",
                score: int = 100, descendants: int = 5, by: str = "user") -> dict[str, Any]:
    return {
        "id": story_id,
        "title": title,
        "url": url,
        "score": score,
        "descendants": descendants,
        "by": by,
        "time": 1778000000,
    }


def _make_snapshot(*, age_hours: float = 1.0, with_top: bool = True,
                   with_controversial: bool = True, with_pulses: bool = True,
                   errors: list[str] | None = None) -> dict[str, Any]:
    """Build a normalized snapshot fixture (post-Phase-1 schema_version=2 shape)."""
    generated_at = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    snap: dict[str, Any] = {
        "meta": {
            "generated_at": generated_at.isoformat(),
            "schema_version": 2,
            "watchlist": ["claude", "agent"],
            "errors": errors or [],
            "duration_seconds": 5.0,
        },
        "top_stories": [],
        "controversial": [],
        "movers": {"since": "x", "changes": []},
        "pulses": {},
        "show_hn": [],
        "ask_hn": [],
        "hiring": {"companies": []},
    }
    if with_top:
        snap["top_stories"] = [_make_story(i, title=f"Top {i}", score=200 - i * 10) for i in range(1, 8)]
    if with_controversial:
        snap["controversial"] = [_make_story(50 + i, title=f"Controversy {i}", score=100, descendants=200) for i in range(3)]
    if with_pulses:
        snap["pulses"] = {
            "claude": {"topic": "claude", "count": 100, "avg_points": 200, "trend": [], "pct_change": 0,
                       "top_stories": [_make_story(100 + i, title=f"Claude topic {i}") for i in range(3)]},
            "agent":  {"topic": "agent", "count": 50, "avg_points": 150, "trend": [], "pct_change": 0,
                       "top_stories": [_make_story(200 + i, title=f"Agent topic {i}") for i in range(3)]},
        }
    return snap


def _stub_fetcher(monkeypatch, *,
                  comments_by_id: dict[int, list[dict[str, Any]]] | None = None,
                  items_by_id: dict[int, dict[str, Any]] | None = None,
                  topic_mentions: dict[str, list[dict[str, Any]]] | None = None,
                  thread_failures: set[int] | None = None,
                  topic_failures: set[str] | None = None) -> None:
    comments_by_id = comments_by_id or {}
    items_by_id = items_by_id or {}
    topic_mentions = topic_mentions or {}
    thread_failures = thread_failures or set()
    topic_failures = topic_failures or set()

    def fake_thread(item_id: int, limit: int = 10) -> list[dict[str, Any]]:
        if item_id in thread_failures:
            raise RuntimeError("thread fetch failed")
        return comments_by_id.get(item_id, [])[:limit]

    def fake_item(item_id: int) -> dict[str, Any] | None:
        return items_by_id.get(item_id)

    def fake_search(topic: str, limit: int = 5) -> list[dict[str, Any]]:
        if topic in topic_failures:
            raise RuntimeError("topic search failed")
        return topic_mentions.get(topic, [])[:limit]

    monkeypatch.setattr(ctx, "fetch_thread_comments", fake_thread)
    monkeypatch.setattr(ctx, "fetch_item", fake_item)
    monkeypatch.setattr(ctx, "search_topic_mentions", fake_search)


def _stub_read_latest(monkeypatch, snap: dict[str, Any] | None) -> None:
    monkeypatch.setattr(ctx, "read_latest", lambda: snap)


# ─── snapshot freshness gating ─────────────────────────────────────────


def test_block_returns_empty_when_no_snapshot(monkeypatch) -> None:
    _stub_read_latest(monkeypatch, None)
    _stub_fetcher(monkeypatch)
    assert ctx.build_briefing_block(watchlist=["claude"]) == ""


def test_block_returns_empty_when_snapshot_stale_25h(monkeypatch) -> None:
    snap = _make_snapshot(age_hours=25.0)
    _stub_read_latest(monkeypatch, snap)
    _stub_fetcher(monkeypatch)
    assert ctx.build_briefing_block(watchlist=["claude"]) == ""


def test_block_returns_empty_when_snapshot_unexpected_shape(monkeypatch) -> None:
    _stub_read_latest(monkeypatch, "not a dict")
    _stub_fetcher(monkeypatch)
    assert ctx.build_briefing_block(watchlist=["claude"]) == ""


def test_block_returns_empty_when_no_candidates(monkeypatch) -> None:
    """Empty snapshot panels → no candidates → empty block."""
    snap = _make_snapshot(age_hours=1.0, with_top=False, with_controversial=False, with_pulses=False)
    _stub_read_latest(monkeypatch, snap)
    _stub_fetcher(monkeypatch)
    assert ctx.build_briefing_block(watchlist=["claude"]) == ""


# ─── candidate selection ───────────────────────────────────────────────


def test_candidate_selection_priority_top_then_controversial_then_pulse(monkeypatch) -> None:
    snap = _make_snapshot()
    _stub_read_latest(monkeypatch, snap)
    _stub_fetcher(monkeypatch)
    candidates = ctx._select_candidates(snap, ["claude", "agent"])
    ids = [c["id"] for c in candidates]
    # First 5 from top_stories (ids 1-5), then 3 from controversial (50, 51, 52),
    # then up to 2 per topic from pulses (100, 101, 200, 201) — capped at 10.
    assert ids[:5] == [1, 2, 3, 4, 5]
    assert ids[5:8] == [50, 51, 52]
    assert len(ids) == 10  # 5 + 3 + 2 from claude pulse only (capped at 10)
    # The two pulse slots should be from claude (priority order)
    assert ids[8:10] == [100, 101]


def test_candidate_selection_dedupes_by_id(monkeypatch) -> None:
    """If the same id appears in top_stories and a pulse list, dedup should keep it once."""
    snap = _make_snapshot()
    # Inject duplicate: a top_stories item also appears in a pulse list
    snap["pulses"]["claude"]["top_stories"][0]["id"] = 1  # collide with top_stories[0]
    _stub_read_latest(monkeypatch, snap)
    _stub_fetcher(monkeypatch)
    candidates = ctx._select_candidates(snap, ["claude", "agent"])
    ids = [c["id"] for c in candidates]
    assert len(ids) == len(set(ids)), f"duplicates in {ids}"
    assert ids.count(1) == 1


def test_candidate_selection_caps_at_10(monkeypatch) -> None:
    snap = _make_snapshot()
    # Inject many more candidates everywhere
    snap["top_stories"] = [_make_story(i) for i in range(20)]
    snap["controversial"] = [_make_story(100 + i) for i in range(10)]
    _stub_read_latest(monkeypatch, snap)
    _stub_fetcher(monkeypatch)
    candidates = ctx._select_candidates(snap, ["claude", "agent"])
    assert len(candidates) == 10


def test_candidate_selection_handles_missing_pulse_topic_gracefully(monkeypatch) -> None:
    """Watchlist topic with no pulse data should not crash."""
    snap = _make_snapshot()
    snap["pulses"] = {}  # entire pulses gone
    _stub_read_latest(monkeypatch, snap)
    _stub_fetcher(monkeypatch)
    candidates = ctx._select_candidates(snap, ["claude", "agent", "missing_topic"])
    # Top 5 + controversial 3 = 8, no pulse contributions
    assert [c["id"] for c in candidates] == [1, 2, 3, 4, 5, 50, 51, 52]


# ─── block rendering ───────────────────────────────────────────────────


def test_block_renders_full_block_when_fresh_with_content(monkeypatch) -> None:
    snap = _make_snapshot()
    _stub_read_latest(monkeypatch, snap)
    _stub_fetcher(monkeypatch,
                  comments_by_id={
                      1: [{"id": 1001, "author": "alice", "text": "great post", "points": 0}],
                  },
                  items_by_id={1: {"id": 1, "text": ""}},
                  topic_mentions={"claude": [{"comment_text": "claude is cool",
                                              "story_title": "Cool Story",
                                              "story_url": "https://x.com",
                                              "author": "bob",
                                              "created_at": "2026-05-09T00:00:00Z",
                                              "story_id": 555}]})
    block = ctx.build_briefing_block(watchlist=["claude", "agent"])
    assert block != ""
    assert "Hacker News This Week" in block
    assert "Top 1" in block  # title from fixture
    assert "alice" in block  # comment author rendered
    assert "great post" in block  # comment text rendered
    assert "claude" in block.lower()  # topic mentions section


def test_block_renders_post_text_for_show_hn(monkeypatch) -> None:
    snap = _make_snapshot()
    _stub_read_latest(monkeypatch, snap)
    _stub_fetcher(monkeypatch,
                  items_by_id={1: {"id": 1, "text": "I built this thing because reasons."}})
    block = ctx.build_briefing_block(watchlist=["claude"])
    assert "I built this thing because reasons" in block


def test_block_includes_algolia_topic_section_after_candidates(monkeypatch) -> None:
    """Algolia per-topic mentions section must come AFTER the candidate items section."""
    snap = _make_snapshot()
    _stub_read_latest(monkeypatch, snap)
    _stub_fetcher(monkeypatch,
                  topic_mentions={
                      "claude": [{"comment_text": "specific claude mention",
                                  "story_title": "Some Story", "story_url": "https://x.com",
                                  "author": "u1", "created_at": "2026-05-09T00:00:00Z", "story_id": 1}]
                  })
    block = ctx.build_briefing_block(watchlist=["claude"])
    candidate_marker = "Top 1"  # title of first candidate
    algolia_marker = "specific claude mention"
    assert block.index(candidate_marker) < block.index(algolia_marker)


def test_block_includes_webReader_instruction_per_item(monkeypatch) -> None:
    """The block should hint Atlas to use webReader for article fetches."""
    snap = _make_snapshot()
    _stub_read_latest(monkeypatch, snap)
    _stub_fetcher(monkeypatch)
    block = ctx.build_briefing_block(watchlist=["claude"])
    assert "webReader" in block, "block must mention webReader so Atlas knows to invoke it for article fetches"


# ─── partial failure handling ──────────────────────────────────────────


def test_block_renders_when_one_thread_fetch_fails(monkeypatch) -> None:
    """If one item's comment fetch fails, that item still renders (without comments)."""
    snap = _make_snapshot()
    _stub_read_latest(monkeypatch, snap)
    _stub_fetcher(monkeypatch,
                  comments_by_id={
                      1: [{"id": 1001, "author": "alice", "text": "first", "points": 0}],
                      2: [{"id": 2001, "author": "bob", "text": "second", "points": 0}],
                  },
                  thread_failures={2})  # item 2's comments fail
    block = ctx.build_briefing_block(watchlist=["claude"])
    assert "Top 1" in block
    assert "Top 2" in block  # still rendered
    assert "first" in block  # item 1's comment present
    # item 2 has no comments rendered, but the item itself is in the block


def test_block_renders_when_one_topic_search_fails(monkeypatch) -> None:
    """If one topic's Algolia search fails, other topics still render."""
    snap = _make_snapshot()
    _stub_read_latest(monkeypatch, snap)
    _stub_fetcher(monkeypatch,
                  topic_mentions={"agent": [{"comment_text": "agent mention",
                                             "story_title": "Story", "story_url": "https://x.com",
                                             "author": "u", "created_at": "2026-05-09T00:00:00Z",
                                             "story_id": 1}]},
                  topic_failures={"claude"})
    block = ctx.build_briefing_block(watchlist=["claude", "agent"])
    assert "agent mention" in block
    # Failure of claude topic shouldn't break the block; it's just absent


def test_block_renders_when_all_topic_searches_fail(monkeypatch) -> None:
    """All topic searches failing → candidate items still render; topic section absent or empty."""
    snap = _make_snapshot()
    _stub_read_latest(monkeypatch, snap)
    _stub_fetcher(monkeypatch, topic_failures={"claude", "agent"})
    block = ctx.build_briefing_block(watchlist=["claude", "agent"])
    assert "Top 1" in block  # candidate items still there


# ─── public API surface ────────────────────────────────────────────────


@pytest.mark.parametrize("public_name", [
    "build_briefing_block",
    "MAX_AGE_HOURS",
])
def test_public_api(public_name: str) -> None:
    assert hasattr(ctx, public_name), f"hackernews_briefing_context must export {public_name}"

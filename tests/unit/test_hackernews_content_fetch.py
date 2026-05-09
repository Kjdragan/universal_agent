"""Unit tests for the Hacker News content fetcher (Lane A, P2.A1).

All CLI invocations are mocked via the underlying `_run_cli` helper in
the snapshot service — the real `hackernews-pp-cli` binary is never
called. Tests cover comment-thread parsing (top-level only, document
order, limit), item fetches (extracts `results` from the meta wrapper),
and Algolia per-topic search (correct args, compact result shape).
"""
from __future__ import annotations

from typing import Any

import pytest

from universal_agent.services import hackernews_content_fetch as fetcher

# ─── fetch_thread_comments ─────────────────────────────────────────────


def _stub_run_cli(monkeypatch, return_value: Any) -> dict[str, Any]:
    """Patch _run_cli on the fetcher module's namespace (it imports the
    helper as a local name, so the patch must target there, not the
    source module)."""
    captured: dict[str, Any] = {}

    def fake(args, timeout: int = 60):  # noqa: ARG001 — match _run_cli signature
        captured["args"] = list(args)
        captured["timeout"] = timeout
        return return_value

    monkeypatch.setattr(fetcher, "_run_cli", fake)
    return captured


def test_fetch_thread_comments_returns_top_level_only_in_document_order(monkeypatch) -> None:
    """Top-level comments (children of the story) should be returned in
    document order. Nested replies (children of children) must NOT be
    in the output — we want the HN canonical "top comments" view."""
    payload = {
        "id": 1,
        "type": "story",
        "children": [
            {"id": 10, "type": "comment", "author": "alice", "text": "first", "points": 0,
             "children": [{"id": 100, "type": "comment", "author": "alice2", "text": "deep reply", "points": 0, "children": []}]},
            {"id": 11, "type": "comment", "author": "bob", "text": "second", "points": 0, "children": []},
            {"id": 12, "type": "comment", "author": "carol", "text": "third", "points": 0, "children": []},
        ],
    }
    captured = _stub_run_cli(monkeypatch, payload)

    out = fetcher.fetch_thread_comments(1, limit=10)

    assert [c["id"] for c in out] == [10, 11, 12]  # document order, no nested
    assert captured["args"] == ["items", "thread", "1"]


def test_fetch_thread_comments_respects_limit(monkeypatch) -> None:
    payload = {
        "id": 1,
        "children": [
            {"id": i, "type": "comment", "text": f"c{i}", "author": "x", "points": 0, "children": []}
            for i in range(20)
        ],
    }
    _stub_run_cli(monkeypatch, payload)
    out = fetcher.fetch_thread_comments(1, limit=5)
    assert len(out) == 5
    assert [c["id"] for c in out] == [0, 1, 2, 3, 4]


def test_fetch_thread_comments_filters_non_comment_children(monkeypatch) -> None:
    """Stories or polls accidentally nested as 'children' should be skipped — only comments."""
    payload = {
        "id": 1,
        "children": [
            {"id": 10, "type": "comment", "text": "ok", "author": "x", "points": 0, "children": []},
            {"id": 11, "type": "poll", "text": "weird", "author": "y", "points": 0, "children": []},
            {"id": 12, "type": "comment", "text": "also ok", "author": "z", "points": 0, "children": []},
        ],
    }
    _stub_run_cli(monkeypatch, payload)
    out = fetcher.fetch_thread_comments(1, limit=10)
    assert [c["id"] for c in out] == [10, 12]


def test_fetch_thread_comments_returns_empty_when_cli_fails(monkeypatch) -> None:
    _stub_run_cli(monkeypatch, None)
    assert fetcher.fetch_thread_comments(1, limit=5) == []


def test_fetch_thread_comments_returns_empty_when_no_children_field(monkeypatch) -> None:
    _stub_run_cli(monkeypatch, {"id": 1, "type": "story"})  # no children
    assert fetcher.fetch_thread_comments(1, limit=5) == []


def test_fetch_thread_comments_returns_empty_on_unexpected_shape(monkeypatch) -> None:
    _stub_run_cli(monkeypatch, ["this", "is", "not", "a", "story"])
    assert fetcher.fetch_thread_comments(1, limit=5) == []


# ─── fetch_item ────────────────────────────────────────────────────────


def test_fetch_item_extracts_results_from_meta_wrapper(monkeypatch) -> None:
    """The CLI returns {meta, results} for items <id>; we want just `results`."""
    payload = {
        "meta": {"source": "live"},
        "results": {
            "id": 42,
            "by": "alice",
            "title": "Hello",
            "text": "post body here",
            "score": 100,
            "descendants": 5,
            "time": 1778000000,
        },
    }
    captured = _stub_run_cli(monkeypatch, payload)
    out = fetcher.fetch_item(42)
    assert out is not None
    assert out["id"] == 42
    assert out["text"] == "post body here"
    assert captured["args"] == ["items", "42"]


def test_fetch_item_returns_none_on_cli_failure(monkeypatch) -> None:
    _stub_run_cli(monkeypatch, None)
    assert fetcher.fetch_item(42) is None


def test_fetch_item_returns_none_on_missing_results_key(monkeypatch) -> None:
    _stub_run_cli(monkeypatch, {"meta": {"source": "live"}})  # no `results`
    assert fetcher.fetch_item(42) is None


def test_fetch_item_returns_none_when_results_is_not_a_dict(monkeypatch) -> None:
    """Some CLI commands return results as a list (e.g., `items <id>` should
    always be a dict, but defensive check). When unexpected, return None."""
    _stub_run_cli(monkeypatch, {"meta": {"source": "live"}, "results": [1, 2, 3]})
    assert fetcher.fetch_item(42) is None


# ─── search_topic_mentions ─────────────────────────────────────────────


def test_search_topic_mentions_passes_correct_args(monkeypatch) -> None:
    """Algolia search must use --tag comment --since 7d for watchlist topic mentions."""
    captured = _stub_run_cli(monkeypatch, {"hits": []})
    fetcher.search_topic_mentions("claude", limit=5)
    assert captured["args"] == [
        "search", "claude",
        "--tag", "comment",
        "--since", "7d",
        "--hits-per-page", "5",
    ]


def test_search_topic_mentions_returns_compact_hits(monkeypatch) -> None:
    """Each hit should be the Algolia hit dict — caller decides how to render it."""
    payload = {
        "hits": [
            {"objectID": "1", "comment_text": "first hit", "story_title": "Story A",
             "story_url": "https://a.com", "author": "alice", "created_at": "2026-05-09T00:00:00Z",
             "story_id": 100, "parent_id": 99, "_tags": ["comment"]},
            {"objectID": "2", "comment_text": "second hit", "story_title": "Story B",
             "story_url": "https://b.com", "author": "bob", "created_at": "2026-05-08T00:00:00Z",
             "story_id": 200, "parent_id": 199, "_tags": ["comment"]},
        ],
    }
    _stub_run_cli(monkeypatch, payload)
    out = fetcher.search_topic_mentions("claude", limit=5)
    assert len(out) == 2
    assert out[0]["comment_text"] == "first hit"
    assert out[1]["story_title"] == "Story B"


def test_search_topic_mentions_returns_empty_on_no_hits(monkeypatch) -> None:
    _stub_run_cli(monkeypatch, {"hits": []})
    assert fetcher.search_topic_mentions("nonexistent_topic", limit=5) == []


def test_search_topic_mentions_returns_empty_on_cli_failure(monkeypatch) -> None:
    _stub_run_cli(monkeypatch, None)
    assert fetcher.search_topic_mentions("claude", limit=5) == []


def test_search_topic_mentions_returns_empty_on_unexpected_shape(monkeypatch) -> None:
    _stub_run_cli(monkeypatch, {"unexpected": "shape"})
    assert fetcher.search_topic_mentions("claude", limit=5) == []


def test_search_topic_mentions_respects_limit_in_args(monkeypatch) -> None:
    captured = _stub_run_cli(monkeypatch, {"hits": []})
    fetcher.search_topic_mentions("claude", limit=12)
    assert "--hits-per-page" in captured["args"]
    idx = captured["args"].index("--hits-per-page")
    assert captured["args"][idx + 1] == "12"


# ─── timeout passthrough ───────────────────────────────────────────────


def test_fetch_thread_comments_uses_default_timeout(monkeypatch) -> None:
    captured = _stub_run_cli(monkeypatch, {"id": 1, "children": []})
    fetcher.fetch_thread_comments(1, limit=5)
    assert captured["timeout"] == fetcher.PER_CALL_TIMEOUT_S


def test_fetch_item_uses_default_timeout(monkeypatch) -> None:
    captured = _stub_run_cli(monkeypatch, {"meta": {}, "results": {"id": 1}})
    fetcher.fetch_item(1)
    assert captured["timeout"] == fetcher.PER_CALL_TIMEOUT_S


def test_search_topic_mentions_uses_default_timeout(monkeypatch) -> None:
    captured = _stub_run_cli(monkeypatch, {"hits": []})
    fetcher.search_topic_mentions("x", limit=5)
    assert captured["timeout"] == fetcher.PER_CALL_TIMEOUT_S


# ─── module sanity ─────────────────────────────────────────────────────


def test_module_exposes_per_call_timeout_constant() -> None:
    """The helper module should expose PER_CALL_TIMEOUT_S so the orchestrator can reference it."""
    assert isinstance(fetcher.PER_CALL_TIMEOUT_S, int)
    assert 5 <= fetcher.PER_CALL_TIMEOUT_S <= 60


@pytest.mark.parametrize("public_name", [
    "fetch_thread_comments",
    "fetch_item",
    "search_topic_mentions",
    "PER_CALL_TIMEOUT_S",
])
def test_public_api(public_name: str) -> None:
    assert hasattr(fetcher, public_name), f"hackernews_content_fetch must export {public_name}"

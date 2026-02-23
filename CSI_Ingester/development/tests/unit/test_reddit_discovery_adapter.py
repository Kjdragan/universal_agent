from csi_ingester.adapters.base import RawEvent
from csi_ingester.adapters.reddit_discovery import RedditDiscoveryAdapter


def test_reddit_discovery_normalize_and_dedupe_key():
    adapter = RedditDiscoveryAdapter({"subreddits": ["artificial"]})
    raw = RawEvent(
        source="reddit_discovery",
        event_type="subreddit_new_post",
        occurred_at="2026-02-23T00:00:00Z",
        payload={
            "post_id": "abc123",
            "subreddit": "artificial",
            "title": "New model release",
            "url": "https://example.com/post",
            "permalink": "/r/artificial/comments/abc123/new_model_release/",
            "author": "demo_user",
            "score": 42,
            "num_comments": 7,
        },
    )

    event = adapter.normalize(raw)

    assert event.source == "reddit_discovery"
    assert event.event_type == "subreddit_new_post"
    assert event.subject["platform"] == "reddit"
    assert event.subject["post_id"] == "abc123"
    assert event.subject["subreddit"] == "artificial"
    assert event.dedupe_key == "reddit:post:abc123"


def test_reddit_discovery_subreddit_list_supports_strings_and_dicts():
    adapter = RedditDiscoveryAdapter(
        {
            "subreddits": [
                "artificial",
                {"name": "MachineLearning"},
                "artificial",  # duplicate
            ]
        }
    )

    subreddits = adapter._subreddits()  # intentional direct check for normalization behavior
    assert subreddits == ["artificial", "MachineLearning"]


def test_reddit_discovery_subreddit_list_includes_watchlist_file(tmp_path):
    watchlist = tmp_path / "reddit_watchlist.json"
    watchlist.write_text(
        """
        {
          "subreddits": [
            {"name": "artificial"},
            {"subreddit": "LocalLLaMA"},
            "geopolitics"
          ]
        }
        """.strip(),
        encoding="utf-8",
    )
    adapter = RedditDiscoveryAdapter(
        {
            "subreddits": ["artificial", {"name": "MachineLearning"}],
            "watchlist_file": str(watchlist),
        }
    )
    subreddits = adapter._subreddits()
    assert subreddits == ["artificial", "MachineLearning", "LocalLLaMA", "geopolitics"]

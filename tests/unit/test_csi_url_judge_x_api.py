"""Tests for the X API tweet-URL fetch fallback (Tier B1).

Verifies that:
- ``parse_tweet_id_from_url`` extracts numeric IDs from x.com / twitter.com
  /status/<id> URLs and returns None for non-tweet URLs.
- ``extract_tweet_urls`` separates tweet URLs from a mixed list, returning
  the (url, tweet_id) pairs and the remaining URLs.
- ``_x_api_tweet_fetch_enabled`` honors the ``UA_CSI_X_API_TWEET_FETCH_ENABLED``
  kill switch.
- ``fetch_tweet_by_id`` issues the correct GET to api.x.com/2/tweets/{id}
  with the expected params and auth header.
- ``enrich_urls`` routes tweet URLs to the X API fetch path (not the legacy
  social_noise filter) when the kill switch is enabled, and falls back to
  social_noise when disabled.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

# ── parse_tweet_id_from_url ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://x.com/foo/status/123", "123"),
        ("https://www.x.com/foo/status/123", "123"),
        ("http://x.com/foo/status/123", "123"),
        ("https://x.com/foo/status/123?s=20", "123"),
        ("https://x.com/foo/status/123/photo/1", "123"),
        ("https://twitter.com/foo/status/9876543210", "9876543210"),
        ("https://www.twitter.com/foo/status/123", "123"),
        ("HTTPS://X.COM/FOO/STATUS/123", "123"),
    ],
)
def test_parse_tweet_id_matches(url, expected):
    from universal_agent.services.csi_url_judge import parse_tweet_id_from_url

    assert parse_tweet_id_from_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://x.com/foo",
        "https://x.com/foo/bar",
        "https://example.com/foo/status/123",
        "https://x.com.evil.com/foo/status/123",
        "not a url",
    ],
)
def test_parse_tweet_id_rejects_non_tweet_urls(url):
    from universal_agent.services.csi_url_judge import parse_tweet_id_from_url

    assert parse_tweet_id_from_url(url) is None


def test_parse_tweet_id_handles_none_safely():
    from universal_agent.services.csi_url_judge import parse_tweet_id_from_url

    # type-checker hates this but we still want runtime resilience
    assert parse_tweet_id_from_url(None) is None  # type: ignore[arg-type]


# ── extract_tweet_urls ───────────────────────────────────────────────────────


def test_extract_tweet_urls_splits_mixed_list():
    from universal_agent.services.csi_url_judge import extract_tweet_urls

    inputs = [
        "https://x.com/foo/status/111",
        "https://docs.example.com/page",
        "https://twitter.com/bar/status/222?s=20",
        "https://github.com/foo/repo",
        "https://x.com/foo",  # not a tweet URL
    ]
    tweets, remaining = extract_tweet_urls(inputs)
    assert tweets == [
        ("https://x.com/foo/status/111", "111"),
        ("https://twitter.com/bar/status/222?s=20", "222"),
    ]
    assert remaining == [
        "https://docs.example.com/page",
        "https://github.com/foo/repo",
        "https://x.com/foo",
    ]


def test_extract_tweet_urls_strips_trailing_noise():
    from universal_agent.services.csi_url_judge import extract_tweet_urls

    tweets, remaining = extract_tweet_urls(
        ["https://x.com/foo/status/123).", "  "]
    )
    assert tweets == [("https://x.com/foo/status/123", "123")]
    assert remaining == []


def test_extract_tweet_urls_handles_empty():
    from universal_agent.services.csi_url_judge import extract_tweet_urls

    tweets, remaining = extract_tweet_urls([])
    assert tweets == []
    assert remaining == []


# ── kill switch ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off"])
def test_kill_switch_disables(monkeypatch, value):
    monkeypatch.setenv("UA_CSI_X_API_TWEET_FETCH_ENABLED", value)
    from universal_agent.services.csi_url_judge import _x_api_tweet_fetch_enabled

    assert _x_api_tweet_fetch_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", ""])
def test_kill_switch_enabled_by_default(monkeypatch, value):
    monkeypatch.setenv("UA_CSI_X_API_TWEET_FETCH_ENABLED", value)
    from universal_agent.services.csi_url_judge import _x_api_tweet_fetch_enabled

    assert _x_api_tweet_fetch_enabled() is True


def test_kill_switch_unset_defaults_to_enabled(monkeypatch):
    monkeypatch.delenv("UA_CSI_X_API_TWEET_FETCH_ENABLED", raising=False)
    from universal_agent.services.csi_url_judge import _x_api_tweet_fetch_enabled

    assert _x_api_tweet_fetch_enabled() is True


# ── fetch_tweet_by_id ────────────────────────────────────────────────────────


def test_fetch_tweet_by_id_issues_expected_request():
    from universal_agent.services.claude_code_intel import fetch_tweet_by_id

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "data": {"id": "123", "text": "hello", "author_id": "9"},
                "includes": {
                    "users": [{"id": "9", "name": "X", "username": "x"}]
                },
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        payload = fetch_tweet_by_id(client, token="test-bearer", tweet_id="123")

    assert payload["data"]["id"] == "123"
    url = str(captured["url"])
    assert url.startswith("https://api.x.com/2/tweets/123")
    assert "tweet.fields=" in url
    assert "expansions=author_id" in url
    assert captured["headers"]["authorization"] == "Bearer test-bearer"


# ── enrich_urls integration: kill switch routes correctly ────────────────────


def test_enrich_urls_skips_x_api_path_when_disabled(monkeypatch, tmp_path: Path):
    """When the kill switch is off, tweet URLs go through the legacy filter."""
    monkeypatch.setenv("UA_CSI_X_API_TWEET_FETCH_ENABLED", "0")
    from universal_agent.services import csi_url_judge

    importlib.reload(csi_url_judge)

    records = csi_url_judge.enrich_urls(
        urls=["https://x.com/foo/status/111"],
        context="ctx",
        output_dir=tmp_path,
        trust_source=False,
    )
    # With the kill switch off, the tweet URL flows through pre_filter_urls
    # which classifies x.com as social_noise.
    assert len(records) == 1
    assert records[0].url == "https://x.com/foo/status/111"
    assert records[0].fetch_status == "filtered"
    assert records[0].skip_reason == "social_domain"


def test_enrich_urls_takes_x_api_path_when_enabled_but_no_auth(
    monkeypatch, tmp_path: Path
):
    """With the switch on but no creds, we downgrade to social_noise so the
    behavior matches the pre-Tier-B1 pipeline."""
    monkeypatch.setenv("UA_CSI_X_API_TWEET_FETCH_ENABLED", "1")
    # Strip all X creds so the X API helper returns x_api_no_auth.
    for key in (
        "X_BEARER_TOKEN",
        "X_OAUTH_CONSUMER_KEY",
        "X_OAUTH_CONSUMER_SECRET",
        "X_OAUTH_ACCESS_TOKEN",
        "X_OAUTH_ACCESS_TOKEN_SECRET",
        "X_OAUTH2_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)

    from universal_agent.services import csi_url_judge

    importlib.reload(csi_url_judge)

    # Patch get_x_bearer_token at import-time to return empty.
    with patch(
        "universal_agent.services.claude_code_intel.get_x_bearer_token",
        return_value="",
    ):
        records = csi_url_judge.enrich_urls(
            urls=["https://x.com/foo/status/111"],
            context="ctx",
            output_dir=tmp_path,
            trust_source=False,
        )

    assert len(records) == 1
    assert records[0].fetch_status == "filtered"
    assert records[0].skip_reason == "social_domain"

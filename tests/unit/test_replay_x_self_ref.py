"""Tests for the t.co → x.com self-reference fetch path.

When a tweet links to another tweet via a t.co shortlink (or directly to
x.com), the browser-style fetch returns the JS-gated SPA chrome and the
link is skipped as ``browser_gated_x_page``. This path dispatches to the
X API ``/2/tweets/{id}`` endpoint so the referenced tweet's body becomes
real grounded content instead of being silently dropped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from universal_agent.services import (
    claude_code_intel_replay as ccir,
    csi_url_judge as cuj,
)


class _StubXApiRecord:
    """Mimics a successful csi_url_judge.EnrichmentRecord."""

    def __init__(self, *, content_path: str, content_chars: int = 200) -> None:
        self.fetch_status = "fetched"
        self.content_path = content_path
        self.content_chars = content_chars
        self.skip_reason = ""


def _write_tweet_md(tmp_path: Path, *, tweet_id: str, text: str) -> Path:
    path = tmp_path / f"tweet_{tweet_id}.md"
    path.write_text(
        f"# Tweet: https://x.com/bcherny/status/{tweet_id}\n\n"
        f"- **Author**: bcherny\n\n---\n\n{text}\n",
        encoding="utf-8",
    )
    return path


def test_try_x_api_tweet_self_ref_disabled_by_env(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_CSI_LINKED_X_SELF_REF_ENABLED", "0")
    assert (
        ccir._try_x_api_tweet_self_ref(
            final_url="https://x.com/bcherny/status/123",
            entry={"url": "https://t.co/abc", "tier": 4, "post_id": "p1"},
            source_dir=tmp_path,
        )
        is None
    )


def test_try_x_api_tweet_self_ref_returns_none_for_non_tweet_urls(monkeypatch, tmp_path):
    """A x.com URL that isn't /status/<id> must not be misrouted."""
    monkeypatch.setattr(cuj, "parse_tweet_id_from_url", lambda url: None)
    assert (
        ccir._try_x_api_tweet_self_ref(
            final_url="https://x.com/bcherny",
            entry={"url": "https://t.co/abc", "tier": 4, "post_id": "p1"},
            source_dir=tmp_path,
        )
        is None
    )


def test_try_x_api_tweet_self_ref_success(monkeypatch, tmp_path):
    """Happy path: tweet ID parsed, X API returns fetched, content returned."""
    tweet_md = _write_tweet_md(tmp_path, tweet_id="999", text="Real tweet body prose.")

    monkeypatch.setattr(cuj, "parse_tweet_id_from_url", lambda url: "999")
    monkeypatch.setattr(
        cuj,
        "_fetch_tweet_via_x_api",
        lambda url, tweet_id, source_dir, *, timeout: _StubXApiRecord(
            content_path=str(tweet_md), content_chars=tweet_md.stat().st_size
        ),
    )

    result = ccir._try_x_api_tweet_self_ref(
        final_url="https://x.com/bcherny/status/999",
        entry={"url": "https://t.co/abc", "tier": 4, "post_id": "p1"},
        source_dir=tmp_path,
    )
    assert result is not None
    content, metadata = result
    assert "Real tweet body prose." in content
    assert metadata["fetch_method"] == "x_api_self_ref"
    assert metadata["source_type"] == "x_tweet"
    assert metadata["tweet_id"] == "999"
    assert metadata["final_url"] == "https://x.com/bcherny/status/999"


def test_try_x_api_tweet_self_ref_returns_none_on_x_api_failure(monkeypatch, tmp_path):
    """When the X API path fails, return None so caller falls through."""

    class FailedRecord:
        fetch_status = "failed"
        content_path = ""
        content_chars = 0
        skip_reason = "x_api_no_auth"

    monkeypatch.setattr(cuj, "parse_tweet_id_from_url", lambda url: "999")
    monkeypatch.setattr(
        cuj,
        "_fetch_tweet_via_x_api",
        lambda url, tweet_id, source_dir, *, timeout: FailedRecord(),
    )

    assert (
        ccir._try_x_api_tweet_self_ref(
            final_url="https://x.com/bcherny/status/999",
            entry={"url": "https://t.co/abc", "tier": 4, "post_id": "p1"},
            source_dir=tmp_path,
        )
        is None
    )


def test_try_x_api_tweet_self_ref_handles_missing_content_file(monkeypatch, tmp_path):
    """X API claims success but content file doesn't exist — return None."""
    monkeypatch.setattr(cuj, "parse_tweet_id_from_url", lambda url: "999")
    monkeypatch.setattr(
        cuj,
        "_fetch_tweet_via_x_api",
        lambda url, tweet_id, source_dir, *, timeout: _StubXApiRecord(
            content_path=str(tmp_path / "nope.md")
        ),
    )
    assert (
        ccir._try_x_api_tweet_self_ref(
            final_url="https://x.com/bcherny/status/999",
            entry={"url": "https://t.co/abc", "tier": 4, "post_id": "p1"},
            source_dir=tmp_path,
        )
        is None
    )


def test_fetch_linked_source_uses_x_self_ref_for_browser_gated_tweets(monkeypatch, tmp_path):
    """End-to-end: a t.co URL redirecting to x.com/.../status/N gets fetched
    via X API instead of being marked skipped.
    """
    tweet_md = _write_tweet_md(tmp_path, tweet_id="888", text="Substantive tweet content.")

    class FakeResponse:
        status_code = 200
        url = "https://x.com/bcherny/status/888"
        headers = {"content-type": "text/html"}
        text = (
            "<html><body>"
            "<p>JavaScript is not available.</p>"
            "<p>We've detected that JavaScript is disabled in this browser.</p>"
            "</body></html>"
        )

    class FakeClient:
        def get(self, url):
            return FakeResponse()

    # Wire up X API success.
    monkeypatch.setattr(
        cuj, "parse_tweet_id_from_url", lambda url: "888" if "888" in url else None
    )
    monkeypatch.setattr(
        cuj,
        "_fetch_tweet_via_x_api",
        lambda url, tweet_id, source_dir, *, timeout: _StubXApiRecord(
            content_path=str(tweet_md), content_chars=tweet_md.stat().st_size
        ),
    )

    entry: dict[str, Any] = {
        "url": "https://t.co/abc",
        "post_id": "p1",
        "tier": 4,
        "action_type": "strategic_follow_up",
    }
    source_dir = tmp_path / "src"
    source_dir.mkdir()

    ccir._fetch_linked_source(
        client=FakeClient(), url="https://t.co/abc", entry=entry, source_dir=source_dir
    )

    assert entry["fetch_status"] == "fetched"
    assert entry.get("skip_reason", "") == ""
    # Source markdown must be written, not the analysis-only stub.
    source_md = source_dir / "source.md"
    assert source_md.exists()
    assert "Substantive tweet content." in source_md.read_text(encoding="utf-8")
    # Metadata records the X API method for auditability.
    metadata = json.loads((source_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["fetch_method"] == "x_api_self_ref"
    assert metadata["final_url"] == "https://x.com/bcherny/status/888"
    assert metadata["source_type"] == "x_tweet"


def test_fetch_linked_source_falls_through_when_x_api_disabled(monkeypatch, tmp_path):
    """When the env switch is off, browser-gated tweets remain skipped."""
    monkeypatch.setenv("UA_CSI_LINKED_X_SELF_REF_ENABLED", "0")

    class FakeResponse:
        status_code = 200
        url = "https://x.com/bcherny/status/888"
        headers = {"content-type": "text/html"}
        text = (
            "<html><body>"
            "<p>JavaScript is not available.</p>"
            "<p>We've detected that JavaScript is disabled in this browser.</p>"
            "</body></html>"
        )

    class FakeClient:
        def get(self, url):
            return FakeResponse()

    entry: dict[str, Any] = {
        "url": "https://t.co/abc",
        "post_id": "p1",
        "tier": 4,
        "action_type": "strategic_follow_up",
    }
    source_dir = tmp_path / "src"
    source_dir.mkdir()

    ccir._fetch_linked_source(
        client=FakeClient(), url="https://t.co/abc", entry=entry, source_dir=source_dir
    )

    assert entry["fetch_status"] == "skipped"
    assert entry["skip_reason"] == "browser_gated_x_page"

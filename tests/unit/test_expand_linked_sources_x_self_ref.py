"""Regression: x.com URLs in linked-source lists hit the X-API self-ref path.

PR #336 wired the X-API self-reference fetch into `_fetch_linked_source`,
but `expand_linked_sources` pre-filters x.com URLs via
`_should_skip_link_fetch` BEFORE the fetcher runs. So a tweet whose
action.links contains a direct x.com URL (e.g. a t.co already resolved by
the upstream tweet payload) never reached the Move-2 code path and stayed
marked as `unsupported_or_opaque_source`. This test pins the corrected
behavior: when the URL parses as an x.com /status/<id>, the X-API path is
tried before the legacy skip.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from universal_agent.services import claude_code_intel_replay as ccir
from universal_agent.services import csi_url_judge as cuj


class _StubXApiRecord:
    def __init__(self, *, content_path: str, content_chars: int = 200) -> None:
        self.fetch_status = "fetched"
        self.content_path = content_path
        self.content_chars = content_chars
        self.skip_reason = ""


def _write_tweet_md(tmp_path: Path, *, tweet_id: str, text: str) -> Path:
    path = tmp_path / f"tweet_{tweet_id}.md"
    path.write_text(
        f"# Tweet: https://x.com/bcherny/status/{tweet_id}\n\n---\n\n{text}\n",
        encoding="utf-8",
    )
    return path


def _packet_with_x_link(tmp_path: Path, x_url: str) -> Path:
    packet_dir = tmp_path / "packet"
    packet_dir.mkdir()
    (packet_dir / "linked_sources.json").write_text(
        json.dumps(
            [
                {
                    "url": x_url,
                    "post_id": "p1",
                    "tier": 4,
                    "action_type": "strategic_follow_up",
                }
            ]
        ),
        encoding="utf-8",
    )
    return packet_dir


def test_x_direct_url_routes_through_x_api_self_ref(monkeypatch, tmp_path):
    """A direct x.com /status URL in linked sources hits the X-API path."""
    tweet_md = _write_tweet_md(tmp_path, tweet_id="555", text="The workaround is X.")
    monkeypatch.setattr(cuj, "parse_tweet_id_from_url", lambda url: "555" if "555" in url else None)
    monkeypatch.setattr(
        cuj,
        "_fetch_tweet_via_x_api",
        lambda url, tweet_id, source_dir, *, timeout: _StubXApiRecord(
            content_path=str(tweet_md), content_chars=tweet_md.stat().st_size
        ),
    )

    packet_dir = _packet_with_x_link(tmp_path, "https://x.com/bcherny/status/555?s=46")
    actions = [
        {"post_id": "p1", "tier": 4, "links": ["https://x.com/bcherny/status/555?s=46"]}
    ]

    entries = ccir.expand_linked_sources(
        packet_dir=packet_dir, actions=actions, enabled=True
    )
    assert len(entries) == 1
    assert entries[0]["fetch_status"] == "fetched"
    assert entries[0].get("skip_reason", "") == ""


def test_x_direct_url_falls_through_when_x_api_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("UA_CSI_LINKED_X_SELF_REF_ENABLED", "0")
    packet_dir = _packet_with_x_link(tmp_path, "https://x.com/bcherny/status/666?s=46")
    actions = [
        {"post_id": "p1", "tier": 4, "links": ["https://x.com/bcherny/status/666?s=46"]}
    ]

    entries = ccir.expand_linked_sources(
        packet_dir=packet_dir, actions=actions, enabled=True
    )
    assert entries[0]["fetch_status"] == "skipped"
    assert entries[0]["skip_reason"] == "unsupported_or_opaque_source"


def test_x_direct_url_falls_through_when_x_api_returns_failure(monkeypatch, tmp_path):
    """X API returns failure → legacy skip behavior preserved."""

    class FailedRecord:
        fetch_status = "failed"
        content_path = ""
        content_chars = 0
        skip_reason = "x_api_no_auth"

    monkeypatch.setattr(cuj, "parse_tweet_id_from_url", lambda url: "777")
    monkeypatch.setattr(
        cuj,
        "_fetch_tweet_via_x_api",
        lambda url, tweet_id, source_dir, *, timeout: FailedRecord(),
    )

    packet_dir = _packet_with_x_link(tmp_path, "https://x.com/bcherny/status/777?s=46")
    actions = [
        {"post_id": "p1", "tier": 4, "links": ["https://x.com/bcherny/status/777?s=46"]}
    ]

    entries = ccir.expand_linked_sources(
        packet_dir=packet_dir, actions=actions, enabled=True
    )
    assert entries[0]["fetch_status"] == "skipped"
    assert entries[0]["skip_reason"] == "unsupported_or_opaque_source"


def test_non_tweet_x_url_still_skipped(monkeypatch, tmp_path):
    """A non-status x.com URL (e.g. profile page) must remain skipped."""
    monkeypatch.setattr(cuj, "parse_tweet_id_from_url", lambda url: None)

    packet_dir = _packet_with_x_link(tmp_path, "https://x.com/bcherny")
    actions = [{"post_id": "p1", "tier": 4, "links": ["https://x.com/bcherny"]}]

    entries = ccir.expand_linked_sources(
        packet_dir=packet_dir, actions=actions, enabled=True
    )
    assert entries[0]["fetch_status"] == "skipped"
    assert entries[0]["skip_reason"] == "unsupported_or_opaque_source"

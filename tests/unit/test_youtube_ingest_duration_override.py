"""Tests for the per-channel duration override on the pre-ingest triage gate.

The gold poller adds Lex Fridman videos to the digest with a 86400s cap;
this test confirms `_should_skip_video_by_metadata` honors that override
and lets long videos through that would otherwise be triaged out.
"""
from __future__ import annotations

from universal_agent.youtube_ingest import _should_skip_video_by_metadata


def test_default_cap_blocks_long_videos():
    # 2-hour video, no override → skipped
    skip, reason = _should_skip_video_by_metadata({"duration": 7200})
    assert skip is True
    assert "video too long" in reason
    assert "5400s" in reason


def test_default_cap_allows_short_enough_videos():
    skip, _ = _should_skip_video_by_metadata({"duration": 1800})
    assert skip is False


def test_override_raises_cap_to_24h():
    # 4-hour video with Lex-style override → not skipped
    skip, _ = _should_skip_video_by_metadata(
        {"duration": 14400},
        max_duration_seconds_override=86400,
    )
    assert skip is False


def test_override_still_blocks_above_override():
    # 25-hour video with 24h override → skipped, message references override
    skip, reason = _should_skip_video_by_metadata(
        {"duration": 90000},
        max_duration_seconds_override=86400,
    )
    assert skip is True
    assert "86400s" in reason


def test_override_none_falls_back_to_default():
    # 2-hour video with override=None → default cap applies → skipped
    skip, _ = _should_skip_video_by_metadata(
        {"duration": 7200},
        max_duration_seconds_override=None,
    )
    assert skip is True


def test_override_zero_falls_back_to_default():
    # 2-hour video with override=0 (invalid sentinel) → default cap applies
    skip, _ = _should_skip_video_by_metadata(
        {"duration": 7200},
        max_duration_seconds_override=0,
    )
    assert skip is True


def test_short_videos_still_blocked_under_override():
    # 10s video; even with a 24h override the min-duration filter fires
    skip, reason = _should_skip_video_by_metadata(
        {"duration": 10},
        max_duration_seconds_override=86400,
    )
    assert skip is True
    assert "too short" in reason

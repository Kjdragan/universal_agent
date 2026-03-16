"""Tests for tutorial_telegram_notifier per-video dedup of youtube_tutorial_ready."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from universal_agent.services import tutorial_telegram_notifier


def _make_ready_payload(video_id: str, *, title: str = "Test Video") -> dict:
    return {
        "kind": "youtube_tutorial_ready",
        "title": "YouTube Tutorial Artifacts Ready",
        "message": f"{title} artifacts are ready for review. (video_id: {video_id})",
        "severity": "success",
        "metadata": {
            "video_id": video_id,
            "tutorial_status": "full",
            "tutorial_run_path": f"youtube-tutorial-creation/2026-03-16/test_{video_id}",
            "tutorial_key_files": [{"label": "README"}],
        },
    }


def test_duplicate_youtube_tutorial_ready_suppressed(monkeypatch):
    """Second youtube_tutorial_ready for same video_id within cooldown is suppressed."""
    monkeypatch.setattr(tutorial_telegram_notifier, "VIDEO_READY_DEDUP_SECONDS", 60.0)
    # Clear module-level state
    tutorial_telegram_notifier._video_ready_last_sent.clear()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("YOUTUBE_TUTORIAL_TELEGRAM_CHAT_ID", "-100123")

    sent_messages: list[str] = []

    with patch.object(
        tutorial_telegram_notifier,
        "_send",
        side_effect=lambda text: (sent_messages.append(text), True)[-1],
    ):
        payload = _make_ready_payload("vid123")
        result1 = tutorial_telegram_notifier.maybe_send(payload)
        assert result1 is True

        # Second call for same video_id should be suppressed
        result2 = tutorial_telegram_notifier.maybe_send(payload)
        assert result2 is False

    assert len(sent_messages) == 1


def test_different_video_ids_are_not_suppressed(monkeypatch):
    """Different video IDs should each get their own notification."""
    monkeypatch.setattr(tutorial_telegram_notifier, "VIDEO_READY_DEDUP_SECONDS", 60.0)
    tutorial_telegram_notifier._video_ready_last_sent.clear()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("YOUTUBE_TUTORIAL_TELEGRAM_CHAT_ID", "-100123")

    sent_messages: list[str] = []

    with patch.object(
        tutorial_telegram_notifier,
        "_send",
        side_effect=lambda text: (sent_messages.append(text), True)[-1],
    ):
        result1 = tutorial_telegram_notifier.maybe_send(_make_ready_payload("vid_a"))
        assert result1 is True

        result2 = tutorial_telegram_notifier.maybe_send(_make_ready_payload("vid_b"))
        assert result2 is True

    assert len(sent_messages) == 2


def test_dedup_expires_after_cooldown(monkeypatch):
    """After cooldown expires, a second notification for the same video should succeed."""
    monkeypatch.setattr(tutorial_telegram_notifier, "VIDEO_READY_DEDUP_SECONDS", 0.0)
    tutorial_telegram_notifier._video_ready_last_sent.clear()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("YOUTUBE_TUTORIAL_TELEGRAM_CHAT_ID", "-100123")

    sent_messages: list[str] = []

    with patch.object(
        tutorial_telegram_notifier,
        "_send",
        side_effect=lambda text: (sent_messages.append(text), True)[-1],
    ):
        result1 = tutorial_telegram_notifier.maybe_send(_make_ready_payload("vid_c"))
        assert result1 is True

        # With 0s cooldown, the dedup should have expired immediately
        result2 = tutorial_telegram_notifier.maybe_send(_make_ready_payload("vid_c"))
        assert result2 is True

    assert len(sent_messages) == 2


def test_non_ready_kinds_not_affected(monkeypatch):
    """Non youtube_tutorial_ready kinds should not be affected by dedup."""
    tutorial_telegram_notifier._video_ready_last_sent.clear()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("YOUTUBE_TUTORIAL_TELEGRAM_CHAT_ID", "-100123")

    sent_messages: list[str] = []

    with patch.object(
        tutorial_telegram_notifier,
        "_send",
        side_effect=lambda text: (sent_messages.append(text), True)[-1],
    ):
        payload = {
            "kind": "youtube_playlist_new_video",
            "title": "New Tutorial Video Detected",
            "message": "Test Video — queued for processing",
            "metadata": {"video_id": "vid123", "video_url": "https://youtube.com/watch?v=vid123"},
        }
        result1 = tutorial_telegram_notifier.maybe_send(payload)
        result2 = tutorial_telegram_notifier.maybe_send(payload)
        assert result1 is True
        assert result2 is True

    assert len(sent_messages) == 2

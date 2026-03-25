"""Tests for tutorial_telegram_notifier per-video dedup of youtube_tutorial_ready."""

from __future__ import annotations

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


@pytest.fixture(autouse=True)
def _reset_telegram_state(monkeypatch, tmp_path):
    tutorial_telegram_notifier._video_ready_last_sent.clear()
    tutorial_telegram_notifier._video_new_last_sent.clear()
    tutorial_telegram_notifier._video_failed_last_sent.clear()
    tutorial_telegram_notifier._health_alert_last_sent.clear()
    tutorial_telegram_notifier._video_message_state_cache = None
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("YOUTUBE_TUTORIAL_TELEGRAM_CHAT_ID", "-100123")
    monkeypatch.setenv("UA_OPS_DIR", str(tmp_path))
    state_path = tutorial_telegram_notifier._state_path()
    if state_path.exists():
        state_path.unlink()
    yield


def test_duplicate_youtube_tutorial_ready_suppressed(monkeypatch):
    """Second youtube_tutorial_ready for same video_id within cooldown is suppressed."""
    monkeypatch.setattr(tutorial_telegram_notifier, "VIDEO_READY_DEDUP_SECONDS", 60.0)
    sent_messages: list[str] = []

    with patch.object(
        tutorial_telegram_notifier,
        "_send_with_message_id",
        side_effect=lambda text: (sent_messages.append(text), (True, len(sent_messages)))[1],
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
    sent_messages: list[str] = []

    with patch.object(
        tutorial_telegram_notifier,
        "_send_with_message_id",
        side_effect=lambda text: (sent_messages.append(text), (True, len(sent_messages)))[1],
    ):
        result1 = tutorial_telegram_notifier.maybe_send(_make_ready_payload("vid_a"))
        assert result1 is True

        result2 = tutorial_telegram_notifier.maybe_send(_make_ready_payload("vid_b"))
        assert result2 is True

    assert len(sent_messages) == 2


def test_dedup_expires_after_cooldown(monkeypatch):
    """If lifecycle state is unavailable, cooldown expiry allows a resend."""
    monkeypatch.setattr(tutorial_telegram_notifier, "VIDEO_READY_DEDUP_SECONDS", 0.0)
    sent_messages: list[str] = []

    with patch.object(
        tutorial_telegram_notifier,
        "_send_with_message_id",
        side_effect=lambda text: (sent_messages.append(text), (True, len(sent_messages)))[1],
    ):
        result1 = tutorial_telegram_notifier.maybe_send(_make_ready_payload("vid_c"))
        assert result1 is True

        tutorial_telegram_notifier._video_message_state_cache = {}
        state_path = tutorial_telegram_notifier._state_path()
        if state_path.exists():
            state_path.unlink()

        result2 = tutorial_telegram_notifier.maybe_send(_make_ready_payload("vid_c"))
        assert result2 is True

    assert len(sent_messages) == 2


def test_new_video_kind_is_deduped(monkeypatch):
    """youtube_playlist_new_video should also be deduped per video_id."""
    monkeypatch.setattr(tutorial_telegram_notifier, "VIDEO_NEW_DEDUP_SECONDS", 60.0)
    sent_messages: list[str] = []

    with patch.object(
        tutorial_telegram_notifier,
        "_send_with_message_id",
        side_effect=lambda text: (sent_messages.append(text), (True, len(sent_messages)))[1],
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
        assert result2 is False  # second should be suppressed

    assert len(sent_messages) == 1


def test_non_deduped_kinds_not_affected(monkeypatch):
    """Kinds without per-video dedup should not be affected."""
    sent_messages: list[str] = []

    with patch.object(
        tutorial_telegram_notifier,
        "_send",
        side_effect=lambda text: (sent_messages.append(text), True)[-1],
    ):
        payload = {
            "kind": "youtube_hook_recovery_queued",
            "title": "Recovery Queued",
            "message": "Test recovery",
            "metadata": {"video_id": "vid123"},
        }
        result1 = tutorial_telegram_notifier.maybe_send(payload)
        result2 = tutorial_telegram_notifier.maybe_send(payload)
        assert result1 is True
        assert result2 is True

    assert len(sent_messages) == 2

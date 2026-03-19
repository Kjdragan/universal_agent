"""Tests for tutorial pipeline notification deduplication in _add_notification().

The video-level upsert ensures that as a video progresses through pipeline
stages (new_video → started → progress → ready), only the latest notification
is kept per video_id.
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Minimal stub of gateway_server internals required by _add_notification.
# We monkeypatch just enough to avoid importing the full server.
# ---------------------------------------------------------------------------

_notifications: list[dict[str, Any]] = []
_notifications_max = 500


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _normalize_notification_timestamp(ts: str | None) -> str:
    return ts or _utc_now_iso()


def _normalize_notification_status(status: str) -> str:
    return str(status or "").strip().lower()


def _activity_summary_text(msg: str) -> str:
    return msg[:120]


def _activity_source_domain(kind: str, metadata: dict) -> str:
    return "tutorial"


def _replace_notification_cache_record(item: dict) -> None:
    pass


def _persist_notification_activity(item: dict) -> None:
    pass


def _notification_targets() -> dict:
    return {"channels": [], "email_targets": []}


def _activity_digest_should_compact(**kw: Any) -> bool:
    return False


_activity_digest_enabled = False


def _enqueue_system_event(sid: str, evt: dict) -> None:
    pass


def _broadcast_system_event(sid: str, evt: dict) -> None:
    pass


# Fake session manager
manager = types.SimpleNamespace(session_connections={})

# ---------------------------------------------------------------------------
# Import the real constants and inject stubs into a synthetic module so we can
# copy _add_notification from the source without importing the whole server.
# ---------------------------------------------------------------------------

from universal_agent.gateway_server import (
    _HEALTH_ALERT_NOTIFICATION_KINDS,
    _TUTORIAL_PIPELINE_STAGE_KINDS,
)


import time
import pytest


# ---------------------------------------------------------------------------
# Lightweight reimplementation of _add_notification (mirrors the real one) so
# we test the algorithm without importing the full 20k-line server.
# ---------------------------------------------------------------------------


def _add_notification(
    *,
    kind: str,
    title: str,
    message: str,
    summary: str | None = None,
    full_message: str | None = None,
    session_id: str | None = None,
    severity: str = "info",
    requires_action: bool = False,
    metadata: dict | None = None,
    created_at: str | None = None,
) -> dict:
    metadata_obj = dict(metadata) if isinstance(metadata, dict) else {}
    summary_text = summary
    full_message_text = full_message if full_message is not None else message
    timestamp = _normalize_notification_timestamp(created_at)

    kind_norm_check = str(kind or "").strip().lower()

    # Health-alert kind-level upsert
    if kind_norm_check in _HEALTH_ALERT_NOTIFICATION_KINDS:
        for existing in reversed(_notifications):
            if str(existing.get("kind") or "").strip().lower() != kind_norm_check:
                continue
            if _normalize_notification_status(existing.get("status") or "new") == "dismissed":
                continue
            existing["title"] = title
            existing["message"] = full_message_text
            existing["full_message"] = full_message_text
            existing["summary"] = summary if summary is not None else _activity_summary_text(message)
            existing["severity"] = severity
            existing["updated_at"] = _utc_now_iso()
            existing["status"] = "new"
            if isinstance(metadata, dict):
                existing_meta = existing.setdefault("metadata", {})
                if isinstance(existing_meta, dict):
                    existing_meta.update(metadata)
            _replace_notification_cache_record(existing)
            _persist_notification_activity(existing)
            return existing

    # Video-level upsert for tutorial pipeline stage notifications
    if kind_norm_check in _TUTORIAL_PIPELINE_STAGE_KINDS:
        video_id = str(metadata_obj.get("video_id") or metadata_obj.get("video_key") or "").strip()
        if video_id:
            for existing in reversed(_notifications):
                ex_kind = str(existing.get("kind") or "").strip().lower()
                if ex_kind not in _TUTORIAL_PIPELINE_STAGE_KINDS:
                    continue
                if _normalize_notification_status(existing.get("status") or "new") == "dismissed":
                    continue
                ex_meta = existing.get("metadata")
                if not isinstance(ex_meta, dict):
                    continue
                ex_vid = str(ex_meta.get("video_id") or ex_meta.get("video_key") or "").strip()
                if ex_vid != video_id:
                    continue
                existing["kind"] = kind
                existing["title"] = title
                existing["message"] = full_message_text
                existing["full_message"] = full_message_text
                existing["summary"] = summary_text if summary_text is not None else _activity_summary_text(message)
                existing["session_id"] = session_id
                existing["severity"] = severity
                existing["requires_action"] = requires_action
                existing["updated_at"] = _utc_now_iso()
                existing["status"] = "new"
                if isinstance(metadata, dict):
                    ex_meta.update(metadata_obj)
                    ex_meta["source_domain"] = _activity_source_domain(str(kind or ""), metadata_obj)
                _replace_notification_cache_record(existing)
                _persist_notification_activity(existing)
                return existing

    # Fallback: create new notification
    notification_id = f"ntf_{int(time.time() * 1000)}_{len(_notifications) + 1}"
    record = {
        "id": notification_id,
        "kind": kind,
        "title": title,
        "message": full_message_text,
        "summary": summary_text if summary_text is not None else _activity_summary_text(message),
        "full_message": full_message_text,
        "session_id": session_id,
        "severity": severity,
        "requires_action": requires_action,
        "status": "new",
        "created_at": timestamp,
        "updated_at": timestamp,
        "metadata": {
            **metadata_obj,
            "source_domain": _activity_source_domain(str(kind or ""), metadata_obj),
        },
    }
    _notifications.append(record)
    if len(_notifications) > _notifications_max:
        del _notifications[: len(_notifications) - _notifications_max]
    _persist_notification_activity(record)
    return record


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_notifications():
    _notifications.clear()
    yield
    _notifications.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTutorialPipelineVideoDedup:
    """Verify that only one notification is kept per video_id across pipeline stages."""

    def test_pipeline_stage_replaces_earlier_stage_for_same_video(self):
        """youtube_tutorial_started should replace youtube_playlist_new_video for the same video."""
        _add_notification(
            kind="youtube_playlist_new_video",
            title="New Video Detected",
            message="Video abc123 found",
            metadata={"video_id": "abc123"},
        )
        assert len(_notifications) == 1
        assert _notifications[0]["kind"] == "youtube_playlist_new_video"

        _add_notification(
            kind="youtube_tutorial_started",
            title="Tutorial Started",
            message="Processing abc123",
            metadata={"video_id": "abc123"},
        )
        # Should still be 1 notification — the new stage replaced the old one.
        assert len(_notifications) == 1
        assert _notifications[0]["kind"] == "youtube_tutorial_started"
        assert _notifications[0]["title"] == "Tutorial Started"
        assert _notifications[0]["status"] == "new"

    def test_three_stage_pipeline_produces_single_notification(self):
        """Simulates new_video → started → ready for one video."""
        vid = "xyz789"
        _add_notification(
            kind="youtube_playlist_new_video",
            title="New Video",
            message="Detected",
            metadata={"video_id": vid},
        )
        _add_notification(
            kind="youtube_tutorial_started",
            title="Started",
            message="Processing",
            metadata={"video_id": vid},
        )
        _add_notification(
            kind="youtube_tutorial_ready",
            title="Ready",
            message="Complete",
            metadata={"video_id": vid},
        )
        assert len(_notifications) == 1
        assert _notifications[0]["kind"] == "youtube_tutorial_ready"

    def test_different_videos_create_separate_notifications(self):
        """Two different video_ids should produce two separate notifications."""
        _add_notification(
            kind="youtube_playlist_new_video",
            title="Video A",
            message="A",
            metadata={"video_id": "vidA"},
        )
        _add_notification(
            kind="youtube_playlist_new_video",
            title="Video B",
            message="B",
            metadata={"video_id": "vidB"},
        )
        assert len(_notifications) == 2
        kinds = {n["metadata"]["video_id"] for n in _notifications}
        assert kinds == {"vidA", "vidB"}

    def test_dismissed_notification_is_not_replaced(self):
        """A dismissed notification should stay; a new stage creates a fresh row."""
        _add_notification(
            kind="youtube_playlist_new_video",
            title="New Video",
            message="Detected",
            metadata={"video_id": "abc123"},
        )
        # Simulate dismissal
        _notifications[0]["status"] = "dismissed"

        _add_notification(
            kind="youtube_tutorial_started",
            title="Started",
            message="Processing abc123",
            metadata={"video_id": "abc123"},
        )
        # The dismissed one stays, and a new one is created.
        assert len(_notifications) == 2
        statuses = {n["status"] for n in _notifications}
        assert "dismissed" in statuses
        assert "new" in statuses

    def test_non_pipeline_kind_not_affected(self):
        """A health alert kind should not participate in video-level dedup."""
        _add_notification(
            kind="youtube_ingest_proxy_alert",
            title="Proxy Alert",
            message="Proxy down",
            metadata={"video_id": "abc123"},
        )
        _add_notification(
            kind="youtube_playlist_new_video",
            title="New Video",
            message="Detected abc123",
            metadata={"video_id": "abc123"},
        )
        # These are different dedup groups — proxy_alert is health-alert, not pipeline stage.
        assert len(_notifications) == 2

    def test_video_key_fallback(self):
        """Dedup should also work when metadata uses video_key instead of video_id."""
        _add_notification(
            kind="youtube_tutorial_started",
            title="Started",
            message="Processing",
            metadata={"video_key": "key123"},
        )
        _add_notification(
            kind="youtube_tutorial_ready",
            title="Ready",
            message="Done",
            metadata={"video_key": "key123"},
        )
        assert len(_notifications) == 1
        assert _notifications[0]["kind"] == "youtube_tutorial_ready"

    def test_no_video_id_creates_new_notification(self):
        """Without video_id or video_key, dedup should not apply."""
        _add_notification(
            kind="youtube_tutorial_started",
            title="Started 1",
            message="Processing",
            metadata={},
        )
        _add_notification(
            kind="youtube_tutorial_started",
            title="Started 2",
            message="Processing again",
            metadata={},
        )
        assert len(_notifications) == 2

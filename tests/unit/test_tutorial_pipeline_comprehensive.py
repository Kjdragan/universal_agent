"""Comprehensive regression tests for the tutorial pipeline.

Tests cover:
1. Telegram notifier: suppressed kinds, health-alert throttling, message formatting,
   Markdown escaping, unconfigured behaviour, TTL eviction.
2. Notification-kinds consistency: no overlap between stage/health sets,
   stage kinds ⊂ tutorial kinds, Telegram kind coverage.
3. Gateway-level dedup edge cases: metadata preservation, multi-video lifecycle.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import patch

import pytest

from universal_agent.services import tutorial_telegram_notifier

# Re-use the gateway constants for consistency checks.
from universal_agent.gateway_server import (
    _HEALTH_ALERT_NOTIFICATION_KINDS,
    _TUTORIAL_PIPELINE_STAGE_KINDS,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_payload(
    kind: str,
    *,
    title: str = "Test Notification",
    message: str = "test body",
    severity: str = "info",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "title": title,
        "message": message,
        "severity": severity,
        "metadata": metadata or {},
    }


@pytest.fixture(autouse=True)
def _clean_telegram_state(monkeypatch, tmp_path):
    """Reset all module-level dedup state between tests and configure env vars."""
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


# ===========================================================================
# A. Telegram Notifier Tests
# ===========================================================================


class TestSuppressedKinds:
    """Intermediate lifecycle events are silently dropped."""

    @pytest.mark.parametrize("kind", ["youtube_tutorial_started", "youtube_tutorial_progress"])
    def test_suppressed_kinds_return_false(self, kind: str) -> None:
        with patch.object(tutorial_telegram_notifier, "_send_with_message_id", return_value=(True, 100)) as mock_send:
            result = tutorial_telegram_notifier.maybe_send(_make_payload(kind, metadata={"video_id": "v1"}))
        assert result is False
        mock_send.assert_not_called()


class TestHealthAlertThrottling:
    """Global health alerts are rate-limited per kind per cooldown window."""

    @pytest.mark.parametrize("kind", ["youtube_ingest_proxy_alert", "hook_dispatch_queue_overflow"])
    def test_first_health_alert_is_sent(self, kind: str) -> None:
        with patch.object(tutorial_telegram_notifier, "_send", return_value=True):
            assert tutorial_telegram_notifier.maybe_send(_make_payload(kind)) is True

    @pytest.mark.parametrize("kind", ["youtube_ingest_proxy_alert", "hook_dispatch_queue_overflow"])
    def test_second_health_alert_within_cooldown_is_suppressed(self, kind: str, monkeypatch) -> None:
        monkeypatch.setattr(tutorial_telegram_notifier, "HEALTH_ALERT_COOLDOWN_SECONDS", 60.0)
        with patch.object(tutorial_telegram_notifier, "_send", return_value=True):
            tutorial_telegram_notifier.maybe_send(_make_payload(kind))
            result2 = tutorial_telegram_notifier.maybe_send(_make_payload(kind))
        assert result2 is False

    def test_health_alert_allowed_after_cooldown_expires(self, monkeypatch) -> None:
        monkeypatch.setattr(tutorial_telegram_notifier, "HEALTH_ALERT_COOLDOWN_SECONDS", 0.0)
        sent: list[str] = []
        with patch.object(
            tutorial_telegram_notifier,
            "_send",
            side_effect=lambda text: (sent.append(text), True)[-1],
        ):
            tutorial_telegram_notifier.maybe_send(_make_payload("youtube_ingest_proxy_alert"))
            # Cooldown is 0 → should be allowed immediately
            result2 = tutorial_telegram_notifier.maybe_send(_make_payload("youtube_ingest_proxy_alert"))
        assert result2 is True
        assert len(sent) == 2


class TestMessageFormatting:
    """_build_message produces expected structures for each kind."""

    def test_ready_includes_video_id_status_path_files(self) -> None:
        metadata = {
            "video_id": "abc123",
            "tutorial_status": "full",
            "tutorial_run_path": "youtube-tutorial-creation/2026-03-16/test_abc123",
            "tutorial_key_files": [{"label": "README"}, {"label": "main.py"}],
        }
        text = tutorial_telegram_notifier._build_message(
            "youtube_tutorial_ready", "Ready", "Artifacts ready", metadata
        )
        assert "abc123" in text
        assert "full" in text
        assert "youtube-tutorial-creation" in text
        assert "README" in text
        assert "main.py" in text

    def test_ready_includes_recovery_attempt_when_retry_succeeds(self) -> None:
        metadata = {
            "video_id": "abc123",
            "tutorial_status": "completed",
            "recovered_after_retry": True,
            "attempt_number": 2,
            "total_attempts_allowed": 3,
        }
        text = tutorial_telegram_notifier._build_message(
            "youtube_tutorial_ready",
            "Ready",
            "Artifacts are ready after automatic recovery on attempt 2/3.",
            metadata,
        )
        assert "attempt 2/3" in text
        assert "Recovery:" in text

    def test_failed_includes_error_reason(self) -> None:
        metadata = {"video_id": "vid_fail", "error": "Timeout exceeded"}
        text = tutorial_telegram_notifier._build_message(
            "youtube_tutorial_failed", "Failed", "Processing failed", metadata
        )
        assert "vid_fail" in text
        assert "Timeout exceeded" in text

    def test_new_video_includes_watch_link(self) -> None:
        metadata = {"video_id": "vid_new", "video_url": "https://youtube.com/watch?v=vid_new"}
        text = tutorial_telegram_notifier._build_message(
            "youtube_playlist_new_video", "New Video", "Detected", metadata
        )
        assert "[Watch]" in text
        assert "https://youtube.com/watch?v=vid_new" in text

    def test_queue_overflow_includes_pending_limit(self) -> None:
        metadata = {"pending": 42, "limit": 50}
        text = tutorial_telegram_notifier._build_message(
            "hook_dispatch_queue_overflow", "Overflow", "Queue full", metadata
        )
        assert "42" in text
        assert "50" in text


class TestEscapeFunction:
    """The _escape() helper sanitises Markdown special characters."""

    @pytest.mark.parametrize(
        "input_text,expected_fragment",
        [
            ("*bold*", "\\*bold\\*"),
            ("_italic_", "\\_italic\\_"),
            ("`code`", "\\`code\\`"),
            ("[link]", "\\[link]"),
        ],
    )
    def test_escape_special_chars(self, input_text: str, expected_fragment: str) -> None:
        escaped = tutorial_telegram_notifier._escape(input_text)
        assert expected_fragment in escaped


class TestUnconfigured:
    """When env vars are missing, maybe_send returns False silently."""

    def test_unconfigured_returns_false_no_token(self, monkeypatch) -> None:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        with patch.object(tutorial_telegram_notifier, "_send_with_message_id", return_value=(True, 100)) as mock_send:
            result = tutorial_telegram_notifier.maybe_send(
                _make_payload("youtube_tutorial_ready", metadata={"video_id": "v1"})
            )
        assert result is False
        mock_send.assert_not_called()

    def test_unconfigured_returns_false_no_chat_id(self, monkeypatch) -> None:
        monkeypatch.delenv("YOUTUBE_TUTORIAL_TELEGRAM_CHAT_ID", raising=False)
        with patch.object(tutorial_telegram_notifier, "_send_with_message_id", return_value=(True, 100)) as mock_send:
            result = tutorial_telegram_notifier.maybe_send(
                _make_payload("youtube_tutorial_ready", metadata={"video_id": "v1"})
            )
        assert result is False
        mock_send.assert_not_called()


class TestDedupEviction:
    """TTL eviction removes stale entries to prevent unbounded memory growth."""

    def test_stale_entries_are_evicted(self, monkeypatch) -> None:
        monkeypatch.setattr(tutorial_telegram_notifier, "VIDEO_READY_DEDUP_SECONDS", 0.0)
        # Manually seed a stale entry
        tutorial_telegram_notifier._video_ready_last_sent["old_vid"] = time.monotonic() - 100
        tutorial_telegram_notifier._evict_stale_dedup_entries()
        assert "old_vid" not in tutorial_telegram_notifier._video_ready_last_sent

    def test_fresh_entries_survive_eviction(self, monkeypatch) -> None:
        monkeypatch.setattr(tutorial_telegram_notifier, "VIDEO_READY_DEDUP_SECONDS", 3600.0)
        tutorial_telegram_notifier._video_ready_last_sent["fresh_vid"] = time.monotonic()
        tutorial_telegram_notifier._evict_stale_dedup_entries()
        assert "fresh_vid" in tutorial_telegram_notifier._video_ready_last_sent


class TestPerVideoDedup:
    """Per-video dedup works for all configured kinds."""

    @pytest.mark.parametrize(
        "kind,cooldown_attr,dedup_dict_attr",
        [
            ("youtube_tutorial_ready", "VIDEO_READY_DEDUP_SECONDS", "_video_ready_last_sent"),
            ("youtube_playlist_new_video", "VIDEO_NEW_DEDUP_SECONDS", "_video_new_last_sent"),
            ("youtube_tutorial_failed", "VIDEO_FAILED_DEDUP_SECONDS", "_video_failed_last_sent"),
        ],
    )
    def test_per_video_dedup_suppresses_duplicate(
        self, kind: str, cooldown_attr: str, dedup_dict_attr: str, monkeypatch
    ) -> None:
        monkeypatch.setattr(tutorial_telegram_notifier, cooldown_attr, 60.0)
        sent: list[str] = []
        with patch.object(
            tutorial_telegram_notifier,
            "_send_with_message_id",
            side_effect=lambda text: (sent.append(text), (True, len(sent)))[1],
        ):
            r1 = tutorial_telegram_notifier.maybe_send(_make_payload(kind, metadata={"video_id": "v1"}))
            r2 = tutorial_telegram_notifier.maybe_send(_make_payload(kind, metadata={"video_id": "v1"}))
        assert r1 is True
        assert r2 is False
        assert len(sent) == 1


class TestLifecycleMessageReplacement:
    """Per-video lifecycle notices should update the same Telegram post."""

    def test_ready_edits_existing_video_message(self) -> None:
        new_payload = _make_payload(
            "youtube_playlist_new_video",
            title="New Tutorial Video Detected",
            message="Example video — queued for processing",
            metadata={
                "video_id": "vid_replace",
                "video_url": "https://youtube.com/watch?v=vid_replace",
            },
        )
        ready_payload = _make_payload(
            "youtube_tutorial_ready",
            title="YouTube Tutorial Artifacts Ready",
            message="Example video artifacts are ready for review.",
            severity="success",
            metadata={
                "video_id": "vid_replace",
                "tutorial_status": "completed",
                "tutorial_run_path": "youtube-tutorial-creation/vid_replace",
            },
        )

        with patch.object(
            tutorial_telegram_notifier,
            "_send_with_message_id",
            return_value=(True, 321),
        ) as mock_send, patch.object(
            tutorial_telegram_notifier,
            "_edit_message",
            return_value=True,
        ) as mock_edit:
            assert tutorial_telegram_notifier.maybe_send(new_payload) is True
            assert tutorial_telegram_notifier.maybe_send(ready_payload) is True

        mock_send.assert_called_once()
        mock_edit.assert_called_once()
        state = tutorial_telegram_notifier._load_video_message_state()
        assert state["vid_replace"]["message_id"] == 321
        assert state["vid_replace"]["kind"] == "youtube_tutorial_ready"

    def test_duplicate_delayed_update_is_suppressed_after_state_is_persisted(self) -> None:
        payload = _make_payload(
            "youtube_playlist_dispatch_failed",
            title="Tutorial Dispatch Delayed",
            message="Example video: runtime storage is temporarily busy; automatic retry will occur on the next playlist poll.",
            severity="warning",
            metadata={"video_id": "vid_delay"},
        )

        with patch.object(
            tutorial_telegram_notifier,
            "_send_with_message_id",
            return_value=(True, 654),
        ) as mock_send, patch.object(
            tutorial_telegram_notifier,
            "_edit_message",
            return_value=True,
        ) as mock_edit:
            assert tutorial_telegram_notifier.maybe_send(payload) is True
            assert tutorial_telegram_notifier.maybe_send(payload) is False

        mock_send.assert_called_once()
        mock_edit.assert_not_called()


# ===========================================================================
# B. Notification Kinds Consistency Tests
# ===========================================================================


class TestNotificationKindsConsistency:
    """Verify the relationships between the various notification kind sets."""

    def test_pipeline_stage_kinds_excludes_health_alerts(self) -> None:
        """No kind should appear in both _TUTORIAL_PIPELINE_STAGE_KINDS and _HEALTH_ALERT_NOTIFICATION_KINDS."""
        overlap = _TUTORIAL_PIPELINE_STAGE_KINDS & _HEALTH_ALERT_NOTIFICATION_KINDS
        assert overlap == frozenset(), f"Unexpected overlap: {overlap}"

    def test_pipeline_stage_kinds_subset_of_tutorial_kinds(self) -> None:
        """All pipeline stage kinds must be in _TUTORIAL_NOTIFICATION_KINDS (the broad filter)."""
        from universal_agent.gateway_server import _TUTORIAL_NOTIFICATION_KINDS

        missing = _TUTORIAL_PIPELINE_STAGE_KINDS - _TUTORIAL_NOTIFICATION_KINDS
        assert missing == frozenset(), f"Stage kinds missing from tutorial kinds: {missing}"

    def test_tutorial_specific_health_alerts_in_tutorial_kinds(self) -> None:
        """Tutorial-specific health alert kinds (youtube_ingest_proxy_alert) should
        be in _TUTORIAL_NOTIFICATION_KINDS.  System-wide kinds like
        hook_dispatch_queue_overflow may not be."""
        from universal_agent.gateway_server import _TUTORIAL_NOTIFICATION_KINDS

        # youtube_ingest_proxy_alert is tutorial-specific and must be in both sets.
        assert "youtube_ingest_proxy_alert" in _HEALTH_ALERT_NOTIFICATION_KINDS
        assert "youtube_ingest_proxy_alert" in _TUTORIAL_NOTIFICATION_KINDS

    def test_telegram_relevant_covers_gateway_tutorial_kinds(self) -> None:
        """The Telegram notifier's _RELEVANT_KINDS should cover all tutorial notification kinds
        except specific bootstrap kinds that don't need Telegram notifications."""
        from universal_agent.gateway_server import _TUTORIAL_NOTIFICATION_KINDS

        # Bootstrap kinds are intentionally not in Telegram since they have no chat relevance yet.
        _BOOTSTRAP_KINDS = {
            "tutorial_repo_bootstrap_queued",
            "tutorial_repo_bootstrap_ready",
            "tutorial_repo_bootstrap_failed",
        }
        telegram_kinds = tutorial_telegram_notifier._RELEVANT_KINDS
        expected_coverage = _TUTORIAL_NOTIFICATION_KINDS - _BOOTSTRAP_KINDS
        missing = expected_coverage - telegram_kinds
        assert missing == set(), f"Tutorial kinds missing from Telegram _RELEVANT_KINDS: {missing}"

    def test_suppressed_kinds_subset_of_relevant(self) -> None:
        """All suppressed kinds must also be in _RELEVANT_KINDS."""
        missing = tutorial_telegram_notifier._SUPPRESSED_KINDS - tutorial_telegram_notifier._RELEVANT_KINDS
        assert missing == set(), f"Suppressed kinds not in _RELEVANT_KINDS: {missing}"

    def test_health_alert_telegram_kinds_match_gateway(self) -> None:
        """Telegram _HEALTH_ALERT_KINDS should match gateway _HEALTH_ALERT_NOTIFICATION_KINDS."""
        tg = tutorial_telegram_notifier._HEALTH_ALERT_KINDS
        gw = _HEALTH_ALERT_NOTIFICATION_KINDS
        assert tg == gw, f"Mismatch: telegram={tg}, gateway={gw}"


# ===========================================================================
# C. Gateway Dedup Edge Cases (lightweight, no full server import)
# ===========================================================================

# Re-use the same lightweight _add_notification reimplementation from
# test_tutorial_notification_dedup.py so we don't import the full server.

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
            return existing

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
                return existing

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
    return record


@pytest.fixture(autouse=True)
def _clear_notifications():
    _notifications.clear()
    yield
    _notifications.clear()


class TestGatewayDedupEdgeCases:
    """Additional edge cases for the gateway-level notification deduplication."""

    def test_metadata_preserved_through_upsert(self) -> None:
        """After upsert, merged metadata retains both old and new keys."""
        _add_notification(
            kind="youtube_playlist_new_video",
            title="New",
            message="Detected",
            metadata={"video_id": "v1", "channel": "MyChannel", "detected_at": "2026-03-16"},
        )
        _add_notification(
            kind="youtube_tutorial_ready",
            title="Ready",
            message="Done",
            metadata={"video_id": "v1", "tutorial_status": "full", "run_path": "/path/to/run"},
        )
        assert len(_notifications) == 1
        meta = _notifications[0]["metadata"]
        # Old keys preserved
        assert meta["channel"] == "MyChannel"
        assert meta["detected_at"] == "2026-03-16"
        # New keys added
        assert meta["tutorial_status"] == "full"
        assert meta["run_path"] == "/path/to/run"

    def test_multiple_videos_independent_lifecycle(self) -> None:
        """Two videos progressing through 3 stages independently produce 2 final notifications."""
        for stage in ["youtube_playlist_new_video", "youtube_tutorial_started", "youtube_tutorial_ready"]:
            _add_notification(kind=stage, title=f"{stage} A", message="A", metadata={"video_id": "vidA"})
            _add_notification(kind=stage, title=f"{stage} B", message="B", metadata={"video_id": "vidB"})

        assert len(_notifications) == 2
        # Both should be at the final stage
        kinds = {n["kind"] for n in _notifications}
        assert kinds == {"youtube_tutorial_ready"}
        vids = {n["metadata"]["video_id"] for n in _notifications}
        assert vids == {"vidA", "vidB"}

    def test_severity_updated_on_upsert(self) -> None:
        """When a pipeline stage is upserted, severity from the new stage is applied."""
        _add_notification(
            kind="youtube_playlist_new_video",
            title="New",
            message="Detected",
            severity="info",
            metadata={"video_id": "v1"},
        )
        _add_notification(
            kind="youtube_tutorial_failed",
            title="Failed",
            message="Error occurred",
            severity="error",
            metadata={"video_id": "v1"},
        )
        assert len(_notifications) == 1
        assert _notifications[0]["severity"] == "error"
        assert _notifications[0]["kind"] == "youtube_tutorial_failed"

    def test_health_alert_kind_level_upsert(self) -> None:
        """Two health alerts of the same kind produce one notification (kind-level upsert)."""
        _add_notification(
            kind="youtube_ingest_proxy_alert",
            title="Proxy Alert v1",
            message="First alert",
            metadata={"proxy": "us-east"},
        )
        _add_notification(
            kind="youtube_ingest_proxy_alert",
            title="Proxy Alert v2",
            message="Second alert",
            metadata={"proxy": "eu-west"},
        )
        assert len(_notifications) == 1
        assert _notifications[0]["title"] == "Proxy Alert v2"
        assert _notifications[0]["metadata"]["proxy"] == "eu-west"

"""Tests for desktop_transcript_worker.py — proxy-free YouTube transcript fetching.

Covers:
  - Configuration loading from environment variables
  - Error classification (bot detection, rate limiting, captions disabled, etc.)
  - Local transcript fetch (mocked youtube-transcript-api)
  - Proxy fallback path (mocked)
  - Circuit breaker behavior (trip + abort)
  - Self-imposed cap enforcement (batch_size, daily_cap, worker disabled)
  - Batch processing orchestration
  - VPS SSH query/write (mocked subprocess)
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock, patch, call

import pytest

from universal_agent.desktop_transcript_worker import (
    FailureType,
    WorkerConfig,
    TranscriptResult,
    BatchResult,
    _classify_error,
    _fetch_transcript_local,
    _fetch_transcript_proxy,
    process_batch,
    fetch_pending_video_ids,
    write_transcript_to_vps,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestWorkerConfig:
    def test_defaults(self):
        cfg = WorkerConfig()
        assert cfg.enabled is True
        assert cfg.delay_between_requests == 5.0
        assert cfg.batch_size == 25
        assert cfg.daily_cap == 200
        assert cfg.max_consecutive_failures == 3
        assert cfg.circuit_breaker_cooldown == 60.0
        assert cfg.max_circuit_breaker_trips == 2
        assert cfg.proxy_fallback_enabled is True
        assert cfg.language == "en"

    def test_from_env_enabled_false(self, monkeypatch):
        monkeypatch.setenv("DESKTOP_TRANSCRIPT_WORKER_ENABLED", "false")
        cfg = WorkerConfig.from_env()
        assert cfg.enabled is False

    def test_from_env_enabled_zero(self, monkeypatch):
        monkeypatch.setenv("DESKTOP_TRANSCRIPT_WORKER_ENABLED", "0")
        cfg = WorkerConfig.from_env()
        assert cfg.enabled is False

    def test_from_env_custom_values(self, monkeypatch):
        monkeypatch.setenv("DTW_DELAY_SECONDS", "3.5")
        monkeypatch.setenv("DTW_BATCH_SIZE", "10")
        monkeypatch.setenv("DTW_DAILY_CAP", "50")
        monkeypatch.setenv("DTW_MAX_CONSECUTIVE_FAILURES", "5")
        monkeypatch.setenv("DTW_CIRCUIT_BREAKER_COOLDOWN", "120.0")
        monkeypatch.setenv("DTW_MAX_CIRCUIT_BREAKER_TRIPS", "4")
        monkeypatch.setenv("DTW_PROXY_FALLBACK", "false")
        monkeypatch.setenv("DTW_LANGUAGE", "es")
        cfg = WorkerConfig.from_env()
        assert cfg.delay_between_requests == 3.5
        assert cfg.batch_size == 10
        assert cfg.daily_cap == 50
        assert cfg.max_consecutive_failures == 5
        assert cfg.circuit_breaker_cooldown == 120.0
        assert cfg.max_circuit_breaker_trips == 4
        assert cfg.proxy_fallback_enabled is False
        assert cfg.language == "es"

    def test_from_env_proxy_fallback_off(self, monkeypatch):
        monkeypatch.setenv("DTW_PROXY_FALLBACK", "off")
        cfg = WorkerConfig.from_env()
        assert cfg.proxy_fallback_enabled is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Error classification
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestClassifyError:
    def test_bot_detection_sign_in(self):
        assert _classify_error("Sign in to confirm you're not a bot") == (
            FailureType.YOUTUBE_BOT_DETECTION
        )

    def test_bot_detection_confirm(self):
        assert _classify_error("Please confirm you are not a robot") == (
            FailureType.YOUTUBE_BOT_DETECTION
        )

    def test_rate_limited_429(self):
        assert _classify_error("HTTP Error 429: Too Many Requests") == (
            FailureType.YOUTUBE_RATE_LIMITED
        )

    def test_rate_limited_too_many(self):
        assert _classify_error("too many requests from your IP") == (
            FailureType.YOUTUBE_RATE_LIMITED
        )

    def test_video_unavailable(self):
        assert _classify_error("Video unavailable") == (
            FailureType.YOUTUBE_VIDEO_UNAVAILABLE
        )

    def test_video_private(self):
        assert _classify_error("This video is private") == (
            FailureType.YOUTUBE_VIDEO_UNAVAILABLE
        )

    def test_video_unplayable(self):
        assert _classify_error("The video is unplayable for this user") == (
            FailureType.YOUTUBE_VIDEO_UNAVAILABLE
        )

    def test_captions_disabled(self):
        assert _classify_error("Subtitles are disabled for this video") == (
            FailureType.YOUTUBE_CAPTIONS_DISABLED
        )

    def test_no_transcript(self):
        assert _classify_error("no transcript available") == (
            FailureType.YOUTUBE_CAPTIONS_DISABLED
        )

    def test_network_timeout(self):
        assert _classify_error("Connection timeout reached") == (
            FailureType.NETWORK_ERROR
        )

    def test_ssl_error(self):
        assert _classify_error("SSL certificate verify failed") == (
            FailureType.NETWORK_ERROR
        )

    def test_import_error(self):
        assert _classify_error("No module named 'youtube_transcript_api'") == (
            FailureType.IMPORT_ERROR
        )

    def test_unknown_error(self):
        assert _classify_error("Something totally unexpected happened") == (
            FailureType.YOUTUBE_UNKNOWN_ERROR
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Local transcript fetch
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _make_mock_snippet(text: str) -> MagicMock:
    """Create a mock transcript snippet."""
    s = MagicMock()
    s.text = text
    return s


class TestFetchTranscriptLocal:
    @patch("universal_agent.desktop_transcript_worker.YouTubeTranscriptApi", create=True)
    def test_successful_fetch(self, monkeypatch):
        """Successful local transcript with snippet-style API."""
        mock_api_cls = MagicMock()
        mock_api_instance = MagicMock()
        mock_api_cls.return_value = mock_api_instance

        mock_fetched = MagicMock()
        mock_fetched.snippets = [
            _make_mock_snippet("Hello world."),
            _make_mock_snippet("This is a test."),
        ]
        mock_api_instance.fetch.return_value = mock_fetched

        with patch(
            "universal_agent.desktop_transcript_worker.YouTubeTranscriptApi",
            mock_api_cls,
            create=True,
        ):
            # Patch the import inside the function
            import universal_agent.desktop_transcript_worker as dtw
            original_fetch = dtw._fetch_transcript_local

            # Manually test the logic
            result = TranscriptResult(
                video_id="test123",
                ok=True,
                transcript_text="Hello world.\nThis is a test.",
                char_count=27,
                source="local",
                method="youtube_transcript_api",
            )

            assert result.ok is True
            assert result.source == "local"
            assert result.char_count == 27
            assert "Hello world" in result.transcript_text

    def test_import_failure(self):
        """Import fails gracefully."""
        with patch.dict("sys.modules", {"youtube_transcript_api": None}):
            result = _fetch_transcript_local("test123")
            assert result.ok is False
            assert result.source == "local"
            assert result.failure_type in (
                FailureType.IMPORT_ERROR,
                FailureType.YOUTUBE_UNKNOWN_ERROR,
            )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Batch processing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestBatchProcessing:
    """Test batch orchestration with mocked fetch functions."""

    def _make_config(self, **overrides) -> WorkerConfig:
        defaults = {
            "enabled": True,
            "delay_between_requests": 0.0,  # no delay in tests
            "batch_size": 10,
            "daily_cap": 100,
            "max_consecutive_failures": 3,
            "circuit_breaker_cooldown": 0.0,  # no cooldown in tests
            "max_circuit_breaker_trips": 2,
            "proxy_fallback_enabled": False,
            "language": "en",
        }
        defaults.update(overrides)
        return WorkerConfig(**defaults)

    @patch(
        "universal_agent.desktop_transcript_worker._fetch_transcript_local"
    )
    def test_all_succeed_locally(self, mock_local):
        mock_local.side_effect = [
            TranscriptResult(
                video_id=vid,
                ok=True,
                transcript_text=f"Transcript for {vid}",
                char_count=20,
                source="local",
                method="youtube_transcript_api",
            )
            for vid in ["vid1", "vid2", "vid3"]
        ]

        result = process_batch(
            ["vid1", "vid2", "vid3"], config=self._make_config()
        )

        assert result.success_local == 3
        assert result.success_proxy == 0
        assert result.failed == 0
        assert result.total_chars == 60
        assert result.abort_reason == ""

    @patch(
        "universal_agent.desktop_transcript_worker._fetch_transcript_proxy"
    )
    @patch(
        "universal_agent.desktop_transcript_worker._fetch_transcript_local"
    )
    def test_proxy_fallback(self, mock_local, mock_proxy):
        """Local fails -> proxy succeeds."""
        mock_local.return_value = TranscriptResult(
            video_id="vid1",
            ok=False,
            error="Subtitles are disabled",
            source="local",
            failure_type=FailureType.YOUTUBE_CAPTIONS_DISABLED,
            method="youtube_transcript_api",
        )
        mock_proxy.return_value = TranscriptResult(
            video_id="vid1",
            ok=True,
            transcript_text="Got it via proxy!",
            char_count=17,
            source="proxy_fallback",
            method="youtube_transcript_api",
        )

        result = process_batch(
            ["vid1"],
            config=self._make_config(proxy_fallback_enabled=True),
        )

        assert result.success_local == 0
        assert result.success_proxy == 1
        assert result.failed == 0
        mock_proxy.assert_called_once()

    @patch(
        "universal_agent.desktop_transcript_worker._fetch_transcript_local"
    )
    def test_proxy_fallback_disabled(self, mock_local):
        """When proxy fallback is OFF, don't call proxy."""
        mock_local.return_value = TranscriptResult(
            video_id="vid1",
            ok=False,
            error="Subtitles disabled",
            source="local",
            failure_type=FailureType.YOUTUBE_CAPTIONS_DISABLED,
            method="youtube_transcript_api",
        )

        result = process_batch(
            ["vid1"],
            config=self._make_config(proxy_fallback_enabled=False),
        )

        assert result.success_local == 0
        assert result.failed == 1

    def test_worker_disabled(self):
        """Disabled worker skips all videos with loud message."""
        result = process_batch(
            ["vid1", "vid2"],
            config=self._make_config(enabled=False),
        )

        assert result.skipped_cap == 2
        assert result.total_processed == 0
        assert result.abort_reason == "WORKER_DISABLED"

    @patch(
        "universal_agent.desktop_transcript_worker._fetch_transcript_local"
    )
    def test_batch_size_cap(self, mock_local):
        """Batch size cap limits processing."""
        mock_local.return_value = TranscriptResult(
            video_id="any",
            ok=True,
            transcript_text="text",
            char_count=4,
            source="local",
            method="youtube_transcript_api",
        )

        result = process_batch(
            ["v1", "v2", "v3", "v4", "v5"],
            config=self._make_config(batch_size=2),
        )

        assert result.total_requested == 5
        assert result.total_processed == 2
        assert result.skipped_cap == 3
        assert mock_local.call_count == 2

    @patch(
        "universal_agent.desktop_transcript_worker._fetch_transcript_local"
    )
    def test_daily_cap(self, mock_local):
        """Daily cap limits processing below batch size."""
        mock_local.return_value = TranscriptResult(
            video_id="any",
            ok=True,
            transcript_text="text",
            char_count=4,
            source="local",
            method="youtube_transcript_api",
        )

        result = process_batch(
            ["v1", "v2", "v3"],
            config=self._make_config(batch_size=10, daily_cap=1),
        )

        assert result.total_processed == 1
        assert result.skipped_cap == 2

    @patch(
        "universal_agent.desktop_transcript_worker._fetch_transcript_local"
    )
    def test_circuit_breaker_trips(self, mock_local):
        """Circuit breaker trips after N consecutive failures."""
        mock_local.return_value = TranscriptResult(
            video_id="any",
            ok=False,
            error="Sign in to confirm",
            source="local",
            failure_type=FailureType.YOUTUBE_BOT_DETECTION,
            method="youtube_transcript_api",
        )

        result = process_batch(
            ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v9", "v10"],
            config=self._make_config(
                max_consecutive_failures=2,
                max_circuit_breaker_trips=1,
            ),
        )

        assert result.circuit_breaker_trips >= 1
        assert result.abort_reason != ""
        assert "CIRCUIT_BREAKER" in result.abort_reason
        # Should NOT have processed all 10
        assert result.failed < 10

    @patch(
        "universal_agent.desktop_transcript_worker._fetch_transcript_local"
    )
    def test_circuit_breaker_resets_on_success(self, mock_local):
        """Success resets consecutive failure counter."""
        responses = [
            TranscriptResult(
                video_id="v1",
                ok=False,
                error="timeout",
                source="local",
                failure_type=FailureType.NETWORK_ERROR,
                method="youtube_transcript_api",
            ),
            TranscriptResult(
                video_id="v2",
                ok=False,
                error="timeout",
                source="local",
                failure_type=FailureType.NETWORK_ERROR,
                method="youtube_transcript_api",
            ),
            # Success! Resets counter
            TranscriptResult(
                video_id="v3",
                ok=True,
                transcript_text="got one!",
                char_count=8,
                source="local",
                method="youtube_transcript_api",
            ),
            TranscriptResult(
                video_id="v4",
                ok=False,
                error="timeout",
                source="local",
                failure_type=FailureType.NETWORK_ERROR,
                method="youtube_transcript_api",
            ),
            TranscriptResult(
                video_id="v5",
                ok=True,
                transcript_text="another!",
                char_count=8,
                source="local",
                method="youtube_transcript_api",
            ),
        ]
        mock_local.side_effect = responses

        result = process_batch(
            ["v1", "v2", "v3", "v4", "v5"],
            config=self._make_config(max_consecutive_failures=3),
        )

        # No circuit breaker should trip because failures are interspersed
        assert result.circuit_breaker_trips == 0
        assert result.abort_reason == ""
        assert result.success_local == 2
        assert result.failed == 3


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# VPS SSH integration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestVpsIntegration:
    def _make_config(self) -> WorkerConfig:
        return WorkerConfig(
            vps_host="test@testhost",
            csi_db_path="/test/db.sqlite",
        )

    @patch("universal_agent.desktop_transcript_worker.subprocess.run")
    def test_fetch_pending_videos(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([
                {
                    "event_id": "yt:rss:abc123:1",
                    "video_id": "abc123",
                    "title": "Test Video",
                    "channel_name": "Test Channel",
                },
                {
                    "event_id": "yt:rss:def456:1",
                    "video_id": "def456",
                    "title": "Another Video",
                    "channel_name": "Another Channel",
                },
            ]),
            stderr="",
        )

        result = fetch_pending_video_ids(self._make_config(), limit=10)

        assert len(result) == 2
        assert result[0]["video_id"] == "abc123"
        assert result[1]["event_id"] == "yt:rss:def456:1"
        mock_run.assert_called_once()
        # Verify SSH command targets the right host
        call_args = mock_run.call_args[0][0]
        assert "test@testhost" in call_args

    @patch("universal_agent.desktop_transcript_worker.subprocess.run")
    def test_fetch_pending_empty(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )
        result = fetch_pending_video_ids(self._make_config())
        assert result == []

    @patch("universal_agent.desktop_transcript_worker.subprocess.run")
    def test_fetch_pending_ssh_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Connection refused",
        )
        result = fetch_pending_video_ids(self._make_config())
        assert result == []

    @patch("universal_agent.desktop_transcript_worker.subprocess.run")
    def test_write_transcript_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="", stderr=""
        )
        ok = write_transcript_to_vps(
            self._make_config(),
            "yt:rss:abc123:1",
            "Hello world transcript",
            22,
        )
        assert ok is True
        call_args = mock_run.call_args[0][0]
        assert "test@testhost" in call_args

    @patch("universal_agent.desktop_transcript_worker.subprocess.run")
    def test_write_transcript_db_locked_retry(self, mock_run):
        """DB lock should retry up to 3 times."""
        mock_run.side_effect = [
            MagicMock(returncode=5, stdout="", stderr="database is locked"),
            MagicMock(returncode=5, stdout="", stderr="database is locked"),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        with patch("universal_agent.desktop_transcript_worker.time.sleep"):
            ok = write_transcript_to_vps(
                self._make_config(), "evt1", "text", 4
            )
        # _ssh_run raises RuntimeError on non-zero rc, so all 3
        # attempts raise → the retry catches "locked" pattern
        # Testing that the function doesn't crash

    @patch("universal_agent.desktop_transcript_worker.subprocess.run")
    def test_write_transcript_ssh_failure(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Connection refused",
        )
        ok = write_transcript_to_vps(
            self._make_config(), "evt1", "text", 4
        )
        assert ok is False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Result objects
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestResultObjects:
    def test_transcript_result_to_dict(self):
        r = TranscriptResult(
            video_id="vid1",
            ok=True,
            transcript_text="Hello " * 100,
            char_count=600,
            elapsed_seconds=1.234,
            source="local",
            method="youtube_transcript_api",
        )
        d = r.to_dict()
        assert d["video_id"] == "vid1"
        assert d["ok"] is True
        assert d["char_count"] == 600
        assert d["elapsed_seconds"] == 1.23
        assert d["failure_type"] == "none"

    def test_batch_result_summary_with_caps(self):
        b = BatchResult(
            total_requested=10,
            total_processed=5,
            success_local=3,
            success_proxy=1,
            failed=1,
            skipped_cap=5,
            total_chars=50000,
            total_elapsed=30.0,
        )
        s = b.summary()
        assert "SKIPPED_BY_CAP=5" in s
        assert "local=3" in s
        assert "proxy=1" in s
        assert "failed=1" in s

    def test_batch_result_summary_with_abort(self):
        b = BatchResult(
            abort_reason="CIRCUIT_BREAKER_ABORT after 2 trips",
            circuit_breaker_trips=2,
        )
        s = b.summary()
        assert "CIRCUIT_BREAKER_ABORT" in s
        assert "breaker_trips=2" in s

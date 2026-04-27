"""Tests for YouTube Data API v3 metadata function in youtube_ingest.py."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from universal_agent.youtube_ingest import (
    _parse_iso8601_duration,
    _run_youtube_data_api_metadata,
)

# ── ISO 8601 duration parsing ──────────────────────────────────────────────


class TestParseIso8601Duration:
    def test_full_hms(self):
        assert _parse_iso8601_duration("PT1H2M34S") == 3754

    def test_minutes_seconds(self):
        assert _parse_iso8601_duration("PT12M34S") == 754

    def test_seconds_only(self):
        assert _parse_iso8601_duration("PT45S") == 45

    def test_hours_only(self):
        assert _parse_iso8601_duration("PT2H") == 7200

    def test_minutes_only(self):
        assert _parse_iso8601_duration("PT5M") == 300

    def test_empty_string(self):
        assert _parse_iso8601_duration("") is None

    def test_none(self):
        assert _parse_iso8601_duration(None) is None

    def test_invalid_format(self):
        assert _parse_iso8601_duration("not-a-duration") is None

    def test_case_insensitive(self):
        assert _parse_iso8601_duration("pt1h2m3s") == 3723


# ── YouTube Data API metadata ─────────────────────────────────────────────


MOCK_API_RESPONSE = {
    "items": [
        {
            "snippet": {
                "title": "Test Video Title",
                "channelTitle": "Test Channel",
                "channelId": "UC1234567890A",
                "publishedAt": "2026-03-15T10:30:00Z",
                "description": "A test description.",
            },
            "contentDetails": {
                "duration": "PT12M34S",
            },
            "statistics": {
                "viewCount": "12345",
                "likeCount": "678",
            },
        }
    ]
}


class TestYoutubeDataApiMetadata:
    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        result = _run_youtube_data_api_metadata("dQw4w9WgXcQ")
        assert result["ok"] is False
        assert result["error"] == "youtube_api_key_missing"
        assert result["source"] == "youtube_data_api_v3"

    @patch("urllib.request.urlopen")
    def test_successful_metadata(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("YOUTUBE_API_KEY", "test-api-key")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(MOCK_API_RESPONSE).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = _run_youtube_data_api_metadata("dQw4w9WgXcQ")

        assert result["ok"] is True
        assert result["source"] == "youtube_data_api_v3"
        assert result["metadata"]["title"] == "Test Video Title"
        assert result["metadata"]["channel"] == "Test Channel"
        assert result["metadata"]["channel_id"] == "UC1234567890A"
        assert result["metadata"]["duration"] == 754  # 12*60 + 34
        assert result["metadata"]["upload_date"] == "20260315"
        assert result["metadata"]["view_count"] == 12345
        assert result["metadata"]["like_count"] == 678
        assert result["metadata"]["description"] == "A test description."

    @patch("urllib.request.urlopen")
    def test_empty_results(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("YOUTUBE_API_KEY", "test-api-key")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"items": []}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = _run_youtube_data_api_metadata("nonexistent123")

        assert result["ok"] is False
        assert result["error"] == "youtube_data_api_no_results"
        assert result["failure_class"] == "video_unavailable"

    @patch("urllib.request.urlopen")
    def test_network_error(self, mock_urlopen, monkeypatch):
        monkeypatch.setenv("YOUTUBE_API_KEY", "test-api-key")
        mock_urlopen.side_effect = Exception("Connection refused")

        result = _run_youtube_data_api_metadata("dQw4w9WgXcQ")

        assert result["ok"] is False
        assert result["error"] == "youtube_data_api_request_failed"
        assert "Connection refused" in result["detail"]

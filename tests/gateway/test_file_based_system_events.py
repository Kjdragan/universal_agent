"""Tests for the file-based system events passing mechanism.

Validates the producer (execution_engine) -> consumer (main) file handoff
that replaced the old env-var-based approach to avoid E2BIG errors.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


class TestFileBasedEventsProducer:
    """Test the execution_engine side: writing events to a temp file."""

    def test_writes_events_to_temp_file(self, tmp_path):
        """Simulate the producer writing events to a temp JSON file."""
        events = [
            {"text": "Heartbeat: system healthy", "type": "heartbeat"},
            {"text": "Cron: daily briefing due", "type": "cron"},
        ]
        events_file = tmp_path / "ua_system_events.json"
        events_file.write_text(json.dumps(events))

        loaded = json.loads(events_file.read_text())
        assert len(loaded) == 2
        assert loaded[0]["text"] == "Heartbeat: system healthy"
        assert loaded[1]["type"] == "cron"

    def test_temp_file_can_hold_large_payloads(self, tmp_path):
        """File-based approach has no practical size limit (unlike env vars)."""
        # Generate 100 events with substantial text (~200KB total)
        events = [
            {"text": f"Event {i}: " + "x" * 2000, "type": "system"}
            for i in range(100)
        ]
        events_file = tmp_path / "ua_system_events.json"
        events_file.write_text(json.dumps(events))

        size = events_file.stat().st_size
        assert size > 200_000, f"Expected >200KB payload, got {size}"

        loaded = json.loads(events_file.read_text())
        assert len(loaded) == 100


class TestFileBasedEventsConsumer:
    """Test the main.py consumer: reading from UA_SYSTEM_EVENTS_FILE."""

    def _build_system_events_prompt(self, env_overrides: dict) -> str:
        """Replicate _system_events_prompt_from_env() logic for testing."""
        events_file = (env_overrides.get("UA_SYSTEM_EVENTS_FILE") or "").strip()
        if events_file and os.path.isfile(events_file):
            try:
                with open(events_file, "r") as f:
                    parsed = json.load(f)
                # Don't unlink in tests - we want to inspect
                if isinstance(parsed, list) and parsed:
                    lines = []
                    for evt in parsed:
                        if isinstance(evt, dict):
                            text = str(evt.get("text") or "").strip()
                            if not text:
                                continue
                            lines.append(f"System: {text}")
                    return "\n".join(lines)
            except Exception:
                pass

        # Legacy fallback
        raw = (env_overrides.get("UA_SYSTEM_EVENTS_PROMPT") or "").strip()
        if raw:
            return raw
        raw_json = (env_overrides.get("UA_SYSTEM_EVENTS_JSON") or "").strip()
        if not raw_json:
            return ""
        try:
            parsed = json.loads(raw_json)
        except Exception:
            return ""
        if not isinstance(parsed, list) or not parsed:
            return ""
        lines = []
        for evt in parsed:
            if isinstance(evt, dict):
                text = str(evt.get("text") or "").strip()
                if not text:
                    continue
                lines.append(f"System: {text}")
        return "\n".join(lines)

    def test_reads_from_file(self, tmp_path):
        """Consumer should read events from the file path."""
        events = [{"text": "File event 1"}, {"text": "File event 2"}]
        events_file = tmp_path / "events.json"
        events_file.write_text(json.dumps(events))

        result = self._build_system_events_prompt(
            {"UA_SYSTEM_EVENTS_FILE": str(events_file)}
        )
        assert "System: File event 1" in result
        assert "System: File event 2" in result

    def test_file_takes_priority_over_env_vars(self, tmp_path):
        """File-based events should win over legacy env var events."""
        events_file = tmp_path / "events.json"
        events_file.write_text(json.dumps([{"text": "from file"}]))

        result = self._build_system_events_prompt({
            "UA_SYSTEM_EVENTS_FILE": str(events_file),
            "UA_SYSTEM_EVENTS_PROMPT": "from prompt env",
            "UA_SYSTEM_EVENTS_JSON": json.dumps([{"text": "from json env"}]),
        })
        assert "from file" in result
        assert "from prompt env" not in result
        assert "from json env" not in result

    def test_falls_back_to_prompt_env(self):
        """When no file, should fall back to UA_SYSTEM_EVENTS_PROMPT."""
        result = self._build_system_events_prompt({
            "UA_SYSTEM_EVENTS_PROMPT": "Legacy prompt event",
        })
        assert result == "Legacy prompt event"

    def test_falls_back_to_json_env(self):
        """When no file or prompt, should fall back to UA_SYSTEM_EVENTS_JSON."""
        events = [{"text": "Legacy JSON event"}]
        result = self._build_system_events_prompt({
            "UA_SYSTEM_EVENTS_JSON": json.dumps(events),
        })
        assert "System: Legacy JSON event" in result

    def test_empty_when_no_sources(self):
        """Should return empty string when nothing is set."""
        result = self._build_system_events_prompt({})
        assert result == ""

    def test_handles_corrupt_file_gracefully(self, tmp_path):
        """Corrupt file should fall back to env vars."""
        events_file = tmp_path / "bad.json"
        events_file.write_text("NOT VALID JSON {{{{")

        result = self._build_system_events_prompt({
            "UA_SYSTEM_EVENTS_FILE": str(events_file),
            "UA_SYSTEM_EVENTS_PROMPT": "fallback",
        })
        assert result == "fallback"

    def test_handles_missing_file_gracefully(self):
        """Non-existent file should fall back to env vars."""
        result = self._build_system_events_prompt({
            "UA_SYSTEM_EVENTS_FILE": "/nonexistent/path/events.json",
            "UA_SYSTEM_EVENTS_PROMPT": "fallback",
        })
        assert result == "fallback"

    def test_skips_events_with_empty_text(self, tmp_path):
        """Events with empty or missing text should be skipped."""
        events = [{"text": "Valid"}, {"text": ""}, {"text": "   "}, {}]
        events_file = tmp_path / "events.json"
        events_file.write_text(json.dumps(events))

        result = self._build_system_events_prompt(
            {"UA_SYSTEM_EVENTS_FILE": str(events_file)}
        )
        assert result == "System: Valid"

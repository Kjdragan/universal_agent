"""Unit tests for _safe_parse_iso in mission_control_tiles.py.

Covers: naive SQLite strings, ISO 8601 with Z suffix, timezone-aware
strings, empty/None inputs, and malformed values.
"""

from datetime import datetime, timezone

from universal_agent.services.mission_control_tiles import _safe_parse_iso


class TestSafeParseIso:
    def test_none_returns_none(self):
        assert _safe_parse_iso(None) is None

    def test_empty_string_returns_none(self):
        assert _safe_parse_iso("") is None

    def test_whitespace_returns_none(self):
        assert _safe_parse_iso("   ") is None

    def test_sqlite_naive_string_becomes_utc(self):
        """SQLite datetime('now') produces naive 'YYYY-MM-DD HH:MM:SS'."""
        result = _safe_parse_iso("2026-05-10 14:30:00")
        assert result is not None
        assert result == datetime(2026, 5, 10, 14, 30, 0, tzinfo=timezone.utc)

    def test_iso_with_z_suffix(self):
        result = _safe_parse_iso("2026-05-10T14:30:00Z")
        assert result is not None
        assert result.tzinfo is not None
        assert result.year == 2026

    def test_iso_with_utc_offset(self):
        result = _safe_parse_iso("2026-05-10T14:30:00+00:00")
        assert result is not None
        assert result.tzinfo is not None

    def test_iso_with_negative_offset(self):
        result = _safe_parse_iso("2026-05-10T09:30:00-05:00")
        assert result is not None
        assert result.tzinfo is not None
        assert result.utcoffset().total_seconds() == -5 * 3600

    def test_naive_string_gets_utc_tzinfo(self):
        result = _safe_parse_iso("2026-01-01 00:00:00")
        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_malformed_string_returns_none(self):
        assert _safe_parse_iso("not-a-date") is None

    def test_partial_date_returns_none(self):
        assert _safe_parse_iso("2026-05") is None

    def test_microseconds_preserved(self):
        result = _safe_parse_iso("2026-05-10T14:30:00.123456+00:00")
        assert result is not None
        assert result.microsecond == 123456

    def test_roundtrip_consistency(self):
        """A UTC ISO string round-trips cleanly."""
        original = "2026-05-10T14:30:00+00:00"
        parsed = _safe_parse_iso(original)
        assert parsed is not None
        assert parsed.isoformat() == original

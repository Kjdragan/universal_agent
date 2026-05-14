"""Unit tests for supervisors/builders.py pure helper functions."""
from __future__ import annotations

from datetime import datetime, timezone

from universal_agent.supervisors.builders import (
    _as_dict,
    _as_list,
    _iso_now,
    _safe_float,
    _safe_int,
    _summary_line,
)


class TestSafeInt:
    def test_int_passthrough(self):
        assert _safe_int(42) == 42

    def test_string_conversion(self):
        assert _safe_int("7") == 7

    def test_float_truncation(self):
        assert _safe_int(3.9) == 3

    def test_invalid_string_returns_default(self):
        assert _safe_int("not-a-number") == 0

    def test_none_returns_default(self):
        assert _safe_int(None) == 0

    def test_custom_default(self):
        assert _safe_int("bad", default=-1) == -1

    def test_empty_string_returns_default(self):
        assert _safe_int("") == 0


class TestSafeFloat:
    def test_float_passthrough(self):
        assert _safe_float(3.14) == 3.14

    def test_string_conversion(self):
        assert _safe_float("2.5") == 2.5

    def test_int_to_float(self):
        assert _safe_float(10) == 10.0

    def test_invalid_returns_default(self):
        assert _safe_float("bad") == 0.0

    def test_none_returns_default(self):
        assert _safe_float(None) == 0.0

    def test_custom_default(self):
        assert _safe_float("x", default=-1.0) == -1.0


class TestAsDict:
    def test_dict_passthrough(self):
        d = {"a": 1}
        assert _as_dict(d) == d

    def test_non_dict_returns_empty(self):
        assert _as_dict("string") == {}

    def test_none_returns_empty(self):
        assert _as_dict(None) == {}

    def test_list_returns_empty(self):
        assert _as_dict([1, 2]) == {}


class TestAsList:
    def test_list_passthrough(self):
        lst = [1, 2, 3]
        assert _as_list(lst) == lst

    def test_non_list_returns_empty(self):
        assert _as_list("string") == []

    def test_none_returns_empty(self):
        assert _as_list(None) == []

    def test_dict_returns_empty(self):
        assert _as_list({"a": 1}) == []


class TestIsoNow:
    def test_returns_valid_iso_string(self):
        result = _iso_now()
        parsed = datetime.fromisoformat(result)
        assert parsed.tzinfo is not None

    def test_is_utc(self):
        result = _iso_now()
        parsed = datetime.fromisoformat(result)
        assert parsed.tzinfo == timezone.utc


class TestSummaryLine:
    def test_label_only(self):
        result = _summary_line({}, label="Test")
        assert result == "Test snapshot"

    def test_with_dispatch_eligible(self):
        result = _summary_line({"dispatch_eligible": 5}, label="Factory")
        assert "Factory snapshot" in result
        assert "dispatch eligible 5" in result

    def test_with_csi_incidents(self):
        result = _summary_line({"open_csi_incidents": 3}, label="CSI")
        assert "open CSI incidents 3" in result

    def test_with_degraded_sources(self):
        result = _summary_line({"source_degraded": 2}, label="CSI")
        assert "degraded CSI sources 2" in result

    def test_all_fields(self):
        kpis = {
            "dispatch_eligible": 10,
            "open_csi_incidents": 4,
            "source_degraded": 1,
        }
        result = _summary_line(kpis, label="Full")
        assert "dispatch eligible 10" in result
        assert "open CSI incidents 4" in result
        assert "degraded CSI sources 1" in result
        assert result.count("|") == 3

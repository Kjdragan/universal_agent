"""Characterization + regression tests for JSON / integer parse error
handling in ``universal_agent.tools.csi_bridge``.

These cover the three parse helpers whose over-broad ``except Exception``
clauses were narrowed to the specific exception types the guarded operations
actually raise:

* ``_loads_obj``                -> ``json.JSONDecodeError``
* ``_read_watchlist``           -> ``(json.JSONDecodeError, OSError)``
* ``_parse_source_min_events``  -> ``json.JSONDecodeError`` + ``(TypeError, ValueError)``

The narrowing is behavior-preserving for every real input (``JSONDecodeError``
was already caught). These tests lock that intended behavior in place and guard
against a future over-narrowing that would re-break it -- notably that a
file-system error (``OSError``) in ``_read_watchlist`` is still reported as a
``parse_failed`` dict rather than allowed to propagate.
"""
from __future__ import annotations

import json
from pathlib import Path

from universal_agent.tools import csi_bridge


# --------------------------------------------------------------------------- #
# _loads_obj
# --------------------------------------------------------------------------- #
class TestLoadsObj:
    def test_none_returns_default(self):
        assert csi_bridge._loads_obj(None, default={"d": 1}) == {"d": 1}

    def test_dict_passthrough(self):
        assert csi_bridge._loads_obj({"a": 1}, default={}) == {"a": 1}

    def test_list_passthrough(self):
        assert csi_bridge._loads_obj([1, 2], default=[]) == [1, 2]

    def test_valid_json_string_parsed(self):
        assert csi_bridge._loads_obj('{"a": 1}', default={}) == {"a": 1}

    def test_empty_string_returns_default(self):
        assert csi_bridge._loads_obj("", default={"d": 1}) == {"d": 1}

    def test_whitespace_only_returns_default(self):
        assert csi_bridge._loads_obj("   \n  ", default={"d": 1}) == {"d": 1}

    def test_malformed_json_returns_default(self):
        # json.JSONDecodeError is the narrowed catch; default must still be returned.
        assert csi_bridge._loads_obj("not valid json", default={"d": 1}) == {"d": 1}

    def test_partial_json_returns_default(self):
        assert csi_bridge._loads_obj('{"a": 1', default={"d": 1}) == {"d": 1}


# --------------------------------------------------------------------------- #
# _read_watchlist
# --------------------------------------------------------------------------- #
class TestReadWatchlist:
    def test_missing_file_reports_not_exists_without_error(self, tmp_path):
        missing = tmp_path / "does_not_exist.json"
        out = csi_bridge._read_watchlist(str(missing), "youtube")
        assert out["exists"] is False
        assert out["count"] == 0
        assert out["preview"] == []
        # No parse attempted -> no error key.
        assert "error" not in out

    def test_malformed_json_reports_parse_failed(self, tmp_path):
        # json.JSONDecodeError path: file exists but is not valid JSON.
        bad = tmp_path / "bad.json"
        bad.write_text("{ not valid json", encoding="utf-8")
        out = csi_bridge._read_watchlist(str(bad), "youtube")
        assert out["exists"] is True
        assert out["count"] == 0
        assert out["error"].startswith("parse_failed:")

    def test_directory_is_handled_as_parse_failed(self, tmp_path):
        # OSError path: read_text() on a directory raises IsADirectoryError
        # (an OSError subclass). The narrowed `(json.JSONDecodeError, OSError)`
        # catch must still report it as parse_failed rather than propagate --
        # this is the regression that would break if someone over-narrowed to
        # JSONDecodeError alone.
        out = csi_bridge._read_watchlist(str(tmp_path), "youtube")
        assert out["exists"] is True
        assert out["error"].startswith("parse_failed:")

    def test_valid_list_watchlist(self, tmp_path):
        wf = tmp_path / "watchlist.json"
        wf.write_text(json.dumps(["chanA", "chanB"]), encoding="utf-8")
        out = csi_bridge._read_watchlist(str(wf), "youtube")
        assert out["exists"] is True
        assert out["count"] == 2
        assert out["preview"] == ["chanA", "chanB"]

    def test_valid_dict_watchlist_with_channels_key(self, tmp_path):
        wf = tmp_path / "watchlist.json"
        wf.write_text(json.dumps({"channels": ["chanA", "chanB", "chanC"]}), encoding="utf-8")
        out = csi_bridge._read_watchlist(str(wf), "youtube")
        assert out["count"] == 3
        assert out["preview"] == ["chanA", "chanB", "chanC"]


# --------------------------------------------------------------------------- #
# _parse_source_min_events
# --------------------------------------------------------------------------- #
class TestParseSourceMinEvents:
    def test_empty_returns_empty(self):
        assert csi_bridge._parse_source_min_events("") == {}
        assert csi_bridge._parse_source_min_events(None) == {}

    def test_json_dict_form(self):
        raw = json.dumps({"youtube_channel_rss": "5", "csi_analytics": 3})
        assert csi_bridge._parse_source_min_events(raw) == {
            "youtube_channel_rss": 5,
            "csi_analytics": 3,
        }

    def test_json_non_int_value_is_skipped(self):
        # int("x") raises ValueError -> narrowed (TypeError, ValueError) catch
        # skips the entry rather than aborting the whole parse.
        raw = json.dumps({"good": 2, "bad": "not-a-number"})
        assert csi_bridge._parse_source_min_events(raw) == {"good": 2}

    def test_malformed_json_falls_back_to_comma_form(self):
        # json.JSONDecodeError -> narrowed outer catch -> comma-separated fallback.
        assert csi_bridge._parse_source_min_events("a=1,b=2") == {"a": 1, "b": 2}

    def test_comma_form_clamps_negative_to_zero(self):
        assert csi_bridge._parse_source_min_events("a=-3") == {"a": 0}

    def test_comma_form_skips_non_int(self):
        assert csi_bridge._parse_source_min_events("a=1,b=oops,c=3") == {"a": 1, "c": 3}

    def test_comma_form_ignores_malformed_tokens(self):
        assert csi_bridge._parse_source_min_events("a=1,no_equals,=5") == {"a": 1}

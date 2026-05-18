"""Tests for private and public helpers in feature_flags that lack coverage.

The existing test_feature_flags_defaults.py covers heartbeat_enabled,
cron_enabled, and task_hub_missions_enabled.  This file covers:

  * _read_int — env-to-int with optional minimum clamp
  * _read_choice — env-to-string allowlist guard
  * _read_csv_list — env-to-list with optional allowlist filter
  * memory_session_sources — CSV parsing with fixed allowlist {"memory","sessions"}
  * memory_flush_enabled — boolean flag pattern
  * memory_scope — choice flag
  * memory_rollover_mode — choice flag
  * gws_events_enabled — boolean flag
  * vp_external_dispatch_enabled — uses _read_env_bool (tri-state)
  * vp_enabled_ids — uses _read_csv_list
"""

from __future__ import annotations

import pytest

from universal_agent.feature_flags import (
    _read_choice,
    _read_csv_list,
    _read_int,
    gws_events_enabled,
    memory_flush_enabled,
    memory_rollover_mode,
    memory_scope,
    memory_session_sources,
    vp_enabled_ids,
    vp_external_dispatch_enabled,
)

# ── _read_int ─────────────────────────────────────────────────────────────────

class TestReadInt:
    def test_returns_default_when_env_absent(self, monkeypatch):
        monkeypatch.delenv("TEST_READ_INT_ABSENT", raising=False)
        assert _read_int("TEST_READ_INT_ABSENT", 42) == 42

    def test_returns_parsed_int(self, monkeypatch):
        monkeypatch.setenv("TEST_READ_INT", "7")
        assert _read_int("TEST_READ_INT", 0) == 7

    def test_returns_default_for_non_numeric(self, monkeypatch):
        monkeypatch.setenv("TEST_READ_INT_BAD", "notanint")
        assert _read_int("TEST_READ_INT_BAD", 99) == 99

    def test_minimum_clamps_from_below(self, monkeypatch):
        monkeypatch.setenv("TEST_READ_INT_MIN", "-10")
        assert _read_int("TEST_READ_INT_MIN", 0, minimum=0) == 0

    def test_minimum_allows_value_above_minimum(self, monkeypatch):
        monkeypatch.setenv("TEST_READ_INT_MIN2", "50")
        assert _read_int("TEST_READ_INT_MIN2", 0, minimum=10) == 50

    def test_minimum_none_does_not_clamp(self, monkeypatch):
        monkeypatch.setenv("TEST_READ_INT_NOMIN", "-5")
        assert _read_int("TEST_READ_INT_NOMIN", 0, minimum=None) == -5

    def test_zero_is_valid_value(self, monkeypatch):
        monkeypatch.setenv("TEST_READ_INT_ZERO", "0")
        assert _read_int("TEST_READ_INT_ZERO", 100) == 0

    def test_minimum_clamps_default_when_env_absent(self, monkeypatch):
        monkeypatch.delenv("TEST_READ_INT_CLAMP_DEFAULT", raising=False)
        assert _read_int("TEST_READ_INT_CLAMP_DEFAULT", -1, minimum=0) == 0


# ── _read_choice ─────────────────────────────────────────────────────────────

class TestReadChoice:
    def test_returns_default_when_env_absent(self, monkeypatch):
        monkeypatch.delenv("TEST_CHOICE_ABSENT", raising=False)
        assert _read_choice("TEST_CHOICE_ABSENT", ("a", "b"), "a") == "a"

    def test_returns_valid_choice(self, monkeypatch):
        monkeypatch.setenv("TEST_CHOICE", "b")
        assert _read_choice("TEST_CHOICE", ("a", "b"), "a") == "b"

    def test_rejects_invalid_choice_returns_default(self, monkeypatch):
        monkeypatch.setenv("TEST_CHOICE_BAD", "c")
        assert _read_choice("TEST_CHOICE_BAD", ("a", "b"), "a") == "a"

    def test_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("TEST_CHOICE_CASE", "B")
        assert _read_choice("TEST_CHOICE_CASE", ("a", "b"), "a") == "b"

    def test_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("TEST_CHOICE_WS", "  a  ")
        assert _read_choice("TEST_CHOICE_WS", ("a", "b"), "b") == "a"

    def test_empty_env_returns_default(self, monkeypatch):
        monkeypatch.setenv("TEST_CHOICE_EMPTY", "")
        assert _read_choice("TEST_CHOICE_EMPTY", ("a", "b"), "a") == "a"


# ── _read_csv_list ────────────────────────────────────────────────────────────

class TestReadCsvList:
    def test_returns_empty_list_when_absent(self, monkeypatch):
        monkeypatch.delenv("TEST_CSV_ABSENT", raising=False)
        assert _read_csv_list("TEST_CSV_ABSENT") == []

    def test_returns_empty_list_for_empty_string(self, monkeypatch):
        monkeypatch.setenv("TEST_CSV_EMPTY", "")
        assert _read_csv_list("TEST_CSV_EMPTY") == []

    def test_parses_comma_separated_values(self, monkeypatch):
        monkeypatch.setenv("TEST_CSV", "a,b,c")
        assert _read_csv_list("TEST_CSV") == ["a", "b", "c"]

    def test_strips_whitespace_around_items(self, monkeypatch):
        monkeypatch.setenv("TEST_CSV_WS", " a , b , c ")
        assert _read_csv_list("TEST_CSV_WS") == ["a", "b", "c"]

    def test_filters_by_allowlist(self, monkeypatch):
        monkeypatch.setenv("TEST_CSV_ALLOW", "a,x,b")
        assert _read_csv_list("TEST_CSV_ALLOW", allowed={"a", "b"}) == ["a", "b"]

    def test_allowlist_filter_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("TEST_CSV_ALLOW_CASE", "A,B,C")
        result = _read_csv_list("TEST_CSV_ALLOW_CASE", allowed={"a", "b"})
        assert result == ["A", "B"]

    def test_all_items_rejected_by_allowlist_returns_empty(self, monkeypatch):
        monkeypatch.setenv("TEST_CSV_REJECT_ALL", "x,y,z")
        assert _read_csv_list("TEST_CSV_REJECT_ALL", allowed={"a", "b"}) == []

    def test_skips_empty_items_in_csv(self, monkeypatch):
        monkeypatch.setenv("TEST_CSV_GAPS", "a,,b,,c")
        assert _read_csv_list("TEST_CSV_GAPS") == ["a", "b", "c"]


# ── memory_session_sources ────────────────────────────────────────────────────

class TestMemorySessionSources:
    def test_returns_default_when_absent(self, monkeypatch):
        monkeypatch.delenv("UA_MEMORY_SOURCES", raising=False)
        assert memory_session_sources() == ["memory", "sessions"]

    def test_parses_single_valid_source(self, monkeypatch):
        monkeypatch.setenv("UA_MEMORY_SOURCES", "memory")
        assert memory_session_sources() == ["memory"]

    def test_parses_both_valid_sources(self, monkeypatch):
        monkeypatch.setenv("UA_MEMORY_SOURCES", "sessions,memory")
        result = memory_session_sources()
        assert set(result) == {"sessions", "memory"}

    def test_invalid_source_filtered_out(self, monkeypatch):
        monkeypatch.setenv("UA_MEMORY_SOURCES", "memory,unknown")
        assert memory_session_sources() == ["memory"]

    def test_all_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("UA_MEMORY_SOURCES", "bogus,garbage")
        assert memory_session_sources() == ["memory", "sessions"]

    def test_case_insensitive_parsing(self, monkeypatch):
        monkeypatch.setenv("UA_MEMORY_SOURCES", "MEMORY")
        assert memory_session_sources() == ["memory"]


# ── memory_flush_enabled ──────────────────────────────────────────────────────

class TestMemoryFlushEnabled:
    def test_defaults_true(self, monkeypatch):
        monkeypatch.delenv("UA_MEMORY_FLUSH_ENABLED", raising=False)
        monkeypatch.delenv("UA_DISABLE_MEMORY_FLUSH_ENABLED", raising=False)
        assert memory_flush_enabled() is True

    def test_enable_env_activates(self, monkeypatch):
        monkeypatch.delenv("UA_DISABLE_MEMORY_FLUSH_ENABLED", raising=False)
        monkeypatch.setenv("UA_MEMORY_FLUSH_ENABLED", "1")
        assert memory_flush_enabled() is True

    def test_disable_env_kills_it(self, monkeypatch):
        monkeypatch.setenv("UA_DISABLE_MEMORY_FLUSH_ENABLED", "1")
        monkeypatch.setenv("UA_MEMORY_FLUSH_ENABLED", "1")
        assert memory_flush_enabled() is False

    def test_explicit_default_false(self, monkeypatch):
        monkeypatch.delenv("UA_MEMORY_FLUSH_ENABLED", raising=False)
        monkeypatch.delenv("UA_DISABLE_MEMORY_FLUSH_ENABLED", raising=False)
        assert memory_flush_enabled(default=False) is False


# ── memory_scope ─────────────────────────────────────────────────────────────

class TestMemoryScope:
    def test_defaults_direct_only(self, monkeypatch):
        monkeypatch.delenv("UA_MEMORY_SCOPE", raising=False)
        assert memory_scope() == "direct_only"

    def test_accepts_all(self, monkeypatch):
        monkeypatch.setenv("UA_MEMORY_SCOPE", "all")
        assert memory_scope() == "all"

    def test_rejects_invalid_returns_default(self, monkeypatch):
        monkeypatch.setenv("UA_MEMORY_SCOPE", "bogus")
        assert memory_scope() == "direct_only"


# ── memory_rollover_mode ─────────────────────────────────────────────────────

class TestMemoryRolloverMode:
    def test_defaults_transcript(self, monkeypatch):
        monkeypatch.delenv("UA_MEMORY_ROLLOVER_MODE", raising=False)
        assert memory_rollover_mode() == "transcript"

    def test_accepts_summary_only(self, monkeypatch):
        monkeypatch.setenv("UA_MEMORY_ROLLOVER_MODE", "summary_only")
        assert memory_rollover_mode() == "summary_only"

    def test_rejects_invalid_returns_default(self, monkeypatch):
        monkeypatch.setenv("UA_MEMORY_ROLLOVER_MODE", "full_text")
        assert memory_rollover_mode() == "transcript"

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("UA_MEMORY_ROLLOVER_MODE", "TRANSCRIPT")
        assert memory_rollover_mode() == "transcript"


# ── gws_events_enabled ────────────────────────────────────────────────────────

class TestGwsEventsEnabled:
    def test_defaults_false(self, monkeypatch):
        monkeypatch.delenv("UA_ENABLE_GOOGLE_WORKSPACE_EVENTS", raising=False)
        monkeypatch.delenv("UA_DISABLE_GOOGLE_WORKSPACE_EVENTS", raising=False)
        assert gws_events_enabled() is False

    def test_enable_env_activates(self, monkeypatch):
        monkeypatch.delenv("UA_DISABLE_GOOGLE_WORKSPACE_EVENTS", raising=False)
        monkeypatch.setenv("UA_ENABLE_GOOGLE_WORKSPACE_EVENTS", "1")
        assert gws_events_enabled() is True

    def test_disable_env_overrides_enable(self, monkeypatch):
        monkeypatch.setenv("UA_DISABLE_GOOGLE_WORKSPACE_EVENTS", "1")
        monkeypatch.setenv("UA_ENABLE_GOOGLE_WORKSPACE_EVENTS", "1")
        assert gws_events_enabled() is False


# ── vp_external_dispatch_enabled ─────────────────────────────────────────────

class TestVpExternalDispatchEnabled:
    def test_defaults_false(self, monkeypatch):
        monkeypatch.delenv("UA_VP_EXTERNAL_DISPATCH_ENABLED", raising=False)
        monkeypatch.delenv("UA_DISABLE_VP_EXTERNAL_DISPATCH_ENABLED", raising=False)
        assert vp_external_dispatch_enabled() is False

    def test_explicit_true_enables(self, monkeypatch):
        monkeypatch.delenv("UA_DISABLE_VP_EXTERNAL_DISPATCH_ENABLED", raising=False)
        monkeypatch.setenv("UA_VP_EXTERNAL_DISPATCH_ENABLED", "true")
        assert vp_external_dispatch_enabled() is True

    def test_disable_env_wins(self, monkeypatch):
        monkeypatch.setenv("UA_DISABLE_VP_EXTERNAL_DISPATCH_ENABLED", "1")
        monkeypatch.setenv("UA_VP_EXTERNAL_DISPATCH_ENABLED", "1")
        assert vp_external_dispatch_enabled() is False

    def test_explicit_false_overrides_default_true(self, monkeypatch):
        monkeypatch.delenv("UA_DISABLE_VP_EXTERNAL_DISPATCH_ENABLED", raising=False)
        monkeypatch.setenv("UA_VP_EXTERNAL_DISPATCH_ENABLED", "false")
        assert vp_external_dispatch_enabled(default=True) is False


# ── vp_enabled_ids ────────────────────────────────────────────────────────────

class TestVpEnabledIds:
    def test_defaults_include_coder_and_general(self, monkeypatch):
        monkeypatch.delenv("UA_VP_ENABLED_IDS", raising=False)
        result = vp_enabled_ids()
        assert "vp.coder.primary" in result
        assert "vp.general.primary" in result

    def test_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("UA_VP_ENABLED_IDS", "vp.coder.primary")
        assert vp_enabled_ids() == ["vp.coder.primary"]

    def test_multiple_ids_parsed(self, monkeypatch):
        monkeypatch.setenv("UA_VP_ENABLED_IDS", "vp.a, vp.b")
        assert vp_enabled_ids() == ["vp.a", "vp.b"]

    def test_empty_env_returns_default(self, monkeypatch):
        monkeypatch.setenv("UA_VP_ENABLED_IDS", "")
        result = vp_enabled_ids()
        assert "vp.coder.primary" in result

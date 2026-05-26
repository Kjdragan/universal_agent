"""Unit tests for heartbeat_scope_filter.py — HEARTBEAT.md scope-section filtering."""
from __future__ import annotations

import pytest

from universal_agent.heartbeat_scope_filter import filter_heartbeat_by_scope

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
HQ_MARKER = "<!-- scope:hq -->"
LOCAL_MARKER = "<!-- scope:local -->"
ALL_MARKER = "<!-- scope:all -->"


# ---------------------------------------------------------------------------
# No markers — content returned as-is
# ---------------------------------------------------------------------------
class TestNoMarkers:
    def test_content_without_markers_returned_unchanged(self):
        content = "Hello world\nThis is heartbeat content."
        result = filter_heartbeat_by_scope(content, "global")
        assert result == content

    def test_empty_string_returned_as_is(self):
        assert filter_heartbeat_by_scope("", "global") == ""

    def test_whitespace_only_returned_as_is(self):
        ws = "   \n  "
        assert filter_heartbeat_by_scope(ws, "global") == ws

    def test_none_handled_gracefully(self):
        # None is falsy — early return
        result = filter_heartbeat_by_scope(None, "global")  # type: ignore[arg-type]
        assert result is None


# ---------------------------------------------------------------------------
# global scope: keeps hq + all, drops local
# ---------------------------------------------------------------------------
class TestGlobalScope:
    def test_keeps_hq_section(self):
        content = f"{HQ_MARKER}\nhq content here\n"
        result = filter_heartbeat_by_scope(content, "global")
        assert "hq content here" in result

    def test_drops_local_section(self):
        content = f"{LOCAL_MARKER}\nlocal only content\n"
        result = filter_heartbeat_by_scope(content, "global")
        assert "local only content" not in result

    def test_keeps_all_section(self):
        content = f"{ALL_MARKER}\nshared content\n"
        result = filter_heartbeat_by_scope(content, "global")
        assert "shared content" in result

    def test_mixed_sections_global(self):
        content = (
            f"{HQ_MARKER}\nhq task\n"
            f"{LOCAL_MARKER}\nlocal task\n"
            f"{ALL_MARKER}\nshared task\n"
        )
        result = filter_heartbeat_by_scope(content, "global")
        assert "hq task" in result
        assert "local task" not in result
        assert "shared task" in result

    def test_pre_marker_content_always_kept(self):
        content = f"preamble content\n{LOCAL_MARKER}\nlocal section\n"
        result = filter_heartbeat_by_scope(content, "global")
        assert "preamble content" in result
        assert "local section" not in result


# ---------------------------------------------------------------------------
# local scope: keeps local + all, drops hq
# ---------------------------------------------------------------------------
class TestLocalScope:
    def test_drops_hq_section(self):
        content = f"{HQ_MARKER}\nhq only content\n"
        result = filter_heartbeat_by_scope(content, "local")
        assert "hq only content" not in result

    def test_keeps_local_section(self):
        content = f"{LOCAL_MARKER}\nlocal content here\n"
        result = filter_heartbeat_by_scope(content, "local")
        assert "local content here" in result

    def test_keeps_all_section(self):
        content = f"{ALL_MARKER}\nshared content\n"
        result = filter_heartbeat_by_scope(content, "local")
        assert "shared content" in result

    def test_mixed_sections_local(self):
        content = (
            f"{HQ_MARKER}\nhq task\n"
            f"{LOCAL_MARKER}\nlocal task\n"
            f"{ALL_MARKER}\nshared task\n"
        )
        result = filter_heartbeat_by_scope(content, "local")
        assert "hq task" not in result
        assert "local task" in result
        assert "shared task" in result


# ---------------------------------------------------------------------------
# Unknown scope: keeps everything (fallback)
# ---------------------------------------------------------------------------
class TestUnknownScope:
    def test_unknown_scope_keeps_all_sections(self):
        content = (
            f"{HQ_MARKER}\nhq content\n"
            f"{LOCAL_MARKER}\nlocal content\n"
            f"{ALL_MARKER}\nshared content\n"
        )
        result = filter_heartbeat_by_scope(content, "bogus_scope")
        assert "hq content" in result
        assert "local content" in result
        assert "shared content" in result

    def test_empty_scope_string_keeps_everything(self):
        content = f"{HQ_MARKER}\nhq only\n"
        result = filter_heartbeat_by_scope(content, "")
        assert "hq only" in result


# ---------------------------------------------------------------------------
# Edge cases — marker formatting
# ---------------------------------------------------------------------------
class TestMarkerFormatting:
    def test_marker_with_extra_spaces(self):
        content = "<!--  scope:hq  -->\nhq content\n"
        result = filter_heartbeat_by_scope(content, "global")
        assert "hq content" in result

    def test_marker_with_leading_whitespace(self):
        content = "   <!-- scope:local -->\nlocal content\n"
        result = filter_heartbeat_by_scope(content, "global")
        assert "local content" not in result

    def test_inline_marker_not_treated_as_scope_marker(self):
        # Marker embedded inside text (not on its own line) should NOT split
        content = "text <!-- scope:local --> more text"
        result = filter_heartbeat_by_scope(content, "global")
        # Since the regex requires the marker on its own line, the content
        # has no split and is returned as-is.
        assert result == content

    def test_multiple_hq_sections_all_kept_for_global(self):
        content = (
            f"{HQ_MARKER}\nfirst hq block\n"
            f"{HQ_MARKER}\nsecond hq block\n"
        )
        result = filter_heartbeat_by_scope(content, "global")
        assert "first hq block" in result
        assert "second hq block" in result

    def test_multiple_local_sections_dropped_for_global(self):
        content = (
            f"{LOCAL_MARKER}\nfirst local block\n"
            f"{LOCAL_MARKER}\nsecond local block\n"
        )
        result = filter_heartbeat_by_scope(content, "global")
        assert "first local block" not in result
        assert "second local block" not in result


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------
class TestOutputShape:
    def test_result_stripped_and_newline_terminated(self):
        content = f"\n\n{HQ_MARKER}\nhq section\n\n"
        result = filter_heartbeat_by_scope(content, "global")
        assert result.endswith("\n")
        assert not result.startswith("\n")

    def test_empty_section_text_excluded(self):
        # Section with only whitespace should not add empty chunks
        content = f"{HQ_MARKER}\n   \n{ALL_MARKER}\nreal content\n"
        result = filter_heartbeat_by_scope(content, "global")
        assert "real content" in result
        # Whitespace-only hq section should not appear as content
        assert result.strip() != ""

    def test_returns_string(self):
        content = f"{HQ_MARKER}\nhq content\n"
        result = filter_heartbeat_by_scope(content, "global")
        assert isinstance(result, str)

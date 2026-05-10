"""Unit tests for utils/session_workspace.py — safe_slug helper."""

import pytest

from universal_agent.utils.session_workspace import safe_slug


class TestSafeSlug:
    def test_simple_text(self):
        assert safe_slug("hello world") == "hello_world"

    def test_empty_returns_fallback(self):
        assert safe_slug("") == "run"

    def test_none_returns_fallback(self):
        assert safe_slug(None) == "run"

    def test_whitespace_only_returns_fallback(self):
        assert safe_slug("   ") == "run"

    def test_special_characters_replaced_with_underscore(self):
        # Each run of non-alphanumeric chars becomes a single _
        assert safe_slug("foo!@#$%bar") == "foo_bar"

    def test_leading_trailing_dots_stripped(self):
        assert safe_slug("...test...") == "test"

    def test_leading_trailing_hyphens_stripped(self):
        assert safe_slug("--test--") == "test"

    def test_custom_fallback(self):
        assert safe_slug("", fallback="default") == "default"

    def test_max_len_truncates(self):
        long_text = "a" * 200
        result = safe_slug(long_text, max_len=50)
        assert len(result) == 50

    def test_dots_and_hyphens_preserved(self):
        assert safe_slug("my-file_v2.0") == "my-file_v2.0"

    def test_all_special_chars_returns_fallback(self):
        assert safe_slug("!@#$%^&*()") == "run"

    def test_mixed_content_collapses_runs(self):
        # Comma+space collapses to single _
        assert safe_slug("Hello, World! 2024") == "Hello_World_2024"

    def test_tabs_and_newlines(self):
        assert safe_slug("hello\tworld\nfoo") == "hello_world_foo"

    def test_max_len_default_80(self):
        long_text = "a" * 200
        result = safe_slug(long_text)
        assert len(result) == 80

    def test_unicode_preserved(self):
        assert safe_slug("café") == "caf"

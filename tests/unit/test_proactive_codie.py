"""Unit tests for proactive_codie pure helper functions.

Tests cover: _slug, _cleanup_task_id, _cleanup_task_description, _pick_daily_theme.
No LLM/DB/network dependencies.
"""

from __future__ import annotations

from datetime import datetime, timezone

from universal_agent.services.proactive_codie import (
    DEFAULT_CLEANUP_THEMES,
    _slug,
    _cleanup_task_id,
    _cleanup_task_description,
    _pick_daily_theme,
)


# ---------------------------------------------------------------------------
# _slug
# ---------------------------------------------------------------------------


def test_slug_converts_to_lowercase_and_replaces_non_alphanumeric():
    assert _slug("Hello World! 123") == "hello-world-123"


def test_slug_strips_leading_and_trailing_hyphens():
    assert _slug("---hello---") == "hello"


def test_slug_truncates_to_80_chars():
    long_input = "a" * 200
    result = _slug(long_input)
    assert len(result) == 80


def test_slug_returns_cleanup_for_empty_input():
    assert _slug("") == "cleanup"
    assert _slug(None) == "cleanup"


def test_slug_returns_cleanup_for_only_special_chars():
    assert _slug("---") == "cleanup"
    assert _slug("!@#$%^&*()") == "cleanup"


def test_slug_handles_mixed_case_and_spaces():
    assert _slug("Add Lightweight UNIT TESTS") == "add-lightweight-unit-tests"


# ---------------------------------------------------------------------------
# _cleanup_task_id
# ---------------------------------------------------------------------------


def test_cleanup_task_id_has_expected_prefix():
    result = _cleanup_task_id("some theme")
    assert result.startswith("proactive-codie:")


def test_cleanup_task_id_suffix_is_12_hex_chars():
    result = _cleanup_task_id("some theme")
    suffix = result.split(":", 1)[1]
    assert len(suffix) == 12
    # Must be valid hex
    int(suffix, 16)


def test_cleanup_task_id_deterministic_for_same_theme():
    theme = "add type hints to untyped public function signatures"
    assert _cleanup_task_id(theme) == _cleanup_task_id(theme)


def test_cleanup_task_id_different_for_different_themes():
    id_a = _cleanup_task_id("theme alpha")
    id_b = _cleanup_task_id("theme beta")
    assert id_a != id_b


# ---------------------------------------------------------------------------
# _cleanup_task_description
# ---------------------------------------------------------------------------


def test_cleanup_task_description_contains_theme():
    desc = _cleanup_task_description(chosen_theme="magic string extraction")
    assert "magic string extraction" in desc


def test_cleanup_task_description_contains_instructions_section():
    desc = _cleanup_task_description(chosen_theme="some theme")
    assert "Instructions:" in desc


def test_cleanup_task_description_contains_note_when_provided():
    desc = _cleanup_task_description(
        chosen_theme="some theme", note="Focus on helpers only."
    )
    assert "Focus on helpers only." in desc
    assert "Additional operator note:" in desc


def test_cleanup_task_description_omits_note_section_when_empty():
    desc = _cleanup_task_description(chosen_theme="some theme", note="")
    assert "Additional operator note:" not in desc


def test_cleanup_task_description_contains_preference_context_when_provided():
    desc = _cleanup_task_description(
        chosen_theme="some theme", preference_context="User prefers small PRs."
    )
    assert "Preference context:" in desc
    assert "User prefers small PRs." in desc


def test_cleanup_task_description_omits_preference_context_when_empty():
    desc = _cleanup_task_description(
        chosen_theme="some theme", preference_context=""
    )
    assert "Preference context:" not in desc


# ---------------------------------------------------------------------------
# _pick_daily_theme
# ---------------------------------------------------------------------------


def test_pick_daily_theme_returns_known_theme():
    theme = _pick_daily_theme()
    assert theme in DEFAULT_CLEANUP_THEMES


def test_pick_daily_theme_returns_string():
    theme = _pick_daily_theme()
    assert isinstance(theme, str)
    assert len(theme) > 0


def test_pick_daily_theme_matches_day_of_year_rotation():
    """Verify the rotation logic: day_of_year % len(themes) selects the theme."""
    day_of_year = datetime.now(timezone.utc).timetuple().tm_yday
    expected = DEFAULT_CLEANUP_THEMES[day_of_year % len(DEFAULT_CLEANUP_THEMES)]
    assert _pick_daily_theme() == expected

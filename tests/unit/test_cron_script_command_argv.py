"""Regression coverage for `!script` cron command parsing.

The original heavy + lightweight cron dispatchers stripped only the
`!script ` prefix and then passed the whole remainder to `python -m`
as a single module name. That broke any registration that needed CLI
args (e.g. `briefings_agent --mode=evening`) — Python errored with
`No module named 'universal_agent.scripts.briefings_agent --mode=evening'`
and the evening briefing cron failed every night since PR #465.

These tests pin the new behaviour: the helper splits on shell tokens,
normalises `path/to/file.py` shorthand, and returns argv suitable for
`python -m`.
"""
from __future__ import annotations

import pytest

from universal_agent.cron_service import _parse_script_command_argv


def test_module_only_command_returns_single_token():
    assert _parse_script_command_argv(
        "!script universal_agent.scripts.briefings_agent"
    ) == ["universal_agent.scripts.briefings_agent"]


def test_command_with_long_option_keeps_args():
    assert _parse_script_command_argv(
        "!script universal_agent.scripts.briefings_agent --mode=evening"
    ) == ["universal_agent.scripts.briefings_agent", "--mode=evening"]


def test_command_with_space_separated_args_splits_correctly():
    assert _parse_script_command_argv(
        "!script universal_agent.scripts.briefings_agent --mode evening --dry-run"
    ) == [
        "universal_agent.scripts.briefings_agent",
        "--mode",
        "evening",
        "--dry-run",
    ]


def test_path_shorthand_is_normalised_to_dotted_module():
    assert _parse_script_command_argv(
        "!script src/universal_agent/scripts/briefings_agent.py --mode=evening"
    ) == [
        "src.universal_agent.scripts.briefings_agent",
        "--mode=evening",
    ]


def test_quoted_args_preserve_spaces():
    assert _parse_script_command_argv(
        '!script some.module --message "hello world"'
    ) == ["some.module", "--message", "hello world"]


def test_leading_and_trailing_whitespace_tolerated():
    assert _parse_script_command_argv(
        "   !script some.module --flag   "
    ) == ["some.module", "--flag"]


def test_missing_prefix_raises():
    with pytest.raises(ValueError):
        _parse_script_command_argv("python -m some.module")


def test_empty_body_raises():
    with pytest.raises(ValueError):
        _parse_script_command_argv("!script   ")

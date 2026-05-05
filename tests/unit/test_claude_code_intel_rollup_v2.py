"""Tests for v2 rollup configuration changes (PR 4).

Verifies that:
- ROLLING_WINDOW_DAYS defaults to 28 (was 14 in v1).
- MAX_ACTION_CONTEXTS defaults are wide enough to not silently truncate.
- Both are env-configurable via UA_CLAUDE_CODE_INTEL_BRIEF_*.
- Invalid/negative env values fall back to defaults.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def fresh_rollup(monkeypatch):
    def _reload(env: dict[str, str] | None = None):
        for key, value in (env or {}).items():
            monkeypatch.setenv(key, value)
        from universal_agent.services import claude_code_intel_rollup

        importlib.reload(claude_code_intel_rollup)
        return claude_code_intel_rollup

    return _reload


def test_v2_default_window_is_28_days(fresh_rollup):
    mod = fresh_rollup()
    assert mod.ROLLING_WINDOW_DAYS == 28, "v2 design lifted the v1 14-day window to 28"


def test_v2_default_max_contexts_is_well_above_v1_18(fresh_rollup):
    mod = fresh_rollup()
    # v1 was 18 — v2 design says "no 18-item cap." We keep some upper bound
    # for safety but it must be materially larger so backfills aren't silently
    # truncated.
    assert mod.MAX_ACTION_CONTEXTS >= 100


def test_window_reads_env(fresh_rollup):
    mod = fresh_rollup({"UA_CLAUDE_CODE_INTEL_BRIEF_WINDOW_DAYS": "60"})
    assert mod.ROLLING_WINDOW_DAYS == 60


def test_max_contexts_reads_env(fresh_rollup):
    mod = fresh_rollup({"UA_CLAUDE_CODE_INTEL_BRIEF_MAX_CONTEXTS": "1000"})
    assert mod.MAX_ACTION_CONTEXTS == 1000


def test_invalid_window_env_falls_back_to_default(fresh_rollup):
    mod = fresh_rollup({"UA_CLAUDE_CODE_INTEL_BRIEF_WINDOW_DAYS": "garbage"})
    assert mod.ROLLING_WINDOW_DAYS == 28


def test_negative_max_contexts_falls_back_to_default(fresh_rollup):
    mod = fresh_rollup({"UA_CLAUDE_CODE_INTEL_BRIEF_MAX_CONTEXTS": "-5"})
    assert mod.MAX_ACTION_CONTEXTS == 500


def test_min_synthesis_tier_unchanged(fresh_rollup):
    """Tier 2 floor is a semantic filter, not a v1 quirk; must stay at 2."""
    mod = fresh_rollup()
    assert mod.MIN_SYNTHESIS_TIER == 2

"""Tests for the override flags on csi_demo_triage_apply_policy CLI.

The flags let an operator do one-time aggressive sweeps without editing
code. The default policy stays conservative; overrides produce a new
StaleTierPolicy with an audit-distinct decided_by stamp.
"""

from __future__ import annotations

import argparse

import pytest

from universal_agent.scripts import csi_demo_triage_apply_policy as cli
from universal_agent.services.csi_demo_triage_policy import (
    DEFAULT_POLICIES,
    StaleTierPolicy,
)


def _ns(**kwargs) -> argparse.Namespace:
    defaults = {
        "apply": False,
        "json": False,
        "require_env_opt_in": False,
        "age_days": None,
        "max_score": None,
        "tier": None,
        "policy_name": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_no_overrides_returns_none():
    """Without overrides the default policy stays in effect."""
    assert cli._custom_policy_from_args(_ns()) is None


def test_age_override_builds_custom_policy():
    policy = cli._custom_policy_from_args(_ns(age_days=7))
    assert policy is not None
    assert policy.max_age_days == 7
    assert policy.tier == DEFAULT_POLICIES[0].tier  # tier defaults to base
    assert policy.max_ranking_score == DEFAULT_POLICIES[0].max_ranking_score
    assert policy.decided_by.startswith("auto-policy:stale-tier-")


def test_score_override_builds_custom_policy():
    policy = cli._custom_policy_from_args(_ns(max_score=3.0))
    assert policy is not None
    assert policy.max_ranking_score == 3.0
    assert policy.max_age_days == DEFAULT_POLICIES[0].max_age_days


def test_tier_override_to_4_is_explicit_opt_in():
    """tier=4 requires explicit operator override — the default never targets it."""
    policy = cli._custom_policy_from_args(_ns(tier=4))
    assert policy is not None
    assert policy.tier == 4


def test_custom_policy_name_propagates_to_decided_by():
    policy = cli._custom_policy_from_args(_ns(age_days=5, policy_name="manual-cleanup"))
    assert policy is not None
    assert policy.name == "manual-cleanup"
    assert policy.decided_by == "auto-policy:manual-cleanup"


def test_default_policy_name_includes_tier_and_override_suffix():
    """When no explicit name given, the name distinguishes overrides from default."""
    policy = cli._custom_policy_from_args(_ns(age_days=5))
    assert policy is not None
    assert policy.name == "stale-tier-3-override"
    assert policy.decided_by == "auto-policy:stale-tier-3-override"

    policy_t4 = cli._custom_policy_from_args(_ns(tier=4, age_days=30))
    assert policy_t4 is not None
    assert policy_t4.name == "stale-tier-4-override"


def test_all_three_overrides_build_correctly():
    policy = cli._custom_policy_from_args(
        _ns(age_days=7, max_score=3.0, tier=4, policy_name="emergency-purge")
    )
    assert policy is not None
    assert policy.max_age_days == 7
    assert policy.max_ranking_score == 3.0
    assert policy.tier == 4
    assert policy.name == "emergency-purge"


def test_max_score_can_be_zero():
    """max_score=0.0 must not collapse to falsy 'no override' check."""
    policy = cli._custom_policy_from_args(_ns(max_score=0.0))
    assert policy is not None
    assert policy.max_ranking_score == 0.0


def test_age_days_can_be_zero():
    """age_days=0 means 'older than zero days' — a no-op upper bound. Still a valid override."""
    policy = cli._custom_policy_from_args(_ns(age_days=0))
    assert policy is not None
    assert policy.max_age_days == 0

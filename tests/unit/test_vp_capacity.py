"""Unit tests for the VP slot-accounting helpers in ``services/vp_capacity.py``.

These are the shared pure helpers that ``todo_dispatch_service``,
``atlas_direct_dispatch``, and ``priority_dispatcher`` all import so the
legacy ToDo lane, the Atlas-direct lane, and the priority dispatcher share
ONE counting implementation. Until now they had no direct test coverage.

The classification is by EXACT set membership on ``agent_id`` (lowercased +
stripped) — the module docstring calls out that this is deliberately *not*
substring matching, so that ids like ``encoder`` or ``atlassian`` are not
misclassified. The substring-safety cases below lock that invariant down.
"""

from __future__ import annotations

import pytest

from universal_agent.services.agent_router import (
    AGENT_CODER,
    AGENT_GENERAL,
    AGENT_SIMONE,
)
from universal_agent.services.vp_capacity import (
    _available_agents_for_llm_routing,
    _vp_active_counts,
)

# ---------------------------------------------------------------------------
# _vp_active_counts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "agent_id",
    ["vp.coder.primary", "codie", "coder"],
)
def test_active_counts_recognises_coder_aliases(agent_id):
    assert _vp_active_counts([{"agent_id": agent_id}]) == (1, 0)


@pytest.mark.parametrize(
    "agent_id",
    ["vp.general.primary", "atlas", "vp.general.secondary", "homer"],
)
def test_active_counts_recognises_general_aliases(agent_id):
    assert _vp_active_counts([{"agent_id": agent_id}]) == (0, 1)


def test_active_counts_splits_a_mixed_pool():
    assignments = [
        {"agent_id": "vp.coder.primary"},
        {"agent_id": "codie"},
        {"agent_id": "atlas"},
        {"agent_id": "homer"},
        {"agent_id": "vp.general.primary"},
    ]
    assert _vp_active_counts(assignments) == (2, 3)


def test_active_counts_is_case_insensitive_and_strips_whitespace():
    assignments = [
        {"agent_id": "  VP.Coder.Primary  "},
        {"agent_id": "ATLAS"},
        {"agent_id": "\tCoder\n"},
    ]
    assert _vp_active_counts(assignments) == (2, 1)


@pytest.mark.parametrize(
    "agent_id",
    [
        "encoder",  # contains "coder" as a substring
        "atlassian",  # contains "atlas" as a substring
        "codie-x",  # alias plus a suffix
        "my-coder-bot",
        "super-atlas-2",
    ],
)
def test_active_counts_does_not_substring_match(agent_id):
    """Exact membership only: substrings of known aliases must NOT count.

    This is the regression the module docstring calls out — switching to
    substring matching would silently misclassify these ids.
    """
    assert _vp_active_counts([{"agent_id": agent_id}]) == (0, 0)


def test_active_counts_ignores_unknown_agents():
    assignments = [
        {"agent_id": "simone"},
        {"agent_id": "some-future-vp"},
        {"agent_id": ""},
    ]
    assert _vp_active_counts(assignments) == (0, 0)


def test_active_counts_handles_missing_malformed_and_empty_input():
    # missing key
    assert _vp_active_counts([{}]) == (0, 0)
    # None value for agent_id
    assert _vp_active_counts([{"agent_id": None}]) == (0, 0)
    # None list
    assert _vp_active_counts(None) == (0, 0)
    # empty list
    assert _vp_active_counts([]) == (0, 0)


# ---------------------------------------------------------------------------
# _available_agents_for_llm_routing
# ---------------------------------------------------------------------------


def _set_caps(monkeypatch, coder_cap: int, general_cap: int) -> None:
    """Pin the VP caps so availability tests are hermetic regardless of the
    ambient environment (the VPS may export these vars)."""
    monkeypatch.setenv("UA_MAX_CONCURRENT_VP_CODER", str(coder_cap))
    monkeypatch.setenv("UA_MAX_CONCURRENT_VP_GENERAL", str(general_cap))


def test_available_agents_default_caps_everything_free(monkeypatch):
    # Default caps (coder=1, general=2); no active assignments.
    _set_caps(monkeypatch, coder_cap=1, general_cap=2)
    available = _available_agents_for_llm_routing([])
    assert AGENT_SIMONE in available
    assert AGENT_CODER in available
    assert AGENT_GENERAL in available


def test_simone_is_always_available_even_under_saturation(monkeypatch):
    # Saturate both pools at default caps.
    _set_caps(monkeypatch, coder_cap=1, general_cap=2)
    assignments = [
        {"agent_id": "vp.coder.primary"},  # coder cap 1
        {"agent_id": "atlas"},
        {"agent_id": "homer"},  # general cap 2
    ]
    available = _available_agents_for_llm_routing(assignments)
    assert AGENT_SIMONE in available
    assert AGENT_CODER not in available
    assert AGENT_GENERAL not in available


def test_coder_unavailable_when_at_cap_but_general_free(monkeypatch):
    _set_caps(monkeypatch, coder_cap=1, general_cap=2)
    assignments = [{"agent_id": "vp.coder.primary"}]  # coder cap 1 reached
    available = _available_agents_for_llm_routing(assignments)
    assert AGENT_CODER not in available
    assert AGENT_GENERAL in available
    assert AGENT_SIMONE in available


def test_general_unavailable_when_pool_at_cap(monkeypatch):
    # Two general agents (atlas + homer) hit the default general cap of 2.
    _set_caps(monkeypatch, coder_cap=1, general_cap=2)
    assignments = [
        {"agent_id": "atlas"},
        {"agent_id": "homer"},
    ]
    available = _available_agents_for_llm_routing(assignments)
    assert AGENT_GENERAL not in available
    assert AGENT_CODER in available  # coder pool untouched


def test_env_override_raises_caps(monkeypatch):
    _set_caps(monkeypatch, coder_cap=2, general_cap=5)
    assignments = [
        {"agent_id": "vp.coder.primary"},
        {"agent_id": "atlas"},
    ]
    available = _available_agents_for_llm_routing(assignments)
    assert AGENT_CODER in available  # 1 active < cap 2
    assert AGENT_GENERAL in available  # 1 active < cap 5
    assert AGENT_SIMONE in available


def test_available_agents_returns_frozenset(monkeypatch):
    _set_caps(monkeypatch, coder_cap=1, general_cap=2)
    assert isinstance(_available_agents_for_llm_routing([]), frozenset)

"""Unit tests for the deterministic classify_task routing (M2 / D3).

classify_task is pure (no DB, no LLM) so these tests just build plain task
dicts and assert the DispatchDecision. The prefer-ATLAS gate is toggled via
UA_DISPATCHER_PREFER_ATLAS.
"""

import pytest

from universal_agent.services import priority_dispatcher as pd
from universal_agent.services.agent_router import (
    AGENT_CODER,
    AGENT_GENERAL,
    AGENT_SIMONE,
)


@pytest.fixture(autouse=True)
def _clear_prefer_atlas(monkeypatch):
    # Default OFF unless a test opts in.
    monkeypatch.delenv("UA_DISPATCHER_PREFER_ATLAS", raising=False)


def _task(**kw):
    kw.setdefault("task_id", "t")
    return kw


# --- P0.1 explicit target_agent -------------------------------------------------


def test_explicit_target_coder():
    d = pd.classify_task(
        _task(metadata={"workflow_manifest": {"target_agent": AGENT_CODER}})
    )
    assert d.agent_id == AGENT_CODER
    assert d.priority == pd.P0
    assert d.is_vp is True
    assert d.rule == "explicit_target_agent"


def test_explicit_target_general():
    d = pd.classify_task(_task(metadata={"target_agent": AGENT_GENERAL}))
    assert d.agent_id == AGENT_GENERAL
    assert d.priority == pd.P0
    assert d.is_vp is True


def test_explicit_target_simone():
    d = pd.classify_task(
        _task(metadata={"workflow_manifest": {"target_agent": AGENT_SIMONE}})
    )
    assert d.agent_id == AGENT_SIMONE
    assert d.priority == pd.P0
    assert d.is_vp is False
    assert d.rule == "explicit_target_simone"


# --- P0.2 chat -----------------------------------------------------------------


@pytest.mark.parametrize("source_kind", ["chat_panel", "simone_chat", "CHAT_PANEL"])
def test_chat_to_simone(source_kind):
    d = pd.classify_task(_task(source_kind=source_kind))
    assert d.agent_id == AGENT_SIMONE
    assert d.priority == pd.P0
    assert d.is_vp is False
    assert d.rule == "chat"


def test_interactive_chat_is_not_chat_source_kind():
    # interactive_chat is a delivery_mode/run_kind, never a source_kind: it
    # must NOT classify as chat (it would fall to the ambiguous tail instead).
    d = pd.classify_task(_task(source_kind="interactive_chat"))
    assert d.rule == "ambiguous_tail_pending"


# --- P0.3 coding ---------------------------------------------------------------


@pytest.mark.parametrize(
    "source_kind", ["tutorial_build", "cody_demo_task", "cody_scaffold_request"]
)
def test_coding_source_kind_to_coder(source_kind):
    d = pd.classify_task(_task(source_kind=source_kind))
    assert d.agent_id == AGENT_CODER
    assert d.priority == pd.P0
    assert d.is_vp is True
    assert d.rule == "coding_to_codie"


def test_coding_workflow_kind_to_coder():
    d = pd.classify_task(
        _task(
            source_kind="internal",
            metadata={"workflow_manifest": {"workflow_kind": "code_change"}},
        )
    )
    assert d.agent_id == AGENT_CODER
    assert d.priority == pd.P0
    assert d.is_vp is True


def test_dead_coding_source_kinds_are_not_coding():
    # tutorial_build_task and mission_control_card_dispatch are NOT written
    # source_kinds (the handoff set included dead members); they must not be
    # treated as coding by source_kind alone.
    for sk in ("tutorial_build_task", "mission_control_card_dispatch"):
        d = pd.classify_task(_task(source_kind=sk))
        assert d.rule == "ambiguous_tail_pending", sk


# --- P2.1 preferred_vp ---------------------------------------------------------


def test_preferred_vp_general_falls_back_to_simone_when_prefer_atlas_off():
    d = pd.classify_task(_task(metadata={"preferred_vp": AGENT_GENERAL}))
    assert d.agent_id == AGENT_SIMONE
    assert d.priority == pd.P2
    assert d.is_vp is False
    assert d.rule == "preferred_vp_general_simone_fallback"


def test_preferred_vp_general_routes_to_atlas_when_prefer_atlas_on(monkeypatch):
    monkeypatch.setenv("UA_DISPATCHER_PREFER_ATLAS", "1")
    d = pd.classify_task(_task(metadata={"preferred_vp": AGENT_GENERAL}))
    assert d.agent_id == AGENT_GENERAL
    assert d.priority == pd.P2
    assert d.is_vp is True
    assert d.rule == "preferred_vp_general"


def test_preferred_vp_coder():
    d = pd.classify_task(_task(metadata={"preferred_vp": AGENT_CODER}))
    assert d.agent_id == AGENT_CODER
    assert d.priority == pd.P2
    assert d.is_vp is True
    assert d.rule == "preferred_vp_coder"


# --- P2.2 ambiguous tail -------------------------------------------------------


def test_untagged_is_ambiguous_tail():
    d = pd.classify_task(_task(source_kind="internal", title="do a thing"))
    assert d.rule == "ambiguous_tail_pending"
    assert d.agent_id == AGENT_SIMONE  # placeholder until the classifier resolves it


# --- precedence / ordering -----------------------------------------------------


def test_explicit_target_beats_coding_signal():
    # An explicit Simone target wins even if the source_kind looks like coding.
    d = pd.classify_task(
        _task(source_kind="tutorial_build", metadata={"target_agent": AGENT_SIMONE})
    )
    assert d.agent_id == AGENT_SIMONE
    assert d.rule == "explicit_target_simone"


def test_priority_sort_p0_before_p2():
    items = [
        _task(task_id="tail", source_kind="internal"),  # P2
        _task(task_id="chat", source_kind="chat_panel"),  # P0
    ]
    decisions = [pd.classify_task(it) for it in items]
    ordered = sorted(decisions, key=lambda x: x.priority)
    assert ordered[0].task_id == "chat"
    assert ordered[-1].task_id == "tail"


# --- drift guard ---------------------------------------------------------------


def test_coder_lane_source_kinds_match_canonical_enum():
    # The dispatcher's coder-lane set must stay identical to the enforced enum
    # in vp_orchestration (the except-clause fallback is only for minimal envs).
    from universal_agent.tools.vp_orchestration import _CODER_LANE_SOURCE_KINDS

    assert pd.CODER_LANE_SOURCE_KINDS == _CODER_LANE_SOURCE_KINDS


# --- M5 §2a: public prefer-ATLAS accessor (for the ZAI Control read-out) --------


def test_prefer_atlas_enabled_default_off():
    # Stage A: default OFF (the autouse fixture clears the env var).
    assert pd.prefer_atlas_enabled() is False


def test_prefer_atlas_enabled_reflects_flag(monkeypatch):
    monkeypatch.setenv("UA_DISPATCHER_PREFER_ATLAS", "1")
    assert pd.prefer_atlas_enabled() is True
    # Mirrors the private dispatch-path reader exactly (single source of truth).
    assert pd.prefer_atlas_enabled() == pd._prefer_atlas_for_general()

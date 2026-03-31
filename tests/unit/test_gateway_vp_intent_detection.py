from universal_agent.gateway import _allow_prompt_inferred_vp_routing, _infer_explicit_vp_target


def test_infer_explicit_general_vp_from_vp_general_word_order():
    vp_id, mission_type = _infer_explicit_vp_target(
        "Use the VP general agent to write a story and email it."
    )
    assert vp_id == "vp.general.primary"
    assert mission_type == "general_task"


def test_infer_explicit_coder_vp_from_vp_coder_word_order():
    vp_id, mission_type = _infer_explicit_vp_target(
        "Use the VP coder agent to refactor this module."
    )
    assert vp_id is not None
    assert mission_type == "coding_task"


def test_does_not_infer_deprecated_dp_alias():
    vp_id, mission_type = _infer_explicit_vp_target(
        "Use the general DP to write a story."
    )
    assert vp_id is None
    assert mission_type is None


def test_prompt_inferred_vp_routing_is_blocked_for_todo_dispatcher():
    assert _allow_prompt_inferred_vp_routing(
        request_source="todo_dispatcher",
        request_run_kind="todo_execution",
    ) is False


def test_prompt_inferred_vp_routing_is_allowed_for_interactive_user_prompt():
    assert _allow_prompt_inferred_vp_routing(
        request_source="user",
        request_run_kind="user",
    ) is True

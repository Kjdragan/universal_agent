from universal_agent.gateway import _infer_explicit_vp_target


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

from universal_agent.durable.normalize import deterministic_task_key


def test_deterministic_task_key_stable():
    input_a = {
        "subagent_type": "report-creation-expert",
        "prompt": "Summarize.",
        "description": "Make a report",
        "extras": {"b": 2, "a": 1},
    }
    input_b = {
        "description": "Make a report",
        "extras": {"a": 1, "b": 2},
        "prompt": "Summarize.",
        "subagent_type": "report-creation-expert",
    }
    assert deterministic_task_key(input_a) == deterministic_task_key(input_b)


def test_deterministic_task_key_ignores_existing_task_key():
    base = {"subagent_type": "image-expert", "prompt": "Draw"}
    with_key = {"task_key": "task:override", **base}
    assert deterministic_task_key(base) == deterministic_task_key(with_key)

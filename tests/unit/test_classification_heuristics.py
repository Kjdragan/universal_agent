from universal_agent.main import _is_context_only_intent


def test_context_only_intent_filename():
    assert _is_context_only_intent("What is the filename?")
    assert _is_context_only_intent("What's the file name?")
    assert not _is_context_only_intent("Summarize the report.")

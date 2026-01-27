from universal_agent.memory.context_manager import ContextManager
from universal_agent.utils.message_history import MessageHistory


def test_prune_history_truncates_dict_tool_results():
    manager = ContextManager()
    long_content = "x" * 2000
    history = [
        {"role": "user", "content": "start"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "search", "id": "t1", "input": {}}],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": long_content,
                }
            ],
        },
        {"role": "assistant", "content": "final"},
        {"role": "user", "content": "tail"},
        {"role": "assistant", "content": "tail2"},
        {"role": "user", "content": "tail3"},
        {"role": "assistant", "content": "tail4"},
        {"role": "user", "content": "tail5"},
    ]

    pruned, stats = manager.prune_history(history)

    # Tool result should be truncated in-place
    pruned_result_blocks = pruned[2]["content"]
    assert isinstance(pruned_result_blocks, list)
    assert len(pruned_result_blocks[0]["content"]) < len(long_content)
    assert stats.tools_summarized >= 1


def test_message_history_get_messages_returns_copy():
    history = MessageHistory(system_prompt_tokens=0)
    history.add_message("user", "hello")
    history.add_message("assistant", "world")

    messages = history.get_messages()
    assert len(messages) == 2

    messages.append({"role": "user", "content": "extra"})
    # Original history should remain unchanged
    assert len(history.get_messages()) == 2

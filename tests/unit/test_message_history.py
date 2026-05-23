"""Unit tests for utils/message_history.py — MessageHistory class.

The MessageHistory class manages conversation context with per-message
token tracking and pair-based truncation. These tests cover all public
methods and the private pair-removal helper, exercising both the
happy path and edge cases without any network or DB dependencies.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from universal_agent.utils.message_history import (
    TRUNCATION_MESSAGE,
    TRUNCATION_NOTICE_TOKENS,
    MessageHistory,
)

# ── helpers ───────────────────────────────────────────────────────────────────

_SMALL_THRESHOLD = 1000
_PATCH_PATH = "universal_agent.utils.message_history.TRUNCATION_THRESHOLD"


class _Usage:
    """Minimal stand-in for the Anthropic SDK usage object."""

    def __init__(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.cache_read_input_tokens = cache_read_input_tokens


def _history_over_threshold(threshold: int = _SMALL_THRESHOLD) -> MessageHistory:
    """Return a four-message history whose total_tokens exceeds *threshold*."""
    h = MessageHistory(system_prompt_tokens=0)
    h.messages = [
        {"role": "user", "content": "msg1"},
        {"role": "assistant", "content": "resp1"},
        {"role": "user", "content": "msg2"},
        {"role": "assistant", "content": "resp2"},
    ]
    # One token tuple per message (input, output)
    h.message_tokens = [
        (300, 200),  # msg1
        (0, 200),    # resp1
        (300, 200),  # msg2
        (0, 200),    # resp2
    ]
    h.total_tokens = threshold + 100  # deliberately over the limit
    return h


# ── __init__ ──────────────────────────────────────────────────────────────────


class TestInit:
    def test_starts_empty(self):
        h = MessageHistory(system_prompt_tokens=0)
        assert h.messages == []
        assert h.message_tokens == []
        assert h._truncation_count == 0

    def test_total_tokens_seeded_by_system_prompt(self):
        h = MessageHistory(system_prompt_tokens=5000)
        assert h.total_tokens == 5000
        assert h._system_prompt_tokens == 5000

    def test_default_system_prompt_is_2000(self):
        h = MessageHistory()
        assert h.total_tokens == 2000


# ── add_message ───────────────────────────────────────────────────────────────


class TestAddMessage:
    def test_appends_message(self):
        h = MessageHistory(system_prompt_tokens=0)
        h.add_message("user", "hello")
        assert h.messages == [{"role": "user", "content": "hello"}]

    def test_tracks_tokens_from_usage(self):
        h = MessageHistory(system_prompt_tokens=0)
        h.add_message("user", "hi", usage=_Usage(input_tokens=100, output_tokens=50))
        # current_input = max(0, 100 − 0) = 100
        assert h.message_tokens[0] == (100, 50)
        assert h.total_tokens == 150

    def test_second_message_subtracts_prior_context(self):
        h = MessageHistory(system_prompt_tokens=0)
        h.add_message("user", "hi", usage=_Usage(input_tokens=100, output_tokens=50))
        # After first message total = 150
        h.add_message("assistant", "ok", usage=_Usage(input_tokens=160, output_tokens=30))
        # current_input = max(0, 160 − 150) = 10
        assert h.message_tokens[1] == (10, 30)
        assert h.total_tokens == 190

    def test_cache_tokens_count_toward_input(self):
        h = MessageHistory(system_prompt_tokens=0)
        h.add_message(
            "user",
            "hi",
            usage=_Usage(input_tokens=100, cache_creation_input_tokens=20, cache_read_input_tokens=30),
        )
        # total_input = 100 + 20 + 30 = 150; current_input = 150 − 0 = 150
        assert h.message_tokens[0][0] == 150

    def test_estimates_tokens_without_usage(self):
        h = MessageHistory(system_prompt_tokens=0)
        content = "a" * 40  # 40 chars → 10 tokens (floor div 4)
        h.add_message("user", content)
        assert h.message_tokens[0] == (10, 0)
        assert h.total_tokens == 10

    def test_empty_content_estimates_zero(self):
        h = MessageHistory(system_prompt_tokens=0)
        h.add_message("user", "")
        assert h.message_tokens[0] == (0, 0)

    def test_current_input_never_negative(self):
        # If usage reports a lower total than prior total, delta is clamped to 0.
        h = MessageHistory(system_prompt_tokens=10_000)
        h.add_message("user", "hi", usage=_Usage(input_tokens=50, output_tokens=10))
        # current_input = max(0, 50 − 10000) = 0
        assert h.message_tokens[0][0] == 0


# ── _remove_oldest_pair ───────────────────────────────────────────────────────


class TestRemoveOldestPair:
    def test_removes_first_two_messages(self):
        h = MessageHistory(system_prompt_tokens=0)
        h.messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]
        h.message_tokens = [(10, 5), (8, 3), (6, 0)]
        h.total_tokens = 32
        h._remove_oldest_pair()
        assert len(h.messages) == 1
        assert h.messages[0]["content"] == "u2"

    def test_subtracts_pair_tokens_from_total(self):
        h = MessageHistory(system_prompt_tokens=0)
        h.messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]
        h.message_tokens = [(10, 5), (8, 3)]
        h.total_tokens = 26
        h._remove_oldest_pair()
        # 26 − (10+5+8+3) = 0
        assert h.total_tokens == 0

    def test_no_op_when_fewer_than_two_messages(self):
        h = MessageHistory(system_prompt_tokens=0)
        h.messages = [{"role": "user", "content": "lone"}]
        h.message_tokens = [(10, 0)]
        h.total_tokens = 10
        h._remove_oldest_pair()
        assert len(h.messages) == 1
        assert h.total_tokens == 10

    def test_no_op_on_empty_history(self):
        h = MessageHistory(system_prompt_tokens=0)
        h._remove_oldest_pair()
        assert h.messages == []
        assert h.total_tokens == 0


# ── truncate ──────────────────────────────────────────────────────────────────


class TestTruncate:
    def test_returns_false_when_under_threshold(self):
        with patch(_PATCH_PATH, _SMALL_THRESHOLD):
            h = MessageHistory(system_prompt_tokens=0)
            h.add_message("user", "hello")
            assert h.truncate() is False

    def test_returns_false_on_empty_history(self):
        with patch(_PATCH_PATH, _SMALL_THRESHOLD):
            h = MessageHistory(system_prompt_tokens=0)
            assert h.truncate() is False

    def test_returns_true_when_truncation_occurs(self):
        with patch(_PATCH_PATH, _SMALL_THRESHOLD):
            h = _history_over_threshold()
            assert h.truncate() is True

    def test_total_tokens_decreases(self):
        with patch(_PATCH_PATH, _SMALL_THRESHOLD):
            h = _history_over_threshold()
            before = h.total_tokens
            h.truncate()
            assert h.total_tokens < before

    def test_first_message_replaced_with_notice(self):
        with patch(_PATCH_PATH, _SMALL_THRESHOLD):
            h = _history_over_threshold()
            h.truncate()
            assert h.messages[0] == TRUNCATION_MESSAGE

    def test_truncation_count_incremented(self):
        with patch(_PATCH_PATH, _SMALL_THRESHOLD):
            h = _history_over_threshold()
            h.truncate()
            assert h._truncation_count == 1

    def test_first_token_entry_uses_notice_token_count(self):
        with patch(_PATCH_PATH, _SMALL_THRESHOLD):
            h = _history_over_threshold()
            h.truncate()
            assert h.message_tokens[0][0] == TRUNCATION_NOTICE_TOKENS

    def test_multiple_truncations_increment_count(self):
        with patch(_PATCH_PATH, _SMALL_THRESHOLD):
            h = _history_over_threshold()
            h.truncate()
            # Push over threshold again for a second truncation
            h.total_tokens = _SMALL_THRESHOLD + 100
            h.messages += [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]
            h.message_tokens += [(300, 200), (0, 100)]
            h.truncate()
            assert h._truncation_count == 2

    def test_messages_remain_after_truncation(self):
        with patch(_PATCH_PATH, _SMALL_THRESHOLD):
            h = _history_over_threshold()
            h.truncate()
            # At least the truncation notice message should remain
            assert len(h.messages) >= 1


# ── should_handoff ────────────────────────────────────────────────────────────


class TestShouldHandoff:
    def test_false_below_threshold(self):
        with patch(_PATCH_PATH, _SMALL_THRESHOLD):
            h = MessageHistory(system_prompt_tokens=0)
            h.total_tokens = _SMALL_THRESHOLD - 1
            assert h.should_handoff() is False

    def test_true_at_threshold(self):
        with patch(_PATCH_PATH, _SMALL_THRESHOLD):
            h = MessageHistory(system_prompt_tokens=0)
            h.total_tokens = _SMALL_THRESHOLD
            assert h.should_handoff() is True

    def test_true_above_threshold(self):
        with patch(_PATCH_PATH, _SMALL_THRESHOLD):
            h = MessageHistory(system_prompt_tokens=0)
            h.total_tokens = _SMALL_THRESHOLD + 5000
            assert h.should_handoff() is True


# ── get_stats ─────────────────────────────────────────────────────────────────


class TestGetStats:
    def test_required_keys_present(self):
        h = MessageHistory(system_prompt_tokens=0)
        stats = h.get_stats()
        for key in ("total_tokens", "message_count", "truncation_count", "threshold",
                    "remaining_capacity", "utilization_pct"):
            assert key in stats

    def test_message_count_reflects_history(self):
        h = MessageHistory(system_prompt_tokens=0)
        h.add_message("user", "hi")
        h.add_message("assistant", "hello")
        assert h.get_stats()["message_count"] == 2

    def test_truncation_count_zero_initially(self):
        h = MessageHistory(system_prompt_tokens=0)
        assert h.get_stats()["truncation_count"] == 0

    def test_remaining_capacity_non_negative(self):
        with patch(_PATCH_PATH, _SMALL_THRESHOLD):
            h = MessageHistory(system_prompt_tokens=0)
            h.total_tokens = _SMALL_THRESHOLD + 500  # over threshold
            assert h.get_stats()["remaining_capacity"] == 0

    def test_utilization_pct_is_float(self):
        h = MessageHistory(system_prompt_tokens=0)
        pct = h.get_stats()["utilization_pct"]
        assert isinstance(pct, float)


# ── format_for_api / get_messages ────────────────────────────────────────────


class TestMessageAccessors:
    def test_format_for_api_returns_copy(self):
        h = MessageHistory(system_prompt_tokens=0)
        h.add_message("user", "hello")
        api = h.format_for_api()
        api.append({"role": "user", "content": "extra"})
        assert len(h.messages) == 1

    def test_format_for_api_matches_internal(self):
        h = MessageHistory(system_prompt_tokens=0)
        h.add_message("user", "hello")
        h.add_message("assistant", "world")
        assert h.format_for_api() == h.messages

    def test_get_messages_returns_copy(self):
        h = MessageHistory(system_prompt_tokens=0)
        h.add_message("user", "hello")
        msgs = h.get_messages()
        msgs.append({"role": "user", "content": "extra"})
        assert len(h.get_messages()) == 1

    def test_get_messages_empty_initially(self):
        h = MessageHistory(system_prompt_tokens=0)
        assert h.get_messages() == []


# ── reset ─────────────────────────────────────────────────────────────────────


class TestReset:
    def test_clears_messages_and_tokens(self):
        h = MessageHistory(system_prompt_tokens=500)
        h.add_message("user", "hi")
        h.reset()
        assert h.messages == []
        assert h.message_tokens == []

    def test_restores_total_to_system_prompt_tokens(self):
        h = MessageHistory(system_prompt_tokens=1234)
        h.add_message("user", "lots of text " * 100)
        h.reset()
        assert h.total_tokens == 1234

    def test_clears_truncation_count(self):
        with patch(_PATCH_PATH, _SMALL_THRESHOLD):
            h = _history_over_threshold()
            h.truncate()
            assert h._truncation_count == 1
            h.reset()
            assert h._truncation_count == 0

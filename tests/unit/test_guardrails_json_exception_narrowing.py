"""Focused regression tests for the best-effort JSON-parsing helpers in the
tool-input guardrails.

These helpers previously used a broad ``except Exception:`` around a
``json.loads()`` call whose input was already guaranteed to be a ``str``
(either via an ``isinstance(..., str)`` guard or ``str(...)`` coercion).
That broad catch was narrowed to ``json.JSONDecodeError`` -- the only
exception ``json.loads`` can raise for a ``str`` argument -- so the caught
set is unchanged.

Because the cleanup is mechanical (identical caught set), classic red-green
does not apply: the fallback contract below holds both before and after the
narrowing. These tests lock that contract so an over-narrow regression
(e.g. a typo'd exception type) would surface immediately, and they give
direct coverage to five helpers that were previously only exercised
indirectly.
"""
from __future__ import annotations

from universal_agent.guardrails.tool_schema import (
    _try_parse_json_list,
    _try_parse_json_object,
)
from universal_agent.mission_guardrails import (
    _extract_nested_tool_names,
    _extract_task_hub_actions,
    _parse_tool_result_payload,
)

# --- _extract_nested_tool_names -------------------------------------------


def test_extract_nested_tool_names_parses_valid_json_string():
    raw = '{"tools": [{"tool_slug": "foo"}, {"tool_slug": "bar"}]}'
    assert _extract_nested_tool_names(raw) == ["foo", "bar"]


def test_extract_nested_tool_names_returns_empty_for_malformed_json():
    # Malformed JSON must still be caught (json.JSONDecodeError) and fall back.
    assert _extract_nested_tool_names("{not valid json") == []


def test_extract_nested_tool_names_returns_empty_for_non_dict_json():
    # Valid JSON but not a dict -> no nested tools.
    assert _extract_nested_tool_names('[1, 2, 3]') == []


def test_extract_nested_tool_names_accepts_dict_directly():
    tool_input = {"tools": [{"tool_slug": "baz"}]}
    assert _extract_nested_tool_names(tool_input) == ["baz"]


# --- _extract_task_hub_actions --------------------------------------------


def test_extract_task_hub_actions_parses_action_from_json_string():
    raw = '{"action": "complete"}'
    assert _extract_task_hub_actions("task_hub_task_action", raw) == ["complete"]


def test_extract_task_hub_actions_returns_empty_for_malformed_json():
    assert _extract_task_hub_actions("task_hub_task_action", "{bad json") == []


def test_extract_task_hub_actions_returns_empty_when_tool_name_unrelated():
    # Early-return path: tool name does not mention task_hub_task_action.
    assert _extract_task_hub_actions("something_else", '{"action": "x"}') == []


# --- _parse_tool_result_payload -------------------------------------------


def test_parse_tool_result_payload_parses_dict_string():
    assert _parse_tool_result_payload('{"ok": true}') == {"ok": True}


def test_parse_tool_result_payload_returns_empty_for_malformed_json():
    # Malformed JSON must still be caught and fall back to {}.
    assert _parse_tool_result_payload("{{not json") == {}


def test_parse_tool_result_payload_returns_empty_for_non_dict_json():
    # Valid JSON scalar/list is not a dict payload -> {}.
    assert _parse_tool_result_payload('[1, 2]') == {}


def test_parse_tool_result_payload_preserves_error_prefix():
    out = _parse_tool_result_payload("error: something failed")
    assert out == {"ok": False, "error": "error: something failed"}


def test_parse_tool_result_payload_passes_dict_through():
    payload = {"already": "a dict"}
    assert _parse_tool_result_payload(payload) is payload


# --- _try_parse_json_list / _try_parse_json_object ------------------------


def test_try_parse_json_list_parses_list_string():
    assert _try_parse_json_list('[1, 2, 3]') == [1, 2, 3]


def test_try_parse_json_list_rejects_non_list_json():
    assert _try_parse_json_list('{"a": 1}') is None


def test_try_parse_json_list_returns_none_for_malformed_json():
    assert _try_parse_json_list("[1, 2,") is None


def test_try_parse_json_list_returns_none_for_non_string():
    assert _try_parse_json_list([1, 2, 3]) is None  # already a list, not a str
    assert _try_parse_json_list(None) is None


def test_try_parse_json_list_returns_none_for_empty_or_whitespace():
    assert _try_parse_json_list("") is None
    assert _try_parse_json_list("   ") is None


def test_try_parse_json_object_parses_dict_string():
    assert _try_parse_json_object('{"a": 1}') == {"a": 1}


def test_try_parse_json_object_rejects_non_object_json():
    assert _try_parse_json_object('[1, 2]') is None


def test_try_parse_json_object_returns_none_for_malformed_json():
    assert _try_parse_json_object('{"a": ') is None


def test_try_parse_json_object_returns_none_for_non_string():
    assert _try_parse_json_object({"a": 1}) is None  # already a dict, not a str
    assert _try_parse_json_object(None) is None

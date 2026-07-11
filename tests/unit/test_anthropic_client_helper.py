"""Unit tests for ``utils/anthropic_client.py`` (Workstream A3).

The three consolidated copies had ZERO direct coverage — every existing
test mocked their public wrappers. These tests pin the shared helper's
contract with a fake SDK client: key precedence, base-url passthrough,
forced tool_choice + thinking-disable, retry shape, exception identity,
and the single-shot text path.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from universal_agent.utils import anthropic_client as ac

TOOL = {"name": "emit", "input_schema": {"type": "object"}}


class FakeMessages:
    def __init__(self, results):
        # results: list of content-lists or Exceptions, consumed per call.
        self._results = list(results)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(content=result)


def _fake_client_factory(monkeypatch, results):
    messages = FakeMessages(results)
    monkeypatch.setattr(ac, "build_anthropic_client", lambda: SimpleNamespace(messages=messages))
    return messages


def _tool_block(payload):
    return SimpleNamespace(type="tool_use", input=payload)


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


class TestKeyResolution:
    def test_precedence_order(self, monkeypatch):
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ZAI_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("ZAI_API_KEY", "zai")
        assert ac.resolve_llm_api_key() == "zai"
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
        assert ac.resolve_llm_api_key() == "token"
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
        assert ac.resolve_llm_api_key() == "key"
        assert ac.has_llm_key()

    def test_no_key_raises_runtime_error(self, monkeypatch):
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ZAI_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        assert not ac.has_llm_key()
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            ac.build_anthropic_client()


class TestCallLlmStructured:
    def test_happy_path_forces_tool_and_disables_thinking(self, monkeypatch):
        messages = _fake_client_factory(monkeypatch, [[_tool_block({"ok": True})]])
        result = ac.call_llm_structured(system="s", user="u", tool=TOOL, max_tokens=123)
        assert result == {"ok": True}
        call = messages.calls[0]
        assert call["max_tokens"] == 123
        assert call["tool_choice"] == {"type": "tool", "name": "emit"}
        assert call["thinking"] == {"type": "disabled"}

    def test_returns_a_copy_of_tool_input(self, monkeypatch):
        payload = {"k": "v"}
        _fake_client_factory(monkeypatch, [[_tool_block(payload)]])
        result = ac.call_llm_structured(system="s", user="u", tool=TOOL, max_tokens=10)
        assert result == payload and result is not payload

    def test_retries_after_no_tool_use_then_succeeds(self, monkeypatch):
        messages = _fake_client_factory(
            monkeypatch,
            [[_text_block("prose only")], [_tool_block({"ok": 1})]],
        )
        result = ac.call_llm_structured(system="s", user="u", tool=TOOL, max_tokens=10)
        assert result == {"ok": 1}
        assert len(messages.calls) == 2

    def test_retries_after_exception_then_succeeds(self, monkeypatch):
        _fake_client_factory(
            monkeypatch, [ConnectionError("net down"), [_tool_block({"ok": 1})]]
        )
        assert ac.call_llm_structured(system="s", user="u", tool=TOOL, max_tokens=10) == {"ok": 1}

    def test_exception_on_last_attempt_propagates_with_type(self, monkeypatch):
        _fake_client_factory(
            monkeypatch, [ConnectionError("first"), ConnectionError("second")]
        )
        with pytest.raises(ConnectionError, match="second"):
            ac.call_llm_structured(system="s", user="u", tool=TOOL, max_tokens=10)

    def test_all_no_tool_use_raises_runtime_error(self, monkeypatch):
        _fake_client_factory(
            monkeypatch, [[_text_block("a")], [_text_block("b")]]
        )
        with pytest.raises(RuntimeError, match="no tool_use"):
            ac.call_llm_structured(system="s", user="u", tool=TOOL, max_tokens=10)


class TestCallLlmText:
    def test_joins_text_blocks_and_strips(self, monkeypatch):
        _fake_client_factory(
            monkeypatch, [[_text_block("hello "), _text_block("world  ")]]
        )
        assert ac.call_llm_text(system="s", user="u", max_tokens=10) == "hello world"

    def test_single_shot_no_retry(self, monkeypatch):
        messages = _fake_client_factory(monkeypatch, [ConnectionError("boom")])
        with pytest.raises(ConnectionError):
            ac.call_llm_text(system="s", user="u", max_tokens=10)
        assert len(messages.calls) == 1

    def test_model_override(self, monkeypatch):
        messages = _fake_client_factory(monkeypatch, [[_text_block("x")]])
        ac.call_llm_text(system="s", user="u", max_tokens=10, model="custom-model")
        assert messages.calls[0]["model"] == "custom-model"

"""Tests for universal_agent.services.telegram_send pure helpers."""

from __future__ import annotations

import pytest

from universal_agent.services.telegram_send import (
    _api_url,
    _build_payload,
    _resolve_token,
)


# ---------------------------------------------------------------------------
# _resolve_token
# ---------------------------------------------------------------------------


class TestResolveToken:
    def test_explicit_token(self):
        assert _resolve_token("bot123") == "bot123"

    def test_explicit_token_stripped(self):
        assert _resolve_token("  bot123  ") == "bot123"

    def test_none_raises_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        with pytest.raises(ValueError, match="No Telegram bot token"):
            _resolve_token(None)

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        assert _resolve_token(None) == "env-token"

    def test_explicit_overrides_env(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        assert _resolve_token("explicit") == "explicit"

    def test_empty_string_raises(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        with pytest.raises(ValueError):
            _resolve_token("")

    def test_whitespace_only_raises(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        with pytest.raises(ValueError):
            _resolve_token("   ")


# ---------------------------------------------------------------------------
# _api_url
# ---------------------------------------------------------------------------


class TestApiUrl:
    def test_send_message(self):
        url = _api_url("tok123", "sendMessage")
        assert url == "https://api.telegram.org/bottok123/sendMessage"

    def test_edit_message(self):
        url = _api_url("abc", "editMessageText")
        assert url == "https://api.telegram.org/botabc/editMessageText"


# ---------------------------------------------------------------------------
# _build_payload
# ---------------------------------------------------------------------------


class TestBuildPayload:
    def test_minimal(self):
        payload = _build_payload(chat_id=42, text="hello")
        assert payload == {
            "chat_id": 42,
            "text": "hello",
            "disable_web_page_preview": True,
        }

    def test_string_chat_id(self):
        payload = _build_payload(chat_id="@channel", text="hi")
        assert payload["chat_id"] == "@channel"

    def test_with_parse_mode(self):
        payload = _build_payload(chat_id=1, text="*bold*", parse_mode="MarkdownV2")
        assert payload["parse_mode"] == "MarkdownV2"

    def test_no_parse_mode_omits_key(self):
        payload = _build_payload(chat_id=1, text="hi")
        assert "parse_mode" not in payload

    def test_with_thread_id(self):
        payload = _build_payload(chat_id=1, text="hi", thread_id=99)
        assert payload["message_thread_id"] == 99

    def test_no_thread_id_omits_key(self):
        payload = _build_payload(chat_id=1, text="hi")
        assert "message_thread_id" not in payload

    def test_disable_preview_false(self):
        payload = _build_payload(chat_id=1, text="hi", disable_preview=False)
        assert payload["disable_web_page_preview"] is False

    def test_all_options(self):
        payload = _build_payload(
            chat_id=123,
            text="msg",
            parse_mode="HTML",
            thread_id=5,
            disable_preview=False,
        )
        assert payload == {
            "chat_id": 123,
            "text": "msg",
            "disable_web_page_preview": False,
            "parse_mode": "HTML",
            "message_thread_id": 5,
        }

"""Tests for link_card_tokens — one-shot TTL-bounded token manager."""

import json
import time

import pytest

from universal_agent.services import link_card_tokens as lct

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _tmp_token_store(tmp_path, monkeypatch):
    """Redirect token storage to a temp file so tests are isolated."""
    store = tmp_path / "link_card_tokens.json"
    monkeypatch.setenv("UA_LINK_CARD_TOKENS_PATH", str(store))
    yield store


# ---------------------------------------------------------------------------
# issue()
# ---------------------------------------------------------------------------


class TestIssue:
    def test_basic_issue(self):
        result = lct.issue("sr-001")
        assert result["spend_request_id"] == "sr-001"
        assert result["token"].startswith("tok_")
        assert result["ttl_seconds"] > 0
        assert result["expires_at"] > time.time()

    def test_issue_persists_to_disk(self, _tmp_token_store):
        lct.issue("sr-001")
        assert _tmp_token_store.exists()
        data = json.loads(_tmp_token_store.read_text())
        assert len(data["tokens"]) == 1

    def test_issue_custom_ttl(self):
        result = lct.issue("sr-002", ttl_seconds=60)
        assert result["ttl_seconds"] == 60

    def test_issue_rejects_empty_id(self):
        with pytest.raises(ValueError, match="spend_request_id required"):
            lct.issue("")

    def test_multiple_issues_independent_tokens(self):
        a = lct.issue("sr-a")
        b = lct.issue("sr-b")
        assert a["token"] != b["token"]


# ---------------------------------------------------------------------------
# peek()
# ---------------------------------------------------------------------------


class TestPeek:
    def test_peek_existing_token(self):
        issued = lct.issue("sr-peek")
        record = lct.peek(issued["token"])
        assert record is not None
        assert record["spend_request_id"] == "sr-peek"
        assert record["consumed"] is False

    def test_peek_missing_token(self):
        assert lct.peek("tok_nonexistent") is None

    def test_peek_does_not_consume(self):
        issued = lct.issue("sr-noconsume")
        lct.peek(issued["token"])
        record = lct.peek(issued["token"])
        assert record["consumed"] is False


# ---------------------------------------------------------------------------
# consume()
# ---------------------------------------------------------------------------


class TestConsume:
    def test_consume_valid_token(self):
        issued = lct.issue("sr-consume", ttl_seconds=300)
        result = lct.consume(issued["token"])
        assert result["ok"] is True
        assert result["spend_request_id"] == "sr-consume"
        assert "expires_at" in result
        assert "issued_at" in result

    def test_consume_marks_consumed(self):
        issued = lct.issue("sr-once", ttl_seconds=300)
        lct.consume(issued["token"])
        record = lct.peek(issued["token"])
        assert record["consumed"] is True
        assert record["consumed_at"] is not None

    def test_consume_twice_rejected(self):
        issued = lct.issue("sr-double", ttl_seconds=300)
        lct.consume(issued["token"])
        result = lct.consume(issued["token"])
        assert result["ok"] is False
        assert result["code"] == "already_consumed"

    def test_consume_expired_token(self, monkeypatch):
        issued = lct.issue("sr-exp", ttl_seconds=1)
        monkeypatch.setattr(time, "time", lambda: issued["expires_at"] + 10)
        result = lct.consume(issued["token"])
        assert result["ok"] is False
        assert result["code"] == "expired"
        assert result["spend_request_id"] == "sr-exp"

    def test_consume_missing_token(self):
        result = lct.consume("tok_doesnotexist")
        assert result["ok"] is False
        assert result["code"] == "not_found"

    def test_consume_empty_string(self):
        result = lct.consume("")
        assert result["ok"] is False
        assert result["code"] == "not_found"

    def test_consume_none_token(self):
        result = lct.consume(None)
        assert result["ok"] is False
        assert result["code"] == "not_found"

    def test_consume_non_string_token(self):
        result = lct.consume(12345)
        assert result["ok"] is False
        assert result["code"] == "not_found"


# ---------------------------------------------------------------------------
# purge_expired()
# ---------------------------------------------------------------------------


class TestPurgeExpired:
    def test_purge_removes_old_tokens(self, monkeypatch):
        freeze = time.time()
        monkeypatch.setattr(time, "time", lambda: freeze)
        lct.issue("sr-old", ttl_seconds=1)

        monkeypatch.setattr(time, "time", lambda: freeze + 100000)
        removed = lct.purge_expired(older_than_seconds=86400)
        assert removed == 1

    def test_purge_keeps_live_tokens(self):
        lct.issue("sr-live", ttl_seconds=3600)
        removed = lct.purge_expired()
        assert removed == 0

    def test_purge_mixed(self, monkeypatch):
        freeze = time.time()
        monkeypatch.setattr(time, "time", lambda: freeze)
        lct.issue("sr-old", ttl_seconds=1)
        lct.issue("sr-live", ttl_seconds=200000)

        # Advance 100000s; cutoff = freeze + 100000 - 86400 = freeze + 13600
        # sr-old expires_at = freeze + 1 < cutoff -> purged
        # sr-live expires_at = freeze + 200000 > cutoff -> kept
        monkeypatch.setattr(time, "time", lambda: freeze + 100000)
        removed = lct.purge_expired(older_than_seconds=86400)
        assert removed == 1

        store_path = lct.resolve_tokens_path()
        data = json.loads(store_path.read_text())
        assert len(data["tokens"]) == 1

    def test_purge_empty_store(self):
        removed = lct.purge_expired()
        assert removed == 0


# ---------------------------------------------------------------------------
# resolve_tokens_path()
# ---------------------------------------------------------------------------


class TestResolveTokensPath:
    def test_env_override(self, monkeypatch, tmp_path):
        custom = tmp_path / "custom.json"
        monkeypatch.setenv("UA_LINK_CARD_TOKENS_PATH", str(custom))
        assert lct.resolve_tokens_path() == custom

    def test_default_path_without_env(self, monkeypatch):
        monkeypatch.delenv("UA_LINK_CARD_TOKENS_PATH", raising=False)
        path = lct.resolve_tokens_path()
        assert path.name == "link_card_tokens.json"
        assert "AGENT_RUN_WORKSPACES" in str(path)

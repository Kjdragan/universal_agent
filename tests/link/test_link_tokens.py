"""Phase 2b tests for the Link card-token store.

Covers:
  - issue() returns a token + spend_request_id + expires_at + ttl.
  - consume() succeeds once, then fails with already_consumed.
  - consume() fails with expired after TTL.
  - consume() fails with not_found for unknown / empty tokens.
  - peek() does not consume.
  - purge_expired() removes old tokens.
  - Token files are stored at UA_LINK_CARD_TOKENS_PATH (override honored).
  - Tokens NEVER store card data (PAN/CVC).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from universal_agent.services import link_card_tokens


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("UA_LINK_CARD_TOKENS_PATH", str(tmp_path / "tokens.json"))
    for var in list(os.environ):
        if var.startswith("UA_LINK_") and var != "UA_LINK_CARD_TOKENS_PATH":
            monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_resolve_path_uses_override(isolated):
    assert link_card_tokens.resolve_tokens_path() == (isolated / "tokens.json").resolve()


def test_issue_returns_expected_shape(isolated):
    out = link_card_tokens.issue("lsrq_001")
    assert out["token"].startswith("tok_")
    assert out["spend_request_id"] == "lsrq_001"
    assert out["expires_at"] > time.time()
    assert out["ttl_seconds"] == 900  # default


def test_issue_persists_to_file(isolated):
    link_card_tokens.issue("lsrq_002")
    file = link_card_tokens.resolve_tokens_path()
    assert file.exists()
    payload = json.loads(file.read_text())
    assert "tokens" in payload
    assert len(payload["tokens"]) == 1


def test_issue_custom_ttl(isolated):
    out = link_card_tokens.issue("lsrq_x", ttl_seconds=60)
    assert out["ttl_seconds"] == 60
    assert out["expires_at"] - time.time() < 70


def test_consume_success_then_already_consumed(isolated):
    out = link_card_tokens.issue("lsrq_003")
    token = out["token"]

    first = link_card_tokens.consume(token)
    assert first["ok"] is True
    assert first["spend_request_id"] == "lsrq_003"

    second = link_card_tokens.consume(token)
    assert second["ok"] is False
    assert second["code"] == "already_consumed"
    assert second["spend_request_id"] == "lsrq_003"


def test_consume_not_found(isolated):
    res = link_card_tokens.consume("tok_nonexistent")
    assert res["ok"] is False
    assert res["code"] == "not_found"


def test_consume_empty_or_invalid(isolated):
    assert link_card_tokens.consume("")["code"] == "not_found"
    assert link_card_tokens.consume(None)["code"] == "not_found"  # type: ignore[arg-type]


def test_consume_expired(isolated):
    out = link_card_tokens.issue("lsrq_old", ttl_seconds=1)
    # Tamper: backdate expires_at into the past.
    file = link_card_tokens.resolve_tokens_path()
    payload = json.loads(file.read_text())
    payload["tokens"][out["token"]]["expires_at"] = time.time() - 60
    file.write_text(json.dumps(payload))

    res = link_card_tokens.consume(out["token"])
    assert res["ok"] is False
    assert res["code"] == "expired"


def test_peek_does_not_consume(isolated):
    out = link_card_tokens.issue("lsrq_p")
    rec1 = link_card_tokens.peek(out["token"])
    rec2 = link_card_tokens.peek(out["token"])
    assert rec1["consumed"] is False
    assert rec2["consumed"] is False
    # Now actually consume to confirm peek didn't flip state.
    assert link_card_tokens.consume(out["token"])["ok"] is True


def test_peek_unknown_returns_none(isolated):
    assert link_card_tokens.peek("tok_no") is None


def test_purge_expired(isolated):
    file = link_card_tokens.resolve_tokens_path()
    payload = {"tokens": {
        "tok_old": {"spend_request_id": "x", "issued_at": 0, "expires_at": time.time() - 100000,
                    "consumed": False, "consumed_at": None},
        "tok_new": {"spend_request_id": "y", "issued_at": 0, "expires_at": time.time() + 100000,
                    "consumed": False, "consumed_at": None},
    }}
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(payload))

    removed = link_card_tokens.purge_expired(older_than_seconds=10)
    assert removed == 1
    remaining = json.loads(file.read_text())["tokens"]
    assert "tok_new" in remaining
    assert "tok_old" not in remaining


def test_tokens_file_never_contains_card_data(isolated):
    """Card PAN/CVC must never appear in the tokens store."""
    link_card_tokens.issue("lsrq_xyz")
    raw = link_card_tokens.resolve_tokens_path().read_text()
    # Token store schema doesn't include card fields, but assert defensively.
    assert "number" not in raw
    assert "cvc" not in raw
    assert "exp_month" not in raw
    assert "4242424242424242" not in raw


def test_issue_requires_spend_request_id(isolated):
    with pytest.raises(ValueError):
        link_card_tokens.issue("")

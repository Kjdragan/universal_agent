from __future__ import annotations

from datetime import datetime, timezone

from universal_agent.services.google_workspace.error_policy import (
    GoogleErrorClass,
    RecoveryAction,
    decide_error_handling,
)
from universal_agent.services.google_workspace.models import ExecutionRoute, GoogleIntent, GoogleTokenRecord
from universal_agent.services.google_workspace.routing import decide_route
from universal_agent.services.google_workspace.scopes import WAVE_1_SCOPES, missing_wave_1_scopes
from universal_agent.services.google_workspace.token_vault import FileTokenVault, InMemoryTokenVault


class ReverseCipher:
    """Test-only reversible cipher implementation."""

    def encrypt(self, plaintext: bytes) -> bytes:
        return plaintext[::-1]

    def decrypt(self, ciphertext: bytes) -> bytes:
        return ciphertext[::-1]


def test_wave_1_scopes_are_locked_and_complete() -> None:
    expected = {
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/spreadsheets",
    }
    assert set(WAVE_1_SCOPES) == expected
    assert missing_wave_1_scopes(expected) == []


def test_route_decision_prefers_direct_for_v1_intent() -> None:
    decision = decide_route(
        GoogleIntent.GMAIL_SEND_REPLY,
        direct_enabled=True,
    )
    assert decision.route is ExecutionRoute.DIRECT
    assert decision.fallback_route is ExecutionRoute.COMPOSIO


def test_route_decision_falls_back_when_direct_not_implemented() -> None:
    decision = decide_route(
        GoogleIntent.SHEETS_READ_APPEND,
        direct_enabled=True,
        direct_implemented={GoogleIntent.GMAIL_SEND_REPLY},
        allow_composio_fallback=True,
    )
    assert decision.route is ExecutionRoute.COMPOSIO
    assert decision.fallback_route is ExecutionRoute.DIRECT


def test_error_policy_scope_denied_requests_additional_scope() -> None:
    decision = decide_error_handling(403, "Request had insufficient authentication scopes")
    assert decision.error_class is GoogleErrorClass.SCOPE_DENIED
    assert decision.action is RecoveryAction.REQUEST_ADDITIONAL_SCOPE
    assert decision.should_retry is False


def test_error_policy_rate_limit_retries() -> None:
    decision = decide_error_handling(429, "quota exceeded")
    assert decision.error_class is GoogleErrorClass.RATE_LIMIT
    assert decision.action is RecoveryAction.RETRY_WITH_BACKOFF
    assert decision.should_retry is True


def test_inmemory_token_vault_round_trip() -> None:
    vault = InMemoryTokenVault()
    record = GoogleTokenRecord(
        user_id="user-1",
        provider_user_email="user@example.com",
        access_token="at",
        refresh_token="rt",
        scopes=("openid",),
        issued_at=datetime.now(timezone.utc),
    )
    vault.upsert(record)
    loaded = vault.get("user-1")
    assert loaded is not None
    assert loaded.access_token == "at"


def test_file_token_vault_round_trip(tmp_path) -> None:
    vault = FileTokenVault(tmp_path / "google_tokens.json", cipher=ReverseCipher())
    record = GoogleTokenRecord(
        user_id="user-2",
        provider_user_email="user2@example.com",
        access_token="access",
        refresh_token="refresh",
        scopes=("openid", "email"),
        issued_at=datetime.now(timezone.utc),
    )
    vault.upsert(record)

    loaded = vault.get("user-2")
    assert loaded is not None
    assert loaded.refresh_token == "refresh"
    assert loaded.has_scope("email")

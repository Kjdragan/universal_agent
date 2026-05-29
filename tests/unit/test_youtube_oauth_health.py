"""Unit tests for youtube_oauth_health signing + age helpers.

The signed-link helpers gate a public endpoint that mints production
credentials, so the round-trip / tamper / expiry / purpose-isolation
properties are load-bearing security behavior — pin them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from universal_agent.services import youtube_oauth_health as yoh


@pytest.fixture(autouse=True)
def _signing_secret(monkeypatch):
    monkeypatch.setenv("UA_ARTIFACT_ACK_SECRET", "test-secret-abc123")
    # Ensure the other fallbacks don't interfere.
    monkeypatch.delenv("UA_OPS_TOKEN", raising=False)
    monkeypatch.delenv("UA_INTERNAL_API_TOKEN", raising=False)


def test_sign_verify_round_trip():
    token = yoh.mint_signed_param("start", ttl_seconds=3600)
    assert token
    assert yoh.check_signed_param("start", token) is True


def test_wrong_purpose_rejected():
    token = yoh.mint_signed_param("start", ttl_seconds=3600)
    assert yoh.check_signed_param("state", token) is False


def test_tampered_signature_rejected():
    token = yoh.mint_signed_param("start", ttl_seconds=3600)
    exp, _, sig = token.partition(".")
    tampered = f"{exp}.{'0' * len(sig)}"
    assert yoh.check_signed_param("start", tampered) is False


def test_expired_token_rejected():
    token = yoh.mint_signed_param("start", ttl_seconds=-10)
    assert yoh.check_signed_param("start", token) is False


def test_malformed_token_rejected():
    assert yoh.check_signed_param("start", "") is False
    assert yoh.check_signed_param("start", "no-dot") is False
    assert yoh.check_signed_param("start", "notanint.deadbeef") is False


def test_no_secret_means_no_token(monkeypatch):
    monkeypatch.delenv("UA_ARTIFACT_ACK_SECRET", raising=False)
    assert yoh.mint_signed_param("start", 3600) == ""
    assert yoh.check_signed_param("start", "123.abc") is False


def test_token_age_days():
    assert yoh.token_age_days(None) is None
    six_days_ago = datetime.now(timezone.utc) - timedelta(days=6)
    age = yoh.token_age_days(six_days_ago)
    assert age is not None and 5.9 < age < 6.1


def test_read_minted_at(monkeypatch):
    monkeypatch.delenv("YOUTUBE_OAUTH_REFRESH_TOKEN_MINTED_AT", raising=False)
    assert yoh.read_minted_at() is None
    stamp = "2026-05-29T11:07:39+00:00"
    monkeypatch.setenv("YOUTUBE_OAUTH_REFRESH_TOKEN_MINTED_AT", stamp)
    got = yoh.read_minted_at()
    assert got is not None and got.year == 2026 and got.month == 5 and got.day == 29


def test_build_consent_url():
    url = yoh.build_consent_url("cid.apps", "https://x.example/cb", "st8")
    assert url.startswith(yoh.OAUTH2_AUTH_URL)
    assert "client_id=cid.apps" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "state=st8" in url
    assert "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fyoutube" in url


def test_warn_age_days_default_and_override(monkeypatch):
    monkeypatch.delenv("UA_YOUTUBE_OAUTH_WARN_AGE_DAYS", raising=False)
    assert yoh.warn_age_days() == yoh.DEFAULT_WARN_AGE_DAYS
    monkeypatch.setenv("UA_YOUTUBE_OAUTH_WARN_AGE_DAYS", "3.5")
    assert yoh.warn_age_days() == 3.5
    monkeypatch.setenv("UA_YOUTUBE_OAUTH_WARN_AGE_DAYS", "garbage")
    assert yoh.warn_age_days() == yoh.DEFAULT_WARN_AGE_DAYS


def test_public_base_url_default(monkeypatch):
    for var in ("FRONTEND_URL", "UA_PUBLIC_BASE_URL", "UA_GATEWAY_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    assert yoh.public_base_url() == "https://app.clearspringcg.com"
    monkeypatch.setenv("UA_PUBLIC_BASE_URL", "https://custom.example/")
    assert yoh.public_base_url() == "https://custom.example"

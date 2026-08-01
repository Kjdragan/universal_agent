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


# ---------------------------------------------------------------------------
# Channel-identity helpers — the check liveness cannot make. A token for the
# wrong channel (wrong profile at Google's account picker) passes liveness
# forever while every playlist read 404s; these pin the machinery that
# turned that silent failure (2026-07-24..26, two dead digests) into an alarm.
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_fetch_token_channel_returns_identity(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(
        payload={"items": [{"id": "UCabc123", "snippet": {"title": "My Channel"}}]}))
    assert yoh.fetch_token_channel("tok") == ("UCabc123", "My Channel")


def test_fetch_token_channel_no_channel_is_empty_not_none(monkeypatch):
    # A valid token with NO channel is an observed identity (the brand-account
    # edge), distinct from "could not determine" — it must not read as None.
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(payload={"items": []}))
    assert yoh.fetch_token_channel("tok") == ("", "")


def test_fetch_token_channel_http_error_is_inconclusive(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(status_code=403, text="quota"))
    assert yoh.fetch_token_channel("tok") is None


def test_fetch_token_channel_network_error_is_inconclusive(monkeypatch):
    import httpx

    def _boom(*a, **k):
        raise OSError("connection reset")

    monkeypatch.setattr(httpx, "get", _boom)
    assert yoh.fetch_token_channel("tok") is None


def test_refresh_access_token_returns_token(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(
        payload={"access_token": "ya29.fresh"}))
    token, detail = yoh.refresh_access_token("cid", "sec", "rt")
    assert token == "ya29.fresh" and detail == "ok"


def test_refresh_access_token_dead_token(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(
        status_code=400, text='{"error": "invalid_grant"}'))
    token, detail = yoh.refresh_access_token("cid", "sec", "rt")
    assert token is None and "invalid_grant" in detail


def test_refresh_access_token_network_error_inconclusive(monkeypatch):
    import httpx

    def _boom(*a, **k):
        raise OSError("timeout")

    monkeypatch.setattr(httpx, "post", _boom)
    token, detail = yoh.refresh_access_token("cid", "sec", "rt")
    assert token is None and detail.startswith("inconclusive")


def test_test_refresh_token_compat_alive_on_ok_and_network_error(monkeypatch):
    # test_refresh_token now delegates to refresh_access_token; its contract
    # (alive on success AND on inconclusive network errors) must not drift.
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(
        payload={"access_token": "t"}))
    assert yoh.test_refresh_token("cid", "sec", "rt") == (True, "ok")

    def _boom(*a, **k):
        raise OSError("net down")

    monkeypatch.setattr(httpx, "post", _boom)
    alive, detail = yoh.test_refresh_token("cid", "sec", "rt")
    assert alive is True and "inconclusive" in detail

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(
        status_code=400, text="invalid_grant"))
    alive, detail = yoh.test_refresh_token("cid", "sec", "rt")
    assert alive is False and "invalid_grant" in detail


def test_expected_channel_keys_are_stable():
    # These names are the Infisical contract; renaming them would orphan the
    # stored identity and silently disable the assertion.
    assert yoh.EXPECTED_CHANNEL_KEY == "YOUTUBE_OAUTH_EXPECTED_CHANNEL_ID"
    assert yoh.EXPECTED_CHANNEL_TITLE_KEY == "YOUTUBE_OAUTH_EXPECTED_CHANNEL_TITLE"


# ── production-mode expiry gating (app published "In production" 2026-08-01) ──


def test_testing_mode_expiry_defaults_off(monkeypatch):
    monkeypatch.delenv(yoh.TESTING_MODE_KEY, raising=False)
    assert yoh.testing_mode_expiry() is False


def test_testing_mode_expiry_truthy_values(monkeypatch):
    for raw in ("1", "true", "YES", "On"):
        monkeypatch.setenv(yoh.TESTING_MODE_KEY, raw)
        assert yoh.testing_mode_expiry() is True, raw
    monkeypatch.setenv(yoh.TESTING_MODE_KEY, "0")
    assert yoh.testing_mode_expiry() is False


def test_resolve_watchdog_state_dead_beats_everything():
    assert (
        yoh.resolve_watchdog_state(
            alive=False, wrong_channel=True, age_days=9.0, threshold=5.0,
            testing_mode=True,
        )
        == "dead"
    )


def test_resolve_watchdog_state_wrong_channel_beats_age():
    assert (
        yoh.resolve_watchdog_state(
            alive=True, wrong_channel=True, age_days=9.0, threshold=5.0,
            testing_mode=True,
        )
        == "wrong_channel"
    )


def test_resolve_watchdog_state_age_warns_only_in_testing_mode():
    # Production mode (the current state): an old token is HEALTHY — tokens
    # no longer age out, and the July 2026 daily nag was a false alarm.
    assert (
        yoh.resolve_watchdog_state(
            alive=True, wrong_channel=False, age_days=9.6, threshold=5.0,
            testing_mode=False,
        )
        == "healthy"
    )
    # Testing mode: the same age correctly warns.
    assert (
        yoh.resolve_watchdog_state(
            alive=True, wrong_channel=False, age_days=9.6, threshold=5.0,
            testing_mode=True,
        )
        == "expiring"
    )


def test_resolve_watchdog_state_unknown_age_is_healthy_in_testing_mode():
    assert (
        yoh.resolve_watchdog_state(
            alive=True, wrong_channel=False, age_days=None, threshold=5.0,
            testing_mode=True,
        )
        == "healthy"
    )

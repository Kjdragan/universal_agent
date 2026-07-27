"""Shared helpers for YouTube OAuth token health + the email-button re-auth flow.

The YouTube OAuth refresh token expires roughly every 7 days because the
Google OAuth app is still in "Testing" mode.  When it dies, the morning
digest cron and the gold-channel poller both fail silently.  This module
backs three callers that together make the weekly re-mint painless:

- ``scripts/youtube_oauth_watchdog.py`` — a daily cron that tests the
  token's liveness and computes its age, then emails a one-tap re-auth
  button *before* the 7-day window closes.
- ``gateway_server.py`` — the public ``/api/v1/youtube-oauth/start`` and
  ``/api/v1/youtube-oauth/callback`` endpoints that the email button
  drives: ``start`` bounces the operator to Google's consent screen, and
  ``callback`` exchanges the code and writes the fresh token (+ a minted-at
  timestamp) back to production Infisical.
- ``scripts/youtube_oauth2_setup.py`` — the legacy localhost CLI mint,
  which also records the minted-at timestamp so the watchdog's age check
  is consistent no matter which path re-authed.

Security model for the public endpoints:
- The ``start`` link carried in the email is HMAC-signed with a TTL (same
  secret source as the brief-feedback links), so the endpoint can't be
  used as an open "trigger a Google consent screen" nuisance.
- The OAuth ``state`` parameter is an independent short-TTL signed token,
  verified on ``callback`` to defend against CSRF.
- Ultimately only a Google *test user* of the OAuth app (i.e. Kevin) can
  complete consent, so even a forged ``start`` hit yields no token.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import logging
import os
import urllib.parse

logger = logging.getLogger(__name__)

OAUTH2_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH2_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube"

# Infisical secret keys.
REFRESH_TOKEN_KEY = "YOUTUBE_OAUTH_REFRESH_TOKEN"
MINTED_AT_KEY = "YOUTUBE_OAUTH_REFRESH_TOKEN_MINTED_AT"

# The channel identity contract. A YouTube OAuth grant is for ONE channel,
# chosen at Google's account picker — and picking the wrong profile there
# yields a token that passes every liveness check while every playlist read
# fails with playlistNotFound (private playlists of the right channel are
# simply invisible to it). That exact miss killed two days of digests on
# 2026-07-24..26. These keys pin which channel the token is SUPPOSED to be:
# adopted from the current healthy token on first watchdog run (trust on
# first use), then asserted daily and at every re-auth callback.
EXPECTED_CHANNEL_KEY = "YOUTUBE_OAUTH_EXPECTED_CHANNEL_ID"
EXPECTED_CHANNEL_TITLE_KEY = "YOUTUBE_OAUTH_EXPECTED_CHANNEL_TITLE"

# Gateway route paths (kept here so the watchdog email and the endpoints
# agree on a single source of truth).
START_PATH = "/api/v1/youtube-oauth/start"
CALLBACK_PATH = "/api/v1/youtube-oauth/callback"

# Default: warn once the token is >= 5 days old (the 7-day expiry leaves a
# ~2-day lead). Override with UA_YOUTUBE_OAUTH_WARN_AGE_DAYS.
DEFAULT_WARN_AGE_DAYS = 5.0


def current_iso() -> str:
    """UTC now as an ISO-8601 string (used for the minted-at stamp)."""
    return datetime.now(timezone.utc).isoformat()


def public_base_url() -> str:
    """Operator-facing public base URL for the gateway (HTTPS).

    Mirrors ``gateway_server._brief_feedback_base_url`` so the OAuth button
    links resolve to the same public host that already serves the
    brief-feedback landing pages.
    """
    return (
        os.getenv("FRONTEND_URL", "")
        or os.getenv("UA_PUBLIC_BASE_URL", "")
        or os.getenv("UA_GATEWAY_BASE_URL", "")
        or "https://app.clearspringcg.com"
    ).strip().rstrip("/")


def warn_age_days() -> float:
    """Return the configured OAuth credential age threshold (days) before warning."""
    raw = (os.getenv("UA_YOUTUBE_OAUTH_WARN_AGE_DAYS") or "").strip()
    if not raw:
        return DEFAULT_WARN_AGE_DAYS
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_WARN_AGE_DAYS


# ── Signed link/state helpers ────────────────────────────────────────────


def _secret() -> bytes:
    """HMAC secret, same precedence as cron_artifact_notifier feedback tokens."""
    raw = (
        os.getenv("UA_ARTIFACT_ACK_SECRET")
        or os.getenv("UA_OPS_TOKEN")
        or os.getenv("UA_INTERNAL_API_TOKEN")
        or ""
    ).strip()
    return raw.encode("utf-8")


def mint_signed_param(purpose: str, ttl_seconds: int) -> str:
    """Return ``"{expires_epoch}.{hex_sig}"`` for a purpose-scoped link/state.

    Returns an empty string when no signing secret is configured (the
    caller should treat that as "feature unavailable" rather than minting
    an unsigned link).
    """
    secret = _secret()
    if not secret:
        return ""
    exp = int(datetime.now(timezone.utc).timestamp()) + int(ttl_seconds)
    payload = f"{purpose}:{exp}".encode("utf-8")
    sig = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def check_signed_param(purpose: str, value: str) -> bool:
    """Validate a token from :func:`mint_signed_param` (TTL + signature)."""
    secret = _secret()
    if not secret or not value or "." not in value:
        return False
    exp_str, _, sig = value.partition(".")
    try:
        exp = int(exp_str)
    except ValueError:
        return False
    if exp < int(datetime.now(timezone.utc).timestamp()):
        return False  # expired
    payload = f"{purpose}:{exp}".encode("utf-8")
    expected = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig.strip())


# ── OAuth URL + token operations ─────────────────────────────────────────


def build_consent_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Build the Google consent URL (offline access, forced consent)."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": YOUTUBE_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{OAUTH2_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    *,
    timeout: float = 15.0,
) -> dict:
    """Exchange an authorization code for tokens. Raises on non-200.

    Synchronous (httpx.post). Callers on the async event loop should wrap
    this in ``asyncio.to_thread`` to avoid blocking.
    """
    import httpx

    resp = httpx.post(
        OAUTH2_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"token exchange failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    *,
    timeout: float = 15.0,
) -> tuple[str | None, str]:
    """Exchange the refresh token for an access token.

    Returns ``(access_token, detail)``.  ``(None, "inconclusive ...")`` on a
    network error — the caller decides whether that counts as alive.  The
    access token is what the channel-identity check needs; discarding it
    (as the old liveness-only check did) is how a wrong-channel token
    passed the watchdog for two days.
    """
    import httpx

    if not (client_id and client_secret and refresh_token):
        return None, "missing client_id / client_secret / refresh_token"
    try:
        resp = httpx.post(
            OAUTH2_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=timeout,
        )
    except Exception as exc:  # network error — inconclusive, not dead
        return None, f"inconclusive (network error: {exc})"
    token = resp.json().get("access_token") if resp.status_code == 200 else None
    if token:
        return token, "ok"
    return None, f"{resp.status_code}: {resp.text[:200]}"


def test_refresh_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    *,
    timeout: float = 15.0,
) -> tuple[bool, str]:
    """Actively test a refresh token against Google.

    Returns ``(alive, detail)``.  ``alive`` is True only when Google issues
    a fresh access token.  An ``invalid_grant`` (the expired-token case)
    returns ``(False, "invalid_grant: ...")``.
    """
    token, detail = refresh_access_token(
        client_id, client_secret, refresh_token, timeout=timeout)
    if token:
        return True, "ok"
    if detail.startswith("inconclusive"):
        return True, f"liveness check {detail}"
    return False, detail


def fetch_token_channel(access_token: str, *, timeout: float = 15.0) -> tuple[str, str] | None:
    """The channel identity this token acts as: ``(channel_id, title)``.

    ``channels?mine=true`` returns the channel selected at Google's account
    picker during consent — the one identity all YouTube API calls act as.
    Returns ``("", "")`` when the token is valid but has NO channel, and
    ``None`` when the answer could not be determined (network/API error) —
    callers must treat ``None`` as inconclusive, never as a mismatch.
    """
    import httpx

    try:
        resp = httpx.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "snippet", "mine": "true"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=timeout,
        )
    except Exception as exc:
        logger.warning("fetch_token_channel: network error: %s", exc)
        return None
    if resp.status_code != 200:
        logger.warning("fetch_token_channel: HTTP %s: %s", resp.status_code, resp.text[:150])
        return None
    items = resp.json().get("items") or []
    if not items:
        return ("", "")
    snippet = items[0].get("snippet") or {}
    return (items[0].get("id") or "", snippet.get("title") or "")


def read_minted_at() -> datetime | None:
    """Parse the minted-at stamp from the environment, if present."""
    raw = (os.getenv(MINTED_AT_KEY) or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def token_age_days(minted_at: datetime | None) -> float | None:
    """Age of the token in days, or None when minted-at is unknown."""
    if minted_at is None:
        return None
    delta = datetime.now(timezone.utc) - minted_at
    return delta.total_seconds() / 86400.0

"""Reddit OAuth2 token lifecycle manager.

Uses the 'password' grant type with a Reddit 'script' application
to acquire and auto-refresh bearer tokens for the authenticated API
at oauth.reddit.com.

Token caching: tokens are cached in-memory and refreshed 60s before
expiry (Reddit tokens last 3600s).  No external state is required.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_REFRESH_MARGIN_SECONDS = 60  # refresh 60s before expiry


class RedditOAuthManager:
    """Acquire and cache Reddit OAuth2 bearer tokens.

    Usage::

        mgr = RedditOAuthManager(
            client_id="...",
            client_secret="...",
            username="...",
            password="...",
            user_agent="CSIIngester/1.0 (by u/your_username)",
        )
        token = await mgr.get_token()
        # Use in header: Authorization: Bearer {token}
    """

    def __init__(
        self,
        *,
        client_id: str = "",
        client_secret: str = "",
        username: str = "",
        password: str = "",
        user_agent: str = "CSIIngester/1.0",
    ) -> None:
        self._client_id = client_id or os.getenv("REDDIT_CLIENT_ID", "")
        self._client_secret = client_secret or os.getenv("REDDIT_CLIENT_SECRET", "")
        self._username = username or os.getenv("REDDIT_USERNAME", "")
        self._password = password or os.getenv("REDDIT_PASSWORD", "")
        self._user_agent = user_agent

        self._access_token: str = ""
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def is_configured(self) -> bool:
        """Return True if all required credentials are present."""
        return bool(
            self._client_id
            and self._client_secret
            and self._username
            and self._password
        )

    async def get_token(self) -> str:
        """Return a valid bearer token, refreshing if needed.

        Raises RuntimeError if credentials are not configured or
        token acquisition fails.
        """
        if not self.is_configured:
            raise RuntimeError(
                "Reddit OAuth not configured: missing one or more of "
                "REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD"
            )

        now = time.time()
        if self._access_token and now < self._expires_at - _REFRESH_MARGIN_SECONDS:
            return self._access_token

        async with self._lock:
            # Double-check after acquiring lock
            now = time.time()
            if self._access_token and now < self._expires_at - _REFRESH_MARGIN_SECONDS:
                return self._access_token
            return await self._acquire_token()

    async def _acquire_token(self) -> str:
        """POST to Reddit's token endpoint using password grant."""
        auth = httpx.BasicAuth(self._client_id, self._client_secret)
        data: dict[str, str] = {
            "grant_type": "password",
            "username": self._username,
            "password": self._password,
        }
        headers = {
            "User-Agent": self._user_agent,
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _TOKEN_URL,
                auth=auth,
                data=data,
                headers=headers,
            )

        if resp.status_code != 200:
            body = resp.text[:500]
            self._access_token = ""
            self._expires_at = 0.0
            raise RuntimeError(
                f"Reddit OAuth token request failed: HTTP {resp.status_code}: {body}"
            )

        payload: dict[str, Any] = resp.json()
        token = str(payload.get("access_token") or "")
        if not token:
            raise RuntimeError(
                f"Reddit OAuth token response missing access_token: {payload}"
            )

        expires_in = int(payload.get("expires_in", 3600))
        self._access_token = token
        self._expires_at = time.time() + expires_in

        logger.info(
            "Reddit OAuth token acquired: expires_in=%ds scope=%s",
            expires_in,
            payload.get("scope", "?"),
        )
        return self._access_token

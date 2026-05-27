"""Service for managing YouTube playlists via the YouTube Data API v3.

Requires OAuth2 credentials to modify user data (like deleting from a playlist).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from universal_agent.infisical_loader import initialize_runtime_secrets

logger = logging.getLogger(__name__)

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
OAUTH2_TOKEN_URL = "https://oauth2.googleapis.com/token"


class YouTubeOAuthError(Exception):
    """Raised when OAuth2 token exchange or refresh fails."""


class YouTubeAPIError(Exception):
    """Raised when the YouTube Data API returns an error response."""


def _get_access_token() -> str:
    """Exchange the stored refresh token for a short-lived access token."""
    initialize_runtime_secrets()
    client_id = str(os.getenv("YOUTUBE_OAUTH_CLIENT_ID") or "").strip()
    client_secret = str(os.getenv("YOUTUBE_OAUTH_CLIENT_SECRET") or "").strip()
    refresh_token = str(os.getenv("YOUTUBE_OAUTH_REFRESH_TOKEN") or "").strip()

    if not client_id or not client_secret or not refresh_token:
        raise YouTubeOAuthError(
            "Missing YOUTUBE_OAUTH_CLIENT_ID, YOUTUBE_OAUTH_CLIENT_SECRET, or YOUTUBE_OAUTH_REFRESH_TOKEN "
            "in Infisical. Please run the youtube_oauth2_setup.py script first."
        )

    response = httpx.post(
        OAUTH2_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15.0,
    )
    if response.status_code != 200:
        raise YouTubeOAuthError(f"Failed to refresh access token: {response.text}")
    
    data = response.json()
    access_token = data.get("access_token")
    if not access_token:
        raise YouTubeOAuthError("No access token returned from Google OAuth2 API")
    return access_token


def get_playlist_items(playlist_id: str) -> list[dict[str, Any]]:
    """Fetch all video items from a YouTube playlist."""
    access_token = _get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    
    items = []
    page_token = None
    
    with httpx.Client(headers=headers, timeout=30.0) as client:
        while True:
            params: dict[str, Any] = {
                "part": "snippet",
                "playlistId": playlist_id,
                "maxResults": 50,
            }
            if page_token:
                params["pageToken"] = page_token
                
            response = client.get(f"{YOUTUBE_API_BASE}/playlistItems", params=params)
            if response.status_code != 200:
                raise YouTubeAPIError(f"Failed to fetch playlist items: {response.text}")
            
            data = response.json()
            for item in data.get("items", []):
                snippet = item.get("snippet", {})
                video_id = snippet.get("resourceId", {}).get("videoId")
                if video_id:
                    # `videoOwnerChannelId` identifies the channel that uploaded
                    # the video (distinct from the playlist owner). The gold-tier
                    # poller threads this so the digest's ingest loop can look
                    # up per-channel duration overrides without an extra API call.
                    items.append({
                        "playlist_item_id": item.get("id"),
                        "video_id": video_id,
                        "title": snippet.get("title"),
                        "video_owner_channel_id": snippet.get("videoOwnerChannelId"),
                    })
                    
            page_token = data.get("nextPageToken")
            if not page_token:
                break
                
    return items


def add_playlist_item(playlist_id: str, video_id: str) -> dict[str, Any]:
    """Add a video to a YouTube playlist and return the created playlist item."""
    access_token = _get_access_token()
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {
                "kind": "youtube#video",
                "videoId": video_id,
            },
        },
    }

    with httpx.Client(headers=headers, timeout=15.0) as client:
        response = client.post(
            f"{YOUTUBE_API_BASE}/playlistItems",
            params={"part": "snippet"},
            json=payload,
        )
        if response.status_code not in {200, 201}:
            raise YouTubeAPIError(f"Failed to add video {video_id} to playlist {playlist_id}: {response.text}")
        return response.json()


def remove_playlist_item(playlist_item_id: str) -> bool:
    """Physically delete an item from the user's YouTube playlist."""
    access_token = _get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}

    with httpx.Client(headers=headers, timeout=15.0) as client:
        response = client.delete(
            f"{YOUTUBE_API_BASE}/playlistItems",
            params={"id": playlist_item_id}
        )
        if response.status_code == 204:
            return True
        elif response.status_code == 404:
            logger.warning(f"Playlist item {playlist_item_id} already deleted or not found.")
            return True
        else:
            raise YouTubeAPIError(f"Failed to delete playlist item {playlist_item_id}: {response.text}")


def get_playlist_metadata(playlist_id: str) -> dict[str, Any]:
    """Fetch a playlist's title, description, and privacy status.

    Used by the digest cron's daily playlist-recreate flow to preserve the
    user-visible title (e.g. "Monday Digest") + any custom description across
    the recreate. Quota cost: 1 unit.

    Raises YouTubeAPIError on non-200 or empty result.
    """
    access_token = _get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}

    with httpx.Client(headers=headers, timeout=15.0) as client:
        response = client.get(
            f"{YOUTUBE_API_BASE}/playlists",
            params={"part": "snippet,status", "id": playlist_id},
        )
    if response.status_code != 200:
        raise YouTubeAPIError(
            f"Failed to fetch playlist metadata for {playlist_id}: {response.text}"
        )
    items = response.json().get("items", [])
    if not items:
        raise YouTubeAPIError(
            f"Playlist {playlist_id} not found (Data API returned empty items array)"
        )
    snippet = items[0].get("snippet") or {}
    status = items[0].get("status") or {}
    return {
        "title": str(snippet.get("title") or "").strip(),
        "description": str(snippet.get("description") or ""),
        "privacy_status": str(status.get("privacyStatus") or "private").strip(),
    }


def create_playlist(
    *,
    title: str,
    description: str = "",
    privacy_status: str = "private",
) -> str:
    """Create a new playlist with the given metadata. Returns the new playlist ID.

    Quota cost: 50 units. ``privacy_status`` must be one of ``private``,
    ``unlisted``, ``public`` per YouTube Data API v3 spec; we default to
    ``private`` so a freshly-created day-of-week playlist is never
    accidentally exposed.
    """
    if not title:
        raise ValueError("title must be non-empty")
    access_token = _get_access_token()
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {
        "snippet": {"title": title, "description": description},
        "status": {"privacyStatus": privacy_status},
    }
    with httpx.Client(headers=headers, timeout=15.0) as client:
        response = client.post(
            f"{YOUTUBE_API_BASE}/playlists",
            params={"part": "snippet,status"},
            json=payload,
        )
    if response.status_code not in {200, 201}:
        raise YouTubeAPIError(
            f"Failed to create playlist {title!r}: {response.text}"
        )
    new_id = response.json().get("id")
    if not new_id:
        raise YouTubeAPIError(
            f"create_playlist for {title!r} returned no id: {response.text}"
        )
    return str(new_id)


def delete_playlist(playlist_id: str) -> bool:
    """Delete a YouTube playlist by ID. Quota cost: 50 units.

    Returns True on 204 (success) or 404 (already gone — treated as success
    for idempotency, same convention as remove_playlist_item).
    Raises YouTubeAPIError on any other non-success status.
    """
    access_token = _get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    with httpx.Client(headers=headers, timeout=15.0) as client:
        response = client.delete(
            f"{YOUTUBE_API_BASE}/playlists",
            params={"id": playlist_id},
        )
    if response.status_code == 204:
        return True
    if response.status_code == 404:
        logger.warning("Playlist %s already deleted or not found.", playlist_id)
        return True
    raise YouTubeAPIError(
        f"Failed to delete playlist {playlist_id}: {response.text}"
    )

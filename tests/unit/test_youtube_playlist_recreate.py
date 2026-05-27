"""Tests for the post-digest playlist recreate flow.

Covers:
- New helpers in `youtube_playlist_manager` (get_playlist_metadata,
  create_playlist, delete_playlist)
- `_recreate_playlist_after_digest` orchestration: skip on zero processed,
  safe ordering (create → infisical → delete), and graceful failure modes.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from universal_agent.services import youtube_playlist_manager as ypm

# --- youtube_playlist_manager helpers ---------------------------------------


def _mock_token(monkeypatch):
    """Stub out OAuth so the helper doesn't try to hit Google."""
    monkeypatch.setattr(ypm, "_get_access_token", lambda: "test-access-token")


def test_get_playlist_metadata_returns_title_description_privacy(monkeypatch):
    _mock_token(monkeypatch)
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "items": [{
            "snippet": {"title": "Monday Digest", "description": "Kev's queue"},
            "status": {"privacyStatus": "private"},
        }]
    }
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = mock_resp
    monkeypatch.setattr(ypm.httpx, "Client", lambda **kw: mock_client)

    meta = ypm.get_playlist_metadata("PLabc")
    assert meta == {
        "title": "Monday Digest",
        "description": "Kev's queue",
        "privacy_status": "private",
    }


def test_get_playlist_metadata_raises_on_empty_items(monkeypatch):
    _mock_token(monkeypatch)
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"items": []}
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = mock_resp
    monkeypatch.setattr(ypm.httpx, "Client", lambda **kw: mock_client)

    with pytest.raises(ypm.YouTubeAPIError):
        ypm.get_playlist_metadata("PL-missing")


def test_get_playlist_metadata_raises_on_non_200(monkeypatch):
    _mock_token(monkeypatch)
    mock_resp = MagicMock(status_code=403, text="Forbidden")
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = mock_resp
    monkeypatch.setattr(ypm.httpx, "Client", lambda **kw: mock_client)

    with pytest.raises(ypm.YouTubeAPIError):
        ypm.get_playlist_metadata("PLabc")


def test_create_playlist_returns_new_id(monkeypatch):
    _mock_token(monkeypatch)
    mock_resp = MagicMock(status_code=201)
    mock_resp.json.return_value = {"id": "PLnew123"}
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.return_value = mock_resp
    monkeypatch.setattr(ypm.httpx, "Client", lambda **kw: mock_client)

    new_id = ypm.create_playlist(title="Monday Digest", description="Q", privacy_status="private")
    assert new_id == "PLnew123"
    # Verify the payload had the right metadata
    call_kwargs = mock_client.post.call_args
    payload = call_kwargs.kwargs["json"]
    assert payload == {
        "snippet": {"title": "Monday Digest", "description": "Q"},
        "status": {"privacyStatus": "private"},
    }


def test_create_playlist_raises_on_failure(monkeypatch):
    _mock_token(monkeypatch)
    mock_resp = MagicMock(status_code=500, text="oops")
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.return_value = mock_resp
    monkeypatch.setattr(ypm.httpx, "Client", lambda **kw: mock_client)

    with pytest.raises(ypm.YouTubeAPIError):
        ypm.create_playlist(title="Monday Digest")


def test_create_playlist_rejects_empty_title(monkeypatch):
    _mock_token(monkeypatch)
    with pytest.raises(ValueError):
        ypm.create_playlist(title="")


def test_delete_playlist_returns_true_on_204(monkeypatch):
    _mock_token(monkeypatch)
    mock_resp = MagicMock(status_code=204)
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.delete.return_value = mock_resp
    monkeypatch.setattr(ypm.httpx, "Client", lambda **kw: mock_client)

    assert ypm.delete_playlist("PLabc") is True


def test_delete_playlist_treats_404_as_success(monkeypatch):
    """Idempotent: deleting an already-gone playlist isn't an error."""
    _mock_token(monkeypatch)
    mock_resp = MagicMock(status_code=404)
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.delete.return_value = mock_resp
    monkeypatch.setattr(ypm.httpx, "Client", lambda **kw: mock_client)

    assert ypm.delete_playlist("PLgone") is True


def test_delete_playlist_raises_on_other_failure(monkeypatch):
    _mock_token(monkeypatch)
    mock_resp = MagicMock(status_code=500, text="server error")
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.delete.return_value = mock_resp
    monkeypatch.setattr(ypm.httpx, "Client", lambda **kw: mock_client)

    with pytest.raises(ypm.YouTubeAPIError):
        ypm.delete_playlist("PLabc")


# --- _recreate_playlist_after_digest orchestration --------------------------


def _get_recreate_fn():
    """Import lazily so the test module loads even if the script's heavy
    imports (markdown, anthropic SDK, etc.) aren't available at collection."""
    from universal_agent.scripts.youtube_daily_digest import (
        _recreate_playlist_after_digest,
    )
    return _recreate_playlist_after_digest


def test_recreate_skips_when_processed_count_zero():
    """Option #3 — no recreate on empty days."""
    recreate = _get_recreate_fn()
    with patch("universal_agent.scripts.youtube_daily_digest.get_playlist_metadata") as mock_meta, \
         patch("universal_agent.scripts.youtube_daily_digest.create_playlist") as mock_create, \
         patch("universal_agent.scripts.youtube_daily_digest.delete_playlist") as mock_delete, \
         patch("universal_agent.scripts.youtube_daily_digest.upsert_infisical_secret") as mock_upsert:
        recreate(day_name="MONDAY", old_playlist_id="PLold", processed_count=0)
    mock_meta.assert_not_called()
    mock_create.assert_not_called()
    mock_delete.assert_not_called()
    mock_upsert.assert_not_called()


def test_recreate_skips_when_old_playlist_id_empty():
    """Defensive: no recreate if we don't know what to recreate."""
    recreate = _get_recreate_fn()
    with patch("universal_agent.scripts.youtube_daily_digest.get_playlist_metadata") as mock_meta:
        recreate(day_name="MONDAY", old_playlist_id="", processed_count=5)
    mock_meta.assert_not_called()


def test_recreate_happy_path_create_before_delete():
    """Safe ordering: metadata → create → upsert → delete (in that order)."""
    recreate = _get_recreate_fn()
    call_order: list[str] = []

    def _mock_meta(_pid):
        call_order.append("meta")
        return {"title": "Monday Digest", "description": "Kev's queue", "privacy_status": "private"}

    def _mock_create(**kwargs):
        call_order.append("create")
        return "PLnew"

    def _mock_upsert(key, value):
        call_order.append(f"upsert:{key}={value}")
        return True

    def _mock_delete(pid):
        call_order.append(f"delete:{pid}")
        return True

    with patch("universal_agent.scripts.youtube_daily_digest.get_playlist_metadata", side_effect=_mock_meta), \
         patch("universal_agent.scripts.youtube_daily_digest.create_playlist", side_effect=_mock_create), \
         patch("universal_agent.scripts.youtube_daily_digest.upsert_infisical_secret", side_effect=_mock_upsert), \
         patch("universal_agent.scripts.youtube_daily_digest.delete_playlist", side_effect=_mock_delete):
        recreate(day_name="MONDAY", old_playlist_id="PLold", processed_count=5)

    assert call_order == [
        "meta",
        "create",
        "upsert:MONDAY_YT_PLAYLIST=PLnew",
        "delete:PLold",
    ]


def test_recreate_skips_delete_when_infisical_upsert_fails():
    """Critical safety: if we can't persist the new ID, do NOT delete the
    old playlist — otherwise we'd have zero playlists with the env var
    pointing at a now-deleted id."""
    recreate = _get_recreate_fn()
    with patch("universal_agent.scripts.youtube_daily_digest.get_playlist_metadata",
               return_value={"title": "Monday Digest", "description": "", "privacy_status": "private"}), \
         patch("universal_agent.scripts.youtube_daily_digest.create_playlist", return_value="PLnew"), \
         patch("universal_agent.scripts.youtube_daily_digest.upsert_infisical_secret", return_value=False) as mock_upsert, \
         patch("universal_agent.scripts.youtube_daily_digest.delete_playlist") as mock_delete:
        recreate(day_name="MONDAY", old_playlist_id="PLold", processed_count=5)
    mock_upsert.assert_called_once_with("MONDAY_YT_PLAYLIST", "PLnew")
    mock_delete.assert_not_called()


def test_recreate_skips_create_when_metadata_fetch_fails():
    """If we can't read the old playlist, don't create a replacement —
    we'd lose the title/description. Digest itself already succeeded."""
    recreate = _get_recreate_fn()
    with patch(
        "universal_agent.scripts.youtube_daily_digest.get_playlist_metadata",
        side_effect=ypm.YouTubeAPIError("403"),
    ), patch("universal_agent.scripts.youtube_daily_digest.create_playlist") as mock_create, \
         patch("universal_agent.scripts.youtube_daily_digest.delete_playlist") as mock_delete:
        recreate(day_name="MONDAY", old_playlist_id="PLold", processed_count=5)
    mock_create.assert_not_called()
    mock_delete.assert_not_called()


def test_recreate_skips_delete_when_create_fails():
    """If create blew up, we don't have a new id to point at — do NOT
    delete the old playlist."""
    recreate = _get_recreate_fn()
    with patch(
        "universal_agent.scripts.youtube_daily_digest.get_playlist_metadata",
        return_value={"title": "Monday Digest", "description": "", "privacy_status": "private"},
    ), patch(
        "universal_agent.scripts.youtube_daily_digest.create_playlist",
        side_effect=ypm.YouTubeAPIError("quota"),
    ), patch("universal_agent.scripts.youtube_daily_digest.delete_playlist") as mock_delete, \
         patch("universal_agent.scripts.youtube_daily_digest.upsert_infisical_secret") as mock_upsert:
        recreate(day_name="MONDAY", old_playlist_id="PLold", processed_count=5)
    mock_delete.assert_not_called()
    mock_upsert.assert_not_called()


def test_recreate_swallows_delete_failure():
    """Delete failure after a successful create+upsert is a non-fatal cleanup
    issue — log + return. (Reaching this state, the digest has already
    succeeded, the new playlist is live, and Infisical points at the new id;
    the old playlist is now an orphan to be manually deleted.)"""
    recreate = _get_recreate_fn()
    with patch(
        "universal_agent.scripts.youtube_daily_digest.get_playlist_metadata",
        return_value={"title": "Monday Digest", "description": "", "privacy_status": "private"},
    ), patch(
        "universal_agent.scripts.youtube_daily_digest.create_playlist",
        return_value="PLnew",
    ), patch(
        "universal_agent.scripts.youtube_daily_digest.upsert_infisical_secret",
        return_value=True,
    ), patch(
        "universal_agent.scripts.youtube_daily_digest.delete_playlist",
        side_effect=ypm.YouTubeAPIError("would-be-orphan"),
    ):
        # Must not raise
        recreate(day_name="MONDAY", old_playlist_id="PLold", processed_count=5)


def test_recreate_falls_back_to_default_title_when_metadata_title_empty():
    """If the playlist API returns an empty title (rare), use the
    conventional `<Day> Digest` naming instead of creating an empty-titled
    playlist."""
    recreate = _get_recreate_fn()
    captured_kwargs = {}

    def _capture(**kwargs):
        captured_kwargs.update(kwargs)
        return "PLnew"

    with patch(
        "universal_agent.scripts.youtube_daily_digest.get_playlist_metadata",
        return_value={"title": "", "description": "", "privacy_status": "private"},
    ), patch(
        "universal_agent.scripts.youtube_daily_digest.create_playlist",
        side_effect=_capture,
    ), patch(
        "universal_agent.scripts.youtube_daily_digest.upsert_infisical_secret",
        return_value=True,
    ), patch(
        "universal_agent.scripts.youtube_daily_digest.delete_playlist",
        return_value=True,
    ):
        recreate(day_name="MONDAY", old_playlist_id="PLold", processed_count=5)
    assert captured_kwargs["title"] == "Monday Digest"

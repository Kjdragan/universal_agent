"""Pin the extracted magic-string/number constants in youtube_playlist_manager.

These were previously inlined literals scattered across the module. Centralizing
them is a pure refactor (identical values), but because they now live in one
place a single careless edit could silently change a timeout or status code
used by every call site. These tests lock the contract.
"""
from universal_agent.services import youtube_playlist_manager as ypm


def test_timeout_constants_preserve_original_values():
    assert ypm._DEFAULT_API_TIMEOUT_SECONDS == 15.0
    assert ypm._LIST_API_TIMEOUT_SECONDS == 30.0


def test_max_page_size_matches_youtube_data_api_ceiling():
    assert ypm._MAX_PLAYLIST_ITEMS_PER_PAGE == 50


def test_http_status_constants_match_wire_values():
    assert ypm._HTTP_OK == 200
    assert ypm._HTTP_CREATED == 201
    assert ypm._HTTP_NO_CONTENT == 204
    assert ypm._HTTP_NOT_FOUND == 404


def test_default_privacy_status_is_private():
    # Never accidentally public — security-relevant default.
    assert ypm._DEFAULT_PRIVACY_STATUS == "private"

"""Unit tests for universal_agent.identity.resolver."""

from __future__ import annotations

import os
from unittest.mock import patch

from universal_agent.identity.resolver import resolve_user_id


class TestResolveUserId:
    def test_returns_requested_id_when_provided(self) -> None:
        assert resolve_user_id("custom_user") == "custom_user"

    def test_returns_requested_id_even_when_env_set(self) -> None:
        with patch.dict(os.environ, {"COMPOSIO_USER_ID": "env_user"}):
            assert resolve_user_id("explicit") == "explicit"

    def test_falls_back_to_composio_user_id(self) -> None:
        with patch.dict(os.environ, {"COMPOSIO_USER_ID": "composio_user"}, clear=True):
            assert resolve_user_id() == "composio_user"

    def test_falls_back_to_default_user_id(self) -> None:
        with patch.dict(
            os.environ, {"DEFAULT_USER_ID": "default_user"}, clear=True
        ):
            assert resolve_user_id() == "default_user"

    def test_composio_takes_precedence_over_default(self) -> None:
        with patch.dict(
            os.environ,
            {"COMPOSIO_USER_ID": "composio", "DEFAULT_USER_ID": "default"},
            clear=True,
        ):
            assert resolve_user_id() == "composio"

    def test_returns_universal_default_when_nothing_set(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            assert resolve_user_id() == "user_universal"

    def test_empty_string_requested_id_is_ignored(self) -> None:
        with patch.dict(os.environ, {"COMPOSIO_USER_ID": "env_user"}, clear=True):
            assert resolve_user_id("") == "env_user"

    def test_none_requested_id_falls_through(self) -> None:
        with patch.dict(os.environ, {"COMPOSIO_USER_ID": "env_user"}, clear=True):
            assert resolve_user_id(None) == "env_user"

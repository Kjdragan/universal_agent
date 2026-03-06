"""Unit tests for the gws workspace event listener (Phase 5)."""

from __future__ import annotations

import asyncio
import json
import os
from unittest import mock

import pytest

from universal_agent.services.gws_event_listener import (
    GwsEventListener,
    _dispatch_hook,
    _gmail_labels,
    _max_results,
    _poll_interval,
)


# ---------------------------------------------------------------------------
# Config helper tests
# ---------------------------------------------------------------------------


class TestConfigHelpers:
    def test_poll_interval_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            if "UA_GWS_EVENTS_POLL_INTERVAL_SECONDS" in os.environ:
                del os.environ["UA_GWS_EVENTS_POLL_INTERVAL_SECONDS"]
            assert _poll_interval() == 60.0

    def test_poll_interval_custom(self):
        with mock.patch.dict(os.environ, {"UA_GWS_EVENTS_POLL_INTERVAL_SECONDS": "120"}):
            assert _poll_interval() == 120.0

    def test_poll_interval_minimum_enforced(self):
        with mock.patch.dict(os.environ, {"UA_GWS_EVENTS_POLL_INTERVAL_SECONDS": "5"}):
            assert _poll_interval() == 15.0

    def test_gmail_labels_default(self):
        env = {k: v for k, v in os.environ.items() if k != "UA_GWS_EVENTS_GMAIL_LABELS"}
        with mock.patch.dict(os.environ, env, clear=True):
            assert _gmail_labels() == "INBOX,UNREAD"

    def test_gmail_labels_custom(self):
        with mock.patch.dict(os.environ, {"UA_GWS_EVENTS_GMAIL_LABELS": "INBOX"}):
            assert _gmail_labels() == "INBOX"

    def test_max_results_default(self):
        env = {k: v for k, v in os.environ.items() if k != "UA_GWS_EVENTS_MAX_RESULTS"}
        with mock.patch.dict(os.environ, env, clear=True):
            assert _max_results() == 20

    def test_max_results_custom(self):
        with mock.patch.dict(os.environ, {"UA_GWS_EVENTS_MAX_RESULTS": "50"}):
            assert _max_results() == 50

    def test_max_results_minimum_enforced(self):
        with mock.patch.dict(os.environ, {"UA_GWS_EVENTS_MAX_RESULTS": "0"}):
            assert _max_results() == 1

    def test_dispatch_hook_default(self):
        env = {k: v for k, v in os.environ.items() if k != "UA_GWS_EVENTS_DISPATCH_HOOK"}
        with mock.patch.dict(os.environ, env, clear=True):
            assert _dispatch_hook() == "gmail/new_message"

    def test_dispatch_hook_custom(self):
        with mock.patch.dict(os.environ, {"UA_GWS_EVENTS_DISPATCH_HOOK": "gmail/inbox"}):
            assert _dispatch_hook() == "gmail/inbox"


# ---------------------------------------------------------------------------
# GwsEventListener lifecycle tests
# ---------------------------------------------------------------------------


class TestGwsEventListenerLifecycle:
    def _make_listener(self, dispatch_fn=None, notification_sink=None):
        async def _noop_dispatch(subpath, payload):
            return True, "ok"

        return GwsEventListener(
            dispatch_fn=dispatch_fn or _noop_dispatch,
            notification_sink=notification_sink,
        )

    def test_starts_disabled_when_flag_off(self):
        with mock.patch.dict(os.environ, {"UA_ENABLE_GOOGLE_WORKSPACE_EVENTS": "0"}):
            listener = self._make_listener()
            assert listener._enabled is False

    def test_starts_enabled_when_flag_on(self):
        with mock.patch.dict(os.environ, {"UA_ENABLE_GOOGLE_WORKSPACE_EVENTS": "1"}):
            listener = self._make_listener()
            assert listener._enabled is True

    def test_disable_flag_overrides_enable(self):
        env = {
            "UA_ENABLE_GOOGLE_WORKSPACE_EVENTS": "1",
            "UA_DISABLE_GOOGLE_WORKSPACE_EVENTS": "1",
        }
        with mock.patch.dict(os.environ, env):
            listener = self._make_listener()
            assert listener._enabled is False

    def test_status_returns_expected_keys(self):
        with mock.patch.dict(os.environ, {"UA_ENABLE_GOOGLE_WORKSPACE_EVENTS": "0"}):
            listener = self._make_listener()
            status = listener.status()
            assert "enabled" in status
            assert "gmail_labels" in status
            assert "poll_interval_seconds" in status
            assert "dispatched_total" in status
            assert "poll_count" in status
            assert "last_poll_at" in status

    @pytest.mark.asyncio
    async def test_start_noop_when_disabled(self):
        with mock.patch.dict(os.environ, {"UA_ENABLE_GOOGLE_WORKSPACE_EVENTS": "0"}):
            listener = self._make_listener()
            await listener.start()
            assert listener._task is None

    @pytest.mark.asyncio
    async def test_start_noop_when_binary_missing(self):
        with mock.patch.dict(os.environ, {"UA_ENABLE_GOOGLE_WORKSPACE_EVENTS": "1"}):
            with mock.patch("shutil.which", return_value=None):
                listener = self._make_listener()
                listener._enabled = True
                await listener.start()
                assert listener._task is None
                assert listener._enabled is False

    @pytest.mark.asyncio
    async def test_poll_now_returns_disabled_when_off(self):
        with mock.patch.dict(os.environ, {"UA_ENABLE_GOOGLE_WORKSPACE_EVENTS": "0"}):
            listener = self._make_listener()
            result = await listener.poll_now()
            assert result["ok"] is False
            assert result["reason"] == "disabled"

    @pytest.mark.asyncio
    async def test_poll_now_returns_error_when_binary_missing(self):
        with mock.patch.dict(os.environ, {"UA_ENABLE_GOOGLE_WORKSPACE_EVENTS": "1"}):
            with mock.patch("shutil.which", return_value=None):
                listener = self._make_listener()
                listener._enabled = True
                result = await listener.poll_now()
                assert result["ok"] is False
                assert result["reason"] == "gws_binary_not_found"


# ---------------------------------------------------------------------------
# Notification sink tests
# ---------------------------------------------------------------------------


class TestGwsEventListenerNotifications:
    @pytest.mark.asyncio
    async def test_notification_emitted_on_new_message(self):
        received: list[dict] = []

        def sink(notification: dict) -> None:
            received.append(notification)

        async def dispatch_fn(subpath: str, payload: dict):
            return True, "ok"

        with mock.patch.dict(os.environ, {"UA_ENABLE_GOOGLE_WORKSPACE_EVENTS": "1"}):
            listener = GwsEventListener(dispatch_fn=dispatch_fn, notification_sink=sink)
            listener._enabled = True

            metadata = {
                "message_id": "abc123",
                "subject": "Test Subject",
                "from_": "sender@example.com",
                "date": "Thu, 1 Jan 2026",
                "thread_id": "thread1",
                "label_ids": ["INBOX"],
                "snippet": "Hello",
            }
            listener._emit_notification(
                kind="gws_gmail_new_message",
                title="New Gmail Message",
                message="Test Subject from sender@example.com",
                severity="info",
                metadata=metadata,
            )

        assert len(received) == 1
        assert received[0]["kind"] == "gws_gmail_new_message"
        assert received[0]["metadata"]["subject"] == "Test Subject"

    def test_notification_sink_none_does_not_raise(self):
        async def dispatch_fn(subpath, payload):
            return True, "ok"

        with mock.patch.dict(os.environ, {"UA_ENABLE_GOOGLE_WORKSPACE_EVENTS": "0"}):
            listener = GwsEventListener(dispatch_fn=dispatch_fn, notification_sink=None)
            listener._emit_notification(
                kind="test",
                title="Test",
                message="Hello",
            )

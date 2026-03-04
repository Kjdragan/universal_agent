"""Unit tests for agentmail_transform webhook transform."""

from __future__ import annotations

import pytest

from webhook_transforms.agentmail_transform import transform


class TestTransform:
    def test_message_received_produces_action(self):
        context = {
            "payload": {
                "type": "event",
                "event_type": "message.received",
                "event_id": "evt_abc123",
                "message": {
                    "inbox_id": "simone@customdomain.com",
                    "thread_id": "thd_001",
                    "message_id": "msg_001",
                    "from": [{"name": "Alice", "email": "alice@example.com"}],
                    "to": [{"name": "Simone", "email": "simone@customdomain.com"}],
                    "subject": "Project Update",
                    "text": "Here is the latest update on the project.",
                    "html": "<p>Here is the latest update on the project.</p>",
                    "labels": ["received"],
                    "attachments": [],
                    "created_at": "2026-03-03T00:00:00Z",
                },
            }
        }
        result = transform(context)
        assert result is not None
        assert result["kind"] == "agent"
        assert result["to"] == "email-handler"
        assert result["session_key"] == "agentmail_thd_001"
        assert "Alice <alice@example.com>" in result["message"]
        assert "Project Update" in result["message"]
        assert "latest update" in result["message"]

    def test_non_received_event_skipped(self):
        context = {
            "payload": {
                "event_type": "message.sent",
                "message": {"message_id": "msg_002"},
            }
        }
        result = transform(context)
        assert result is None

    def test_empty_payload_skipped(self):
        assert transform({"payload": {}}) is None
        assert transform({"payload": None}) is None
        assert transform({}) is None

    def test_attachments_noted_in_message(self):
        context = {
            "payload": {
                "event_type": "message.received",
                "event_id": "evt_att",
                "message": {
                    "inbox_id": "simone@test.com",
                    "thread_id": "thd_att",
                    "message_id": "msg_att",
                    "from": [{"email": "bob@example.com"}],
                    "to": [{"email": "simone@test.com"}],
                    "subject": "With Attachment",
                    "text": "See attached.",
                    "attachments": [
                        {"filename": "report.pdf", "content_type": "application/pdf", "size": 12345},
                        {"filename": "data.csv", "content_type": "text/csv", "size": 456},
                    ],
                },
            }
        }
        result = transform(context)
        assert result is not None
        assert "Attachments (2)" in result["message"]
        assert "report.pdf" in result["message"]
        assert "data.csv" in result["message"]

    def test_session_key_falls_back_to_message_id(self):
        context = {
            "payload": {
                "event_type": "message.received",
                "event_id": "evt_nothd",
                "message": {
                    "inbox_id": "simone@test.com",
                    "thread_id": "",
                    "message_id": "msg_solo",
                    "from": "someone@example.com",
                    "to": "simone@test.com",
                    "subject": "No Thread",
                    "text": "Hello",
                },
            }
        }
        result = transform(context)
        assert result is not None
        assert result["session_key"] == "agentmail_msg_solo"

    def test_from_as_string(self):
        context = {
            "payload": {
                "event_type": "message.received",
                "event_id": "evt_str",
                "message": {
                    "inbox_id": "simone@test.com",
                    "thread_id": "thd_str",
                    "message_id": "msg_str",
                    "from": "direct@example.com",
                    "subject": "String From",
                    "text": "Body",
                },
            }
        }
        result = transform(context)
        assert result is not None
        assert "direct@example.com" in result["message"]

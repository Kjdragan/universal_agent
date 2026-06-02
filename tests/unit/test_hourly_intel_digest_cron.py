"""Unit tests for the deterministic hourly-intel-digest cron entrypoint.

`scripts/hourly_intel_digest_cron.py::run_once` is the LLM-independent
replacement for Simone's heartbeat invoking `/hourly-intel-digest`. It composes
the digest payload, sends it via AgentMail, and stamps the artifacts delivered.
We test the decision logic by mocking the seams (compose, mailer, stamp).
"""

from __future__ import annotations

import os
import sqlite3
import unittest
from unittest.mock import AsyncMock, patch

from universal_agent.scripts import hourly_intel_digest_cron as cron


class _FakeMailer:
    """Stand-in for AgentMailService with an async API surface."""

    instances: list["_FakeMailer"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.sent: list[dict] = []
        self._started = True
        _FakeMailer.instances.append(self)

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def send_email(self, **kwargs):
        self.sent.append(kwargs)
        return {"status": "sent", "message_id": "msg_test_1", "inbox": "oddcity216@agentmail.to"}


def _ready_payload() -> dict:
    return {
        "status": "ready",
        "subject": "[Intel · 10:00] Topic A",
        "text": "body",
        "html": "<p>body</p>",
        "recipient": "kevinjdragan@gmail.com",
        "cc": [],
        "inbox_id": "oddcity216@agentmail.to",
        "artifact_ids": ["pa_a", "pa_b"],
        "needs_attention": False,
        "brief_count": 2,
    }


class RunOnceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        _FakeMailer.instances = []
        self._conn = sqlite3.connect(":memory:")

    def tearDown(self) -> None:
        self._conn.close()

    async def test_ready_sends_and_marks_delivered(self) -> None:
        with patch.dict(os.environ, {"UA_INTEL_DIGEST_CRON_ENABLED": "1"}), \
             patch.object(cron, "compose_send_payload", return_value=_ready_payload()), \
             patch.object(cron, "AgentMailService", _FakeMailer), \
             patch.object(cron, "mark_all_delivered") as mark, \
             patch.object(cron, "record_email_delivery") as rec:
            status = await cron.run_once(self._conn)

        self.assertEqual(status, "sent")
        self.assertEqual(len(_FakeMailer.instances), 1)
        sent = _FakeMailer.instances[0].sent
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["to"], "kevinjdragan@gmail.com")
        self.assertEqual(sent[0]["subject"], "[Intel · 10:00] Topic A")
        self.assertIn("<p>body</p>", sent[0]["html"])
        mark.assert_called_once_with(self._conn, ["pa_a", "pa_b"])
        # one email-delivery record per artifact, carrying the message_id
        self.assertEqual(rec.call_count, 2)
        self.assertEqual(rec.call_args_list[0].kwargs["message_id"], "msg_test_1")

    async def test_not_ready_does_not_send(self) -> None:
        for st in ("paused", "throttled", "no_candidates"):
            _FakeMailer.instances = []
            with patch.dict(os.environ, {"UA_INTEL_DIGEST_CRON_ENABLED": "1"}), \
                 patch.object(cron, "compose_send_payload", return_value={"status": st}), \
                 patch.object(cron, "AgentMailService", _FakeMailer), \
                 patch.object(cron, "mark_all_delivered") as mark:
                status = await cron.run_once(self._conn)
            self.assertEqual(status, st)
            self.assertEqual(_FakeMailer.instances, [])
            mark.assert_not_called()

    async def test_disabled_flag_composes_but_does_not_send(self) -> None:
        with patch.dict(os.environ, {"UA_INTEL_DIGEST_CRON_ENABLED": "0"}), \
             patch.object(cron, "compose_send_payload", return_value=_ready_payload()) as comp, \
             patch.object(cron, "AgentMailService", _FakeMailer), \
             patch.object(cron, "mark_all_delivered") as mark:
            status = await cron.run_once(self._conn)
        self.assertEqual(status, "disabled")
        comp.assert_called_once()  # still composes (so mark_superseded etc. run)
        self.assertEqual(_FakeMailer.instances, [])
        mark.assert_not_called()

    async def test_send_failure_does_not_mark_delivered(self) -> None:
        class _BoomMailer(_FakeMailer):
            async def send_email(self, **kwargs):
                raise RuntimeError("agentmail 500")

        with patch.dict(os.environ, {"UA_INTEL_DIGEST_CRON_ENABLED": "1"}), \
             patch.object(cron, "compose_send_payload", return_value=_ready_payload()), \
             patch.object(cron, "AgentMailService", _BoomMailer), \
             patch.object(cron, "mark_all_delivered") as mark:
            status = await cron.run_once(self._conn)
        self.assertEqual(status, "send_failed")
        mark.assert_not_called()


if __name__ == "__main__":
    unittest.main()

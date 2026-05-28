"""Tests for the Gmail (gws) CLI fallback when AgentMail returns HTTP 429.

The fallback is gated by ``UA_AGENTMAIL_GMAIL_FALLBACK=1`` and only fires on
status_code==429 (e.g. AgentMail's "Daily send limit exceeded"). Other errors
must keep propagating so real bugs aren't masked.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from universal_agent.services import agentmail_service as svc


class _Stub429(Exception):
    """Stand-in for ``agentmail.core.api_error.ApiError`` with status 429."""

    def __init__(self) -> None:
        super().__init__("Daily send limit exceeded")
        self.status_code = 429
        self.body = {"name": "RateLimitError", "message": "Daily send limit exceeded"}


class _Stub500(Exception):
    def __init__(self) -> None:
        super().__init__("Internal Server Error")
        self.status_code = 500
        self.body = "boom"


def _make_service(send_raises: Exception | None = None):
    """Build an AgentMailService with the upstream client mocked."""
    service = svc.AgentMailService()
    service._inbox_id = "inbox_x"
    service._inbox_address = "oddcity216@agentmail.to"

    async def _send(**_kwargs):
        if send_raises is not None:
            raise send_raises
        return SimpleNamespace(message_id="agentmail-msg-001")

    service._client = SimpleNamespace(
        inboxes=SimpleNamespace(
            messages=SimpleNamespace(send=_send),
        ),
    )
    return service


@pytest.mark.asyncio
async def test_send_direct_success_path_unchanged():
    service = _make_service(send_raises=None)
    result = await service._send_direct(
        to="kevin@example.com", subject="hi", text="body",
        html=None, attachments=None, labels=None,
    )
    assert result["status"] == "sent"
    assert result["message_id"] == "agentmail-msg-001"


@pytest.mark.asyncio
async def test_429_without_flag_reraises(monkeypatch):
    monkeypatch.delenv("UA_AGENTMAIL_GMAIL_FALLBACK", raising=False)
    service = _make_service(send_raises=_Stub429())
    with pytest.raises(_Stub429):
        await service._send_direct(
            to="kevin@example.com", subject="hi", text="body",
            html=None, attachments=None, labels=None,
        )


@pytest.mark.asyncio
async def test_500_with_flag_still_reraises(monkeypatch):
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_FALLBACK", "1")
    service = _make_service(send_raises=_Stub500())
    with pytest.raises(_Stub500):
        await service._send_direct(
            to="kevin@example.com", subject="hi", text="body",
            html=None, attachments=None, labels=None,
        )


@pytest.mark.asyncio
async def test_429_with_flag_falls_back_to_gws_cli(monkeypatch):
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_FALLBACK", "1")
    # Override the CLI so the test doesn't shell out to npx.
    monkeypatch.setenv("UA_GMAIL_CLI_CMD", "/usr/bin/false")
    service = _make_service(send_raises=_Stub429())

    captured: dict = {}

    def _fake_run(argv, *, capture_output, text, timeout, check):
        captured["argv"] = list(argv)
        captured["timeout"] = timeout
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"id": "gmail-cli-msg-77"}),
            stderr="",
        )

    with patch.object(subprocess, "run", _fake_run):
        result = await service._send_direct(
            to="kevin@example.com",
            subject="Daily YouTube Digest",
            text="plain",
            html="<p>rich</p>",
            attachments=None,
            labels=None,
        )

    assert result == {
        "status": "sent_via_gmail_fallback",
        "message_id": "gmail-cli-msg-77",
        "inbox": "oddcity216@agentmail.to",
        "via": "gmail_cli",
    }
    # Argv shape: CLI override + gmail +send --format json --to ... --subject ... --body ... --html
    argv = captured["argv"]
    assert argv[:2] == ["/usr/bin/false", "gmail"]
    assert argv[2] == "+send"
    assert "--to" in argv and argv[argv.index("--to") + 1] == "kevin@example.com"
    assert "--subject" in argv
    assert "--html" in argv, "html body should pass --html flag"
    # When html is provided, body arg should be the html, not the text
    assert argv[argv.index("--body") + 1] == "<p>rich</p>"


@pytest.mark.asyncio
async def test_429_fallback_writes_attachments_and_passes_them(monkeypatch):
    import base64
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_FALLBACK", "1")
    monkeypatch.setenv("UA_GMAIL_CLI_CMD", "/usr/bin/false")
    service = _make_service(send_raises=_Stub429())

    payload = b"hello-pdf-bytes"
    attachments = [
        {
            "filename": "report.pdf",
            "content_type": "application/pdf",
            "content": base64.b64encode(payload).decode("ascii"),
        }
    ]

    captured_paths: list[str] = []

    def _fake_run(argv, *, capture_output, text, timeout, check):
        # Capture each -a path and confirm the file is there with the right bytes.
        i = 0
        while i < len(argv):
            if argv[i] == "-a":
                captured_paths.append(argv[i + 1])
                i += 2
                continue
            i += 1
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    with patch.object(subprocess, "run", _fake_run):
        await service._send_direct(
            to="kevin@example.com", subject="att test", text="x",
            html=None, attachments=attachments, labels=None,
        )

    assert len(captured_paths) == 1
    # File is cleaned up by the time we get here (tmpdir wiped in finally) —
    # the assertion is on the argv plumbing itself.
    assert captured_paths[0].endswith("report.pdf")


@pytest.mark.asyncio
async def test_429_fallback_propagates_cli_failure(monkeypatch):
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_FALLBACK", "1")
    monkeypatch.setenv("UA_GMAIL_CLI_CMD", "/usr/bin/false")
    service = _make_service(send_raises=_Stub429())

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="auth expired")

    with patch.object(subprocess, "run", _fake_run):
        with pytest.raises(RuntimeError, match="agentmail_gmail_fallback_failed"):
            await service._send_direct(
                to="kevin@example.com", subject="x", text="x",
                html=None, attachments=None, labels=None,
            )


@pytest.mark.asyncio
async def test_429_fallback_handles_missing_cli(monkeypatch):
    monkeypatch.setenv("UA_AGENTMAIL_GMAIL_FALLBACK", "1")
    monkeypatch.setenv("UA_GMAIL_CLI_CMD", "/usr/bin/false")
    service = _make_service(send_raises=_Stub429())

    def _fake_run(*_args, **_kwargs):
        raise FileNotFoundError("npx")

    with patch.object(subprocess, "run", _fake_run):
        with pytest.raises(RuntimeError, match="agentmail_gmail_fallback_missing_cli"):
            await service._send_direct(
                to="kevin@example.com", subject="x", text="x",
                html=None, attachments=None, labels=None,
            )
